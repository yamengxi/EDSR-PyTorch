import torch
import torch.nn as nn
import torch.nn.functional as F

from model import common


def make_model(args, parent=False):
    return BFN(args)

def generate_masks(num):
    masks = []
    for i in range(num):
        now = list(range(2 ** num))
        length = 2 ** (num - i)
        for j in range(2 ** i):
            tmp = now[j*length:j*length+length//2]
            now[j*length:j*length+length//2] = now[j*length+length//2:j*length+length]
            now[j*length+length//2:j*length+length] = tmp
        masks.append(now)
    return masks


class MainBlock(nn.Module):
    def __init__(self, in_channels, act):
        super(MainBlock, self).__init__()
        self.num_butterflies = 6
        self.masks = generate_masks(self.num_butterflies)
        # import pdb
        # pdb.set_trace()

        self.conv_acts = []
        for i in range(self.num_butterflies * 2):
            self.conv_acts.append(
                nn.Sequential(nn.Conv2d(in_channels, in_channels, 3, 1, 1, groups=in_channels), act(in_channels))
            )
        self.conv_acts = nn.Sequential(*self.conv_acts)


    def forward(self, x):
        last = x
        for i in range(self.num_butterflies):
            now = self.conv_acts[i*2](last)
            last = last[:,self.masks[i],:,:]
            now = now + self.conv_acts[i*2+1](last)
            last = now
        return now + x


class BFN(nn.Module):
    """BFN network structure.

    Args:
        args.scale (list[int]): Upsampling scale for the input image.
        args.n_colors (int): Channels of the input image.
        args.n_feats (int): Channels of the mid layer.
        args.n_resblocks (int): 
        act: Activate function used in BFN. Default: nn.PReLU.
    """
    def __init__(self, args):
        super(BFN, self).__init__()
        assert len(args.scale) == 1
        scale = args.scale[0]
        n_colors = args.n_colors
        n_feats = args.n_feats
        n_resblocks = args.n_resblocks
        if args.act == 'relu':
            act = nn.ReLU
        elif args.act == 'prelu':
            act = nn.PReLU
        else:
            raise NotImplementedError("")
        rgb_range = args.rgb_range


        # RGB mean for DIV2K
        rgb_mean = (0.4488, 0.4371, 0.4040)
        rgb_std = (1.0, 1.0, 1.0)
        self.sub_mean = common.MeanShift(rgb_range, rgb_mean, rgb_std)

        self.head = nn.Sequential(nn.Conv2d(n_colors, n_feats, 3, 1, 1), act())

        self.main_blocks = []
        for i in range(n_resblocks):
            self.main_blocks.append(MainBlock(n_feats, act))
        self.main_blocks = nn.Sequential(*self.main_blocks)

        self.features_fusion_module = nn.Sequential(
            nn.Conv2d(n_feats * (n_resblocks + 1), n_feats * 2, 1, 1, 0),
            act(),
            nn.Conv2d(n_feats * 2, n_feats, 3, 1, 1),
            act(),
            nn.Conv2d(n_feats, n_feats, 3, 1, 1)
        )

        self.upsampler = common.Upsampler(common.default_conv, scale, n_feats)

        self.tail = nn.Sequential(
            act(n_feats),
            nn.Conv2d(n_feats, n_feats, 3, 1, 1),
            act(n_feats),
            nn.Conv2d(n_feats, n_colors, 3, 1, 1)
        )

        self.add_mean = common.MeanShift(rgb_range, rgb_mean, rgb_std, 1)


    def forward(self, x):
        x = self.sub_mean(x)
        x = self.head(x)

        now = x
        outs = [now]
        for main_block in self.main_blocks:
            now = main_block(now)
            outs.append(now)

        out = torch.cat(outs, 1)
        out = self.features_fusion_module(out) + x

        out = self.upsampler(out)

        out = self.tail(out)

        out = self.add_mean(out)

        return out


if __name__ == '__main__':
    # test network
    import os
    os.environ['CUDA_VISIBLE_DEVICES'] = '1'

    import argparse
    args = argparse.Namespace()
    args.scale = [4]
    args.patch_size = 192
    args.n_colors = 3
    args.n_feats = 64
    args.n_resblocks = 12
    args.act = 'prelu'
    args.rgb_range = 255
    # args.version = 'v1'


    model = BFN(args)
    model.train()

    from torchsummary import summary

    summary(model.cuda(), input_size=(3, 64, 64), batch_size=8)

    # 300*(batch_size*1000)/batch_size=300000 次迭代
    # 设每次迭代需要x秒，那么训练完毕需要300000x秒，折合83.3333x小时
    # x的可接受范围在0.5s左右，也即每次迭代必须在0.5s左右结束
