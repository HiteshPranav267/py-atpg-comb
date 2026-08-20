import random
from abc import ABC, abstractmethod
from typing import List
from pyatpg.core.netlist import Circuit
from pyatpg.core.graph_analysis import CircuitGraph

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
    def select_payload_target(self, circuit: Circuit) -> str:
        """Select a net from the circuit to act as the Trojan payload target."""
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
        
    def select_payload_target(self, circuit: Circuit) -> str:
        valid_nets = self._get_valid_nets(circuit)
        # Avoid primary inputs as payload targets because we'd have to override external pins,
        # which might be tricky in simple Verilog structural generation.
        # It's safer to target the output of an existing internal gate or a primary output.
        targetable_nets = [net for net in valid_nets if net not in circuit.inputs]
        if not targetable_nets:
            raise ValueError("No targetable nets found for payload.")
        return random.choice(targetable_nets)


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

    def select_payload_target(self, circuit: Circuit) -> str:
        valid_nets = [n for n in self._get_valid_nets(circuit) if n not in circuit.inputs]
        if not valid_nets:
            raise ValueError("No targetable nets found for payload.")
        _cc0, _cc1, co = CircuitGraph(circuit).compute_scoap()
        # Highest CO = hardest to propagate to an observable output.
        return max(valid_nets, key=lambda n: co.get(n, 0))


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

    def select_payload_target(self, circuit: Circuit) -> str:
        valid_nets = [n for n in self._get_valid_nets(circuit) if n not in circuit.inputs]
        if not valid_nets:
            raise ValueError("No targetable nets found for payload.")
        toggle_data = CircuitGraph(circuit).compute_toggle_rates()
        return min(
            valid_nets,
            key=lambda n: toggle_data.get(n, {}).get('toggle_rate', 0.5),
        )
