"""Kiến trúc chính xác từ 3-best.ipynb; không tải pretrained khi triển khai."""
from pathlib import Path

import timm
import torch
from torch import nn
from timm.data import resolve_model_data_config


class PackagingNet(nn.Module):
    def __init__(self, model_name: str = "mobilenetv4_conv_medium.e500_r256_in1k", pretrained: bool = False) -> None:
        super().__init__()
        self.backbone = timm.create_model(model_name, pretrained=pretrained, num_classes=0)
        dc = resolve_model_data_config(self.backbone)
        self.register_buffer("mean", torch.tensor(dc["mean"]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(dc["std"]).view(1, 3, 1, 1))
        with torch.no_grad():
            self.backbone.eval()
            nf = self.backbone.forward_head(
                self.backbone.forward_features(torch.zeros(1, 3, 128, 128)), pre_logits=True
            ).shape[1]
        self.drop = nn.Dropout(0.2)
        self.head = nn.Linear(nf, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = (x - self.mean) / self.std
        f = self.backbone.forward_head(self.backbone.forward_features(x), pre_logits=True)
        return self.head(self.drop(f))


def load_model(path: str | Path) -> PackagingNet:
    """Chỉ nạp state_dict; không unpickle packaging_model_full.pth."""
    model = PackagingNet()
    state = torch.load(path, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.cpu().eval()
