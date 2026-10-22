from __future__ import annotations

import datetime as _dt
import json
import math
import re
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..schemas import TraceEvent

# Limits keep one trace line small and make it hard to log free text by accident.
MAX_ITEMS, MAX_STRING, MAX_DEPTH = 50, 300, 6
# Names that are never written, whatever a caller passes (total_tokens etc. are fine).
_SECRET_NAME = re.compile(r"(?i)^(.*_)?(api_?key|key|authorization|secret|password|bearer|access_token|auth_token|credentials?)$")
_warned: set[str] = set()  # one stderr warning per sink per process


def clean_attributes(value: Any, depth: int = 0) -> Any:
    """JSON-safe, size-bounded copy of event attributes; secret-looking names are dropped."""
    if depth > MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): clean_attributes(v, depth + 1) for k, v in value.items() if not _SECRET_NAME.match(str(k))}
    if isinstance(value, (set, frozenset)):
        value = sorted(value, key=str)
    if isinstance(value, (list, tuple)):
        return [clean_attributes(v, depth + 1) for v in list(value)[:MAX_ITEMS]]
    return str(value)[:MAX_STRING]


class Tracer:
    def __init__(self, jsonl_path: str = "") -> None:
        self._sequences: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._jsonl_path = Path(jsonl_path) if jsonl_path else None

    def emit(
        self,
        *,
        request_id: str,
        step: str,
        component: str,
        outcome: str,
        elapsed_ms: float = 0,
        evidence_ids: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceEvent | None:
        """Record one event. Never raises: a tracing problem must not fail a search request."""
        finished = step in ("request_failed", "response_ready")
        try:
            with self._lock:
                self._sequences[request_id] += 1
                sequence = self._sequences[request_id]

            event = TraceEvent(
                request_id=request_id,
                sequence=sequence,
                step=step,
                component=component,
                outcome=outcome,
                elapsed_ms=max(0, elapsed_ms),
                evidence_ids=evidence_ids or [],
            )
            record = event.model_dump(mode="json")
            if attributes:
                record["attributes"] = clean_attributes(attributes)
            line = json.dumps(record, ensure_ascii=False)
            self._write(line)
            return event
        except Exception as exc:
            self._warn("event", exc)
            return None
        finally:
            if finished:
                with self._lock:
                    self._sequences.pop(request_id, None)

    def _write(self, line: str) -> None:
        try:
            try:
                print(line, file=sys.stdout, flush=True)
            except UnicodeEncodeError:
                # A pipe or console with a legacy code page cannot show Arabic: show escapes
                # (the line stays valid JSON) instead of losing the event on the console.
                print(line.encode("ascii", "backslashreplace").decode("ascii"), file=sys.stdout, flush=True)
        except Exception as exc:
            self._warn("stdout", exc)
        if self._jsonl_path is not None:
            try:
                with self._lock:  # one whole line per write, also across worker threads
                    self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._jsonl_path.open("a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
            except Exception as exc:
                self._warn("jsonl", exc)

    @staticmethod
    def _warn(sink: str, exc: Exception) -> None:
        if sink in _warned:
            return
        _warned.add(sink)
        try:
            print(f"trace write failed ({sink}): {type(exc).__name__}; tracing continues without it", file=sys.stderr)
        except Exception:
            pass
