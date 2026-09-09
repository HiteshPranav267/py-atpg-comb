"""Parser tests pinned to real Atalanta output.

The fixtures in tests/fixtures are verbatim output from Atalanta 2.0 run on
benchmarks/c432.bench. The expected values below are read off that output by
eye, so a regression in the parser shows up as a mismatch against the tool's
own numbers rather than against something the parser generated.
"""
from pathlib import Path

import pytest

from pyatpg.atpg.parsing import (
    extract_version,
    parse_fault_dispositions,
    parse_summary_report,
    parse_test_vectors,
    parse_undetected_faults,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def summary_text() -> str:
    return (FIXTURES / "atalanta_c432_summary.log").read_text()


def test_reads_version_from_banner(summary_text):
    assert extract_version(summary_text) == "2.0"


def test_circuit_structure_matches_tool_output(summary_text):
    circuit, _, _, _, _ = parse_summary_report(summary_text)
    assert circuit.name == "c432"
    assert circuit.primary_inputs == 36
    assert circuit.primary_outputs == 7
    assert circuit.gates == 160
    assert circuit.logic_depth == 17


def test_atpg_parameters_match_tool_output(summary_text):
    _, parameters, _, _, _ = parse_summary_report(summary_text)
    assert parameters.generation_mode == "RPT + DTPG + TC"
    assert parameters.random_pattern_limit == 16
    assert parameters.backtrack_limit == 10
    assert parameters.compaction_mode == "REVERSE + SHUFFLE"
    assert parameters.shuffling_compaction_limit == 2
    assert parameters.shuffles == 4
    assert parameters.random_seed is not None


def test_generation_results_match_tool_output(summary_text):
    _, _, results, _, _ = parse_summary_report(summary_text)
    assert results.patterns_before_compaction == 85
    assert results.patterns_after_compaction == 63
    assert results.fault_coverage == 99.046
    assert results.collapsed_faults == 524
    assert results.redundant_faults == 1
    assert results.aborted_faults == 4
    assert results.backtrackings == 47


def test_detected_faults_are_derived_consistently(summary_text):
    _, _, results, _, _ = parse_summary_report(summary_text)
    # Atalanta defines aborted as collapsed - detected - redundant.
    assert results.detected_faults == 524 - 1 - 4


def test_timing_and_memory_are_read(summary_text):
    _, _, _, timing, memory_mb = parse_summary_report(summary_text)
    assert timing.total is not None
    assert timing.initialization is not None
    assert timing.fault_simulation is not None
    assert timing.fan is not None
    # "4. Memory used" doubles as a section heading; it must still parse.
    assert memory_mb is not None


def test_absent_metrics_are_none_not_zero():
    """A missing metric must never look like a real zero."""
    circuit, parameters, results, timing, memory_mb = parse_summary_report("")
    assert results.fault_coverage is None
    assert results.collapsed_faults is None
    assert results.patterns_after_compaction is None
    assert results.detected_faults is None
    assert circuit.primary_inputs is None
    assert parameters.backtrack_limit is None
    assert timing.total is None
    assert memory_mb is None


def test_uncompacted_run_reports_a_single_pattern_count():
    text = """3. Test pattern generation results
   Number of test patterns                   : 42
   Fault coverage                            : 88.500 %
"""
    _, _, results, _, _ = parse_summary_report(text)
    assert results.patterns_before_compaction == 42
    assert results.patterns_after_compaction == 42
    assert results.fault_coverage == 88.5


def test_test_vectors_parse_with_io_ordering():
    vectors = parse_test_vectors((FIXTURES / "atalanta_c432.vec").read_text())
    assert vectors.count == 63
    assert len(vectors.primary_inputs) == 36
    assert len(vectors.primary_outputs) == 7
    first = vectors.vectors[0]
    assert first.index == 1
    assert len(first.inputs) == 36
    assert len(first.outputs) == 7
    assert set(first.inputs) <= set("01xXzZ")


def test_undetected_faults_parse():
    faults = parse_undetected_faults((FIXTURES / "atalanta_c432.ufaults").read_text())
    assert len(faults) == 4
    assert all(f.status == "undetected" for f in faults)
    assert all(f.fault_type in {"stuck-at-0", "stuck-at-1"} for f in faults)
    assert faults[0].gate == "379"
    assert faults[0].fault_name == "379 /1"


def test_empty_fault_report_yields_no_faults():
    assert parse_undetected_faults("") == []


def test_fault_dispositions_split_redundant_from_aborted():
    """Real Atalanta -l output on c432 (same circuit as the -U fixture above).

    -U alone reports these same 4 faults as "undetected" with no way to tell
    the redundant one apart -- the point of parse_fault_dispositions is that
    -l's headed breakdown makes that distinction possible at all.
    """
    text = (FIXTURES / "atalanta_c432.atalanta.log").read_text()
    faults = parse_fault_dispositions(text)

    redundant = [f for f in faults if f.status == "redundant"]
    aborted = [f for f in faults if f.status == "aborted"]

    assert len(redundant) == 1
    assert redundant[0].gate == "393->429"
    assert redundant[0].fault_name == "393->429 /1"

    assert len(aborted) == 4
    assert {f.gate for f in aborted} == {"379", "347", "259", "377"}

    # Matches the summary block's own counts (parse_summary_report on the
    # same log) -- the two parsers must never disagree.
    _, _, results, _, _ = parse_summary_report(text)
    assert len(redundant) == results.redundant_faults
    assert len(aborted) == results.aborted_faults


def test_fault_dispositions_empty_text_yields_nothing():
    assert parse_fault_dispositions("") == []


def test_fault_dispositions_ignore_unrelated_content():
    """Only text inside the two headed blocks counts as a fault line."""
    text = """
* Log file for the circuit c432.bench.
fpTestFile    1: 0000 1111   3 faults detected

* List of identified redundant faults:
100 /0

* Number of faults detected by each test pattern:
200 /1
"""
    faults = parse_fault_dispositions(text)
    assert len(faults) == 1
    assert faults[0].gate == "100"
    assert faults[0].status == "redundant"
