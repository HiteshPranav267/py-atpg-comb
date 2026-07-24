import time
from typing import List, Optional
from pyatpg.core.netlist import Circuit
from pyatpg.core.graph_analysis import CircuitGraph
from .models import GateFeature, GateRole, ATPGResult, GroundTruth, FeatureSet

class FeatureExtractor:
    """
    Fuses structural, topological, dynamic, and testability features 
    into a canonical FeatureSet representation for downstream consumption.
    """
    def extract(self, circuit: Circuit, atpg_result: Optional[ATPGResult] = None, ground_truth: Optional[GroundTruth] = None) -> FeatureSet:
        start_time = time.time()
        
        # 1. Use the existing CircuitGraph to get core properties without re-implementing them
        cg = CircuitGraph(circuit)
        raw_features = cg.get_gate_features()
        
        gate_features: List[GateFeature] = []
        
        # Identify the roles of nodes based on inputs/outputs
        inputs = set(circuit.inputs)
        outputs = set(circuit.outputs)
        
        # Ground Truth identifiers
        trigger_gates = set(ground_truth.trigger_gates) if ground_truth else set()
        payload_gate = ground_truth.payload_gate if ground_truth else ""
        trojan_id = ground_truth.trojan_id if ground_truth else None
        
        for gate_id, raw in raw_features.items():
            # Determine Role
            if gate_id in inputs:
                role = GateRole.INPUT
            elif gate_id in outputs:
                role = GateRole.OUTPUT
            else:
                role = GateRole.INTERNAL
                
            # ATPG Statuses
            detectable = False
            undetected = False
            redundant = False
            fault_count = 0
            
            if atpg_result and gate_id in atpg_result.gate_statuses:
                status_dict = atpg_result.gate_statuses[gate_id]
                detectable = status_dict.get('detectable', False)
                undetected = status_dict.get('undetected', False)
                redundant = status_dict.get('redundant', False)
                fault_count = status_dict.get('fault_count', 0)
                
            # Ground Truth mapping
            is_trigger = gate_id in trigger_gates
            is_payload = (gate_id == payload_gate) or (gate_id in trigger_gates and "HT_PAYLOAD" in gate_id)
            
            # The JSON lists 'HT_PAYLOAD_XOR' in inserted_gates, so we can also check by name if needed,
            # but using ground truth provided by adapter is cleaner.
            # In our metadata adapter, we just dumped inserted_gates into trigger_gates for simplicity,
            # let's refine the check:
            is_trojan_gate = is_trigger or is_payload or "HT_" in gate_id.upper()
            if "TRIGGER" in gate_id.upper():
                is_trigger = True
            if "PAYLOAD" in gate_id.upper():
                is_payload = True
                
            gf = GateFeature(
                gate_id=gate_id,
                gate_type=raw.get('gate_type', 'UNKNOWN'),
                role=role,
                level=raw.get('level', 0),
                fanin=raw.get('fan_in', 0),
                fanout=raw.get('fan_out', 0),
                pi_distance=raw.get('level', 0),
                po_distance=raw.get('level_to_output', 0),
                cc0=raw.get('cc0', 0),
                cc1=raw.get('cc1', 0),
                co=raw.get('co', 0),
                toggle_rate=raw.get('toggle_rate', 0.5),
                detectable=detectable,
                undetected=undetected,
                redundant=redundant,
                fault_count=fault_count,
                is_trojan_gate=is_trojan_gate,
                is_trigger_gate=is_trigger,
                is_payload_gate=is_payload,
                trojan_id=trojan_id,
                extra_features={
                    'centrality': raw.get('centrality', 0.0),
                    'fan_ratio': raw.get('fan_ratio', 0.0)
                }
            )
            gate_features.append(gf)
            
        extraction_time = (time.time() - start_time) * 1000.0
        
        return FeatureSet(
            circuit_name=getattr(circuit, 'name', 'unknown'),
            gate_features=gate_features,
            atpg_coverage=atpg_result.global_coverage if atpg_result else None,
            extraction_time_ms=extraction_time
        )
