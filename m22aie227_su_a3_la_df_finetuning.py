# -*- coding: utf-8 -*-
"""M22AIE227_SU_A3_LA_DF_finetuning.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1UJkD4s7XLLVnsbtGuuXAnbx9quW4g3yl

#### Git clone SSL_Anti-spoofing
"""

from google.colab import drive
drive.mount('/content/drive')

!git clone https://github.com/TakHemlata/SSL_Anti-spoofing.git

"""#### Install requirements """

# !pip install torch==1.8.1+cu111 -f https://download.pytorch.org/whl/torch_stable.html   --not feasible hence installing nearesr version 1.11
!pip install torch==1.11.0

# !pip install torchvision==0.9.1+cu111 -f https://download.pytorch.org/whl/torch_stable.html  --not feasible hence installing nearesr version 0.12
!pip install torchvision==0.12.0

# !pip install torchaudio==0.8.1 -f https://download.pytorch.org/whl/torch_stable.html   --not feasible hence installing nearesr version 0.11
!pip install torchaudio==0.11.0

# Commented out IPython magic to ensure Python compatibility.
#installing fairseq
# %cd /content/SSL_Anti-spoofing/fairseq-a54021305d6b3c4c5959ac9395135f63202db8f1
!pip install --editable ./

# Commented out IPython magic to ensure Python compatibility.
#installing requirements
# %cd /content/SSL_Anti-spoofing/
!pip install -r requirements.txt

#specific numpy version required for fairseq
!pip install numpy==1.22.4

"""#### Imports"""

import glob
import os
import pandas as pd
import numpy as np

import torch
import torch.nn as nn
from torch.utils.data import Dataset,DataLoader
from torch import Tensor
import librosa

from sklearn.metrics import det_curve,RocCurveDisplay,auc,roc_curve
import matplotlib.pyplot as plt

"""#### FOR Dataset"""

!rm -rf /content/for-2seconds/

!wget 'https://www.eecs.yorku.ca/~bil/Datasets/for-2sec.tar.gz'

!tar -xzvf /content/for-2sec.tar.gz -C /content/

## creating a dataframe that consists of audio file path, tand their label either real (1) or fake (0)
df = pd.DataFrame(glob.glob("/content/for-2seconds/*/*/*.wav"), columns = ['file_path'])
df['real_or_fake'] = df['file_path'].apply(lambda x : x.split('/')[-2])
df['split_type'] = df['file_path'].apply(lambda x : x.split('/')[-3])
df['label'] = df['real_or_fake'].apply(lambda x : 1 if x=='real' else 0)
df = df.sample(frac=1).reset_index(drop=True)

df.head()

df.split_type.value_counts()

df.label.value_counts()

#utility function that either truncate or pad audio signal to create a fix lenght signal
def pad(x, max_len=64600//2):
    x_len = x.shape[0]
    if x_len >= max_len:
        return x[:max_len]
    # need to pad
    num_repeats = int(max_len / x_len)+1
    padded_x = np.tile(x, (1, num_repeats))[:, :max_len][0]
    return padded_x

#custom dataset class for evaluation, this class takes list of audio paths & their labels
class Dataset_FOR(Dataset):
  def __init__(self, file_path, label):
    self.file_path = file_path
    self.cut=64600//2 # take ~4//2 sec audio (64600//2 samples) ie 2 secs audio (32300 samples)
    self.label  = label
  def __len__(self):
    return len(self.file_path)
  def __getitem__(self, index):
    X, fs = librosa.load(self.file_path[index], sr=16000)
    X_pad = pad(X,self.cut)
    x_inp = Tensor(X_pad)
    label = self.label[index]
    return x_inp, label

train_set = Dataset_FOR(df[df.split_type == 'training']['file_path'].tolist()[:2500],
                        df[df.split_type == 'training']['label'].tolist()[:2500])
train_set[0][0].shape

validation_set = Dataset_FOR(df[df.split_type == 'validation']['file_path'].tolist()[:500],
                        df[df.split_type == 'validation']['label'].tolist()[:500])
validation_set[0][0].shape

test_set = Dataset_FOR(df[df.split_type == 'testing']['file_path'].tolist(),
                        df[df.split_type == 'testing']['label'].tolist())
test_set[0][0].shape

"""#### Model"""

#downloaded Pre trainned model for LA and saved at  my drive location '/content/drive/MyDrive/LA_model.pth'
#downloaded Pre trainned model XLSR and saved at  my drive location /content/drive/MyDrive/Best_LA_model_for_DF.pth
# above model path need to updated in line 24 in model.py file ie :::  cp_path = '/content/drive/MyDrive/Classroom/xlsr2_300m.pt'

# Commented out IPython magic to ensure Python compatibility.
# %cd /content/SSL_Anti-spoofing

!wget https://dl.fbaipublicfiles.com/fairseq/wav2vec/xlsr2_300m.pt

# updated in line 24 in model.py file ie :::  cp_path = /content/SSL_Anti-spoofing/xlsr2_300m.pt
from model import Model

#define pretrianed model path for LA & DF & output file path for both
args = {
        'la_model_path' : '/content/drive/MyDrive/LA_model.pth',
        'df_model_path' : '/content/drive/MyDrive/Best_LA_model_for_DF.pth' ,
        'lr' :     0.000001,
        'weight_decay' : 0.0001,
        'la_eval_output' : '/content/finetuned_la_score.txt',
        'df_eval_output' : '/content/finetuned_df_score.txt',
        }

#set device
device = 'cuda' if torch.cuda.is_available() else 'cpu'

def get_model(args, device, la_or_df):
  model = Model(args,device)
  nb_params = sum([param.view(-1).size()[0] for param in model.parameters()])
  model =model.to(device)
  if la_or_df.lower() == 'df':
    model =nn.DataParallel(model).to(device)
    model.load_state_dict(torch.load(args['df_model_path'],map_location=device))
    print('Model loaded : {}'.format(args['df_model_path']))
  else:
    model.load_state_dict(torch.load(args['la_model_path'],map_location=device))
    print('Model loaded : {}'.format(args['la_model_path']))
  print('nb_params:',nb_params)
  return model

"""#### FIne tuneing (For Both LA & DF)

"""

def train_epoch(train_loader, model, lr,optim, device):
  running_loss = 0
  num_total = 0.0
  model.train()
  #set objective (Loss) functions
  weight = torch.FloatTensor([0.1, 0.9]).to(device)

  criterion = nn.CrossEntropyLoss(weight=weight)
  for batch_x, batch_y in train_loader:
    batch_size = batch_x.size(0)
    num_total += batch_size
    batch_x = batch_x.to(device)
    batch_y = batch_y.view(-1).type(torch.int64).to(device)
    batch_out = model(batch_x)
    batch_loss = criterion(batch_out, batch_y)
    running_loss += (batch_loss.item() * batch_size)
    optimizer.zero_grad()
    batch_loss.backward()
    optimizer.step()
  running_loss /= num_total
  return running_loss

def evaluate_accuracy(dev_loader, model, device):
  val_loss = 0.0
  num_total = 0.0
  model.eval()
  weight = torch.FloatTensor([0.1, 0.9]).to(device)
  criterion = nn.CrossEntropyLoss(weight=weight)
  for batch_x, batch_y in dev_loader:
    batch_size = batch_x.size(0)
    num_total += batch_size
    batch_x = batch_x.to(device)
    batch_y = batch_y.view(-1).type(torch.int64).to(device)
    batch_out = model(batch_x)

    batch_loss = criterion(batch_out, batch_y)
    val_loss += (batch_loss.item() * batch_size)
  val_loss /= num_total

  return val_loss

"""##### fintuning LA model on FOR dataset"""

# Training and validation of LA model for fintuning
batch_size = 16
num_epochs = 10
model_save_path = '/content/LA_finetuned/'
!mkdir '/content/LA_finetuned/'

model = get_model(args, device, 'la')
optimizer = torch.optim.Adam(model.parameters(), lr=args['lr'],weight_decay=args['weight_decay'])

train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=False, drop_last=False)
dev_loader = DataLoader(validation_set, batch_size=batch_size, shuffle=False, drop_last=False)

for epoch in range(num_epochs):
  running_loss = train_epoch(train_loader,model, args['lr'],optimizer, device)
  val_loss = evaluate_accuracy(dev_loader, model, device)
  print("epochs :::: ", epoch, '\t', 'train_loss :: ', running_loss, '\t',  'val_loss :: ', val_loss,)
  torch.save(model.state_dict(), os.path.join(model_save_path, f"finetuned_LA_model_epochs_{epoch}.pth"))

#saving model in drive for further uses
torch.save(model.state_dict(), '/content/drive/MyDrive/best_finetuned_LA_model.pth')

"""##### fintuning DF model on FOR dataset"""

# Training and validation of DF model for fintuning
num_epochs = 10
batch_size = 16
model_save_path = '/content/DF_finetuned/'
!mkdir '/content/DF_finetuned/'

model = get_model(args, device, 'df')
optimizer = torch.optim.Adam(model.parameters(), lr=args['lr'],weight_decay=args['weight_decay'])

train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=False, drop_last=False)
dev_loader = DataLoader(validation_set, batch_size=batch_size, shuffle=False, drop_last=False)

for epoch in range(num_epochs):
  running_loss = train_epoch(train_loader,model, args['lr'],optimizer, device)
  val_loss = evaluate_accuracy(dev_loader, model, device)
  print("epochs :::: ", epoch, '\t', 'train_loss :: ', running_loss, '\t',  'val_loss :: ', val_loss,)
  torch.save(model.state_dict(), os.path.join(model_save_path, f"finetuned_DF_model_epochs_{epoch}.pth"))

#saving model in drive for further uses
torch.save(model.state_dict(), '/content/drive/MyDrive/best_finetuned_DF_model.pth')

"""#### Inference (For both LA & DF)"""

def produce_evaluation_file(dataset,batch_size, model, device, save_path):
  data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, drop_last=False)
  model.eval()

  for batch_x,label in data_loader:
    label_list = []
    score_list = []
    batch_size = batch_x.size(0)
    batch_x = batch_x.to(device)
    batch_out = model(batch_x)
    batch_score = (batch_out[:, 1]).data.cpu().numpy().ravel()
    # add outputs
    label_list.extend(label)
    score_list.extend(batch_score.tolist())
    with open(save_path, 'a+') as fh:
      for l, s in zip(label_list,score_list):
        fh.write('{} {}\n'.format(l, s))
    fh.close()
  print('Scores are saved to {}'.format(save_path))

#updating args model path with best fine tuned model path for both LA & DF
args = {
        'la_model_path' : '/content/drive/MyDrive/best_finetuned_LA_model.pth',
        'df_model_path' : '/content/drive/MyDrive/best_finetuned_DF_model.pth' ,
        'lr' :     0.000001,
        'weight_decay' : 0.0001,
        'la_eval_output' : '/content/finetuned_la_score.txt',
        'df_eval_output' : '/content/finetuned_df_score.txt',
        }

batch_size = 16
##inference using LA model, & saving scores in txt files
model = get_model(args, device, la_or_df='la')
produce_evaluation_file(test_set,batch_size, model, device, args['la_eval_output'])

batch_size = 16
##inference using DF model, & saving scores in txt files
model = get_model(args, device, la_or_df='df')
produce_evaluation_file(test_set,batch_size, model, device, args['df_eval_output'])

"""#### Evaluation Metrics (EER & AUC)

"""

#computing EER
def compute_eer(truth, scores):
  frr, far, th = det_curve(truth, scores)
  abs_diffs = np.abs(frr - far)
  min_index = np.argmin(abs_diffs)
  eer = np.mean((frr[min_index], far[min_index]))
  return eer

##plotting ROC Curve with AUC score
def plot_roc_curve_with_auc(truth, scores, la_or_df):
  fpr, tpr, thresholds = roc_curve(truth,scores)
  roc_auc = auc(fpr, tpr)
  display = RocCurveDisplay(fpr=fpr, tpr=tpr, roc_auc=roc_auc,estimator_name='example estimator')
  display.plot()
  if la_or_df =='la':
    plt.title("ROC curve with AUC score for finetuned LA model by M22AIE227")
  else:
    plt.title("ROC curve with AUC score for finetunned DF model by M22AIE227")
  plt.show()

"""##### LA Model Evaluation"""

la_df = pd.read_csv('/content/finetuned_la_score.txt', sep = ' ', header = None)
la_df.columns = ['truth', 'scores']

la_eer = compute_eer(la_df.truth, la_df.scores)
print("EER (Equal Error Rate) for finetuned LA model : ", round(la_eer, 4))

#plotting roc cureve with auc for LA model
plot_roc_curve_with_auc(la_df.truth, la_df.scores, 'la')

"""##### DF Model Evaluation"""

df_df = pd.read_csv('/content/finetuned_df_score.txt', sep = ' ', header = None)
df_df.columns = ['truth', 'scores']

df_eer = compute_eer(df_df.truth, df_df.scores)
print("EER (Equal Error Rate) for fintuned DF model : ", round(df_eer, 4))

#plotting roc cureve with auc for LA model
plot_roc_curve_with_auc(df_df.truth, df_df.scores, 'df')

