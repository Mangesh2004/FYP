import re
from pathlib import Path

WORLD = Path(__file__).resolve().parents[1] / "worlds" / "facility.wbt"


def test_world_file_holds_no_saved_runtime_state():
    text = WORLD.read_text(encoding="utf-8")
    assert "customData" not in text, "a Webots run saved per-episode config into the world"
    assert not re.search(r"^\s*hidden ", text, re.M), "a Webots run saved physics state into the world"
    controllers = re.findall(r'controller "([^"]+)"', text)
    assert sorted(c for c in controllers if c != "<none>") == ["agent", "agent", "commander", "mission_supervisor"]
