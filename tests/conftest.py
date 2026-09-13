import pytest


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
