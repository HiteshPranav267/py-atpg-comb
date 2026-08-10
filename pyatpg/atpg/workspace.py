"""Isolated, inspectable working directories for Atalanta jobs.

Every run gets its own directory keyed by an opaque job id. Nothing is
deleted once the run finishes: the raw netlist, fault list, generated
vectors, undetected-fault report and captured stdout/stderr all stay on
disk so a result can be re-inspected or downloaded after the fact.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

#: Root under which all job directories live. Overridable for deployments
#: with a writable scratch volume.
JOBS_ROOT_ENV_VAR = "ATALANTA_JOBS_ROOT"

_DEFAULT_JOBS_DIRNAME = "atalanta_jobs"

#: Anything outside this set is stripped from a caller-supplied circuit name
#: before it is used to build a filename.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")

#: Artifacts are keyed by a stable logical name so the API can expose them
#: without leaking absolute paths.
ARTIFACT_BENCH = "bench"
ARTIFACT_FAULT_LIST = "fault_list"
ARTIFACT_TEST_VECTORS = "test_vectors"
ARTIFACT_UNDETECTED_FAULTS = "undetected_faults"
ARTIFACT_LOG = "log"
ARTIFACT_STDERR = "stderr"


def sanitize_circuit_name(raw: str | None, fallback: str = "circuit") -> str:
    """Reduce an uploaded filename to a safe, extension-free stem.

    Guards against path traversal and shell-significant characters: the
    result is used to build filenames passed to a subprocess.
    """
    if not raw:
        return fallback

    stem = Path(str(raw)).name
    for suffix in (".bench", ".verilog", ".v"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    cleaned = _SAFE_NAME_RE.sub("_", stem).strip("._-")
    return cleaned or fallback


def jobs_root() -> Path:
    """Directory containing every job workspace."""
    configured = os.environ.get(JOBS_ROOT_ENV_VAR)
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / _DEFAULT_JOBS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class JobWorkspace:
    """A single Atalanta job's directory.

    A job may hold more than one run (targeted and non-targeted), so each
    run owns its artifact mapping rather than the workspace keeping a single
    registry that the second run would overwrite.
    """

    job_id: str
    directory: Path
    circuit_name: str

    @classmethod
    def create(cls, circuit_name: str | None) -> "JobWorkspace":
        safe_name = sanitize_circuit_name(circuit_name)
        job_id = f"{int(time.time())}-{uuid.uuid4().hex[:12]}"
        directory = jobs_root() / job_id
        directory.mkdir(parents=True, exist_ok=False)
        return cls(job_id=job_id, directory=directory, circuit_name=safe_name)

    @classmethod
    def open_existing(cls, job_id: str) -> "JobWorkspace | None":
        """Reopen a previous job by id, or None if it no longer exists.

        The id is validated rather than trusted: it arrives from a URL.
        """
        if not re.fullmatch(r"[0-9]+-[0-9a-f]{12}", job_id or ""):
            return None
        directory = jobs_root() / job_id
        if not directory.is_dir():
            return None
        return cls(job_id=job_id, directory=directory, circuit_name="")

    def path_for(self, filename: str) -> Path:
        return self.directory / filename

    def write_text(self, filename: str, content: str) -> Path:
        """Write a job input with Unix line endings.

        Atalanta's netlist parser treats a stray carriage return as part of
        the token, so a CRLF file fails with "Gate type ... is not valid".
        Uploads routinely arrive with CRLF, and on Windows Python would
        translate the newlines a second time, so endings are normalised here
        and translation is disabled explicitly.
        """
        path = self.path_for(filename)
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalized)
        return path

    def resolve_artifact(self, filename: str) -> Path | None:
        """Resolve a filename to a path strictly inside this workspace.

        Rejects traversal attempts (``..``, absolute paths, symlinks out).
        """
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            return None
        candidate = (self.directory / filename).resolve()
        try:
            candidate.relative_to(self.directory.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def destroy(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def prune_old_jobs(max_age_seconds: int = 24 * 3600, keep_latest: int = 200) -> int:
    """Delete stale job directories so scratch space doesn't grow unbounded.

    Returns the number of directories removed. Failures are ignored: pruning
    is housekeeping and must never break a live request.
    """
    root = jobs_root()
    try:
        entries = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return 0

    now = time.time()
    removed = 0
    for index, entry in enumerate(entries):
        try:
            too_old = (now - entry.stat().st_mtime) > max_age_seconds
        except OSError:
            continue
        if index >= keep_latest or too_old:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed
