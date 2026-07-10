"""Broadcast Layer domain model.

A broadcast carries one or more access TRACKS. A track is a channel of
access for a language and modality: captions (timed text) or sign
(interpreter video reference). Tracks are peers by design; the Deaf
community's preference is that sign language is never a second-class
afterthought, so the model treats a caption track and an ASL track
identically except for how their cues are produced.

Each track is fed by a SOURCE with a role: primary human, hot-standby
human, or AI backstop. The active source can hand off to another without a
gap in the track. Every emitted CUE carries a signed receipt so a broadcast
can produce a compliance record after the fact.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

SIGNING_KEY = os.environ.get("BL_SIGNING_KEY", "dev-signing-key").encode()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def now_ms() -> int:
    return int(time.time() * 1000)


class Modality(str, Enum):
    captions = "captions"      # timed text
    sign = "sign"             # interpreter video reference
    translated = "translated"  # timed text in a non-source language


class SourceRole(str, Enum):
    primary = "primary"          # the human doing the work
    standby = "standby"          # hot-standby human, ready to take over
    ai_backstop = "ai_backstop"  # automated fallback, clearly labeled


class SourceKind(str, Enum):
    human = "human"
    ai = "ai"


class SignPlacement(str, Enum):
    """Signer window placement. Lower-right is the documented convention in
    ITU-R BT.2448; the standard's preferred model is that these are
    user-adjustable, so this is a default, not a lock."""
    lower_right = "lower_right"
    lower_left = "lower_left"
    right_third = "right_third"     # "wipe" style, ~half screen vertically
    full_side = "full_side"


class SignPresentation(BaseModel):
    """Closed-signing presentation parameters, per ITU-R BT.2448.

    Closed signing means the signer is delivered as a separate, toggleable
    stream rather than burned into the video (open signing). The receiver
    composits it, optionally using an alpha channel for contour blending;
    without alpha the signer appears in a separate window. Size and position
    are defaults here and are intended to be user-adjustable at the receiver.
    """
    closed_signing: bool = True          # separate toggleable stream (preferred)
    alpha_channel: bool = False          # contour blend vs. separate window
    placement: SignPlacement = SignPlacement.lower_right
    height_pct: float = 50.0             # signer height as % of screen height
    user_adjustable: bool = True         # viewer may change size/position
    # EBU TR 065 (HbbTV): typical HbbTV devices cannot overlay a separate
    # signer video, so closed signing is delivered as a combined video
    # (programme + signer) composited at the back end and launched via an
    # HbbTV application, or as a second screen. Captions distribute as
    # EBU-TT-D (the European TTML profile referenced by HbbTV 2.0).
    delivery: str = "combined-video-backend"   # or "second-screen"
    standard: str = "ITU-R BT.2448; EBU TR 065 (HbbTV)"


class Track(BaseModel):
    id: str = Field(default_factory=lambda: _id("trk"))
    broadcast_id: str
    modality: Modality
    language: str = "en"
    label: str = ""            # human-readable, e.g. "English captions"
    sign_presentation: Optional[SignPresentation] = None  # set for sign tracks

    def display(self) -> str:
        return self.label or f"{self.language} {self.modality.value}"


class Source(BaseModel):
    id: str = Field(default_factory=lambda: _id("src"))
    track_id: str
    kind: SourceKind
    role: SourceRole
    name: str = ""            # captioner/interpreter name or model id
    connected: bool = False
    last_seen_ms: int = Field(default_factory=now_ms)


class Cue(BaseModel):
    """A single unit on a track: a caption line, or a sign-segment marker."""
    id: str = Field(default_factory=lambda: _id("cue"))
    track_id: str
    seq: int
    start_ms: int             # media time this cue applies to
    end_ms: int
    text: str = ""           # caption/translated text; empty for sign refs
    sign_clip_ref: Optional[str] = None  # URL/id of interpreter segment
    source_id: str = ""
    source_kind: SourceKind = SourceKind.human
    source_role: SourceRole = SourceRole.primary
    emitted_ms: int = Field(default_factory=now_ms)
    corrected: bool = False   # was this cue edited before air
    signature: str = ""

    def sign(self) -> "Cue":
        body = (f"{self.id}|{self.track_id}|{self.seq}|{self.start_ms}|"
                f"{self.end_ms}|{self.text}|{self.sign_clip_ref}|"
                f"{self.source_kind.value}|{self.source_role.value}|"
                f"{self.emitted_ms}")
        self.signature = hmac.new(SIGNING_KEY, body.encode(),
                                  hashlib.sha256).hexdigest()
        return self

    def verify(self) -> bool:
        want = self.signature
        return hmac.compare_digest(want, self.sign().signature)


class Broadcast(BaseModel):
    id: str = Field(default_factory=lambda: _id("bcast"))
    name: str
    started_ms: int = Field(default_factory=now_ms)
    ended_ms: Optional[int] = None
    broadcast_delay_ms: int = 0  # offset to match the stream's built-in delay
