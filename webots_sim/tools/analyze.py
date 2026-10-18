"""Summarize a batch: per condition x method table with 95% CIs, paired tests, and plots.

Usage:
    python tools/analyze.py D:/FYP/experiments/pilot [--ref cbba]

Writes summary.csv, summary.md, stats.json and *.png next to results.csv.
Success = all 4 rooms inspected and every alive agent reported mission complete before max time.
Completion time of failed episodes is censored at max time (rank-based tests handle this).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402

COND_ORDER = ["normal", "loss20", "commander_cut", "robot_killed", "door_blocked"]
METHOD_ORDER = ["static", "greedy", "cbba", "central", "learned"]
COLORS = {"static": "#9e9e9e", "greedy": "#f2a541", "cbba": "#2a6fdb", "central": "#c44536", "learned": "#3a9d5d"}


def fnum(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r.get("crashed"):
            r["_crashed"] = True
        r["seed"] = int(r["seed"])
        for k in list(r):
            if k not in ("condition", "method", "crashed", "timed_out", "_crashed"):
                v = fnum(r[k])
                r[k] = v if v is not None else r[k] if r[k] not in ("", "None") else None
        r["timed_out"] = str(r["timed_out"]) == "True"
        r["success"] = (r["rooms_inspected"] == 4) and not r["timed_out"]
        r["hazard_recall"] = r["hazards_confirmed"] / r["hazards_total"] if r["hazards_total"] else None
        r["victim_recall"] = r["victims_found"] / r["victims_total"] if r["victims_total"] else None
        r["time_censored"] = r["mission_complete_time"] if r["success"] and r["mission_complete_time"] else r["sim_time"]
        rows.append(r)
    return rows


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def boot_ci(x: list[float], n_boot: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    x = np.array([v for v in x if v is not None and not math.isnan(v)], float)
    if x.size == 0:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    means = rng.choice(x, (n_boot, x.size)).mean(axis=1)
    return float(x.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    conds = [c for c in COND_ORDER if any(r["condition"] == c for r in rows)]
    methods = [m for m in METHOD_ORDER if any(r["method"] == m for r in rows)]
    for c in conds:
        for m in methods:
            g = [r for r in rows if r["condition"] == c and r["method"] == m]
            if not g:
                continue
            k = sum(r["success"] for r in g)
            lo, hi = wilson(k, len(g))
            hz = boot_ci([r["hazard_recall"] for r in g])
            fc = boot_ci([r["false_confirmations"] for r in g])
            rooms = boot_ci([r["rooms_inspected"] for r in g])
            succ_t = [r["mission_complete_time"] for r in g if r["success"] and r["mission_complete_time"]]
            rec = [r["recovery_time_after_kill"] for r in g if r["recovery_time_after_kill"] is not None]
            out.append({
                "condition": c, "method": m, "n": len(g), "crashed": sum(1 for r in g if r.get("_crashed")),
                "success_rate": k / len(g), "success_ci_lo": lo, "success_ci_hi": hi,
                "rooms_mean": rooms[0], "hazard_recall": hz[0], "hazard_recall_ci_lo": hz[1], "hazard_recall_ci_hi": hz[2],
                "victim_recall": boot_ci([r["victim_recall"] for r in g])[0],
                "false_conf_mean": fc[0],
                "time_median_success": float(np.median(succ_t)) if succ_t else None,
                "time_iqr_success": [float(np.percentile(succ_t, 25)), float(np.percentile(succ_t, 75))] if succ_t else None,
                "time_censored_median": float(np.median([r["time_censored"] for r in g])),
                "recovery_median": float(np.median(rec)) if rec else None,
                "loc_error_mean": boot_ci([r["localization_error_m"] for r in g])[0],
                "stuck_mean": boot_ci([r["stuck_recoveries"] for r in g])[0],
                "msgs_sent_mean": boot_ci([r["messages_sent"] for r in g])[0],
            })
    return out


def paired_tests(rows: list[dict], ref: str) -> dict:
    res = {}
    for c in COND_ORDER:
        for m in METHOD_ORDER:
            if m == ref:
                continue
            a = {r["seed"]: r for r in rows if r["condition"] == c and r["method"] == ref}
            b = {r["seed"]: r for r in rows if r["condition"] == c and r["method"] == m}
            seeds = sorted(set(a) & set(b))
            if len(seeds) < 5:
                continue
            ta = np.array([a[s]["time_censored"] for s in seeds])
            tb = np.array([b[s]["time_censored"] for s in seeds])
            sa = np.array([a[s]["success"] for s in seeds])
            sb = np.array([b[s]["success"] for s in seeds])
            try:
                w = stats.wilcoxon(ta, tb, zero_method="zsplit")
                wp = float(w.pvalue)
            except ValueError:
                wp = float("nan")
            # McNemar exact test on paired success
            b01 = int(np.sum(sa & ~sb))
            b10 = int(np.sum(~sa & sb))
            mc = float(stats.binomtest(b01, b01 + b10, 0.5).pvalue) if b01 + b10 else 1.0
            res[f"{c}:{ref}_vs_{m}"] = {
                "n_pairs": len(seeds), "median_time_diff_s": float(np.median(ta - tb)),
                "wilcoxon_p_time": wp, "ref_only_success": b01, "other_only_success": b10, "mcnemar_p_success": mc,
            }
    return res


def grouped_bar(summary, key, lo, hi, ylabel, fname, out, ylim=None):
    conds = [c for c in COND_ORDER if any(s["condition"] == c for s in summary)]
    methods = [m for m in METHOD_ORDER if any(s["method"] == m for s in summary)]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    w = 0.8 / len(methods)
    for i, m in enumerate(methods):
        xs, ys, el, eh = [], [], [], []
        for j, c in enumerate(conds):
            s = next((s for s in summary if s["condition"] == c and s["method"] == m), None)
            if not s or s[key] is None or (isinstance(s[key], float) and math.isnan(s[key])):
                continue
            xs.append(j + (i - (len(methods) - 1) / 2) * w)
            ys.append(s[key])
            if lo:
                el.append(max(0.0, s[key] - s[lo]))
                eh.append(max(0.0, s[hi] - s[key]))
        ax.bar(xs, ys, w * 0.95, color=COLORS.get(m), label=m,
               yerr=[el, eh] if lo else None, capsize=3, error_kw={"lw": 1})
    ax.set_xticks(range(len(conds)))
    ax.set_xticklabels(conds)
    ax.set_ylabel(ylabel)
    if ylim:
        ax.set_ylim(*ylim)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(ncol=len(methods), fontsize=9, loc="upper center", bbox_to_anchor=(0.5, 1.13), frameon=False)
    fig.tight_layout()
    fig.savefig(out / fname, dpi=130)
    plt.close(fig)


def time_boxes(rows, out):
    conds = [c for c in COND_ORDER if any(r["condition"] == c for r in rows)]
    methods = [m for m in METHOD_ORDER if any(r["method"] == m for r in rows)]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    w = 0.8 / len(methods)
    for i, m in enumerate(methods):
        data, pos = [], []
        for j, c in enumerate(conds):
            v = [r["time_censored"] for r in rows if r["condition"] == c and r["method"] == m]
            if v:
                data.append(v)
                pos.append(j + (i - (len(methods) - 1) / 2) * w)
        bp = ax.boxplot(data, positions=pos, widths=w * 0.85, patch_artist=True, showfliers=True)
        for b in bp["boxes"]:
            b.set_facecolor(COLORS.get(m))
            b.set_alpha(0.8)
        ax.plot([], [], color=COLORS.get(m), lw=8, label=m)
    ax.set_xticks(range(len(conds)))
    ax.set_xticklabels(conds)
    ax.set_ylabel("mission time [s sim] (failures at max time)")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(ncol=len(methods), fontsize=9, loc="upper center", bbox_to_anchor=(0.5, 1.13), frameon=False)
    fig.tight_layout()
    fig.savefig(out / "mission_time.png", dpi=130)
    plt.close(fig)


def write_md(summary, tests, out, ref):
    lines = ["| condition | method | n | success (95% CI) | rooms | hazard recall (95% CI) | false conf. | median time (success) | recovery after kill |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        t = f"{s['time_median_success']:.0f} s" if s["time_median_success"] else "-"
        rc = f"{s['recovery_median']:.0f} s" if s["recovery_median"] else "-"
        hr = "-" if math.isnan(s["hazard_recall"]) else f"{s['hazard_recall']:.2f} ({s['hazard_recall_ci_lo']:.2f}-{s['hazard_recall_ci_hi']:.2f})"
        lines.append(f"| {s['condition']} | {s['method']} | {s['n']} | {s['success_rate']:.2f} ({s['success_ci_lo']:.2f}-{s['success_ci_hi']:.2f}) | "
                     f"{s['rooms_mean']:.2f} | {hr} | {s['false_conf_mean']:.2f} | {t} | {rc} |")
    lines += ["", f"Paired tests ({ref} vs other, same seeds). Time: Wilcoxon signed-rank on censored time; success: exact McNemar.", "",
              "| comparison | pairs | median time diff (ref - other) | p (time) | ref-only successes | other-only successes | p (success) |",
              "|---|---|---|---|---|---|---|"]
    for k, v in tests.items():
        lines.append(f"| {k} | {v['n_pairs']} | {v['median_time_diff_s']:.1f} s | {v['wilcoxon_p_time']:.3g} | {v['ref_only_success']} | "
                     f"{v['other_only_success']} | {v['mcnemar_p_success']:.3g} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--ref", default="cbba")
    a = ap.parse_args()
    out = Path(a.dir)
    rows = load(out / "results.csv")
    summary = summarize(rows)
    tests = paired_tests(rows, a.ref)
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    (out / "stats.json").write_text(json.dumps({"summary": summary, "paired_tests": tests}, indent=2), encoding="utf-8")
    write_md(summary, tests, out, a.ref)
    grouped_bar(summary, "success_rate", "success_ci_lo", "success_ci_hi", "mission success rate", "success_rate.png", out, (0, 1.05))
    grouped_bar(summary, "rooms_mean", None, None, "rooms inspected (of 4)", "rooms_inspected.png", out, (0, 4.2))
    grouped_bar(summary, "hazard_recall", "hazard_recall_ci_lo", "hazard_recall_ci_hi", "hazards confirmed / present",
                "hazard_recall.png", out, (0, 1.05))
    grouped_bar(summary, "false_conf_mean", None, None, "false confirmations per episode", "false_confirmations.png", out)
    time_boxes(rows, out)
    print((out / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
