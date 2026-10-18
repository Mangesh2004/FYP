"""Fails if any agent-side module can reach simulator ground truth.

Agent-side code = controllers/agent, controllers/commander, swarm/, learning/policy_runtime.py.
Forbidden: importing the truth package, using the Supervisor API, camera recognition (which returns
labelled objects), and reading supervisor-only files.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_SIDE = [ROOT / "controllers" / "agent", ROOT / "controllers" / "commander", ROOT / "swarm",
              ROOT / "learning" / "policy_runtime.py", ROOT / "learning" / "features.py"]
FORBIDDEN_NAMES = {"Supervisor"}
FORBIDDEN_ATTRS = {"getFromDef", "getFromId", "getSelf", "getRoot", "getRecognitionObjects", "recognitionEnable",
                   "getRecognitionNumberOfObjects", "getRecognitionSegmentationImage", "getPosition", "getOrientation",
                   "getField", "simulationSetMode"}
FORBIDDEN_STRINGS = ("truth.json", "slots.json", "furniture.json", "supervisor_events", "trajectory.json",
                     "metrics.json", "labels.jsonl")


def violations(source: str, filename: str = "<src>") -> list[str]:
    tree = ast.parse(source, filename)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "truth" or a.name.startswith("truth."):
                    out.append(f"{filename}:{node.lineno} imports {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "truth" or mod.startswith("truth."):
                out.append(f"{filename}:{node.lineno} imports from {mod}")
            for a in node.names:
                if a.name in FORBIDDEN_NAMES:
                    out.append(f"{filename}:{node.lineno} imports {a.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            out.append(f"{filename}:{node.lineno} uses {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            out.append(f"{filename}:{node.lineno} calls .{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for s in FORBIDDEN_STRINGS:
                if s in node.value:
                    out.append(f"{filename}:{node.lineno} references '{s}'")
    return out


def agent_files() -> list[Path]:
    files = []
    for p in AGENT_SIDE:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += sorted(p.rglob("*.py"))
    return files


def test_agent_side_code_has_no_ground_truth_access():
    files = agent_files()
    assert len(files) >= 8, f"expected agent-side modules, found {files}"
    found = []
    for f in files:
        found += violations(f.read_text(encoding="utf-8"), str(f.relative_to(ROOT)))
    assert not found, "ground-truth leaks:\n" + "\n".join(found)


def test_checker_catches_each_kind_of_leak():
    leaks = {
        "import truth.episode": 1,
        "from truth.metrics import score": 1,
        "from controller import Supervisor": 1,
        "x = robot.getFromDef('FIRE_0')": 1,
        "objs = cam.getRecognitionObjects()": 1,
        "open('runs/x/truth.json')": 1,
        "p = node.getPosition()": 1,
    }
    for src, n in leaks.items():
        assert len(violations(src)) >= n, f"checker missed: {src}"
    assert violations("import numpy as np\nx = np.zeros(3)") == []


def test_supervisor_is_the_only_controller_using_supervisor_api():
    sup = (ROOT / "controllers" / "mission_supervisor" / "mission_supervisor.py").read_text(encoding="utf-8")
    assert violations(sup), "sanity: the supervisor should be flagged by the same checker"
