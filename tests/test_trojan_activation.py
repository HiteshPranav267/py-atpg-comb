"""pyatpg.trojan.activation, independent of a real Atalanta binary.

Atalanta only runs on Linux in this repo (see test_atpg_runner.py's module
docstring), so these tests fake AtalantaRunner.run entirely -- they pin down
the activating-fault-value logic and the pass/fail mapping, which is what
this module actually adds. A real-binary integration test that exercises
the full insert() -> verify_trigger_activatable() path lives in
test_trojan_insertion.py, skipped when Atalanta isn't resolvable.
"""
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from pyatpg.trojan.activation import (
    ActivationUnverifiable,
    TriggerNotActivatable,
    verify_trigger_activatable,
)


@dataclass
class _FakeResults:
    detected_faults: int


@dataclass
class _FakeFault:
    status: str


@dataclass
class _FakeRun:
    results: _FakeResults
    faults: list = field(default_factory=list)


class _FakeCircuit:
    def to_bench(self) -> str:
        return "# fake bench text"


def _patched(detected_faults: int, faults=(), calls: list | None = None):
    """Patch verify_trigger_activatable's Atalanta calls to return a canned
    run, optionally recording each call's targeted_faults into `calls`."""
    fake_run = _FakeRun(results=_FakeResults(detected_faults=detected_faults), faults=list(faults))

    class _FakeRunner:
        def __init__(self, binary=None):
            pass

        def run(self, workspace, bench_text, label, targeted_faults, targeted_gates, backtrack_limit=None):
            if calls is not None:
                calls.append(targeted_faults)
            return fake_run

    return patch.multiple(
        "pyatpg.trojan.activation",
        resolve_atalanta_binary=lambda: object(),
        AtalantaRunner=_FakeRunner,
    )


@pytest.mark.parametrize("gate_type,value", [("AND", "0"), ("OR", "0"), ("NAND", "1"), ("NOR", "1")])
def test_activating_fault_value_matches_gate_semantics(gate_type, value):
    """AND/OR are 'on' at 1 (checked via the complementary s-a-0 fault);
    NAND/NOR invert that."""
    calls: list = []
    with _patched(detected_faults=1, calls=calls), patch(
        "pyatpg.trojan.activation.JobWorkspace.create"
    ) as create_ws:
        create_ws.return_value.destroy = lambda: None
        verify_trigger_activatable(_FakeCircuit(), "ht_trigger_out_1", gate_type)
    assert calls == [[f"ht_trigger_out_1 /{value}"]]


def test_xor_trigger_is_unverifiable_without_touching_atalanta():
    """XOR has no single controlling input combination -- this must raise
    before ever resolving a binary or spawning a job."""
    with patch("pyatpg.trojan.activation.resolve_atalanta_binary") as resolve:
        with pytest.raises(ActivationUnverifiable):
            verify_trigger_activatable(_FakeCircuit(), "ht_trigger_out_1", "XOR")
        resolve.assert_not_called()


def test_passes_when_atalanta_detects_the_activating_fault():
    with _patched(detected_faults=1), patch("pyatpg.trojan.activation.JobWorkspace.create") as create_ws:
        create_ws.return_value.destroy = lambda: None
        verify_trigger_activatable(_FakeCircuit(), "ht_trigger_out_1", "AND")  # must not raise


def test_raises_when_atalanta_does_not_detect_the_fault():
    with _patched(detected_faults=0, faults=[_FakeFault(status="aborted")]), patch(
        "pyatpg.trojan.activation.JobWorkspace.create"
    ) as create_ws:
        create_ws.return_value.destroy = lambda: None
        with pytest.raises(TriggerNotActivatable, match="aborted"):
            verify_trigger_activatable(_FakeCircuit(), "ht_trigger_out_1", "AND")
