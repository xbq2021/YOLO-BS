import math
from collections import OrderedDict

import torch
import torch.nn as nn
import torch.nn.functional as F
class TransformerLayer(nn.Module):
    # Transformer layer https://arxiv.org/abs/2010.11929 (LayerNorm layers removed for better performance)
    def __init__(self, c, num_heads):
        super().__init__()
        self.q = nn.Linear(c, c, bias=False)
        self.k = nn.Linear(c, c, bias=False)
        self.v = nn.Linear(c, c, bias=False)
        self.ma = nn.MultiheadAttention(embed_dim=c, num_heads=num_heads)
        self.fc1 = nn.Linear(c, c, bias=False)
        self.fc2 = nn.Linear(c, c, bias=False)

    def forward(self, x):
        x = self.ma(self.q(x), self.k(x), self.v(x))[0] + x
        x = self.fc2(self.fc1(x)) + x
        return x
class TransformerBlock(nn.Module):
    # Vision Transformer https://arxiv.org/abs/2010.11929
    def __init__(self, c1, c2, num_heads, num_layers):
        super().__init__()
        self.conv = None
        if c1 != c2:
            self.conv = BasicConv(c1, c2)
        self.linear = nn.Linear(c2, c2)  # learnable position embedding
        self.tr = nn.Sequential(*(TransformerLayer(c2, num_heads) for _ in range(num_layers)))
        self.c2 = c2

    def forward(self, x):
        if self.conv is not None:
            x = self.conv(x)
        b, _, w, h = x.shape
        p = x.flatten(2).permute(2, 0, 1)
        return self.tr(p + self.linear(p)).permute(1, 2, 0).reshape(b, self.c2, w, h)
class Bottleneck(nn.Module):
    # Standard bottleneck
    def __init__(self, c1, c2, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, shortcut, groups, expansion
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = BasicConv(c1, c_, 1, 1)
        self.cv2 = BasicConv(c_, c2, 3, 1, g=g)
        self.add = shortcut and c1 == c2

    def forward(self, x):
        return x + self.cv2(self.cv1(x)) if self.add else self.cv2(self.cv1(x))
# class C3(nn.Module):
#     # CSP Bottleneck with 3 convolutions
#     def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
#         super().__init__()
#         c_ = int(c2 * e)  # hidden channels
#         self.cv1 = BasicConv(c1, c_, 1, 1)
#         self.cv2 = BasicConv(c1, c_, 1, 1)
#         self.cv3 = BasicConv(2 * c_, c2, 1)  # optional act=FReLU(c2)
#         self.m = nn.Sequential(*(Bottleneck(c_, c_, shortcut, g, e=1.0) for _ in range(n)))
#         # self.m = nn.Sequential(*(CrossConv(c_, c_, 3, 1, g, 1.0, shortcut) for _ in range(n)))

#     def forward(self, x):
#         return self.cv3(torch.cat((self.m(self.cv1(x)), self.cv2(x)), 1))
class Transformerblock_body(nn.Module):
    # C3 module with TransformerBlock()
    def __init__(self, c1, c2, n=4, shortcut=True, g=1, e=0.5):
        super().__init__()
        c_ = int(c2 * e)
        self.downsample_conv = BasicConv(c1, c2, 3, 2)
        self.cv1 = BasicConv(c2, c_, 1, 1)
        self.cv2 = BasicConv(c2, c_, 1, 1)
        self.cv3 = BasicConv(2 * c_, c2, 1)  # optional act=FReLU(c2)
        self.m = TransformerBlock(c_, c_, 4, n)
    def forward(self, x):
        x = self.downsample_conv(x)
        return self.cv3(torch.cat((self.m(self.cv1(x)), self.cv2(x)), 1))
#-------------------------------------------------#
#   MISH????????????
#-------------------------------------------------#
class Mish(nn.Module):
    def __init__(self):
        super(Mish, self).__init__()

    def forward(self, x):
        return x * torch.tanh(F.softplus(x))

#---------------------------------------------------#
#   ????????? -> ?????? + ????????? + ????????????
#   Conv2d + BatchNormalization + Mish
#---------------------------------------------------#
class BasicConv(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=1, stride=1):
        super(BasicConv, self).__init__()

        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride, kernel_size//2, bias=False)
        self.bn = nn.BatchNorm2d(out_channels)
        self.activation = Mish()

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        x = self.activation(x)
        return x

#---------------------------------------------------#
#   CSPdarknet???????????????????????????
#   ????????????????????????
#---------------------------------------------------#
class Resblock(nn.Module):
    def __init__(self, channels, hidden_channels=None):
        super(Resblock, self).__init__()

        if hidden_channels is None:
            hidden_channels = channels

        self.block = nn.Sequential(
            BasicConv(channels, hidden_channels, 1),
            BasicConv(hidden_channels, channels, 3)
        )

    def forward(self, x):
        return x + self.block(x)

#--------------------------------------------------------------------#
#   CSPdarknet????????????
#   ????????????ZeroPadding2D??????????????????2x2????????????????????????????????????
#   ?????????????????????????????????shortconv???????????????????????????????????????????????????
#   ??????????????????num_blocks?????????????????????????????????????????????
#   ????????????CSPdarknet???????????????????????????????????????+????????????????????????
#--------------------------------------------------------------------#
class Resblock_body(nn.Module):
    def __init__(self, in_channels, out_channels, num_blocks, first):
        super(Resblock_body, self).__init__()
        #----------------------------------------------------------------#
        #   ?????????????????????2x2????????????????????????????????????
        #----------------------------------------------------------------#
        self.downsample_conv = BasicConv(in_channels, out_channels, 3, stride=2)

        if first:
            #--------------------------------------------------------------------------#
            #   ?????????????????????????????????self.split_conv0???????????????????????????????????????????????????
            #--------------------------------------------------------------------------#
            self.split_conv0 = BasicConv(out_channels, out_channels, 1)

            #----------------------------------------------------------------#
            #   ??????????????????num_blocks?????????????????????????????????????????????
            #----------------------------------------------------------------#
            self.split_conv1 = BasicConv(out_channels, out_channels, 1)  
            self.blocks_conv = nn.Sequential(
                Resblock(channels=out_channels, hidden_channels=out_channels//2),
                BasicConv(out_channels, out_channels, 1)
            )

            self.concat_conv = BasicConv(out_channels*2, out_channels, 1)
        else:
            #--------------------------------------------------------------------------#
            #   ?????????????????????????????????self.split_conv0???????????????????????????????????????????????????
            #--------------------------------------------------------------------------#
            self.split_conv0 = BasicConv(out_channels, out_channels//2, 1)

            #----------------------------------------------------------------#
            #   ??????????????????num_blocks?????????????????????????????????????????????
            #----------------------------------------------------------------#
            self.split_conv1 = BasicConv(out_channels, out_channels//2, 1)
            self.blocks_conv = nn.Sequential(
                *[Resblock(out_channels//2) for _ in range(num_blocks)],
                BasicConv(out_channels//2, out_channels//2, 1)
            )

            self.concat_conv = BasicConv(out_channels, out_channels, 1)

    def forward(self, x):
        x = self.downsample_conv(x)

        x0 = self.split_conv0(x)

        x1 = self.split_conv1(x)
        x1 = self.blocks_conv(x1)

        #------------------------------------#
        #   ??????????????????????????????
        #------------------------------------#
        x = torch.cat([x1, x0], dim=1)
        #------------------------------------#
        #   ??????????????????????????????
        #------------------------------------#
        x = self.concat_conv(x)

        return x

#---------------------------------------------------#
#   CSPdarknet53 ???????????????
#   ???????????????416x416x3?????????
#   ??????????????????????????????
#---------------------------------------------------#
class CSPDarkNet(nn.Module):
    def __init__(self, layers):
        super(CSPDarkNet, self).__init__()
        self.inplanes = 32
        # 416,416,3 -> 416,416,32
        self.conv1 = BasicConv(3, self.inplanes, kernel_size=3, stride=1)
        self.feature_channels = [64, 128, 256, 512, 1024]

        self.stages = nn.ModuleList([
            # 416,416,32 -> 208,208,64
            Resblock_body(self.inplanes, self.feature_channels[0], layers[0], first=True),
            # 208,208,64 -> 104,104,128
            Resblock_body(self.feature_channels[0], self.feature_channels[1], layers[1], first=False),
            # 104,104,128 -> 52,52,256
            Resblock_body(self.feature_channels[1], self.feature_channels[2], layers[2], first=False),
            # 52,52,256 -> 26,26,512
            Resblock_body(self.feature_channels[2], self.feature_channels[3], layers[3], first=False),
            # 26,26,512 -> 13,13,1024
            #Resblock_body(self.feature_channels[3], self.feature_channels[4], layers[4], first=False)
            Transformerblock_body(self.feature_channels[3], self.feature_channels[4], layers[4])
        ])

        self.num_features = 1
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()


    def forward(self, x):
        x = self.conv1(x)

        x = self.stages[0](x)
        x = self.stages[1](x)
        x = self.stages[2](x)
        out4 = self.stages[3](x)
        out5 = self.stages[4](out4)

        return out4, out5
    
def darknet53(pretrained):
    model = CSPDarkNet([1, 2, 8, 8, 4])
    if pretrained:
        model.load_state_dict(torch.load("model_data/CSPdarknet53_backbone_weights.pth"))
    return model
