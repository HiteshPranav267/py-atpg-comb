import os

import pytest

# Never write test runs into the real run database (DATABASE_URL in .env).
os.environ["PYATPG_RECORD_RUNS"] = "0"


@pytest.fixture(autouse=True)
def _isolate_llm_result_cache(tmp_path, monkeypatch):
    """Keep the combinational LLM result cache out of the repo's outputs/.

    The service caches model answers per netlist on disk. Left at its
    default path, a test that runs a fake provider over a fixture circuit
    would seed a cache that a later test -- expecting the provider to be
    called, or to fail -- then hits, making the suite order-dependent.
    Tests that exercise the cache itself pass their own path explicitly.
    """
    from pyatpg.atpg import llm_context

    monkeypatch.setattr(
        llm_context, "DEFAULT_CACHE_PATH", str(tmp_path / "comb_llm_cache.json")
    )


# The ISCAS/Trust-Hub benchmark circuits are not in the repository (they are
# kept locally in benchmarks/). On a checkout without that folder, a test that
# needs one is skipped instead of failing; the one module that reads a
# benchmark at import time is left out of collection altogether.
import os as _os

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_HAVE_BENCHMARKS = _os.path.isdir(_os.path.join(_REPO_ROOT, "benchmarks"))
collect_ignore = [] if _HAVE_BENCHMARKS else ["test_trojan_dataset_format.py"]


def _missing_benchmark(excinfo) -> bool:
    """Did the test fail because a benchmarks/ file is absent?"""
    exc = excinfo[1] if excinfo else None
    while exc is not None:
        if isinstance(exc, FileNotFoundError) and "benchmarks" in str(exc).replace("\\", "/"):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _skip_if_benchmark_missing(outcome) -> None:
    if not _HAVE_BENCHMARKS and _missing_benchmark(outcome.excinfo):
        outcome.force_exception(pytest.skip.Exception("benchmarks/ not present in this checkout"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _skip_if_benchmark_missing((yield))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _skip_if_benchmark_missing((yield))
