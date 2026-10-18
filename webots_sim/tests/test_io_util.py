import json
import threading
import time

from swarm.io_util import atomic_write_text


def test_write_succeeds_after_reader_releases_file(tmp_path):
    p = tmp_path / "status.json"
    p.write_text("{}", encoding="utf-8")
    fh = open(p, encoding="utf-8")
    threading.Timer(0.03, fh.close).start()
    assert atomic_write_text(p, json.dumps({"ok": 1}))
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": 1}


def test_write_gives_up_without_raising_while_file_stays_locked(tmp_path):
    p = tmp_path / "status.json"
    p.write_text("{}", encoding="utf-8")
    with open(p, encoding="utf-8"):
        t0 = time.time()
        ok = atomic_write_text(p, "{\"x\": 2}")
        assert time.time() - t0 < 2.0
    import os
    if os.name == "nt":
        assert ok is False
        assert p.read_text(encoding="utf-8") == "{}"
    assert not list(tmp_path.glob("*.tmp"))
