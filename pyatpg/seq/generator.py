"""Non-search candidate sources: random sequences and mutations of winners.

Kept separate from PODEM so the engine can freely mix candidate sources
(PODEM, random, mutated-successful, LLM-proposed) and treat them uniformly.
"""
from __future__ import annotations

import random

from pyatpg.core.netlist import Circuit

from .models import TestSequence


def random_sequence(circuit: Circuit, cycles: int, rng: random.Random) -> TestSequence:
    inputs = sorted(circuit.inputs)
    vectors = [{pi: rng.randint(0, 1) for pi in inputs} for _ in range(cycles)]
    return TestSequence(vectors=vectors, origin="random")


def mutate(sequence: TestSequence, rng: random.Random, flip_rate: float = 0.12) -> TestSequence:
    """Flip a fraction of bits across the sequence -- a cheap local search step."""
    new_vectors = []
    for vector in sequence.vectors:
        new_vectors.append(
            {pi: (1 - v if rng.random() < flip_rate else v) for pi, v in vector.items()}
        )
    return TestSequence(vectors=new_vectors, origin="mutation")


def random_pool(
    circuit: Circuit, count: int, cycles: int, rng: random.Random
) -> list[TestSequence]:
    return [random_sequence(circuit, cycles, rng) for _ in range(count)]
