import json
from pyatpg.analysis.models import FeatureSet, GateRole


class PromptBuilder:
    """Converts a canonical FeatureSet into structured LLM prompts.

    Agnostic of LLM provider. Supports future templating.
    """

    def build(self, feature_set: FeatureSet, template: str = "zero_shot") -> str:
        if template == "zero_shot":
            return self._build_zero_shot(feature_set)
        else:
            raise ValueError(f"Unknown prompt template: {template}")

    def _build_zero_shot(self, feature_set: FeatureSet) -> str:
        prompt = [
            "You are an expert hardware security engineer analyzing a digital logic circuit for potential Hardware Trojans.",
            "",
            "### Task",
            "I will provide you with a list of internal gates extracted from a netlist. Each gate includes structural, dynamic, and testability features.",
            "Your objective is to identify which gates are most likely to be part of a Hardware Trojan trigger or payload, and rank them from MOST suspicious to LEAST suspicious.",
            "",
            "### Trojan Characteristics",
            "Hardware Trojans are designed to be stealthy. Look for:",
            "1. Rarity: Gates with very low toggle rates (rarely switch).",
            "2. Testability Evasion: Gates that are extremely hard to control (high CC0/CC1) and hard to observe (high CO).",
            "3. Structural Isolation: Gates with high fan-in (many conditions to activate) but low fan-out.",
            "4. Undetected Faults: Gates flagged as 'undetected' by ATPG, meaning test patterns fail to cover them.",
            "",
            "### Feature Descriptions",
            "- gate_id: Unique identifier for the gate.",
            "- gate_type: Logic function (AND, OR, XOR, etc.).",
            "- fanin / fanout: Number of inputs / outputs.",
            "- level: Topological depth from primary inputs.",
            "- cc0 / cc1: Combinational controllability 0/1 (SCOAP). Higher = harder to control.",
            "- co: Combinational observability (SCOAP). Higher = harder to observe.",
            "- toggle_rate: Fraction of simulation cycles where the output toggled.",
            "- atpg_undetected: Boolean flag indicating if ATPG failed to generate a test pattern.",
            "- atpg_redundant: Boolean flag indicating if the gate logic is redundant.",
            "",
            "### Data",
            "Here are the gates to analyze (inputs/outputs are excluded):",
            "```json"
        ]

        # Filter out inputs/outputs, just like heuristic rankers
        gates_to_rank = []
        for gf in feature_set.gate_features:
            if gf.role == GateRole.INPUT or gf.role == GateRole.OUTPUT:
                continue
            gates_to_rank.append({
                "gate_id": gf.gate_id,
                "gate_type": gf.gate_type,
                "fanin": gf.fanin,
                "fanout": gf.fanout,
                "level": gf.level,
                "cc0": gf.cc0,
                "cc1": gf.cc1,
                "co": gf.co,
                "toggle_rate": round(gf.toggle_rate, 4),
                "atpg_undetected": gf.undetected,
                "atpg_redundant": gf.redundant,
                "centrality": round(gf.extra_features.get('centrality', 0.0), 4)
            })

        prompt.append(json.dumps(gates_to_rank, indent=2))
        prompt.append("```")

        prompt.extend([
            "",
            "### Output Format",
            "You MUST output exactly valid JSON matching the schema below. Do not wrap the JSON in Markdown formatting (no ```json). Output ONLY the raw JSON object.",
            "Rank the gates by suspicion (highest suspicion first). Provide a 'confidence' score between 0.0 and 1.0, and a brief 'reason' for why the gate is suspicious.",
            "",
            "{",
            '    "ranking": [',
            "        {",
            '            "gate": "G17",',
            '            "confidence": 0.94,',
            '            "reason": "Extremely low toggle rate, hard to observe (high CO), and structurally isolated."',
            "        }",
            "    ]",
            "}"
        ])

        return "\n".join(prompt)
