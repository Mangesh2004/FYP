"""Trusted local sensor adapter. Only this simulation system reads conditions."""
from dataclasses import dataclass
import json

from .knowledge import LocalKnowledge
from .models import Availability, Observation, _id, _number, _tick
from .simulation import Phase, SimulationEngine


@dataclass(frozen=True)
class SensorRule:
    subject: str
    sensing_capability: str
    confidence: float

    def __post_init__(self) -> None:
        _id(self.subject)
        _id(self.sensing_capability)
        _number(self.confidence, high=1)


@dataclass(frozen=True)
class PerceptionConfiguration:
    rules: tuple[SensorRule, ...] = (
        SensorRule("fire", "thermal", 0.85),
        SensorRule("gas", "gas", 0.9),
        SensorRule("person", "visual", 0.75),
        SensorRule("person", "thermal", 0.6),
    )
    interval_ticks: int = 1
    max_age_ticks: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.rules, tuple) or any(not isinstance(r, SensorRule) for r in self.rules):
            raise ValueError("rules must be a tuple of SensorRule")
        if len({(r.subject, r.sensing_capability) for r in self.rules}) != len(self.rules):
            raise ValueError("duplicate sensor rule")
        _tick(self.interval_ticks)
        _tick(self.max_age_ticks)
        if self.interval_ticks == 0:
            raise ValueError("sensor interval must be positive")


@dataclass(frozen=True)
class LocalPerception:
    configuration: PerceptionConfiguration = PerceptionConfiguration()

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, PerceptionConfiguration):
            raise ValueError("invalid perception configuration")

    def __call__(self, engine: SimulationEngine) -> None:
        tick = engine.time.tick
        if tick % self.configuration.interval_ticks:
            return
        world = engine.world
        for agent in sorted(world.agents, key=lambda a: a.identity.agent_id):
            if agent.availability not in {Availability.AVAILABLE, Availability.BUSY} or agent.battery == 0:
                continue
            local = sorted((c for c in world.conditions if c.location_id == agent.position.location_id), key=lambda c: c.subject)
            for condition in local:
                for rule in sorted(self.configuration.rules, key=lambda r: (r.subject, r.sensing_capability)):
                    if rule.subject != condition.subject or rule.sensing_capability not in agent.capabilities.sensing:
                        continue
                    identity = json.dumps([agent.identity.agent_id, tick, condition.location_id, condition.subject, rule.sensing_capability], separators=(",", ":"))
                    observation = Observation("sensor:" + identity, agent.identity.agent_id,
                                              condition.location_id, condition.subject, condition.value,
                                              tick, rule.confidence, rule.sensing_capability)
                    engine.deliver_observation(agent.identity.agent_id, observation)


def install_local_perception(engine: SimulationEngine, configuration: PerceptionConfiguration = PerceptionConfiguration()) -> None:
    """Install before start, with one independently owned store per existing agent."""
    if not isinstance(configuration, PerceptionConfiguration):
        raise ValueError("invalid perception configuration")
    for agent in engine.world.agents:
        engine.register_local_knowledge(LocalKnowledge(agent.identity.agent_id, configuration.max_age_ticks))
    engine.register_system("local-perception", Phase.PERCEPTION, LocalPerception(configuration))
