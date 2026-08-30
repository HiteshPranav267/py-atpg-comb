"""Pre-flight validation of the .bench text handed to Atalanta.

Atalanta rejects most malformed netlists cleanly (a "Fatal error" on stderr),
but a *cyclic* combinational netlist is the exception: it walks off the end of
its levelisation pass and dies with SIGSEGV -- exit code 139 on Linux, or
0xC0000005 / 3221225477 on Windows. All the caller ever sees is an opaque
"exited with code 3221225477", which is what a user reports as "segmentation
fault".

The check therefore lives here, on the exact text Atalanta is about to read,
rather than on some upstream in-memory Circuit. There are two independent
entry points into :class:`~pyatpg.atpg.runner.AtalantaRunner` -- the FastAPI
``AtpgAnalysisService`` and the Flask desktop app's ``/api/jobs/dispatch`` --
and only the first one carried a DAG guard, so a cyclic netlist loaded in the
desktop app reached the engine unchecked. Validating the bench text closes
that gap for every caller, present and future, and cannot drift from what the
engine sees the way a check on a separately-parsed Circuit can.
"""
from __future__ import annotations

import re

#: `<output> = <GATE>(<in>, <in>, ...)` -- the only line form that makes an edge.
_GATE_LINE = re.compile(r"^\s*([^\s=#]+)\s*=\s*([A-Za-z_]\w*)\s*\((.*)\)\s*$")

#: `INPUT(x)` / `OUTPUT(x)`, case-insensitively, as Atalanta accepts them.
_PORT_LINE = re.compile(r"^\s*(INPUT|OUTPUT)\s*\(\s*([^)]*?)\s*\)\s*$", re.IGNORECASE)

#: Gate keywords that are state elements, not same-cycle combinational logic.
_STATE_GATES = {"DFF", "SDFF", "LATCH"}


class CyclicNetlistError(ValueError):
    """The netlist has a combinational feedback loop Atalanta would crash on."""


def find_combinational_cycle(bench_text: str) -> list[str] | None:
    """Return one combinational feedback loop in ``bench_text``, or None.

    The loop is returned as the list of nets on it, in order, with the entry
    net repeated at the end -- enough for an error message to name the actual
    problem instead of just asserting one exists.

    A flip-flop's output is treated as a free source (its value comes from the
    previous clock cycle, not from this one), matching
    ``pyatpg.seq.timeframe.is_combinational_part_acyclic``. Without that, the
    ordinary register feedback present in essentially every sequential circuit
    would read as a cycle.
    """
    drivers: dict[str, list[str]] = {}
    for raw in bench_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or _PORT_LINE.match(line):
            continue
        match = _GATE_LINE.match(line)
        if not match:
            continue
        output, gate_type, args = match.group(1), match.group(2).upper(), match.group(3)
        if gate_type in _STATE_GATES:
            continue  # Q is a free source; its D edge is next-cycle, not this one.
        drivers[output] = [a.strip() for a in args.split(",") if a.strip()]

    # Iterative DFS with an explicit stack: these netlists reach tens of
    # thousands of gates, well past Python's recursion limit.
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}

    for root in drivers:
        if colour.get(root, WHITE) != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        colour[root] = GREY
        path.append(root)
        while stack:
            node, index = stack[-1]
            inputs = drivers.get(node, ())
            if index < len(inputs):
                stack[-1] = (node, index + 1)
                nxt = inputs[index]
                if nxt not in drivers:
                    continue  # a primary input or undriven net: not on any cycle
                state = colour.get(nxt, WHITE)
                if state == GREY:
                    return path[path.index(nxt):] + [nxt]
                if state == BLACK:
                    continue
                colour[nxt] = GREY
                path.append(nxt)
                stack.append((nxt, 0))
            else:
                colour[node] = BLACK
                stack.pop()
                path.pop()
    return None


def assert_atalanta_safe(bench_text: str, label: str = "this netlist") -> None:
    """Raise if ``bench_text`` would crash Atalanta rather than be rejected.

    Raises:
        CyclicNetlistError: the netlist contains a combinational feedback loop.
    """
    cycle = find_combinational_cycle(bench_text)
    if cycle is None:
        return
    shown = " -> ".join(cycle[:8]) + (" -> ..." if len(cycle) > 8 else "")
    raise CyclicNetlistError(
        f"The netlist for {label} contains a combinational feedback loop "
        f"({shown}) with no flip-flop breaking it. A combinational ATPG engine "
        "cannot levelize such a circuit -- Atalanta crashes on it rather than "
        "reporting an error -- so the run was stopped before it was invoked."
    )
