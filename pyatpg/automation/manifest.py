"""Resumable state for a batch run: what's done, what failed, what to retry.

One JSON file per output directory, rewritten atomically after every item so
a crash mid-run loses at most the item in flight, not the whole manifest.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def fingerprint_file(path: Path) -> str:
    """Cheap change-detector: size + mtime, not a content hash.

    Good enough to notice "this circuit file was replaced" (a friend
    re-dropped it with fixes) without reading and hashing every file on
    every scan of a large benchmark directory.
    """
    stat = path.stat()
    return f"{stat.st_size}-{int(stat.st_mtime_ns)}"


@dataclass
class ManifestRecord:
    key: str
    status: str  # "done" | "failed" | "running"
    fingerprint: str
    attempts: int = 0
    #: "permanent" | "transient" | "fatal" | None (None on success)
    classification: str | None = None
    error: str | None = None
    #: Small, JSON-safe summary of the result -- not the full analysis
    #: payload, which lives in its own per-circuit file.
    result: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManifestRecord":
        return cls(**{**data, "key": data.get("key", "")})


class Manifest:
    """Tracks one record per item (circuit path, dataset dir, ...).

    ``failed`` records with classification ``transient`` are eligible for
    retry on the very next call to :meth:`needs_run` -- that's what lets a
    later scheduled invocation pick up where a rate-limited run left off,
    with a fresh retry budget, instead of that item being stuck failed
    forever or the whole suite being re-run from scratch.
    """

    def __init__(self, path: Path):
        self.path = path
        self._records: dict[str, ManifestRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A half-written manifest from a killed process. Starting clean
            # just means this run re-does work a good manifest would have
            # let it skip -- recoverable -- rather than crashing the whole
            # suite over one corrupt file.
            return
        for key, data in raw.get("records", {}).items():
            self._records[key] = ManifestRecord.from_dict({**data, "key": key})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": {k: r.to_dict() for k, r in self._records.items()}}
        # Write to a temp file and replace, so a crash mid-write never
        # leaves a truncated manifest behind.
        fd, tmp_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=".manifest-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get(self, key: str) -> ManifestRecord | None:
        return self._records.get(key)

    def needs_run(self, key: str, fingerprint: str, *, force: bool = False) -> bool:
        """True unless this exact item already finished successfully.

        A changed fingerprint (file edited/replaced) always needs a re-run,
        even if the old content had already succeeded. A permanent failure
        against the *same* fingerprint is not retried automatically -- the
        circuit itself is the problem -- but ``force`` overrides that.
        """
        record = self._records.get(key)
        if force or record is None:
            return True
        if record.fingerprint != fingerprint:
            return True
        if record.status == "done":
            return False
        if record.classification == "permanent":
            return False
        return True  # failed-transient, or left "running" by a crash

    def start(self, key: str, fingerprint: str) -> None:
        self._records[key] = ManifestRecord(
            key=key, status="running", fingerprint=fingerprint, updated_at=_now()
        )
        self.save()

    def record_success(self, key: str, fingerprint: str, attempts: int, result: dict[str, Any]) -> None:
        self._records[key] = ManifestRecord(
            key=key,
            status="done",
            fingerprint=fingerprint,
            attempts=attempts,
            result=result,
            updated_at=_now(),
        )
        self.save()

    def record_failure(
        self, key: str, fingerprint: str, attempts: int, classification: str, error: str
    ) -> None:
        self._records[key] = ManifestRecord(
            key=key,
            status="failed",
            fingerprint=fingerprint,
            attempts=attempts,
            classification=classification,
            error=error,
            updated_at=_now(),
        )
        self.save()

    def all_records(self) -> list[ManifestRecord]:
        return list(self._records.values())


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
