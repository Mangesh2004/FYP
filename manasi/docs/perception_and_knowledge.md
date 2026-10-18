# Local perception and agent-local knowledge — Task 5

## Running

```sh
.venv/bin/python -m coopai.perception_demo
.venv/bin/python -m coopai.gui
.venv/bin/python -m pytest
```

No additional dependencies are required. The GUI now enables perception by default.
Generic engine/scenario demos retain their earlier behavior; explicitly opt in:

```python
from coopai.scenario import load_scenario
from coopai.perception import PerceptionConfiguration, install_local_perception

scenario = load_scenario("scenarios/four_room_disaster.json")
engine = scenario.create_engine(with_perception=True)
# For custom sensor rules/cadence/freshness, create without perception and call:
# install_local_perception(engine, PerceptionConfiguration(interval_ticks=2))
```

## Perception boundary and rules

`LocalPerception` is a trusted simulation adapter registered in PERCEPTION. It reads
truth, filters by current location and actual sensing capability, and calls the
existing `deliver_observation` interface with immutable Observation values.
Operational capabilities never grant sensing capabilities. Available/busy agents
with positive battery can sense; failed/offline/unpowered agents cannot. No energy
is consumed and no movement is introduced. Detection range is the current location
only, with no adjacent-room sensing.

Default SensorRules are explicit, configurable subject/modality pairs:

| Subject | Required sensing tag | Confidence input |
|---|---|---|
| fire | thermal | 0.85 |
| gas | gas | 0.90 |
| person | thermal | 0.60 |
| person | visual | 0.75 |

Thus A observes room-1 fire, B room-2 gas, C room-3 possible person via thermal and
visual, and D observes no specialized conditions. There is no rule that converts
exploration or an absence of observations into a clear/safe room conclusion.
Unsupported condition subjects are unknown to sensors until an explicit rule is
added. Default confidence is illustrative evidence quality, not calibrated
probability or a claim of experimental accuracy. No advanced sensor physics or
random noise is implemented. Values are copied only after the access filter, with
confidence attached; `possible` is never converted to `present` or confirmed.

PerceptionConfiguration validates a tuple of unique SensorRules, confidence [0,1],
a positive integer interval_ticks and nonnegative max_age_ticks. Default cadence
is every tick and freshness limit is three ticks. Conditions and agents are
iterated in stable ID/subject/modality order. Observation IDs encode agent, tick,
location, subject and modality in an unambiguous JSON tuple. Timestamps use the
current simulation tick. Same seed/scenario reproduces histories; differing seeds
also give identical sensing in this milestone because no noise is introduced.
A future noise adapter must use existing named seeded streams, never global random.

## Independent local stores and update policy

`LocalKnowledge` in `coopai.knowledge` has one immutable owner ID, a freshness policy,
a private evidence-ID index, ordered history and latest-evidence index. Every agent
has a separate instance. The engine's owner-to-store registry is lifecycle plumbing,
not a shared global belief store. Controller callbacks get only their own immutable
snapshot in the new default-empty `AgentView.knowledge` field.

`record(observation, received_at)` accepts only evidence whose original source is
this store's agent. Future peer observations require an explicit new input path;
no peer communication is implemented. It validates nonfuture receipt ordering,
ignores exact duplicate IDs, and rejects ID reuse with changed content. Late old
observations remain in history but never overwrite newer evidence. Unique receipt
ticks cannot regress. History records both original Observation and receipt tick.

Latest entries are keyed by (location, subject, modality). Newest observed tick wins;
equal observed ticks use lexicographically greatest observation ID as a deterministic
tie break. Confidence is preserved and does not override time ordering. Multiple
modalities remain separate; their agreement does not multiply confidence or produce
a fused belief. `lookup` retrieves one modality, `known` retrieves all modalities
for a subject/location, `snapshot` returns sorted latest evidence, and `history`
returns all unique received evidence in arrival order. Empty results mean unknown.

`KnowledgeEntry.is_stale(now, max_age_ticks)` is true when
`now - observed_at > max_age_ticks`; evidence at the exact limit is still fresh.
`LocalKnowledge.stale(now)` applies the store's configured limit. Staleness never
deletes evidence, changes a value, or lowers its stored confidence silently. No
fusion, confidence decay, probability calibration, conflict resolution or truth
backfill is implemented. Repeated observations are genuine repeated local samples,
not independent corroboration increasing certainty.

## Engine integration and reset lifecycle

`register_local_knowledge` requires setup state, a known unique owner and an empty
store. Registered owners cannot be removed from world snapshots. `install_local_perception`
registers one store per initial agent and the trusted sensor system before start.
New agent runtime registration would require a subsequent lifecycle extension.

Delivered observations for registered owners are queued for local recording. At
the beginning of LOCAL_UPDATE, the engine records pending local evidence before
agent decisions. Decision callbacks receive their own raw inbox plus current local
knowledge; agents without controllers still record evidence and their raw inbox is
drained after the decision phase to avoid accumulation. No global conditions, peer
stores, task catalogue, event schedule or simulator reference is added to AgentView.
Perception/delivery adapters should emit before LOCAL_UPDATE; evidence emitted after
that phase is recorded on the following tick.

The engine owns the stores' reset lifecycle: `reset` clears pending evidence, raw
inboxes, store histories/latest indexes and existing runtime buffers while retaining
store identities, freshness settings and installed sensor rules. Observations are
regenerated from tick 0, so histories replay without external reset hooks. The
stateless LocalPerception adapter retains no RNG or counters. This addresses the
callback-owned-state limitation for these stores only; arbitrary user closures,
controller-owned memory, files and other future systems are still not rewound.
Failed runs can contain partial observations, but reset clears them consistently
with the existing fail-stop engine contract.

The generic delivery interface is trusted: references/timestamps are validated,
but adapters remain responsible for sensor access checks. LocalPerception enforces
those checks. An external caller that captures world/engine or mutates a registered
store directly can bypass an ordinary Python API boundary; this is not a malicious
code sandbox. Real future Gazebo adapters can emit the same Observation type into
this interface without exposing their private world to decisions.

## GUI inspection

Click an agent circle, press 1–4 (A–D), or click Knowledge. K/Status toggles back
to the original agent/task/event panel. Selecting an agent changes the right panel
only; the left plan remains explicitly labeled SIMULATOR VIEW — NOT AGENT KNOWLEDGE.
The new heading is AGENT-LOCAL KNOWLEDGE / selected ID. Entries show local value,
modality, original source, confidence, observed/received ticks and fresh/STALE status.
Unknown is explicitly distinct from clear/safe. Reset retains selection and clears
all local evidence. Displayed freshness uses the selected store's configured limit.

The inspector reads recorded evidence only. A failed sensor can retain an old
`fire: present` entry marked STALE while the separate truth plan shows severe fire;
a regression test verifies this. No rendering data feeds back into knowledge.
See [local inspector preview](task5_preview.knowledge.png).

## Demonstration and limitations

The six-tick demo reports A's fire present then severe at observation tick 3,
B's local gas, C's possible-person evidence at 60%/75%, and D unknown. Histories
contain A:6, B:6, C:12, D:0 entries. Reset clears them and the six-tick replay
compares full histories for equality. No observations or results are fabricated.

Sensors are ideal categorical transducers with configurable confidence, not
realistic fire/gas/human sensor physics. There is no false positive/negative model,
neighbor range, task discovery, movement, communication, decisions or execution.
History is retained for the bounded mission and has no long-run retention policy.
No new packages are installed. Next milestone: peer-to-peer communication with
explicit provenance, receipt timing and access controls, followed by justified
knowledge updates; do not interpret the new evidence stores as implemented fusion.


## Task 6 update

Local `record` retains its direct-only source guard. New `record_peer` accepts
validated original evidence and forwarding provenance via explicit endpoint
processing. Exact evidence IDs remain unique without confidence inflation. Delivery
and original observation times are preserved; late old observations do not overwrite
newer entries. The GUI now defaults to messaging; use --no-communication for the
isolated Task 5 behavior. See [communication](communication.md).


Task 10 advances actual coordinate/location state before perception. Specialized
sensing continues to use only the current location and existing sensing tags.
An active arrived routine-inspection procedure can produce a coarse direct
`operational:exploration` observation of its local configured hazard subject.
This explicitly operation-derived modality does not add a sensor to the agent's
capability set; it runs only after beginning the appropriate owned task. Missing
conditions remain unknown. Reports retain timestamps/confidence and are shared
through the existing peer channel. See [execution evidence rules](movement_execution.md).
