"""Pygame dashboard. Launch with python -m coopai.gui [scenario.json]."""
import argparse
import os
from pathlib import Path

from .geometry import Rectangle
from .scenario import Scenario, load_scenario
from .simulation import RunState
from .communication import CommunicationConfiguration
from .allocation_messages import AllocationConfiguration
from .execution_models import ExecutionConfiguration, scenario_execution_specs
from .visualization import DashboardSnapshot, PlaybackController, ScreenTransform, capture_dashboard


DEFAULT_SCENARIO = Path(__file__).resolve().parents[2] / "scenarios/four_room_disaster.json"
BACKGROUND = (17, 24, 36)
PANEL = (25, 35, 49)
TEXT = (232, 239, 246)
MUTED = (157, 177, 197)
ACCENT = (85, 214, 188)
AGENT_COLORS = ((80, 189, 255), (247, 186, 87), (201, 141, 255), (91, 220, 177))
TRUTH_LABEL = "SIMULATOR VIEW — NOT AGENT KNOWLEDGE"


class DashboardApp:
    WIDTH, HEIGHT = 1200, 780
    BODY_TOP = 130
    SIDE_X = 710

    def __init__(self, scenario: Scenario, *, with_communication: bool = True,
                 communication_configuration: CommunicationConfiguration = CommunicationConfiguration(),
                 with_allocation: bool | None = None, with_execution: bool | None = None,
                 with_recovery: bool | None = None,
                 execution_configuration: ExecutionConfiguration = ExecutionConfiguration(), mission_case=None):
        # Optional GUI dependency is imported only when the application is created.
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        self.pg = pygame
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT))
        pygame.display.set_caption("CoopAI | Disaster-response simulator")
        self.font = pygame.font.Font(None, 20)
        self.small = pygame.font.Font(None, 18)
        self.title_font = pygame.font.Font(None, 30)
        self.heading = pygame.font.Font(None, 23)
        self.mission_case = mission_case
        if mission_case is not None:
            if mission_case.scenario != scenario:
                raise ValueError("mission metadata and scenario disagree")
            communication_configuration = mission_case.communication
        self.scenario = scenario
        self.engine = scenario.create_engine(with_perception=True, with_priority=True, with_suitability=True)
        if with_communication:
            self.engine.enable_communication(communication_configuration)
        if with_allocation is True and not with_communication:
            raise ValueError("allocation requires communication")
        if with_communication and with_allocation is not False:
            self.engine.enable_allocation(AllocationConfiguration.for_latency(communication_configuration.latency_ticks))
        if with_execution is True and not self.engine.allocation_enabled:
            raise ValueError("execution requires allocation")
        if self.engine.allocation_enabled and with_execution is not False:
            self.engine.enable_execution(scenario.floor_plan, scenario_execution_specs(scenario.task_details), execution_configuration)
        if self.engine.execution_enabled and with_recovery is not False:
            self.engine.enable_recovery()
        self.initial_world = self.engine.world
        self.playback = PlaybackController(self.engine)
        self.transform = ScreenTransform.fit(scenario.floor_plan, Rectangle(24, 160, 662, 520))
        self.scroll = 0
        self.max_scroll = 0
        self.error = ""
        self.show_knowledge = False
        self.show_communication = False
        self.show_priority = False
        self.show_suitability = False
        self.show_allocation = False
        self.show_execution = False
        self.show_recovery = False
        self.show_scenario_menu = self.show_summary = self.show_events = False
        self._summary_dismissed = False
        self.selected_task = None
        self.task_hitboxes = []
        self._mission_cache_key = None
        self._menu_cases = None
        self._menu_rects = {}
        self.selected_agent = sorted(a.identity.agent_id for a in self.engine.world.agents)[0] if self.engine.world.agents else None
        self.running = True
        self._control_changed = False
        self.buttons = {name: pygame.Rect(x, 80, width, 34) for name, x, width in (
            ("play", 24, 82), ("pause", 114, 82), ("step", 204, 100),
            ("reset", 312, 82), ("slower", 426, 36), ("faster", 552, 36))}
        self.buttons["knowledge"] = pygame.Rect(600, 80, 98, 34)
        self.buttons["communication"] = pygame.Rect(708, 80, 90, 34)
        self.buttons["priority"] = pygame.Rect(808, 80, 82, 34)
        self.buttons["suitability"] = pygame.Rect(900, 80, 100, 34)
        self.buttons["allocation"] = pygame.Rect(1010, 80, 98, 34)
        self.buttons["execution"] = pygame.Rect(1116, 80, 76, 34)
        self.buttons["recovery"] = pygame.Rect(600, 18, 112, 32)
        self.buttons["scenarios"] = pygame.Rect(432, 18, 152, 30)
        self.buttons["tasks"] = pygame.Rect(432, 50, 152, 24)
        self.buttons["summary"] = pygame.Rect(600, 50, 112, 24)
        self.agent_colors = {agent.identity.agent_id: AGENT_COLORS[i % len(AGENT_COLORS)]
                             for i, agent in enumerate(sorted(self.engine.world.agents, key=lambda a: a.identity.agent_id))}

    def close(self) -> None:
        self.pg.quit()

    def _text(self, surface, value: str, x: int, y: int, color=TEXT, font=None, width=None) -> None:
        font = font or self.font
        if width is not None and font.size(value)[0] > width:
            while value and font.size(value + "...")[0] > width:
                value = value[:-1]
            value += "..."
        surface.blit(font.render(value, True, color), (x, y))

    def _enabled(self, name: str) -> bool:
        state = self.engine.state
        if name == "play":
            return state in {RunState.READY, RunState.PAUSED}
        if name == "pause":
            return state is RunState.RUNNING
        if name == "step":
            return state in {RunState.READY, RunState.RUNNING, RunState.PAUSED}
        return True

    def _action(self, name: str) -> None:
        if not self._enabled(name):
            return
        self._control_changed = True
        if name in {"knowledge", "communication", "priority", "suitability", "allocation", "execution", "recovery"}:
            self.selected_task = None
            self.show_summary = self.show_events = False
            self._summary_dismissed = True
            if name != "recovery":
                self.show_recovery = False
        if name == "scenarios":
            self.playback.pause()
            self.show_scenario_menu = not self.show_scenario_menu
        elif name == "tasks":
            self._task_cycle()
        elif name in {"summary", "events"}:
            visible = (self.show_summary or self._automatic_summary()) if name == "summary" else self.show_events
            for mode in ("knowledge", "communication", "priority", "suitability", "allocation", "execution", "recovery", "summary", "events"):
                setattr(self, "show_" + mode, False)
            self.selected_task = None
            setattr(self, "show_" + name, not visible)
            self._summary_dismissed = True
            self.scroll = 0
        elif name == "play":
            self.playback.play()
        elif name == "pause":
            self.playback.pause()
        elif name == "step":
            self.playback.single_step()
        elif name == "reset":
            self.playback.reset()
            self._mission_cache_key = None
            self.show_summary = False
            self._summary_dismissed = False
            if self.selected_task not in {t.task_id for t in self.engine.world.tasks}:
                self.selected_task = None
            self.scroll = 0
            self.error = ""
        elif name in {"slower", "faster"}:
            speeds = self.playback.SPEEDS
            index = speeds.index(self.playback.speed) + (-1 if name == "slower" else 1)
            self.playback.set_speed(speeds[max(0, min(len(speeds) - 1, index))])
        elif name == "recovery":
            self.show_recovery = not self.show_recovery
            self.show_execution = self.show_allocation = self.show_suitability = self.show_priority = self.show_knowledge = self.show_communication = False
            self.scroll = 0
        elif name == "execution":
            self.show_execution = not self.show_execution
            self.show_allocation = self.show_suitability = self.show_priority = self.show_knowledge = self.show_communication = False
            self.scroll = 0
        elif name == "allocation":
            self.show_execution = False
            self.show_allocation = not self.show_allocation
            self.show_suitability = self.show_priority = self.show_knowledge = self.show_communication = False
            self.scroll = 0
        elif name == "suitability":
            self.show_execution = False
            self.show_allocation = False
            self.show_suitability = not self.show_suitability
            self.show_priority = self.show_knowledge = self.show_communication = False
            self.scroll = 0
        elif name == "knowledge":
            self.show_execution = False
            self.show_allocation = False
            self.show_suitability = False
            self.show_knowledge = not self.show_knowledge
            self.show_communication = False
            self.show_priority = False
            self.scroll = 0
        elif name == "communication":
            self.show_execution = False
            self.show_allocation = False
            self.show_suitability = False
            self.show_communication = not self.show_communication
            self.show_knowledge = False
            self.show_priority = False
            self.scroll = 0
        elif name == "priority":
            self.show_execution = False
            self.show_allocation = False
            self.show_suitability = False
            self.show_priority = not self.show_priority
            self.show_knowledge = False
            self.show_communication = False
            self.scroll = 0

    def handle_event(self, event) -> None:
        pg = self.pg
        if self.show_scenario_menu and event.type not in {pg.QUIT, pg.WINDOWFOCUSLOST}:
            if event.type == pg.KEYDOWN:
                if event.key in {pg.K_ESCAPE, pg.K_s}:
                    self.show_scenario_menu = False
                elif pg.K_1 <= event.key <= pg.K_5:
                    from .mission_scenarios import SCENARIO_FILES
                    self.switch_scenario(tuple(SCENARIO_FILES)[event.key-pg.K_1])
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                for identity,rect in self._menu_rects.items():
                    if rect.collidepoint(event.pos):
                        self.switch_scenario(identity)
                        break
            return
        if event.type == pg.QUIT:
            self.running = False
        elif event.type == pg.WINDOWFOCUSLOST:
            self._action("pause")
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            for name, rect in self.buttons.items():
                if rect.collidepoint(event.pos):
                    self._action(name)
                    break
            else:
                for identity,rect in self.task_hitboxes:
                    if rect.collidepoint(event.pos):
                        self._select_task(identity)
                        return
                snapshot = capture_dashboard(self.scenario, self.engine)
                for agent in snapshot.agents:
                    x, y = self.transform.point(*agent.marker)
                    if (event.pos[0] - x) ** 2 + (event.pos[1] - y) ** 2 <= 22 ** 2:
                        self.selected_agent = agent.state.identity.agent_id
                        self.show_knowledge = not (self.show_priority or self.show_suitability or self.show_allocation or self.show_execution or self.show_recovery or self.selected_task is not None or self.show_summary or self.show_events)
                        self.show_communication = False
                        self.scroll = 0
                        break
                else:
                    historical = {a.task.task_id for o in self.engine.recovery_snapshot() for a in o.attempts[:-1]}
                    candidates = []
                    counts = {}
                    for task in snapshot.tasks:
                        if task.task.task_id in historical:
                            continue
                        loc = task.task.location_id
                        index = counts.get(loc,0); counts[loc] = index+1
                        x,y = self.transform.point(*task.marker)
                        radius = 25+7*index if self.mission_case else 25
                        delta = abs(((event.pos[0]-x)**2+(event.pos[1]-y)**2)**.5-radius)
                        if delta <= 7:
                            candidates.append((delta,task.task.task_id))
                    if candidates:
                        self._select_task(min(candidates)[1])
        elif event.type == pg.MOUSEWHEEL:
            self.scroll = max(0, min(self.max_scroll, self.scroll - event.y * 36))
        elif event.type == pg.KEYDOWN:
            if event.key == pg.K_ESCAPE:
                self.running = False
            elif event.key == pg.K_SPACE:
                self._action("pause" if self.engine.state is RunState.RUNNING else "play")
            elif event.key == pg.K_s:
                self._action("scenarios")
            elif event.key == pg.K_TAB:
                self._action("tasks")
            elif event.key == pg.K_v:
                self._action("summary")
            elif event.key == pg.K_j:
                self._action("events")
            elif event.key == pg.K_k:
                self._action("knowledge")
            elif event.key == pg.K_m:
                self._action("communication")
            elif event.key == pg.K_h:
                self._action("recovery")
            elif event.key == pg.K_x:
                self._action("execution")
            elif event.key == pg.K_l:
                self._action("allocation")
            elif event.key == pg.K_e:
                self._action("suitability")
            elif event.key == pg.K_p:
                self._action("priority")
            elif pg.K_1 <= event.key <= pg.K_9:
                ids = sorted(a.identity.agent_id for a in self.engine.world.agents)
                index = event.key - pg.K_1
                if index < len(ids):
                    self.selected_agent = ids[index]
                    self.show_knowledge = not (self.show_priority or self.show_suitability or self.show_allocation or self.show_execution or self.show_recovery or self.selected_task is not None or self.show_summary or self.show_events)
                    self.show_communication = False
                    self.scroll = 0
            else:
                action = {pg.K_RIGHT: "step", pg.K_n: "step", pg.K_r: "reset",
                          pg.K_MINUS: "slower", pg.K_EQUALS: "faster", pg.K_PLUS: "faster"}.get(event.key)
                if action:
                    self._action(action)

    def update(self, elapsed_seconds: float) -> int:
        return self.playback.advance(elapsed_seconds)

    def _condition_style(self, snapshot: DashboardSnapshot, location_id: str):
        conditions = [c for c in snapshot.conditions if c.location_id == location_id]
        label = " | ".join(f"{c.subject}: {c.value}" for c in conditions)
        if any(c.value == "severe" for c in conditions):
            return (97, 42, 46), (255, 132, 121), label
        if any(c.subject == "fire" and c.value not in {"clear", "cleared", "absent"} for c in conditions):
            return (68, 40, 43), (255, 161, 130), label
        if any(c.subject == "gas" and c.value not in {"clear", "cleared", "absent"} for c in conditions):
            return (66, 57, 35), (250, 210, 123), label
        if any(c.subject == "person" for c in conditions):
            return (37, 49, 73), (167, 191, 255), label
        return (29, 52, 52), ACCENT, label

    def _floor_plan(self, snapshot: DashboardSnapshot) -> None:
        pg = self.pg
        self._text(self.screen, "FOUR-ROOM RESPONSE ENVIRONMENT", 24, self.BODY_TOP, MUTED, self.small)
        for area in snapshot.areas:
            color, label_color, condition = self._condition_style(snapshot, area.location.location_id)
            rect = pg.Rect(self.transform.rectangle(area.bounds))
            if area.area_type == "corridor":
                color = PANEL
            pg.draw.rect(self.screen, color, rect)
            self._text(self.screen, area.location.label, rect.x + 12, rect.y + 10, TEXT, self.heading, rect.width - 24)
            if condition:
                self._text(self.screen, condition, rect.x + 12, rect.y + 34, label_color, self.small, rect.width - 24)
        for wall in self.scenario.floor_plan.walls():
            pg.draw.line(self.screen, MUTED, *self.transform.segment(wall), 4)
        for passage in self.scenario.floor_plan.passages:
            start, end = self.transform.segment(passage.opening)
            pg.draw.line(self.screen, ACCENT, start, end, 3)
            if passage.target is None:
                self._text(self.screen, "ENTRY", start[0] - 58, start[1], ACCENT, self.small)
        if self.engine.execution_enabled:
            for agent in snapshot.agents:
                state = self.engine.execution_snapshot(agent.state.identity.agent_id)
                if len(state.route) > 1:
                    points = [self.transform.point(p.x, p.y) for p in state.route]
                    pg.draw.lines(self.screen, self.agent_colors[agent.state.identity.agent_id], False, points, 2)
                    for point in points:
                        pg.draw.circle(self.screen, self.agent_colors[agent.state.identity.agent_id], point, 4, 1)
        for agent in snapshot.agents:
            x, y = self.transform.point(*agent.marker)
            color = self.agent_colors[agent.state.identity.agent_id]
            if self.mission_case and agent.state.availability.value in {"offline", "failed"}:
                color = (255,132,121)
            pg.draw.circle(self.screen, BACKGROUND, (x, y), 19)
            pg.draw.circle(self.screen, color, (x, y), 17, 3)
            glyph = self.heading.render(agent.state.identity.agent_id, True, color)
            self.screen.blit(glyph, glyph.get_rect(center=(x, y)))
            self._text(self.screen, agent.battery_text, x - 14, y + 22, color, self.small)
        current_attempts = {o.current_task_id for o in self.engine.recovery_snapshot()}
        historical_attempts = {a.task.task_id for o in self.engine.recovery_snapshot() for a in o.attempts[:-1]}
        labels_per_location = {}
        for task in snapshot.tasks:
            if task.task.task_id in historical_attempts and task.task.task_id not in current_attempts:
                continue
            x, y = self.transform.point(*task.marker)
            color = ACCENT if task.task.status.value == "completed" else (255,132,121) if task.task.status.value == "failed" else (211,222,237)
            if self.engine.execution_enabled:
                index = labels_per_location.get(task.task.location_id,0)
                labels_per_location[task.task.location_id] = index+1
                pg.draw.circle(self.screen, color, (x,y), 25+7*index if self.mission_case else 25, 2)
            else:
                pg.draw.rect(self.screen, color, pg.Rect(x - 5, y - 5, 10, 10), 2)
            label_y = y - 7 + (index * 20 if self.mission_case and self.engine.execution_enabled else 0)
            self._text(self.screen, task.task.task_id, x + (30 if self.engine.execution_enabled else 12), label_y, color, self.small)
        if self.mission_case:
            self._text(self.screen, self.mission_case.display_name + " | S scenarios | Tab tasks | V summary | J events", 24, 735, ACCENT, self.small, 662)
        self._text(self.screen, "Agent circles: real positions   Task rings: destinations   Teal: doors" if self.engine.execution_enabled else "Circles: stationary agents   Squares: tasks   Teal: passages", 24, 710, MUTED, self.small)
        if self.mission_case is None:
            self._text(self.screen, "X: execution/progress   Paths: accepted routes   Completion does not clear hazards" if self.engine.execution_enabled else "Decentralized allocation enabled; no movement or task execution." if self.engine.allocation_enabled else "No movement or task execution; allocation disabled.", 24, 735, MUTED, self.small)

    def _side_panel(self, snapshot: DashboardSnapshot) -> None:
        if self.selected_task is not None:
            self._task_panel(snapshot)
            return
        if self.show_summary or self._automatic_summary():
            self._summary_panel()
            return
        if self.show_events:
            self._events_panel()
            return
        if self.show_recovery:
            self._recovery_panel(snapshot)
            return
        if self.show_execution:
            self._execution_panel(snapshot)
            return
        if self.show_allocation:
            self._allocation_panel(snapshot)
            return
        if self.show_suitability:
            self._suitability_panel(snapshot)
            return
        if self.show_priority:
            self._priority_panel(snapshot)
            return
        if self.show_communication:
            self._communication_panel(snapshot)
            return
        if self.show_knowledge:
            self._knowledge_panel(snapshot)
            return
        if self.mission_case is not None:
            self._overview_panel(snapshot)
            return
        pg = self.pg
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        content_height = 26 + len(snapshot.agents) * 52 + 12 + 26 + len(snapshot.tasks) * 62 + 12 + 26 + max(1, len(snapshot.events)) * 36
        panel = pg.Surface((width, max(height, content_height)))
        panel.fill(PANEL)
        self.max_scroll = max(0, content_height - height)
        self.scroll = min(self.scroll, self.max_scroll)
        y = 4
        self._text(panel, "AGENT STATUS", 12, y, ACCENT, self.heading)
        y += 26
        for agent in snapshot.agents:
            state = agent.state
            color = self.agent_colors[state.identity.agent_id]
            self._text(panel, f"{state.identity.agent_id}  {state.position.location_id}  |  {state.availability.value}", 12, y, color, self.font, width - 86)
            self._text(panel, agent.battery_text, width - 48, y, color)
            pg.draw.rect(panel, (49, 65, 79), (width - 88, y + 21, 74, 5))
            pg.draw.rect(panel, color, (width - 88, y + 21, round(74 * state.battery), 5))
            self._text(panel, f"Sense: {agent.sensing_text}", 12, y + 18, MUTED, self.small, width - 110)
            self._text(panel, f"Ops: {agent.operational_text}", 12, y + 34, MUTED, self.small, width - 24)
            y += 52
        y += 12
        self._text(panel, "TASK STATUS", 12, y, ACCENT, self.heading)
        y += 26
        for task in snapshot.tasks:
            self._text(panel, f"{task.task.task_id}  {task.details.task_type}", 12, y, TEXT, self.font, width - 24)
            self._text(panel, f"{task.task.location_id}  |  Initial urgency {task.initial_urgency:.1f}  |  {task.task.status.value}", 12, y + 17, MUTED, self.small, width - 24)
            self._text(panel, task.requirement_text, 12, y + 33, MUTED, self.small, width - 24)
            self._text(panel, f"Owner: {task.owner_text}", 12, y + 47, MUTED, self.small, width - 24)
            y += 62
        y += 12
        self._text(panel, "EVENT LOG", 12, y, ACCENT, self.heading)
        y += 26
        if not snapshot.events:
            self._text(panel, "No events applied yet.", 12, y, MUTED, self.small)
        for record in snapshot.events:
            self._text(panel, f"Tick {record.tick}  |  {record.event.event_id}  |  {record.event.target_id}", 12, y, TEXT, self.small, width - 24)
            self._text(panel, record.event.description, 12, y + 16, MUTED, self.small, width - 24)
            y += 36
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0, self.scroll, width, height))
        if self.max_scroll:
            self._text(self.screen, "Scroll for more", self.SIDE_X + width - 102, self.HEIGHT - 17, MUTED, self.small)

    def _knowledge_panel(self, snapshot: DashboardSnapshot) -> None:
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        entries = self.engine.knowledge_snapshot(self.selected_agent) if self.selected_agent else ()
        max_age = self.engine.knowledge_max_age(self.selected_agent) if self.selected_agent else 3
        panel = self.pg.Surface((width, max(height, 140 + len(entries) * 78)))
        panel.fill(PANEL)
        self._text(panel, f"AGENT-LOCAL KNOWLEDGE / {self.selected_agent or 'none'}", 12, 8, ACCENT, self.heading)
        self._text(panel, "Select a circle or press 1-4. K returns to status.", 12, 36, MUTED, self.small)
        self._text(panel, "This agent's direct and explicitly received evidence.", 12, 58, MUTED, self.small)
        if not entries:
            self._text(panel, "No observations. Conditions remain UNKNOWN.", 12, 94, TEXT, self.font, width - 24)
            self._text(panel, "Unknown does not mean clear or safe.", 12, 119, MUTED, self.small)
        for i, entry in enumerate(entries):
            obs = entry.observation
            y = 94 + i * 78
            stale = entry.is_stale(snapshot.time.tick, max_age)
            self._text(panel, f"{obs.location_id} | {obs.subject}: {obs.value}", 12, y, TEXT, self.heading, width - 24)
            provenance = f"Remote via {entry.forwarded_by}" if entry.is_remote else "Direct"
            self._text(panel, f"{provenance} | Modality: {obs.sensing_capability} | Source: {obs.source_id}", 12, y + 23, MUTED, self.small, width - 24)
            self._text(panel, f"Confidence: {obs.confidence:.0%} | Observed tick: {obs.observed_at}", 12, y + 42, MUTED, self.small, width - 24)
            self._text(panel, f"Received: {entry.received_at} | {'STALE' if stale else 'fresh'} (max age {max_age} ticks)", 12, y + 59, MUTED, self.small, width - 24)
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0, self.scroll, width, height))

    def _communication_panel(self, snapshot: DashboardSnapshot) -> None:
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        transport = self.engine.communication
        trace = transport.trace if transport else ()
        activities = [r for r in trace if r.action in {"sent", "delivered", "dropped", "rejected", "duplicate_evidence"}]
        rows = activities[-60:]
        panel = self.pg.Surface((width, max(height, 116 + len(rows) * 38)))
        panel.fill(PANEL)
        self._text(panel, "COMMUNICATION / SIMULATOR DIAGNOSTICS", 12, 8, ACCENT, self.font)
        self._text(panel, "M / Messages returns to status. Scroll for activity.", 12, 34, MUTED, self.small)
        counts = {action: sum(r.action == action for r in trace) for action in ("sent", "delivered", "dropped")}
        self._text(panel, f"Sent {counts['sent']} | Delivered {counts['delivered']} | Dropped {counts['dropped']}", 12, 58, TEXT)
        if transport:
            self._text(panel, f"Latency {transport.configuration.latency_ticks} tick(s) | Pending {transport.pending_count}", 12, 80, MUTED, self.small)
        if not rows:
            self._text(panel, "No communication activity yet.", 12, 108, MUTED, self.small)
        for i, record in enumerate(rows):
            envelope = record.envelope
            y = 110 + i * 38
            color = (255, 132, 121) if record.action in {"dropped", "rejected"} else TEXT
            self._text(panel, f"Tick {record.tick} | {record.action} | {envelope.sender_id} -> {envelope.recipient_id}", 12, y, color, self.font, width - 24)
            self._text(panel, f"{envelope.kind.value} {record.reason}", 12, y + 18, MUTED, self.small, width - 24)
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0, self.scroll, width, height))

    def _recovery_panel(self, snapshot: DashboardSnapshot) -> None:
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        lines = [("RECOVERY / SIMULATOR DIAGNOSTICS", ACCENT),
                 ("H returns to status; R still resets. Scroll for history.", MUTED)]
        if not self.engine.recovery_enabled:
            lines.append(("Recovery disabled; requires autonomous execution.", MUTED))
        else:
            from .recovery_metrics import recovery_metrics
            cache_key = id(self.engine), self.engine.time.tick
            if getattr(self, "_recovery_metric_key", None) != cache_key:
                self._recovery_metrics = recovery_metrics(self.engine)
                self._recovery_metric_key = cache_key
            metrics = {m.objective_id: m for m in self._recovery_metrics}
            own_priorities = {r.task_id: r.score for r in self.engine.priority_snapshot(self.selected_agent)}
            for objective in sorted(self.engine.recovery_snapshot(), key=lambda o: (-len(o.attempts), o.objective_id)):
                latest = objective.attempts[-1]
                lines.append((f"{objective.objective_id}: {objective.state}", ACCENT))
                lines.append((f"Current owner {latest.task.owner_id or '-'} | attempt {latest.generation}", TEXT))
                for attempt in objective.attempts:
                    lines.append((f"Attempt {attempt.generation}: {attempt.task.status.value}; owner {attempt.previous_owner or '-'}; progress {attempt.progress_ticks}", MUTED))
                    if attempt.reason:
                        lines.append((attempt.reason, MUTED))
                metric = metrics[objective.objective_id]
                lines.append((f"Failure {metric.failure_tick} | detected {metric.detection_tick} | released {metric.ownership_release_tick}", MUTED))
                lines.append((f"New owner {metric.replacement_agent or '-'} at {metric.replacement_assignment_tick} | completed {metric.completion_tick}", MUTED))
                lines.append((f"Recovery {metric.recovery_duration_ticks} ticks | rounds {metric.allocation_rounds} | messages {metric.messages_sent} | drops {metric.messages_dropped}", MUTED))
                score = own_priorities.get(latest.task.task_id)
                histories = [r.score for batch in self.engine.priority_history(self.selected_agent) for r in batch if r.task_id == latest.task.task_id]
                priority_text = f"{score:.3f}" if score is not None else "unknown"
                range_text = f"{min(histories):.3f} to {max(histories):.3f}" if histories else "unknown"
                lines.append((f"{self.selected_agent} local priority {priority_text}; observed range {range_text}", MUTED))
                rounds = [r for r in self.engine.allocation_snapshot(self.selected_agent) if r.round.task_id == latest.task.task_id]
                if rounds:
                    round = rounds[-1]
                    lines.append((f"Round epoch {round.round.epoch} | coordinator {round.round.coordinator_id} | {round.phase.value}", MUTED))
            lines.append(("RECENT REASSESSMENT / PEER EVENTS", ACCENT))
            for event in [e for e in self.engine.recovery_trace if e.action != "availability announced"][-12:]:
                lines.append((f"t{event.tick} {event.agent_id or '-'} {event.task_id}: {event.action}", MUTED))
            for event in self.engine.communication.trace[-6:]:
                lines.append((f"t{event.tick} {event.action} {event.envelope.sender_id}->{event.envelope.recipient_id} {event.reason}", MUTED))
        panel = self.pg.Surface((width, max(height, len(lines) * 25 + 12)))
        panel.fill(PANEL)
        for i, (line, color) in enumerate(lines):
            self._text(panel, line, 12, 8 + i * 25, color, self.font if i == 0 else self.small, width - 24)
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0, self.scroll, width, height))

    def _execution_panel(self, snapshot: DashboardSnapshot) -> None:
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        panel = self.pg.Surface((width, max(height, 720)))
        panel.fill(PANEL)
        self._text(panel, f"EXECUTION / SIMULATOR DIAGNOSTICS / {self.selected_agent}", 12, 8, ACCENT, self.font, width-24)
        self._text(panel, "X returns to status; 1-4 / circles select agent.", 12, 34, MUTED, self.small)
        if not self.engine.execution_enabled:
            self._text(panel, "Execution disabled; allocation remains independent.", 12, 68, MUTED)
        else:
            state = self.engine.execution_snapshot(self.selected_agent)
            own = next(a.state for a in snapshot.agents if a.state.identity.agent_id == self.selected_agent)
            task = next((t.task for t in snapshot.tasks if t.task.task_id == state.task_id), None)
            self._text(panel, f"Phase {state.phase.value.upper()} | Task {state.task_id or '-'}", 12, 68, ACCENT, self.heading)
            self._text(panel, f"Position {own.position.location_id} ({own.position.x:.2f}, {own.position.y:.2f}) | Battery {own.battery:.1%}", 12, 99, TEXT, self.font, width-24)
            self._text(panel, f"Destination {task.location_id if task else '-'} | Status {task.status.value if task else '-'}", 12, 124, MUTED, self.font)
            self._text(panel, f"Progress {state.progress_ticks}/{state.duration_ticks} valid execution ticks", 12, 153, TEXT, self.font)
            self.pg.draw.rect(panel, (49,65,79), (12,180,width-24,8))
            fraction=state.progress_ticks/state.duration_ticks if state.duration_ticks else 0
            self.pg.draw.rect(panel, ACCENT, (12,180,round((width-24)*fraction),8))
            self._text(panel, "Completion reports observed conditions; hazards remain.", 12, 204, MUTED, self.small, width-24)
            self._text(panel, f"Accepted waypoints {len(state.route)} | Next {state.waypoint_index}", 12, 233, TEXT, self.font)
            y=260
            for point in state.route[:8]:
                self._text(panel, f"{point.location_id}: ({point.x:.2f}, {point.y:.2f})", 12, y, MUTED, self.small)
                y+=20
            if state.outcome:
                self._text(panel, "Outcome: " + state.outcome.reason, 12, y+8, ACCENT if state.outcome.success else (255,132,121), self.small, width-24)
                y+=35
                for observation in state.outcome.findings:
                    self._text(panel, f"{observation.subject}={observation.value} | {observation.sensing_capability} | confidence {observation.confidence:.0%} | observed {observation.observed_at}", 12, y, MUTED, self.small, width-24)
                    y+=20
            self._text(panel, "MOVEMENT / EXECUTION EVENTS", 12, y+12, ACCENT, self.font)
            y+=40
            for event in [e for e in self.engine.execution_trace if e.agent_id==self.selected_agent][-8:]:
                self._text(panel, f"t{event.tick} {event.action}: {event.reason}", 12, y, MUTED, self.small, width-24)
                y+=25
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0,self.scroll,width,height))

    def _allocation_panel(self, snapshot: DashboardSnapshot) -> None:
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        rounds = self.engine.allocation_snapshot(self.selected_agent) if self.engine.allocation_enabled and self.selected_agent else ()
        trace = self.engine.allocation_history(self.selected_agent) if self.engine.allocation_enabled and self.selected_agent else ()
        panel = self.pg.Surface((width, max(height, 240 + len(rounds) * 132 + 28 * min(8, len(trace)))))
        panel.fill(PANEL)
        self._text(panel, "ALLOCATION / SIMULATOR DIAGNOSTICS", 12, 8, ACCENT, self.font, width - 24)
        self._text(panel, "L returns to status; 1-4 select independent agent state.", 12, 34, MUTED, self.small)
        self._text(panel, "Provisional award != protocol accepted != confirmed owner.", 12, 56, TEXT, self.small, width - 24)
        self._text(panel, "AUTHORITATIVE TASKS (not a shared agent knowledge store)", 12, 82, MUTED, self.small, width - 24)
        for i, display in enumerate(snapshot.tasks):
            task = display.task
            self._text(panel, f"{task.task_id}: {task.status.value} | confirmed owner {task.owner_id or 'none'}", 12, 108 + 23 * i, TEXT, self.font, width - 24)
        self._text(panel, f"AGENT-LOCAL ROUNDS / {self.selected_agent}", 12, 212, ACCENT, self.font)
        if not self.engine.allocation_enabled:
            self._text(panel, "Allocation disabled; requires peer communication.", 12, 240, MUTED)
        elif not rounds:
            self._text(panel, "No local rounds yet. Play/step to negotiate.", 12, 240, MUTED)
        for i, state in enumerate(rounds):
            y = 240 + i * 132
            spec = state.round
            self._text(panel, f"{spec.task_id} | epoch {spec.epoch} | coordinator {spec.coordinator_id}", 12, y, TEXT, self.heading, width - 24)
            self._text(panel, f"Phase: {state.phase.value}", 12, y + 24, ACCENT, self.font, width - 24)
            self._text(panel, f"Provisional {state.provisional_owner or '-'} | confirmed {state.confirmed_owner or '-'}", 12, y + 46, MUTED, self.small)
            bids = ", ".join(f"{r.message.sender_id} {r.message.score:.3f}@{r.message.bid_tick}" for r in state.bids)
            self._text(panel, "Received bids: " + (bids or "none"), 12, y + 65, MUTED, self.small, width - 24)
            self._text(panel, f"Deadlines bid {spec.bid_deadline}, accept {spec.accept_deadline}, commit {spec.commit_deadline}", 12, y + 84, MUTED, self.small, width - 24)
            recent = next((e for e in reversed(trace) if e.round_id == spec.round_id and e.action in {"rejected award", "commit rejected", "round expired", "round closed"}), None)
            self._text(panel, state.reason or (recent.detail if recent else "Only actual local/received protocol information shown."), 12, y + 103, MUTED, self.small, width - 24)
        y = 240 + len(rounds) * 132
        for event in trace[-8:]:
            self._text(panel, f"t{event.tick} {event.task_id}: {event.action} {event.detail}", 12, y, MUTED, self.small, width - 24)
            y += 28
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0, self.scroll, width, height))

    def _suitability_panel(self, snapshot: DashboardSnapshot) -> None:
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        known = self.engine.known_tasks(self.selected_agent) if self.selected_agent else ()
        results = {r.task_id: r for r in self.engine.suitability_snapshot(self.selected_agent)} if self.selected_agent else {}
        priorities = {r.task_id: r for r in self.engine.priority_snapshot(self.selected_agent)} if self.selected_agent else {}
        panel = self.pg.Surface((width, max(height, 110 + len(known) * 126)))
        panel.fill(PANEL)
        self._text(panel, f"ELIGIBILITY / SELF-SUITABILITY / {self.selected_agent}", 12, 8, ACCENT, self.font, width - 24)
        self._text(panel, "E returns to status. 1-4 / circles select agent.", 12, 34, MUTED, self.small)
        self._text(panel, "Priority: task importance. Eligibility: hard feasibility.", 12, 56, TEXT, self.small)
        self._text(panel, "Suitability: eligible agent fit; does not itself choose an owner.", 12, 78, MUTED, self.small)
        for i, announcement in enumerate(known):
            task_id = announcement.task.task_id
            result = results.get(task_id)
            y = 108 + i * 126
            self._text(panel, f"{task_id} / {announcement.task_type}", 12, y, TEXT, self.heading)
            if result is None:
                self._text(panel, "Task announced. Play/step to evaluate.", 12, y + 26, MUTED)
                continue
            score = "N/A" if result.score is None else f"{result.score:.3f}"
            priority = priorities.get(task_id)
            self._text(panel, f"Eligible: {result.eligibility.eligible} | Suitability {score} | Tick {result.evaluation_tick}", 12, y + 23, ACCENT, self.font)
            self._text(panel, f"Priority {priority.score:.3f} | Travel {result.eligibility.travel_ticks} ticks" if priority else f"Travel {result.eligibility.travel_ticks} ticks", 12, y + 44, MUTED, self.small)
            detail = " | ".join(f"{f.name} {f.value:.2f} (w {f.normalized_weight:.2f})" for f in result.factors) if result.score is not None else result.eligibility.explanation
            words = detail.split()
            for line in range(3):
                chunk = []
                while words and self.small.size(" ".join((*chunk, words[0])))[0] < width - 24:
                    chunk.append(words.pop(0))
                self._text(panel, " ".join(chunk) + (" ..." if line == 2 and words else ""), 12, y + 65 + line * 16, MUTED, self.small)
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0, self.scroll, width, height))

    def _priority_panel(self, snapshot: DashboardSnapshot) -> None:
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        known = self.engine.known_tasks(self.selected_agent) if self.selected_agent else ()
        results = self.engine.priority_snapshot(self.selected_agent) if self.selected_agent else ()
        by_id = {r.task_id: r for r in results}
        announcements = {a.task.task_id: a for a in known}
        order = [r.task_id for r in results] or sorted(announcements)
        panel = self.pg.Surface((width, max(height, 108 + len(order) * 126)))
        panel.fill(PANEL)
        self._text(panel, f"CALCULATED AGENT-LOCAL PRIORITY / {self.selected_agent or 'none'}", 12, 8, ACCENT, self.font, width - 24)
        self._text(panel, "1-4 / circles select agent. P returns to status.", 12, 34, MUTED, self.small)
        self._text(panel, "Scenario initial urgency != calculated priority (0-1).", 12, 56, TEXT, self.small, width - 24)
        self._text(panel, "U urgency | D deadline | E risk | I importance | Q uncertainty", 12, 78, MUTED, self.small, width - 24)
        for i, task_id in enumerate(order):
            y = 106 + i * 126
            announcement = announcements[task_id]
            result = by_id.get(task_id)
            self._text(panel, f"{task_id} / {announcement.task_type}", 12, y, TEXT, self.heading, width - 24)
            if result is None:
                self._text(panel, "Task announced. Play/step to evaluate.", 12, y + 26, MUTED)
                continue
            self._text(panel, f"Score {result.score:.3f} | Initial urgency {announcement.initial_urgency:.2f} | Eval tick {result.evaluation_tick}", 12, y + 22, ACCENT, self.font, width - 24)
            factors = {f.name: f.value for f in result.factors}
            self._text(panel, " | ".join(f"{label} {factors[name]:.2f}" for label, name in (("U","urgency"),("D","deadline"),("E","environment"),("I","importance"),("Q","uncertainty"))), 12, y + 43, MUTED, self.small, width - 24)
            selected = next((e for e in result.evidence if e.disposition == "selected"), None)
            detail = f"{result.evidence_status}; no reliable mapped evidence" if selected is None else f"{result.evidence_status}; source {selected.entry.observation.source_id}, {selected.entry.observation.sensing_capability}, obs {selected.entry.observation.observed_at}"
            self._text(panel, detail, 12, y + 62, MUTED, self.small, width - 24)
            words = result.explanation.split()
            for line in range(2):
                chunk = []
                while words and self.small.size(" ".join((*chunk, words[0])))[0] < width - 24:
                    chunk.append(words.pop(0))
                text = " ".join(chunk) + (" ..." if line == 1 and words else "")
                self._text(panel, text, 12, y + 82 + line * 16, MUTED, self.small, width - 24)
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0, self.scroll, width, height))

    def draw(self) -> DashboardSnapshot:
        snapshot = capture_dashboard(self.scenario, self.engine)
        self.screen.fill(BACKGROUND)
        self.task_hitboxes = []
        self._text(self.screen, "CoopAI / Disaster response", 24, 19, TEXT, self.title_font)
        self._text(self.screen, TRUTH_LABEL, 24, 49, ACCENT, self.font)
        self._text(self.screen, f"Tick {snapshot.time.tick}/{self.scenario.mission.duration_ticks}  |  {snapshot.time.seconds:.1f}s  |  {snapshot.run_state.value.upper()} | Seed {self.scenario.mission.seed}", 764, 22, TEXT, self.heading)
        self._text(self.screen, "Space play/pause  |  Right step  |  R reset  |  +/- speed", 764, 49, MUTED, self.small)
        for name, rect in self.buttons.items():
            enabled = self._enabled(name)
            self.pg.draw.rect(self.screen, (44, 68, 78) if enabled else (30, 39, 49), rect, border_radius=6)
            label = {"play": "Play", "pause": "Pause", "step": "Step", "reset": "Reset", "slower": "-", "faster": "+", "knowledge": "Status" if self.show_knowledge else "Knowledge", "communication": "Status" if self.show_communication else "Messages", "priority": "Status" if self.show_priority else "Priority", "suitability": "Status" if self.show_suitability else "Eligibility", "allocation": "Status" if self.show_allocation else "Allocation", "execution": "Status" if self.show_execution else "Execute", "recovery": "Status" if self.show_recovery else "H Recovery", "scenarios": "S Scenarios", "tasks": "Tab Tasks", "summary": "V Summary"}[name]
            glyph = self.font.render(label, True, TEXT if enabled else (89, 106, 120))
            self.screen.blit(glyph, glyph.get_rect(center=rect.center))
        self._text(self.screen, f"{self.playback.speed:g}x", 486, 89, ACCENT, self.font)
        self._floor_plan(snapshot)
        self._side_panel(snapshot)
        if self.show_scenario_menu:
            self._draw_scenario_menu()
        if self.error:
            self._text(self.screen, "Simulation stopped: " + self.error, 24, 760, (255, 132, 121), self.small, 662)
        self.pg.display.flip()
        return snapshot

    def switch_scenario(self, scenario_id: str) -> None:
        """Build first, then replace the complete session; no old queues or policies."""
        from .mission_scenarios import get_scenario
        case = get_scenario(scenario_id)
        engine = case.create_engine()
        self.mission_case, self.scenario, self.engine = case, case.scenario, engine
        self.initial_world = engine.world
        self.playback = PlaybackController(engine)
        self.transform = ScreenTransform.fit(case.scenario.floor_plan, Rectangle(24, 160, 662, 520))
        self.agent_colors = {a.identity.agent_id: AGENT_COLORS[i % len(AGENT_COLORS)]
                             for i,a in enumerate(sorted(engine.world.agents,key=lambda a:a.identity.agent_id))}
        self.selected_agent = min(self.agent_colors) if self.agent_colors else None
        self.selected_task = None
        self.show_scenario_menu = self.show_summary = self.show_events = False
        self._summary_dismissed = False
        for mode in ("knowledge", "communication", "priority", "suitability", "allocation", "execution", "recovery"):
            setattr(self, "show_" + mode, False)
        self.scroll = self.max_scroll = 0
        self.error = ""
        self.task_hitboxes = []
        self._mission_cache_key = self._recovery_metric_key = None
        self._control_changed = True

    def _select_task(self, task_id: str) -> None:
        if not any(t.task_id == task_id for t in self.engine.world.tasks):
            raise ValueError("unknown task selection")
        for mode in ("knowledge", "communication", "priority", "suitability", "allocation", "execution", "recovery", "summary", "events"):
            setattr(self, "show_" + mode, False)
        self.selected_task = task_id
        self._summary_dismissed = True
        self.scroll = 0

    def _task_cycle(self) -> None:
        current = {o.current_task_id for o in self.engine.recovery_snapshot()}
        ids = sorted(t.task_id for t in self.engine.world.tasks if not current or t.task_id in current)
        if ids:
            index = (ids.index(self.selected_task) + 1) % len(ids) if self.selected_task in ids else 0
            self._select_task(ids[index])

    def _automatic_summary(self) -> bool:
        return bool(self.mission_case and (self.engine.state is RunState.FINISHED or self._objectives_finished()) and not self._summary_dismissed
                    and not self.selected_task and not self.show_events
                    and not any(getattr(self, "show_" + m) for m in ("knowledge", "communication", "priority", "suitability", "allocation", "execution", "recovery")))

    def _objectives_finished(self) -> bool:
        """Presentation only: future scripted work prevents premature completion labels."""
        from .scenario import TaskDiscovery
        summary,_ = self._mission_diagnostics()
        total = len(self.scenario.initial_world.tasks) + sum(isinstance(e,TaskDiscovery) for e in self.scenario.events)
        remaining = {e.event.event_id for e in self.scenario.events} - {e.event.event_id for e in self.engine.event_trace}
        return total > 0 and summary.completed_objectives == total and not remaining

    def _mission_diagnostics(self):
        from .mission_report import mission_summary, significant_events
        key = id(self.engine), self.engine.time.tick, self.engine.state
        if key != self._mission_cache_key:
            self._mission_summary = mission_summary(self.engine, self.mission_case.display_name if self.mission_case else None)
            self._mission_events = significant_events(self.engine)
            self._mission_cache_key = key
        return self._mission_summary, self._mission_events

    def _report_panel(self, title: str, lines: list[str], task_links=None) -> None:
        """Wrap actual stored explanations; clipped rows remain scrollable/clickable."""
        width = self.WIDTH - self.SIDE_X - 16
        height = self.HEIGHT - self.BODY_TOP - 16
        rows, links = [], []
        y = 38
        for index,line in enumerate(lines):
            start = y
            words, wrapped = line.split(), ""
            for word in words:
                if wrapped and self.small.size(wrapped + " " + word)[0] > width - 26:
                    rows.append((wrapped, y)); y += 19; wrapped = word
                else:
                    wrapped = (wrapped + " " + word).strip()
            rows.append((wrapped, y)); y += 22
            if task_links and index in task_links:
                links.append((task_links[index], start, y - start))
        panel = self.pg.Surface((width, max(height, y + 12)))
        panel.fill(PANEL)
        self._text(panel, title, 12, 8, ACCENT, self.heading, width-24)
        for line, top in rows:
            self._text(panel, line, 12, top, TEXT, self.small, width-24)
        self.max_scroll = max(0, panel.get_height() - height)
        self.scroll = min(self.scroll, self.max_scroll)
        self.screen.blit(panel, (self.SIDE_X, self.BODY_TOP), (0,self.scroll,width,height))
        viewport = self.pg.Rect(self.SIDE_X,self.BODY_TOP,width,height)
        self.task_hitboxes = [(identity,self.pg.Rect(self.SIDE_X+8,self.BODY_TOP+top-self.scroll,width-16,h).clip(viewport))
                             for identity,top,h in links if top+h > self.scroll and top < self.scroll+height]
        if self.max_scroll:
            self._text(self.screen,"Scroll for more",self.SIDE_X+width-100,self.HEIGHT-17,MUTED,self.small)

    def _overview_panel(self, snapshot) -> None:
        summary, events = self._mission_diagnostics()
        lines = [self.mission_case.display_name,
                 f"Objectives completed {summary.completed_objectives}/{summary.total_objectives} | Failed attempts {summary.failed_execution_attempts}",
                 "AGENTS / physical state"]
        for a in snapshot.agents:
            state = a.state
            phase = self.engine.execution_snapshot(state.identity.agent_id).phase.value
            lines.extend((f"{state.identity.agent_id} | {state.position.location_id} | {state.battery:.0%} | {state.availability.value}",
                          f"Task {state.task_id or '-'} | {phase}"))
        lines.append(f"TASKS / priority known to selected agent {self.selected_agent}; click a row")
        priority = {r.task_id:r.score for r in self.engine.priority_snapshot(self.selected_agent)}
        current = {o.current_task_id:o for o in self.engine.recovery_snapshot()}
        links = {}
        for target in snapshot.tasks:
            task = target.task
            if current and task.task_id not in current:
                continue
            score = priority.get(task.task_id)
            text = f"{score:.3f}" if score is not None else "unknown"
            links[len(lines)] = task.task_id
            lines.append(f"{task.task_id} / {target.details.task_type.replace('-', ' ')}")
            links[len(lines)] = task.task_id
            progress = current[task.task_id].attempts[-1].progress_ticks if task.task_id in current else 0
            lines.append(f"{task.status.value} | owner {task.owner_id or '-'} | work {progress}/{target.details.duration_ticks} | priority {text}")
        lines.append("RECENT MISSION EVENTS / J opens history")
        for event in events[-5:]:
            detail = event.detail.split(";",1)[0] if event.category == "priority changed" else event.detail
            lines.append(f"t{event.tick} {event.agent_id or '-'} {event.task_id or ''} {event.category}: {detail}")
        self._report_panel("MISSION OVERVIEW", lines, links)

    def _task_panel(self, snapshot) -> None:
        from .mission_report import decision_explanation
        target = next((t for t in snapshot.tasks if t.task.task_id == self.selected_task),None)
        if target is None:
            self._report_panel("TASK INSPECTION", ["This runtime task is absent after reset; Tab chooses an initial task."])
            return
        task = target.task
        lines = [f"{task.task_id} / {target.details.task_type.replace('-', ' ')}", "Tab cycles tasks; 1-4 select agent; K/P/E/L/X/H keep their inspectors.",
                 f"SIMULATOR: {task.location_id} | {task.status.value} | confirmed owner {task.owner_id or '-'}",
                 target.requirement_text,
                 f"Required battery {task.requirements.minimum_battery:.0%}; deadline {task.deadline}"]
        lines.extend(f"Resource {r.name}: demand {r.amount:g}" for r in task.requirements.resources)
        lines.append(f"SELECTED AGENT {self.selected_agent} / local information")
        lines.extend(decision_explanation(self.engine,self.selected_agent,task.task_id))
        for objective in self.engine.recovery_snapshot():
            if any(a.task.task_id == task.task_id for a in objective.attempts):
                lines.append(f"OBJECTIVE {objective.objective_id}: {objective.state}")
                for attempt in objective.attempts:
                    lines.append(f"Attempt {attempt.generation}: {attempt.task.status.value}, owner {attempt.previous_owner or '-'}, progress {attempt.progress_ticks}, ended {attempt.ended_at}")
                    if attempt.reason:
                        lines.append(attempt.reason)
        self._report_panel("TASK / DECISION EXPLANATION", lines)

    def _summary_panel(self) -> None:
        summary,_ = self._mission_diagnostics()
        terminal = self.engine.state is RunState.FINISHED
        title = "MISSION COMPLETE" if self._objectives_finished() else "MISSION INCOMPLETE" if terminal else "MISSION PROGRESS"
        lines = [summary.scenario_name, f"Simulation {summary.run_state}; processed ticks {summary.ticks}",
                 f"Total objectives {summary.total_objectives} | Completed {summary.completed_objectives} | Incomplete {summary.incomplete_objectives}",
                 f"Pending {summary.pending_objectives} | Permanently failed {summary.permanently_failed_objectives}",
                 f"Failed execution attempts {summary.failed_execution_attempts} | Confirmed reassignments {summary.reassignments}",
                 f"Agents failed/offline {summary.agent_failures} | Last objective completion tick {summary.last_completion_tick}",
                 "Inspections report conditions; hazards have not been mitigated.", "OBJECTIVE OUTCOMES / click to inspect"]
        links = {}
        for objective in summary.objectives:
            links[len(lines)] = objective.current_task_id
            lines.append(f"{objective.objective_id}: {objective.state}; owner {objective.owner_id or '-'}; attempts {objective.attempts}; completed {objective.completion_tick}")
        lines.append("FINAL / CURRENT BATTERY")
        lines.extend(f"{a}: {b:.1%}" for a,b in summary.final_battery)
        if not terminal and self._objectives_finished():
            lines.append("All configured work is complete. Space pauses; the clock can continue to its horizon.")
        lines.append("R resets exact inputs. S opens five scenarios. V toggles summary.")
        self._report_panel(title,lines,links)

    def _events_panel(self) -> None:
        _,events = self._mission_diagnostics()
        lines = ["J returns to overview. Repeated heartbeats/work increments are hidden; M/X/H retain detailed diagnostics."]
        for event in events[-80:]:
            lines.append(f"t{event.tick} {event.category} | {event.agent_id or '-'} {event.task_id or ''}: {event.detail}")
        if not events:
            lines.append("No processed mission events yet.")
        self._report_panel("SIGNIFICANT MISSION EVENTS", lines)

    def _draw_scenario_menu(self) -> None:
        from .mission_scenarios import list_scenarios
        if self._menu_cases is None:
            self._menu_cases = list_scenarios()
        overlay = self.pg.Surface((self.WIDTH,self.HEIGHT),self.pg.SRCALPHA)
        overlay.fill((5,10,18,235))
        self.screen.blit(overlay,(0,0))
        self._text(self.screen,"SELECT A MISSION SCENARIO",100,68,ACCENT,self.title_font)
        self._text(self.screen,"Click a case or press 1-5. S / Escape closes. Switching starts a fresh paused session.",100,106,MUTED,self.font)
        self._menu_rects = {}
        for i,case in enumerate(self._menu_cases):
            rect = self.pg.Rect(92,154+i*112,1016,100)
            self._menu_rects[case.scenario_id] = rect
            self.pg.draw.rect(self.screen,PANEL,rect,border_radius=8)
            self.pg.draw.rect(self.screen,ACCENT if self.mission_case and self.mission_case.scenario_id==case.scenario_id else MUTED,rect,1,border_radius=8)
            self._text(self.screen,f"{i+1}. {case.display_name}" + (" / primary review" if i==0 else ""),108,rect.y+10,TEXT,self.heading)
            self._text(self.screen,case.description,108,rect.y+39,MUTED,self.font,984)
            self._text(self.screen,case.demonstration_objective,108,rect.y+67,ACCENT,self.small,984)

    def scenario_smoke_test(self, screenshot=None) -> None:
        """Actual controls, menu selection, movement, all-case outcome and reset checks."""
        from .mission_report import verify_expected
        from .scenarios_demo import replay_signature
        from .mission_scenarios import SCENARIO_FILES
        assert self.mission_case is not None
        pg = self.pg
        identity = self.mission_case.scenario_id
        self.show_scenario_menu = False
        # Exercise all five actual keyboard selections before restoring this case.
        for index,name in enumerate(SCENARIO_FILES):
            self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_s));self.draw()
            self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_1+index))
            assert self.mission_case.scenario_id==name and self.engine.time.tick==0
            assert self.engine.communication.trace==() and self.engine.commit_trace==()
        self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_s));self.draw()
        rect = self._menu_rects[identity]
        self.handle_event(pg.event.Event(pg.MOUSEBUTTONDOWN,button=1,pos=rect.center))
        assert self.engine.time.tick==0 and self.engine.state is RunState.READY
        initial = self.engine.world
        self.draw()
        if screenshot:
            screenshot.parent.mkdir(parents=True,exist_ok=True)
            pg.image.save(self.screen,str(screenshot.with_name(screenshot.stem+".initial"+screenshot.suffix)))
        self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_SPACE))
        self.update(self.engine.mission.tick_seconds)
        self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_SPACE))
        paused = self.engine.time.tick
        self.update(10)
        assert self.engine.time.tick==paused
        while self.engine.time.tick < self.engine.mission.duration_ticks:
            self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_RIGHT))
            self.draw();pg.event.pump()
            if screenshot and self.engine.time.tick==11:
                pg.image.save(self.screen,str(screenshot.with_name(screenshot.stem+".movement"+screenshot.suffix)))
        summary = verify_expected(self.mission_case,self.engine)
        before = replay_signature(self.engine)
        self.draw()
        if screenshot:
            pg.image.save(self.screen,str(screenshot))
        self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_TAB));self.draw()
        self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_2));self.draw()
        assert self.selected_task and self.selected_agent=="B"
        self.handle_event(pg.event.Event(pg.KEYDOWN,key=pg.K_r))
        assert self.engine.world==initial and self.engine.time.tick==0
        assert not self.engine.commit_trace and not self.engine.recovery_trace and not self.engine.communication.trace
        self.engine.run(self.engine.mission.duration_ticks)
        assert replay_signature(self.engine)==before
        self._action("reset")
        print(f"Scenario GUI smoke passed: driver={pg.display.get_driver()} case={identity} completed={summary.completed_objectives}/{summary.total_objectives} menu_ok=True reset_replay_equal=True")

    def smoke_test(self, screenshot: Path | None = None) -> None:
        """Bounded integration exercise via actual Pygame button/key events."""
        pg = self.pg
        def click(name):
            self.handle_event(pg.event.Event(pg.MOUSEBUTTONDOWN, button=1, pos=self.buttons[name].center))
        self.draw()
        click("play")
        assert self.engine.state is RunState.RUNNING
        self.update(self.engine.time.tick_seconds)
        assert self.engine.time.tick == 1
        click("pause")
        self.update(10)
        assert self.engine.time.tick == 1
        for _ in range(5):
            self.handle_event(pg.event.Event(pg.KEYDOWN, key=pg.K_RIGHT))
            pg.event.pump()
            self.draw()
            if screenshot and self.engine.allocation_enabled and self.engine.time.tick == 4:
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                self.selected_agent = "B"
                self._action("allocation")
                self.draw()
                pg.image.save(self.screen, str(screenshot.with_name(screenshot.stem + ".provisional" + screenshot.suffix)))
                self._action("allocation")
        target_tick = 6
        expected_complete = False
        if self.engine.allocation_enabled:
            network = self.engine.communication.configuration
            budget = AllocationConfiguration.for_latency(network.latency_ticks)
            confirmation_tick = budget.bid_window_ticks + 5 * network.latency_ticks + 1
            target_tick = (self.scenario.mission.duration_ticks if self.engine.execution_enabled else
                           min(self.scenario.mission.duration_ticks, max(6, confirmation_tick)))
            expected_complete = (confirmation_tick <= self.scenario.mission.duration_ticks
                                 and network.loss_probability == 0 and network.links is None
                                 and not network.disconnections and network.ttl_ticks > network.latency_ticks)
            for _ in range(target_tick - self.engine.time.tick):
                self.handle_event(pg.event.Event(pg.KEYDOWN, key=pg.K_RIGHT))
                self.draw()
                if screenshot and self.engine.execution_enabled and self.engine.time.tick == 9:
                    self.selected_agent = "D"
                    self._action("execution")
                    self.draw()
                    pg.image.save(self.screen, str(screenshot.with_name(screenshot.stem + ".movement" + screenshot.suffix)))
                    self._action("execution")
        snapshot = self.draw()
        fire = next(c.value for c in snapshot.conditions if c.subject == "fire")
        battery = next(a.state.battery for a in snapshot.agents if a.state.identity.agent_id == "B")
        assert snapshot.time.tick == target_tick and fire == "severe" and len(snapshot.events) == 2
        assert 0 <= battery <= 0.4 if self.engine.execution_enabled else battery == 0.4
        if self.engine.allocation_enabled:
            active = [t.task.owner_id for t in snapshot.tasks if t.task.status.value in {"assigned", "in_progress"}]
            assert len(set(active)) == len(active)
            if self.engine.execution_enabled:
                assert all(t.task.status.value in {"pending", "assigned", "in_progress", "completed", "failed"} for t in snapshot.tasks)
                if expected_complete and network.latency_ticks == 1 and self.engine._execution_adapter.configuration == ExecutionConfiguration():
                    assert all(t.task.status.value == "completed" for t in snapshot.tasks)
            else:
                assert all(t.task.status.value in {"pending", "assigned"} for t in snapshot.tasks)
                if expected_complete:
                    assert all(t.task.owner_id is not None for t in snapshot.tasks)
        else:
            assert all(t.task.owner_id is None and t.task.status.value == "pending" for t in snapshot.tasks)
        if not self.engine.execution_enabled:
            assert [sum(not e.is_remote for e in self.engine.observation_history(a)) for a in ("A", "B", "C", "D")] == [target_tick, target_tick, 2 * target_tick, 0]
        else:
            assert all(e.observation.source_id == a for a in ("A", "B", "C", "D") for e in self.engine.observation_history(a) if not e.is_remote)
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            pg.image.save(self.screen, str(screenshot))
            self.selected_agent = "D" if self.engine.execution_enabled else "B" if self.engine.communication else "A"
            self.show_knowledge = True
            self.draw()
            knowledge_path = screenshot.with_name(screenshot.stem + ".knowledge" + screenshot.suffix)
            pg.image.save(self.screen, str(knowledge_path))
            self.show_knowledge = False
            if self.engine.communication:
                self.show_communication = True
                self.draw()
                pg.image.save(self.screen, str(screenshot.with_name(screenshot.stem + ".messages" + screenshot.suffix)))
                self.show_communication = False
            self.show_priority = True
            self.draw()
            pg.image.save(self.screen, str(screenshot.with_name(screenshot.stem + ".priority" + screenshot.suffix)))
            self.show_priority = False
            self._action("suitability")
            self.draw()
            pg.image.save(self.screen, str(screenshot.with_name(screenshot.stem + ".eligibility" + screenshot.suffix)))
            self._action("suitability")
            self._action("allocation")
            self.draw()
            pg.image.save(self.screen, str(screenshot.with_name(screenshot.stem + ".allocation" + screenshot.suffix)))
            self._action("allocation")
            self._action("execution")
            self.draw()
            pg.image.save(self.screen, str(screenshot.with_name(screenshot.stem + ".execution" + screenshot.suffix)))
            self._action("execution")
        click("faster")
        assert self.playback.speed == 2
        click("reset")
        self.draw()
        assert self.engine.world == self.initial_world and self.engine.event_trace == ()
        assert self.engine.time.tick == 0 and self.engine.state is RunState.READY
        assert all(self.engine.knowledge_snapshot(a) == () for a in ("A", "B", "C", "D"))
        assert all(self.engine.priority_snapshot(a) == () for a in ("A", "B", "C", "D"))
        assert all(self.engine.suitability_snapshot(a) == () for a in ("A", "B", "C", "D"))
        assert self.engine.commit_trace == ()
        if self.engine.allocation_enabled:
            assert all(self.engine.allocation_snapshot(a) == () for a in ("A", "B", "C", "D"))
        assert self.engine.execution_trace == ()
        if self.engine.execution_enabled:
            assert all(self.engine.execution_snapshot(a).phase.value == "idle" for a in ("A", "B", "C", "D"))
        if self.engine.communication:
            assert self.engine.communication.trace == () and self.engine.communication.pending_count == 0
        print(f"GUI smoke passed: driver={pg.display.get_driver()} tick={snapshot.time.tick} fire={fire} B_battery={battery:.0%} events=2 reset_ok=True")

    def run(self) -> None:
        clock = self.pg.time.Clock()
        while self.running:
            elapsed = clock.tick(60) / 1000
            try:
                self._control_changed = False
                for event in self.pg.event.get():
                    self.handle_event(event)
                if not self.running:
                    break
                # This interval preceded input handling. Do not charge paused time
                # to a newly started run or the previous interval at a new speed.
                self.update(0 if self._control_changed else elapsed)
            except Exception as exc:
                self.error = str(exc)
                self.playback.pause()
            self.draw()


def main() -> None:
    from .mission_scenarios import SCENARIO_FILES, get_scenario
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario_path", nargs="?", type=Path, help="legacy scenario JSON (default with old feature flags)")
    parser.add_argument("--scenario", choices=tuple(SCENARIO_FILES), dest="case_id", help="select a configured autonomous review mission")
    parser.add_argument("--smoke-test", action="store_true", help="exercise controls/menu/rendering/outcome/reset and exit")
    parser.add_argument("--no-recovery", action="store_true", help="retain Task 10 terminal failure behavior")
    parser.add_argument("--recovery-fixture", choices=("failure", "new-task", "disruption"), help="retain Task 11 fixtures")
    parser.add_argument("--no-execution", action="store_true")
    parser.add_argument("--no-allocation", action="store_true")
    parser.add_argument("--no-communication", action="store_true")
    parser.add_argument("--latency-ticks", type=int)
    parser.add_argument("--loss-probability", type=float)
    parser.add_argument("--ttl-ticks", type=int)
    parser.add_argument("--screenshot", type=Path, help="save bounded smoke screenshots")
    args = parser.parse_args()
    if args.screenshot and not args.smoke_test:
        parser.error("--screenshot requires --smoke-test")
    network_options = any(x is not None for x in (args.latency_ticks,args.loss_probability,args.ttl_ticks))
    feature_flags = args.no_recovery or args.no_execution or args.no_allocation or args.no_communication
    if args.case_id and (args.scenario_path or args.recovery_fixture or network_options or feature_flags):
        parser.error("--scenario uses its validated full mission configuration; legacy path/flags and --recovery-fixture are separate modes")
    if args.recovery_fixture and (args.scenario_path or feature_flags or network_options):
        parser.error("Task 11 fixtures require their default communication/allocation/execution configuration")
    case = None
    app = None
    try:
        if args.case_id or not (args.scenario_path or args.recovery_fixture or feature_flags or network_options):
            case = get_scenario(args.case_id or "corridor")
        configuration = CommunicationConfiguration(latency_ticks=args.latency_ticks if args.latency_ticks is not None else 1,
            loss_probability=args.loss_probability if args.loss_probability is not None else 0,
            ttl_ticks=args.ttl_ticks if args.ttl_ticks is not None else 6)
        scenario = case.scenario if case else load_scenario(args.scenario_path or DEFAULT_SCENARIO)
        app = DashboardApp(scenario, with_communication=not args.no_communication,
            communication_configuration=configuration,
            with_allocation=False if args.no_allocation else None,
            with_execution=False if args.no_execution else None,
            with_recovery=False if args.no_recovery else None, mission_case=case)
        if case:
            app.show_scenario_menu = not args.case_id and not args.smoke_test
        if args.recovery_fixture:
            from .recovery_demo import build_demo
            app.scenario, app.engine = build_demo(args.recovery_fixture)
            app.initial_world = app.engine.world
            app.playback = PlaybackController(app.engine)
            app.show_recovery = True
        if args.smoke_test and case:
            app.scenario_smoke_test(args.screenshot)
        elif args.smoke_test and args.recovery_fixture:
            app.engine.run(app.engine.mission.duration_ticks)
            app.draw()
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True,exist_ok=True)
                app.pg.image.save(app.screen, str(args.screenshot))
            before = app.engine.recovery_snapshot()
            assert all(o.state == "completed" for o in before)
            app._action("reset")
            assert app.engine.world == app.initial_world
            print(f"Recovery GUI smoke passed: driver={app.pg.display.get_driver()} fixture={args.recovery_fixture} objectives={len(before)} reset_ok=True")
        elif args.smoke_test:
            app.smoke_test(args.screenshot)
        else:
            app.run()
    except ImportError as exc:
        parser.exit(1, f"GUI dependency unavailable: {exc}. Install with python -m pip install -e '.[gui]'.\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Unable to launch: {exc}\n")
    finally:
        if app is not None:
            app.close()


if __name__ == "__main__":
    main()
