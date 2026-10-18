"""Actual latency, loss, outage and provenance demo: python -m coopai.communication_demo."""
from .communication import CommunicationConfiguration, Disconnection
from .models import DynamicEvent, EventKind
from .scenario import load_scenario
from .scenario_demo import DEFAULT_SCENARIO


class DemoSharingPolicy:
    def __call__(self, view, endpoint):
        agent, tick = view.own_state.identity.agent_id, view.time.tick
        target = None
        subject = None
        if agent == "A" and tick in (0, 5):
            target, subject = "B", "fire"
        elif agent == "C" and tick in (1, 4):
            target, subject = "D", "person"
        if target:
            entry = next(e for e in view.knowledge if e.observation.subject == subject and not e.is_remote)
            endpoint.share_observation(entry.observation.observation_id, target, tick)

    def reset(self):
        pass


def build_demo():
    scenario = load_scenario(DEFAULT_SCENARIO)
    engine = scenario.create_engine(with_perception=True)
    engine.enable_communication(CommunicationConfiguration(latency_ticks=2, ttl_ticks=8,
                                disconnections=(Disconnection("D", 2, 4),)),
                                policy_factory=lambda agent: DemoSharingPolicy())
    def change_loss(sim, event):
        sim.communication.set_loss_probability(1 if event.event_id == "comm-loss-on" else 0)
    for event_id, tick in (("comm-loss-on", 4), ("comm-loss-off", 5)):
        engine.schedule(DynamicEvent(event_id, tick, EventKind.COMMUNICATION_CHANGED, "network", "Scripted loss setting"), change_loss)
    return engine


def main():
    engine = build_demo()
    engine.start()
    for _ in range(8):
        engine.step()
        entries = [e for e in engine.knowledge_snapshot("B") if e.observation.subject == "fire"]
        text = "; ".join(f"{e.observation.value} source={e.observation.source_id} via={e.forwarded_by} observed={e.observation.observed_at} received={e.received_at}" for e in entries)
        print(f"next_tick={engine.time.tick} B_fire={text or 'UNKNOWN'}")
    for record in engine.communication.trace:
        print(f"tick={record.tick} {record.action} {record.envelope.sender_id}->{record.envelope.recipient_id} {record.envelope.kind.value} {record.reason}")
    history = tuple(engine.observation_history(a) for a in "ABCD")
    trace = engine.communication.trace
    assert any(e.is_remote and e.observation.subject == "fire" for e in history[1])
    assert any(r.action == "dropped" and r.reason == "loss" for r in trace)
    assert any(r.action == "dropped" and r.reason == "disconnected" for r in trace)
    assert engine.knowledge_snapshot("D") == ()
    engine.reset()
    assert engine.communication.trace == () and engine.communication.pending_count == 0
    engine.run(8)
    assert engine.communication.trace == trace and tuple(engine.observation_history(a) for a in "ABCD") == history
    print("replay_equal=True latency_ticks=2 loss_drop=True disconnected_drop=True D_knowledge=UNKNOWN")


if __name__ == "__main__":
    main()
