"""Service layer for circuit operations."""
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.core.netlist import Circuit
from pyatpg.core.graph_analysis import CircuitGraph
from pyatpg.analysis.feature_extractor import FeatureExtractor
from pyatpg.analysis.adapters import AtalantaAdapter, MetadataAdapter
from pyatpg.analysis.models import FeatureSet
from typing import Dict, Any, Optional


class CircuitService:
    """Stateless service for parsing and analyzing circuits."""

    @staticmethod
    def parse_verilog(text: str, name: str = "unknown") -> Dict[str, Any]:
        parser = VerilogParser()
        circuit = parser.parse(text)
        circuit.name = name
        cg = CircuitGraph(circuit)
        stats = cg.get_stats()

        return {
            "name": name,
            "total_nodes": stats.get("total_nodes", 0),
            "gates": stats.get("gates", 0),
            "primary_inputs": stats.get("primary_inputs", 0),
            "primary_outputs": stats.get("primary_outputs", 0),
            "levels": stats.get("levels", 0),
        }

    @staticmethod
    def parse_to_circuit(text: str, name: str = "unknown") -> Circuit:
        parser = VerilogParser()
        circuit = parser.parse(text)
        circuit.name = name
        return circuit
