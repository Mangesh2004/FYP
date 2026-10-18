"""Deterministic headless mechanics. This module never selects task owners.

System/event callbacks are trusted infrastructure. Agent controllers receive only
AgentView. Python encapsulation is an API boundary, not a hostile-code sandbox.
"""
from dataclasses import dataclass, replace
from enum import Enum, IntEnum
from hashlib import sha256
import random
from typing import Callable

from .interfaces import AgentCommand, AgentController, AgentView, SimulationTime
from .models import AgentState, DynamicEvent, MissionConfiguration, Observation, _id, _tick
from .world import WorldSnapshot
from .knowledge import KnowledgeEntry, LocalKnowledge
from .communication import AgentCommunicationPort, AgentEndpoint, CommunicationConfiguration, ShareLocalChanges, SharingPolicy, SimulatedTransport
from .models import Environment
from .allocation import AgentAllocator, AllocationEvent, RoundSnapshot
from .allocation_adapter import CommitRegistry
from .allocation_messages import AllocationConfiguration, CommitFeedback, CommitRequest
from .eligibility import EligibilityConfiguration, SelfEvaluationContext
from .suitability import AgentSuitabilityState, SuitabilityConfiguration, SuitabilityResult
from .execution import AgentExecutor
from .execution_adapter import ExecutionAdapter
from .execution_models import ExecutionConfiguration, ExecutionPhase, ExecutionSnapshot, TaskExecutionSpec
from .geometry import FloorPlan
from .recovery import AgentRecovery
from .recovery_adapter import RecoveryRegistry
from .recovery_models import ObjectiveAnnouncement, RecoveryConfiguration, RecoveryRequest
from .navigation import located_position
from .priority import AgentPriorityState, PriorityConfiguration, PriorityResult, TaskAnnouncement


class RunState(Enum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"


class Phase(IntEnum):
    EXECUTION = 1
    PERCEPTION = 2
    DELIVERY = 3
    LOCAL_UPDATE = 4
    DECISION = 5
    SUBMISSION = 6
    RECORD = 7


@dataclass(frozen=True)
class EventRecord:
    tick: int
    event: DynamicEvent


SystemCallback = Callable[["SimulationEngine"], None]
EventCallback = Callable[["SimulationEngine", DynamicEvent], None]


class SimulationEngine:
    def __init__(self, mission: MissionConfiguration, world: WorldSnapshot):
        if not isinstance(mission, MissionConfiguration) or not isinstance(world, WorldSnapshot):
            raise ValueError("mission and world must be validated models")
        self._mission = mission
        self._world = world
        self._tick = 0
        self._state = RunState.READY
        self._systems: dict[str, tuple[Phase, SystemCallback]] = {}
        self._controllers: dict[str, AgentController] = {}
        self._events: dict[str, tuple[DynamicEvent, EventCallback]] = {}
        self._event_ids: set[str] = set()
        self._rngs: dict[str, random.Random] = {}
        self._observations: dict[str, list[Observation]] = {}
        self._knowledge: dict[str, LocalKnowledge] = {}
        self._knowledge_pending: list[tuple[str, Observation]] = []
        self._transport: SimulatedTransport | None = None
        self._sharing_policies: dict[str, SharingPolicy] = {}
        self._priorities: dict[str, AgentPriorityState] = {}
        self._suitability: dict[str, AgentSuitabilityState] = {}
        self._allocators: dict[str, AgentAllocator] = {}
        self._commit_registry: CommitRegistry | None = None
        self._allocation_pending: list[CommitRequest] = []
        self._allocation_feedback: dict[str, list[CommitFeedback]] = {}
        self._execution_adapter: ExecutionAdapter | None = None
        self._executors: dict[str, AgentExecutor] = {}
        self._recovery_registry = None
        self._recovery_agents = {}
        self._recovery_pending = []
        self._recovery_feedback = {}
        self._mission_announcements = {}
        self._procedure_baseline = None
        self._commands: list[AgentCommand] = []
        self._command_ids: set[str] = set()
        self._trace: list[EventRecord] = []
        self._stepping = False
        self._baseline: tuple[WorldSnapshot, dict[str, tuple[DynamicEvent, EventCallback]]] | None = None

    @property
    def mission(self) -> MissionConfiguration:
        return self._mission

    @property
    def time(self) -> SimulationTime:
        """Tick is the next tick to process; seconds is simulated elapsed time."""
        return SimulationTime(self._tick, self.mission.tick_seconds)

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def world(self) -> WorldSnapshot:
        """Ground truth for trusted systems/renderers; never hand to decisions."""
        return self._world

    @property
    def event_trace(self) -> tuple[EventRecord, ...]:
        return tuple(self._trace)

    def _editable(self) -> None:
        if self._state is RunState.READY or self._stepping:
            return
        raise RuntimeError("world/schedule edits require setup or a trusted step callback")

    def replace_world(self, world: WorldSnapshot) -> None:
        """Atomic validated update; coupled agent/task updates use one snapshot."""
        self._editable()
        if not isinstance(world, WorldSnapshot):
            raise ValueError("expected WorldSnapshot")
        if self._transport and set(self._transport.agent_ids) != {a.identity.agent_id for a in world.agents}:
            raise ValueError("registered communication endpoint roster cannot change")
        if not (set(self._controllers) | set(self._knowledge) | set(self._priorities)) <= {a.identity.agent_id for a in world.agents}:
            raise ValueError("cannot remove an agent with a registered controller or knowledge store")
        if any(a.updated_at > self._tick for a in world.agents) or any(t.created_at > self._tick for t in world.tasks):
            raise ValueError("entity timestamp is in the future")
        self._world = world

    def register_agent(self, agent: AgentState) -> None:
        if not isinstance(agent, AgentState):
            raise ValueError("expected AgentState")
        if any(a.identity.agent_id == agent.identity.agent_id for a in self._world.agents):
            raise ValueError("duplicate agent")
        self.replace_world(replace(self._world, agents=(*self._world.agents, agent)))

    def update_agent(self, agent: AgentState) -> None:
        if not isinstance(agent, AgentState):
            raise ValueError("expected AgentState")
        previous = next((a for a in self._world.agents if a.identity.agent_id == agent.identity.agent_id), None)
        if previous is None:
            raise ValueError("unknown agent")
        if agent.identity != previous.identity or agent.updated_at < previous.updated_at:
            raise ValueError("identity changed or timestamp regressed")
        self.replace_world(replace(self._world, agents=tuple(
            agent if a.identity.agent_id == agent.identity.agent_id else a for a in self._world.agents)))

    def _setup_only(self) -> None:
        if self._state is not RunState.READY:
            raise RuntimeError("registration requires ready state")

    def register_system(self, system_id: str, phase: Phase, callback: SystemCallback) -> None:
        self._setup_only()
        _id(system_id)
        if system_id in self._systems or not isinstance(phase, Phase) or not callable(callback):
            raise ValueError("invalid or duplicate system")
        if phase is Phase.DECISION:
            raise ValueError("decision phase is reserved for restricted agent controllers")
        self._systems[system_id] = (phase, callback)

    def register_controller(self, agent_id: str, callback: AgentController) -> None:
        self._setup_only()
        if agent_id in self._controllers or not callable(callback) or not any(a.identity.agent_id == agent_id for a in self._world.agents):
            raise ValueError("unknown agent, duplicate or invalid controller")
        self._controllers[agent_id] = callback

    def register_local_knowledge(self, store: LocalKnowledge) -> None:
        self._setup_only()
        if not isinstance(store, LocalKnowledge) or store.agent_id in self._knowledge:
            raise ValueError("invalid or duplicate local knowledge store")
        if not any(a.identity.agent_id == store.agent_id for a in self._world.agents):
            raise ValueError("unknown knowledge owner")
        if store.history:
            raise ValueError("initial local knowledge must be empty")
        self._knowledge[store.agent_id] = store

    def knowledge_snapshot(self, agent_id: str) -> tuple[KnowledgeEntry, ...]:
        """Trusted inspector access; controllers receive only their own snapshot."""
        if agent_id not in self._knowledge:
            raise ValueError("no local knowledge registered for agent")
        return self._knowledge[agent_id].snapshot()

    def observation_history(self, agent_id: str) -> tuple[KnowledgeEntry, ...]:
        if agent_id not in self._knowledge:
            raise ValueError("no local knowledge registered for agent")
        return self._knowledge[agent_id].history

    def knowledge_max_age(self, agent_id: str) -> int:
        if agent_id not in self._knowledge:
            raise ValueError("no local knowledge registered for agent")
        return self._knowledge[agent_id].max_age_ticks

    def enable_task_priority(self, configuration: PriorityConfiguration = PriorityConfiguration()) -> None:
        self._setup_only()
        if self._priorities or not isinstance(configuration, PriorityConfiguration):
            raise ValueError("priority already enabled or invalid configuration")
        self._priorities = {a.identity.agent_id: AgentPriorityState(a.identity.agent_id, configuration)
                            for a in self._world.agents}

    def enable_self_evaluation(self, public_topology: Environment,
                               eligibility_configuration: EligibilityConfiguration = EligibilityConfiguration(),
                               configuration: SuitabilityConfiguration = SuitabilityConfiguration()) -> None:
        """Explicitly disclose static topology; independent own-agent evaluations."""
        self._setup_only()
        if self._suitability or not self._priorities:
            raise ValueError("self evaluation requires task announcements/priority; enable only once")
        self._suitability = {a.identity.agent_id: AgentSuitabilityState(a.identity.agent_id, public_topology,
                            eligibility_configuration, configuration) for a in self._world.agents}

    def suitability_snapshot(self, agent_id: str) -> tuple[SuitabilityResult, ...]:
        if agent_id not in self._suitability:
            raise ValueError("self evaluation not enabled for agent")
        return self._suitability[agent_id].results

    def suitability_history(self, agent_id: str) -> tuple[tuple[SuitabilityResult, ...], ...]:
        if agent_id not in self._suitability:
            raise ValueError("self evaluation not enabled for agent")
        return self._suitability[agent_id].history

    def _update_self_evaluations(self):
        for agent_id, state in sorted(self._suitability.items()):
            view = self._agent_view(agent_id)
            state.update(SelfEvaluationContext(view.own_state, view.time.tick, view.known_tasks,
                                               view.evidence_history, state.topology, self._consumed_resources(agent_id)))

    def enable_allocation(self, configuration: AllocationConfiguration = AllocationConfiguration()) -> None:
        self._setup_only()
        if self._allocators or self._transport is None or not self._suitability or not isinstance(configuration, AllocationConfiguration):
            raise ValueError("allocation requires communication/self-evaluation; enable only once")
        roster = self._transport.agent_ids  # Public contacts only, never peer states.
        self._allocators = {a: AgentAllocator(a, roster, configuration) for a in roster}
        self._commit_registry = CommitRegistry(roster, configuration)

    def allocation_snapshot(self, agent_id: str) -> tuple[RoundSnapshot, ...]:
        if agent_id not in self._allocators:
            raise ValueError("allocation not enabled for agent")
        return self._allocators[agent_id].rounds

    def allocation_history(self, agent_id: str) -> tuple[AllocationEvent, ...]:
        if agent_id not in self._allocators:
            raise ValueError("allocation not enabled for agent")
        return self._allocators[agent_id].trace

    @property
    def commit_trace(self) -> tuple[CommitFeedback, ...]:
        return self._commit_registry.trace if self._commit_registry else ()

    @property
    def allocation_enabled(self) -> bool:
        return bool(self._allocators)

    def _run_allocators(self) -> None:
        for agent_id, allocator in sorted(self._allocators.items()):
            port = AgentCommunicationPort(self._transport.endpoint(agent_id), self._tick)
            output = allocator.step(self._agent_view(agent_id), port)
            for evidence in output.evidence:
                self._commit_registry.record(agent_id, evidence)
            for request in output.commits:
                if request.agent_id != agent_id or request.issued_at != self._tick:
                    raise ValueError("spoofed allocation commit")
                self._allocation_pending.append(request)
            for announcement in output.announcements:
                self.announce_task(agent_id, announcement)
            if output.announcements:
                # Received ownership notifications changed this owner's knowledge.
                # Keep later sharing/controller views consistent with those inputs.
                view = self._agent_view(agent_id)
                self._priorities[agent_id].update(self._tick, view.evidence_history)
                state = self._suitability[agent_id]
                state.update(SelfEvaluationContext(view.own_state, self._tick, view.known_tasks,
                                                   view.evidence_history, state.topology, self._consumed_resources(agent_id)))

    def _admit_commits(self) -> None:
        if not self._commit_registry:
            return
        processed = {r.envelope.message_id: r.tick for r in self._transport.trace if r.action == "processed"}
        # Stable admission of already-selected outcomes, not score-based selection.
        for request in sorted(self._allocation_pending, key=lambda r: r.request_id):
            state = self._suitability[request.agent_id]
            view = self._agent_view(request.agent_id)
            context = SelfEvaluationContext(view.own_state, self._tick, view.known_tasks, view.evidence_history, state.topology)
            world, feedback = self._commit_registry.apply(request, self._tick, self._world, context,
                                                         state.eligibility_configuration, processed)
            self.replace_world(world)
            self._allocation_feedback.setdefault(request.agent_id, []).append(feedback)
        self._allocation_pending.clear()

    def enable_execution(self, public_plan: FloorPlan, specs: tuple[TaskExecutionSpec, ...],
                         configuration: ExecutionConfiguration = ExecutionConfiguration(),
                         *, autonomous: bool = True) -> None:
        self._setup_only()
        if self._execution_adapter is not None or not self._allocators or type(autonomous) is not bool:
            raise ValueError("execution requires existing decentralized allocation; enable only once")
        if not isinstance(public_plan, FloorPlan) or public_plan.environment() != self._world.environment:
            raise ValueError("execution geometry and public topology disagree")
        adapter = ExecutionAdapter(public_plan, specs, self._transport.agent_ids, configuration)
        agents = tuple(replace(a, position=located_position(public_plan, a.position)) for a in self._world.agents)
        self.replace_world(replace(self._world, agents=agents))
        self._execution_adapter = adapter
        self._executors = {a.identity.agent_id: AgentExecutor(a.identity.agent_id, public_plan, specs, configuration)
                           for a in agents} if autonomous else {}

    @property
    def execution_enabled(self) -> bool:
        return self._execution_adapter is not None

    def execution_snapshot(self, agent_id: str) -> ExecutionSnapshot:
        if self._execution_adapter is None:
            raise ValueError("execution is not enabled")
        return self._execution_adapter.snapshot(agent_id)

    @property
    def execution_trace(self):
        return self._execution_adapter.trace if self._execution_adapter else ()

    def _execution_announcement(self, task):
        # Trusted validation of this applicant's task, not a decision context.
        spec = self._execution_adapter.specs[task.task_id]
        return TaskAnnouncement(task, spec.task_type, task.created_at)

    def _consumed_resources(self, agent_id):
        if self._execution_adapter:
            own = next(a for a in self._world.agents if a.identity.agent_id == agent_id)
            state = self._execution_adapter.snapshot(agent_id)
            if own.task_id == state.task_id:
                return state.consumed_resources
        return ()

    def _run_executors(self) -> None:
        for agent_id, executor in sorted(self._executors.items()):
            port = AgentCommunicationPort(self._transport.endpoint(agent_id), self._tick)
            announcements = executor.communicate(self._agent_view(agent_id), port)
            for announcement in announcements:
                self.announce_task(agent_id, announcement)
            if announcements:
                view = self._agent_view(agent_id)
                self._priorities[agent_id].update(self._tick, view.evidence_history)
                state = self._suitability[agent_id]
                state.update(SelfEvaluationContext(view.own_state, self._tick, view.known_tasks,
                            view.evidence_history, state.topology, self._consumed_resources(agent_id)))
            for command in executor(self._agent_view(agent_id)):
                if command.agent_id != agent_id or command.issued_at != self._tick or command.command_id in self._command_ids:
                    raise ValueError("invalid autonomous execution command")
                self._command_ids.add(command.command_id)
                self._commands.append(command)

    def enable_recovery(self, configuration: RecoveryConfiguration = RecoveryConfiguration(),
                        *, recoverable_objectives: frozenset[str] | None = None) -> None:
        self._setup_only()
        if self._recovery_registry or not self._executors or not isinstance(configuration, RecoveryConfiguration):
            raise ValueError("recovery requires autonomous execution; enable only once")
        ids = {t.task_id for t in self._world.tasks}
        if recoverable_objectives is None:
            recoverable_objectives = frozenset(ids)
        if not isinstance(recoverable_objectives, frozenset) or not recoverable_objectives <= ids:
            raise ValueError("invalid explicit recoverable objective policy")
        initial = tuple(ObjectiveAnnouncement(t.task_id, 1,
            TaskAnnouncement(t, self._execution_adapter.specs[t.task_id].task_type, self._tick),
            self._execution_adapter.specs[t.task_id], t.task_id in recoverable_objectives) for t in self._world.tasks)
        self._recovery_registry = RecoveryRegistry(initial, configuration)
        self._recovery_agents = {owner: AgentRecovery(owner,
            tuple(a for a in initial if a.spec.task_id in {k.task.task_id for k in self.known_tasks(owner)}), configuration)
            for owner in self._executors}

    @property
    def recovery_enabled(self) -> bool:
        return self._recovery_registry is not None

    @property
    def recovery_trace(self):
        if not self._recovery_registry:
            return ()
        return tuple(sorted((*self._recovery_registry.trace,
            *(e for a in self._recovery_agents.values() for e in a.trace)),
            key=lambda e: (e.tick, e.objective_id, e.task_id, e.agent_id or "", e.action)))

    def recovery_snapshot(self):
        return self._recovery_registry.snapshots() if self._recovery_registry else ()

    def introduce_objective(self, task, spec, recipient_id: str, *, recoverable: bool = True) -> None:
        """Trusted discovery event: register physical truth, disclose to ONE agent."""
        self._editable()
        if not self._stepping:
            raise RuntimeError("introduce objectives inside a trusted runtime discovery callback; use scenario setup for initial tasks")
        if not self._recovery_registry or recipient_id not in self._recovery_agents:
            raise ValueError("new objectives require recovery and a known recipient")
        offer = ObjectiveAnnouncement(task.task_id, 1, TaskAnnouncement(task, spec.task_type, self._tick), spec, recoverable)
        if any(t.task_id == task.task_id for t in self._world.tasks) or task.created_at > self._tick:
            raise ValueError("duplicate/future objective")
        updated = replace(self._world, tasks=(*self._world.tasks, task))
        self._recovery_registry.add_objective(offer, self._tick)
        self.replace_world(updated)
        self._execution_adapter.specs[task.task_id] = spec
        self.announce_task(recipient_id, offer.announcement)
        self._mission_announcements.setdefault(recipient_id, []).append(offer)

    def request_task_release(self, agent_id: str, reason: str = "Explicit owner release") -> None:
        """Trusted user/adapter input; commitment proof validated at submission."""
        self._editable()
        if not self._recovery_registry:
            raise ValueError("recovery is not enabled")
        own = next((a for a in self._world.agents if a.identity.agent_id == agent_id), None)
        if own is None or own.task_id is None or own.availability.value != "busy":
            raise ValueError("release requires an active owner")
        proof = next(f.request.request_id for f in self.commit_trace if f.accepted and f.request.task_id == own.task_id and f.request.agent_id == agent_id)
        from .recovery_models import RecoveryTrigger
        self._recovery_pending.append(RecoveryRequest(agent_id, own.task_id, agent_id, proof, self._tick, RecoveryTrigger.RELEASE, reason))

    def _run_recovery(self):
        for owner, actor in sorted(self._recovery_agents.items()):
            port = AgentCommunicationPort(self._transport.endpoint(owner), self._tick)
            announcements, requests, renewals, specs = actor.step(self._agent_view(owner), port)
            for spec in specs:
                self._executors[owner].specs[spec.task_id] = spec
            for a in announcements:
                # Repeated availability must never overwrite an assigned/terminal snapshot.
                if a.task.task_id not in {k.task.task_id for k in self.known_tasks(owner)} or a.task.status.value == "failed":
                    self.announce_task(owner, a)
            for task_id, proof in renewals:
                self._recovery_registry.renew(self, owner, task_id, proof)
            self._recovery_pending.extend(requests)

    def _admit_recovery(self):
        if not self._recovery_registry:
            return
        for request in sorted(self._recovery_pending, key=lambda r: (r.task_id, r.agent_id, r.trigger.value)):
            feedback = self._recovery_registry.apply(self, request)
            self._recovery_feedback.setdefault(request.agent_id, []).append(feedback)
        self._recovery_pending.clear()

    def announce_task(self, agent_id: str, announcement: TaskAnnouncement) -> None:
        """Trusted explicit mission announcement; does not expose the task registry."""
        self._editable()
        if agent_id not in self._priorities:
            raise ValueError("unknown priority recipient")
        self._priorities[agent_id].announce(announcement, self._tick)

    def set_priority_configuration(self, agent_id: str, configuration: PriorityConfiguration) -> None:
        self._editable()
        if agent_id not in self._priorities or not isinstance(configuration, PriorityConfiguration):
            raise ValueError("invalid mission priority update")
        self._priorities[agent_id].configuration = configuration

    def known_tasks(self, agent_id: str) -> tuple[TaskAnnouncement, ...]:
        if agent_id not in self._priorities:
            raise ValueError("priority is not enabled for agent")
        return self._priorities[agent_id].known_tasks()

    def priority_snapshot(self, agent_id: str) -> tuple[PriorityResult, ...]:
        if agent_id not in self._priorities:
            raise ValueError("priority is not enabled for agent")
        return self._priorities[agent_id].results

    def priority_history(self, agent_id: str) -> tuple[tuple[PriorityResult, ...], ...]:
        if agent_id not in self._priorities:
            raise ValueError("priority is not enabled for agent")
        return self._priorities[agent_id].history

    @property
    def communication(self) -> SimulatedTransport | None:
        """Trusted transport/diagnostics, never supplied to agent callbacks."""
        return self._transport

    def enable_communication(self, configuration: CommunicationConfiguration = CommunicationConfiguration(),
                             policy_factory: Callable[[str], SharingPolicy] | None = None) -> None:
        self._setup_only()
        if self._transport is not None:
            raise ValueError("communication already enabled")
        if not isinstance(configuration, CommunicationConfiguration):
            raise ValueError("invalid communication configuration")
        ids = tuple(sorted(a.identity.agent_id for a in self.world.agents))
        if set(ids) != set(self._knowledge):
            raise ValueError("communication requires independent registered knowledge for every agent")
        endpoints = tuple(AgentEndpoint(self._knowledge[agent], tuple(peer for peer in ids if peer != agent), configuration.ttl_ticks) for agent in ids)
        transport = SimulatedTransport(endpoints, configuration)
        policies = {agent: policy_factory(agent) if policy_factory else ShareLocalChanges() for agent in ids}
        if any(not callable(p) or not callable(getattr(p, "reset", None)) for p in policies.values()):
            raise ValueError("sharing policies require callable and reset interfaces")
        if len({id(p) for p in policies.values()}) != len(policies):
            raise ValueError("each agent must have an independent sharing-policy instance")
        self._transport, self._sharing_policies = transport, policies

    def _agent_view(self, agent_id: str) -> AgentView:
        own = next(a for a in self._world.agents if a.identity.agent_id == agent_id)
        knowledge = self._knowledge[agent_id].snapshot() if agent_id in self._knowledge else ()
        endpoint = self._transport.endpoint(agent_id) if self._transport else None
        return AgentView(self.time, own, tuple(self._observations.get(agent_id, ())), knowledge,
                         endpoint.received_this_tick if endpoint else (), endpoint.peer_statuses if endpoint else (),
                         self._priorities[agent_id].known_tasks() if agent_id in self._priorities else (),
                         self._knowledge[agent_id].history if agent_id in self._knowledge else (),
                         self._priorities[agent_id].results if agent_id in self._priorities else (),
                         self._suitability[agent_id].results if agent_id in self._suitability else (),
                         tuple(self._allocation_feedback.get(agent_id, ())),
                         self._execution_adapter.snapshot(agent_id) if self._execution_adapter else None,
                         self._execution_adapter.feedback(agent_id) if self._execution_adapter else (),
                         tuple(self._recovery_feedback.get(agent_id, ())),
                         tuple(self._mission_announcements.get(agent_id, ())))

    def schedule(self, event: DynamicEvent, handler: EventCallback) -> None:
        self._editable()
        if not isinstance(event, DynamicEvent) or not callable(handler):
            raise ValueError("invalid event or handler")
        if event.event_id in self._event_ids:
            raise ValueError("event IDs cannot be reused before reset")
        if event.tick < self._tick or event.tick >= self.mission.duration_ticks:
            raise ValueError("event outside remaining simulation horizon")
        if self._stepping and event.tick == self._tick:
            raise ValueError("callbacks may schedule only future ticks")
        self._events[event.event_id] = (event, handler)
        self._event_ids.add(event.event_id)

    def random_stream(self, name: str) -> random.Random:
        """Independent named RNG; use only in deterministic trusted callbacks."""
        _id(name)
        if not self._stepping:
            raise RuntimeError("random streams are used inside trusted step callbacks")
        if name not in self._rngs:
            seed = int.from_bytes(sha256(f"{self.mission.seed}:{name}".encode()).digest(), "big")
            self._rngs[name] = random.Random(seed)
        return self._rngs[name]

    def deliver_observation(self, recipient_id: str, observation: Observation) -> None:
        """Trusted adapter publishes already-filtered evidence; no sensing here."""
        if not self._stepping:
            raise RuntimeError("observations are delivered inside trusted step callbacks")
        if not isinstance(observation, Observation):
            raise ValueError("expected Observation")
        agents = {a.identity.agent_id: a for a in self._world.agents}
        locations = {x.location_id for x in self._world.environment.locations}
        if recipient_id not in agents or observation.source_id not in agents or observation.location_id not in locations:
            raise ValueError("unknown observation reference")
        if observation.observed_at > self._tick:
            raise ValueError("future observation")
        if recipient_id in self._knowledge:
            if observation.source_id != recipient_id:
                raise ValueError("peer observations are not implemented for local knowledge")
            self._knowledge_pending.append((recipient_id, observation))
        self._observations.setdefault(recipient_id, []).append(observation)

    def take_commands(self) -> tuple[AgentCommand, ...]:
        """Trusted execution adapter drains intents strictly after issue tick."""
        if not self._stepping:
            raise RuntimeError("commands are consumed inside simulation steps")
        ready = tuple(c for c in self._commands if c.issued_at < self._tick)
        self._commands = [c for c in self._commands if c.issued_at >= self._tick]
        return ready

    def start(self) -> None:
        if self._stepping or self._state is not RunState.READY:
            raise RuntimeError("start requires ready state")
        # Revalidate initial temporal consistency before sealing the replay baseline.
        self.replace_world(self._world)
        self._baseline = (self._world, dict(self._events))
        if self._execution_adapter:
            self._procedure_baseline = (dict(self._execution_adapter.specs), {a: dict(e.specs) for a,e in self._executors.items()})
        for agent_id, state in self._priorities.items():
            state.start()
            state.update(self._tick, self._knowledge[agent_id].history if agent_id in self._knowledge else ())
        for state in self._suitability.values():
            state.start()
        self._update_self_evaluations()
        self._state = RunState.RUNNING

    def pause(self) -> None:
        if self._stepping or self._state is not RunState.RUNNING:
            raise RuntimeError("pause requires running state outside callbacks")
        self._state = RunState.PAUSED

    def resume(self) -> None:
        if self._state is not RunState.PAUSED:
            raise RuntimeError("resume requires paused state")
        self._state = RunState.RUNNING

    def reset(self) -> None:
        if self._stepping:
            raise RuntimeError("cannot reset inside a callback")
        if self._baseline is not None:
            self._world, events = self._baseline
            self._events = dict(events)
        self._event_ids = set(self._events)
        self._tick = 0
        self._state = RunState.READY
        self._rngs.clear()
        self._observations.clear()
        self._knowledge_pending.clear()
        for state in self._priorities.values():
            state.reset()
        for state in self._suitability.values():
            state.reset()
        for store in self._knowledge.values():
            store.reset()
        if self._transport:
            self._transport.reset()
            for policy in self._sharing_policies.values():
                policy.reset()
        for allocator in self._allocators.values():
            allocator.reset()
        if self._commit_registry:
            self._commit_registry.reset()
        self._allocation_pending.clear()
        self._allocation_feedback.clear()
        if self._execution_adapter:
            self._execution_adapter.reset()
        for executor in self._executors.values():
            executor.reset()
        if self._recovery_registry:
            self._recovery_registry.reset()
            for actor in self._recovery_agents.values():
                actor.reset()
        self._recovery_pending.clear()
        self._recovery_feedback.clear()
        self._mission_announcements.clear()
        if self._procedure_baseline:
            specs, actors = self._procedure_baseline
            self._execution_adapter.specs = dict(specs)
            for owner, values in actors.items():
                self._executors[owner].specs = dict(values)
        self._commands.clear()
        self._command_ids.clear()
        self._trace.clear()

    def step(self) -> None:
        if self._stepping or self._state is not RunState.RUNNING:
            raise RuntimeError("step requires running state and is not reentrant")
        self._stepping = True
        try:
            due = sorted((item for item in self._events.values() if item[0].tick == self._tick), key=lambda item: item[0].event_id)
            for event, handler in due:
                handler(self, event)
                del self._events[event.event_id]
                self._trace.append(EventRecord(self._tick, event))
            for phase in Phase:
                if phase is Phase.EXECUTION and self._execution_adapter:
                    self._execution_adapter.advance(self)
                if phase is Phase.PERCEPTION and self._execution_adapter:
                    self._execution_adapter.inspect(self)
                if phase is Phase.DELIVERY and self._transport:
                    self._transport.deliver(self._tick)
                if phase is Phase.LOCAL_UPDATE:
                    for recipient, observation in self._knowledge_pending:
                        self._knowledge[recipient].record(observation, self._tick)
                    self._knowledge_pending.clear()
                    if self._transport:
                        self._transport.process(self._tick)
                if phase is Phase.DECISION:
                    self._run_recovery()
                    for agent_id, state in sorted(self._priorities.items()):
                        view = self._agent_view(agent_id)
                        state.update(view.time.tick, view.evidence_history)
                    self._update_self_evaluations()
                    self._run_allocators()
                    self._run_executors()
                    if self._transport:
                        for agent_id, policy in sorted(self._sharing_policies.items()):
                            port = AgentCommunicationPort(self._transport.endpoint(agent_id), self._tick)
                            policy(self._agent_view(agent_id), port)
                    for agent_id, controller in sorted(self._controllers.items()):
                        commands = controller(self._agent_view(agent_id))
                        if not isinstance(commands, tuple):
                            raise ValueError("controller must return a tuple of commands")
                        for command in commands:
                            if not isinstance(command, AgentCommand) or command.agent_id != agent_id or command.issued_at != self._tick or command.command_id in self._command_ids:
                                raise ValueError("invalid, spoofed or duplicate command")
                            self._command_ids.add(command.command_id)
                            self._commands.append(command)
                        self._observations.pop(agent_id, None)
                    self._allocation_feedback.clear()
                    self._recovery_feedback.clear()
                    self._mission_announcements.clear()
                    if self._execution_adapter:
                        self._execution_adapter.clear_feedback()
                    for agent_id in self._knowledge.keys() - self._controllers.keys():
                        self._observations.pop(agent_id, None)
                else:
                    if phase is Phase.RECORD and self._recovery_registry:
                        self._recovery_registry.observe(self)
                    for _, (system_phase, callback) in sorted(self._systems.items()):
                        if system_phase is phase:
                            callback(self)
                    if phase is Phase.SUBMISSION and self._transport:
                        self._transport.submit(self._tick, self.random_stream("communication-loss"))
                        self._admit_commits()
                        self._admit_recovery()
            self._tick += 1
            if self._tick == self.mission.duration_ticks:
                self._state = RunState.FINISHED
        except Exception:
            self._state = RunState.FAILED
            raise
        finally:
            self._stepping = False

    def run(self, max_steps: int) -> int:
        """Advance at most max_steps; ready auto-starts, paused requires resume."""
        _tick(max_steps)
        if max_steps == 0:
            return 0
        if self._state is RunState.READY:
            self.start()
        if self._state is RunState.FINISHED:
            return 0
        if self._state is not RunState.RUNNING:
            raise RuntimeError("run requires ready or running state")
        completed = 0
        while completed < max_steps and self._state is RunState.RUNNING:
            self.step()
            completed += 1
        return completed
