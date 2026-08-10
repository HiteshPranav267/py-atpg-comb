"""Parsers for the three output formats Atalanta produces.

These read Atalanta's own output verbatim. Where a value is absent from the
output it stays None; nothing is defaulted, derived or estimated here, so a
missing metric can never masquerade as a real zero.
"""
from __future__ import annotations

import re

from .models import (
    AtalantaRun,
    AtpgParameters,
    AtpgResults,
    CircuitStructure,
    FaultEntry,
    TestVector,
    TestVectorSet,
    TimingBreakdown,
)

#: Summary lines are "   Some label   : value". Labels never contain a colon.
_LABELLED_LINE_RE = re.compile(r"^\s*(?P<label>[^:]+?)\s*:\s*(?P<value>.*?)\s*$")

#: Some metrics double as section headings ("4. Memory used : 9.422 MB"), so
#: the section number is stripped before the label is looked up.
_SECTION_NUMBER_RE = re.compile(r"^\d+\.\s*")

_VERSION_RE = re.compile(r"atalanta\s*\(version\s*(?P<version>[^)]+)\)", re.IGNORECASE)

#: "  12: 0101 1100" -> index, input vector, output response.
_VECTOR_RE = re.compile(r"^\s*(?P<index>\d+)\s*:\s*(?P<inputs>[01xXzZ]+)\s+(?P<outputs>[01xXzZ]+)\s*$")

#: "379 /1" -> gate 379, stuck-at-1.
_FAULT_RE = re.compile(r"^(?P<gate>\S+)\s+/(?P<value>[01])\s*$")


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def extract_version(text: str) -> str | None:
    """Read the version out of Atalanta's welcome banner."""
    match = _VERSION_RE.search(text or "")
    return match.group("version").strip() if match else None


def _collect_labelled_values(text: str) -> dict[str, str]:
    """Map every "label : value" line in the summary to its raw value.

    Labels are lowercased and whitespace-collapsed so lookups are stable
    against Atalanta's column padding.
    """
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        # Banner lines are boxed in asterisks and contain no real metrics.
        if line.lstrip().startswith("*"):
            continue
        match = _LABELLED_LINE_RE.match(line)
        if not match:
            continue
        label = " ".join(match.group("label").split()).lower()
        label = _SECTION_NUMBER_RE.sub("", label)
        value = match.group("value").strip()
        if label and value:
            values[label] = value
    return values


def parse_summary_report(text: str) -> tuple[CircuitStructure, AtpgParameters, AtpgResults, TimingBreakdown, float | None]:
    """Parse Atalanta's "SUMMARY OF TEST PATTERN GENERATION RESULTS" block.

    Returns the circuit structure, ATPG parameters, generation results,
    CPU timing breakdown, and peak memory in MB (None if not reported).
    """
    values = _collect_labelled_values(text)

    circuit = CircuitStructure(
        name=values.get("name of the circuit"),
        primary_inputs=_to_int(values.get("number of primary inputs")),
        primary_outputs=_to_int(values.get("number of primary outputs")),
        gates=_to_int(values.get("number of gates")),
        logic_depth=_to_int(values.get("level of the circuit")),
    )

    parameters = AtpgParameters(
        generation_mode=values.get("test pattern generation mode"),
        random_pattern_limit=_to_int(values.get("limit of random patterns (packets)")),
        backtrack_limit=_to_int(values.get("backtrack limit")),
        backtrack_limit_phase1=_to_int(values.get("backtrack limit (phase 1 test generation)")),
        backtrack_limit_phase2=_to_int(values.get("backtrack limit (phase 2 test generation)")),
        random_seed=_to_int(values.get("initial random number generator seed")),
        compaction_mode=values.get("test pattern compaction mode"),
        shuffling_compaction_limit=_to_int(values.get("limit of shuffling compaction")),
        shuffles=_to_int(values.get("number of shuffles")),
    )

    # With compaction disabled Atalanta prints a single "Number of test
    # patterns" line instead of the before/after pair.
    patterns_uncompacted = values.get("number of test patterns")
    results = AtpgResults(
        patterns_before_compaction=_to_int(
            values.get("number of test patterns before compaction", patterns_uncompacted)
        ),
        patterns_after_compaction=_to_int(
            values.get("number of test patterns after compaction", patterns_uncompacted)
        ),
        fault_coverage=_to_float(values.get("fault coverage")),
        collapsed_faults=_to_int(values.get("number of collapsed faults")),
        redundant_faults=_to_int(values.get("number of identified redundant faults")),
        aborted_faults=_to_int(values.get("number of aborted faults")),
        backtrackings=_to_int(values.get("total number of backtrackings")),
    )

    timing = TimingBreakdown(
        initialization=_to_float(values.get("initialization")),
        fault_simulation=_to_float(values.get("fault simulation")),
        fan=_to_float(values.get("fan")),
        total=_to_float(values.get("total")),
    )

    memory_mb = _to_float(values.get("memory used"))

    return circuit, parameters, results, timing, memory_mb


def apply_summary(run: AtalantaRun, log_text: str) -> AtalantaRun:
    """Populate a run in place from its captured stdout."""
    circuit, parameters, results, timing, memory_mb = parse_summary_report(log_text)
    run.circuit = circuit
    run.parameters = parameters
    run.results = results
    run.timing = timing
    run.memory_mb = memory_mb
    run.version = extract_version(log_text)
    return run


def parse_test_vectors(text: str) -> TestVectorSet:
    """Parse an Atalanta .vec file.

    The format is a few ``*``-prefixed headers followed by indented signal
    name lists, then one line per pattern: ``index: <inputs> <outputs>``.
    """
    result = TestVectorSet()
    if not text:
        return result

    section: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("*"):
            lowered = stripped.lower()
            if "primary inputs" in lowered:
                section = "inputs"
            elif "primary outputs" in lowered:
                section = "outputs"
            elif "test pattern" in lowered:
                section = "patterns"
            else:
                section = None
            continue

        if section == "patterns":
            match = _VECTOR_RE.match(line)
            if match:
                result.vectors.append(
                    TestVector(
                        index=int(match.group("index")),
                        inputs=match.group("inputs"),
                        outputs=match.group("outputs"),
                    )
                )
        elif section == "inputs":
            result.primary_inputs.extend(stripped.split())
        elif section == "outputs":
            result.primary_outputs.extend(stripped.split())

    return result


def parse_undetected_faults(text: str, status: str = "undetected") -> list[FaultEntry]:
    """Parse Atalanta's -U report of faults it could not detect.

    Only faults Atalanta actually listed are returned. A gate's absence from
    this file means its faults were detected, which is the caller's business
    to interpret, not this parser's.
    """
    faults: list[FaultEntry] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "*")):
            continue
        match = _FAULT_RE.match(stripped)
        if not match:
            continue
        gate = match.group("gate")
        value = match.group("value")
        faults.append(
            FaultEntry(
                fault_name=f"{gate} /{value}",
                gate=gate,
                fault_type=f"stuck-at-{value}",
                status=status,
            )
        )
    return faults


def parse_fault_list(text: str) -> list[FaultEntry]:
    """Parse a targeted fault list (the file handed to Atalanta via -f)."""
    return parse_undetected_faults(text, status="targeted")
