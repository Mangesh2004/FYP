import numpy as np
import torch

from learning.abstract_env import MissionSim, TaskAllocAEC, greedy_policy, run_episode, static_policy_for
from learning.features import N_SLOTS, OBS_DIM, build_obs
from learning.policy_runtime import TaskPolicy
from swarm.blueprint import Blueprint
from swarm.tasks import TaskBook

GREEDY = lambda n, o, m: greedy_policy(o, m)  # noqa: E731
STATIC = lambda n, o, m: static_policy_for(n)(o, m)  # noqa: E731


def test_episode_is_deterministic_per_seed_and_varies_across_seeds():
    a = run_episode(GREEDY, 7, "loss20")
    b = run_episode(GREEDY, 7, "loss20")
    assert a == b
    times = {run_episode(GREEDY, s, "normal")["time"] for s in range(20)}
    assert len(times) > 15


def test_static_split_cannot_recover_from_robot_loss_but_greedy_can():
    sim = MissionSim()
    st = [run_episode(STATIC, s, "robot_killed", sim)["success"] for s in range(60)]
    gr = [run_episode(GREEDY, s, "robot_killed", sim)["success"] for s in range(60)]
    assert np.mean(gr) > np.mean(st) + 0.5


def test_heartbeat_delay_follows_geometric_loss_model():
    sim = MissionSim()
    sim.reset(1, "normal")
    sim.loss = 0.2
    d = np.array([sim._delay() for _ in range(20000)])
    assert abs(d.mean() - 1 / 0.8) < 0.03
    assert d.min() == 1.0


def test_blocked_door_costs_time():
    sim = MissionSim()
    t_norm = np.mean([run_episode(GREEDY, s, "normal", sim)["time"] for s in range(80)])
    t_door = np.mean([run_episode(GREEDY, s, "door_blocked", sim)["time"] for s in range(80)])
    assert t_door > t_norm


def test_exported_policy_matches_sb3_logits(tmp_path):
    from sb3_contrib import MaskablePPO

    from learning.abstract_env import SharedPolicyEnv
    from learning.train_ippo import export_policy

    model = MaskablePPO("MlpPolicy", SharedPolicyEnv("normal"), seed=0, n_steps=64, batch_size=64,
                        policy_kwargs={"net_arch": {"pi": [32, 32], "vf": [32, 32]}}, device="cpu")
    path = tmp_path / "p.pt"
    export_policy(model, path)
    pol = TaskPolicy(path)
    obs = np.random.default_rng(0).uniform(0, 1.5, (5, OBS_DIM)).astype(np.float32)
    with torch.no_grad():
        t = torch.as_tensor(obs)
        lat = model.policy.mlp_extractor.forward_actor(model.policy.extract_features(t, model.policy.pi_features_extractor))
        ref = model.policy.action_net(lat).numpy()
    for i in range(5):
        assert np.allclose(pol.logits(obs[i]), ref[i], atol=1e-5)


def test_learned_allocator_only_picks_eligible_tasks(tmp_path):
    from sb3_contrib import MaskablePPO

    from learning.abstract_env import SharedPolicyEnv
    from learning.train_ippo import export_policy
    from swarm.allocation import LearnedAllocator

    model = MaskablePPO("MlpPolicy", SharedPolicyEnv("normal"), seed=1, n_steps=64, batch_size=64, device="cpu")
    path = tmp_path / "p.pt"
    export_policy(model, path)
    book = TaskBook(Blueprint())
    for r in ("R1", "R2", "R3", "R4"):
        book.mark_done(f"inspect:{r}", "B", 1.0)
    book.add_verify("gas", [3.0, 3.0], "R2", "B", 2.0)
    fire = book.add_verify("fire", [-3.0, -3.0], "R3", "B", 2.0)
    alloc = LearnedAllocator("A", "thermal", book, policy_path=path)
    assert alloc.choose(5.0, [-6, 0], {}) == fire
    obs, mask, owner = build_obs(book, "gas", [-6, 0], None, set(), None)
    assert mask.sum() == 1 and list(owner.values())[0].startswith("verify:gas")
    assert obs.shape == (OBS_DIM,) and mask.shape == (N_SLOTS,)


def test_pettingzoo_api_compliance():
    from pettingzoo.test import api_test

    api_test(TaskAllocAEC("normal"), num_cycles=200)
