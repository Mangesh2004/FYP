# Task 9 verification — 14 October 2026

Task 9 completion criteria pass. Environment: macOS Apple Silicon arm64,
Python 3.14.2, existing `.venv`, pytest 9.1.1 and pygame-ce 2.5.8. No dependencies
were installed. No commit/push performed. The repository was already wholly
untracked at inspection; it remains untracked. Scenario JSON was preserved.

## Created files

- `src/coopai/allocation_messages.py`: strict typed wire schema, public coordinator
  rotation, bounded configuration, audit evidence, commit requests/feedback.
- `src/coopai/allocation.py`: independent owner-local negotiation, received-bid
  ranking, reservation/timeout handling, learned owners and immutable diagnostics.
- `src/coopai/allocation_adapter.py`: separate protocol-evidence validation and
  atomic single-owner/free-agent admission. No ranking or winner selection.
- `src/coopai/allocation_demo.py`: actual normal, latency and controlled-award-loss
  traces with deterministic reset comparisons.
- `tests/test_allocation.py`: 74 new behavioral/schema/admission/integration cases.
- `docs/decentralized_allocation.md`: architecture, state machine, public roster,
  messages, timing, proof/capacity semantics, safety/liveness and limitations.
- `docs/task_9_verification.md`: this report.
- `docs/task9_preview.png`, `docs/task9_preview.knowledge.png`,
  `docs/task9_preview.messages.png`, `docs/task9_preview.priority.png`,
  `docs/task9_preview.eligibility.png`, `docs/task9_preview.provisional.png`,
  `docs/task9_preview.allocation.png`: native macOS smoke screenshots.

## Modified files

- `src/coopai/messages.py`: validate registered `allocation.v1` payloads while
  preserving generic coordination and existing message kinds.
- `src/coopai/interfaces.py`: only own-agent commit feedback added to AgentView.
- `src/coopai/simulation.py`: opt-in independent allocators, own announcement/result
  refresh, auditable admission, own feedback and reset lifecycle.
- `src/coopai/scenario.py`: backward-compatible `with_allocation` opt-in.
- `src/coopai/gui.py`: L/Allocation inspector, default negotiation with communication,
  no-allocation CLI, provisional/confirmed screenshots and robust latency/loss smoke.
- `tests/test_gui.py`: two added inspector/disabled-mode integration tests; existing
  tests preserved.
- `README.md`, `PROJECT_CONTEXT.md`, `docs/system_specification.md`,
  `docs/simulation_engine.md`, `docs/visualization.md`,
  `docs/eligibility_suitability.md`: current commands, contracts,
  review scope, physical admission assumptions and GUI behavior.

## Commands and actual results

Baseline `.venv/bin/python -m pytest -q`: **417 passed in 0.55s**.
Final `.venv/bin/python -m pytest -q`: **493 passed in 2.39s**.
All previous tests pass; **76 new tests** (74 allocation + 2 GUI).

Coverage includes strict bid/sender/score/timestamp validation; exact current local
eligibility/suitability; rounds/windows; deterministic coordinator rotation;
priority ordering/ties; actual P2P delivery; multiple-task contention; single-agent
capacity; duplicate/late/unknown/changed messages and closures; lost bids/awards/
acceptance/commits/closure; expired envelopes; temporary/permanent partitions;
coordinator/winner failure; resource/battery changes; atomic proof-backed updates;
missing/unreceived evidence; conflicting commits; local knowledge isolation;
controller result consistency; seeded loss and complete deterministic reset; GUI
provisional versus confirmed ownership, mode switching and reset.

`.venv/bin/python -m compileall -q src tests`: exit 0.
`.venv/bin/python -m pip check`: `No broken requirements found.` Pip disabled an
unwritable user cache and emitted its cache warning; dependency validation passed.

`.venv/bin/python -m coopai.allocation_demo`: exit 0. Actual results:

| Task | Confirmed owner | Default admission tick |
|---|---|---|
| task-1 fire verification | A | 6 |
| task-2 gas inspection | B | 3 |
| task-3 person search | C | 6 |
| task-4 routine inspection | D | 6 |

All tasks are ASSIGNED; all owners have one matching BUSY commitment; positions
remain unchanged. Public task-specific coordinators are B/fire+gas, D/person,
C/routine in epoch 0. Own priority orders rounds. A fire bid is 0.970, C fire
0.727, C person 0.925, D routine 0.712. These are algorithm outputs, not scripted
assignments. B's own protocol uses local audited actions; every peer interaction
uses actual existing transport. At configured latency 2, B commits at 5, others 11.

Controlled failure: all three peer awards dropped at tick 3. Before retry, only
gas/B is assigned. Bounded negotiations close/expire, coordinator roles rotate at
10, routine/D commits at 13 and fire/A/person/C at 16. Failure/latency experiment
horizon is explicitly extended to 40 ticks; tasks/capabilities/events are unchanged.
Normal run prints `allocation_replay_equal=True`; fault run prints
`failure_replay_equal=True`. Complete world/event/message/commit/per-agent
round/history signatures match after reset.

`SDL_VIDEODRIVER=dummy .venv/bin/python -m coopai.gui --smoke-test --screenshot docs/task9_preview.png`:
passed at tick 9; reset clears world assignments/rounds/feedback/audit and existing
knowledge/priority/suitability/message state.

`SDL_VIDEODRIVER=dummy .venv/bin/python -m coopai.gui --smoke-test --latency-ticks 2`:
passed at tick 16.

`SDL_VIDEODRIVER=dummy .venv/bin/python -m coopai.gui --smoke-test --loss-probability 1`:
passed at tick 9; checks ownership safety without asserting successful peer consensus.

Final native `.venv/bin/python -m coopai.gui --smoke-test --screenshot docs/task9_preview.png`:
exit 0, `driver=cocoa tick=9 fire=severe B_battery=40% events=2 reset_ok=True`.
The bounded smoke uses actual button/key events and snapshots provisional state at
clock tick 4 and confirmation after clock tick 9. Provisional and confirmed screenshots
were visually inspected: owner/status labels, received bids, coordinator/deadlines
and selected-agent states are readable and distinct. Further rounds/events scroll.

## Architectural outcome, issues and assumptions

CFP→BID/NO_BID→PROVISIONAL_AWARD→ACCEPT/REJECT→COMMIT→own authoritative
feedback→ROUND_CLOSED. The coordinator compares only received bids and its own
current bid. Ties use score descending, bid tick ascending, agent ID ascending.
One local reservation limits capacity; hard eligibility is rechecked on acceptance,
commit and admission. A score is never substituted for hard requirements.

The simulator does not choose winners. Its separate commit registry validates one
already-selected outcome with actual delivered/processed evidence and atomically
applies pending/free-state guards. This authoritative physical admission provides
single-owner simulation consistency; it is explicitly **not distributed consensus
without that guard under arbitrary network partitions**. Public roster/configured
clock and benign reporting are assumptions. There is no cryptographic authentication.

The loss tests revealed a real baseline weakness: when all peer bids at tick 1
are lost, C can win its local routine-inspection bid, leaving no free visual agent
for person search. The test preserves that pending task instead of forcing a
successful mission or optimizing globally. Timeout recovery does not imply optimal
mission completion. Confirmed ownership is not reassigned, and no execution or
failure recovery after execution is implemented. Task announcements between epochs
wait for the next coordinator; unknown task calls do not publish task definitions.
Histories are retained in memory; current few-agent workload is small.

A delayed closure could have overwritten newer locally announced requirements;
explicit digest/version checks and a regression test now reject that stale closure.
No unresolved implementation blocker remains. Safety/liveness limits are documented.

Ready for a separately authorized abstract movement/task execution milestone using
confirmed task commitments and existing command/feedback interfaces. No movement,
execution, dynamic reassignment, Gazebo, ML or complex consensus was implemented.
Stop for review.
