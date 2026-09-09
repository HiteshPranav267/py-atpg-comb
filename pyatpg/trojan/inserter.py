from typing import Optional
from pyatpg.core.netlist import Circuit
from pyatpg.trojan.activation import ActivationUnverifiable, TriggerNotActivatable, verify_trigger_activatable
from pyatpg.trojan.strategy import InsertionStrategy, RandomStrategy
from pyatpg.trojan.trigger import CombinationalTriggerInserter
from pyatpg.trojan.payload import CombinationalPayloadInserter
from pyatpg.trojan.metadata import TrojanMetadata

class TrojanInserter:
    """Configurable orchestrator for hardware trojan insertion."""

    def __init__(
        self,
        trigger: str = "AND",
        payload: str = "XOR",
        strategy: Optional[InsertionStrategy] = None,
        verify_activation: bool = False,
    ):
        self.trigger_type = trigger.upper()
        self.payload_type = payload.upper()
        self.strategy = strategy if strategy else RandomStrategy()

        self.trigger_inserter = CombinationalTriggerInserter(gate_type=self.trigger_type)
        self.payload_inserter = CombinationalPayloadInserter(gate_type=self.payload_type)

        #: Runs one extra targeted Atalanta job per insertion to prove the
        #: trigger's activation condition is actually reachable and
        #: observable (pyatpg.trojan.activation), rejecting the insertion
        #: with an exception otherwise. Off by default: it requires a
        #: working Atalanta binary and a subprocess call per insertion,
        #: which every existing caller (including the unit test suite) does
        #: not expect or need. Callers generating a dataset meant to survive
        #: scrutiny should turn it on explicitly.
        self.verify_activation = verify_activation

    def insert(self, circuit: Circuit, trojan_id: int, num_trigger_inputs: int = 2) -> tuple[Circuit, TrojanMetadata]:
        """
        Takes a base circuit, performs a deep copy, inserts a Trojan,
        and returns the compromised circuit along with its metadata.
        """
        # 1. Deep Copy to avoid accumulating Trojans
        compromised = circuit.clone()
        
        # 2. Select Nets using Strategy
        trigger_inputs = self.strategy.select_trigger_nets(compromised, num_inputs=num_trigger_inputs)
        payload_target = self.strategy.select_payload_target(compromised, trigger_inputs)

        if payload_target in trigger_inputs:
            raise ValueError(
                f"Payload target {payload_target} is also a trigger input; "
                "this would create a combinational feedback loop."
            )
        
        trigger_output_name = f"ht_trigger_out_{trojan_id}"
        
        # 3. Insert Trigger
        trigger_gates, trigger_out = self.trigger_inserter.insert(
            compromised, 
            target_nets=trigger_inputs, 
            trigger_out_name=trigger_output_name
        )
        
        # 4. Insert Payload
        payload_gates, affected_net = self.payload_inserter.insert(
            compromised, 
            target_net=payload_target, 
            trigger_net=trigger_out
        )
        
        # 4b. Guard against a cyclic result. A combinational netlist with a
        # feedback loop is illegal and makes ATALANTA segfault during
        # levelisation rather than reporting an error, so fail loudly here
        # instead of shipping a netlist that crashes the ATPG tool.
        import networkx as nx

        check = nx.DiGraph()
        for out, gate in compromised.gates.items():
            for inp in gate.inputs:
                check.add_edge(inp, out)
        if not nx.is_directed_acyclic_graph(check):
            raise ValueError(
                "Trojan insertion produced a combinational feedback loop "
                f"(trigger={trigger_inputs}, payload={payload_target})."
            )

        # 4c. Confirm the trigger is actually activatable -- reachable from
        # the primary inputs and observable at a primary output -- before
        # accepting the insertion. Without this, a trojan whose activation
        # condition Atalanta's own search cannot satisfy can ship
        # undetected (see pyatpg.trojan.activation's docstring for the case
        # that motivated it).
        activation_confirmed: Optional[bool] = None
        if self.verify_activation:
            try:
                verify_trigger_activatable(
                    compromised, trigger_out, self.trigger_type,
                    circuit_name=f"trojan_{trojan_id}_activation",
                )
                activation_confirmed = True
            except ActivationUnverifiable:
                # This trigger type (XOR/XNOR) has no single activating
                # fault to check -- not evidence against the placement,
                # just outside what this check covers. Leave it unverified
                # rather than rejecting a trojan we have no finding against.
                activation_confirmed = None
            # TriggerNotActivatable, and anything verify_trigger_activatable
            # did not itself wrap (a missing/broken Atalanta binary), are
            # deliberately left to propagate: the caller asked for this
            # guarantee, so a placement that fails it -- or an engine that
            # can't confirm it -- must reject the insertion rather than
            # ship it silently unverified. generator.py's per-variant loop
            # already catches and skips this the same way it does a
            # cyclic-netlist ValueError.

        # 5. Create Metadata
        metadata = TrojanMetadata(
            trojan_id=trojan_id,
            circuit="unknown", # To be filled by generator
            trigger_type=self.trigger_type,
            payload_type=self.payload_type,
            trigger_inputs=trigger_inputs,
            trigger_output=trigger_out,
            payload_target=payload_target,
            inserted_gates=trigger_gates + payload_gates,
            affected_net=affected_net,
            strategy=self.strategy.name,
            activation_confirmed=activation_confirmed,
        )

        return compromised, metadata
