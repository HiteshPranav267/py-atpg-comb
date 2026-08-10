"""Parsing and structural profiling of an input circuit.

Reuses the project's existing Verilog/BENCH parsers and graph analysis
rather than introducing a second representation of a circuit.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyatpg.core.graph_analysis import CircuitGraph
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

    @property
    def is_bench(self) -> bool:
        return self.filename.lower().endswith(_BENCH_SUFFIXES)

    def to_bench(self) -> str:
        """The circuit in ISCAS .bench form, which is what Atalanta reads.

        BENCH input is passed through untouched so the netlist Atalanta sees
        is byte-identical to what the user supplied.
        """
        return self.source_text if self.is_bench else self.circuit.to_bench()


@dataclass
class StructuralProfile:
    """Graph-derived statistics and per-gate features."""

    statistics: dict[str, Any]
    gate_profiles: list[dict[str, Any]]
    graph: CircuitGraph


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
    return ParsedCircuit(
        circuit=circuit,
        source_text=source_text,
        filename=filename,
        name=stem,
    )


def profile_circuit(parsed: ParsedCircuit, toggle_patterns: int = 3000) -> StructuralProfile:
    """Compute SCOAP, topology and toggle-rate features for every gate."""
    graph = CircuitGraph(parsed.circuit)
    statistics = graph.get_stats()
    graph.compute_toggle_rates(num_patterns=toggle_patterns)
    return StructuralProfile(
        statistics=statistics,
        gate_profiles=graph.to_llm_feature_profiles(),
        graph=graph,
    )
