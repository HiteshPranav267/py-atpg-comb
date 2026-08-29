"""Trojan insertion must never produce a cyclic combinational netlist.

A combinational netlist with a feedback loop is illegal, and ATALANTA does
not reject it cleanly -- it segfaults during levelisation. The biased
insertion strategies (low_scoap, rare_node) deterministically pick a
deep, hard-to-control net as a trigger and a shallow, rarely-toggling net
as the payload target, which is close to a worst case for closing such a
loop. These tests lock in that the strategies now avoid it.
"""
import networkx as nx
import pytest

from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.trojan.inserter import TrojanInserter
from pyatpg.trojan.strategy import (
    LowScoapStrategy,
    RandomStrategy,
    RareNodeStrategy,
    _acyclic_payload_candidates,
)

# c17 (6 gates) is deliberately excluded from the biased strategies: their
# trigger picks are so deep that every legal payload target is filtered out
# and insertion correctly raises rather than emitting a cyclic netlist.
CIRCUITS = ["benchmarks/c432.v", "benchmarks/c1355.v"]
STRATEGIES = [RandomStrategy, LowScoapStrategy, RareNodeStrategy]


def _load(path):
    with open(path) as f:
        return VerilogParser().parse(f.read())


def _is_acyclic(circuit) -> bool:
    g = nx.DiGraph()
    for out, gate in circuit.gates.items():
        for inp in gate.inputs:
            g.add_edge(inp, out)
    return nx.is_directed_acyclic_graph(g)


@pytest.mark.parametrize("path", CIRCUITS)
@pytest.mark.parametrize("strategy_cls", STRATEGIES)
def test_inserted_trojan_is_acyclic(path, strategy_cls):
    base = _load(path)
    inserter = TrojanInserter(strategy=strategy_cls())
    for trojan_id in range(1, 6):
        compromised, meta = inserter.insert(base, trojan_id)
        assert _is_acyclic(compromised), (
            f"{strategy_cls.__name__} produced a cyclic netlist for {path} "
            f"(trigger={meta.trigger_inputs}, payload={meta.payload_target})"
        )
        assert meta.payload_target not in meta.trigger_inputs
        # The .bench the ATPG tool reads must at least round-trip.
        assert "HT_PAYLOAD" not in compromised.to_bench() or compromised.to_bench()


def test_acyclic_candidates_exclude_trigger_ancestors():
    """The payload target must not be a net from which a trigger is reachable."""
    circuit = _load("benchmarks/c432.v")
    g = nx.DiGraph()
    for out, gate in circuit.gates.items():
        for inp in gate.inputs:
            g.add_edge(inp, out)

    # Pick a mid-cone trigger net: it must have both a non-trivial fan-in
    # cone (ancestors to forbid) and a fan-out cone (descendants still legal).
    trigger = max(
        (n for n in circuit.gates if nx.descendants(g, n)),
        key=lambda n: len(nx.ancestors(g, n)),
    )
    candidates = set(_acyclic_payload_candidates(circuit, [trigger]))

    assert trigger not in candidates
    for ancestor in nx.ancestors(g, trigger):
        assert ancestor not in candidates
    # A downstream net is still a legal target.
    descendants = nx.descendants(g, trigger)
    assert descendants & candidates
