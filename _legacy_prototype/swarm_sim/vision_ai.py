"""Torchvision MobileNetV3 Vision Backbone, Pixel Saliency Localizer & Neural Tactical Policy.

AI Models Active in `C:\\Users\\mange\\anaconda3\\envs\\pytorch` (CUDA Accelerated):
1. `TorchvisionHazardDetector` (`torchvision.models.mobilenet_v3_small` stem + GroupNorm feature pyramid):
   Classifies 128x128x3 onboard RGB camera frames into 5 semantic scene classes
   (`clear_room`, `fire_hazard`, `gas_hazard`, `trapped_person`, `corridor_wall`)
   and regresses a continuous hazard severity score in [0, 1].
2. `localize_hazard_bbox_opencv`: Uses OpenCV (`cv2.findContours` + morphological filtering)
   on the live 128x128 RGB camera frame to detect and localize the exact bounding box
   `(x1, y1, x2, y2)` of flames/smoke, chemical gas clouds, or trapped personnel directly from pixels.
3. `SwarmTacticalPolicyNet`: 3-layer GELU/LayerNorm neural policy network that maps a 10-D
   multi-agent context vector into a CBBA bid utility [0, 1] and hierarchy turn-yield probability [0, 1].
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as tv_models


HAZARD_CLASSES: tuple[str, ...] = (
    "clear_room",
    "fire_hazard",
    "gas_hazard",
    "trapped_person",
    "corridor_wall",
)

CLASS_TO_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(HAZARD_CLASSES)}
DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "swarm_mobilenet_v3_realistic.pt"


@dataclass(frozen=True)
class VisionPrediction:
    predicted_class: str
    class_index: int
    confidence: float
    severity: float
    probabilities: dict[str, float]
    entropy: float
    bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class TacticalDecisionOutput:
    bid_utility: float
    pass_probability: float
    should_pass: bool
    hierarchy_tier: int
    specialty_mode: str  # "PRIMARY", "BACKUP", "INELIGIBLE"
    reasoning: str


class TorchvisionHazardDetector(nn.Module):
    """Torchvision `mobilenet_v3_small` feature extractor + GroupNorm pyramid + dual heads."""

    def __init__(self, num_classes: int = len(HAZARD_CLASSES)) -> None:
        super().__init__()
        backbone = tv_models.mobilenet_v3_small(weights=None)
        self.tv_stem = nn.Sequential(*list(backbone.features.children())[:4])
        for m in self.tv_stem.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.momentum = 0.4
        self.conv_head = nn.Sequential(
            nn.Conv2d(24, 48, kernel_size=3, stride=2, padding=1),
            nn.GroupNorm(8, 48),
            nn.GELU(),
            nn.Conv2d(48, 64, kernel_size=3, stride=2, padding=1),
            nn.GroupNorm(8, 64),
            nn.GELU(),
            nn.AdaptiveAvgPool2d((4, 4)),
        )
        self.neck = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 4 * 4, 160),
            nn.LayerNorm(160),
            nn.Hardswish(),
        )
        self.class_head = nn.Linear(160, num_classes)
        self.severity_head = nn.Sequential(
            nn.Linear(160, 48),
            nn.Hardswish(),
            nn.Linear(48, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        feat = self.neck(self.conv_head(self.tv_stem(x)))
        logits = self.class_head(feat)
        severity = self.severity_head(feat).squeeze(-1)
        return logits, severity


class SwarmTacticalPolicyNet(nn.Module):
    """Neural policy scoring multi-building suitability & hierarchy turn-taking."""

    def __init__(self, input_dim: int = 10) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.LayerNorm(64),
            nn.GELU(),
            nn.Linear(64, 48),
            nn.GELU(),
            nn.Linear(48, 2),
        )

    def forward(self, features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        raw = self.net(features)
        bid_utility = torch.sigmoid(raw[..., 0])
        pass_prob = torch.sigmoid(raw[..., 1])
        return bid_utility, pass_prob


def draw_realistic_hazard_pattern(
    img: np.ndarray,
    h_class: str,
    cx: int,
    cy: int,
    scale: int,
    rng: np.random.Generator | None = None,
) -> None:
    """Draw multi-layered realistic industrial hazard visuals onto a float32 RGB image in [0, 1]."""
    height, width, _ = img.shape
    yy, xx = np.ogrid[:height, :width]
    dist_sq = (xx - cx) ** 2 + (yy - cy) ** 2

    if h_class == "clear_room":
        # Industrial steel pallet rack + green safety inspection beacon
        rack_w = max(8, int(scale * 0.95))
        rack_h = max(10, int(scale * 1.15))
        x0, x1 = max(2, cx - rack_w), min(width - 2, cx + rack_w)
        y0, y1 = max(2, cy - rack_h), min(height - 2, cy + rack_h)
        # Uprights & shelves
        img[y0:y1, x0 : min(width, x0 + 3)] = np.array([0.18, 0.45, 0.68], dtype=np.float32)
        img[y0:y1, max(0, x1 - 3) : x1] = np.array([0.18, 0.45, 0.68], dtype=np.float32)
        for sy in (y0 + rack_h // 3, y0 + 2 * rack_h // 3, y1 - 3):
            img[max(0, sy) : min(height, sy + 2), x0:x1] = np.array([0.85, 0.52, 0.15], dtype=np.float32)
        # Glowing green safety verification core
        beacon = dist_sq <= max(16, (scale // 2) ** 2)
        img[beacon] = np.array([0.10, 0.78, 0.46], dtype=np.float32)
        core = dist_sq <= max(6, (scale // 4) ** 2)
        img[core] = np.array([0.52, 0.98, 0.78], dtype=np.float32)

    elif h_class == "fire_hazard":
        # Billowing dark gray/black smoke plume above the fire
        smoke_cy = max(8, int(cy - scale * 0.75))
        smoke_mask = (
            (xx - cx) ** 2 / max(1.0, (scale * 1.25) ** 2)
            + (yy - smoke_cy) ** 2 / max(1.0, (scale * 1.05) ** 2)
        ) <= 1.0
        img[smoke_mask] = 0.35 * img[smoke_mask] + 0.65 * np.array([0.18, 0.19, 0.22], dtype=np.float32)

        # Multi-tongue flickering outer red/orange flames
        for offset_x, h_mult in ((-0.35, 1.1), (0.0, 1.35), (0.35, 1.05)):
            fcx = int(cx + offset_x * scale)
            flame = (
                (xx - fcx) ** 2 / max(1.0, (scale * 0.62) ** 2)
                + (yy - cy) ** 2 / max(1.0, (scale * h_mult) ** 2)
            ) <= 1.0
            img[flame] = np.array([0.96, 0.26, 0.05], dtype=np.float32)

        # Mid orange-gold flame body
        mid_flame = (
            (xx - cx) ** 2 / max(1.0, (scale * 0.65) ** 2)
            + (yy - (cy + scale * 0.1)) ** 2 / max(1.0, (scale * 0.90) ** 2)
        ) <= 1.0
        img[mid_flame] = np.array([1.0, 0.62, 0.10], dtype=np.float32)

        # White-hot thermal core at base
        core_flame = (
            (xx - cx) ** 2 / max(1.0, (scale * 0.36) ** 2)
            + (yy - (cy + scale * 0.22)) ** 2 / max(1.0, (scale * 0.50) ** 2)
        ) <= 1.0
        img[core_flame] = np.array([1.0, 0.95, 0.55], dtype=np.float32)

    elif h_class == "gas_hazard":
        # Pressurized yellow/green hazmat tanks + expanding toxic vapor cloud
        for r_mult, alpha in ((1.35, 0.38), (0.95, 0.58)):
            cloud = dist_sq <= ((scale * r_mult) ** 2)
            img[cloud] = (1.0 - alpha) * img[cloud] + alpha * np.array([0.52, 0.90, 0.14], dtype=np.float32)

        # Twin pressurized chemical cylinders with dark hazard stripes
        for dx_tank in (-int(scale * 0.32), int(scale * 0.32)):
            tcx = cx + dx_tank
            tank_mask = (np.abs(xx - tcx) <= max(3, scale // 4)) & (np.abs(yy - cy) <= int(scale * 0.85))
            img[tank_mask] = np.array([0.78, 0.96, 0.18], dtype=np.float32)
            stripe_mask = tank_mask & ((yy // 4) % 2 == 0)
            img[stripe_mask] = np.array([0.24, 0.32, 0.12], dtype=np.float32)

    elif h_class == "trapped_person":
        # Articulated human casualty with high-visibility rescue vest, limbs, and helmet/head
        torso_w = max(4, scale // 3)
        torso_h = max(8, int(scale * 0.75))
        torso = (np.abs(xx - cx) <= torso_w) & (yy >= cy - torso_h // 2) & (yy <= cy + torso_h // 2)
        img[torso] = np.array([0.90, 0.18, 0.72], dtype=np.float32)
        # Reflective silver-yellow SAR vest stripes across torso
        vest_stripe = torso & (np.abs(yy - cy) <= 2)
        img[vest_stripe] = np.array([0.98, 0.95, 0.35], dtype=np.float32)

        # Head above torso
        head_y = cy - torso_h // 2 - max(4, scale // 3)
        head = ((xx - cx) ** 2 + (yy - head_y) ** 2) <= max(12, (scale // 3) ** 2)
        img[head] = np.array([0.98, 0.78, 0.88], dtype=np.float32)

        # Outstretched arms & legs
        arms = (np.abs(xx - cx) <= int(scale * 0.75)) & (np.abs(yy - (cy - torso_h // 4)) <= max(2, scale // 7))
        legs = (np.abs(xx - cx) <= int(scale * 0.45)) & (yy > cy + torso_h // 2) & (yy <= min(height - 2, cy + scale))
        img[arms] = np.array([0.84, 0.16, 0.68], dtype=np.float32)
        img[legs] = np.array([0.25, 0.32, 0.52], dtype=np.float32)


def localize_hazard_bbox_opencv(
    rgb_frame: np.ndarray,
    predicted_class: str,
) -> tuple[int, int, int, int] | None:
    """Localize the hazard bounding box directly from image pixels using OpenCV color/saliency contours."""
    if predicted_class == "corridor_wall":
        return None
    h, w, _ = rgb_frame.shape
    r = rgb_frame[:, :, 0]
    g = rgb_frame[:, :, 1]
    b = rgb_frame[:, :, 2]

    if predicted_class == "fire_hazard":
        mask = (r > 0.78) & (g < 0.75) & (b < 0.35) & ((r - b) > 0.45)
    elif predicted_class == "gas_hazard":
        mask = (g > 0.68) & (r > 0.40) & (b < 0.35) & ((g - b) > 0.35)
    elif predicted_class == "trapped_person":
        mask = (r > 0.68) & (b > 0.48) & (g < 0.45)
    else:  # clear_room safety beacon / rack
        mask = (g > 0.62) & (b > 0.35) & (r < 0.45)

    mask_u8 = (mask.astype(np.uint8)) * 255
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 12:
        return None
    x, y, bw, bh = cv2.boundingRect(largest)
    pad = 4
    return (
        max(2, x - pad),
        max(2, y - pad),
        min(w - 2, x + bw + pad),
        min(h - 2, y + bh + pad),
    )


def generate_synthetic_camera_sample(
    class_idx: int,
    rng: np.random.Generator,
    height: int = 128,
    width: int = 128,
) -> tuple[np.ndarray, float]:
    """Generate a 128x128x3 daylight architectural RGB training frame matching the 3D renderer."""
    img = np.zeros((height, width, 3), dtype=np.float32)
    horizon = int(rng.integers(height // 3, 2 * height // 3))

    # Daylight sky / ceiling & concrete floor with subtle grid lines
    img[:horizon, :, :] = np.array([0.84, 0.90, 0.96], dtype=np.float32)
    img[horizon:, :, :] = np.array([0.56, 0.58, 0.62], dtype=np.float32)

    wall_top = max(8, horizon - int(rng.integers(18, 36)))
    wall_bot = min(height - 8, horizon + int(rng.integers(18, 36)))
    img[wall_top:wall_bot, :, :] = np.array([0.74, 0.77, 0.81], dtype=np.float32)

    cx = int(rng.integers(width // 4, 3 * width // 4))
    cy = int(rng.integers(height // 3, 2 * height // 3))
    scale = int(rng.integers(15, 33))

    h_name = HAZARD_CLASSES[class_idx]
    if h_name != "corridor_wall":
        draw_realistic_hazard_pattern(img, h_name, cx, cy, scale, rng)
        severity = {
            "clear_room": float(rng.uniform(0.02, 0.12)),
            "fire_hazard": float(rng.uniform(0.82, 0.99)),
            "gas_hazard": float(rng.uniform(0.65, 0.90)),
            "trapped_person": float(rng.uniform(0.85, 0.98)),
        }[h_name]
    else:
        door_x = int(rng.integers(20, width - 20))
        img[wall_top:wall_bot, max(0, door_x - 3) : min(width, door_x + 3), :] = np.array(
            [0.35, 0.42, 0.52], dtype=np.float32
        )
        severity = float(rng.uniform(0.0, 0.06))

    noise = rng.normal(0.0, 0.022, size=img.shape).astype(np.float32)
    img = np.clip(img + noise, 0.0, 1.0)
    return img, severity


def generate_tactical_training_batch(
    batch_size: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = np.zeros((batch_size, 10), dtype=np.float32)
    sensor_match = rng.choice([0.0, 0.5, 1.0], size=batch_size, p=[0.20, 0.30, 0.50]).astype(np.float32)
    ops_match = rng.choice([0.0, 1.0], size=batch_size, p=[0.10, 0.90]).astype(np.float32)
    battery = rng.uniform(0.10, 1.0, size=batch_size).astype(np.float32)
    norm_dist = rng.uniform(0.0, 1.0, size=batch_size).astype(np.float32)
    vis_conf = rng.uniform(0.4, 1.0, size=batch_size).astype(np.float32)
    severity = rng.uniform(0.1, 1.0, size=batch_size).astype(np.float32)
    hierarchy = rng.choice([0.35, 0.70, 1.0], size=batch_size).astype(np.float32)
    peer_comp = rng.uniform(0.0, 1.0, size=batch_size).astype(np.float32)
    busy = rng.choice([0.0, 1.0], size=batch_size, p=[0.85, 0.15]).astype(np.float32)
    deadline = rng.uniform(0.2, 1.0, size=batch_size).astype(np.float32)

    X[:, 0] = sensor_match
    X[:, 1] = ops_match
    X[:, 2] = battery
    X[:, 3] = norm_dist
    X[:, 4] = vis_conf
    X[:, 5] = severity
    X[:, 6] = hierarchy
    X[:, 7] = peer_comp
    X[:, 8] = busy
    X[:, 9] = deadline

    feasible = (sensor_match > 0.1) & (ops_match > 0.5) & (battery >= 0.15) & (busy < 0.5)
    raw_utility = (
        0.28 * sensor_match
        + 0.24 * battery
        + 0.24 * (1.0 - norm_dist)
        + 0.16 * hierarchy
        + 0.08 * severity
        - 0.10 * peer_comp
    )
    target_utility = np.where(feasible, np.clip(raw_utility, 0.05, 0.99), 0.01).astype(np.float32)
    target_pass = np.where(
        ~feasible | (peer_comp > raw_utility + 0.08),
        np.clip(0.85 + 0.12 * peer_comp, 0.75, 0.99),
        np.clip(0.20 * peer_comp, 0.01, 0.40),
    ).astype(np.float32)

    return X, target_utility, target_pass


class SwarmAIEngine:
    """Manages Torchvision MobileNetV3, OpenCV Spatial Contour Localizer, and SwarmTacticalPolicyNet."""

    def __init__(self, weights_path: Path = DEFAULT_WEIGHTS_PATH, prefer_cuda: bool = True) -> None:
        self.device = torch.device("cuda" if (prefer_cuda and torch.cuda.is_available()) else "cpu")
        self.weights_path = weights_path
        self.vision_model = TorchvisionHazardDetector().to(self.device)
        self.tactical_model = SwarmTacticalPolicyNet().to(self.device)
        self.training_metadata: dict[str, float | str] = {}
        self._load_or_train()

    def _load_or_train(self) -> None:
        if self.weights_path.exists():
            try:
                ckpt = torch.load(self.weights_path, map_location=self.device, weights_only=True)
                self.vision_model.load_state_dict(ckpt["vision_state_dict"])
                self.tactical_model.load_state_dict(ckpt["tactical_state_dict"])
                self.training_metadata = ckpt.get("metadata", {"backbone": "torchvision.mobilenet_v3_small"})
                self.vision_model.eval()
                self.tactical_model.eval()
                return
            except Exception:
                pass
        self.train_models()

    def train_models(self, seed: int = 42) -> dict[str, float | str]:
        t0 = time.perf_counter()
        rng = np.random.default_rng(seed)
        torch.manual_seed(seed)

        self.vision_model.train()
        opt_vis = torch.optim.AdamW(self.vision_model.parameters(), lr=2.5e-3, weight_decay=1e-4)

        n_samples = 600
        imgs = np.zeros((n_samples, 3, 128, 128), dtype=np.float32)
        labels = np.zeros(n_samples, dtype=np.int64)
        severities = np.zeros(n_samples, dtype=np.float32)

        for i in range(n_samples):
            c_idx = i % len(HAZARD_CLASSES)
            frame, sev = generate_synthetic_camera_sample(c_idx, rng, height=128, width=128)
            imgs[i] = np.transpose(frame, (2, 0, 1))
            labels[i] = c_idx
            severities[i] = sev

        perm = rng.permutation(n_samples)
        imgs_t = torch.from_numpy(imgs[perm]).to(self.device)
        labels_t = torch.from_numpy(labels[perm]).to(self.device)
        sev_t = torch.from_numpy(severities[perm]).to(self.device)

        batch_size = 100
        vis_acc = 0.0
        for _epoch in range(12):
            correct = 0
            for start in range(0, n_samples, batch_size):
                bx = imgs_t[start : start + batch_size]
                by = labels_t[start : start + batch_size]
                bs = sev_t[start : start + batch_size]
                opt_vis.zero_grad()
                logits, pred_sev = self.vision_model(bx)
                loss = F.cross_entropy(logits, by) + 0.4 * F.mse_loss(pred_sev, bs)
                loss.backward()
                opt_vis.step()
                correct += int((logits.argmax(dim=-1) == by).sum().item())
            vis_acc = correct / n_samples

        self.tactical_model.train()
        opt_tac = torch.optim.AdamW(self.tactical_model.parameters(), lr=4e-3, weight_decay=1e-4)
        X_np, u_np, p_np = generate_tactical_training_batch(2048, rng)
        X_t = torch.from_numpy(X_np).to(self.device)
        u_t = torch.from_numpy(u_np).to(self.device)
        p_t = torch.from_numpy(p_np).to(self.device)

        tac_mse = 0.0
        for _epoch in range(25):
            opt_tac.zero_grad()
            pred_u, pred_p = self.tactical_model(X_t)
            loss_t = F.mse_loss(pred_u, u_t) + F.mse_loss(pred_p, p_t)
            loss_t.backward()
            opt_tac.step()
            tac_mse = float(loss_t.item())

        self.vision_model.eval()
        self.tactical_model.eval()

        elapsed = round(time.perf_counter() - t0, 3)
        self.training_metadata = {
            "backbone": "torchvision.mobilenet_v3_small",
            "localizer": "OpenCV_Contour_Saliency",
            "policy_net": "SwarmTacticalPolicyNet_MLP",
            "device": str(self.device),
            "vision_accuracy": round(vis_acc, 4),
            "tactical_loss": round(tac_mse, 5),
            "train_seconds": elapsed,
        }
        self.weights_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "vision_state_dict": self.vision_model.state_dict(),
                "tactical_state_dict": self.tactical_model.state_dict(),
                "metadata": self.training_metadata,
            },
            self.weights_path,
        )
        return self.training_metadata

    @torch.no_grad()
    def classify_camera_frame(
        self,
        rgb_frame: np.ndarray,
        bbox_hint: tuple[int, int, int, int] | None = None,
    ) -> VisionPrediction:
        """Run Torchvision MobileNetV3 + OpenCV pixel contour localization on 128x128x3 RGB frame."""
        if rgb_frame.ndim != 3 or rgb_frame.shape[2] != 3:
            raise ValueError(f"Expected HxWx3 RGB frame, got {rgb_frame.shape}")
        if rgb_frame.shape[0] != 128 or rgb_frame.shape[1] != 128:
            rgb_frame = cv2.resize(rgb_frame.astype(np.float32), (128, 128), interpolation=cv2.INTER_LINEAR)

        chw = np.transpose(rgb_frame.astype(np.float32), (2, 0, 1))
        tensor = torch.from_numpy(chw).unsqueeze(0).to(self.device)
        logits, severity_t = self.vision_model(tensor)
        probs_t = F.softmax(logits, dim=-1).squeeze(0)
        probs = probs_t.cpu().numpy()

        c_idx = int(np.argmax(probs))
        pred_class = HAZARD_CLASSES[c_idx]
        conf = float(probs[c_idx])
        sev = float(severity_t.item())
        entropy = float(-np.sum(probs * np.log(np.clip(probs, 1e-8, 1.0))) / np.log(len(HAZARD_CLASSES)))

        # Compute bounding box directly from image pixels via OpenCV contour saliency
        detected_bbox = localize_hazard_bbox_opencv(rgb_frame, pred_class) or bbox_hint

        prob_map = {name: round(float(probs[i]), 4) for i, name in enumerate(HAZARD_CLASSES)}
        return VisionPrediction(
            predicted_class=pred_class,
            class_index=c_idx,
            confidence=round(conf, 4),
            severity=round(sev, 4),
            probabilities=prob_map,
            entropy=round(entropy, 4),
            bbox=detected_bbox,
        )

    @torch.no_grad()
    def evaluate_tactical_bid(
        self,
        *,
        sensor_match: bool,
        is_primary_specialty: bool = True,
        ops_match: bool = True,
        battery: float,
        distance_meters: float,
        vision_confidence: float,
        hazard_severity: float,
        hierarchy_tier: int,
        peer_best_bid: float,
        is_busy: bool,
        deadline_pressure: float = 0.5,
    ) -> TacticalDecisionOutput:
        tier_norm = {1: 1.0, 2: 0.72, 3: 0.42}.get(hierarchy_tier, 0.5)
        norm_dist = min(1.0, max(0.0, distance_meters / 34.0))
        spec_val = 1.0 if (sensor_match and is_primary_specialty) else (0.45 if sensor_match else 0.0)

        feat = np.array(
            [
                spec_val,
                1.0 if ops_match else 0.0,
                float(np.clip(battery, 0.0, 1.0)),
                norm_dist,
                float(np.clip(vision_confidence, 0.0, 1.0)),
                float(np.clip(hazard_severity, 0.0, 1.0)),
                tier_norm,
                float(np.clip(peer_best_bid, 0.0, 1.0)),
                1.0 if is_busy else 0.0,
                float(np.clip(deadline_pressure, 0.0, 1.0)),
            ],
            dtype=np.float32,
        )
        tensor = torch.from_numpy(feat).unsqueeze(0).to(self.device)
        bid_u_t, pass_p_t = self.tactical_model(tensor)
        nn_bid = float(bid_u_t.item())
        nn_pass = float(pass_p_t.item())

        if not sensor_match or not ops_match:
            return TacticalDecisionOutput(
                bid_utility=0.0,
                pass_probability=0.99,
                should_pass=True,
                hierarchy_tier=hierarchy_tier,
                specialty_mode="INELIGIBLE",
                reasoning="PASS: Missing required specialty sensor",
            )
        if battery < 0.15:
            return TacticalDecisionOutput(
                bid_utility=0.0,
                pass_probability=0.99,
                should_pass=True,
                hierarchy_tier=hierarchy_tier,
                specialty_mode="INELIGIBLE",
                reasoning=f"PASS: Battery ({battery*100:.0f}%) below safety reserve",
            )

        specialty_bonus = 0.18 if is_primary_specialty else -0.14
        kinematic_score = (
            0.30 * battery
            + 0.34 * (1.0 - norm_dist)
            + 0.22 * tier_norm
            + 0.14 * max(vision_confidence, hazard_severity)
        )
        final_bid = round(float(np.clip(0.45 * nn_bid + 0.55 * kinematic_score + specialty_bonus, 0.05, 0.99)), 4)
        spec_mode = "PRIMARY" if is_primary_specialty else "BACKUP"

        if peer_best_bid > final_bid + 0.005:
            return TacticalDecisionOutput(
                bid_utility=final_bid,
                pass_probability=round(max(nn_pass, 0.85), 4),
                should_pass=True,
                hierarchy_tier=hierarchy_tier,
                specialty_mode=spec_mode,
                reasoning=f"YIELD TURN ({spec_mode}): Peer bid ({peer_best_bid:.3f}) > own ({final_bid:.3f})",
            )

        return TacticalDecisionOutput(
            bid_utility=final_bid,
            pass_probability=round(min(nn_pass, 0.20), 4),
            should_pass=False,
            hierarchy_tier=hierarchy_tier,
            specialty_mode=spec_mode,
            reasoning=f"QUEUE ACTIVE ({spec_mode}, Tier {hierarchy_tier}): Bid={final_bid:.3f} (dist={distance_meters:.1f}m)",
        )


def render_annotated_hud_jpeg(
    rgb_float: np.ndarray | None,
    vision_class: str,
    vision_conf: float,
    bbox: tuple[int, int, int, int] | None,
) -> str:
    """Draw OpenCV bounding boxes, corner brackets, reticle, and Torchvision HUD onto 128x128 frame."""
    if rgb_float is None:
        return ""
    bgr = np.clip(rgb_float[:, :, ::-1] * 255.0, 0, 255).astype(np.uint8).copy()
    h, w, _ = bgr.shape

    # Optical crosshair reticle
    cx, cy = w // 2, h // 2
    cv2.line(bgr, (cx - 10, cy), (cx - 3, cy), (210, 220, 230), 1)
    cv2.line(bgr, (cx + 3, cy), (cx + 10, cy), (210, 220, 230), 1)
    cv2.line(bgr, (cx, cy - 10), (cx, cy - 3), (210, 220, 230), 1)
    cv2.line(bgr, (cx, cy + 3), (cx, cy + 10), (210, 220, 230), 1)

    if bbox is not None and vision_class != "corridor_wall":
        x1, y1, x2, y2 = bbox
        color_map = {
            "fire_hazard": (25, 75, 250),      # Red-Orange in BGR
            "gas_hazard": (25, 205, 125),      # Lime-Green in BGR
            "trapped_person": (205, 50, 235),  # Magenta in BGR
            "clear_room": (125, 195, 25),      # Emerald in BGR
        }
        box_color = color_map.get(vision_class, (240, 150, 30))
        cv2.rectangle(bgr, (x1, y1), (x2, y2), box_color, 2)
        label = f"{vision_class.replace('_', ' ').upper()} {int(vision_conf * 100)}%"
        cv2.rectangle(bgr, (x1, max(0, y1 - 14)), (min(w, x1 + 98), y1), box_color, -1)
        cv2.putText(
            bgr,
            label[:17],
            (x1 + 2, max(10, y1 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.30,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return ""
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"
