"""What machine actually ran this analysis.

Timing numbers are only meaningful next to the hardware that produced them --
"0.003s" means nothing on its own. This reports the host's CPU model, core
count and OS once per process, so every run's report and export can say what
it was measured on without a fresh syscall per request.
"""
from __future__ import annotations

import platform
from dataclasses import asdict, dataclass
from functools import lru_cache


@dataclass(frozen=True)
class SystemInfo:
    cpu_model: str
    physical_cores: int | None
    logical_cores: int | None
    os: str
    python_version: str

    def to_dict(self) -> dict:
        return asdict(self)


def _cpu_model() -> str:
    """The best CPU model string this platform will give us without a
    third-party dependency.

    ``platform.processor()`` returns the real model string on Windows (from
    the registry) but is frequently empty on Linux, where the model lives in
    ``/proc/cpuinfo`` instead -- so that file is read as a fallback rather
    than reporting "unknown" on the platform most deployments actually run
    on. Neither path raises past this function: a report that can't name the
    CPU should still show every other field rather than fail outright.
    """
    model = platform.processor()
    if model:
        return model
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.machine() or "unknown"


@lru_cache(maxsize=1)
def get_system_info() -> SystemInfo:
    """Host CPU/OS info, computed once per process and cached.

    Core counts use ``os.cpu_count()`` (logical) and fall back to it for
    "physical" too when the environment offers no way to tell them apart
    without installing psutil -- reported as equal rather than guessed.
    """
    import os

    logical = os.cpu_count()
    return SystemInfo(
        cpu_model=_cpu_model(),
        physical_cores=logical,
        logical_cores=logical,
        os=f"{platform.system()} {platform.release()}",
        python_version=platform.python_version(),
    )
