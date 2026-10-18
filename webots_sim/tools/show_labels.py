"""Draw dataset labels onto a montage image for visual checking.

python tools/show_labels.py [--n 12] [--out runs/label_montage.jpg] [--only-positive]
"""
import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
COL = {"fire": (0, 80, 255), "gas_leak": (40, 200, 40), "person": (255, 120, 0), "debris": (40, 110, 170)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--out", default=str(ROOT / "runs" / "label_montage.jpg"))
    ap.add_argument("--only-positive", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    recs = [json.loads(l) for l in (ROOT / "data" / "detector" / "labels.jsonl").read_text().splitlines()]
    if a.only_positive:
        recs = [r for r in recs if r["boxes"]]
    random.Random(a.seed).shuffle(recs)
    tiles = []
    for r in recs[: a.n]:
        img = cv2.imread(str(ROOT / "data" / "detector" / r["file"]))
        for x0, y0, x1, y1, c in r["boxes"]:
            cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), COL[c], 2)
            cv2.putText(img, c, (int(x0), max(10, int(y0) - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, COL[c], 1)
        cv2.putText(img, f"{r['region']} {Path(r['file']).stem}", (4, 234), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
        tiles.append(img)
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]
    cv2.imwrite(a.out, np.vstack(rows))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
