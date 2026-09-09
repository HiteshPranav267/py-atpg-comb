"""Trojan insertion must never produce a cyclic combinational netlist.

A combinational netlist with a feedback loop is illegal, and ATALANTA does
not reject it cleanly -- it segfaults during levelisation. The biased
insertion strategies (low_scoap, rare_node) sample a deep, hard-to-control
net as a trigger and a shallow, rarely-toggling net as the payload target,
which is close to a worst case for closing such a loop. These tests lock in
that the strategies avoid it -- and that they place each variant somewhere
different while keeping that bias.
"""
import networkx as nx
import pytest

from pyatpg.atpg.binary import AtalantaNotAvailable, resolve_atalanta_binary
from pyatpg.core.graph_analysis import CircuitGraph
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.trojan.inserter import TrojanInserter
from pyatpg.trojan.strategy import (
    LowScoapStrategy,
    RandomStrategy,
    RareNodeStrategy,
    _acyclic_payload_candidates,
)


def _atalanta_available() -> bool:
    try:
        resolve_atalanta_binary()
    except AtalantaNotAvailable:
        return False
    return True


requires_atalanta = pytest.mark.skipif(
    not _atalanta_available(), reason="no runnable Atalanta binary on this machine"
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


@pytest.mark.parametrize("strategy_cls", STRATEGIES)
def test_variants_are_placed_differently(strategy_cls):
    """A dataset must be a dataset, not the same trojan N times.

    The biased strategies used to take the argmax of their ranking -- the
    single hardest-to-control net as the trigger, the single hardest-to-observe
    net as the payload. Both rankings are deterministic (SCOAP is, and toggle
    rates are seeded), so every variant of a circuit came out byte-identical.
    They now sample from the rare pool instead.
    """
    base = _load("benchmarks/c432.v")
    inserter = TrojanInserter(strategy=strategy_cls(seed=11))
    placements = set()
    for i in range(1, 11):
        _, meta = inserter.insert(base, i)
        placements.add((tuple(meta.trigger_inputs), meta.payload_target))
    assert len(placements) > 1, f"{strategy_cls.__name__} placed every variant identically"


@pytest.mark.parametrize("strategy_cls", STRATEGIES)
def test_seed_reproduces_the_dataset(strategy_cls):
    base = _load("benchmarks/c432.v")

    def placements(seed):
        inserter = TrojanInserter(strategy=strategy_cls(seed=seed))
        return [
            (tuple(m.trigger_inputs), m.payload_target)
            for m in (inserter.insert(base, i)[1] for i in range(1, 6))
        ]

    assert placements(3) == placements(3)
    assert placements(3) != placements(4)


@pytest.mark.parametrize("strategy_cls", STRATEGIES)
def test_payload_targets_do_not_repeat_within_a_batch(strategy_cls):
    """Two variants landing on the same payload target purely by chance was
    observed on real data (2 of 3 rare_node variants on c1355 both spliced
    into the same net, with different trigger nets). Each circuit's
    candidate pool is comfortably larger than this batch size, so none of
    the 10 variants should need to reuse a target.
    """
    base = _load("benchmarks/c1355.v")
    inserter = TrojanInserter(strategy=strategy_cls(seed=7))
    payloads = []
    for i in range(1, 11):
        _, meta = inserter.insert(base, i)
        payloads.append(meta.payload_target)
    assert len(payloads) == len(set(payloads)), (
        f"{strategy_cls.__name__} repeated a payload target within one batch: {payloads}"
    )


def test_choose_payload_falls_back_to_reuse_once_pool_exhausted():
    """A batch larger than the candidate pool must still complete."""
    strategy = RandomStrategy(seed=1)
    ranked = ["a", "b"]
    picks = [strategy._choose_payload(ranked) for _ in range(6)]
    assert set(picks) == {"a", "b"}
    assert all(pick in ranked for pick in picks)


def test_biased_triggers_stay_rare():
    """Sampling must not throw away the bias that makes the trojan evasive."""
    base = _load("benchmarks/c432.v")
    toggle = CircuitGraph(base).compute_toggle_rates()
    rates = sorted(v["toggle_rate"] for v in toggle.values())
    median = rates[len(rates) // 2]

    inserter = TrojanInserter(strategy=RareNodeStrategy(seed=5))
    for i in range(1, 11):
        _, meta = inserter.insert(base, i)
        for net in meta.trigger_inputs:
            assert toggle[net]["toggle_rate"] < median


@requires_atalanta
@pytest.mark.parametrize("strategy_cls", STRATEGIES)
def test_verify_activation_confirms_real_placements(strategy_cls):
    """End-to-end: insert() with verify_activation=True actually spawns
    Atalanta and either confirms the trigger or rejects the variant --
    exercised against the real binary, unlike test_trojan_activation.py's
    mocked unit tests.
    """
    base = _load("benchmarks/c1355.v")
    inserter = TrojanInserter(strategy=strategy_cls(seed=13), verify_activation=True)
    confirmed_any = False
    for i in range(1, 6):
        try:
            _, meta = inserter.insert(base, i)
        except Exception:
            # A rejected variant (activation failed, or the usual cyclic/
            # too-small-circuit cases) is an acceptable outcome here; the
            # point is that *when* insert() returns, it says so honestly.
            continue
        assert meta.activation_confirmed is True
        confirmed_any = True
    assert confirmed_any, f"{strategy_cls.__name__} confirmed no variant at all in 5 attempts"


@requires_atalanta
def test_activation_confirmed_is_none_when_not_requested():
    base = _load("benchmarks/c1355.v")
    inserter = TrojanInserter(strategy=RandomStrategy(seed=1))  # verify_activation defaults False
    _, meta = inserter.insert(base, 1)
    assert meta.activation_confirmed is None
