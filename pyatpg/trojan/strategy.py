import random
from abc import ABC, abstractmethod
from typing import List
from pyatpg.core.netlist import Circuit

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
    """Selects trigger nets with low controllability and high observability."""
    @property
    def name(self) -> str:
        return "low_scoap"
    
    # Placeholder: currently falls back to random until SCOAP integration is added to the strategy
    pass


class RareNodeStrategy(RandomStrategy):
    """Selects trigger nets with low toggle rates."""
    @property
    def name(self) -> str:
        return "rare_node"
        
    # Placeholder: currently falls back to random until Toggle integration is added to the strategy
    pass
