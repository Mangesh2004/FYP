"""Train and evaluate the on-board hazard detector on frames captured in Webots.

Splits (by layout seed, never by frame):
    train            layouts   0-149, all regions except R3
    val              layouts 150-169, all regions except R3
    test_layout      layouts 1000+,   all regions except R3   (unseen object arrangements and lighting)
    test_unseen_room layouts 1000+,   region R3 only          (room never seen in training)

python -m perception.detector train [--epochs 14]
python -m perception.detector eval
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm.perception import CLASSES, DEFAULT_WEIGHTS, build_model  # noqa: E402

DATA = ROOT / "data" / "detector"
EXP = ROOT.parent / "experiments" / "detector"
CLS_ID = {c: i for i, c in enumerate(CLASSES)}
DEPLOY_THRESH = 0.55


def load_split(name: str) -> list[dict]:
    recs = [json.loads(l) for l in (DATA / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    out = []
    for r in recs:
        lid, reg = r["layout"], r["region"]
        if name == "train" and lid < 150 and reg != "R3":
            out.append(r)
        elif name == "val" and 150 <= lid < 170 and reg != "R3":
            out.append(r)
        elif name == "test_layout" and lid >= 1000 and reg != "R3":
            out.append(r)
        elif name == "test_unseen_room" and lid >= 1000 and reg == "R3":
            out.append(r)
    return out


class FrameDataset(torch.utils.data.Dataset):
    def __init__(self, recs: list[dict], augment: bool) -> None:
        self.recs = recs
        self.augment = augment

    def __len__(self) -> int:
        return len(self.recs)

    def __getitem__(self, i: int):
        r = self.recs[i]
        img = cv2.cvtColor(cv2.imread(str(DATA / r["file"])), cv2.COLOR_BGR2RGB)
        boxes = np.array([b[:4] for b in r["boxes"]], dtype=np.float32).reshape(-1, 4)
        labels = np.array([CLS_ID[b[4]] for b in r["boxes"]], dtype=np.int64)
        if self.augment:
            if random.random() < 0.5:
                img = img[:, ::-1].copy()
                w = img.shape[1]
                boxes[:, [0, 2]] = w - boxes[:, [2, 0]]
            if random.random() < 0.8:
                alpha = random.uniform(0.7, 1.3)
                beta = random.uniform(-25, 25)
                img = np.clip(img.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
        t = torch.from_numpy(img).permute(2, 0, 1).float() / 255.0
        target = {"boxes": torch.from_numpy(boxes), "labels": torch.from_numpy(labels)}
        return t, target


def collate(batch):
    return tuple(zip(*batch))


# ------------------------------------------------------------------ metrics
def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x0 = np.maximum(a[:, None, 0], b[None, :, 0])
    y0 = np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = np.minimum(a[:, None, 2], b[None, :, 2])
    y1 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    ab = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (aa[:, None] + ab[None, :] - inter + 1e-9)


def average_precision(preds: list[dict], gts: list[dict], cls: int, iou_t: float):
    """All-point interpolated AP for one class. preds/gts: per-image dicts of boxes/labels/scores."""
    scores, tps = [], []
    n_gt = 0
    for p, g in zip(preds, gts):
        gb = g["boxes"][g["labels"] == cls]
        n_gt += len(gb)
        m = p["labels"] == cls
        pb, ps = p["boxes"][m], p["scores"][m]
        order = np.argsort(-ps)
        pb, ps = pb[order], ps[order]
        used = np.zeros(len(gb), bool)
        ious = iou_matrix(pb, gb)
        for k in range(len(pb)):
            j = int(np.argmax(ious[k])) if len(gb) else -1
            ok = j >= 0 and ious[k, j] >= iou_t and not used[j]
            if ok:
                used[j] = True
            scores.append(ps[k])
            tps.append(1 if ok else 0)
    if n_gt == 0:
        return float("nan"), np.array([]), np.array([])
    order = np.argsort(-np.array(scores))
    tp = np.array(tps)[order] if tps else np.array([])
    ctp = np.cumsum(tp)
    cfp = np.cumsum(1 - tp)
    rec = ctp / n_gt
    prec = ctp / np.maximum(ctp + cfp, 1e-9)
    mrec = np.concatenate([[0], rec, [1]])
    mpre = np.concatenate([[1], prec, [0]])
    for i in range(len(mpre) - 2, -1, -1):
        mpre[i] = max(mpre[i], mpre[i + 1])
    idx = np.where(mrec[1:] != mrec[:-1])[0]
    ap = float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))
    return ap, rec, prec


def confusion_and_pr(preds, gts, thresh: float, iou_t: float = 0.5):
    k = len(CLASSES)  # index 0 = background
    cm = np.zeros((k, k), dtype=np.int64)  # rows = ground truth, cols = prediction
    for p, g in zip(preds, gts):
        m = p["scores"] >= thresh
        pb, pl = p["boxes"][m], p["labels"][m]
        gb, gl = g["boxes"], g["labels"]
        ious = iou_matrix(gb, pb)
        used_p = set()
        pairs = sorted(((ious[i, j], i, j) for i in range(len(gb)) for j in range(len(pb)) if ious[i, j] >= iou_t), reverse=True)
        used_g = set()
        for _, i, j in pairs:
            if i in used_g or j in used_p:
                continue
            used_g.add(i)
            used_p.add(j)
            cm[gl[i], pl[j]] += 1
        for i in range(len(gb)):
            if i not in used_g:
                cm[gl[i], 0] += 1
        for j in range(len(pb)):
            if j not in used_p:
                cm[0, pl[j]] += 1
    per = {}
    for c in range(1, k):
        tp = cm[c, c]
        fp = cm[:, c].sum() - tp
        fn = cm[c, :].sum() - tp
        per[CLASSES[c]] = {"precision": float(tp / max(tp + fp, 1)), "recall": float(tp / max(tp + fn, 1)),
                           "tp": int(tp), "fp": int(fp), "fn": int(fn)}
    return cm, per


@torch.no_grad()
def predict(model, recs: list[dict], device) -> tuple[list[dict], list[dict]]:
    model.eval()
    preds, gts = [], []
    ds = FrameDataset(recs, augment=False)
    loader = torch.utils.data.DataLoader(ds, batch_size=16, shuffle=False, collate_fn=collate, num_workers=0)
    for imgs, targets in loader:
        outs = model([im.to(device) for im in imgs])
        for o, t in zip(outs, targets):
            preds.append({"boxes": o["boxes"].cpu().numpy(), "labels": o["labels"].cpu().numpy(), "scores": o["scores"].cpu().numpy()})
            gts.append({"boxes": t["boxes"].numpy(), "labels": t["labels"].numpy()})
    return preds, gts


def summarize(preds, gts) -> dict:
    res = {"per_class_ap50": {}, "per_class_ap50_95": {}}
    ap50s, ap5095s = [], []
    for c in range(1, len(CLASSES)):
        ap50, _, _ = average_precision(preds, gts, c, 0.5)
        aps = [average_precision(preds, gts, c, t)[0] for t in np.arange(0.5, 0.96, 0.05)]
        ap5095 = float(np.nanmean(aps)) if not all(math.isnan(a) for a in aps) else float("nan")
        res["per_class_ap50"][CLASSES[c]] = ap50
        res["per_class_ap50_95"][CLASSES[c]] = ap5095
        if not math.isnan(ap50):
            ap50s.append(ap50)
            ap5095s.append(ap5095)
    res["mAP50"] = float(np.mean(ap50s)) if ap50s else float("nan")
    res["mAP50_95"] = float(np.mean(ap5095s)) if ap5095s else float("nan")
    cm, per = confusion_and_pr(preds, gts, DEPLOY_THRESH)
    res["at_deploy_threshold"] = {"score_threshold": DEPLOY_THRESH, "per_class": per, "confusion_matrix": cm.tolist(),
                                  "labels": ["background"] + CLASSES[1:]}
    res["images"] = len(gts)
    res["gt_boxes"] = {CLASSES[c]: int(sum((g["labels"] == c).sum() for g in gts)) for c in range(1, len(CLASSES))}
    return res


# ------------------------------------------------------------------ train
def train(epochs: int, batch: int, lr: float) -> None:
    torch.manual_seed(0)
    random.seed(0)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tr, va = load_split("train"), load_split("val")
    print(f"train {len(tr)} frames, val {len(va)} frames, device {device}", flush=True)
    model = build_model(pretrained=True).to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.SGD(params, lr=lr, momentum=0.9, weight_decay=1e-4)
    loader = torch.utils.data.DataLoader(FrameDataset(tr, True), batch_size=batch, shuffle=True,
                                         collate_fn=collate, num_workers=0, drop_last=True)
    total = epochs * len(loader)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 300) * 0.5 * (1 + math.cos(math.pi * s / total)))
    log = []
    best = -1.0
    EXP.mkdir(parents=True, exist_ok=True)
    DEFAULT_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for ep in range(epochs):
        model.train()
        losses = []
        for imgs, targets in loader:
            imgs = [i.to(device) for i in imgs]
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]
            loss_dict = model(imgs, targets)
            loss = sum(loss_dict.values())
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 10.0)
            opt.step()
            sched.step()
            losses.append(float(loss))
        preds, gts = predict(model, va, device)
        val = summarize(preds, gts)
        entry = {"epoch": ep + 1, "train_loss": float(np.mean(losses)), "val_mAP50": val["mAP50"],
                 "val_mAP50_95": val["mAP50_95"], "elapsed_s": round(time.time() - t0, 1)}
        log.append(entry)
        print(json.dumps(entry), flush=True)
        if val["mAP50"] > best:
            best = val["mAP50"]
            torch.save(model.state_dict(), DEFAULT_WEIGHTS)
    (EXP / "train_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    plot_curves(log)
    print("best val mAP50", best, "saved", DEFAULT_WEIGHTS, flush=True)


def plot_curves(log: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ep = [e["epoch"] for e in log]
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.4))
    ax[0].plot(ep, [e["train_loss"] for e in log], "o-", color="#c0392b")
    ax[0].set_title("training loss")
    ax[0].set_xlabel("epoch")
    ax[1].plot(ep, [e["val_mAP50"] for e in log], "o-", label="mAP@0.5")
    ax[1].plot(ep, [e["val_mAP50_95"] for e in log], "s-", label="mAP@0.5:0.95")
    ax[1].set_title("validation (held-out layouts)")
    ax[1].set_xlabel("epoch")
    ax[1].legend()
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(EXP / "training_curves.png", dpi=130)


# ------------------------------------------------------------------ eval
def evaluate() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model().to(device)
    model.load_state_dict(torch.load(DEFAULT_WEIGHTS, map_location=device, weights_only=True))
    EXP.mkdir(parents=True, exist_ok=True)
    report = {}
    for split in ("test_layout", "test_unseen_room"):
        recs = load_split(split)
        preds, gts = predict(model, recs, device)
        res = summarize(preds, gts)
        report[split] = res
        cm = np.array(res["at_deploy_threshold"]["confusion_matrix"])
        labels = res["at_deploy_threshold"]["labels"]
        fig, ax = plt.subplots(figsize=(4.8, 4.2))
        ax.imshow(cm, cmap="Blues")
        ax.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
        ax.set_yticks(range(len(labels)), labels)
        ax.set_xlabel("predicted")
        ax.set_ylabel("ground truth")
        for i in range(len(labels)):
            for j in range(len(labels)):
                ax.text(j, i, str(cm[i, j]), ha="center", va="center", color="black" if cm[i, j] < cm.max() / 2 else "white")
        ax.set_title(f"{split} (score >= {DEPLOY_THRESH})")
        fig.tight_layout()
        fig.savefig(EXP / f"confusion_{split}.png", dpi=130)
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(4.8, 3.8))
        for c in range(1, len(CLASSES)):
            ap, rec, prec = average_precision(preds, gts, c, 0.5)
            if rec.size:
                ax.plot(rec, prec, label=f"{CLASSES[c]} AP={ap:.2f}")
        ax.set_xlabel("recall")
        ax.set_ylabel("precision")
        ax.set_title(f"PR @IoU0.5 - {split}")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(EXP / f"pr_{split}.png", dpi=130)
        plt.close(fig)
        montage(recs, preds, split)
        print(split, json.dumps({k: res[k] for k in ("mAP50", "mAP50_95", "images", "gt_boxes")}), flush=True)
    (EXP / "eval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def montage(recs, preds, split: str, n: int = 8) -> None:
    rng = random.Random(1)
    idx = [i for i, r in enumerate(recs) if r["boxes"]]
    rng.shuffle(idx)
    tiles = []
    for i in idx[:n]:
        img = cv2.imread(str(DATA / recs[i]["file"]))
        for x0, y0, x1, y1, c in recs[i]["boxes"]:
            cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), (255, 255, 255), 1)
        p = preds[i]
        for b, l, s in zip(p["boxes"], p["labels"], p["scores"]):
            if s < DEPLOY_THRESH:
                continue
            col = {1: (0, 80, 255), 2: (40, 200, 40), 3: (255, 120, 0), 4: (40, 110, 170)}[int(l)]
            cv2.rectangle(img, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), col, 2)
            cv2.putText(img, f"{CLASSES[int(l)]} {s:.2f}", (int(b[0]), max(10, int(b[1]) - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1)
        tiles.append(img)
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]
    cv2.imwrite(str(EXP / f"detections_{split}.jpg"), np.vstack(rows))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train", "eval"])
    ap.add_argument("--epochs", type=int, default=14)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=0.01)
    a = ap.parse_args()
    if a.cmd == "train":
        train(a.epochs, a.batch, a.lr)
    else:
        evaluate()


if __name__ == "__main__":
    main()
