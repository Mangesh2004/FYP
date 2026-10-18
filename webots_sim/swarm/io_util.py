"""File output that survives concurrent readers on Windows."""
from __future__ import annotations

import os
import time
from pathlib import Path


def atomic_write_text(path: Path, text: str, retries: int = 25) -> bool:
    """Write via a temp file and rename. On Windows the rename fails while another process has the
    target open (the supervisor and dashboard poll these files), so retry briefly and give up quietly
    rather than crash the controller."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(retries):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.002 * (i + 1))
    try:
        tmp.unlink()
    except OSError:
        pass
    return False
