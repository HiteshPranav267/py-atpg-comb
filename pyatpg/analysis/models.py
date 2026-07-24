import json
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

class GateRole(str, Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    INTERNAL = "INTERNAL"
    
@dataclass(frozen=True)
class GateFeature:
    """Canonical representation of a gate and its extracted features."""
    # Identifiers
    gate_id: str
    gate_type: str
    role: GateRole
    
    # Structural/Graph
    level: int
    fanin: int
    fanout: int
    pi_distance: int
    po_distance: int
    
    # Testability (SCOAP)
    cc0: int
    cc1: int
    co: int
    
    # Dynamic
    toggle_rate: float
    
    # ATPG
    detectable: bool = False
    undetected: bool = False
    redundant: bool = False
    fault_count: int = 0
    
    # Ground Truth (Optional)
    is_trojan_gate: bool = False
    is_trigger_gate: bool = False
    is_payload_gate: bool = False
    trojan_id: Optional[int] = None
    
    # Future-proofing
    extra_features: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d['role'] = self.role.value
        return d
        
    @classmethod
    def from_dict(cls, data: dict) -> 'GateFeature':
        d = dict(data)
        d['role'] = GateRole(d['role'])
        return cls(**d)

@dataclass
class ATPGResult:
    """Represents the status of faults for specific gates."""
    global_coverage: float
    total_patterns: int
    gate_statuses: Dict[str, Dict[str, Any]] # gate_id -> {'detectable': True, 'undetected': False, ...}

@dataclass
class GroundTruth:
    """Represents known hardware trojan metadata."""
    trojan_id: int
    trigger_gates: List[str]
    payload_gate: str
    affected_net: str

@dataclass
class FeatureSet:
    """Circuit-level representation holding multiple GateFeatures."""
    circuit_name: str
    gate_features: List[GateFeature]
    atpg_coverage: Optional[float] = None
    extraction_time_ms: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "circuit_name": self.circuit_name,
            "atpg_coverage": self.atpg_coverage,
            "extraction_time_ms": self.extraction_time_ms,
            "gate_features": [f.to_dict() for f in self.gate_features]
        }
        
    @classmethod
    def from_dict(cls, data: dict) -> 'FeatureSet':
        d = dict(data)
        features = [GateFeature.from_dict(f) for f in d.get("gate_features", [])]
        d["gate_features"] = features
        return cls(**d)
        
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
        
    @classmethod
    def from_json(cls, json_str: str) -> 'FeatureSet':
        return cls.from_dict(json.loads(json_str))
