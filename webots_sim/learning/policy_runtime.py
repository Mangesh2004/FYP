"""Runs the exported task-allocation policy inside a Webots agent process (plain torch, no SB3)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "models" / "task_policy.pt"


class TaskPolicy:
    def __init__(self, path: Path | None = None) -> None:
        import torch

        self.torch = torch
        ckpt = torch.load(path or DEFAULT_POLICY, map_location="cpu", weights_only=True)
        self.layers = [(l["w"].float(), l["b"].float()) for l in ckpt["layers"]]
        self.obs_dim = int(ckpt["obs_dim"])

    def logits(self, obs: np.ndarray) -> np.ndarray:
        x = self.torch.as_tensor(np.asarray(obs, np.float32)).reshape(1, -1)
        with self.torch.no_grad():
            for i, (w, b) in enumerate(self.layers):
                x = x @ w.T + b
                if i < len(self.layers) - 1:
                    x = self.torch.tanh(x)
        return x.numpy()[0]

    def pick_slot(self, obs: np.ndarray, mask: np.ndarray) -> int:
        lg = self.logits(obs)
        lg = np.where(np.asarray(mask) > 0, lg, -np.inf)
        return int(np.argmax(lg))
