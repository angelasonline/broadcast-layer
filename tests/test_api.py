"""API + live cue path tests via TestClient (HTTP and WebSocket)."""
from fastapi.testclient import TestClient
import os, tempfile
os.environ["BL_JOURNAL_PATH"] = tempfile.mktemp(suffix=".jsonl")
from broadcastlayer.api import app, engine


def _fresh():
    engine.broadcasts.clear(); engine.tracks.clear()
    return TestClient(app)


def test_full_control_room_flow_and_exports():
    c = _fresh()
    b = c.post("/v1/broadcasts", json={"name": "News", "broadcast_delay_ms": 6000}).json()
    t = c.post(f"/v1/broadcasts/{b['id']}/tracks",
               json={"modality": "captions", "language": "en", "label": "English captions"}).json()
    tid = t["track_id"]
    s = c.post(f"/v1/tracks/{tid}/sources",
               json={"kind": "human", "role": "primary", "name": "Captioner A"}).json()
    sid = s["source_id"]

    with c.websocket_connect(f"/v1/tracks/{tid}/produce?source_id={sid}") as prod:
        with c.websocket_connect(f"/v1/tracks/{tid}/subscribe") as sub:
            prod.send_json({"start_ms": 0, "end_ms": 2000, "text": "Good evening."})
            got = sub.receive_json()
            assert got["text"] == "Good evening."
            assert got["source_kind"] == "human" and got["source_role"] == "primary"
            # dashboard shows LIVE while the source is connected
            row = c.get("/v1/dashboard").json()["tracks"][0]
            assert row["status"] == "LIVE" and row["active_source"] == "Captioner A"
            sub.close()

    vtt = c.get(f"/v1/tracks/{tid}.vtt")
    assert vtt.status_code == 200 and "Good evening." in vtt.text
    assert vtt.headers["content-type"].startswith("text/vtt")
    ttml = c.get(f"/v1/tracks/{tid}.ttml")
    assert ttml.status_code == 200 and "Good evening." in ttml.text

    rep = c.get(f"/v1/broadcasts/{b['id']}/report").json()
    assert rep["total_cues"] == 1 and rep["all_cues_signed"] is True



def test_dashboard_shows_no_source_before_connect():
    c = _fresh()
    b = c.post("/v1/broadcasts", json={"name": "X"}).json()
    c.post(f"/v1/broadcasts/{b['id']}/tracks", json={"modality": "sign", "label": "ASL"})
    row = c.get("/v1/dashboard").json()["tracks"][0]
    assert row["status"] == "NO SOURCE"


def test_pages_serve():
    c = _fresh()
    for path in ("/", "/caption", "/dashboard", "/player"):
        assert c.get(path).status_code == 200
