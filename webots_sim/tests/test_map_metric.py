import math

import numpy as np

from perception.detector import CLS_ID, average_precision, confusion_and_pr, iou_matrix

FIRE, GAS = CLS_ID["fire"], CLS_ID["gas_leak"]


def img(boxes, labels, scores=None):
    d = {"boxes": np.array(boxes, float).reshape(-1, 4), "labels": np.array(labels, int)}
    if scores is not None:
        d["scores"] = np.array(scores, float)
    return d


def test_iou_known_values():
    a = np.array([[0, 0, 10, 10]], float)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]], float)
    assert np.allclose(iou_matrix(a, b), [[1.0, 50 / 150, 0.0]], atol=1e-6)


def test_ap_hand_computed_tp_fp_tp():
    gt = [img([[0, 0, 10, 10], [50, 50, 60, 60]], [FIRE, FIRE])]
    pr = [img([[0, 0, 10, 10], [100, 100, 110, 110], [50, 50, 60, 60]], [FIRE] * 3, [0.9, 0.8, 0.7])]
    ap, rec, prec = average_precision(pr, gt, FIRE, 0.5)
    # PR points (P=1,R=.5) (P=.5,R=.5) (P=.667,R=1): all-point AP = .5*1 + .5*(2/3)
    assert math.isclose(ap, 0.5 + 0.5 * 2 / 3, rel_tol=1e-6)


def test_ap_duplicates_and_misses():
    gt = [img([[0, 0, 10, 10]], [FIRE]), img([[0, 0, 10, 10]], [FIRE])]
    perfect = [img([[0, 0, 10, 10]], [FIRE], [0.9]), img([[1, 1, 10, 10]], [FIRE], [0.8])]
    assert math.isclose(average_precision(perfect, gt, FIRE, 0.5)[0], 1.0)
    miss_one = [img([[0, 0, 10, 10]], [FIRE], [0.9]), img(np.zeros((0, 4)), [], [])]
    assert math.isclose(average_precision(miss_one, gt, FIRE, 0.5)[0], 0.5)
    dup = [img([[0, 0, 10, 10], [0, 0, 10, 10]], [FIRE, FIRE], [0.9, 0.95]), img([[0, 0, 10, 10]], [FIRE], [0.1])]
    assert average_precision(dup, gt, FIRE, 0.5)[0] < 1.0
    wrong_class = [img([[0, 0, 10, 10]], [GAS], [0.9]), img([[0, 0, 10, 10]], [GAS], [0.9])]
    assert average_precision(wrong_class, gt, FIRE, 0.5)[0] == 0.0
    loose = [img([[0, 0, 10, 14]], [FIRE], [0.9]), img([[0, 0, 10, 14]], [FIRE], [0.9])]
    assert average_precision(loose, gt, FIRE, 0.5)[0] == 1.0
    assert average_precision(loose, gt, FIRE, 0.75)[0] == 0.0


def test_confusion_matrix_counts_class_swaps_and_background():
    gt = [img([[0, 0, 10, 10], [50, 50, 60, 60]], [FIRE, GAS])]
    pr = [img([[0, 0, 10, 10], [50, 50, 60, 60], [200, 200, 210, 210], [300, 300, 310, 310]],
              [GAS, GAS, FIRE, FIRE], [0.9, 0.9, 0.9, 0.2])]
    cm, per = confusion_and_pr(pr, gt, thresh=0.5)
    assert cm[FIRE, GAS] == 1
    assert cm[GAS, GAS] == 1
    assert cm[0, FIRE] == 1
    assert cm.sum() == 3
    assert per["gas_leak"]["precision"] == 0.5 and per["gas_leak"]["recall"] == 1.0
    assert per["fire"]["recall"] == 0.0
