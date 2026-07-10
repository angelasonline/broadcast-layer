"""Broadcast Layer API: hardened control-room service.

HTTP + WebSocket surface for operating access tracks on a live broadcast,
with the reliability properties a broadcast engineer expects: reconnect
resume, subscriber backpressure isolation, ordered catch-up delivery, rate
limiting, structured logging, health and readiness probes, and a durable
compliance journal that survives restart.

Security posture (see deploy/DEPLOY.md): content is not persisted beyond the
compliance record; transport is TLS in production; no viewer PII; the signing
key and SSO integrate at deployment; runs in the customer's cloud, VPC, or
on-prem.
"""
from __future__ import annotations

import json
import logging
import os
import time

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .engine import Engine
from .models import Modality, Source, SourceKind, SourceRole
from .persistence import Journal
from .reliability import BoundedQueue, RateLimiter, ResumeRegistry

logging.basicConfig(
    level=os.environ.get("BL_LOG_LEVEL", "INFO"),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("broadcastlayer")

app = FastAPI(title="Broadcast Layer")
journal = Journal()
engine = Engine(journal=journal)
_ready = {"ok": True}

limiter = RateLimiter(rate_per_sec=50, burst=100)     # control-plane guard
resumes = ResumeRegistry()
# track_id -> {subscriber_id: BoundedQueue}
_subs: dict[str, dict[str, BoundedQueue]] = {}
_started = time.time()


def _page(name: str) -> str:
    from pathlib import Path
    return (Path(__file__).parent.parent / "static" / name).read_text()


@app.middleware("http")
async def _ratelimit(request: Request, call_next):
    if request.url.path.startswith("/v1/") and request.method == "POST":
        key = request.client.host if request.client else "anon"
        if not limiter.allow(key):
            log.warning("rate limited %s", key)
            return JSONResponse({"error": "rate limited"}, status_code=429)
    return await call_next(request)


# ---- health / readiness ----

@app.get("/healthz")
def healthz() -> dict:
    live = sum(1 for ts in engine.tracks.values() if ts.active_source())
    return {"ok": True, "uptime_s": round(time.time() - _started, 1),
            "broadcasts": len(engine.broadcasts), "tracks": len(engine.tracks),
            "tracks_with_live_source": live}


@app.get("/readyz")
def readyz():
    if not _ready["ok"]:
        return JSONResponse({"ready": False}, status_code=503)
    return {"ready": True}


# ---- control-room API ----

class StartBroadcast(BaseModel):
    name: str
    broadcast_delay_ms: int = 0


@app.post("/v1/broadcasts")
def start_broadcast(body: StartBroadcast) -> dict:
    b = engine.start_broadcast(body.name, body.broadcast_delay_ms)
    log.info("broadcast started id=%s", b.id)
    return b.model_dump()


class AddTrack(BaseModel):
    modality: Modality
    language: str = "en"
    label: str = ""


@app.post("/v1/broadcasts/{bid}/tracks")
def add_track(bid: str, body: AddTrack) -> dict:
    ts = engine.add_track(bid, body.modality, body.language, body.label)
    return {"track_id": ts.track.id, "display": ts.track.display()}


class AddSource(BaseModel):
    kind: SourceKind
    role: SourceRole
    name: str = ""
    resume_token: str = ""


@app.post("/v1/tracks/{tid}/sources")
def add_source(tid: str, body: AddSource) -> dict:
    ts = engine.tracks[tid]
    s = Source(track_id=tid, kind=body.kind, role=body.role,
               name=body.name, connected=True)
    ts.add_source(s)
    if body.resume_token:
        resumes.bind(body.resume_token, s.id)
    return {"source_id": s.id,
            "active": ts.active_source().id if ts.active_source() else None}


@app.get("/v1/broadcasts/{bid}/report")
def report(bid: str) -> dict:
    return engine.compliance_report(bid)


@app.get("/v1/tracks/{tid}.vtt")
def track_vtt(tid: str) -> PlainTextResponse:
    return PlainTextResponse(engine.tracks[tid].to_webvtt(), media_type="text/vtt")


@app.get("/v1/tracks/{tid}.ttml")
def track_ttml(tid: str) -> PlainTextResponse:
    return PlainTextResponse(engine.tracks[tid].to_ttml(),
                             media_type="application/ttml+xml")


@app.get("/v1/dashboard")
def dashboard_data() -> dict:
    now = time.time() * 1000
    out = []
    for ts in engine.tracks.values():
        recent = [c for c in ts.cues if now - c.emitted_ms < 60000]
        act = ts.active_source()
        subs = len(_subs.get(ts.track.id, {}))
        drops = sum(q.dropped for q in _subs.get(ts.track.id, {}).values())
        out.append({
            "track": ts.track.display(), "modality": ts.track.modality.value,
            "active_source": act.name if act else None,
            "active_role": act.role.value if act else None,
            "active_kind": act.kind.value if act else None,
            "sources": len(ts.sources),
            "healthy_sources": sum(1 for s in ts.sources.values() if ts._healthy(s)),
            "cues_last_min": len(recent), "total_cues": len(ts.cues),
            "handoffs": ts.handoffs, "subscribers": subs,
            "dropped_frames": drops,
            "status": "LIVE" if act else "NO SOURCE"})
    return {"tracks": out}


# ---- live cue path with reconnect resume ----

async def _fanout(tid: str, payload: dict) -> None:
    for q in list(_subs.get(tid, {}).values()):
        q.push(payload)


@app.websocket("/v1/tracks/{tid}/produce")
async def produce(ws: WebSocket, tid: str, source_id: str = "",
                  resume_token: str = ""):
    """Captioner/interpreter feed. If source_id is empty but a resume_token
    is known, resume the existing source (idempotent reconnect)."""
    await ws.accept()
    ts = engine.tracks.get(tid)
    if ts is None:
        await ws.close(code=4004)
        return
    if not source_id and resume_token:
        source_id = resumes.resolve(resume_token) or ""
    if source_id not in ts.sources:
        await ws.close(code=4004)
        return
    ts.touch(source_id, connected=True)
    log.info("produce connected track=%s source=%s", tid, source_id)
    try:
        while True:
            raw = await ws.receive_text()
            if raw == "hb":
                ts.touch(source_id, connected=True)
                continue
            msg = json.loads(raw)
            cue = ts.emit(msg["start_ms"], msg["end_ms"],
                          text=msg.get("text", ""),
                          sign_clip_ref=msg.get("sign_clip_ref"),
                          source_id=source_id,
                          corrected=msg.get("corrected", False))
            await _fanout(tid, {
                "seq": cue.seq, "start_ms": cue.start_ms, "end_ms": cue.end_ms,
                "text": cue.text, "sign_clip_ref": cue.sign_clip_ref,
                "source_kind": cue.source_kind.value,
                "source_role": cue.source_role.value, "corrected": cue.corrected})
    except WebSocketDisconnect:
        ts.touch(source_id, connected=False)  # standby takes over; resume on return
        log.info("produce disconnected track=%s source=%s", tid, source_id)


@app.websocket("/v1/tracks/{tid}/subscribe")
async def subscribe(ws: WebSocket, tid: str, since_seq: int = 0):
    """Players/monitors. Reconnecting clients pass since_seq to catch up on
    missed cues in order; a bounded queue isolates slow clients."""
    await ws.accept()
    ts = engine.tracks.get(tid)
    if ts is None:
        await ws.close(code=4004)
        return
    import uuid
    sid = uuid.uuid4().hex
    q = BoundedQueue()
    _subs.setdefault(tid, {})[sid] = q
    # ordered catch-up for reconnects
    for c in ts.cues_since(since_seq):
        q.push({"seq": c.seq, "start_ms": c.start_ms, "end_ms": c.end_ms,
                "text": c.text, "sign_clip_ref": c.sign_clip_ref,
                "source_kind": c.source_kind.value,
                "source_role": c.source_role.value, "corrected": c.corrected})

    import asyncio
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.pull(), timeout=0.05)
                await ws.send_text(json.dumps(item))
                continue
            except asyncio.TimeoutError:
                pass
            # opportunistically drain any client heartbeat without blocking
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=0.001)
            except (asyncio.TimeoutError, KeyError):
                pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _subs.get(tid, {}).pop(sid, None)


# ---- pages ----

@app.get("/", response_class=HTMLResponse)
def home() -> str: return _page("index.html")


@app.get("/caption", response_class=HTMLResponse)
def caption_console() -> str: return _page("caption.html")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str: return _page("dashboard.html")


@app.get("/player", response_class=HTMLResponse)
def player_demo() -> str: return _page("player.html")
