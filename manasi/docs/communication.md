# Peer communication and distributed evidence — Task 6

## Run and configure

```sh
.venv/bin/python -m coopai.communication_demo
.venv/bin/python -m coopai.gui
.venv/bin/python -m coopai.gui --latency-ticks 2 --loss-probability 0.25 --ttl-ticks 8
.venv/bin/python -m coopai.gui --no-communication
.venv/bin/python -m pytest
```

No new dependencies. Generic scenario engines retain the previous default without
perception or communication. `scenario.create_engine(with_communication=True)`
enables local perception and the default sharing policy. For custom network settings:

```python
from coopai.scenario import load_scenario
from coopai.communication import CommunicationConfiguration, Disconnection

scenario = load_scenario("scenarios/four_room_disaster.json")
engine = scenario.create_engine(with_perception=True)
engine.enable_communication(CommunicationConfiguration(
    latency_ticks=2, loss_probability=0.2, ttl_ticks=8,
    links=(("A", "B"), ("B", "A")),
    disconnections=(Disconnection("B", 3, 6),),
))
```

## Endpoints, agent ports and transport

Each AgentEndpoint owns its agent's LocalKnowledge reference, outgoing/incoming
queues, message/evidence duplicate indexes, sharing/refresh caches, processed message
history and received peer-status snapshots. It has no world, engine, transport or
peer-knowledge reference. Its contact roster is public identities, not private peer
state. Broadcast expands into distinct envelopes for each peer, retaining one
original evidence ID.

SimulatedTransport has endpoint references and network configuration solely for
routing. It never reads world hazards, positions, capabilities, tasks or global
beliefs. `communication.py` imports neither simulation nor world modules. The engine
centrally schedules physical network mechanics; agent policies decide what to send.
No shared global knowledge dictionary synchronizes agents.

Agent policies receive AgentView plus an AgentCommunicationPort bound to that
agent and decision tick. The port exposes own immutable knowledge, contact identities,
received peer statuses and validated send/share methods. It does not expose incoming
injection, processing, queue draining, reset, world or global diagnostics. Future
policies cannot use these public methods to deliver messages in the same tick or
change execution commitments. Low-level endpoint receive/process methods are trusted
adapter interfaces used by the transport, not supplied to policies. Python private
attributes are an ordinary API boundary, not isolation against malicious code.

## Wire schemas and timestamps

Existing MessageEnvelope is reused without changing its model. Its fields are ID,
sender/recipient, kind, sent tick, JSON-object text, optional expiry/correlation and
schema version 1. Helpers create unambiguous IDs from [sender, local counter]. All
message IDs must be globally unique in a run; raw custom IDs must honor that rule.
Message helpers create expiry at send + configured TTL.

`messages.py` implements strict typed payload decoding/encoding:

| Kind | Payload |
|---|---|
| OBSERVATION | Complete Observation fields plus provenance path |
| STATE | Sender's AgentStatus: ID, location, battery, availability, update tick |
| COORDINATION | Topic plus an immutable canonical JSON-object data string |

TASK kind remains reserved and is rejected because no task-message schema or
allocation protocol is authorized. Coordination messages are delivered/validated
without choosing an assignment or executing an action. Observation timestamp cannot
follow send time; status ID must equal sender and its update tick cannot be future.
Unknown fields, duplicate JSON keys, invalid confidence/types, nonfinite values,
incorrect sender/path and forwarding loops are rejected. Endpoint observation sends
must match evidence already recorded in that endpoint, including confidence and
provenance; send helpers cannot invent observations from simulator truth.

DeliveryReceipt adds actual delivered_at, strictly later than sent_at, without
mutating the envelope. KnowledgeEntry retains original Observation, actual receipt
tick, immediate forwarded_by, message ID and full path ending at the recipient.
STATE receipts keep both original send and actual receipt times. AgentView.messages
contains this endpoint's successfully processed receipts for the current tick;
AgentView.peer_statuses contains only statuses previously delivered to it. Neither
field is authoritative peer state or a global transport log.

## Tick ordering, expiry and failures

The existing world-event and phase order is preserved:

1. Scheduled world/network-setting events execute by event ID.
2. PERCEPTION produces capability/location-limited local observations.
3. At the beginning of DELIVERY, transport delivers messages whose due tick has
   arrived, sorted by (due tick, message ID).
4. At the beginning of LOCAL_UPDATE, engine records local observations, then each
   endpoint processes its incoming queue in delivery/message-ID order. Knowledge
   is ready before callbacks. A message changes only the recipient's store.
5. In DECISION, per-agent sharing policies run in agent-ID order using restricted
   views/ports, followed by ordinary command controllers. Each gets current local
   evidence and its own received messages/statuses.
6. At the end of SUBMISSION, outgoing envelopes from all endpoints are routed in
   message-ID order. Due tick = send tick + positive configured latency.

Trusted extension systems retain their existing deterministic system-ID order;
transport hooks run at the phase boundaries described above. At least one tick of
latency prevents recursive same-tick delivery. Replies generated after reception
also wait configured latency. Agent commands remain governed by the unchanged
prior-tick execution rule.

Connectivity checks run at both submission and delivery. A disconnected sender or
recipient, or a missing directed link, causes a logged immediate drop. A queued
message encountering an outage at arrival is dropped; it is not held for recovery.
Loss probability is applied once at submission after connectivity checks. Expiry at
or before actual arrival drops the message before incoming processing. Endpoint
processing also checks expiry to protect delayed/batched adapter inputs.

Unknown recipients and sender spoofing are errors before endpoint queueing. Wrong
creation ticks, unknown evidence and changed duplicate IDs are rejected. Exact
message duplicates are suppressed; different messages carrying the same evidence
are processed as duplicate_evidence without adding history or confidence. Invalid
incoming content is logged rejected and cannot update knowledge; valid other
messages continue processing. Invalid trusted-policy submission failures propagate
through the engine's existing FAILED state, requiring reset. No retries, ACKs,
durable delivery, queue-overflow policy or guaranteed reliability are claimed.
Pending messages beyond the simulation horizon remain undelivered and are cleared
by reset; they are not counted as delivered or automatically dropped.

## Network configuration and reproducibility

Defaults: latency 1 tick, loss 0, TTL 6 ticks, all-to-all public contact roster and
fully connected routing. Optional links are explicit directed pairs; None means all
routes, and an empty tuple blocks all. This provides connectivity constraints, not
physical radio-range simulation. Endpoint/policy contacts remain known even when
network routes are unavailable; attempts can drop rather than exposing remote truth.
Disconnections are half-open [start_tick, end_tick) intervals per endpoint. Both
endpoints must be online for a route. Unknown IDs/self/duplicate links and invalid
intervals are rejected. The roster is fixed after communication registration.

Trusted event handlers can call transport.set_connected and set_loss_probability
for dynamic network changes. They do not change knowledge directly. Physical agent
failure/availability and network connectivity remain separate: default policies do
not send from failed/offline agents, but receiving-route availability is governed
by network settings. Fault experiments must explicitly configure outages rather
than infer a peer's private physical state inside routing.

Loss uses the existing named seeded stream `communication-loss`, with draws only
for otherwise routable messages when probability > 0. Stable submission order,
positive latency and endpoint-ID processing produce identical traces for identical
code/config/scenario/seed and deterministic policies. Different traffic/policies
change draw sequences; cross-policy common-random-number fairness and bitwise
cross-Python-version guarantees are not promised.

Reset clears pending/outgoing/incoming messages, trace, counters, duplicate indexes,
peer-status histories, sharing caches, runtime connectivity/loss overrides and
engine-owned local/peer evidence. Static network configuration remains; each
registered per-agent policy.reset() is invoked. The engine reseeds named streams
on demand. Each policy must be a distinct instance with callable/reset methods;
stateful policy caches therefore participate in replay. External UI/files/user
closures outside registered policy state remain outside reset. Generic standalone
transport.reset resets its endpoint/transport state; the engine separately resets
knowledge stores as part of the full simulation lifecycle.

## Initial sharing policy and evidence semantics

ShareLocalChanges is replaceable through engine.enable_communication(policy_factory).
Each agent shares newly observed local evidence when value/confidence changes, and
refreshes with a genuinely newer sample after three ticks. It sends own status every
four ticks. The policy reads its own view/endpoint only and does not scan peer stores.
Unchanged values are not broadcast every render frame or every sample. Shared IDs
are cached per recipient. No synthetic fresh timestamps are manufactured; if sensing
stops, the same old observation is not repeatedly sent as fresh evidence.

Received evidence is not automatically forwarded, avoiding feedback loops. Explicit
forwarding is supported: B can send A's received observation to C with path A→B,
and C records A→B→C while source/timestamp/confidence remain A's. Recipients already
on the path are rejected. Multiple modalities remain separate. Most recent observed
tick wins within (location, subject, modality); equal ticks use evidence ID. Late
old evidence is retained in history but never overwrites newer information. Stale
peer observations remain stale according to original observed time, not arrival.
Delayed processing preserves delivery timestamps and may append old receipt times;
local direct receipt-order validation remains unchanged.

No confidence boost, source-reliability weighting or probabilistic fusion occurs.
Conflicting unique observations retain full history/provenance, and duplicate routes
remain in message-processing history without becoming independent evidence. The
first receipt establishes a unique evidence entry; later identical evidence does
not replace its first provenance/time. Future reconciliation can inspect conflicts
rather than pretend the latest selected value is certain.

## Actual demonstration

The demo replaces the default policy with an explicit, stateless per-agent policy
and uses two-tick latency. It does not fabricate messages or knowledge:

| Tick | Actual effect |
|---|---|
| 0 | A records local fire and sends it to B; B still has no fire evidence |
| 1 | C sends person evidence to D; B still has no fire evidence |
| 2 | A→B arrives; B records original source A, observation tick 0, receipt tick 2 |
| 3 | C→D arrival drops during D's configured [2,4) outage |
| 4 | C sends another person sample; configured loss 1 drops it |
| 5 | Loss restored to 0; A sends its severe-fire sample to B |
| 7 | B receives severe fire with observed tick 5, receipt tick 7 |

D has no specialized local sensor evidence and receives neither dropped observation.
Reset replay matches the entire transport trace and all agent observation histories.
The demo's temporary loss changes are scheduled COMMUNICATION_CHANGED events; text
is never interpreted as executable instructions. Seeded fractional-loss tests also
verify both drops and successes, reproducibility and differences between seeds.

## GUI and next milestone

GUI communication is enabled by default; `--no-communication` retains isolated
Task 5 behavior. M/Messages opens a separate **COMMUNICATION / SIMULATOR DIAGNOSTICS**
inspector with sent/delivered/dropped totals, latency, pending count and the latest
60 relevant activities. Scroll for more rows; knowledge/status remains available.
The knowledge inspector distinguishes Direct from Remote via sender and preserves
original source, modality, confidence and timestamps. Its entries are recipient-local;
all-network diagnostics are never included in agent callback views. Reset clears
both inspectors' communication/evidence timelines. See
[remote knowledge preview](task6_preview.knowledge.png) and
[communication preview](task6_preview.messages.png).

The legacy GUI test fixture explicitly disables communication to keep testing its
original isolated-perception assumptions; new tests enable transport and verify
remote evidence, sent/delivered/dropped display and reset. Native Cocoa window
smoke verification passed. No new packages, UI redesign, physical movement, task
assignment, scoring, auctions, execution or Gazebo integration were added.

Ready for the next bounded decision milestone: priority/eligibility/suitability
interfaces driven by agent-visible evidence and permitted mission inputs, followed
by decentralized allocation. Coordination payloads are an extension point, not an
implemented auction. Real networking, authenticated identities, reliable transport,
advanced radio models and confidence-aware fusion remain explicit future work.


Task 11 registers strict coordination topic recovery.v1 for heartbeat and validated
objective/attempt availability (Task requirements plus execution procedure). It reuses
the same queued transport, timestamps, loss/TTL/duplicate handling and peer ports.
It does not introduce a shared knowledge/task catalogue or TASK-kind wire bypass.
See [recovery contracts](dynamic_reassessment_reallocation.md).
