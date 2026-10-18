"""Observation used by the learned allocator, shared by the abstract training env and the Webots agent.

The observation has N_SLOTS fixed task slots: inspect R1..R4 in slots 0-3 and up to four verification
tasks in slots 4-7 (in the order this agent learned about them). Each slot has N_FEAT features computed
only from the agent's own task book, its own pose and what the peer told it over the radio.
"""
from __future__ import annotations

import numpy as np

N_SLOTS = 8
N_FEAT = 8
OBS_DIM = N_SLOTS * N_FEAT + N_SLOTS
ROOM_SLOTS = {"R1": 0, "R2": 1, "R3": 2, "R4": 3}


def slot_map(book) -> dict[str, int]:
    """task id -> slot index for this agent's book."""
    out = {}
    k = 0
    for tid, t in book.tasks.items():
        if t["kind"] == "inspect":
            out[tid] = ROOM_SLOTS[t["room"]]
        elif k < N_SLOTS - 4:
            out[tid] = 4 + k
            k += 1
    return out


def candidates(book, capability: str) -> list[dict]:
    return [t for t in book.open_tasks() if book.eligible(t, capability)]


def build_obs(book, capability: str, pos, peer_pos, peer_tasks: set[str], current: str | None):
    """Returns (obs vector, mask, slot->task id)."""
    slots = slot_map(book)
    cands = [t for t in candidates(book, capability) if t["id"] in slots]
    feats = np.zeros((N_SLOTS, N_FEAT), np.float32)
    mask = np.zeros(N_SLOTS, np.float32)
    owner: dict[int, str] = {}
    for t in cands:
        s = slots[t["id"]]
        travel = book.travel(pos, t)
        ptravel = book.travel(peer_pos, t) if peer_pos is not None else 30.0
        feats[s] = [book.priority(t), travel / 20.0, ptravel / 20.0,
                    1.0 if t["kind"] == "verify" else 0.0,
                    1.0 if t["id"] in peer_tasks else 0.0,
                    1.0 if t["id"] == current else 0.0,
                    1.0 if peer_pos is None else 0.0,
                    len(cands) / N_SLOTS]
        mask[s] = 1.0
        owner[s] = t["id"]
    return np.concatenate([feats.reshape(-1), mask]), mask, owner
