"""On-board object detector (torchvision Faster R-CNN MobileNetV3-Large FPN) for the robot camera."""
from __future__ import annotations

from pathlib import Path

import numpy as np

CLASSES = ["__background__", "fire", "gas_leak", "person", "debris"]
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = ROOT / "models" / "detector_frcnn_mnv3.pt"


def bgra_to_rgb(buf: bytes, width: int, height: int) -> np.ndarray:
    a = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4)
    return a[:, :, [2, 1, 0]].copy()


def build_model(num_classes: int = len(CLASSES), pretrained: bool = False):
    import torchvision
    from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    if pretrained:
        model = fasterrcnn_mobilenet_v3_large_fpn(
            weights=torchvision.models.detection.FasterRCNN_MobileNet_V3_Large_FPN_Weights.COCO_V1,
            min_size=240, max_size=320)
    else:
        model = fasterrcnn_mobilenet_v3_large_fpn(weights=None, weights_backbone=None,
                                                  min_size=240, max_size=320)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
    return model


class Detector:
    def __init__(self, weights: Path = DEFAULT_WEIGHTS, score_thresh: float = 0.5, device: str | None = None) -> None:
        import torch

        self.torch = torch
        self.available = Path(weights).exists()
        self.score_thresh = score_thresh
        if not self.available:
            return
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = build_model()
        state = torch.load(weights, map_location="cpu", weights_only=True)
        self.model.load_state_dict(state)
        self.model.to(self.device).eval()

    def detect(self, rgb: np.ndarray) -> list[dict]:
        if not self.available:
            return []
        t = self.torch.from_numpy(rgb).permute(2, 0, 1).float().div(255.0).to(self.device)
        with self.torch.no_grad():
            out = self.model([t])[0]
        dets = []
        for box, label, score in zip(out["boxes"].cpu().numpy(), out["labels"].cpu().numpy(), out["scores"].cpu().numpy()):
            if score < self.score_thresh:
                continue
            dets.append({"cls": CLASSES[int(label)], "score": float(score), "box": [float(v) for v in box]})
        return dets
