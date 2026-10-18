"""Launch one Webots world headless-ish, capture its console, and kill it on timeout.

Usage:
    python tools/run_webots.py WORLD [--timeout 300] [--port 1310] [--log out.txt]
        [--gui] [--env KEY=VALUE ...]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WEBOTS = Path(r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe")


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(world: Path, timeout: float, port: int, log: Path | None, gui: bool,
        extra_env: dict[str, str], fast: bool = True) -> int:
    # Webots may save the running scene (poses, customData, swapped controllers) back into the world
    # file on quit, so every launch runs a disposable copy next to the original
    run_world = world.with_name(f"_run{port}_{world.name}")
    shutil.copyfile(world, run_world)
    try:
        return _run(run_world, timeout, port, log, gui, extra_env, fast)
    finally:
        for p in (run_world, run_world.with_name(f".{run_world.stem}.wbproj"), run_world.with_name(f".{run_world.stem}.jpg")):
            try:
                p.unlink()
            except OSError:
                pass


def _run(world: Path, timeout: float, port: int, log: Path | None, gui: bool,
         extra_env: dict[str, str], fast: bool) -> int:
    args = [str(WEBOTS), "--stdout", "--stderr", f"--port={port}"]
    if not gui:
        args += ["--batch", "--minimize", "--no-rendering"]
    if fast:
        args.append("--mode=fast")
    args.append(str(world))
    env = os.environ.copy()
    env.update(extra_env)
    # Webots' Windows launcher only forwards its console when started through a shell redirect
    log = log or Path(os.devnull)
    inner = " ".join(f'"{a}"' if " " in a else a for a in args) + f' > "{log}" 2>&1'
    proc = subprocess.Popen(f'cmd /s /c "{inner}"', env=env)
    start = time.time()
    try:
        while proc.poll() is None:
            if time.time() - start > timeout:
                kill_tree(proc.pid)
                with open(log, "a", encoding="utf-8") as out:
                    out.write(f"\nRUN_WEBOTS_TIMEOUT after {timeout:.0f}s\n")
                return 124
            time.sleep(0.5)
        return proc.returncode
    finally:
        if proc.poll() is None:
            kill_tree(proc.pid)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("world")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--port", type=int, default=1310)
    ap.add_argument("--log")
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--env", nargs="*", default=[])
    a = ap.parse_args()
    env = dict(kv.split("=", 1) for kv in a.env)
    code = run(Path(a.world), a.timeout, a.port, Path(a.log) if a.log else None, a.gui, env,
               fast=not a.realtime)
    print(f"RUN_WEBOTS_EXIT {code}")
    sys.exit(0 if code in (0, 124) else code)


if __name__ == "__main__":
    main()
