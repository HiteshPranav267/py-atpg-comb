"""AtalantaRunner's own argv construction, independent of running the binary.

Atalanta only runs on Linux in this repo (the vendored build is an ELF
binary; Windows development machines resolve a build that may not exist),
so these tests fake the subprocess call rather than requiring a real
Atalanta -- they pin down exactly what argv this wrapper builds, which is
what test_atpg_parsing.py's fixture-based tests can't cover.
"""
import subprocess
from unittest.mock import patch

from pyatpg.atpg.binary import AtalantaBinary
from pyatpg.atpg.runner import AtalantaRunner
from pyatpg.atpg.workspace import JobWorkspace

MINIMAL_BENCH = """
INPUT(a)
INPUT(b)
OUTPUT(z)
z = AND(a, b)
"""

FAKE_SUMMARY = """
******   SUMMARY OF TEST PATTERN GENERATION RESULTS   ******
1. Circuit structure
   Name of the circuit                       : fake
   Number of primary inputs                  : 2
   Number of primary outputs                 : 1
   Number of gates                           : 1
   Level of the circuit                      : 1

3. Test pattern generation results
   Number of test patterns before compaction : 1
   Number of test patterns after compaction  : 1
   Fault coverage                            : 100.000 %
   Number of collapsed faults                : 2
   Number of identified redundant faults     : 0
   Number of aborted faults                  : 0
   Total number of backtrackings             : 0
"""


def _fake_binary() -> AtalantaBinary:
    return AtalantaBinary(path=__import__("pathlib").Path("atalanta"), platform_name="Linux")


def _fake_completed(command):
    return subprocess.CompletedProcess(args=command, returncode=0, stdout=FAKE_SUMMARY, stderr="")


def _run(workspace, **kwargs):
    runner = AtalantaRunner(binary=_fake_binary())
    with patch("pyatpg.atpg.runner.subprocess.run") as mock_run:
        mock_run.side_effect = lambda command, **_: _fake_completed(command)
        run = runner.run(workspace, MINIMAL_BENCH, label="test", **kwargs)
    return run, mock_run.call_args.args[0]


def test_backtrack_limit_omitted_by_default(tmp_path):
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    _, command = _run(ws)
    assert "-b" not in command


def test_backtrack_limit_passed_as_dash_b(tmp_path):
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    _, command = _run(ws, backtrack_limit=50)
    assert "-b" in command
    assert command[command.index("-b") + 1] == "50"


def test_dash_l_is_always_passed(tmp_path):
    """-l is what makes parse_fault_dispositions possible at all (Finding C)."""
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    _, command = _run(ws)
    assert "-l" in command


def test_run_still_succeeds_with_a_backtrack_limit(tmp_path):
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    run, _ = _run(ws, backtrack_limit=25)
    assert run.exit_code == 0
    assert run.results.fault_coverage == 100.0


def test_phase2_and_learning_flags_are_forwarded(tmp_path):
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    _, argv = _run(ws, backtrack_limit=1000, backtrack_limit_phase2=1000, static_learning=True)
    assert argv[argv.index("-b") + 1] == "1000"
    assert argv[argv.index("-B") + 1] == "1000"
    assert "-L" in argv


def test_phase2_and_learning_omitted_by_default(tmp_path):
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    _, argv = _run(ws)
    assert "-B" not in argv
    assert "-L" not in argv


def test_attempt_tag_keeps_artifacts_apart(tmp_path):
    """Two attempts at the same fault list must not overwrite each other."""
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    first, _ = _run(ws, targeted_faults=["z /0"])
    second, _ = _run(ws, targeted_faults=["z /0"], attempt_tag="b1000")
    assert first.artifacts["bench"] == "fake_targeted.bench"
    assert second.artifacts["bench"] == "fake_targeted_b1000.bench"


def test_pattern_file_does_not_share_atalantas_scratch_vec_name(tmp_path):
    """Atalanta writes its own "<stem>.vec" regardless of -t; the -t file
    must have a different name or the two writers clobber each other."""
    ws = JobWorkspace(job_id="t", directory=tmp_path, circuit_name="fake")
    _, argv = _run(ws)
    test_file = argv[argv.index("-t") + 1]
    bench = argv[-1]
    assert test_file != bench.rsplit(".", 1)[0] + ".vec"
    assert test_file.endswith(".test")
