import random
from abc import ABC, abstractmethod
from typing import List, Optional

import networkx as nx

from pyatpg.core.netlist import Circuit
from pyatpg.core.graph_analysis import CircuitGraph


def _circuit_digraph(circuit: Circuit) -> nx.DiGraph:
    """Signal-flow graph of the *clean* circuit: edge driver -> load."""
    g = nx.DiGraph()
    g.add_nodes_from(circuit.nodes)
    for out, gate in circuit.gates.items():
        for inp in gate.inputs:
            g.add_edge(inp, out)
    return g


def _acyclic_payload_candidates(
    circuit: Circuit, trigger_nets: List[str]
) -> List[str]:
    """Payload targets that will not close a combinational feedback loop.

    The payload splices in a gate whose fan-in includes the trigger output,
    and the trigger output is driven by ``trigger_nets``. So the insertion
    adds the path::

        trigger_nets -> ht_trigger_out -> payload_target -> (payload fan-out)

    If any trigger net is reachable from ``payload_target`` in the existing
    netlist, that path joins an existing ``payload_target -> ... -> trigger_net``
    path into a cycle. A cyclic "combinational" netlist is illegal, and
    ATALANTA does not reject it cleanly -- it segfaults during levelisation.

    Uniform-random selection almost never picks such a target, which is why
    ``RandomStrategy`` appeared to work; ``LowScoapStrategy`` and
    ``RareNodeStrategy`` pick the hardest-to-control net as a trigger (deep in
    the circuit) and the hardest-to-observe / rarest net as the payload
    (near the inputs), which is close to a worst case for this loop.
    """
    g = _circuit_digraph(circuit)
    forbidden = set(trigger_nets)
    for net in trigger_nets:
        if net in g:
            forbidden |= nx.ancestors(g, net)
    return [
        n for n in circuit.nodes
        if n not in circuit.inputs and n not in forbidden
    ]


class InsertionStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass
        
    @abstractmethod
    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        """Select `num_inputs` nets from the circuit to act as the Trojan trigger inputs."""
        pass
        
    @abstractmethod
    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        """Select a net from the circuit to act as the Trojan payload target.

        ``trigger_nets`` are the already-selected trigger inputs; the payload
        target must be chosen so that splicing the trigger output into it does
        not create a combinational feedback loop.
        """
        pass


class RandomStrategy(InsertionStrategy):
    """Selects trigger and payload nets purely randomly."""
    
    @property
    def name(self) -> str:
        return "random"
        
    def _get_valid_nets(self, circuit: Circuit) -> List[str]:
        # Exclude primary outputs from being triggers if they have no fan-out, 
        # but for randomness, any internal net or input is fine.
        # Actually, let's just pick any net except the ones we are about to inject.
        return list(circuit.nodes)
        
    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        valid_nets = self._get_valid_nets(circuit)
        if len(valid_nets) < num_inputs:
            raise ValueError("Circuit too small to select triggers.")
        return random.sample(valid_nets, num_inputs)
        
    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        # Avoid primary inputs as payload targets because we'd have to override
        # external pins, and avoid any net from which a trigger net is
        # reachable, which would make the circuit cyclic (see
        # _acyclic_payload_candidates).
        candidates = _acyclic_payload_candidates(circuit, trigger_nets or [])
        if not candidates:
            raise ValueError(
                "No acyclic payload target available for the selected trigger nets."
            )
        return random.choice(candidates)


class LowScoapStrategy(RandomStrategy):
    """Selects trigger nets that are hard to control, and a payload target
    that is hard to observe, using SCOAP testability metrics.

    Real trojans are inserted on nets unlikely to be driven to a specific
    value (and effects unlikely to be observed) during ordinary test
    generation, so they survive functional/structural testing undetected.
    Uniform random selection has no such bias and is just as likely to land
    on an easy-to-test net, which is both unrealistic and -- because
    testability-based detectors (including this tool's own targeted-ATPG
    mode) rank gates by exactly these SCOAP metrics -- makes the inserted
    trojan indistinguishable from ordinary hard-to-test noise only by luck,
    rather than by the trojan's own placement being genuinely evasive.
    """

    @property
    def name(self) -> str:
        return "low_scoap"

    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        valid_nets = self._get_valid_nets(circuit)
        if len(valid_nets) < num_inputs:
            raise ValueError("Circuit too small to select triggers.")
        cc0, cc1, _co = CircuitGraph(circuit).compute_scoap()
        # Highest combined CC0+CC1 = hardest to drive to either value.
        ranked = sorted(
            valid_nets,
            key=lambda n: cc0.get(n, 1) + cc1.get(n, 1),
            reverse=True,
        )
        return ranked[:num_inputs]

    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        candidates = _acyclic_payload_candidates(circuit, trigger_nets or [])
        if not candidates:
            raise ValueError(
                "No acyclic payload target available for the selected trigger nets."
            )
        _cc0, _cc1, co = CircuitGraph(circuit).compute_scoap()
        # Highest CO = hardest to propagate to an observable output.
        return max(candidates, key=lambda n: co.get(n, 0))


class RareNodeStrategy(RandomStrategy):
    """Selects trigger nets with low simulated toggle rates, and a payload
    target that toggles rarely, so the trojan's activation condition and
    its effect both stay hidden from ordinary random/functional testing.

    See ``LowScoapStrategy`` for why unbiased random selection is an
    unrealistic and easily-detected placement.
    """

    @property
    def name(self) -> str:
        return "rare_node"

    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        valid_nets = self._get_valid_nets(circuit)
        if len(valid_nets) < num_inputs:
            raise ValueError("Circuit too small to select triggers.")
        toggle_data = CircuitGraph(circuit).compute_toggle_rates()
        ranked = sorted(
            valid_nets,
            key=lambda n: toggle_data.get(n, {}).get('toggle_rate', 0.5),
        )
        return ranked[:num_inputs]

    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        candidates = _acyclic_payload_candidates(circuit, trigger_nets or [])
        if not candidates:
            raise ValueError(
                "No acyclic payload target available for the selected trigger nets."
            )
        toggle_data = CircuitGraph(circuit).compute_toggle_rates()
        return min(
            candidates,
            key=lambda n: toggle_data.get(n, {}).get('toggle_rate', 0.5),
        )
