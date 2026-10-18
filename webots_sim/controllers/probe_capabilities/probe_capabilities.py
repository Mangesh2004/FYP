"""Probe which Webots features behave as the facility design needs."""
import json
import math
import traceback
from pathlib import Path

LOG = Path(__file__).resolve().parents[2] / "runs" / "probe_capabilities.log"


def log(msg: str) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")
    print(msg, flush=True)


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    from controller import Supervisor

    robot = Supervisor()
    ts = int(robot.getBasicTimeStep())
    cam = robot.getDevice("camera")
    cam.enable(ts)
    cam.recognitionEnable(ts)
    sensors = {n: robot.getDevice(n) for n in ("t_px", "t_py", "t_nx", "t_ny")}
    for s in sensors.values():
        s.enable(ts)

    heat = robot.getFromDef("HEAT").getField("translation")
    wall = robot.getFromDef("WALL").getField("translation")
    heatb = robot.getFromDef("HEAT_BOUNDED").getField("translation")

    def read(label: str) -> None:
        for _ in range(3):
            robot.step(ts)
        log(label + " " + json.dumps({k: round(v.getValue(), 5) for k, v in sensors.items()}))

    read("T0 light at +x 2m clear")
    heat.setSFVec3f([0, 2, 0.3])
    read("T1 light at +y 2m clear")
    heat.setSFVec3f([4, 0, 0.3])
    read("T2 light at +x 4m clear")
    wall.setSFVec3f([1.5, 0, 0.6])
    heat.setSFVec3f([3, 0, 0.3])
    read("T3 light at +x 3m behind wall")
    wall.setSFVec3f([1.5, 0, -5])
    read("T4 light at +x 3m wall removed")

    log(f"recognition count={cam.getRecognitionNumberOfObjects()}")
    for o in cam.getRecognitionObjects():
        n = o.getNumberOfColors()
        log(json.dumps({
            "model": o.getModel(),
            "colors": [round(c, 3) for c in o.getColors()[: 3 * n]],
            "pos_img": list(o.getPositionOnImage()),
            "size_img": list(o.getSizeOnImage()),
        }))
    cam.saveImage(str(LOG.with_suffix(".jpg")), 90)

    heat.setSFVec3f([0, 0, -5])
    robot.getFromDef("HEATB_LIGHT").getField("on").setSFBool(True)
    heatb.setSFVec3f([2, 0, 0.3])
    read("T5 bounded light at +x 2m")
    robot.getFromDef("HEATB_LIGHT").getField("on").setSFBool(False)
    read("T6 bounded light switched off")
    log("PROBE_DONE")
    robot.simulationQuit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log(traceback.format_exc())
        raise
