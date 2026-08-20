"""Tests for the sequential (time-frame-expansion) ATPG engine.

No LLM calls happen anywhere here -- these pin the deterministic path
(:mod:`pyatpg.seq`), which is required to work standalone and to be the
source of truth even when the LLM layer is enabled. The LLM layer itself is
tested against a stub provider so no network/API key is needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pyatpg.core.netlist import Circuit, FlipFlop, NetlistParser
from pyatpg.seq import dalgebra as da
from pyatpg.seq.engine import EngineConfig, SequentialAtpgEngine
from pyatpg.seq.faultsim import checkpoint_fault_list, evaluate_coverage
from pyatpg.seq.models import SequentialFault, TestSequence, build_sequential_comparison
from pyatpg.seq.podem import podem
from pyatpg.seq.simulator import simulate, traces_diverge
from pyatpg.seq.timeframe import build_unrolled_circuit, is_combinational_part_acyclic

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"


def _small_shift_register() -> Circuit:
    """A minimal sequential circuit: PI -> AND with FF Q -> FF D, PO = FF Q.

    G3 = AND(A, G1)   where G1 is a flip-flop's Q output
    G1 = DFF(G3)
    OUTPUT(G1)
    """
    c = Circuit()
    c.add_input("A")
    c.add_output("G1")
    c.add_gate("AND", "G3", ["A", "G1"])
    c.add_flipflop(FlipFlop(q="G1", d="G3"))
    return c


# --------------------------------------------------------------- dalgebra
def test_dalgebra_and_gate_truth_table():
    assert da.eval3("AND", [0, 0]) == 0
    assert da.eval3("AND", [1, da.X]) == da.X
    assert da.eval3("AND", [1, 1]) == 1


def test_dalgebra_pair_produces_d_and_dbar():
    # good=1, faulty=0 (stuck-at-0 downstream of a activated site) is D.
    pair = da.eval_pair("BUF", [(1, 0)])
    assert pair == da.D
    assert da.has_fault_effect(pair)


def test_dalgebra_unknown_is_not_a_fault_effect():
    assert not da.has_fault_effect(da.UNKNOWN)
    assert not da.has_fault_effect(da.ONE)


# -------------------------------------------------------------- timeframe
def test_circuit_graph_levels_and_scoap_ignore_register_feedback():
    """The same false-cycle problem also silently broke CircuitGraph's own
    compute_levels()/compute_scoap() for any sequential circuit with
    register feedback: nx.topological_sort(self.G) raised NetworkXUnfeasible,
    so levels fell back to all-zero and SCOAP fell back to an arbitrary
    (non-topological) node order, corrupting cc0/cc1/co for every gate."""
    from pyatpg.core.graph_analysis import CircuitGraph

    circuit = NetlistParser().parse((BENCHMARKS / "s27.bench").read_text())
    graph = CircuitGraph(circuit)
    stats = graph.get_stats()
    assert stats["is_dag"] is True
    assert stats["max_level"] > 0  # was silently 0 before the fix

    cc0, cc1, co = graph.compute_scoap()
    # G17 = NOT(G11) is the sole PO -- observability of the circuit's only
    # output must be 0 (trivially observable), not some arbitrary fallback.
    assert co["G17"] == 0
    assert cc1["G17"] >= 1


def test_promote_undefined_signals_to_inputs():
    c = Circuit()
    c.add_input("A")
    c.add_output("Y")
    c.add_gate("AND", "Y", ["A", "GHOST"])  # GHOST is never defined anywhere
    promoted = c.promote_undefined_signals_to_inputs()
    assert promoted == ["GHOST"]
    assert "GHOST" in c.inputs
    assert is_combinational_part_acyclic(c)


def test_promote_undefined_signals_is_a_noop_when_everything_is_defined():
    circuit = _small_shift_register()
    assert circuit.promote_undefined_signals_to_inputs() == []


def test_s400_dangling_clock_net_is_not_reported_as_a_feedback_loop():
    """Regression: s400.bench/s400.v reference Phi1H, a clock-phase wire
    that is declared but never driven by any gate -- a data-quality issue
    in the benchmark files themselves (lost, presumably, when the original
    latch-based ISCAS-89 circuit was flattened), not a real unbroken
    combinational feedback loop. Before Circuit.promote_undefined_signals_
    to_inputs, an undefined signal and a genuine async latch loop were
    structurally indistinguishable -- both just never resolved during a
    topological pass -- so s400 was wrongly rejected with a misleading
    "combinational feedback loop" error."""
    from pyatpg.atpg.structural import parse_circuit

    parsed = parse_circuit((BENCHMARKS / "s400.bench").read_text(), "s400.bench")
    assert parsed.implicit_inputs == ["Phi1H"]
    assert is_combinational_part_acyclic(parsed.circuit)


def test_register_feedback_is_not_a_combinational_cycle():
    """s27 has ordinary feedback through its flip-flops (G5/G6/G7 feed logic
    that drives their own D inputs) -- CircuitGraph.get_stats()['is_dag']
    flags that as cyclic because it doesn't distinguish "next cycle" from
    "this cycle", which would make sequential ATPG reject nearly every real
    circuit. is_combinational_part_acyclic must correctly accept it."""
    from pyatpg.seq.timeframe import is_combinational_part_acyclic

    circuit = NetlistParser().parse((BENCHMARKS / "s27.bench").read_text())
    assert is_combinational_part_acyclic(circuit)


def test_unrolling_frame_0_ff_outputs_are_free_inputs():
    circuit = _small_shift_register()
    unrolled = build_unrolled_circuit(circuit, n_frames=2)

    assert "f0_G1" in unrolled.circuit.inputs  # frame-0 pseudo-PI (free state)
    assert unrolled.state_pseudo_inputs["f0_G1"] == "G1"
    # frame-1's FF output is NOT a free input -- it's aliased to frame 0's D net.
    assert "f1_G1" not in unrolled.circuit.inputs
    assert "f0_A" in unrolled.circuit.inputs
    assert "f1_A" in unrolled.circuit.inputs
    assert unrolled.pi_frames["f1_A"] == (1, "A")


def test_unrolling_exposes_a_po_copy_per_frame():
    circuit = _small_shift_register()
    unrolled = build_unrolled_circuit(circuit, n_frames=3)
    # 3 frames -> 3 PO copies, one per cycle. Frame k>0's "G1" (a flip-flop
    # Q) is aliased to frame k-1's D net rather than getting its own name
    # (see test_unrolling_frame_0_ff_outputs_are_free_inputs), so the PO
    # names below are the *resolved* (aliased) unrolled names.
    assert len(unrolled.po_frames) == 3
    assert {frame for frame, _ in unrolled.po_frames.values()} == {0, 1, 2}
    assert all(orig == "G1" for _frame, orig in unrolled.po_frames.values())


# ------------------------------------------------------------------ podem
def test_podem_finds_sequence_for_reachable_fault():
    circuit = _small_shift_register()
    # Two frames: G3 (frame 0's AND output / FF input) becomes frame 1's FF
    # output, which is a primary output copy -- so this fault needs a second
    # cycle to become observable, exactly the case single-frame combinational
    # search cannot handle.
    unrolled = build_unrolled_circuit(circuit, n_frames=2)
    result = podem(unrolled.circuit, "f0_G3", 0, set(unrolled.po_frames.keys()))
    assert result.success
    assert result.pi_values.get("f0_A") == 1
    assert result.pi_values.get("f0_G1") == 1


# --------------------------------------------------------------- simulator
def test_simulator_resets_flipflops_to_zero_by_default():
    circuit = _small_shift_register()
    trace = simulate(circuit, [{"A": 1}])
    # G1 (Q) starts at 0, so G3 = AND(1, 0) = 0 on cycle 0.
    assert trace[0].primary_outputs["G1"] == 0
    assert trace[0].next_state["G1"] == 0


def test_simulator_propagates_state_across_cycles():
    circuit = _small_shift_register()
    trace = simulate(circuit, [{"A": 1}, {"A": 1}, {"A": 1}])
    # cycle0: Q=0 -> D=AND(1,0)=0 -> next Q=0
    # cycle1: Q=0 -> D=AND(1,0)=0 -> next Q=0 (A AND Q never turns on from 0)
    assert [c.next_state["G1"] for c in trace] == [0, 0, 0]


def test_fault_injection_forces_stuck_at_value():
    circuit = _small_shift_register()
    good = simulate(circuit, [{"A": 1}])
    faulty = simulate(circuit, [{"A": 1}], fault_node="G3", fault_stuck_at=1)
    assert good[0].primary_outputs["G1"] == 0
    assert faulty[0].next_state["G1"] == 1
    assert traces_diverge(good, faulty) is None  # divergence shows up in state, not yet at a PO
    faulty2 = simulate(circuit, [{"A": 1}, {"A": 0}], fault_node="G3", fault_stuck_at=1)
    good2 = simulate(circuit, [{"A": 1}, {"A": 0}])
    assert traces_diverge(good2, faulty2) == 1  # G1 output differs on cycle 1


# ------------------------------------------------------------------ engine
def test_checkpoint_fault_list_excludes_unreachable_inputs():
    """An input that drives nothing (e.g. a clock port declared as a plain
    `input CK` in structural Verilog, with clocking implicit in the
    per-cycle simulation model rather than modeled as a real signal) must
    not appear in the fault list -- a stuck-at fault on it can never be
    activated, so including it would silently cap coverage below 100% on
    an otherwise fully-covered circuit."""
    c = Circuit()
    c.add_input("A")
    c.add_input("CK")  # never referenced by any gate or flip-flop D
    c.add_output("Y")
    c.add_gate("BUF", "Y", ["A"])
    faults = checkpoint_fault_list(c)
    nodes = {f.node for f in faults}
    assert "CK" not in nodes
    assert nodes == {"A", "Y"}


def test_checkpoint_fault_list_covers_every_net_both_polarities():
    circuit = _small_shift_register()
    faults = checkpoint_fault_list(circuit)
    nodes = {f.node for f in faults}
    assert nodes == {"A", "G1", "G3"}
    assert len(faults) == 2 * len(nodes)


def test_evaluate_coverage_is_never_fabricated_for_empty_sequences():
    circuit = _small_shift_register()
    faults = checkpoint_fault_list(circuit)
    results, detections, undetected, delta = evaluate_coverage(circuit, faults, [])
    assert results.fault_coverage == 0.0
    assert detections == []
    assert len(undetected) == len(faults)


def test_engine_reaches_full_coverage_on_s27_without_llm():
    circuit = NetlistParser().parse((BENCHMARKS / "s27.bench").read_text())
    engine = SequentialAtpgEngine(
        circuit,
        circuit_name="s27",
        config=EngineConfig(target_coverage=100.0, max_sequences=60, base_cycles=4, random_pool_size=30),
    )
    result = engine.run()
    assert result.results.fault_coverage == 100.0
    assert result.undetected == []
    assert result.results.sequence_count > 0
    # every accepted sequence must have contributed at least one detection
    # (this is the minimisation property: no dead weight in the test set).
    assert all(result.newly_detected_by_sequence[i] for i in range(len(result.sequences)))


def test_comparison_shows_a_real_delta_when_the_ranker_unlocks_more_search():
    """s298 is a case where random-fill + PODEM alone plateau well below
    100% -- exactly the circuit a deterministic-vs-LLM-assisted comparison
    needs to be meaningful on. Uses a stub ranker (no network) standing in
    for the real LLM-2 call: the point of this test is the comparison
    plumbing, not the LLM's judgement, which pyatpg.llm's own tests cover."""
    circuit = NetlistParser().parse((BENCHMARKS / "s298.bench").read_text())
    config = EngineConfig(
        target_coverage=95.0, max_sequences=40, base_cycles=4,
        random_pool_size=15, podem_max_frames=3, podem_max_backtracks=40,
        rng_seed=1234,
    )

    baseline = SequentialAtpgEngine(circuit, circuit_name="s298", config=config).run()
    llm_assisted = SequentialAtpgEngine(
        circuit, circuit_name="s298", config=config, candidate_ranker=lambda faults, ctx: None
    ).run()

    comparison = build_sequential_comparison(baseline, llm_assisted)
    assert comparison.coverage_delta is not None and comparison.coverage_delta > 0
    assert comparison.additional_faults_detected
    assert set(comparison.additional_faults_detected) == set(llm_assisted.detected) - set(baseline.detected)
    # every "additional" fault must be independently reproducible via the
    # LLM-assisted run's own sequences -- never asserted from the delta alone.
    for fault_name in comparison.additional_faults_detected[:5]:
        node, stuck_at = fault_name.rsplit("/", 1)
        fault = SequentialFault(node=node, stuck_at=int(stuck_at))
        assert any(
            evaluate_coverage(circuit, [fault], [seq])[0].detected_faults == 1
            for seq in llm_assisted.sequences
        )


def test_engine_never_reports_fault_effects_beyond_the_simulator():
    """Guards against LLM (or any future candidate source) fabricating coverage:
    every detected fault must be independently reproducible by re-simulating
    the returned sequences with no other machinery involved."""
    circuit = NetlistParser().parse((BENCHMARKS / "s27.bench").read_text())
    engine = SequentialAtpgEngine(
        circuit,
        circuit_name="s27",
        config=EngineConfig(target_coverage=100.0, max_sequences=60, base_cycles=4, random_pool_size=30),
    )
    result = engine.run()

    all_faults = checkpoint_fault_list(circuit)
    by_name = {f.name: f for f in all_faults}
    for fault_name in result.detected:
        fault = by_name[fault_name]
        assert any(
            evaluate_coverage(circuit, [fault], [seq])[0].detected_faults == 1
            for seq in result.sequences
        )


# --------------------------------------------------------------------- LLM
class _StubProvider:
    """Deterministic stand-in for GeminiProvider -- no network involved."""

    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def test_llm_context_store_neighborhood_and_compaction_stay_small():
    from pyatpg.llm.store import LocalContextStore

    circuit = NetlistParser().parse((BENCHMARKS / "s27.bench").read_text())
    store = LocalContextStore.build(circuit)
    faults = checkpoint_fault_list(circuit)

    ctx = store.compact_fault_context(faults, max_faults=3)
    assert len(ctx["target_faults"]) <= 3
    # the compact context must never balloon to "the whole circuit":
    # far fewer entries than there are faults or gates in the netlist.
    assert len(ctx["neighborhood"]) < len(circuit.gates) + len(circuit.inputs)


def test_llm_fault_prioritizer_grounds_response_against_the_batch():
    from pyatpg.llm.store import LocalContextStore
    from pyatpg.llm.token_tracker import TokenTracker
    from pyatpg.llm.vector_generator import LLMFaultPrioritizer

    circuit = NetlistParser().parse((BENCHMARKS / "s27.bench").read_text())
    store = LocalContextStore.build(circuit)
    faults = checkpoint_fault_list(circuit)
    tracker = TokenTracker()

    batch = store.top_k_difficult(faults, 6)
    hallucinated = "G999999/0"
    real_name = batch[0].name
    provider = _StubProvider(response=f"{hallucinated}, {real_name}")

    prioritizer = LLMFaultPrioritizer(provider, store, tracker, circuit_summary="test circuit", max_faults_per_call=6)
    ranked = prioritizer.rank(faults, {})

    assert ranked is not None
    assert ranked[0].name == real_name  # honoured the valid part of the response
    assert all(f.name != hallucinated for f in ranked)  # hallucinated name dropped
    assert len(ranked) == len(faults)  # nothing lost, just reordered
    assert tracker.total_calls == 1


def test_llm_fault_prioritizer_returns_none_without_a_provider():
    from pyatpg.llm.store import LocalContextStore
    from pyatpg.llm.token_tracker import TokenTracker
    from pyatpg.llm.vector_generator import LLMFaultPrioritizer

    circuit = NetlistParser().parse((BENCHMARKS / "s27.bench").read_text())
    store = LocalContextStore.build(circuit)
    faults = checkpoint_fault_list(circuit)
    prioritizer = LLMFaultPrioritizer(None, store, TokenTracker(), circuit_summary="")
    assert prioritizer.rank(faults, {}) is None


def test_token_tracker_distinguishes_cached_from_real_calls():
    from pyatpg.llm.token_tracker import TokenTracker

    tracker = TokenTracker()
    tracker.record("llm1", "a prompt", "a response")
    tracker.record_cached("llm1")
    summary = tracker.summary()
    assert summary["api_calls"] == 1
    assert summary["cached_hits"] == 1
    assert summary["prompt_tokens_est"] > 0
