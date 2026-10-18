import json
import math

from swarm.allocation import CBBAAllocator, GreedyAllocator, StaticAllocator
from swarm.blueprint import Blueprint
from swarm.radio import Radio
from swarm.tasks import TaskBook

BP = Blueprint()


def hb(alloc, book, pos):
    return {"src": alloc.me, "type": "hb", "pose": pos + [0], "cap": alloc.cap, **book.gossip(), **alloc.outgoing()}


def run_cbba(rounds=6, deliver=True, posA=(-6, 0.5), posB=(-6, -0.5)):
    ba, bb = TaskBook(BP), TaskBook(BP)
    a, b = CBBAAllocator("A", "thermal", ba), CBBAAllocator("B", "gas", bb)
    for r in range(rounds):
        t = float(r)
        ta = a.choose(t, list(posA), {})
        tb = b.choose(t, list(posB), {})
        if deliver:
            b.on_peer(hb(a, ba, list(posA)), t)
            a.on_peer(hb(b, bb, list(posB)), t)
    return a, b, ta, tb


def test_cbba_converges_to_conflict_free_bundles_covering_all_rooms():
    a, b, ta, tb = run_cbba()
    assert ta != tb
    won_a = {t for t, w in a.z.items() if w == "A"}
    won_b = {t for t, w in b.z.items() if w == "B"}
    assert not (won_a & won_b), (won_a, won_b)
    assert won_a | won_b == {f"inspect:R{i}" for i in range(1, 5)}
    assert {t for t, w in a.z.items() if w == "B"} == won_b


def test_cbba_without_communication_produces_conflicts():
    a, b, ta, tb = run_cbba(deliver=False)
    won_a = {t for t, w in a.z.items() if w == "A"}
    won_b = {t for t, w in b.z.items() if w == "B"}
    assert won_a & won_b, "without messages both agents should claim overlapping tasks"


def test_cbba_survivor_takes_over_tasks_after_peer_timeout():
    a, b, _, _ = run_cbba()
    before = {t for t, w in a.z.items() if w == "A"}
    a.choose(100.0, [-6, 0.5], {})
    after = {t for t, w in a.z.items() if w == "A"}
    assert len(after) > len(before)
    assert all(w in (None, "A") for w in a.z.values())


def test_verification_task_goes_only_to_capable_agent():
    ba, bb = TaskBook(BP), TaskBook(BP)
    for book in (ba, bb):
        book.add_verify("gas", [3.0, 3.0], "R2", "A", 10.0)
        for rid in ("R1", "R2", "R3", "R4"):
            book.mark_done(f"inspect:{rid}", "A", 1.0)
    a, b = CBBAAllocator("A", "thermal", ba), CBBAAllocator("B", "gas", bb)
    assert a.choose(0, [2.5, 3.0], {}) is None
    assert b.choose(0, [-6, 0], {}).startswith("verify:gas")


def test_greedy_conflict_resolved_by_route_length():
    ba, bb = TaskBook(BP), TaskBook(BP)
    a, b = GreedyAllocator("A", "thermal", ba), GreedyAllocator("B", "gas", bb)
    ta = a.choose(0, [-3.5, 0.0], {})
    tb = b.choose(0, [-3.5, 0.2], {})
    assert ta == tb
    a.on_peer(hb(b, bb, [-3.5, 0.2]), 0.5)
    b.on_peer(hb(a, ba, [-3.5, 0.0]), 0.5)
    ta2 = a.choose(1, [-3.5, 0.0], {})
    tb2 = b.choose(1, [-3.5, 0.2], {})
    assert ta2 != tb2


def test_static_split_never_reassigns():
    book = TaskBook(BP)
    s = StaticAllocator("A", "thermal", book)
    seen = set()
    for i in range(4):
        t = s.choose(i, [-6, 0], {})
        if t is None:
            break
        seen.add(t)
        book.mark_done(t, "A", i)
    assert seen == {"inspect:R1", "inspect:R3"}
    assert s.my_scope_done({"thermal"})


def test_priority_uses_coopai_weights():
    book = TaskBook(BP)
    t = book.tasks["inspect:R1"]
    base = book.priority(t)
    book.urgency["R1"] = 1.0
    assert math.isclose(book.priority(t) - base, 0.25 * 0.6, rel_tol=1e-6)


class FakeEmitter:
    def __init__(self, bus):
        self.bus = bus

    def send(self, data):
        self.bus.append(data)


class FakeReceiver:
    def __init__(self, bus, strength=0.05):
        self.bus = bus
        self.s = strength

    def getQueueLength(self):
        return len(self.bus)

    def getSignalStrength(self):
        return self.s

    def getBytes(self):
        return self.bus[0]

    def nextPacket(self):
        self.bus.pop(0)


def test_radio_loss_rate_matches_configuration():
    bus = []
    tx = Radio(FakeEmitter(bus), FakeReceiver([]), "A", 0.0, 1)
    rx = Radio(FakeEmitter([]), FakeReceiver(bus, strength=1 / 25.0), "B", 0.2, 2)
    for i in range(4000):
        tx.send({"type": "hb", "i": i})
    got = rx.poll()
    rate = 1 - len(got) / 4000
    assert 0.17 < rate < 0.23, rate
    assert sum(rx.missing.values()) == (got[-1]["seq"] - got[0]["seq"] + 1) - len(got)


def test_radio_far_packets_drop_more():
    bus_near, bus_far = [], []
    tx1 = Radio(FakeEmitter(bus_near), FakeReceiver([]), "A", 0.0, 1)
    tx2 = Radio(FakeEmitter(bus_far), FakeReceiver([]), "A", 0.0, 1)
    for i in range(2000):
        tx1.send({"i": i})
        tx2.send({"i": i})
    near = Radio(FakeEmitter([]), FakeReceiver(bus_near, 1 / 25.0), "B", 0.0, 3).poll()
    far = Radio(FakeEmitter([]), FakeReceiver(bus_far, 1 / (30.0 ** 2)), "B", 0.0, 3).poll()
    assert len(near) == 2000
    assert len(far) < 1700
    assert json.loads(json.dumps(near[0]))["src"] == "A"
