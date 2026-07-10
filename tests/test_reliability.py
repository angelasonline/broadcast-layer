"""Hardening tests: persistence/restart, reconnect resume, backpressure,
catch-up, rate limiting. These are the failure modes a broadcaster probes."""
import os
import tempfile

from broadcastlayer.engine import Engine
from broadcastlayer.models import Modality, Source, SourceKind, SourceRole
from broadcastlayer.persistence import Journal
from broadcastlayer.reliability import BoundedQueue, RateLimiter, ResumeRegistry


def test_compliance_record_survives_restart():
    path = tempfile.mktemp(suffix=".jsonl")
    try:
        j1 = Journal(path)
        e1 = Engine(journal=j1)
        b = e1.start_broadcast("Live Event", broadcast_delay_ms=6000)
        ts = e1.add_track(b.id, Modality.captions, "en", "English captions")
        ts.add_source(Source(track_id=ts.track.id, kind=SourceKind.human,
                             role=SourceRole.primary, name="Cap", connected=True))
        ts.emit(0, 1000, text="before the crash")
        ts.emit(1000, 2000, text="also before")

        # simulate a hard restart: brand new engine, same journal file
        e2 = Engine(journal=Journal(path))
        assert b.id in e2.broadcasts
        rep = e2.compliance_report(b.id)
        assert rep["total_cues"] == 2
        assert rep["all_cues_signed"] is True      # signatures survive
        assert rep["broadcast_delay_ms"] == 6000
        # seq continues, not restarts, after recovery
        rec_track = next(t for t in e2.broadcast_tracks(b.id))
        rec_track.add_source(Source(track_id=rec_track.track.id,
                                    kind=SourceKind.human, role=SourceRole.primary,
                                    name="Cap2", connected=True))
        cue = rec_track.emit(2000, 3000, text="after restart")
        assert cue.seq == 3
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_torn_final_line_is_skipped_on_replay():
    path = tempfile.mktemp(suffix=".jsonl")
    try:
        j = Journal(path)
        j.append("broadcast", {"id": "bcast_x", "name": "N", "started_ms": 1})
        with open(path, "a") as f:
            f.write('{"kind":"cue","data":{"id":"cue_')  # torn write, no newline
        recovered = list(j.replay())
        assert len(recovered) == 1 and recovered[0][0] == "broadcast"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_reconnect_resumes_same_source():
    reg = ResumeRegistry()
    reg.bind("token-abc", "src_123")
    assert reg.resolve("token-abc") == "src_123"
    reg.release("token-abc")
    assert reg.resolve("token-abc") is None


def test_backpressure_drops_oldest_not_captioner():
    q = BoundedQueue(maxlen=3)
    for i in range(10):        # slow subscriber: 10 cues, capacity 3
        q.push({"seq": i})
    assert q.dropped == 7      # oldest dropped, newest kept, drop counted


def test_catch_up_after_reconnect_is_ordered():
    e = Engine()
    b = e.start_broadcast("X")
    ts = e.add_track(b.id, Modality.captions)
    ts.add_source(Source(track_id=ts.track.id, kind=SourceKind.human,
                        role=SourceRole.primary, name="c", connected=True))
    for i in range(5):
        ts.emit(i*1000, i*1000+1000, text=f"line {i}")
    missed = ts.cues_since(2)   # client last saw seq 2
    assert [c.seq for c in missed] == [3, 4, 5]


def test_rate_limiter_allows_burst_then_throttles():
    rl = RateLimiter(rate_per_sec=0, burst=3)
    assert [rl.allow("k") for _ in range(5)] == [True, True, True, False, False]
    assert rl.allow("other") is True  # per-key isolation
