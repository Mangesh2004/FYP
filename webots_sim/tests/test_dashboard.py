import json
import re
import pytest

from fastapi.testclient import TestClient

from dashboard.server import STATIC, create_app


def _tele(name, t, mode, peers, **kw):
    return {"t": t, "name": name, "pose": [0.0, 0.0, 0.0], "state": "INSPECT", "task": "inspect:R1", "mode": mode,
            "goal": None, "path": None, "heat": 0.1, "gas_ppm": 0.0, "capability": "thermal", "findings": [],
            "dets": [], "peers": peers, "blocked": [], "done": [], "msgs_tx": 1, "msgs_rx": 1,
            "missing_by_seq": {}, **kw}


def _write(run, a_t=60.0, b_t=40.0):
    (run / "telemetry_A.json").write_text(json.dumps(
        _tele("A", a_t, "peer_only", {"B": {"pos": [1, 1], "alive": False}}, map_png="AAAA")))
    (run / "telemetry_B.json").write_text(json.dumps(
        _tele("B", b_t, "commander_linked", {"A": {"pos": [0, 0], "alive": True}})))


def test_link_graph_reflects_each_live_agents_view_and_hides_silent_agents(tmp_path):
    _write(tmp_path)
    st = TestClient(create_app(tmp_path)).get("/api/state").json()
    assert st["agents"]["B"]["stale"] and not st["agents"]["A"]["stale"]
    assert sorted((l["from"], l["to"], l["up"]) for l in st["links"]) == [("A", "B", False), ("A", "CMD", False)]
    _write(tmp_path, a_t=60.0, b_t=59.0)
    st = TestClient(create_app(tmp_path)).get("/api/state").json()
    assert ("B", "CMD", True) in [(l["from"], l["to"], l["up"]) for l in st["links"]]


def test_timeline_merges_sources_in_time_order_and_reads_appended_lines(tmp_path):
    _write(tmp_path)
    (tmp_path / "agent_A.jsonl").write_text(json.dumps({"t": 5.0, "ev": "task_start"}) + "\n"
                                            + json.dumps({"t": 6.0, "ev": "heartbeat"}) + "\n")
    (tmp_path / "supervisor_events.jsonl").write_text(json.dumps({"t": 1.0, "ev": "episode_start"}) + "\n"
                                                      + '{"t": 9.0, "ev": "fau')
    client = TestClient(create_app(tmp_path))
    tl = client.get("/api/state").json()["timeline"]
    assert [(e["src"], e["ev"]) for e in tl] == [("SIM", "episode_start"), ("A", "task_start")]
    with (tmp_path / "supervisor_events.jsonl").open("a") as fh:
        fh.write('lt_commander_cut"}\n')
    tl = client.get("/api/state").json()["timeline"]
    assert [e["ev"] for e in tl] == ["episode_start", "task_start", "fault_commander_cut"]


def test_websocket_streams_state_and_sends_unchanged_images_once(tmp_path):
    _write(tmp_path)
    with TestClient(create_app(tmp_path, period=0.01)).websocket_connect("/ws") as ws:
        first, second = json.loads(ws.receive_text()), json.loads(ws.receive_text())
    assert first["agents"]["A"]["map_png"] == "AAAA"
    assert "map_png" not in second["agents"]["A"] and second["agents"]["A"]["state"] == "INSPECT"


def test_dashboard_is_passive():
    app = create_app()
    for r in app.routes:
        assert not (set(getattr(r, "methods", None) or ()) & {"POST", "PUT", "PATCH", "DELETE"}), r.path
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<(button|input|form|select|textarea)\b", html, re.I)
    assert "fetch(" not in html


def test_fire_view_streams_real_sensor_and_verification_fields(tmp_path):
    sensors={'lidar':{'ranges_m':[1.2,None,3.4],'angles_rad':[1,0,-1],
                      'sample_t':12.0,'valid_count':2},'thermal_raw':[.2,.3,.4,.5]}
    verification={'phase':'measure','sample_count':12,'median':.41,'threshold':.3,
                  'close_visual_frames':3,'camera_fresh':True}
    (tmp_path/'telemetry_A.json').write_text(json.dumps(_tele('A',12,'peer_only',{},
        sensors=sensors,verification=verification,camera_t=11.8,detector='Faster R-CNN MobileNetV3')))
    client=TestClient(create_app(tmp_path))
    a=client.get('/api/state').json()['agents']['A']
    assert a['sensors']==sensors
    assert a['verification']==verification and a['camera_t']==11.8
    page=client.get('/fire')
    assert page.status_code==200 and 'lidar' in page.text.lower()


def test_complete_demo_snapshot_is_preferred_over_intermediate_base_telemetry(tmp_path):
    (tmp_path/'telemetry_A.json').write_text(json.dumps(_tele('A',20,'peer_only',{})))
    complete=_tele('A',20,'peer_only',{},sensors={'lidar':{'valid_count':320}})
    (tmp_path/'fire_telemetry_A.json').write_text(json.dumps(complete))
    a=TestClient(create_app(tmp_path)).get('/api/state').json()['agents']['A']
    assert a['sensors']==complete['sensors']


def test_infinite_lidar_diagnostic_cannot_break_browser_json_stream(tmp_path):
    _write(tmp_path)
    (tmp_path/'agent_A.jsonl').write_text(json.dumps({'t':1,'ev':'lidar_diag','range':float('inf')})+'\n')
    client=TestClient(create_app(tmp_path))
    response=client.get('/api/state')
    assert response.status_code==200
    assert response.json()['timeline'][0]['range'] is None
    with client.websocket_connect('/ws') as ws:
        raw=ws.receive_text()
    json.loads(raw,parse_constant=lambda value:pytest.fail('Nonstandard browser JSON: '+value))
