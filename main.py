import argparse
import os
import json
import torch
import torch.nn as nn
import time
import numpy as np
import sentencepiece as spm
from torch.utils.data import DataLoader

from data import NMTDataset, collate_fn
from model import Transformer, Encoder, Decoder
from utils import AverageMeter, Logger, str2bool

parser = argparse.ArgumentParser(description='Transformer dialect machine translation')
parser.add_argument('--data-dir', default='/nas/datahub/kr-dialect/dataset',type=str,
                    help='path to dataset directory')
parser.add_argument('--save-path', default='./result',type=str,
                    help='Save path')
parser.add_argument('--batch-size', default=128,type=int,
                    help='batch size')
parser.add_argument('--epoch', default=30,type=int,
                    help='Number of Training Epoch')
parser.add_argument('--vocab-size', default=4000,type=int,
                    help='vocab size')
parser.add_argument('--hidden-dim', default=256,type=int,
                    help='Token Embedding Dimension')
parser.add_argument('--enc-layer', default=6,type=int,
                    help='Number of encoder block')
parser.add_argument('--dec-layer', default=6,type=int,
                    help='Number of decoder block')
parser.add_argument('--enc-head', default=8,type=int,
                    help='Number of attention head in encoder layer')
parser.add_argument('--dec-head', default=8,type=int,
                    help='Number of attention head in decoder layer')
parser.add_argument('--use-loc', default=False,type=str2bool,
                    help='Whether to use location information')
args = parser.parse_args()

def main():
    print(args)
    # Set save path
    save_path = args.save_path
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    # Save configuration
    with open(save_path+'/configuration.json', 'w') as f:
        json.dump(args.__dict__, f, indent=2)

    # Load Data
    sp = spm.SentencePieceProcessor()
    sp.Load(f'{args.data_dir}/bpe_{args.vocab_size}.model')
    train_dataset = NMTDataset(
        args.data_dir,
        sp,
        'train',
        use_loc=args.use_loc
    )
    val_dataset = NMTDataset(
        args.data_dir,
        sp,
        'val',
        use_loc=args.use_loc
    )
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate_fn,
        num_workers=4,
        pin_memory=True)
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collate_fn,
        num_workers=4,
        pin_memory=True)
    
    # Create Model
    device = torch.device('cuda:0')
    num_location = len(np.unique(val_dataset.location_label))
    if args.use_loc:
        num_vocab = sp.get_piece_size()+num_location
    else:
        num_vocab = sp.get_piece_size()

    encoder = Encoder(
        input_dim=num_vocab, 
        hidden_dim=args.hidden_dim, 
        n_layers=args.enc_layer, 
        n_heads=args.enc_head, 
        pf_dim=512, 
        dropout_ratio=0.1, 
        device=device)

    decoder = Decoder(
        output_dim=num_vocab, 
        hidden_dim=args.hidden_dim, 
        n_layers=args.dec_layer, 
        n_heads=args.dec_head, 
        pf_dim=512, 
        dropout_ratio=0.1, 
        device=device)

    model = Transformer(
        encoder,
        decoder,
        sp.pad_id(),
        sp.pad_id(),
        device
    ).to(device)
    model.apply(initialize_weights)

    # Loss & Optimizer
    criterion = nn.CrossEntropyLoss(ignore_index=sp.pad_id())
    optimizer = torch.optim.Adam(model.parameters(),lr=0.0005,weight_decay=0.0001)

    # Logger
    train_logger = Logger(os.path.join(save_path,'train.log'))
    val_logger = Logger(os.path.join(save_path, 'val.log'))

    # Train & validate
    for epoch in range(args.epoch):
        train(model,train_loader,optimizer,criterion,train_logger,epoch,device)
        validate(model,val_loader,criterion,val_logger,epoch,device)

    # Save Model
    torch.save(model.state_dict(),os.path.join(save_path,'last_model.pth'))

def train(model,loader,optimizer,criterion,train_logger,epoch,device):
    model.train()

    train_loss = AverageMeter()
    iter_time = AverageMeter()
    data_time = AverageMeter()

    end = time.time()
    for i,(src,tgt,label) in enumerate(loader):
        src,tgt,label = src.to(device), tgt.to(device),label.to(device)
        data_time.update(time.time()-end)

        optimizer.zero_grad()
        output, _ = model(src,tgt[:,:-1]) # Output is prediction for each token except the last one

        output_dim = output.shape[-1]
        output= output.contiguous().view(-1, output_dim)
        tgt = tgt[:,1:].contiguous().view(-1)

        loss = criterion(output,tgt)
        loss.backward()
        optimizer.step()

        train_loss.update(loss.item(),src.size(0))
        iter_time.update(time.time()-end)
        end = time.time()

        if i%100 == 0:
            print(f"[{i+1}/{len(loader)}][{epoch+1}/{args.epoch}] \
            Train Loss : {train_loss.avg:.4f} Iter Time : {iter_time.avg:.4f} \
            Data Time : {data_time.avg:.4f}")

    train_logger.write([epoch,train_loss.avg,iter_time.avg,data_time.avg])

def validate(model,loader,criterion,val_logger,epoch,device):
    model.eval()

    val_loss = AverageMeter()
    end = time.time()
    with torch.no_grad():
        for i,(src,tgt,label) in enumerate(loader):
            src,tgt,label = src.to(device), tgt.to(device),label.to(device)
            output, _ = model(src,tgt[:,:-1]) # Output is prediction for each token except the last one
            output_dim = output.shape[-1]
            output= output.contiguous().view(-1, output_dim)
            tgt = tgt[:,1:].contiguous().view(-1)
            loss = criterion(output,tgt)
            val_loss.update(loss.item(),src.size(0))

    print(f"\n Epoch : [{epoch+1}]/[{args.epoch}] Validation Loss : {val_loss.avg:.4f} \n")
    val_logger.write([epoch,val_loss.avg])


def initialize_weights(m):
    if hasattr(m, 'weight') and m.weight.dim() > 1:
        nn.init.xavier_uniform_(m.weight.data)


if __name__ == '__main__':
    main()
