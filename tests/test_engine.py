"""Broadcast Layer engine tests: redundancy, handoff, output, receipts."""
from broadcastlayer.engine import Engine
from broadcastlayer.models import Modality, Source, SourceKind, SourceRole


def _bcast_with_track():
    e = Engine()
    b = e.start_broadcast("Evening News", broadcast_delay_ms=6000)
    ts = e.add_track(b.id, Modality.captions, "en", "English captions")
    return e, b, ts


def test_caption_and_sign_are_peer_tracks():
    e = Engine()
    b = e.start_broadcast("Ceremony")
    cap = e.add_track(b.id, Modality.captions, "en", "English captions")
    asl = e.add_track(b.id, Modality.sign, "ase", "ASL")
    assert {t.track.modality for t in e.broadcast_tracks(b.id)} == \
        {Modality.captions, Modality.sign}
    for ts, kw in ((cap, {"text": "Good evening."}),
                   (asl, {"sign_clip_ref": "clip://seg1"})):
        s = Source(track_id=ts.track.id, kind=SourceKind.human,
                   role=SourceRole.primary, name="pro", connected=True)
        ts.add_source(s)
        cue = ts.emit(0, 2000, **kw)
        assert cue.verify()


def test_primary_wins_over_standby_and_ai():
    e, b, ts = _bcast_with_track()
    for role, name in ((SourceRole.ai_backstop, "ai"),
                       (SourceRole.standby, "backup"),
                       (SourceRole.primary, "lead")):
        ts.add_source(Source(track_id=ts.track.id,
                             kind=(SourceKind.ai if role == SourceRole.ai_backstop
                                   else SourceKind.human),
                             role=role, name=name, connected=True))
    assert ts.active_source().name == "lead"


def test_seamless_handoff_when_primary_drops():
    e, b, ts = _bcast_with_track()
    lead = Source(track_id=ts.track.id, kind=SourceKind.human,
                  role=SourceRole.primary, name="lead", connected=True)
    backup = Source(track_id=ts.track.id, kind=SourceKind.human,
                    role=SourceRole.standby, name="backup", connected=True)
    ts.add_source(lead)
    ts.add_source(backup)
    assert ts.active_source().name == "lead"
    ts.emit(0, 1000, text="line one")
    ts.touch(lead.id, connected=False)
    assert ts.active_source().name == "backup"
    cue = ts.emit(1000, 2000, text="line two")
    assert cue.source_role == SourceRole.standby
    assert ts.handoffs == 1


def test_ai_backstop_labeled_when_all_humans_gone():
    e, b, ts = _bcast_with_track()
    human = Source(track_id=ts.track.id, kind=SourceKind.human,
                   role=SourceRole.primary, name="lead", connected=True)
    ai = Source(track_id=ts.track.id, kind=SourceKind.ai,
                role=SourceRole.ai_backstop, name="whisper-live", connected=True)
    ts.add_source(human)
    ts.add_source(ai)
    ts.touch(human.id, connected=False)
    cue = ts.emit(0, 1000, text="auto caption")
    assert cue.source_kind == SourceKind.ai
    assert cue.source_role == SourceRole.ai_backstop


def test_webvtt_and_ttml_output_valid():
    e, b, ts = _bcast_with_track()
    ts.add_source(Source(track_id=ts.track.id, kind=SourceKind.human,
                        role=SourceRole.primary, name="cap", connected=True))
    ts.emit(0, 2000, text="Hello world")
    ts.emit(2000, 4000, text="Second line")
    vtt = ts.to_webvtt()
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.000" in vtt
    assert "Hello world" in vtt
    ttml = ts.to_ttml()
    assert ttml.startswith("<?xml")
    assert 'begin="0.000s"' in ttml and "Second line" in ttml


def test_compliance_report_signed_and_complete():
    e, b, ts = _bcast_with_track()
    ts.add_source(Source(track_id=ts.track.id, kind=SourceKind.human,
                        role=SourceRole.primary, name="cap", connected=True))
    ts.emit(0, 1000, text="one")
    ts.emit(1000, 2000, text="two")
    rep = e.compliance_report(b.id)
    assert rep["total_cues"] == 2
    assert rep["all_cues_signed"] is True
    assert rep["tracks"][0]["human_cues"] == 2
    assert rep["broadcast_delay_ms"] == 6000


def test_tampered_cue_fails_verification():
    e, b, ts = _bcast_with_track()
    ts.add_source(Source(track_id=ts.track.id, kind=SourceKind.human,
                        role=SourceRole.primary, name="cap", connected=True))
    cue = ts.emit(0, 1000, text="original")
    cue.text = "tampered"
    assert cue.verify() is False


def test_no_source_cannot_emit():
    e, b, ts = _bcast_with_track()
    try:
        ts.emit(0, 1000, text="x")
        assert False, "should have raised"
    except RuntimeError as exc:
        assert "no live source" in str(exc)


def test_sign_track_carries_bt2448_presentation():
    from broadcastlayer.models import SignPlacement
    e = Engine()
    b = e.start_broadcast("Ceremony")
    asl = e.add_track(b.id, Modality.sign, "ase", "ASL")
    cap = e.add_track(b.id, Modality.captions, "en", "English captions")
    # sign track gets closed-signing presentation per ITU-R BT.2448
    p = asl.track.sign_presentation
    assert p is not None
    assert p.closed_signing is True            # separate toggleable stream
    assert p.placement == SignPlacement.lower_right
    assert p.user_adjustable is True
    assert "ITU-R BT.2448" in p.standard and "HbbTV" in p.standard
    # caption track has no signer presentation
    assert cap.track.sign_presentation is None


def test_compliance_report_maps_fcc_79_1_quality_standards():
    e = Engine()
    b = e.start_broadcast("News", broadcast_delay_ms=6000)
    ts = e.add_track(b.id, Modality.captions, "en", "English captions")
    ts.add_source(Source(track_id=ts.track.id, kind=SourceKind.human,
                        role=SourceRole.primary, name="cap", connected=True))
    ts.emit(0, 2000, text="Good evening.")
    ts.emit(2000, 4000, text="Our top story.", corrected=True)
    rep = e.compliance_report(b.id)
    assert "FCC 47 CFR 79.1" in rep["standards"]
    q = rep["tracks"][0]["fcc_79_1_quality"]
    # the four named standards are all present
    assert set(q) == {"accuracy", "synchronicity", "completeness", "placement"}
    assert q["accuracy"]["human_produced_cues"] == 2
    assert q["accuracy"]["corrections_applied"] == 1
    assert q["synchronicity"]["broadcast_delay_ms"] == 6000
    assert q["completeness"]["cues"] == 2
