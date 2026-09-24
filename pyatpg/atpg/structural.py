"""Parsing and structural profiling of an input circuit.

Reuses the project's existing Verilog/BENCH parsers and graph analysis
rather than introducing a second representation of a circuit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyatpg.core.graph_analysis import CircuitGraph, DEFAULT_TOGGLE_SEED
from pyatpg.core.netlist import Circuit, NetlistParser
from pyatpg.core.verilog_parser import VerilogParser

_VERILOG_SUFFIXES = (".v", ".verilog")
_BENCH_SUFFIXES = (".bench",)


@dataclass
class ParsedCircuit:
    """An input circuit plus the source text it came from."""

    circuit: Circuit
    source_text: str
    filename: str
    name: str
    #: Signals referenced by a gate/flip-flop but never driven by anything
    #: (not a declared input, gate output, or flip-flop Q) -- promoted to
    #: free primary inputs during parsing. See
    #: Circuit.promote_undefined_signals_to_inputs for why.
    implicit_inputs: list[str] = None  # type: ignore[assignment]
    #: Remarks from the parser about how the netlist was interpreted (e.g.
    #: constant-tied outputs dropped), surfaced to the user as run notes.
    parse_notes: list[str] = None  # type: ignore[assignment]
    #: True when ``circuit`` was derived from the source (e.g. its full-scan
    #: model), so the source text no longer describes it.
    derived: bool = False

    def __post_init__(self) -> None:
        if self.implicit_inputs is None:
            self.implicit_inputs = []
        if self.parse_notes is None:
            self.parse_notes = []

    @property
    def is_bench(self) -> bool:
        return self.filename.lower().endswith(_BENCH_SUFFIXES)

    def to_bench(self) -> str:
        """The circuit in ISCAS .bench form, which is what Atalanta reads.

        BENCH input is passed through untouched -- so the netlist Atalanta
        sees is byte-identical to what the user supplied -- *unless* parsing
        found undefined signals, in which case that guarantee would hide a
        net Atalanta will fail on anyway; regenerating from the (now
        input-promoted) circuit is the only way it actually gets one.
        """
        if self.is_bench and not self.implicit_inputs and not self.derived:
            return self.source_text
        return self.circuit.to_bench()


@dataclass
class StructuralProfile:
    """Graph-derived statistics and per-gate features."""

    statistics: dict[str, Any]
    gate_profiles: list[dict[str, Any]]
    graph: CircuitGraph
    #: Stimulus seed and pattern count the toggle rates were measured with.
    #: Recorded so a run's gate ranking can be reproduced exactly.
    toggle_seed: int | None = DEFAULT_TOGGLE_SEED
    toggle_patterns: int = 3000
    #: Wall-clock seconds spent computing SCOAP/toggle/centrality/topology
    #: (the CircuitGraph build, get_stats, compute_toggle_rates and
    #: to_llm_feature_profiles calls in profile_circuit below). A
    #: non-targeted run pays this cost too -- it is computed unconditionally,
    #: for the structural trojan-shape detector and the circuit payload
    #: stats -- but a non-targeted run never uses the *ranking* those
    #: features would drive, so this is reported separately rather than
    #: folded into either run's Atalanta timing.
    extraction_time_s: float = 0.0


def parse_circuit(source_text: str, filename: str) -> ParsedCircuit:
    """Parse Verilog or BENCH source into a :class:`Circuit`.

    Raises:
        ValueError: for an unsupported extension.
    """
    lowered = (filename or "").lower()
    if lowered.endswith(_VERILOG_SUFFIXES):
        parser: VerilogParser | NetlistParser = VerilogParser()
    elif lowered.endswith(_BENCH_SUFFIXES):
        parser = NetlistParser()
    else:
        raise ValueError(
            f"Unsupported circuit format: {filename!r}. Provide a .v, .verilog or .bench file."
        )

    circuit = parser.parse(source_text)
    stem = Path(filename).stem
    if stem.endswith("_mod"):
        stem = stem[:-4]
    circuit.name = stem
    implicit_inputs = circuit.promote_undefined_signals_to_inputs()
    return ParsedCircuit(
        circuit=circuit,
        source_text=source_text,
        filename=filename,
        name=stem,
        implicit_inputs=implicit_inputs,
        parse_notes=list(getattr(parser, "notes", [])),
    )


def profile_circuit(
    parsed: ParsedCircuit,
    toggle_patterns: int = 3000,
    toggle_seed: int | None = DEFAULT_TOGGLE_SEED,
) -> StructuralProfile:
    """Compute SCOAP, topology and toggle-rate features for every gate.

    ``toggle_seed`` fixes the toggle-rate stimulus so the resulting gate
    ranking is reproducible; pass None for nondeterministic sampling.
    """
    started = time.perf_counter()
    graph = CircuitGraph(parsed.circuit)
    statistics = graph.get_stats()
    graph.compute_toggle_rates(num_patterns=toggle_patterns, seed=toggle_seed)
    gate_profiles = graph.to_llm_feature_profiles()
    elapsed = time.perf_counter() - started
    return StructuralProfile(
        statistics=statistics,
        gate_profiles=gate_profiles,
        graph=graph,
        toggle_seed=toggle_seed,
        toggle_patterns=toggle_patterns,
        extraction_time_s=elapsed,
    )
