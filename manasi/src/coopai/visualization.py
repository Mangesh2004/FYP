"""Display values and playback controls, with no Pygame dependency.

Privileged snapshots are for the simulator dashboard only, never agent inputs.
"""
from dataclasses import dataclass
from math import isfinite

from .geometry import AreaGeometry, FloorPlan, Rectangle, Segment
from .interfaces import SimulationTime
from .models import AgentState, Task, _number
from .scenario import Scenario, TaskDetails
from .simulation import EventRecord, RunState, SimulationEngine
from .world import WorldCondition


@dataclass(frozen=True)
class ScreenTransform:
    origin_x: float
    origin_y: float
    scale: float

    @classmethod
    def fit(cls, plan: FloorPlan, viewport: Rectangle, padding: float = 24) -> "ScreenTransform":
        _number(padding)
        left = min(a.bounds.x for a in plan.areas)
        top = min(a.bounds.y for a in plan.areas)
        right = max(a.bounds.x + a.bounds.width for a in plan.areas)
        bottom = max(a.bounds.y + a.bounds.height for a in plan.areas)
        width, height = viewport.width - 2 * padding, viewport.height - 2 * padding
        if width <= 0 or height <= 0:
            raise ValueError("viewport is too small for padding")
        scale = min(width / (right - left), height / (bottom - top))
        return cls(viewport.x + (viewport.width - (right - left) * scale) / 2 - left * scale,
                   viewport.y + (viewport.height - (bottom - top) * scale) / 2 - top * scale,
                   scale)

    def point(self, x: float, y: float) -> tuple[int, int]:
        return round(self.origin_x + x * self.scale), round(self.origin_y + y * self.scale)

    def rectangle(self, bounds: Rectangle) -> tuple[int, int, int, int]:
        x, y = self.point(bounds.x, bounds.y)
        right, bottom = self.point(bounds.x + bounds.width, bounds.y + bounds.height)
        return x, y, right - x, bottom - y

    def segment(self, line: Segment) -> tuple[tuple[int, int], tuple[int, int]]:
        return self.point(line.x1, line.y1), self.point(line.x2, line.y2)


@dataclass(frozen=True)
class AgentDisplay:
    state: AgentState
    marker: tuple[float, float]

    @property
    def battery_text(self) -> str:
        return f"{self.state.battery:.0%}"

    @property
    def sensing_text(self) -> str:
        return ", ".join(sorted(self.state.capabilities.sensing)) or "none"

    @property
    def operational_text(self) -> str:
        return ", ".join(sorted(self.state.capabilities.operational)) or "none"


@dataclass(frozen=True)
class TaskDisplay:
    task: Task
    details: TaskDetails
    initial_urgency: float
    marker: tuple[float, float]

    @property
    def requirement_text(self) -> str:
        caps = self.task.requirements.capabilities
        sense = ", ".join(sorted(caps.sensing)) or "none"
        ops = ", ".join(sorted(caps.operational)) or "none"
        return f"Sense: {sense} | Ops: {ops}"

    @property
    def owner_text(self) -> str:
        return self.task.owner_id or "unassigned"


@dataclass(frozen=True)
class DashboardSnapshot:
    time: SimulationTime
    run_state: RunState
    areas: tuple[AreaGeometry, ...]
    conditions: tuple[WorldCondition, ...]
    agents: tuple[AgentDisplay, ...]
    tasks: tuple[TaskDisplay, ...]
    events: tuple[EventRecord, ...]


def capture_dashboard(scenario: Scenario, engine: SimulationEngine) -> DashboardSnapshot:
    """Read existing truth once and derive display-only markers. No engine writes."""
    world = engine.world
    areas = {a.location.location_id: a for a in scenario.floor_plan.areas}
    metadata = {t.task_id: t for t in scenario.task_details}
    initial = {t.task_id: t.urgency for t in scenario.initial_world.tasks}
    agents, tasks = [], []
    for location_id, area in sorted(areas.items()):
        occupants = sorted((a for a in world.agents if a.position.location_id == location_id), key=lambda a: a.identity.agent_id)
        targets = sorted((t for t in world.tasks if t.location_id == location_id), key=lambda t: t.task_id)
        # Offsets separate display markers within the actual location, not physical motion.
        for i, agent in enumerate(occupants):
            marker = ((agent.position.x, agent.position.y) if agent.position.x is not None else
                      (area.bounds.x + area.bounds.width * (i + 1) / (len(occupants) + 1),
                       area.bounds.y + area.bounds.height * 0.55))
            agents.append(AgentDisplay(agent, marker))
        for i, task in enumerate(targets):
            marker = (area.bounds.center if engine.execution_enabled else
                      (area.bounds.x + area.bounds.width * (i + 1) / (len(targets) + 1),
                       area.bounds.y + area.bounds.height * 0.83))
            detail = metadata.get(task.task_id)
            if detail is None:
                spec = engine._execution_adapter.specs.get(task.task_id) if engine.execution_enabled else None
                detail = TaskDetails(task.task_id, spec.task_type if spec else "unregistered", spec.duration_ticks if spec else 1)
            tasks.append(TaskDisplay(task, detail, initial.get(task.task_id, task.urgency), marker))
    return DashboardSnapshot(engine.time, engine.state, tuple(areas.values()), world.conditions,
                             tuple(sorted(agents, key=lambda a: a.state.identity.agent_id)),
                             tuple(sorted(tasks, key=lambda t: t.task.task_id)), engine.event_trace)


class PlaybackController:
    """Wall-time budget chooses when to call engine.step; it is not simulation time."""
    SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
    MAX_STEPS_PER_FRAME = 8

    def __init__(self, engine: SimulationEngine):
        self.engine = engine
        self.speed = 1.0
        self._budget = 0.0

    def play(self) -> None:
        self._budget = 0
        if self.engine.state is RunState.READY:
            self.engine.start()
        elif self.engine.state is RunState.PAUSED:
            self.engine.resume()

    def pause(self) -> None:
        if self.engine.state is RunState.RUNNING:
            self.engine.pause()
        self._budget = 0

    def single_step(self) -> None:
        if self.engine.state in {RunState.FINISHED, RunState.FAILED}:
            return
        self.play()
        self.engine.step()
        self.pause()

    def reset(self) -> None:
        self.engine.reset()
        self._budget = 0

    def set_speed(self, speed: float) -> None:
        if isinstance(speed, bool) or speed not in self.SPEEDS:
            raise ValueError("unsupported playback speed")
        self.speed = speed
        self._budget = 0

    def advance(self, elapsed_seconds: float) -> int:
        _number(elapsed_seconds)
        if self.engine.state is not RunState.RUNNING:
            self._budget = 0
            return 0
        self._budget += elapsed_seconds * self.speed
        if not isfinite(self._budget):
            raise ValueError("elapsed playback budget overflow")
        count = 0
        interval = self.engine.time.tick_seconds
        # Small relative tolerance absorbs floating-point frame-interval summation.
        while self._budget + interval * 1e-12 >= interval and count < self.MAX_STEPS_PER_FRAME:
            self.engine.step()
            self._budget = max(0, self._budget - interval)
            count += 1
            if self.engine.state is not RunState.RUNNING:
                self._budget = 0
                break
        return count
