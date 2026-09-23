"""The targeted run raises its search budget while faults abort.

A targeted list is built from the hardest faults in the circuit, and it is
tiny, so Atalanta's default 10 backtracks is both where those faults abort
and a budget there is no reason to keep. The service re-runs the same list
up a ladder of budgets until nothing aborts, reports the best attempt, and
records every attempt so the reader can see the cost.
"""
from pyatpg.atpg.models import AtalantaRun
from pyatpg.atpg.service import TARGETED_EFFORT_LADDER, AtpgAnalysisService


class _ScriptedRunner:
    """Returns a scripted aborted-count per attempt and records the argv."""

    def __init__(self, aborted_per_attempt):
        self.aborted = list(aborted_per_attempt)
        self.calls = []

    def run(self, workspace, bench_text, label, **kwargs):
        self.calls.append(kwargs)
        run = AtalantaRun(label=label, targeted=True)
        aborted = self.aborted.pop(0) if self.aborted else 0
        run.results.collapsed_faults = 100
        run.results.redundant_faults = 0
        run.results.aborted_faults = aborted
        run.results.fault_coverage = 100.0 - aborted
        run.results.patterns_after_compaction = 10
        run.timing.total = 0.01
        return run


def _service(runner):
    svc = AtpgAnalysisService(runner=runner)
    return svc


def test_no_aborted_faults_means_a_single_attempt():
    runner = _ScriptedRunner([0])
    notes = []
    run = _service(runner)._run_targeted(None, "", ["g1"], None, notes)
    assert len(runner.calls) == 1
    assert run.attempts == []
    assert notes == []


def test_aborted_faults_climb_the_ladder_until_resolved():
    runner = _ScriptedRunner([6, 5, 0])
    notes = []
    run = _service(runner)._run_targeted(None, "", ["g1"], None, notes)

    assert len(runner.calls) == 1 + len(TARGETED_EFFORT_LADDER)
    # Each rung forwards its own budget, with a distinct artifact tag.
    for call, (b, B, learn) in zip(runner.calls[1:], TARGETED_EFFORT_LADDER):
        assert call["backtrack_limit"] == b
        assert call["backtrack_limit_phase2"] == B
        assert call["static_learning"] is learn
        assert call["attempt_tag"] == f"b{b}"
    assert run.results.aborted_faults == 0
    assert [a["aborted_faults"] for a in run.attempts] == [6, 5, 0]
    assert run.attempts[0]["label"] == "default budget"
    assert run.attempts[-1]["label"] == "-b 10000 -B 10000 -L"
    assert any("6 fault(s) aborted" in n and "every fault" in n for n in notes)


def test_stops_climbing_once_nothing_aborts():
    runner = _ScriptedRunner([3, 0])
    run = _service(runner)._run_targeted(None, "", ["g1"], None, [])
    assert len(runner.calls) == 2
    assert run.results.aborted_faults == 0


def test_best_attempt_is_kept_even_when_a_later_rung_is_worse():
    """More budget should not make things worse, but if an attempt does
    (different random fill), the reported run is still the best one."""
    runner = _ScriptedRunner([4, 1, 2])
    run = _service(runner)._run_targeted(None, "", ["g1"], None, [])
    assert run.results.aborted_faults == 1
    assert len(run.attempts) == 3
    assert [a["reported"] for a in run.attempts] == [False, True, False]


def test_caller_budget_at_or_above_a_rung_skips_it():
    """An explicit -b 1000 already covers the first rung."""
    runner = _ScriptedRunner([2, 0])
    notes = []
    run = _service(runner)._run_targeted(None, "", ["g1"], 1000, notes)
    assert runner.calls[0]["backtrack_limit"] == 1000
    assert runner.calls[1]["backtrack_limit"] == 10000
    assert run.results.aborted_faults == 0


def test_empty_ladder_pins_the_targeted_run_to_the_callers_budget():
    """``effort_ladder=()`` makes the targeted run a same-budget comparison.

    Measuring whether targeting itself helps means giving both runs the same
    per-fault search budget. With the ladder on, a targeted run that aborts
    climbs to 10000 backtracks while the non-targeted baseline stays at the
    requested limit, so any detection difference is confounded by budget.
    Pinned, the targeted run must issue exactly one attempt at exactly the
    caller's limit even though faults are still aborting.
    """
    runner = _ScriptedRunner([7, 0, 0])
    notes = []
    svc = AtpgAnalysisService(runner=runner, effort_ladder=())
    run = svc._run_targeted(None, "", ["g1"], 5, notes)

    assert len(runner.calls) == 1
    assert runner.calls[0]["backtrack_limit"] == 5
    assert "backtrack_limit_phase2" not in runner.calls[0]
    assert "static_learning" not in runner.calls[0]
    # Aborted faults are reported as they are, not escalated away.
    assert run.results.aborted_faults == 7
    # A single attempt records no ladder history and adds no escalation note.
    assert run.attempts == []
    assert notes == []


def test_default_service_still_climbs_the_ladder():
    """The pinning option must not change any existing caller's behaviour."""
    runner = _ScriptedRunner([6, 5, 0])
    svc = AtpgAnalysisService(runner=runner)
    assert svc.effort_ladder == TARGETED_EFFORT_LADDER
    svc._run_targeted(None, "", ["g1"], None, [])
    assert len(runner.calls) == 1 + len(TARGETED_EFFORT_LADDER)
