"""Execution of the Atalanta ATPG engine.

Atalanta is invoked directly as an argument vector -- there is no shell, no
generated script, and no interpolation of user-supplied names into a command
string. Circuit names are sanitised into filenames by the workspace before
they reach this module.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .binary import AtalantaBinary, resolve_atalanta_binary
from .models import AtalantaRun
from .parsing import apply_summary, parse_test_vectors, parse_undetected_faults
from .workspace import (
    ARTIFACT_BENCH,
    ARTIFACT_FAULT_LIST,
    ARTIFACT_LOG,
    ARTIFACT_STDERR,
    ARTIFACT_TEST_VECTORS,
    ARTIFACT_UNDETECTED_FAULTS,
    JobWorkspace,
)

#: Atalanta is a fast combinational engine; anything beyond this is a hang.
DEFAULT_TIMEOUT_SECONDS = 600

#: Header Atalanta prints only once it has actually generated patterns.
SUMMARY_MARKER = "SUMMARY OF TEST PATTERN GENERATION RESULTS"


class AtalantaExecutionError(RuntimeError):
    """Atalanta could not be executed, or exited non-zero."""


class AtalantaRunner:
    """Runs Atalanta inside a job workspace and parses everything it emits."""

    def __init__(self, binary: AtalantaBinary | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self._binary = binary
        self.timeout = timeout

    @property
    def binary(self) -> AtalantaBinary:
        # Resolved lazily so importing this module never fails on a machine
        # without a build.
        if self._binary is None:
            self._binary = resolve_atalanta_binary()
        return self._binary

    def run(
        self,
        workspace: JobWorkspace,
        bench_text: str,
        label: str,
        targeted_faults: list[str] | None = None,
        targeted_gates: list[str] | None = None,
    ) -> AtalantaRun:
        """Execute one Atalanta job.

        Args:
            workspace: directory that will hold every artifact of this run.
            bench_text: the circuit in ISCAS .bench format.
            label: human-readable name for this run, used in the UI.
            targeted_faults: fault lines ("<gate> /0") to pass via -f. When
                None, Atalanta derives and collapses the full fault list
                itself, which is the non-targeted run.
            targeted_gates: the gate ids the faults were derived from,
                recorded on the result for correlation.

        Raises:
            AtalantaExecutionError: on a non-zero exit, a timeout, or a
                missing/unrunnable binary. Never returns a partial result
                that could be mistaken for a successful zero-coverage run.
        """
        prefix = f"{workspace.circuit_name}_{'targeted' if targeted_faults is not None else 'full'}"

        bench_path = workspace.write_text(f"{prefix}.bench", bench_text)
        vec_path = workspace.path_for(f"{prefix}.vec")
        ufaults_path = workspace.path_for(f"{prefix}.ufaults")
        log_path = workspace.path_for(f"{prefix}.log")
        stderr_path = workspace.path_for(f"{prefix}.stderr.log")

        command: list[str] = [str(self.binary.path)]
        fault_path: Path | None = None
        if targeted_faults is not None:
            fault_path = workspace.write_text(f"{prefix}.faults", "\n".join(targeted_faults) + "\n")
            command += ["-f", fault_path.name]
        command += ["-t", vec_path.name, "-U", ufaults_path.name, bench_path.name]

        try:
            completed = subprocess.run(
                command,
                cwd=workspace.directory,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AtalantaExecutionError(
                f"Atalanta timed out after {self.timeout}s for run {label!r}."
            ) from exc
        except OSError as exc:
            raise AtalantaExecutionError(
                f"Could not execute Atalanta at {self.binary.path}: {exc}"
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        log_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        if completed.returncode != 0:
            raise AtalantaExecutionError(
                f"Atalanta exited with code {completed.returncode} for run {label!r}.\n"
                f"{(stderr or stdout).strip()[:2000]}"
            )

        # Atalanta reports fatal netlist errors on stderr and still exits 0,
        # so the exit code alone cannot be trusted. Require the summary block
        # it only prints on a completed run -- otherwise every metric would
        # parse as None and be indistinguishable from a genuine empty result.
        if SUMMARY_MARKER not in stdout:
            detail = (stderr or stdout).strip() or "Atalanta produced no output."
            raise AtalantaExecutionError(
                f"Atalanta did not complete test pattern generation for run {label!r} "
                f"(exit code {completed.returncode}, no results summary emitted).\n"
                f"{detail[:2000]}"
            )

        run = AtalantaRun(
            label=label,
            targeted=targeted_faults is not None,
            command=[Path(command[0]).name, *command[1:]],
            exit_code=completed.returncode,
            targeted_gates=list(targeted_gates or []),
            raw_log=stdout,
            raw_stderr=stderr,
        )
        apply_summary(run, stdout)

        if vec_path.is_file():
            run.test_vectors = parse_test_vectors(vec_path.read_text(encoding="utf-8", errors="replace"))
        if ufaults_path.is_file():
            run.faults = parse_undetected_faults(
                ufaults_path.read_text(encoding="utf-8", errors="replace")
            )

        produced = {
            ARTIFACT_BENCH: bench_path,
            ARTIFACT_FAULT_LIST: fault_path,
            ARTIFACT_TEST_VECTORS: vec_path,
            ARTIFACT_UNDETECTED_FAULTS: ufaults_path,
            ARTIFACT_LOG: log_path,
            ARTIFACT_STDERR: stderr_path,
        }
        run.artifacts = {
            key: path.name
            for key, path in produced.items()
            if path is not None and path.is_file()
        }
        return run


def build_fault_lines(gates: list[str]) -> list[str]:
    """Expand gate ids into the stuck-at-0/1 fault lines Atalanta's -f expects."""
    lines: list[str] = []
    for gate in gates:
        clean = str(gate).strip()
        if not clean:
            continue
        lines.append(f"{clean} /0")
        lines.append(f"{clean} /1")
    return lines
