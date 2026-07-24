from dataclasses import dataclass, asdict
import json
from typing import List, Optional

@dataclass
class TrojanMetadata:
    trojan_id: int
    circuit: str
    trigger_type: str
    payload_type: str
    trigger_inputs: List[str]
    trigger_output: str
    payload_target: str
    inserted_gates: List[str]
    affected_net: str
    strategy: str

    def to_dict(self):
        return asdict(self)

    def save(self, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=4)
