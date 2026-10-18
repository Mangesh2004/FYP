import math

from truth.episode import CONDITIONS, make_episode
from truth.gas_model import GasModel, wall_segments_from_world
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_same_seed_same_episode_different_seed_different():
    a, b = make_episode(11, "random"), make_episode(11, "random")
    assert a == b
    layouts = {str((e["hazards"], e["victims"], e["spawns"])) for e in (make_episode(s, "random") for s in range(20))}
    assert len(layouts) >= 18


def test_conditions_share_the_base_layout_and_differ_in_faults():
    eps = {c: make_episode(7, c) for c in CONDITIONS if c != "random"}
    base = {c: (e["hazards"], e["victims"], e["spawns"], e["noise_scale"]) for c, e in eps.items()}
    assert len({str(v) for v in base.values()}) == 1
    assert eps["normal"]["faults"] == [] and eps["normal"]["packet_loss"] == 0.0
    assert eps["loss20"]["packet_loss"] == 0.2
    assert eps["commander_cut"]["faults"][0]["kind"] == "commander_cut"
    assert eps["robot_killed"]["faults"][0]["target"] in ("A", "B")
    assert eps["door_blocked"]["faults"][0]["door"] in ("D1", "D2", "D3", "D4")
    demo = eps["demo"]["faults"]
    assert [f["kind"] for f in demo] == ["door_blocked", "commander_cut", "robot_killed"]
    assert [f["t"] for f in demo] == sorted(f["t"] for f in demo) and eps["demo"]["packet_loss"] == 0.1


def test_hazards_are_inside_their_rooms_and_apart():
    from swarm.blueprint import Blueprint
    bp = Blueprint()
    for s in range(30):
        e = make_episode(s, "random")
        objs = e["hazards"] + e["victims"] + e["distractors"]
        for o in objs:
            assert bp.room_of(*o["pos"]) == o["room"]
        for i, o in enumerate(objs):
            for p in objs[i + 1:]:
                assert math.dist(o["pos"], p["pos"]) > 1.0


def test_gas_concentration_falls_off_and_is_blocked_by_walls():
    walls = wall_segments_from_world((ROOT / "worlds" / "facility.wbt").read_text(encoding="utf-8"))
    assert len(walls) > 10
    gm = GasModel(walls)
    src = [{"pos": [3.5, 3.0], "strength": 1.0}]
    near = gm.concentration((3.5, 2.0), src)
    far = gm.concentration((3.5, 1.5), src)
    behind_wall = (1.0, -0.2)
    through_door = (3.5, -0.2)
    no_walls = GasModel([])
    assert near > far > 0
    assert gm.concentration(behind_wall, src) < 0.2 * no_walls.concentration(behind_wall, src)
    assert math.isclose(gm.concentration(through_door, src), no_walls.concentration(through_door, src))
