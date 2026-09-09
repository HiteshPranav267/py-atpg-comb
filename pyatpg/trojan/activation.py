"""Verifies a freshly-inserted trojan's trigger is actually activatable.

``TrojanInserter.insert()`` used to check only that the resulting netlist
stayed acyclic -- nothing confirmed the trigger's activation condition could
ever be satisfied. That gap was not theoretical: a diagnostic comparison of
six c1355 trojan variants found one (a ``rare_node`` placement) whose own
trigger fault came back *aborted* from a real targeted ATPG run, discovered
only by accident, after the fact. The RL-insertion literature this project's
strategies are grounded in (Sarihi et al., arXiv:2204.04350) gates every
candidate trojan on a PODEM activation check before accepting it; this module
is the equivalent for this pipeline, using Atalanta itself as the oracle
rather than reimplementing PODEM.
"""
from __future__ import annotations

from pyatpg.atpg.binary import resolve_atalanta_binary
from pyatpg.atpg.runner import AtalantaExecutionError, AtalantaRunner
from pyatpg.atpg.workspace import JobWorkspace
from pyatpg.core.netlist import Circuit

#: The stuck-at value whose *complement* is the trigger's activated ("on")
#: output. Detecting that stuck-at fault requires Atalanta to both justify
#: the on-value from the primary inputs and propagate it to a primary
#: output -- exactly the property a working trigger needs. AND/OR are "on"
#: at output=1 (checked via the s-a-0 fault); NAND/NOR invert that. XOR/XNOR
#: have no single controlling input combination -- not a fixed value the
#: trigger settles to when "activated" -- so they are out of scope for v1
#: and raise ActivationUnverifiable instead of guessing.
_ACTIVATING_FAULT_VALUE = {
    "AND": "0",
    "OR": "0",
    "NAND": "1",
    "NOR": "1",
}


class ActivationUnverifiable(RuntimeError):
    """This trigger gate type has no single activating fault to check."""


class TriggerNotActivatable(RuntimeError):
    """Atalanta could not prove the trigger's on-state is reachable and
    observable within the search budget used."""


def verify_trigger_activatable(
    circuit: Circuit,
    trigger_output: str,
    trigger_type: str,
    circuit_name: str = "trojan",
    backtrack_limit: int | None = None,
) -> None:
    """Raise unless Atalanta can activate and observe ``trigger_output``.

    ``circuit`` must be the *compromised* circuit (trigger and payload
    already spliced in) -- checking the clean circuit would prove nothing
    about whether the payload's splice point actually sensitizes a path to
    an output. Raises ``TriggerNotActivatable`` on anything other than a
    clean detection (aborted, redundant, or simply absent from the result),
    ``ActivationUnverifiable`` for a trigger type with no single activating
    fault, and lets ``AtalantaNotAvailable``/``AtalantaExecutionError``
    propagate -- a caller that asked for this guarantee should find out the
    engine couldn't supply it, not silently skip the check.
    """
    value = _ACTIVATING_FAULT_VALUE.get(trigger_type.upper())
    if value is None:
        raise ActivationUnverifiable(
            f"No single activating fault for trigger type {trigger_type!r}; "
            "activation verification only covers AND/OR/NAND/NOR triggers."
        )

    runner = AtalantaRunner(binary=resolve_atalanta_binary())
    workspace = JobWorkspace.create(circuit_name)
    fault = f"{trigger_output} /{value}"
    try:
        run = runner.run(
            workspace,
            circuit.to_bench(),
            label="activation-check",
            targeted_faults=[fault],
            targeted_gates=[trigger_output],
            backtrack_limit=backtrack_limit,
        )
    except AtalantaExecutionError as exc:
        raise TriggerNotActivatable(
            f"Could not verify trigger {trigger_output!r}: Atalanta failed ({exc})."
        ) from exc
    finally:
        workspace.destroy()

    if run.results.detected_faults != 1:
        statuses = sorted({f.status for f in run.faults}) or ["not reported"]
        raise TriggerNotActivatable(
            f"Trigger {trigger_output!r}'s activating fault ({fault}) was not "
            f"detected by Atalanta (status: {', '.join(statuses)}) -- this "
            "trojan's trigger condition may not be reachable, or not "
            "observable, within the search budget used."
        )
