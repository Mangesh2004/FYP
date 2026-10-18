"""Passive mission dashboard. It only reads the files a running episode writes into its run
directory (agent telemetry, commander picture, event logs) and pushes them over a WebSocket.
It has no controls: nothing here can change the simulation.

    python -m dashboard.server [--run RUN_DIR] [--port 8765]

Without --run it follows runs/latest.txt, which the supervisor rewrites at every GUI episode start.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
AGENTS = ("A", "B")
STALE_S = 5.0
TIMELINE_MAX = 300
SKIP_EVENTS = {"heartbeat"}


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"),parse_constant=lambda _:None)
    except (OSError, ValueError):
        return None


class EventTail:
    """Incrementally reads appended lines of a jsonl file; tolerates a half-written last line."""

    def __init__(self, path: Path, source: str) -> None:
        self.path, self.source, self.offset, self.buf = path, source, 0, ""

    def read(self) -> list[dict]:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
                self.offset = fh.tell()
        except OSError:
            return []
        self.buf += chunk
        *lines, self.buf = self.buf.split("\n")
        out = []
        for ln in lines:
            try:
                rec = json.loads(ln,parse_constant=lambda _:None)
            except ValueError:
                continue
            if rec.get("ev") not in SKIP_EVENTS:
                rec["src"] = self.source
                out.append(rec)
        return out


class RunWatcher:
    def __init__(self, run_dir: Path | None = None) -> None:
        self.fixed = run_dir
        self.run_dir: Path | None = None
        self.tails: list[EventTail] = []
        self.timeline: list[dict] = []
        bp = _read_json(ROOT / "config" / "blueprint.json") or {}
        self.layout = {k: bp.get(k) for k in ("map_bounds", "rooms", "doors", "corridor", "entrance", "commander")}

    def _current_dir(self) -> Path | None:
        if self.fixed:
            return self.fixed
        try:
            return Path((ROOT / "runs" / "latest.txt").read_text(encoding="utf-8").strip())
        except OSError:
            return None

    def _switch(self, d: Path | None) -> None:
        self.run_dir, self.timeline = d, []
        self.tails = [] if d is None else (
            [EventTail(d / f"agent_{a}.jsonl", a) for a in AGENTS]
            + [EventTail(d / "commander.jsonl", "CMD"), EventTail(d / "supervisor_events.jsonl", "SIM")])

    def snapshot(self) -> dict:
        d = self._current_dir()
        if d != self.run_dir:
            self._switch(d)
        if d is None:
            return {"run_dir": None, "layout": self.layout}
        new = [e for t in self.tails for e in t.read()]
        if new:
            self.timeline = sorted(self.timeline + new, key=lambda e: e.get("t", 0.0))[-TIMELINE_MAX:]
        tele = {a: (_read_json(d / f"fire_telemetry_{a}.json") or _read_json(d / f"telemetry_{a}.json")) for a in AGENTS}
        status = {a: _read_json(d / f"status_{a}.json") for a in AGENTS}
        cmd = _read_json(d / "commander_picture.json")
        sim_t = max([t["t"] for t in tele.values() if t] + [cmd["t"] if cmd else 0.0] +
                    [e.get("t", 0.0) for e in self.timeline[-20:] if e["src"] == "SIM"] + [0.0])
        agents = {}
        for a in AGENTS:
            t = tele[a]
            if not t:
                continue
            agents[a] = {k: t.get(k) for k in ("t", "pose", "state", "task", "mode", "goal", "path", "heat", "gas_ppm",
                                               "capability", "findings", "dets", "peers", "blocked", "done",
                                               "msgs_tx", "msgs_rx", "missing_by_seq", "map_meta",
                                               "sensors", "verification", "camera_t", "detector",
                                               "motion_command", "tilt_deg", "safety_stops")}
            agents[a]["stale"] = sim_t - t["t"] > STALE_S
            for img in ("map_png", "camera_jpg"):
                if t.get(img):
                    agents[a][img] = t[img]
        return {"run_dir": str(d), "sim_t": sim_t, "layout": self.layout, "agents": agents,
                "links": link_graph(agents), "commander": cmd, "status": status,
                "metrics": _read_json(d / "metrics.json"), "timeline": self.timeline[-120:]}


def link_graph(agents: dict) -> list[dict]:
    """Links as each live agent currently believes them: peer heartbeats and commander contact."""
    links = []
    for a, t in agents.items():
        if t.get("stale"):
            continue
        for p, info in (t.get("peers") or {}).items():
            links.append({"from": a, "to": p, "up": bool(info.get("alive"))})
        links.append({"from": a, "to": "CMD", "up": t.get("mode") == "commander_linked"})
    return links


def create_app(run_dir: Path | None = None, period: float = 0.5) -> FastAPI:
    app = FastAPI(title="FYP swarm dashboard (read-only)")
    watcher = RunWatcher(run_dir)
    app.state.watcher = watcher

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    def state():
        return watcher.snapshot()

    @app.get("/fire")
    def fire_view():
        return FileResponse(STATIC / "fire.html")

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        sent_imgs: dict[str, str] = {}
        try:
            while True:
                snap = watcher.snapshot()
                for a, t in snap.get("agents", {}).items():
                    for img in ("map_png", "camera_jpg"):
                        key, val = f"{a}:{img}", t.get(img)
                        if val is not None and sent_imgs.get(key) == val:
                            t.pop(img)
                        elif val is not None:
                            sent_imgs[key] = val
                await sock.send_text(json.dumps(snap,allow_nan=False))
                await asyncio.sleep(period)
        except WebSocketDisconnect:
            return

    return app


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=None)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    print(f"dashboard: http://127.0.0.1:{args.port}  (following {args.run or 'runs/latest.txt'})", flush=True)
    uvicorn.run(create_app(args.run), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
