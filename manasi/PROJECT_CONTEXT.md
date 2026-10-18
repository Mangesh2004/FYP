# PROJECT_CONTEXT.md

# Decentralized AI Framework for Cooperative Multi-Agent Autonomous Systems

## 1. Project Overview

This is a final-year Bachelor of Engineering Computer Engineering research and implementation project.

We are developing a **simulation-based, decentralized decision-intelligence framework** that enables multiple heterogeneous autonomous agents to cooperate, share information, make decisions, and dynamically allocate responsibilities in changing environments.

The project focuses on **high-level cooperative decision-making**, not low-level robotic navigation, mechanical design, or physical hardware.

The final deliverable must be a fully functional, experimentally validated Python-based framework with an interactive simulation and meaningful performance evaluation.

This is not merely a visualization project. The simulation exists to demonstrate and evaluate the underlying decision algorithms.

## 2. Fundamental Research Problem

Consider an environment containing multiple autonomous agents.

Each agent may have different:

- Sensing capabilities
- Operational capabilities
- Current location
- Battery or resource availability
- Task commitments
- Local observations
- Knowledge and confidence levels
- Communication availability

Meanwhile, tasks and environmental conditions may change dynamically.

A centralized task scheduler creates a potential single point of failure. Static task allocation can also become ineffective when agent availability, mission priorities, or environmental conditions change.

We want to investigate how autonomous agents can make cooperative decisions without permanent dependence on a central decision-making authority.

Our framework continuously addresses four fundamental questions:

1. **Task Priority:** What should be done first?
2. **Agent Eligibility:** Which agents can perform the task?
3. **Agent Suitability:** Which eligible agent is currently most appropriate?
4. **Reassessment:** Should the existing decision continue or change?

These decisions must account for evolving information and mission context.

## 3. Primary Simulation Scenario

The initial and primary validation scenario is **four-room disaster response**.

The environment contains four rooms:

| Room | Initial condition | General priority |
|---|---|---|
| Room 1 | Fire hazard | High |
| Room 2 | Hazardous gas | Medium |
| Room 3 | Possible trapped person | High |
| Room 4 | Clear / routine inspection | Low |

The simulation will include multiple heterogeneous autonomous agents.

Example capabilities include:

- Thermal sensing
- Gas sensing
- Visual observation
- Exploration
- Assistance or task-specific operational capabilities

The exact number of agents, capability combinations, and task requirements should be configurable.

An agent's ability to **detect** a hazard must not automatically imply that it can **resolve** that hazard.

For example, a thermal sensor may provide evidence of a fire, but firefighting requires an additional operational capability.

Likewise, detecting a trapped person does not automatically mean the agent can physically rescue them.

The initial simulation can model tasks such as inspection, hazard verification, locating people, and simulated assistance. It does not need to model realistic firefighting physics or physical rescue.

The four-room environment should be configurable rather than hardcoded into decision algorithms.

We will focus on making this one scenario work correctly before considering additional application domains.

## 4. Fundamental System Architecture

The framework follows a continuous decision cycle:

**Mission Configuration → Local Observation → Agent State Update → Peer-to-Peer Communication → Distributed Knowledge Update → Task Priority → Agent Eligibility → Agent Suitability → Decentralized Task Allocation → Task Execution → Monitoring → Reassessment / Reallocation**

The architecture must distinguish:

### A. Simulation Environment

Maintains the true state of the simulated world, including rooms, hazards, agents, events, and simulation time.

This is the environment's ground truth.

### B. Autonomous Agents

Each agent maintains its own identity, capabilities, resources, current state, local knowledge, and assigned responsibilities.

### C. Perception and Local Knowledge

Agents obtain observations through simulated sensors.

Observations may be incomplete, outdated, uncertain, or inconsistent.

An agent must not have unrestricted access to the simulation's global ground truth.

### D. Peer-to-Peer Communication

Agents exchange observations, state updates, task information, and coordination messages.

Communication should eventually support configurable latency, message loss, and agent disconnection.

### E. Distributed Knowledge

Each agent updates its own knowledge based on local observations and information received from peers.

Confidence, timestamps, source reliability, and stale information may affect how observations are interpreted.

A single globally shared knowledge database must not silently replace agent-local knowledge.

### F. Decision Intelligence

The decision framework evaluates:

- Task priority
- Agent eligibility
- Agent-task suitability
- Allocation and coordination
- Reassessment and reassignment

Decision components must have clearly defined interfaces and be replaceable for research experiments.

### G. Execution and Monitoring

The simulation models abstract agent movement, task progress, resource consumption, and task outcomes.

Events may trigger reassessment.

Examples include new tasks, agent failures, changed priorities, updated observations, resource depletion, and communication disruptions.

### H. Experimentation and Evaluation

The framework records measurable outcomes for reproducible comparisons between decision methods.

## 5. Decentralization Requirements

Decentralization is a fundamental architectural requirement.

The simulation engine may manage global simulation time, environmental events, and physical state.

However, it must not secretly perform task allocation on behalf of the agents.

Task ownership and coordination should emerge through agent-level decisions and communication.

The framework should eventually handle:

- Competing agents requesting the same task
- Conflicting assignments
- Delayed or missing messages
- Agent disconnection
- Task reassignment after failure
- Avoiding unnecessary reassignment
- Recovery when an agent becomes available again

These mechanisms should be developed progressively rather than implemented prematurely.

A centralized simulator is acceptable. A hidden centralized decision authority is not.

## 6. Research and Algorithmic Strategy

We are not committing to a specific advanced AI algorithm at the beginning.

Our research approach is:

**Implement baselines → Identify weaknesses → Design a hybrid or improved method → Evaluate comparatively.**

Potential baseline methods include:

1. Static or predefined task allocation
2. Dynamic rule-based or utility-based allocation
3. Distributed auction / Contract Net allocation

Potential mechanisms for the proposed method include:

- Confidence-aware distributed information updates
- Context-sensitive task priority
- Hard eligibility constraints
- Multi-criteria suitability scoring
- Distributed bidding and task allocation
- Event-triggered reassessment
- Reassignment costs and stability controls
- Failure detection and recovery

These are candidate mechanisms, not established research contributions.

The final algorithm should be selected and refined using literature review, controlled experiments, and observed limitations.

A hybrid approach is acceptable if its components are technically justified and its advantages can be demonstrated.

We must not assume that combining known methods automatically establishes novelty or superiority.

Machine learning, Graph Neural Networks, and Multi-Agent Reinforcement Learning are optional research directions, not mandatory technologies.

Do not introduce them unless a specific limitation justifies their use.

## 7. Implementation Technology

Primary development environment:

- macOS on Apple Silicon
- MacBook Air M4
- 16 GB RAM
- Python
- VS Code
- Codex
- Git and GitHub

Initial implementation:

- Python core framework
- Deterministic, step-based simulation
- Structured scenario configuration
- Automated testing with pytest
- Research logging and performance analysis

Visualization options:

- Pygame for an interactive 2D simulation
- Matplotlib for graphs and performance results
- Optional Three.js visualization later, if justified

The initial implementation should not depend on a complex 3D engine.

The core decision framework must remain independent of visualization.

Gazebo, ROS 2, robotics hardware, physical prototypes, and drone logistics are outside the current implementation scope. A future Gazebo adapter is permissible; current simulation mechanics must remain separate from decentralized decision algorithms.

## 8. Experimental Validation

The completed framework should support reproducible experiments.

Candidate metrics include:

- Mission completion time
- Task completion rate
- Allocation latency
- Reallocation latency
- Recovery time after agent failure
- Communication overhead
- Resource utilization
- Unnecessary task switching
- Unassigned or conflicting tasks

Experiments should use controlled conditions, consistent scenario definitions, reproducible random seeds, and fair comparisons.

The final report should identify both strengths and weaknesses.

Do not fabricate improvements, simulation results, or research novelty.

## 9. Long-Term Development Roadmap

The implementation will proceed through seven phases.

### Phase 1 — Foundation

1. Formalize system specification and interfaces.
2. Establish Python project structure, tests, and documentation.
3. Build the deterministic simulation engine.

### Phase 2 — Environment, Agents, and Tasks

4. Implement environment representation.
5. Implement heterogeneous agents and their states.
6. Implement tasks, requirements, and lifecycle.
7. Implement scenario configuration loading.

### Phase 3 — Distributed Information

8. Implement local observations.
9. Implement agent-local knowledge.
10. Implement peer-to-peer messaging.
11. Implement distributed information updates and confidence handling.

### Phase 4 — Decision Modules

12. Implement dynamic task priority.
13. Implement agent eligibility.
14. Implement agent-task suitability.
15. Implement decentralized baseline allocation and conflict handling.

### Phase 5 — Execution and Reassessment

16. Implement abstract task execution and movement.
17. Implement monitoring and dynamic events.
18. Implement reassessment, reassignment, and stability mechanisms.
19. Implement metrics, event logging, and visualization.

### Phase 6 — Research Validation

20. Implement and compare baseline and proposed methods.
21. Conduct reproducible experiments.
22. Analyze results and limitations.

### Phase 7 — Finalization

23. Complete documentation and technical report.
24. Prepare and verify the final demonstration and reproducibility package.

The roadmap may be refined if implementation or research reveals justified changes, but changes must be documented.

## 10. Development Workflow

The project is implemented incrementally.

For every task:

1. Review the project context.
2. Understand the current task and dependencies.
3. Inspect existing code.
4. Implement only the requested scope.
5. Write appropriate tests.
6. Run the tests.
7. Report changes, decisions, and limitations.
8. Await review before moving to the next task.

Do not implement multiple future milestones merely because they are mentioned in the roadmap.

Do not introduce unnecessary frameworks or abstractions.

Prefer understandable, modular, typed, well-tested Python code.

Make reasonable implementation decisions independently when they do not conflict with the project objectives.

If a major architectural decision could affect decentralization, correctness, or research validity, explain the trade-offs before implementing it.

## 11. Project Success Criteria

The project is successful when:

1. Multiple heterogeneous agents operate in a simulated environment.
2. Agents observe their environment and maintain local knowledge.
3. Agents exchange information peer-to-peer.
4. Task priorities change appropriately with mission context.
5. Eligibility constraints are correctly enforced.
6. Suitability decisions account for agent state and task requirements.
7. Tasks are allocated through decentralized coordination.
8. Dynamic changes trigger appropriate reassessment.
9. Failures and communication disruptions can be tested.
10. Baseline and proposed decision methods can be compared.
11. Experiments produce reproducible and interpretable results.
12. An interactive simulation demonstrates the system's behavior.
13. The implementation is documented, tested, and reproducible.

The project must prioritize correctness, genuine decentralized decision-making, research validity, and explainability over unnecessary visual complexity.

## 12. Instructions to Codex

Treat this document as persistent project context.

Use it when making architectural and implementation decisions.

Do not interpret future roadmap items as permission to implement them immediately.

Task 1 established the specification and Python foundation. Task 2 established the deterministic headless simulation engine. Task 3 adds the configured four-room environment and JSON scenario loading. Task 4 adds the first interactive 2D visualization. Task 5 adds local perception/knowledge; Task 6 adds peer communication. Task 7 adds mission-aware agent-local priority. Task 8 adds hard eligibility and agent-local suitability. Task 9 adds bounded Contract Net-inspired decentralized allocation. Task 10 adds autonomous abstract movement and task execution. Task 11 adds bounded failure-driven recovery, fresh attempts, new task sharing and recovery metrics. Task 12 adds a validated five-scenario registry, full autonomous review missions, a GUI scenario menu, stored decision explanations and mission summaries. Priority-driven preemption and comparative experiments remain follow-up work.

When the implementation is complete, report the actual test results and wait for review before proceeding.

## 13. Academic review target and milestone update

Updated requirement supplied on 14 October 2026: an academic implementation review
is approximately eight days away. Prioritize a functional interactive four-room
Python simulation with real decentralized decision logic. Develop a 2D visualization
early, after the simulation engine and basic environment. Correctness and
decentralization take priority over appearance. Task 2 remains headless and excludes
allocation, fusion, perception, advanced movement and visualization. A future Gazebo
adapter must use clean time/observation/command/event interfaces; no Gazebo integration
or dependencies are authorized now. Stop for review after each bounded milestone.


## 14. Task 3 environment and next milestone

Task 3 uses four rectangular rooms connected through one shared corridor, with
an exterior entry, four heterogeneous agents and four unassigned inspection/search
tasks. Layout geometry is separate from authoritative hazards and task state.
A strict JSON loader constructs existing models and uses the existing deterministic
engine. Scripted condition/battery changes demonstrate event application without
perception or agent intelligence. See docs/scenario_loading.md for the schema.
Task 4 should display rooms, walls/passages, agent markers/IDs/capabilities, task
markers, hazards, clock, play/pause/step/reset controls and an event log. No GUI,
Pygame, allocation or navigation is part of Task 3. Future Gazebo compatibility
remains an interface constraint, not permission to implement an adapter now.


## 15. Task 4 visualization milestone

The interactive dashboard uses the existing scenario/engine and labels all privileged
information SIMULATOR VIEW — NOT AGENT KNOWLEDGE. It provides Play/Pause/Step/Reset,
speed selection, geometry, agent/task status and applied-event logging. Rendering
and wall-time playback remain separate from the engine and agent decision interface.
Pygame Community Edition is an optional GUI dependency because standard pygame has
no matching native wheel for the current Python 3.14 environment. Agents stay
stationary; tasks stay pending/unassigned. No perception, communication, fusion,
allocation, navigation or Gazebo integration is authorized by Task 4. See
`docs/visualization.md`. Stop for review before subsequent intelligence milestones.


## 16. Task 5 perception and local knowledge

Task 5 implements current-location, capability-gated perception with timestamped,
confidence-bearing observations. Independent LocalKnowledge instances record each
agent's evidence, retain uncertainty and history, select latest evidence per modality,
and flag staleness without truth backfill. The engine manages their reset lifecycle;
controllers receive only their own immutable knowledge snapshot. GUI circle/1–4
selection shows a separate agent-local knowledge inspector. Generic scenario engines
opt in; the GUI enables sensing automatically. No random sensor noise is introduced.
Peer communication, fusion, scoring, allocation, navigation, execution and Gazebo
remain future work. See `docs/perception_and_knowledge.md`. Stop for review.


## 17. Task 6 peer communication

Independent agent endpoints exchange explicit observation/status/coordination
messages through deterministic simulated transport. Positive latency, seeded loss,
directed links, temporary disconnections, expiration and duplicate handling are
implemented. Source/evidence identity, forwarding sender, confidence and observed/
received timestamps are retained in recipient-local stores. No shared belief
synchronization or confidence inflation is allowed. Agent policies receive tick-bound
send ports and local views only. Reset clears transport/endpoint/policy state and
knowledge for replay. GUI M/Messages provides privileged diagnostics; knowledge
shows Direct/Remote provenance. Generic engines opt in; GUI defaults to messaging.
See `docs/communication.md`. No priority/eligibility/suitability algorithms,
allocation, movement, execution, Gazebo or advanced fusion is authorized by Task 6.
Stop for review before decision-making milestones.


## 18. Task 7 mission-aware task priority

Each agent maintains explicit versioned task announcements and an independent
priority state. Initial mission tasks are announced to all agents; new world tasks
remain hidden until a permitted announcement. A pure weighted baseline combines
announced urgency, deadline pressure, local evidence risk/confidence/freshness,
mission importance and uncertainty. Confidence/freshness quality filtering and a
conservative conflict rule preserve provenance without advanced fusion. Configuration
is public mission data in the optional scenario `priority` section, not room-based
logic. GUI P/Priority shows selected-agent calculated priorities. Reset restores
announcements/settings and reproduces results. No scoring of agent eligibility or
suitability, allocation, execution, navigation, Gazebo or ML is authorized by Task 7.
See `docs/task_priority.md`. Stop for review before the next milestone.


## 19. Task 8 hard eligibility and self-suitability

Task 8 adds exact category-specific capabilities, availability/status, commitments,
known lifecycle, battery/resource requirements, directed reachability and optional
local-evidence restrictions as hard feasibility rules. A separate configurable
weighted baseline scores eligible tasks using battery/resource reserve, public graph
travel effort and local information quality. Each agent evaluates only itself and
its announced tasks; no world/peer truth or global ranking enters decisions. The
GUI E inspector separates priority, eligibility and suitability. Reset reproduces
independent histories. See `docs/eligibility_suitability.md`. No allocation,
reassignment, movement, execution, Gazebo or ML is authorized by Task 8. Stop for
review before decentralized allocation and conflict-handling work.


## 20. Task 9 decentralized allocation baseline

Independent agent allocators coordinate task-specific bounded rounds using a shared
public contact roster/clock and rotating coordinators. Actual typed P2P calls,
local eligibility/suitability bids, received-bid ranking, provisional awards,
explicit acceptance and commit evidence determine outcomes. Priority orders local
coordination and competing awards. A separate simulator admission adapter validates
one already-selected outcome and atomically applies pending task ownership/free-agent
capacity; it never chooses a candidate or compares private scores. This single-owner
simulation guard is an explicit assumption, not a claim of distributed consensus
under arbitrary partitions. Local reservations expire, retries rotate coordinators,
confirmed owners notify peers, and reset reproduces all histories. GUI L inspection
separates provisional/protocol/authoritative ownership. The greedy baseline can leave
person search pending when bid loss consumes the only visual agent on routine work;
no global optimization silently repairs that limitation. See
`docs/decentralized_allocation.md`. No movement, task execution, post-execution
reassignment, ML, Gazebo or advanced consensus is authorized by Task 9. Stop for
review before abstract execution and monitoring.


## 21. Task 10 autonomous movement and evidence-backed execution

Owner-local execution controllers act only on their own confirmed assignment and
public geometry/procedures. Coordinate positions, graph-selected center/door
waypoints and a separate command-validation adapter produce deterministic motion.
Explicit begin/work/complete commands enforce arrival, capability, duration,
operational status, energy/resources and fresh direct observations. Required
consumables are charged once; consumed quota is credited only to the same active
owned task. Coarse exploration inspection has an explicit operational modality,
not fabricated thermal/gas/visual capabilities. Successful inspection does not
resolve environmental hazards. Failures stop motion, clear active ownership/capacity
and retain terminal FAILED status rather than silently reopening/reassigning work.
Typed peer status notices preserve independent task awareness. GUI X shows real
poses/routes/progress/outcomes; reset reproduces full histories. Default completion
ticks are fire/A 12, gas/B 9, person/C 13, routine/D 15; D moves at 8 and arrives
11. Deadlines remain priority-pressure inputs; fire/person complete after desired
tick 8. See `docs/movement_execution.md`. No full reassignment, advanced recovery,
robot collisions/physics, ROS2, Gazebo or ML is authorized by Task 10. Stop for
review before Task 11 reassessment/reallocation.


## 22. Task 11 local reassessment and decentralized recovery

Each agent detects own execution failure or peer heartbeat silence from its own view.
Explicit recovery policy and bounded attempt budget govern retry admission. The
simulator validates the old commitment/lease and atomically registers a fresh attempt;
it never ranks replacements. FAILED stays terminal and preserves previous owner,
progress, timing and outcome. Fresh attempt IDs isolate stale bids, ACKs, notices
and commands. New availability reaches peers through actual repeated messages;
existing local priority, hard eligibility, suitability and Contract Net allocation
select replacements. New tasks are explicitly disclosed to one initial recipient.
A healthy owner renews its authoritative lease despite peer network loss; silence
alone cannot revoke its ownership. This is centralized physical validation, not
distributed consensus. Optional full priority preemption is deferred; reversible
operator release uses explicit restart/no-refund semantics. H/Recovery preserves
R/reset and all previous GUI controls. See docs/dynamic_reassessment_reallocation.md
for actual D→A recovery, new-task/disruption fixtures, measured metrics and limits.
No comparative superiority, new research novelty, ML, ROS2/Gazebo or future hardware
integration is claimed. Stop for review before research experiments/further milestones.


## 23. Task 12 multi-scenario review simulation

The review date is October 21, 2026. Five JSON mission configurations reuse the same
local perception/knowledge, P2P messaging, priority, eligibility/suitability, allocation,
physical execution and recovery pipeline: corridor, distributed, emergency,
failure-disruption and constrained. Configurations define conditions and verification
assertions, never winning agents, replacement identities or movement scripts. Optional
coordinates, typed task-discovery/power/connection events and an explicit opt-in for
infeasible objectives extend existing loading. Review capability vocabulary/procedures
and all model/geometry/resource references are validated. Legacy JSON/fixtures remain.

S selects a fresh correctly configured engine; Tab/clicked task rows/rings explain the
selected agent's actual stored evaluations, bids, evidence and local owner belief.
V summarizes objectives separately from failed attempts; J filters significant events.
Existing K/M/P/E/L/X/H and playback/reset/selection controls remain. All movement uses
physical simulator coordinates. A power outage is distinct from a healthy agent's
communication disconnection. Mission summaries never claim hazard mitigation.

All five headless scenarios replay deterministically, including an explicitly pending
resource-constrained objective. Native macOS GUI selection/reset is verified. This is
functional review readiness, not benchmarking or comparative research evidence.
See docs/task_12_scenarios.md and docs/task_12_verification.md. Task 13 addresses
adaptive decision refinements; Task 14 research comparisons; Task 15 final validation
and presentation. Stop for review before those tasks. No automatic commit/push.
