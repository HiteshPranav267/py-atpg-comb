"""5-value D-algebra (0, 1, X, D, D-bar) as good/faulty ternary pairs.

Rather than a symbolic truth table for D/D-bar (error prone to get right for
every gate type), a D-algebra value is represented as a pair
``(good, faulty)`` where each half is an ordinary ternary value in
``{0, 1, None}`` (``None`` == X). Evaluating a gate is then just evaluating
ordinary ternary logic twice -- once per half -- which is easy to get right
and easy to test. The pair recovers the usual symbols:

    0  = (0, 0)      1  = (1, 1)      X = (None, None)
    D  = (1, 0)      D' = (0, 1)

This is a standard equivalence (see Roth's original D-algorithm paper) and
is exactly what a parallel good/faulty simulator already computes -- the
only difference here is that inputs may also be X, which is what makes it
usable for implication during search.
"""
from __future__ import annotations

from typing import Any

X = None
Pair = tuple  # (good, faulty), each in {0, 1, X}

ZERO: Pair = (0, 0)
ONE: Pair = (1, 1)
UNKNOWN: Pair = (X, X)
D: Pair = (1, 0)
DBAR: Pair = (0, 1)


def make_pair(value) -> Pair:
    if isinstance(value, tuple):
        return value
    return (value, value)


def is_unknown(pair: Pair) -> bool:
    return pair[0] is X and pair[1] is X


def has_fault_effect(pair: Pair) -> bool:
    """True for D or D': the two halves disagree and neither is X."""
    g, f = pair
    return g is not X and f is not X and g != f


def invert3(v):
    return X if v is X else 1 - v


def eval3(gtype: str, ins: list) -> Any:
    """Ordinary 3-valued (0/1/X) evaluation of one gate type."""
    gtype = gtype.upper()
    if gtype == "AND":
        if any(v == 0 for v in ins):
            return 0
        if any(v is X for v in ins):
            return X
        return 1
    if gtype == "NAND":
        return invert3(eval3("AND", ins))
    if gtype == "OR":
        if any(v == 1 for v in ins):
            return 1
        if any(v is X for v in ins):
            return X
        return 0
    if gtype == "NOR":
        return invert3(eval3("OR", ins))
    if gtype in ("NOT",):
        return invert3(ins[0])
    if gtype in ("BUF", "BUFF"):
        return ins[0]
    if gtype == "XOR":
        if any(v is X for v in ins):
            return X
        parity = 0
        for v in ins:
            parity ^= v
        return parity
    raise ValueError(f"Unsupported gate type: {gtype}")


def eval_pair(gtype: str, input_pairs: list[Pair]) -> Pair:
    """Evaluate one gate under the D-algebra by evaluating both halves."""
    good = eval3(gtype, [p[0] for p in input_pairs])
    faulty = eval3(gtype, [p[1] for p in input_pairs])
    return (good, faulty)


def is_controlling_value(gtype: str) -> int | None:
    """The input value that alone determines a gate's output, if any."""
    gtype = gtype.upper()
    if gtype in ("AND", "NAND"):
        return 0
    if gtype in ("OR", "NOR"):
        return 1
    return None


def inverts(gtype: str) -> bool:
    return gtype.upper() in ("NAND", "NOR", "NOT")
