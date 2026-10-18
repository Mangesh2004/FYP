"""Train the shared task-allocation policy (independent PPO with parameter sharing, action masking).

Usage:
    python -m learning.train_ippo --steps 300000 --seeds 0 1 2
    python -m learning.train_ippo --evaluate-only

Outputs (D:/FYP/experiments/learning/):
    curves_seed{N}.json, learning_curves.png      evaluation success / mission time vs training steps
    abstract_eval.json, abstract_eval.md           final comparison vs greedy, static, random on held-out seeds
Deployed policy: webots_sim/models/task_policy.pt (best seed by held-out eval return), loaded by
learning/policy_runtime.py inside each Webots agent process.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from learning.abstract_env import MissionSim, SharedPolicyEnv, greedy_policy, run_episode, static_policy_for  # noqa: E402
from learning.features import N_SLOTS, OBS_DIM  # noqa: E402
from learning.policy_runtime import TaskPolicy  # noqa: E402

EXP = ROOT.parent / "experiments" / "learning"
MODEL = ROOT / "models" / "task_policy.pt"
EVAL_CONDS = ("normal", "loss20", "robot_killed", "door_blocked")
EVAL_SEED0 = 1_000_000


def export_policy(model, path: Path) -> None:
    pol = model.policy
    layers = []
    for m in pol.mlp_extractor.policy_net:
        if isinstance(m, torch.nn.Linear):
            layers.append({"w": m.weight.detach().cpu().clone(), "b": m.bias.detach().cpu().clone()})
    layers.append({"w": pol.action_net.weight.detach().cpu().clone(), "b": pol.action_net.bias.detach().cpu().clone()})
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"layers": layers, "activation": "tanh", "obs_dim": OBS_DIM, "n_slots": N_SLOTS}, path)


def evaluate(policy_fn, n: int = 200, seed0: int = EVAL_SEED0) -> dict:
    sim = MissionSim()
    out = {}
    for cond in EVAL_CONDS:
        res = [run_episode(policy_fn, seed0 + i, cond, sim) for i in range(n)]
        out[cond] = {
            "n": n,
            "success": float(np.mean([r["success"] for r in res])),
            "time_mean": float(np.mean([r["time"] for r in res])),
            "time_sd": float(np.std([r["time"] for r in res])),
            "hazard_recall": float(np.sum([r["hazards_confirmed"] for r in res]) / max(1, np.sum([r["hazards"] for r in res]))),
            "duplicates": float(np.mean([r["duplicates"] for r in res])),
            "false_conf": float(np.mean([r["false_conf"] for r in res])),
            "times": [r["time"] for r in res],
            "successes": [bool(r["success"]) for r in res],
        }
    return out


def sb3_policy_fn(model):
    def pick(agent, obs, mask):
        a, _ = model.predict(obs, action_masks=mask.astype(bool), deterministic=True)
        return int(a)
    return pick


def bc_warmstart(model, seed: int, episodes: int, epochs: int = 40, gamma: float = 0.99) -> dict:
    """Behaviour-clone the greedy rule into the actor and fit the critic to greedy's discounted returns,
    so PPO starts from a working policy instead of random task picks."""
    env = SharedPolicyEnv("random", seed=seed * 1000 + 999)
    O, M, A, G = [], [], [], []
    for _ in range(episodes):
        obs, _ = env.reset()
        rewards, done = [], False
        while not done:
            mask = env.action_masks()
            a = greedy_policy(obs, mask)
            O.append(obs), M.append(mask), A.append(a)
            obs, r, term, trunc, _ = env.step(a)
            rewards.append(r)
            done = term or trunc
        g, rets = 0.0, []
        for r in reversed(rewards):
            g = r + gamma * g
            rets.append(g)
        G.extend(reversed(rets))
    o, m = torch.as_tensor(np.array(O)), torch.as_tensor(np.array(M))
    a, g = torch.as_tensor(np.array(A)), torch.as_tensor(np.array(G), dtype=torch.float32)
    pol, n = model.policy, len(A)
    opt = torch.optim.Adam(pol.parameters(), lr=1e-3)
    rng = np.random.default_rng(seed)
    for _ in range(epochs):
        for idx in np.array_split(rng.permutation(n), max(1, n // 256)):
            dist = pol.get_distribution(o[idx], action_masks=m[idx].numpy())
            v = pol.predict_values(o[idx]).squeeze(-1)
            loss = -dist.log_prob(a[idx]).mean() + 0.5 * torch.nn.functional.mse_loss(v, g[idx])
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(pol.parameters(), 1.0)
            opt.step()
    with torch.no_grad():
        dist = pol.get_distribution(o, action_masks=m.numpy())
        agree = float((dist.distribution.probs.argmax(-1) == a).float().mean())
    return {"bc_samples": n, "bc_agreement": agree}


def train_one(seed: int, steps: int, eval_every: int, bc_episodes: int = 0, tag: str = "") -> dict:
    from sb3_contrib import MaskablePPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    venv = DummyVecEnv([lambda i=i: SharedPolicyEnv("random", seed=seed * 1000 + i) for i in range(8)])
    model = MaskablePPO("MlpPolicy", venv, seed=seed, n_steps=256, batch_size=256, n_epochs=8,
                        learning_rate=1e-4 if bc_episodes else 3e-4,
                        gamma=0.99, gae_lambda=0.95, ent_coef=0.01, clip_range=0.2,
                        policy_kwargs={"net_arch": {"pi": [128, 128], "vf": [128, 128]}}, device="cpu", verbose=0)
    curve = []
    t0 = time.time()
    best = -1e9
    path = EXP / f"policy_{tag}seed{seed}.pt"

    def checkpoint(extra=None):
        nonlocal best
        ev = evaluate(sb3_policy_fn(model), n=50, seed0=EVAL_SEED0 + 500_000)  # validation seeds, not the test seeds
        row = {"steps": model.num_timesteps, "wall_s": round(time.time() - t0, 1),
               **{f"{c}_success": ev[c]["success"] for c in EVAL_CONDS},
               **{f"{c}_time": ev[c]["time_mean"] for c in EVAL_CONDS}, **(extra or {})}
        score = np.mean([ev[c]["success"] for c in EVAL_CONDS]) - np.mean([ev[c]["time_mean"] for c in EVAL_CONDS]) / 600
        row["val_score"] = float(score)
        curve.append(row)
        print(json.dumps(row), flush=True)
        if score > best:
            best = score
            export_policy(model, path)

    if bc_episodes:
        checkpoint(bc_warmstart(model, seed, bc_episodes))
    while model.num_timesteps < steps:
        model.learn(eval_every, reset_num_timesteps=False)
        checkpoint()
        (EXP / f"curves_{tag}seed{seed}.json").write_text(json.dumps(curve, indent=2), encoding="utf-8")
    return {"seed": seed, "best_val_score": best, "path": str(path)}


def plot_curves(seeds: list[int]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    families = (("", "PPO from scratch", "-"), ("bc_", "PPO + greedy warm start", "-."))
    for tag, label, ls in families:
        for seed in seeds:
            p = EXP / f"curves_{tag}seed{seed}.json"
            if not p.exists():
                continue
            c = json.loads(p.read_text(encoding="utf-8"))
            x = [r["steps"] for r in c]
            axes[0].plot(x, [np.mean([r[f"{k}_success"] for k in EVAL_CONDS]) for r in c], ls=ls, label=f"{label}, seed {seed}")
            axes[1].plot(x, [np.mean([r[f"{k}_time"] for k in EVAL_CONDS]) for r in c], ls=ls, label=f"{label}, seed {seed}")
    g = evaluate(lambda n, o, m: greedy_policy(o, m), n=50, seed0=EVAL_SEED0 + 500_000)
    axes[0].axhline(np.mean([g[k]["success"] for k in EVAL_CONDS]), color="#f2a541", ls="--", label="greedy")
    axes[1].axhline(np.mean([g[k]["time_mean"] for k in EVAL_CONDS]), color="#f2a541", ls="--", label="greedy")
    axes[0].set_title("validation success rate (mean over 4 conditions)")
    axes[1].set_title("validation mission time [s] (failures = 300)")
    for ax in axes:
        ax.set_xlabel("training decisions")
        ax.grid(alpha=0.3)
        ax.legend()
    fig.tight_layout()
    fig.savefig(EXP / "learning_curves.png", dpi=130)
    plt.close(fig)


def final_eval() -> None:
    from scipy import stats

    pol = TaskPolicy(MODEL)
    methods = {
        "learned": lambda n, o, m: pol.pick_slot(o, m),
        "greedy": lambda n, o, m: greedy_policy(o, m),
        "static": lambda n, o, m: static_policy_for(n)(o, m),
        "random": lambda n, o, m: int(np.random.default_rng().choice(np.flatnonzero(m))),
    }
    res = {k: evaluate(f, n=500) for k, f in methods.items()}
    lines = ["| condition | method | success | mission time mean (sd) | hazard recall | duplicates |", "|---|---|---|---|---|---|"]
    tests = {}
    for c in EVAL_CONDS:
        for k in methods:
            r = res[k][c]
            lines.append(f"| {c} | {k} | {r['success']:.3f} | {r['time_mean']:.1f} ({r['time_sd']:.1f}) | {r['hazard_recall']:.3f} | {r['duplicates']:.3f} |")
        a, b = np.array(res["learned"][c]["times"]), np.array(res["greedy"][c]["times"])
        w = stats.wilcoxon(a, b, zero_method="zsplit")
        tests[c] = {"learned_minus_greedy_mean_s": float((a - b).mean()), "wilcoxon_p": float(w.pvalue)}
    lines += ["", "Paired on the same 500 held-out seeds per condition (Wilcoxon signed-rank on mission time):", ""]
    for c, t in tests.items():
        lines.append(f"- {c}: learned - greedy = {t['learned_minus_greedy_mean_s']:+.1f} s, p = {t['wilcoxon_p']:.3g}")
    for k in res:
        for c in res[k]:
            res[k][c].pop("times")
            res[k][c].pop("successes")
    (EXP / "abstract_eval.json").write_text(json.dumps({"results": res, "tests": tests}, indent=2), encoding="utf-8")
    (EXP / "abstract_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=300_000)
    ap.add_argument("--eval-every", type=int, default=20_000)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--evaluate-only", action="store_true")
    ap.add_argument("--select", nargs="*", type=int,
                    help="pick the best (by validation score) of these trained seeds over both run families and deploy it")
    ap.add_argument("--bc-episodes", type=int, default=0, help="greedy behaviour-cloning warm start episodes (0 = off)")
    a = ap.parse_args()
    EXP.mkdir(parents=True, exist_ok=True)
    if a.select:
        cands = [(tag, s) for tag in ("", "bc_") for s in a.select if (EXP / f"curves_{tag}seed{s}.json").exists()]
        val = {c: max(r["val_score"] for r in json.loads((EXP / f"curves_{c[0]}seed{c[1]}.json").read_text())) for c in cands}
        tag, seed = max(val, key=val.get)
        MODEL.write_bytes((EXP / f"policy_{tag}seed{seed}.pt").read_bytes())
        (EXP / "deployed.json").write_text(json.dumps({"run": f"{tag}seed{seed}", "val_score": val[(tag, seed)],
                                                       "all_val_scores": {f"{t}seed{s}": v for (t, s), v in val.items()}},
                                                      indent=2), encoding="utf-8")
        print(f"deployed {tag}seed{seed} (val {val[(tag, seed)]:.4f}) -> {MODEL}")
        plot_curves(a.select)
        final_eval()
        return
    if a.evaluate_only:
        final_eval()
        return
    tag = "bc_" if a.bc_episodes else ""
    for s in a.seeds:
        print(json.dumps(train_one(s, a.steps, a.eval_every, a.bc_episodes, tag)), flush=True)


if __name__ == "__main__":
    main()
