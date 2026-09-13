"""Whole-circuit fault simulation of an existing pattern set.

The targeted run's own coverage is a percentage of the handful of faults it
was given. What its patterns detect across the *whole* circuit is a
different, and for comparison the only honest, number -- and it must come
from simulating the patterns, never from Atalanta's per-list figure.
"""
from pyatpg.atpg.faultsim import simulate_pattern_coverage
# Aliased so pytest does not try to collect the Test* dataclasses.
from pyatpg.atpg.models import TestVector as Vector, TestVectorSet as VectorSet
from pyatpg.atpg.structural import parse_circuit

# z = AND(a, b): faults a/0, b/0, z/0 need (1,1); a/1 needs (0,1); b/1
# needs (1,0); z/1 needs any pattern with z=0.
AND2 = """
INPUT(a)
INPUT(b)
OUTPUT(z)
z = AND(a, b)
"""


def _vectors(rows, pis=("a", "b"), pos=("z",)):
    vs = VectorSet(primary_inputs=list(pis), primary_outputs=list(pos))
    for i, (inputs, outputs) in enumerate(rows):
        vs.vectors.append(Vector(index=i + 1, inputs=inputs, outputs=outputs))
    return vs


def _circuit(text=AND2):
    return parse_circuit(text, "t.bench").circuit


def test_exhaustive_patterns_detect_every_fault():
    result = simulate_pattern_coverage(
        _circuit(), _vectors([("11", "1"), ("01", "0"), ("10", "0")])
    )
    assert result.unavailable_reason is None
    assert result.total_faults == 6
    assert result.detected_faults == 6
    assert result.coverage == 100.0


def test_single_pattern_detects_only_what_it_can():
    # (1,1) alone: detects a/0, b/0, z/0 -- and nothing stuck at 1.
    result = simulate_pattern_coverage(_circuit(), _vectors([("11", "1")]))
    assert result.detected_faults == 3
    assert result.coverage == 50.0


def test_fault_free_mismatch_with_recorded_response_is_reported_not_scored():
    """A pattern file whose expected outputs disagree with our simulation
    means we are not looking at the circuit Atalanta ran; say so."""
    result = simulate_pattern_coverage(_circuit(), _vectors([("11", "0")]))
    assert result.coverage is None
    assert "disagrees" in (result.unavailable_reason or "")


def test_unknown_signal_names_are_reported():
    result = simulate_pattern_coverage(_circuit(), _vectors([("11", "1")], pis=("a", "q")))
    assert result.coverage is None
    assert "q" in (result.unavailable_reason or "")


def test_empty_pattern_set_is_unavailable():
    result = simulate_pattern_coverage(_circuit(), VectorSet())
    assert result.coverage is None
    assert result.patterns == 0


def test_agrees_with_brute_force_on_a_deeper_circuit():
    """Cross-check the event-driven bit-parallel simulator against a naive
    one-fault-one-pattern simulation on a small multi-level circuit."""
    import itertools

    from pyatpg.core.simulator import Simulator

    text = """
INPUT(a)
INPUT(b)
INPUT(c)
INPUT(d)
OUTPUT(y)
OUTPUT(w)
n1 = NAND(a, b)
n2 = NOR(c, d)
n3 = XOR(n1, n2)
y = AND(n3, a)
w = NOT(n2)
"""
    circuit = _circuit(text)
    pis = sorted(circuit.inputs)
    pos = sorted(circuit.outputs)
    sim = Simulator(circuit)
    # Simulator.evaluate orders by circuit.inputs/outputs iteration order.
    in_order, out_order = list(circuit.inputs), list(circuit.outputs)

    patterns = ["0110", "1001", "1111", "0000", "1010"]
    rows = []
    for bits in patterns:
        by_name = dict(zip(pis, bits))
        outs = sim.evaluate([int(by_name[n]) for n in in_order])
        out_by_name = dict(zip(out_order, outs))
        rows.append((bits, "".join(str(out_by_name[n]) for n in pos)))

    result = simulate_pattern_coverage(circuit, _vectors(rows, pis=pis, pos=pos))
    assert result.unavailable_reason is None

    # Brute force: inject each fault by rewriting the circuit's evaluation.
    def faulty_outputs(site, stuck, bits):
        values = dict(zip(pis, (int(b) for b in bits)))
        if site in values:
            values[site] = stuck
        unresolved = dict(circuit.gates)
        while unresolved:
            for name, g in list(unresolved.items()):
                if any(i not in values for i in g.inputs):
                    continue
                ins = [values[i] for i in g.inputs]
                v = {
                    "AND": int(all(ins)), "OR": int(any(ins)), "NAND": int(not all(ins)),
                    "NOR": int(not any(ins)), "NOT": int(not ins[0]), "XOR": sum(ins) % 2,
                }[g.gtype]
                values[name] = stuck if name == site else v
                del unresolved[name]
        return tuple(values[o] for o in pos)

    sites = sorted(set(circuit.gates) | set(pis))
    expected = 0
    for site, stuck in itertools.product(sites, (0, 1)):
        good = [faulty_outputs("__none__", 0, b) for b in patterns]
        bad = [faulty_outputs(site, stuck, b) for b in patterns]
        if any(g != f for g, f in zip(good, bad)):
            expected += 1
    assert result.total_faults == 2 * len(sites)
    assert result.detected_faults == expected
