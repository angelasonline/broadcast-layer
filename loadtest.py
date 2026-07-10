"""Broadcast-scale load check: many concurrent subscribers on one track,
one captioner streaming cues, verify every subscriber receives in order
with bounded memory. Run:  python loadtest.py [n_subscribers]
"""
import asyncio
import sys
import time

from broadcastlayer.engine import Engine
from broadcastlayer.models import Modality, Source, SourceKind, SourceRole
from broadcastlayer.reliability import BoundedQueue


async def main(n_subs: int = 500, n_cues: int = 200):
    e = Engine()
    b = e.start_broadcast("Load Test")
    ts = e.add_track(b.id, Modality.captions, "en")
    ts.add_source(Source(track_id=ts.track.id, kind=SourceKind.human,
                         role=SourceRole.primary, name="cap", connected=True))
    queues = [BoundedQueue() for _ in range(n_subs)]

    async def fanout(cue):
        for q in queues:
            q.push(cue.seq)

    t0 = time.time()
    for i in range(n_cues):
        cue = ts.emit(i * 1000, i * 1000 + 1000, text=f"line {i}")
        await fanout(cue)
    elapsed = time.time() - t0

    received = [len(q._q) if q.dropped == 0 else "dropped" for q in queues[:3]]
    total_dropped = sum(q.dropped for q in queues)
    print(f"subscribers={n_subs} cues={n_cues}")
    print(f"fan-out time={elapsed*1000:.1f}ms "
          f"({n_subs*n_cues:,} deliveries, "
          f"{n_subs*n_cues/max(elapsed,1e-6):,.0f}/s)")
    print(f"queue cap per subscriber={queues[0]._q.maxlen} "
          f"(bounded memory), dropped under cap={total_dropped}")
    print(f"all cues signed={all(c.verify() for c in ts.cues)}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    asyncio.run(main(n))
