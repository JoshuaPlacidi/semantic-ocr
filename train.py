import config

import os
os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"
print('--- Experiment: ' + config.EXPERIMENT)
print('  - Devices:', config.DEVICE_IDS)
print('--- Training...')
print('  - Encoder:', config.ENCODER)
print('  - Decoder:', config.DECODER)

from transformers import BertTokenizer
tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')

import torch
import torch.utils.data
import torch.nn.functional as F
torch.backends.cudnn.enabled = False

torch.manual_seed(config.RANDOM_SEED)
#torch.cuda.manual_seed(0)

import numpy as np
from tqdm import tqdm

from utils import AttnLabelConverter
# from model import Model
import string

import json
import pandas as pd

from coco_dataset import get_datasets
from model import get_model

import time

device_ids = config.DEVICE_IDS
device = torch.device(config.PRIMARY_DEVICE if torch.cuda.is_available() else 'cpu')

train_ldr, val_ldr = get_datasets()

## Model Config

character = string.printable[:-6]
converter = AttnLabelConverter(character)

batch_max_length = config.MAX_TEXT_LENGTH

rgb = False

model = get_model(config.SAVED_MODEL)



def get_val_score(model):
    print('  - Running Validation')
    model.eval()

    total = 0
    correct = 0
    case_correct = 0

    pred_dict = dict()

    val_loss = 0

    with torch.no_grad():
        for img_path_batch, img_batch, text_batch, scene_batch, overlap_batch in tqdm(val_ldr):
            image_tensor = img_batch
            text = text_batch

            image = image_tensor.to(device)
            length_for_pred = torch.IntTensor([batch_max_length] * len(img_path_batch)).to(device)
            text_for_pred = torch.LongTensor(config.BATCH_SIZE, batch_max_length + 1).fill_(0).to(device)
            scene_batch = scene_batch.to(device)
            overlap_batch = overlap_batch.to(device)

            #encoded_text, _ = converter.encode(text_batch, batch_max_length=batch_max_length)

            preds = model(image, text_for_pred, scene_batch, overlap_batch, is_train=False)

            #target = encoded_text[:, 1:]  # without [GO] Symbol
            # cost = criterion(preds.view(-1, preds.shape[-1]), target.contiguous().view(-1))
            # val_loss = cost.item()

            _, preds_index = preds.max(2)
            preds_str = converter.decode(preds_index, length_for_pred)

            preds_prob = F.softmax(preds, dim=2)
            preds_max_prob, _ = preds_prob.max(dim=2)

            print(text_batch[0] + ':', preds_str[0])
            
            print(tokenizer.decode(overlap_batch[0]))

            #time.sleep(10)

            for img, text, pred, pred_max_prob in zip(img_batch, text_batch, preds_str, preds_max_prob):

                pred_EOS = pred.find('[s]')
                pred = pred[:pred_EOS]  # prune after "end of sentence" token ([s])
                pred_max_prob = pred_max_prob[:pred_EOS]

                #print('"' + text + '"' + ' - "' + str(pred) + '"')

                if text in pred_dict.keys():
                    d_total, d_correct, ls = pred_dict[text]
                    if(text == str(pred)):
                        case_correct += 1
                        d_correct += 1
                    else:
                        ls.append(str(pred))
                    d_total += 1
                    pred_dict[text] = (d_total, d_correct, ls)
                else:
                    if(text == str(pred)):
                        case_correct += 1
                        pred_dict[text] = (1, 1, [])
                    else:
                        pred_dict[text] = (1, 0, [str(pred)])

                if text.lower() == str(pred).lower():
                    correct += 1

                total += 1

    return round(case_correct*100/total,5), val_loss, pred_dict

# Training

import torch.optim as optim
import time
from utils import AttnLabelConverter, Averager

if rgb:
    input_channel = 3


converter = AttnLabelConverter(character)
criterion = torch.nn.CrossEntropyLoss(ignore_index=0).to(device) 

num_class = len(converter.character)

loss_avg = Averager()

filtered_parameters = []
params_num = []
for p in filter(lambda p: p.requires_grad, model.parameters()):
    filtered_parameters.append(p)
    params_num.append(np.prod(p.size()))

optimizer = optim.AdamW(filtered_parameters, lr=0.0001)#, betas=(beta1, 0.999))
scheduler = optim.lr_scheduler.StepLR(optimizer=optimizer, step_size=5, gamma=0.1)

df = pd.DataFrame(columns=['epoch','cost_avg','val_acc','val_loss'])

start_iter = 0
start_time = time.time()
best_accuracy = -1
best_norm_ED = -1
iteration = start_iter
torch.cuda.empty_cache()
epochs = config.EPOCHS

print('--- Training for ' + str(epochs) + ' epochs. Number of parameters:', sum(params_num))

base_case_correct, val_loss, pred_dict = get_val_score(model)
df = df.append({'epoch': '0', 'cost_avg':'n/a', 'val_acc':base_case_correct, 'val_loss':val_loss}, ignore_index=True)
print(df)

best_model = 59

for epoch in range(config.EPOCHS):
    model.train()
    epoch_cost = 0
    print('  - Epoch: ' + str(epoch+1))
    for img_path_batch, img_batch, text_batch, scene_vector_batch, overlap_vector_batch in tqdm(train_ldr):
        image = img_batch.to(device)

        scene_vector_batch = scene_vector_batch.to(device)
        overlap_vector_batch = overlap_vector_batch.to(device)

        text, length = converter.encode(text_batch, batch_max_length=batch_max_length)
        preds = model(image, text[:, :-1], scene_vector_batch, overlap_vector_batch)
        target = text[:, 1:]  # without [GO] Symbol

        cost = criterion(preds.view(-1, preds.shape[-1]), target.contiguous().view(-1))
        model.zero_grad()
        cost.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 2)
        optimizer.step()
        
        loss_avg.add(cost)
        epoch_cost += cost.item()

        iteration += 1

        # length_for_pred = torch.IntTensor([config.MAX_TEXT_LENGTH] * len(img_path_batch))
        # _, preds_index = preds.max(2)
        # preds_str = converter.decode(preds_index, length_for_pred)
        # print(text_batch[0] + ': ' + preds_str[0])
    
    case_correct, val_loss, pred_dict = get_val_score(model)
    scheduler.step()


    epoch_avg = round(epoch_cost/len(train_ldr),5)
    df = df.append({'epoch': (epoch+1), 'cost_avg':epoch_avg, 'val_acc':case_correct, 'val_loss':val_loss}, ignore_index=True)
    df.to_csv('./results/' + config.EXPERIMENT + '_training_log.csv', index=False)
    print(' - ' + config.EXPERIMENT)
    print(df)
    print('\n\n')

    if case_correct > best_model:
        best_model = case_correct
        results_path = './results/models/' + config.EXPERIMENT + '_e_' + str(epoch+1)

        torch.save(model.state_dict(), results_path  + '.pt')
        with open(results_path + '.json', 'w') as dict_file:
            json.dump(pred_dict, dict_file)
        print('  - Model + Val Dict saved to: ' + results_path + '\n\n')

print('--- Finished Training')