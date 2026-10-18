# Decentralized allocation baseline — Task 9

## Architecture and information boundaries

This is a Contract Net-inspired **baseline**, not a novelty claim, globally optimal
assignment algorithm or consensus protocol. Each `AgentAllocator` owns independent
rounds, received bids, a single reservation, learned ownership and a local trace.
Its `step(AgentView, AgentCommunicationPort)` receives own state, explicitly announced
tasks, own priority/suitability results, own delivered messages and own commit
feedback only. It has no simulator/world/transport/other-allocator reference. Public
contacts contain identities, not peer capabilities or scores. No room-specific
assignment logic exists. An ineligible agent can coordinate a task; coordination
is a temporary communication role and does not imply ability to execute that task.

| Module | Responsibility | Boundary |
|---|---|---|
| `allocation_messages.py` | Frozen round/message/evidence/commit contracts and strict schema | No world or decision state |
| `allocation.py` | Independent agent negotiation, received-bid comparison, reservations | Own view + tick-bound peer send port |
| Existing `communication.py` / `messages.py` | Latency/loss/expiry/routing, strict typed coordination topic | Real queued P2P messages, no allocation side effects |
| `allocation_adapter.py` | Audit proof validation and atomic ownership admission | Trusted world updates; never chooses a candidate |
| Existing `simulation.py` | Invoke owner-local policies, deliver feedback, reset | Does not rank private suitability or determine round winners |
| GUI/demo | Read-only diagnostics and lifecycle controls | Explicitly privileged; never fed to decisions |

Generic scenario engines opt in with `create_engine(with_allocation=True)`; this
installs existing perception, announcements, priority, suitability and communication.
The default GUI enables allocation when communication is enabled. Use
`--no-allocation` to retain Task 8 behavior or `--no-communication` for isolated
sensing. No dependency was added; JSON scenario tasks/capabilities/events were preserved.

## Deterministic coordinator and round identity

Assumptions: benign agents, fixed shared **public** contact roster, common configured
simulation clock and public protocol windows. Contact IDs come from each endpoint's
public peer list, never a query for peer states. For task ID t and epoch e:

```
roster = sorted(public_contact_ids_including_self)
offset = integer(SHA256(t)) % len(roster)
coordinator = roster[(offset + e) % len(roster)]
```

Only that owner-local agent initiates its round; a failed/offline/zero-battery
coordinator cannot initiate/respond. Rotation permits another coordinator next epoch
without reading a global failure detector. A busy agent can coordinate for peers but
cannot bid for a new task. Membership is static; different rosters/configurations
are rejected rather than claiming agreement. No leader election or network partition
consensus is implemented.

At each epoch boundary, a coordinator opens its locally known pending tasks in
**descending own task priority**, stable task ID on ties. It opens all such rounds
at that boundary; no simulator catalogue is consulted. Task IDs/version, canonical
SHA256 task snapshot digest, coordinator, epoch, opening tick and phase deadlines
identify the round. Task digest canonicalizes capability sets and enum values, so
hash/set iteration cannot alter it. Unknown calls never reveal or register a task.
A task discovered between boundaries waits until the next eligible epoch.

## Windows and phase state machine

Default `AllocationConfiguration`: bid window **3**, award/accept window **3**,
commit window **3**, confirmation notification interval **3** ticks. Epoch length
is their sum + 1 = **10** ticks. Epoch 0 opens at 0, closes bids at 3, requires
acceptance before 6 and admission before 9; epoch 1 opens at 10. Deadlines are
half-open: receiving at a deadline is late, even if the message was sent in time.

`AllocationConfiguration.for_latency(L)` publicly configures bid and award windows
as 2L+1, commit window L+2. L is an explicit budget, not inferred private network
state or a promise of delivery. Too-small budgets, short TTL, loss or partitions
can cause timeouts. GUI uses its configured latency budget; manually constructed
engines must supply appropriate public windows. Infinite latency cannot be recovered
within bounded rounds.

| State | Trigger and action | Next state |
|---|---|---|
| BIDDING | Known call received; calculate own current eligibility/suitability; send BID or NO_BID | BIDDING |
| BIDDING, coordinator | Bid deadline; choose from received valid bids only; no bids → CANCEL | AWARDED or CLOSED |
| AWARDED | Winner rechecks own eligibility/capacity; sends ACCEPT and reserves one slot, or REJECT | ACCEPTED at winner; coordinator awaits response |
| AWARDED, coordinator | Matching ACCEPT received before deadline; send COMMIT with evidence references | PROTOCOL_CONFIRMED |
| ACCEPTED, winner | Matching COMMIT received; recheck eligibility; issue auditable commit request | PROTOCOL_CONFIRMED, awaiting own simulator feedback |
| PROTOCOL_CONFIRMED | Own admission feedback accepted; announce ROUND_CLOSED to peers | COMMITTED |
| Any unconfirmed state | Rejection, cancellation or deadline elapsed | CLOSED or EXPIRED; reservation cleared |
| Any matching peer round | Valid owner notification received | COMMITTED local ownership belief |

A sent COMMIT is protocol confirmation, **not** proof that authoritative admission
succeeded. Only accepted own feedback permits sending a successful ROUND_CLOSED.
Confirmed owners repeat closure notifications to recover from a lost notification.
A delayed notification may exceed its original round deadline, but must match the
original task revision or the exact already-learned assignment. A newer requirement
revision/different owner cannot be overwritten by an old closure.

## Messages and bids

The existing `MessageKind.COORDINATION` wraps strict topic `allocation.v1`. Generic
coordination topics continue to work; TASK wire discovery remains outside scope.
Each protocol message includes kind, full bounded round descriptor, sender, issue
tick, optional candidate/score/bid-evaluation tick, immutable reference IDs and
short reason. Strict decoding rejects wrong/missing/extra fields, duplicate JSON
keys, bad enum values, nonfinite/out-of-range scores, sender/timestamp mismatches,
invalid phase timestamps and incorrect reference counts/roles. It preserves the
existing deterministic transport ordering and envelope expiration semantics.

- CALL_FOR_PROPOSALS: coordinator asks known peers for this known task/version.
- BID: eligible agent's own current Task 8 score, evaluation tick and identity.
- NO_BID: ineligible, unavailable or reserved agent abstains with a reason.
- PROVISIONAL_AWARD: coordinator names one winner and references its bid.
- ACCEPT / REJECT: candidate explicitly confirms a bounded reservation or declines.
- COMMIT: coordinator references the exact bid, award and acceptance.
- CANCEL: close a failed/no-bid/timeout round; does not release an assigned task.
- ROUND_CLOSED: confirmed owner announces admission success and commit reference.

BID contains no knowledge store, observations or peer scores. Current `SuitabilityResult`
is reused, including its hard eligibility gate; unavailable/ineligible agents never
send a scored BID. Eligible zero score is valid. No priority multiplication is used:
priority orders local coordination/competing awards; suitability compares candidates
for one task. Agents may bid on several tasks because bids do not reserve capacity.
The first eligible awards processed at the same tick are ordered by the recipient's
own descending task priority then task ID; it accepts only one. Already reserved or
BUSY agents reject competing awards. Eligibility is checked again at acceptance,
commit request and authoritative admission to handle battery/resource/status changes.

The coordinator ranks **only its own bid and peer bids actually received before the
bid deadline** by higher suitability, earlier bid tick, then lexicographically smaller
agent ID. One immutable bid per agent/round is retained. Duplicate calls/bids do not
create new reservations or boost scores. The coordinator's own bid uses an auditable
local action; it can be earlier than peers' bids, which is an explicit tie bias.
Self-award/accept/commit are also auditable local actions, never fake network messages
or direct calls to another agent. Every interaction between different agents uses
the existing P2P router; there is no endpoint-to-endpoint decision shortcut.

## Commit admission and the three ownership levels

1. **Local provisional belief:** round award / reservation; no task owner changed.
2. **Protocol confirmation:** matching bid→award→accept→commit; still awaiting admission.
3. **Authoritative confirmed assignment:** trusted adapter accepts the request and
   atomically changes PENDING→ASSIGNED plus AVAILABLE→BUSY and matching task commitment.
   The winner learns via its own feedback on the next decision tick; peers learn
   only via explicit closure messages. Local views may lag the world.

`CommitRequest` names task, applicant, round, issue tick and four distinct evidence
IDs. The separate `CommitRegistry` checks audited message content, exact reference
lineage, sender/recipient roles, candidate/score consistency, configured coordinator,
phase deadlines, chronology and actual processed peer-message receipts. Sending
an award or acceptance without delivery is insufficient. Local evidence is allowed
only when coordinator=winner and sender=recipient. It verifies the applicant's
current own hard eligibility (including its own evidence restrictions), matching
known/world task snapshot, world task still pending and applicant still free.
It examines only that applicant, not other candidates' private state/scores.

Engine admission occurs after message submission, ordered by stable request ID.
For conflicting valid outcomes, the first admitted outcome changes the world; later
requests fail the pending/free compare-and-set checks. This is **centralized physical
state admission**, not a suitability optimizer or negotiation decision. The adapter
never selects another winner, broadcasts unrelated ownership or fills absent bids.
Requests are idempotent; reset clears the admission ledger. Rejections are own-agent
feedback with a reason, without revealing unrelated authoritative records.

This authoritative atomic single-owner guard is an explicit simulation safety
assumption. It prevents two confirmed simulator owners and incompatible agent
commitments even if local participants disagree. It is not proof of unique
fully distributed ownership without such a guard under arbitrary partitions.
On a real deployment a suitable transactional execution authority/coordination
mechanism would be required; no Gazebo/distributed consensus integration exists.
Benign participants are trusted to report their scores and successful own feedback;
there is no cryptographic authentication or Byzantine tolerance.

## Failure, recovery and liveness

- Lost/late bids: coordinator proceeds with the valid bids it has, or cancels.
- Lost award/acceptance: bounded acceptance timeout; no physical assignment.
- Lost COMMIT: candidate's reservation expires; no physical assignment.
- Candidate eligibility changes: decline/abort before commit; adapter revalidates.
- Coordinator failure: no new protocol output; peers expire, next epoch rotates role.
- Temporary disconnection: router drops unavailable routes; retry after recovery.
- Lost owner notification: owner repeats closure; peers remain honestly unaware
  until delivery. Conflicting retries cannot overwrite authoritative ownership.
- Duplicate/stale/mismatched messages: endpoint deduplication plus per-round checks,
  task versions/digests and evidence references prevent repeated commits.
- No eligible available bidder: task remains pending, with bounded retries.

There are no same-epoch fallback awards, retransmissions of negotiation messages,
failure-detector election, priority aging, permanent unconfirmed reservations or
execution/reassignment logic. A reservation only blocks that agent's capacity; it
never locks a task in the world. Confirmed assignments remain assigned indefinitely
in this milestone. A mission horizon can end before a round expires; reset clears
local reservations and all authoritative assignments, and pause does not advance
the simulation clock. Retry requires more configured simulation time.

Safety does not imply mission completion. If all peer bids at tick 1 are lost, C's
legitimate local routine bid can win at tick 3. Later, C is unavailable for person
search, and no other agent has visual sensing. The tested result leaves person
search pending. This greedy/partial-information weakness is documented rather than
silently repaired with global optimization or reassignment. Under permanent loss/
partition no guarantee of agreement, assignment or optimal utilization is made.
Liveness requires eventual bid/award/accept/commit delivery within configured windows,
a live coordinator in some epoch, stable task awareness and sufficient free eligible
capacity. No global consensus claim is made.

## Actual four-room traces and reproducibility

`.venv/bin/python -m coopai.allocation_demo` uses the unedited scenario. Epoch-0
coordinators are B for fire/gas, D for person search and C for routine inspection.
B's own priority opens fire before gas, even though B cannot perform fire work.

- Tick 0: public announcements already known; CFPs sent and local coordinator bids made.
- Tick 1: peers evaluate themselves and send bids/no-bids. A fire bid 0.970;
  C fire bid 0.727; C person bid 0.925; D routine bid 0.712.
- Tick 2: peer bids arrive at coordinators.
- Tick 3: provisional awards A/fire, B/gas, C/person, D/routine. B's entirely
  local role path is accepted/admitted; gas owner becomes B.
- Tick 4: remote winners accept/reserve; B learns its authoritative gas assignment.
- Tick 5: coordinators receive acceptance and send COMMIT; B's existing battery
  event still changes 0.9→0.4 without forcing any assignment.
- Tick 6: remote COMMITs arrive; adapter admits A/fire, C/person, D/routine.
- Tick 7: winners receive own confirmation and send closure.
- Tick 8: peers learn confirmed ownership; GUI next-tick clock reads 9.

Final owners: task-1 A, task-2 B, task-3 C, task-4 D, all **ASSIGNED**, positions
unchanged. These outcomes are produced by messages and scores, not scenario scripts.
At latency 2 with explicit windows, B commits at 5 and remote winners at 11.
The latency/failure experiments extend only the mission horizon to 40 ticks.

The controlled award-loss experiment drops the three peer awards at tick 3.
Gas stays assigned to B; other tasks remain pending at tick 9. Expiration clears
reservations and epoch-1 coordinators retry at 10. Routine/D commits at 13; fire/A
and person/C commit at 16. The demo prints both `allocation_replay_equal=True`
and `failure_replay_equal=True`. Another test explicitly preserves the peer-bid-loss
starvation limitation above.

Reset restores the original world, event schedule, announcements/settings, RNG,
endpoints and all independent allocator rounds/counters/records/reservations/ownership,
feedback and admission audit. Replay compares complete world/event/message/commit
and per-agent round/event histories. No callbacks with hidden mutable counters are
used for the controlled-loss experiment. The same code/config/runtime/seed repeats;
cross-interpreter floating-point identity is not promised.

## GUI, verification and future execution

Press **L**/Allocation and select **1–4** or an agent circle. The inspector displays
an explicitly privileged authoritative status/owner list and the selected agent's
own rounds, received bids, provisional owner, confirmed local owner, deadlines,
rejection/expiry reasons and recent protocol events. It does not merge private bid
sets or imply that every agent knows global assignments. Scroll for further rounds/
events. The separate P/E panels remain available. Rendering never negotiates or
changes world state. Provisional screenshot:
[allocation at tick 3](task9_preview.provisional.png); confirmed screenshot:
[allocation after tick 8](task9_preview.allocation.png).

Automated tests cover typed bids, phases, coordinator rotation, priority/ties,
message loss/expiry/ordering/duplicates, capacity, current eligibility, stale task
revisions/closures, atomic conflicting admission, isolation, failure/recovery,
replay and GUI controls. See [actual verification](task_9_verification.md).

Next milestone can add abstract movement and task execution using these confirmed
commitments and existing command/feedback contracts. Positions currently remain
stationary; there is no progress, completion, battery consumption model, navigation,
execution failure reassignment, Gazebo, ML or advanced consensus. Future work may
investigate constrained bids/capacity protection, fallback awards, discovery messages,
aging, switching stability and quantitative experimental evaluation of this baseline.


Task 10 now consumes these confirmed commitments through owner-local execution
commands; see [movement/execution](movement_execution.md). The default GUI enables
both stages; use `--no-execution` for this milestone's original GUI behavior. Own
completion/failure feedback and typed P2P execution status notices update task
awareness. Obsolete assigned-owner closure announcements stop for locally terminal
tasks. Allocation remains independent and message-based; execution never selects
another owner or reopens failed tasks. Historical round confirmation is distinct
from current authoritative completed/failed status.


Task 11 retries preserve this protocol unchanged: explicitly recoverable objectives
receive fresh Task IDs, so old rounds/proofs cannot restore failed attempts. New
availability is disseminated through typed P2P messages. Replacement ranking still
occurs only at temporary coordinators using their received bids. See
[reassessment and recovery](dynamic_reassessment_reallocation.md).
