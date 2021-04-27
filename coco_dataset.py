import json
import string

import torch
import torchvision.transforms as transforms
from PIL import Image
from torch.utils.data import Dataset
from tqdm import tqdm
from transformers import BertTokenizer

import coco_text
import config

tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')

class Annotations_Dataset(Dataset):
    def __init__(self, set='train'):
        
        # Open COCO-Text api
        ct = coco_text.COCO_Text(config.COCO_TEXT_API_PATH)

        self.annotations = []
        self.to_tensor = transforms.ToTensor()
        self.resize = transforms.Resize((32,100))

        # Open text annotations
        with open(config.COCO_TEXT_API_PATH) as f:
            text_annotations = json.load(f)        

        # Load object annotations
        self.classes = ['background']

        if config.SEMANTIC_SOURCE == 'VG': # If using Visual Genome object data
            with open('./annotations/features/vg_frequency.json') as object_annotations: # Load object features
                self.object_annotations = json.load(object_annotations)

            with open('./annotations/features/vg_classes.txt') as VG_labels: # Load visual genome class labels
                for object in VG_labels.readlines():
                    self.classes.append(object.split(',')[0].lower().strip().replace("'", ''))

        elif config.SEMANTIC_SOURCE == 'COCO': # If using MS COCO object data
            with open('./annotations/features/coco_frequency.json') as object_annotations:
                self.object_annotations = json.load(object_annotations)

            with open('./annotations/features/coco_classes.txt') as COCO_labels: # Load MS COCO class labels
                for object in COCO_labels.readlines():
                    self.classes.append(object.split(',')[0].lower().strip().replace("'", ''))

        if config.SEMANTIC_FORM == 'FREQ':
            self.overlap_vector_size = len(self.classes)
            self.scene_vector_size = len(self.classes)

        # Process text annotations
        for _, anno in tqdm(text_annotations['anns'].items()):
            if anno['legibility'] == 'legible': # If annotation is legibile

                image = ct.loadImgs(ids=anno['image_id']) # Load annotation image data

                if image[0]['set'] == set: # Check if in train or val set
                    
                    # Load and set annotations image path and its scene and overlap data
                    anno['img_path'] = config.IMAGE_PATH + image[0]['file_name']
                    anno['scene'] = self.object_annotations[str(anno['image_id'])]['scene']
                    anno['overlap'] = self.object_annotations[str(anno['image_id'])]['overlap'][str(anno['id'])]

                    # If set == check annotation is a model compatible string (legal characters, <25 length etc...), if val just check language is english
                    if set == 'train':
                        if check_anno(anno['utf8_string']):
                            self.annotations.append(anno)
                    else:
                        if anno['language'] == 'english':
                            self.annotations.append(anno)
                       
    def __len__(self):
        return len(self.annotations)
    
    def __getitem__(self, index):

        # Load annotation and image
        anno = self.annotations[index]
        img = Image.open(anno['img_path']).convert('L')
        img = img.crop((anno['bbox'][0], anno['bbox'][1], anno['bbox'][0] + anno['bbox'][2], anno['bbox'][1] + anno['bbox'][3]))  
        img = self.resize(img)
        img = self.to_tensor(img)
        
        if config.SEMANTIC_FORM == 'BERT':
            overlap = get_bert_tokens(anno, self.classes, 7, 20, 'overlap')
            scene = get_bert_tokens(anno, self.classes, 7, 60, 'scene')

        elif config.SEMANTIC_FORM == 'FREQ':
            #print(anno['overlap'], anno['scene'])

            overlap_padded = torch.zeros(20)
            overlap_objs = torch.LongTensor(get_object_vector(anno['overlap'], self.overlap_vector_size))
            if overlap_objs.shape[0] > 0:
                overlap_objs = overlap_objs
                overlap_padded[:overlap_objs.shape[0]] = overlap_objs

            scene_padded = torch.zeros(70)
            scene_objs = torch.LongTensor(get_object_vector(anno['scene'], self.scene_vector_size))
            if scene_objs.shape[0] > 60:
                print(anno['img_path'], anno['scene'])
            if scene_objs.shape[0] > 0:
                scene_objs = scene_objs
                scene_padded[:scene_objs.shape[0]] = scene_objs

            
            #print(overlap_padded.shape)

        else:
            overlap = torch.zeros(1)
            scene = torch.zeros(1)

        

        return anno['img_path'], img, anno['utf8_string'], scene_padded, overlap_padded

def get_bert_tokens(anno, classes, max_length, sequence_pad, key, encode_frequency = False):
    # tokens = []
    # for key, val in anno[key].items():
    #     for _ in range(val):
    #         token = torch.tensor(tokenizer.encode(classes[int(key)], max_length=max_length, padding='max_length'))
    #         tokens.append(token) 
    # if not tokens: # If tokens is empty
    #     token = torch.tensor(tokenizer.encode(classes[0], max_length=max_length, padding='max_length'))
    #     tokens.append(token)

    # for _ in range(sequence_pad-len(tokens)):
    #     tokens.append(torch.zeros(max_length))

    # tokens = torch.stack(tokens)

    sentence = ""
    for k, v in anno[key].items():
        if encode_frequency:
            for _ in range(v):
                sentence += classes[int(k) + 1] + ' [SEP] '
        else:
            sentence += classes[int(k) + 1] + ' [SEP] '

    sentence = sentence[:-7]

    tokens = torch.tensor(tokenizer.encode(sentence, max_length=sequence_pad, padding='max_length', truncation='longest_first'))

    return tokens

# takes a dictionary of type dict[object_label] = count and converts it to a sparse vector
def get_object_vector(dict, length):

    objs_vectors = []

    for key, val in dict.items():
        for _ in range(val):
            objs_vectors.append(int(key))

    return objs_vectors
    
def check_anno(anno_text):
    return anno_text == anno_text.strip().translate({ord(c): None for c in string.printable[-6:]+'/°-'})[0:25]#string.printable[-38:]+'°'})[0:25]

def get_datasets():
    print('--- Loading Data')
    # txt_annos_path = "F:/dev/Datasets/COCO/2014/COCO_Text_2014.json"
    # feats_path = './comb_data/VG/area_resize.json'#'./comb_data/VG_area_resize_iou75.json' #new_comb.json  './comb_data/tfidf_area_resize.json'
    # image_path = "F:/dev/Datasets/COCO/2014/images/train2014/"

    train_data = Annotations_Dataset(set='train')
    val_data = Annotations_Dataset(set='val')


    train_loader = torch.utils.data.DataLoader(train_data, batch_size=config.BATCH_SIZE, shuffle=True,num_workers=0)
    val_loader = torch.utils.data.DataLoader(val_data, batch_size=config.BATCH_SIZE, shuffle=True,num_workers=0)

    print('  - ' + str(len(train_loader)) + ' training batches')
    print('  - ' + str(len(val_loader)) + ' val batches')

    return train_loader, val_loader