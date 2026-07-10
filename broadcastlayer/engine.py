"""Track engine: redundancy, handoff, and standards-compliant output.

The core guarantee: a track is never blank while any source can serve it.
Sources have roles (primary human, hot-standby human, AI backstop). The
engine picks the active source by role priority and health, and hands off
seamlessly when the active source drops. Every cue is signed on emission.

Output is standards-compliant timed text (WebVTT and TTML), so any
broadcast player or streaming pipeline can ingest a track without bespoke
integration.
"""
from __future__ import annotations

from typing import Optional

from .models import (Broadcast, Cue, Modality, SignPresentation, Source,
                     SourceKind, SourceRole, Track, now_ms)
from .persistence import Journal

# A source is considered live if seen within this window.
SOURCE_STALE_MS = 4000

# Role priority: lower number wins when multiple sources are healthy.
_ROLE_ORDER = {SourceRole.primary: 0, SourceRole.standby: 1,
               SourceRole.ai_backstop: 2}


class TrackState:
    """Live state for one track: its sources, sequence counter, cues."""

    def __init__(self, track: Track, on_cue=None):
        self.track = track
        self.sources: dict[str, Source] = {}
        self.cues: list[Cue] = []
        self._seq = 0
        self._active_source_id: Optional[str] = None
        self.handoffs = 0
        self._on_cue = on_cue  # journal hook, set by Engine

    def add_source(self, source: Source) -> None:
        self.sources[source.id] = source
        self._recompute_active()

    def touch(self, source_id: str, connected: bool = True) -> None:
        s = self.sources.get(source_id)
        if s:
            s.connected = connected
            s.last_seen_ms = now_ms()
            self._recompute_active()

    def _healthy(self, s: Source) -> bool:
        return s.connected and (now_ms() - s.last_seen_ms) <= SOURCE_STALE_MS

    def active_source(self) -> Optional[Source]:
        return self.sources.get(self._active_source_id or "")

    def _recompute_active(self) -> None:
        healthy = [s for s in self.sources.values() if self._healthy(s)]
        if not healthy:
            self._active_source_id = None
            return
        healthy.sort(key=lambda s: _ROLE_ORDER.get(s.role, 9))
        chosen = healthy[0]
        if chosen.id != self._active_source_id:
            if self._active_source_id is not None:
                self.handoffs += 1
            self._active_source_id = chosen.id

    def emit(self, start_ms: int, end_ms: int, text: str = "",
             sign_clip_ref: Optional[str] = None,
             source_id: Optional[str] = None,
             corrected: bool = False) -> Cue:
        """Emit a signed cue from the active (or named) source."""
        src = self.sources.get(source_id) if source_id else self.active_source()
        if src is None:
            raise RuntimeError("no live source for track; cannot emit cue")
        self._seq += 1
        cue = Cue(track_id=self.track.id, seq=self._seq,
                  start_ms=start_ms, end_ms=end_ms, text=text,
                  sign_clip_ref=sign_clip_ref, source_id=src.id,
                  source_kind=src.kind, source_role=src.role,
                  corrected=corrected).sign()
        self.cues.append(cue)
        if self._on_cue:
            self._on_cue(cue)
        return cue

    # ---- standards-compliant export ----

    @staticmethod
    def _ts_vtt(ms: int) -> str:
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    def to_webvtt(self) -> str:
        lines = ["WEBVTT", ""]
        for c in self.cues:
            if c.text:
                lines.append(str(c.seq))
                lines.append(f"{self._ts_vtt(c.start_ms)} --> "
                             f"{self._ts_vtt(c.end_ms)}")
                lines.append(c.text)
                lines.append("")
        return "\n".join(lines)

    def to_ttml(self) -> str:
        def ts(ms: int) -> str:
            return f"{ms/1000:.3f}s"
        body = "\n".join(
            f'      <p begin="{ts(c.start_ms)}" end="{ts(c.end_ms)}">'
            f'{_xml_escape(c.text)}</p>'
            for c in self.cues if c.text)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="'
            + self.track.language + '">\n  <body>\n    <div>\n'
            + body + '\n    </div>\n  </body>\n</tt>')

    def cues_since(self, seq: int) -> list["Cue"]:
        """Cues after the given seq, for a subscriber catching up on reconnect."""
        return [c for c in self.cues if c.seq > seq]

    def _fcc_quality(self) -> dict:
        """Attestable evidence against the four FCC 47 CFR 79.1(j)(2) caption
        quality standards. These are structural attestations (what the system
        can prove), not subjective scores; accuracy in particular requires
        human/Deaf review, which this records the conditions for rather than
        grading."""
        texts = [c for c in self.cues if c.text]
        gaps = 0
        for a, b in zip(texts, texts[1:]):
            if b.start_ms - a.end_ms > 3000:   # >3s silence between cues
                gaps += 1
        human = sum(1 for c in self.cues if c.source_kind == SourceKind.human)
        return {
            "accuracy": {
                "human_produced_cues": human,
                "ai_backstop_cues": len(self.cues) - human,
                "corrections_applied": sum(1 for c in self.cues if c.corrected),
                "note": "Accuracy is produced by a professional captioner; AI "
                        "backstop cues are labeled and not counted as human "
                        "accuracy. Final accuracy is confirmed by human review."},
            "synchronicity": {
                "broadcast_delay_ms": None,  # filled by report()
                "cue_count": len(texts),
                "note": "Cues carry media timestamps aligned to the broadcast "
                        "delay offset so caption timing matches air."},
            "completeness": {
                "cues": len(texts),
                "coverage_gaps_over_3s": gaps,
                "note": "Cues run for the broadcast; gaps over 3s are flagged."},
            "placement": {
                "note": "Caption placement follows the player safe-area for "
                        "timed text; sign placement follows ITU-R BT.2448.",
                "sign_presentation": (self.track.sign_presentation.model_dump()
                                      if self.track.sign_presentation else None)},
        }

    def coverage(self) -> dict:
        """Compliance view: how much of the broadcast this track covered."""
        human = sum(1 for c in self.cues if c.source_kind == SourceKind.human)
        ai = sum(1 for c in self.cues if c.source_kind == SourceKind.ai)
        return {"track": self.track.display(), "cues": len(self.cues),
                "human_cues": human, "ai_backstop_cues": ai,
                "handoffs": self.handoffs,
                "active_source": (self.active_source().name
                                  if self.active_source() else None),
                "fcc_79_1_quality": self._fcc_quality()}


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class Engine:
    """All live broadcasts and their tracks."""

    def __init__(self, journal: Optional[Journal] = None):
        self.broadcasts: dict[str, Broadcast] = {}
        self.tracks: dict[str, TrackState] = {}
        self.journal = journal
        if self.journal:
            self._recover()

    def _cue_writer(self):
        j = self.journal
        def write(cue):
            if j:
                j.append("cue", cue.model_dump())
        return write

    def _recover(self):
        """Rebuild broadcasts, tracks, and signed cues from the journal.
        Live source connections are intentionally not restored; consoles
        reconnect and the compliance record is what we preserve."""
        for kind, data in self.journal.replay():
            if kind == "broadcast":
                b = Broadcast(**data)
                self.broadcasts[b.id] = b
            elif kind == "track":
                t = Track(**data)
                self.tracks[t.id] = TrackState(t, on_cue=self._cue_writer())
            elif kind == "cue":
                c = Cue(**data)
                ts = self.tracks.get(c.track_id)
                if ts:
                    ts.cues.append(c)
                    ts._seq = max(ts._seq, c.seq)

    def start_broadcast(self, name: str, broadcast_delay_ms: int = 0) -> Broadcast:
        b = Broadcast(name=name, broadcast_delay_ms=broadcast_delay_ms)
        self.broadcasts[b.id] = b
        if self.journal:
            self.journal.append("broadcast", b.model_dump())
        return b

    def add_track(self, broadcast_id: str, modality: Modality,
                  language: str = "en", label: str = "") -> TrackState:
        t = Track(broadcast_id=broadcast_id, modality=modality,
                  language=language, label=label,
                  sign_presentation=(SignPresentation()
                                     if modality == Modality.sign else None))
        ts = TrackState(t, on_cue=self._cue_writer())
        self.tracks[t.id] = ts
        if self.journal:
            self.journal.append("track", t.model_dump())
        return ts

    def broadcast_tracks(self, broadcast_id: str) -> list[TrackState]:
        return [ts for ts in self.tracks.values()
                if ts.track.broadcast_id == broadcast_id]

    @staticmethod
    def _with_delay(cov: dict, delay_ms: int) -> dict:
        cov["fcc_79_1_quality"]["synchronicity"]["broadcast_delay_ms"] = delay_ms
        return cov

    def compliance_report(self, broadcast_id: str) -> dict:
        b = self.broadcasts[broadcast_id]
        tracks = self.broadcast_tracks(broadcast_id)
        return {
            "broadcast": b.name, "broadcast_id": b.id,
            "started_ms": b.started_ms, "ended_ms": b.ended_ms,
            "broadcast_delay_ms": b.broadcast_delay_ms,
            "standards": ["FCC 47 CFR 79.1", "ITU-R BT.2448", "EBU HbbTV accessibility"],
            "tracks": [self._with_delay(ts.coverage(), b.broadcast_delay_ms)
                       for ts in tracks],
            "total_cues": sum(len(ts.cues) for ts in tracks),
            "all_cues_signed": all(c.verify()
                                   for ts in tracks for c in ts.cues),
        }
