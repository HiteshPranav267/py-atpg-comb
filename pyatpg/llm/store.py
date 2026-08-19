"""Full local circuit/feature/fault/history store, with compact retrieval.

This is the "keep everything locally, send only what's relevant" half of
the context-reduction design. Every piece of data the project already
computes (SCOAP, fan-in/out, levels, toggle rate -- via
``CircuitGraph.get_gate_features``) plus every fault and every simulation
outcome is kept here in full. Nothing is ever discarded. What goes to an
LLM call is always a small slice built on demand by the ``compact_*``
methods below, never the store itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyatpg.core.graph_analysis import CircuitGraph
from pyatpg.core.netlist import Circuit

from ..seq.models import SequentialFault


@dataclass
class HistoryEntry:
    sequence_origin: str
    sequence_length: int
    newly_detected: list[str]
    involved_nodes: set[str]


@dataclass
class LocalContextStore:
    """Local, never-fully-transmitted state for one ATPG run."""

    circuit: Circuit
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    adjacency: dict[str, set[str]] = field(default_factory=dict)
    history: list[HistoryEntry] = field(default_factory=list)

    @classmethod
    def build(cls, circuit: Circuit) -> "LocalContextStore":
        graph = CircuitGraph(circuit)
        features = graph.get_gate_features()
        adjacency: dict[str, set[str]] = {n: set() for n in features}
        for out_name, gate in circuit.gates.items():
            for inp in gate.inputs:
                adjacency.setdefault(inp, set()).add(out_name)
                adjacency.setdefault(out_name, set()).add(inp)
        for ff in circuit.flipflops:
            adjacency.setdefault(ff.d, set()).add(ff.q)
            adjacency.setdefault(ff.q, set()).add(ff.d)
        return cls(circuit=circuit, features=features, adjacency=adjacency)

    # ---------------------------------------------------------- retrieval
    def neighborhood(self, node: str, radius: int = 1) -> list[str]:
        seen = {node}
        frontier = {node}
        for _ in range(radius):
            nxt: set[str] = set()
            for n in frontier:
                nxt |= self.adjacency.get(n, set())
            frontier = nxt - seen
            seen |= nxt
        seen.discard(node)
        return sorted(seen)

    def top_k_difficult(self, faults: list[SequentialFault], k: int) -> list[SequentialFault]:
        """Hardest-first: low controllability of the *required* activation
        value, and low observability -- the standard SCOAP difficulty proxy.
        """

        def difficulty(f: SequentialFault) -> float:
            feat = self.features.get(f.node, {})
            control = feat.get("cc1", 1) if f.stuck_at == 0 else feat.get("cc0", 1)
            observe = feat.get("co", 1)
            return control + observe

        return sorted(faults, key=difficulty, reverse=True)[:k]

    def record(self, origin: str, length: int, newly_detected: list[str], involved_nodes: set[str]) -> None:
        self.history.append(
            HistoryEntry(
                sequence_origin=origin,
                sequence_length=length,
                newly_detected=newly_detected,
                involved_nodes=involved_nodes,
            )
        )

    def relevant_history(self, nodes: set[str], limit: int = 3) -> list[HistoryEntry]:
        relevant = [h for h in self.history if h.involved_nodes & nodes]
        return relevant[-limit:]

    # -------------------------------------------------------- compact ctx
    def compact_fault_context(
        self, faults: list[SequentialFault], max_faults: int = 5, neighborhood_radius: int = 1
    ) -> dict[str, Any]:
        """Build the small, self-contained context object LLM-2 receives.

        Only the current target faults, their immediate circuit
        neighborhood, their own SCOAP/toggle metrics, and history entries
        that touched one of those same nodes -- never the whole circuit,
        fault list, or history.
        """
        targets = faults[:max_faults]
        nodes = {f.node for f in targets}
        neighborhood_nodes: set[str] = set()
        for node in nodes:
            neighborhood_nodes |= set(self.neighborhood(node, neighborhood_radius))
        neighborhood_nodes -= nodes

        fault_entries = []
        for f in targets:
            feat = self.features.get(f.node, {})
            fault_entries.append(
                {
                    "fault": f.name,
                    "node": f.node,
                    "gate_type": feat.get("gate_type"),
                    "cc0": feat.get("cc0"),
                    "cc1": feat.get("cc1"),
                    "co": feat.get("co"),
                    "level": feat.get("level"),
                    "toggle_rate": feat.get("toggle_rate"),
                }
            )

        neighbor_entries = [
            {"node": n, "gate_type": self.features.get(n, {}).get("gate_type")}
            for n in sorted(neighborhood_nodes)[:20]
        ]

        history = self.relevant_history(nodes)
        history_entries = [
            {
                "origin": h.sequence_origin,
                "length": h.sequence_length,
                "newly_detected": h.newly_detected[:5],
            }
            for h in history
        ]

        return {
            "target_faults": fault_entries,
            "neighborhood": neighbor_entries,
            "relevant_history": history_entries,
            "flip_flop_count": len(self.circuit.flipflops),
        }
