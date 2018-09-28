
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--language", dest="language", type=str)
parser.add_argument("--load-from", dest="load_from", type=str)
#parser.add_argument("--save-to", dest="save_to", type=str)
parser.add_argument("--gpu", dest="gpu", type=bool)


import random

parser.add_argument("--batchSize", type=int, default=64)
parser.add_argument("--char_embedding_size", type=int, default=100)
parser.add_argument("--hidden_dim", type=int, default=2048)
parser.add_argument("--layer_num", type=int, default=2)
parser.add_argument("--weight_dropout_in", type=float, default=0.3)
parser.add_argument("--weight_dropout_hidden", type=float, default=0.25)
parser.add_argument("--char_dropout_prob", type=float, default=0.05)
parser.add_argument("--char_noise_prob", type = float, default= 0.0)
parser.add_argument("--learning_rate", type = float, default= 0.4)
parser.add_argument("--myID", type=int, default=random.randint(0,1000000000))
parser.add_argument("--sequence_length", type=int, default=50)


args=parser.parse_args()
print(args)


def device(x):
    if args.gpu:
        return x.cuda()
    else:
        return x





from acqdivReader import AcqdivReader, AcqdivReaderPartition

acqdivCorpusReader = AcqdivReader(args.language)



def plus(it1, it2):
   for x in it1:
      yield x
   for x in it2:
      yield x

try:
   with open("/checkpoint/mhahn/char-vocab-acqdiv-"+args.language, "r") as inFile:
     itos = inFile.read().strip().split("\n")
except FileNotFoundError:
    print("Creating new vocab")
    char_counts = {}
    # get symbol vocabulary
    with open("/private/home/mhahn/data/acqdiv/"+args.language+"-vocab.txt", "r") as inFile:
      words = inFile.read().strip().split("\n")
      for word in words:
         for char in word.lower():
            char_counts[char] = char_counts.get(char, 0) + 1
    char_counts = [(x,y) for x, y in char_counts.items()]
    itos = [x for x,y in sorted(char_counts, key=lambda z:(z[0],-z[1]))]
    with open("/checkpoint/mhahn/char-vocab-acqdiv-"+args.language, "w") as outFile:
       print("\n".join(itos), file=outFile)
#itos = sorted(itos)
itos.append("\n")
itos.append(" ")
print(itos)
stoi = dict([(itos[i],i) for i in range(len(itos))])

halfSequenceLength = int(args.sequence_length/2)



import random


import torch

print(torch.__version__)

from weight_drop import WeightDrop


rnn = device(torch.nn.LSTM(args.char_embedding_size, args.hidden_dim, args.layer_num))

rnn_parameter_names = [name for name, _ in rnn.named_parameters()]
print(rnn_parameter_names)
#quit()


rnn_drop = WeightDrop(rnn, [(name, args.weight_dropout_in) for name, _ in rnn.named_parameters() if name.startswith("weight_ih_")] + [ (name, args.weight_dropout_hidden) for name, _ in rnn.named_parameters() if name.startswith("weight_hh_")])

# -1, because whitespace doesn't actually appear
output = device(torch.nn.Linear(args.hidden_dim, len(itos)-1+3))
char_embeddings = device(torch.nn.Embedding(num_embeddings=len(itos)-1+3, embedding_dim=args.char_embedding_size))

logsoftmax = torch.nn.LogSoftmax(dim=2)

train_loss = torch.nn.NLLLoss(ignore_index=0)
print_loss = torch.nn.NLLLoss(size_average=False, reduce=False, ignore_index=0)
char_dropout = torch.nn.Dropout2d(p=args.char_dropout_prob)

modules = [rnn, output, char_embeddings]
def parameters():
   for module in modules:
       for param in module.parameters():
            yield param

parameters_cached = [x for x in parameters()]

optim = torch.optim.SGD(parameters(), lr=args.learning_rate, momentum=0.0) # 0.02, 0.9

named_modules = {"rnn" : rnn, "output" : output, "char_embeddings" : char_embeddings, "optim" : optim}

if args.load_from is not None:
  checkpoint = torch.load("/checkpoint/mhahn/"+args.load_from+".pth.tar")
  for name, module in named_modules.items():
      module.load_state_dict(checkpoint[name])

from torch.autograd import Variable


data = AcqdivReaderPartition(acqdivCorpusReader, partition="train").reshuffledIterator(blankBeforeEOS=False, originalIterator=AcqdivReader.iteratorMorph)


numeric_with_blanks = []

indices_with_blanks = []
count = 0
print("Prepare chunks")
for chunk in data:
  indices_with_blanks.append((chunk, -1))
  numeric_with_blanks.append(stoi[" "]+3)
  countInChunk = 0

  for char in chunk[0]:
#    print((char if char != "\n" else "\\n", stoi[char]+3 if char in stoi else 2))
    indices_with_blanks.append((chunk, countInChunk))

    count += 1
    countInChunk += 1
    if char not in stoi:
        print(char)
    numeric_with_blanks.append(stoi[char]+3 if char in stoi else 2)
 #   print((char, chunk))
#quit()
# select a portion
numeric_with_blanks = numeric_with_blanks[:100000]
indices_with_blanks = indices_with_blanks[:100000]

boundaries = []
numeric_full = []
indices_full = []
for entry in zip(numeric_with_blanks, indices_with_blanks, range(len(numeric_with_blanks))):
 # print((entry-3, itos[entry-3]))
  #assert entry > 3
  if entry[0] > 3 and itos[entry[0]-3] == " ":
     boundaries.append(len(numeric_full))
  else:
     numeric_full.append(entry[0])
     indices_full.append(entry[1])
  if len(boundaries) > 0:
       assert len(numeric_full) - boundaries[-1] < 100, "".join([itos[x-3] for x in numeric_with_blanks[max(0,entry[2]-150):entry[2]+150]])
 #    print(itos[entry[0]-3],entry[1])
#quit()

future_surprisal_with = [None for _ in numeric_full]
future_surprisal_without = [None for _ in numeric_full]

char_surprisal = [None for _ in numeric_full]
char_entropy = [None for _ in numeric_full]


for start in range(0, len(numeric_full)-args.sequence_length, args.batchSize):
      numeric = [([0] + numeric_full[b:b+args.sequence_length]) for b in range(start, start+args.batchSize)]
      maxLength = max([len(x) for x in numeric])
      for i in range(len(numeric)):
        numeric[i] = numeric[i] + [0]*(maxLength-len(numeric[i]))


      input_tensor = Variable(device(torch.LongTensor(numeric).transpose(0,1)[:-1]), requires_grad=False)
      target_tensor = Variable(device(torch.LongTensor(numeric).transpose(0,1)[1:]), requires_grad=False)
      embedded = char_embeddings(input_tensor)


      out, _ = rnn_drop(embedded, None)
      logits = output(out) 
      log_probs = logsoftmax(logits)

      entropy = (- log_probs * torch.exp(log_probs)).sum(2).view((maxLength-1), args.batchSize).data.cpu().numpy()

      # 
      loss = print_loss(log_probs.view(-1, len(itos)-1+3), target_tensor.view(-1)).view((maxLength-1), args.batchSize)
      losses = loss.data.cpu().numpy()
#      for i in range(len(numeric[0])-1):

#         boundaries_index = [0 for _ in numeric]
      if random.random() > 0.95:
        print(start/len(numeric_full))
        print(loss.mean())
        for i in range((args.sequence_length-1)-1):
           print((losses[i][0], itos[numeric[0][i+1]-3]))

#         print((i,losses[i][0], itos[numeric[0][i+1]-1]))
      for i in range(start, start+args.batchSize):
         #print(losses[:int(halfSequenceLength),i-start].sum())
         surprisalAtStart = losses[:halfSequenceLength,i-start].sum()
         surprisalAtMid = losses[halfSequenceLength:, i-start].sum()
         #print(losses[:,i-start])
         if i+halfSequenceLength < len(future_surprisal_with):
            future_surprisal_with[i+halfSequenceLength] = surprisalAtMid
            char_surprisal[i+halfSequenceLength] = losses[halfSequenceLength, i-start]
            char_entropy[i+halfSequenceLength] = entropy[halfSequenceLength, i-start]
         if i < len(future_surprisal_without):
            future_surprisal_without[i] = surprisalAtStart
             
def mi(x,y):
  return   x-y if x is not None and y is not None else None

chars = []
predictor = []
dependent = []

utteranceBoundaries = []
lastWasUtteranceBoundary = False

timeSinceLastBoundary = 0

indices_without_boundaries = []
boundaries_index = 0
for i in range(len(numeric_full)):
   if boundaries_index < len(boundaries):
       assert i <= boundaries[boundaries_index]
   boundary = False
   while boundaries_index < len(boundaries) and i == boundaries[boundaries_index]:
      boundary = True
      boundaries_index += 1
   #   if boundaries_index < len(boundaries):
   #       assert i < boundaries[boundaries_index], (i, boundaries[boundaries_index])
   #else:
      
   pmiFuturePast = mi(future_surprisal_without[i], future_surprisal_with[i])
   print((itos[numeric_full[i]-3], char_surprisal[i], pmiFuturePast, pmiFuturePast < 0 if pmiFuturePast is not None else None, boundary)) # pmiFuturePast < 2 if pmiFuturePast is not None else None,
   if pmiFuturePast is not None:
     character = itos[numeric_full[i]-3] if numeric_full[i] != 2 else itos[-3]
     assert character != " "
     if character == "\n":
        lastWasUtteranceBoundary = True
     else:
       chars.append(character)
       predictor.append([pmiFuturePast, char_surprisal[i], char_entropy[i], 1 if lastWasUtteranceBoundary else 0]) #char_surprisal[i], pmiFuturePast]) #pmiFuturePast])
       dependent.append(1 if boundary else 0)
       timeSinceLastBoundary = timeSinceLastBoundary+1 if boundary == False else 0
       assert timeSinceLastBoundary < 100, (i, boundaries[boundaries_index])
       lastWasUtteranceBoundary = False
       indices_without_boundaries.append(indices_full[i])

# TODO exclude utterance boundaries from the logistic classification, and record where they were


# , char_surprisal[i], char_entropy[i]

# char_surprisal[i], 

#print(predictor)
#print(dependent)

zeroPredictor = [0]*len(predictor[0])

predictorShiftedP1 = predictor[1:]+[zeroPredictor]
predictorShiftedP2 = predictor[2:]+[zeroPredictor,zeroPredictor]
predictorShiftedP3 = predictor[3:]+[zeroPredictor,zeroPredictor,zeroPredictor]
predictorShiftedP4 = predictor[4:]+[zeroPredictor,zeroPredictor,zeroPredictor,zeroPredictor]

predictorShiftedM1 = [zeroPredictor]+predictor[:-1]
predictorShiftedM2 = [zeroPredictor,zeroPredictor]+predictor[:-2]
predictorShiftedM3 = [zeroPredictor,zeroPredictor,zeroPredictor]+predictor[:-3]
predictorShiftedM4 = [zeroPredictor,zeroPredictor,zeroPredictor,zeroPredictor]+predictor[:-4]

predictor = [a+b+c+d+e+f+g for a, b, c, d, e, f, g in zip(predictor, predictorShiftedP1, predictorShiftedP2, predictorShiftedP3, predictorShiftedM1, predictorShiftedM2, predictorShiftedM3)]



from sklearn.model_selection import train_test_split
x_train, x_test, y_train, y_test, chars_train, chars_test, indices_train, indices_test = train_test_split(predictor, dependent, chars, indices_without_boundaries, test_size=0.5, random_state=0, shuffle=False)


from sklearn.linear_model import LogisticRegression

logisticRegr = LogisticRegression()

logisticRegr.fit(x_train, y_train)

# Returns a NumPy Array
# Predict for One Observation (image)

predictions = logisticRegr.predict(x_test)
#predictions = [1 if x[0]<0 else 0 for x in x_test]
#print(predictions)

for char, predicted, real, predictor in zip(chars_test, predictions, y_test, x_test):
    print((char, predicted, real, predictor[0]))

realLexicon = set()
extractedLexicon = {}
extractedLexiconWithReal = {}
currentWord = ""
currentWordReal = ""
realWords = 0
predictedWords = 0

# for each predicted word, count whether it is an example of:
agreement = 0
oversegmented = 0
undersegmented = 0
missegmented = 0

# for each REAL word
wasOversegmented = 0
wasUndersegmented = 0


lastPredictedStartCoincidedWithRealStart = True
lastRealStartCoincidedWithPredictedStart = True

lastIndex = None
for char, predicted, real, indices in zip(chars_test, predictions, y_test, indices_test):
   assert char != " "

#   print(indices)
   if predicted == 1:
     print(currentWord)


   if indices[0] is not lastIndex:
#     print((lastPredictedStartCoincidedWithRealStart, currentWord, currentWordReal, indices))
     print("")
     print(list(indices[0][-1]))
     lastIndex = indices[0]
   
   if real ==1:
       if predicted == 1 and currentWord == currentWordReal:
           agreement += 1
       elif predicted == 1 and lastPredictedStartCoincidedWithRealStart:
         wasUndersegmented += 1
       elif predicted == 1 and lastRealStartCoincidedWithPredictedStart:
         wasOversegmented += 1
# oversegmentation:
       # wasOversegmented
       #  wasUndersegmented
       # wasMissegmented



#       realLexicon.add(currentWordReal)
 #      currentWordReal = char
   #else:
  #     currentWordReal += char

   if predicted == 1:
       if real == 1 and lastPredictedStartCoincidedWithRealStart:
           # correct or undersegmented
          if currentWord == currentWordReal:
             _ = _
          elif len(currentWord) == len(currentWordReal):
              assert currentWord == currentWordReal, (currentWord, currentWordReal)
          elif len(currentWord) < len(currentWordReal):
              print(currentWord, currentWordReal)
              assert False
          else:
             assert len(currentWord) > len(currentWordReal)
             undersegmented += 1
       elif real == 1 and not lastPredictedStartCoincidedWithRealStart:
          if len(currentWord) > len(currentWordReal): # missegmented
             missegmented += 1
          else:
             assert len(currentWord) < len(currentWordReal), (currentWord, currentWordReal)
             oversegmented += 1
       elif real == 0 and len(currentWord) <= len(currentWordReal):
             oversegmented += 1
       elif real == 0 and len(currentWord) > len(currentWordReal):
            missegmented += 1

       if real == 1:
          lastPredictedStartCoincidedWithRealStart = True
       else:
          lastPredictedStartCoincidedWithRealStart = False
  
   if real == 1:
       if predicted == 1:
          lastRealStartCoincidedWithPredictedStart = True
       else:
          lastRealStartCoincidedWithPredictedStart = False
 
   if predicted == 1:
       predictedWords += 1
       extractedLexicon[currentWord] = extractedLexicon.get(currentWord, 0) + 1
       extractedLexiconWithReal[(currentWord, currentWordReal, real==1)] = extractedLexiconWithReal.get((currentWord, currentWordReal, real==1), 0) + 1

       currentWord = char
   else:
       currentWord += char


   if real ==1:
       realWords += 1
       realLexicon.add(currentWordReal)
       currentWordReal = char
   else:
       currentWordReal += char
   #print(currentWord, currentWordReal, lastPredictedStartCoincidedWithRealStart)
   assert len(currentWordReal) < 100, currentWordReal

assert agreement + oversegmented + undersegmented + missegmented == predictedWords

print("Extracted words")
print(sorted(list(extractedLexicon.items()), key=lambda x:x[1]))
print("Incorrect Words")
incorrectWords = [(x,y) for (x,y) in extractedLexicon.items() if x in set(list(extractedLexicon)).difference(realLexicon)]
print(sorted(incorrectWords, key=lambda x:x[1]))
print("Correct words")
correctWords = [(x,y) for (x,y) in extractedLexicon.items() if x in set(list(extractedLexicon)).intersection(realLexicon)]
print(sorted(correctWords, key=lambda x:x[1]))
annotatedWords = [(x,y) for (x,y) in extractedLexiconWithReal.items()]
print(sorted(annotatedWords, key=lambda x:x[1]))

print("Lexicon")
print("Precision")
print(len(correctWords)/len(extractedLexicon))
print("Recall")
print(len(correctWords)/len(realLexicon))
print("..")
print("Classifying the predicted words", ["Agreement", agreement, "Oversegmented", oversegmented, "Undersegmented", undersegmented, "Missegmented", missegmented])
print("Classifying the real endpoints", ["over", wasOversegmented, "under", wasUndersegmented])
print("quality")
print("Precision")
print(agreement/predictedWords)
print("Recall")
print(agreement/realWords)

predictedBoundariesTotal = 0
predictedBoundariesCorrect = 0
realBoundariesTotal = 0

predictedAndReal = len([1 for x, y in zip(predictions, y_test) if x==1 and x==y])
predictedCount = sum(predictions)
targetCount = sum(y_test)
print("Boundaries")
print("Precision")
print(predictedAndReal/predictedCount)
print("Recall")
print(predictedAndReal/targetCount)

score = logisticRegr.score(x_test, y_test)
print(score)

quantities = {"agreement" : agreement, "oversegmented" : oversegmented, "undersegmented" : undersegmented, "missegmented" : missegmented, "over" : wasOversegmented, "under" : wasUndersegmented, "lexical_precision" : len(correctWords)/len(extractedLexicon), "lexical_recall" : len(correctWords)/len(realLexicon), "token_precision" : agreement/predictedWords, "token_recall" : agreement/realWords, "boundary_precision" : predictedAndReal/predictedCount, "boundary_recall" : predictedAndReal/targetCount, "boundary_accuracy" : score}

with open("/checkpoint/mhahn/trajectories/"+__file__+"_"+args.load_from, "w") as outFile:
     for key, value in quantities.items():
        outFile.write("\t".join(list(map(str, ([key, value]))))+"\n")


#import matplotlib.pyplot as plt
#import seaborn as sns
#from sklearn import metrics
#
#cm = metrics.confusion_matrix(y_test, predictions)
#print(cm)



#print([x-y if x is not None and y is not None else None for x,y in zip(future_surprisal_without, future_surprisal_with)])

##      print(train[batch*args.batchSize:(batch+1)*args.batchSize])
#      numeric = [([0] + [stoi[data[x]]+1 for x in range(b, b+args.sequence_length) if x < len(data)]) for b in train[batch*args.batchSize:(batch+1)*batchSize]]
#     # print(numeric)
#      input_tensor = Variable(torch.LongTensor(numeric[:-1]).transpose(0,1).cuda(), requires_grad=False)
#      target_tensor = Variable(torch.LongTensor(numeric[1:]).transpose(0,1).cuda(), requires_grad=False)
#
#    #  print(char_embeddings)
#      embedded = char_embeddings(input_tensor)
#      out, _ = rnn(embedded, None)
#      logits = output(out) 
#      log_probs = logsoftmax(logits)
#   #   print(logits)
#  #    print(log_probs)
# #     print(target_tensor)
#      loss = train_loss(log_probs.view(-1, len(itos)+1), target_tensor.view(-1))
#      optim.zero_grad()
#      if batch % 10 == 0:
#         print(loss)
#      loss.backward()
#      optim.step()
#      
#
