"""Locating and validating the Atalanta executable.

Atalanta is a C/C++ tool; this module is only concerned with finding a
runnable build of it for the current platform. Resolution is deliberately
strict: a missing or unrunnable executable raises rather than falling back
to something that might silently produce empty logs, which is how a glibc
incompatibility once surfaced as "0% fault coverage" instead of an error.
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Env var allowing a deployment to point at a build outside the repo.
BINARY_ENV_VAR = "ATALANTA_BIN"

_WINDOWS_CANDIDATES = (
    Path("Atalanta") / "data" / "Atalanta.exe",
    Path("Atalanta") / "Atalanta.exe",
)
_POSIX_CANDIDATES = (
    Path("Atalanta") / "atalanta",
    Path("bin") / "atalanta",
)


class AtalantaNotAvailable(RuntimeError):
    """Raised when no usable Atalanta executable can be found."""


@dataclass(frozen=True)
class AtalantaBinary:
    """A resolved, executable Atalanta build."""

    path: Path
    platform_name: str

    @property
    def is_windows(self) -> bool:
        return self.platform_name == "Windows"


def _candidate_paths() -> tuple[Path, ...]:
    system = platform.system()
    relatives = _WINDOWS_CANDIDATES if system == "Windows" else _POSIX_CANDIDATES
    return tuple(PROJECT_ROOT / rel for rel in relatives)


def resolve_atalanta_binary() -> AtalantaBinary:
    """Locate the Atalanta executable for this platform.

    Order of preference: the ATALANTA_BIN environment variable, then the
    known in-repo build locations.

    Raises:
        AtalantaNotAvailable: if nothing runnable is found. The message lists
            every path tried so a failed deployment is diagnosable from the
            error alone.
    """
    system = platform.system()
    tried: list[str] = []

    override = os.environ.get(BINARY_ENV_VAR)
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    candidates.extend(_candidate_paths())

    for candidate in candidates:
        tried.append(str(candidate))
        if not candidate.is_file():
            continue
        if system != "Windows" and not os.access(candidate, os.X_OK):
            # Present but not executable: fix it rather than skipping, since a
            # freshly unpacked build routinely loses the executable bit.
            try:
                candidate.chmod(candidate.stat().st_mode | 0o111)
            except OSError:
                continue
        return AtalantaBinary(path=candidate.resolve(), platform_name=system)

    raise AtalantaNotAvailable(
        "No runnable Atalanta executable found for platform "
        f"{system!r}. Tried: {', '.join(tried)}. "
        f"Build it from source (see build.sh) or set {BINARY_ENV_VAR} to an "
        "existing build."
    )
