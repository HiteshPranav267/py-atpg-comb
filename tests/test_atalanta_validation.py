"""A cyclic netlist must never reach Atalanta.

Atalanta does not reject a combinational feedback loop -- it segfaults during
levelisation (exit 0xC0000005 on Windows, SIGSEGV on Linux), which reaches the
user as an opaque "exited with code 3221225477". The guard therefore lives on
the bench text in AtalantaRunner.run, the one chokepoint shared by both the
FastAPI service and the Flask desktop app's /api/jobs/dispatch; the latter had
no DAG check of its own, which is how cyclic netlists still got through after
the insertion-side fix.
"""
import pytest

from pyatpg.atpg.validation import (
    CyclicNetlistError,
    assert_atalanta_safe,
    find_combinational_cycle,
)

ACYCLIC = """
# a plain combinational netlist
INPUT(a)
INPUT(b)
OUTPUT(z)
n1 = AND(a, b)
n2 = NOT(n1)
z = OR(n1, n2)
"""

CYCLIC = """
INPUT(a)
INPUT(b)
OUTPUT(z)
n1 = AND(a, n2)
n2 = OR(b, n1)
z = NOT(n1)
"""

SELF_LOOP = """
INPUT(a)
OUTPUT(z)
n1 = AND(a, n1)
z = NOT(n1)
"""

# Ordinary register feedback: n1 -> DFF q -> n1. Legal, and present in
# essentially every sequential circuit, so it must not read as a cycle.
CLOCKED_FEEDBACK = """
INPUT(a)
OUTPUT(z)
q = DFF(n1)
n1 = AND(a, q)
z = NOT(q)
"""


def test_acyclic_netlist_passes():
    assert find_combinational_cycle(ACYCLIC) is None
    assert_atalanta_safe(ACYCLIC)  # must not raise


def test_cycle_is_found_and_reported():
    cycle = find_combinational_cycle(CYCLIC)
    assert cycle is not None
    assert set(cycle) == {"n1", "n2"}
    assert cycle[0] == cycle[-1], "the loop should close on the net it entered"


def test_self_loop_is_found():
    assert find_combinational_cycle(SELF_LOOP) == ["n1", "n1"]


def test_flip_flop_feedback_is_not_a_combinational_cycle():
    """A DFF's Q is a free source: its value came from the previous cycle."""
    assert find_combinational_cycle(CLOCKED_FEEDBACK) is None


def test_assert_names_the_offending_nets():
    with pytest.raises(CyclicNetlistError) as excinfo:
        assert_atalanta_safe(CYCLIC, label="c432 variant 7")
    message = str(excinfo.value)
    assert "c432 variant 7" in message
    assert "n1" in message and "n2" in message


def test_comments_and_ports_are_not_mistaken_for_gates():
    text = "# z = AND(z, z)\nINPUT(a)\nOUTPUT(z)\nz = NOT(a)\n"
    assert find_combinational_cycle(text) is None


def test_runner_refuses_before_invoking_the_binary():
    """The guard must fire without the executable even being resolved.

    Resolution is lazy, so a machine with no Atalanta build still exercises
    this -- and it proves the check runs *before* the subprocess, which is the
    whole point: once the process starts, a crash is all that comes back.
    """
    from pyatpg.atpg.runner import AtalantaRunner

    runner = AtalantaRunner()
    with pytest.raises(CyclicNetlistError):
        runner.run(workspace=None, bench_text=CYCLIC, label="cyclic")
