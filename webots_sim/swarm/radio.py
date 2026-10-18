"""Radio link over Webots Emitter/Receiver with sequence numbers and a receive-side channel model.

The channel model drops packets with probability  p = base_loss + distance term  where the
distance comes from the Webots signal strength (1/r^2) of each packet. Decision code above this
layer never sees the loss parameters; it only sees which packets arrived.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict


class Radio:
    def __init__(self, emitter, receiver, name: str, base_loss: float, seed: int) -> None:
        self.tx = emitter
        self.rx = receiver
        self.name = name
        self.base_loss = base_loss
        self.rng = random.Random(seed)
        self.seq = 0
        self.sent = 0
        self.received = 0
        self.dropped = 0
        self.bytes_sent = 0
        self.last_seq: dict[str, int] = {}
        self.missing: dict[str, int] = defaultdict(int)

    def send(self, msg: dict) -> None:
        self.seq += 1
        msg = {**msg, "src": self.name, "seq": self.seq}
        data = json.dumps(msg, separators=(",", ":")).encode()
        self.tx.send(data)
        self.sent += 1
        self.bytes_sent += len(data)

    def _drop(self, strength: float) -> bool:
        r = 1.0 / math.sqrt(strength) if strength > 0 else 99.0
        p = self.base_loss + 0.3 * min(1.0, max(0.0, (r - 14.0) / 14.0))
        return self.rng.random() < p

    def poll(self) -> list[dict]:
        out = []
        while self.rx.getQueueLength() > 0:
            strength = self.rx.getSignalStrength()
            data = self.rx.getBytes()
            self.rx.nextPacket()
            try:
                msg = json.loads(data.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            src = msg.get("src")
            if src == self.name:
                continue
            if self._drop(strength):
                self.dropped += 1
                continue
            prev = self.last_seq.get(src)
            if prev is not None and msg.get("seq", 0) > prev + 1:
                self.missing[src] += msg["seq"] - prev - 1
            self.last_seq[src] = msg.get("seq", 0)
            self.received += 1
            out.append(msg)
        return out

    def stats(self) -> dict:
        return {"msgs_tx": self.sent, "msgs_rx": self.received, "bytes_tx": self.bytes_sent,
                "missing_by_seq": dict(self.missing)}
