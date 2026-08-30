"""A targeted run's fault coverage must never be presented unqualified.

Atalanta computes coverage over the fault list it is given. Under -f that list
holds only the selected gates' faults, so a targeted run routinely reports
99-100% while most of the circuit's faults were never in it -- on c1355 the
targeted list is ~10% of the collapsed faults, and reports 100% against a
whole-circuit 98%.

Compare mode already carried this caveat through build_comparison. A
targeted-only run built no comparison at all, so the number reached the UI with
nothing attached.
"""
from pyatpg.atpg.models import AtalantaRun, build_comparison
from pyatpg.atpg.service import _targeted_scope_note


def _run(label: str, targeted: bool, collapsed: int, detected: int, coverage: float):
    run = AtalantaRun(label=label, targeted=targeted)
    run.results.collapsed_faults = collapsed
    run.results.redundant_faults = 0
    run.results.aborted_faults = 0
    run.results.fault_coverage = coverage
    run.results.patterns_after_compaction = 10
    return run


def test_scope_note_names_the_denominator():
    run = _run("Targeted", True, collapsed=164, detected=163, coverage=99.39)
    note = _targeted_scope_note(run, gates=[f"G{i}" for i in range(82)])

    assert "164 collapsed fault(s)" in note
    assert "82 selected gate(s)" in note
    assert "not over the whole circuit" in note


def test_scope_note_survives_an_unreported_denominator():
    """Atalanta not reporting a value must not fabricate one."""
    run = _run("Targeted", True, collapsed=None, detected=None, coverage=None)
    note = _targeted_scope_note(run, gates=["G1", "G2"])

    assert "2 selected gate(s)" in note
    assert "None" not in note


def test_targeted_only_analysis_carries_the_caveat():
    """The end-to-end shape: targeted-only mode must not ship a bare percentage."""
    from pyatpg.atpg.service import AtpgAnalysis
    from pyatpg.atpg.models import AnalysisMode

    analysis = AtpgAnalysis(job_id="j", mode=AnalysisMode.TARGETED, circuit={})
    analysis.runs["targeted"] = _run("Targeted", True, 164, 163, 99.39)
    analysis.notes.append(_targeted_scope_note(analysis.runs["targeted"], ["G1"] * 82))

    payload = analysis.to_dict()
    assert payload["comparison"] is None, "targeted-only builds no comparison"
    assert any("not over the whole circuit" in n for n in payload["notes"])


def test_compare_mode_still_reports_circuit_wide_coverage():
    """The existing Compare-mode caveats must be untouched by this change."""
    targeted = _run("Targeted", True, collapsed=166, detected=166, coverage=100.0)
    non_targeted = _run("Full", False, collapsed=1582, detected=1553, coverage=98.167)

    c = build_comparison(targeted, non_targeted)

    assert c.targeted_coverage == 100.0
    # 166/1582 -- the honest figure, an order of magnitude below the headline.
    assert c.targeted_circuit_wide_coverage == 10.493
    assert c.targeted_fault_share_pct == 10.49
    assert any("own fault list" in n for n in c.notes)
    # A targeted run scoring at or above the full run on a fraction of the
    # faults is exactly the misreading the second note exists to head off.
    assert any(
        "not evidence the targeted run performed better" in " ".join(n.split())
        for n in c.notes
    )
