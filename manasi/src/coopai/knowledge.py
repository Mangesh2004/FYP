"""Agent-owned local evidence; no world access, sharing or belief fusion."""
from dataclasses import dataclass

from .models import Observation, _id, _tick


@dataclass(frozen=True)
class KnowledgeEntry:
    observation: Observation
    received_at: int
    forwarded_by: str | None = None
    message_id: str | None = None
    path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.observation, Observation):
            raise ValueError("expected Observation")
        _tick(self.received_at)
        if self.received_at < self.observation.observed_at:
            raise ValueError("receipt cannot precede observation")
        if (self.forwarded_by is None) != (self.message_id is None):
            raise ValueError("forwarder and message ID must be supplied together")
        if self.forwarded_by is not None:
            _id(self.forwarded_by)
            _id(self.message_id)
        if not isinstance(self.path, tuple):
            raise ValueError("provenance must be a tuple")
        for agent in self.path:
            _id(agent)
        if self.path and (self.path[0] != self.observation.source_id or len(set(self.path)) != len(self.path)):
            raise ValueError("invalid provenance chain")
        if self.is_remote and (len(self.path) < 2 or self.path[-2] != self.forwarded_by):
            raise ValueError("remote provenance must identify the last forwarding sender")

    @property
    def is_remote(self) -> bool:
        return self.forwarded_by is not None

    def is_stale(self, now: int, max_age_ticks: int) -> bool:
        _tick(now)
        _tick(max_age_ticks)
        if now < self.received_at:
            raise ValueError("query time precedes receipt")
        return now - self.observation.observed_at > max_age_ticks


class LocalKnowledge:
    def __init__(self, agent_id: str, max_age_ticks: int = 3):
        _id(agent_id)
        _tick(max_age_ticks)
        self._agent_id = agent_id
        self._max_age_ticks = max_age_ticks
        self.reset()

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def max_age_ticks(self) -> int:
        return self._max_age_ticks

    def stale(self, now: int) -> tuple[KnowledgeEntry, ...]:
        _tick(now)
        return tuple(e for e in self.snapshot() if e.is_stale(now, self.max_age_ticks))

    def reset(self) -> None:
        self._evidence: dict[str, KnowledgeEntry] = {}
        self._latest: dict[tuple[str, str, str], KnowledgeEntry] = {}
        self._history: list[KnowledgeEntry] = []
        self._last_received = 0

    def record(self, observation: Observation, received_at: int) -> bool:
        entry = KnowledgeEntry(observation, received_at)
        if observation.source_id != self.agent_id:
            raise ValueError("local evidence must originate from this agent; peer input is not implemented")
        return self._record(entry)

    def record_peer(self, observation: Observation, received_at: int, *, forwarded_by: str,
                    message_id: str, path: tuple[str, ...]) -> bool:
        if not isinstance(path, tuple) or not path or path[-1] != forwarded_by or self.agent_id in path:
            raise ValueError("invalid peer provenance or forwarding loop")
        entry = KnowledgeEntry(observation, received_at, forwarded_by, message_id, (*path, self.agent_id))
        return self._record(entry, allow_old_receipt=True)

    def _record(self, entry: KnowledgeEntry, *, allow_old_receipt: bool = False) -> bool:
        observation, received_at = entry.observation, entry.received_at
        if received_at < self._last_received and not allow_old_receipt:
            raise ValueError("receipt time cannot regress")
        prior = self._evidence.get(observation.observation_id)
        if prior is not None:
            if prior.observation != observation:
                raise ValueError("observation ID reused with different content")
            return False
        key = (observation.location_id, observation.subject, observation.sensing_capability)
        current = self._latest.get(key)
        order = lambda item: (item.observation.observed_at, item.observation.observation_id)
        self._evidence[observation.observation_id] = entry
        self._history.append(entry)
        self._last_received = max(self._last_received, received_at)
        if current is None or order(entry) > order(current):
            self._latest[key] = entry
        return True

    def lookup(self, location_id: str, subject: str, modality: str) -> KnowledgeEntry | None:
        return self._latest.get((location_id, subject, modality))

    def known(self, location_id: str, subject: str) -> tuple[KnowledgeEntry, ...]:
        """Return latest evidence per modality; no fusion or certainty conversion."""
        return tuple(e for e in self.snapshot() if e.observation.location_id == location_id and e.observation.subject == subject)

    def snapshot(self) -> tuple[KnowledgeEntry, ...]:
        return tuple(self._latest[key] for key in sorted(self._latest))

    @property
    def history(self) -> tuple[KnowledgeEntry, ...]:
        return tuple(self._history)
