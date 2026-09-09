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
    #: Whether the trigger was proven activatable (Atalanta found a pattern
    #: that both justifies its "on" value from the primary inputs and
    #: propagates it to a primary output) -- see pyatpg.trojan.activation.
    #: None means this was never checked (TrojanInserter's
    #: verify_activation was off), not "checked and failed": a failed check
    #: makes insert() raise rather than return, so a variant with this field
    #: set is always either True or was never asked about.
    activation_confirmed: Optional[bool] = None

    def to_dict(self):
        return asdict(self)

    def save(self, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=4)
