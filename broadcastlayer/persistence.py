"""Durable compliance journal.

The compliance record is the one thing that must survive a mid-broadcast
restart: if the server dies at minute 40 of a live broadcast, the signed
cue history and broadcast metadata cannot be lost, because it is the legal
access record. Live delivery state (who is connected right now) is
ephemeral and rebuilt on reconnect; the *record* is journaled.

Implementation: append-only JSONL per process, fsync'd, replayed on boot.
No external dependency, so it works in a customer VPC or on-prem with
nothing to provision. A production deployment can point BL_JOURNAL_PATH at
durable storage or swap this module for a database-backed journal without
touching the engine.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Iterator, Optional

DEFAULT_PATH = os.environ.get("BL_JOURNAL_PATH", "broadcast-journal.jsonl")


class Journal:
    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_PATH
        self._lock = threading.Lock()

    def append(self, kind: str, data: dict) -> None:
        line = json.dumps({"kind": kind, "data": data}, separators=(",", ":"))
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

    def replay(self) -> Iterator[tuple[str, dict]]:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    yield rec["kind"], rec["data"]
                except (json.JSONDecodeError, KeyError):
                    continue  # skip a torn final line from a hard crash

    def reset(self) -> None:
        with self._lock:
            if os.path.exists(self.path):
                os.remove(self.path)
