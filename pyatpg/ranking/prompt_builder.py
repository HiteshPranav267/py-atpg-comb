import json
import os
from pyatpg.analysis.models import FeatureSet, GateFeature, GateRole

#: Upper bound on how many gates a ranking prompt ever starts from, before
#: the token-budget check below can shrink it further. Mostly a ceiling on
#: candidate-selection diversity, not the real safety mechanism -- that's
#: :data:`DEFAULT_TOKEN_BUDGET`, because a fixed gate *count* isn't a
#: reliable proxy for prompt size: real standard-cell names and large SCOAP
#: values cost several times what a short synthetic id does per gate.
#: ``LLM_RANKING_GATE_LIMIT`` overrides it; 0 disables both the count cap
#: and the token-budget shrink below, sending every gate uncapped -- fine
#: for Gemini/Ollama/a paid tier, not for Groq free.
DEFAULT_RANKING_GATE_LIMIT = 60
RANKING_GATE_LIMIT_ENV_VAR = "LLM_RANKING_GATE_LIMIT"

#: Token ceiling (prompt + reserved completion) a ranking request is kept
#: under by shrinking the gate pool, when the count cap above still leaves
#: it too big. Set with margin below Groq's free-tier 8K TPM -- the estimate
#: below is deliberately pessimistic, not exact, so the margin is the real
#: safety net. ``LLM_RANKING_TOKEN_BUDGET`` overrides it; has no effect when
#: the gate limit is explicitly uncapped (0).
DEFAULT_TOKEN_BUDGET = 6000
TOKEN_BUDGET_ENV_VAR = "LLM_RANKING_TOKEN_BUDGET"

#: Characters per token, deliberately on the low (= token-count-high) side.
#: tiktoken measures ~2.5-2.8 on this JSON encoding, but a provider's own
#: tokenizer bills denser than that -- the ATPG side found a real count 19%
#: above what 2.3 predicted (see ``pyatpg.atpg.llm_context``) -- and
#: under-counting is the dangerous direction, since it is what lets an
#: over-budget request through to be refused.
_CHARS_PER_TOKEN_ESTIMATE = 2.0

#: Estimated completion tokens per gate, per template: the zero-shot reply
#: carries a full "reason" sentence (measured ~57/gate against real
#: standard-cell data), the compact one a clipped phrase. Plus a flat
#: overhead covering the JSON scaffolding and, for models that reason
#: before answering, the hidden reasoning trace -- that trace burns real
#: tokens against the same budget (~700 measured) even though it never
#: appears in the response. Sized with headroom deliberately: tuned to the
#: measured average, a run whose reasoning ran long truncated mid-JSON.
TOKENS_PER_GATE_OUTPUT = {"zero_shot": 75, "compact": 35}
OUTPUT_OVERHEAD_TOKENS = 1200

#: Templates in order of decreasing size. :class:`~pyatpg.ranking.llm.LLMRanker`
#: walks this on a request-too-large failure, so a model with a small
#: context (or a stingy free-tier per-minute budget) degrades to a terser
#: prompt instead of failing the run outright.
TEMPLATE_FALLBACK_ORDER = ("zero_shot", "compact")

#: Columns of the compact encoding, in order -- one gate per CSV row under
#: a single header, which the ATPG side measured at roughly a third the
#: tokens of the same numbers as labelled JSON (see
#: ``pyatpg.atpg.llm_context``, which uses the same trick for its own
#: prompts).
_COMPACT_COLUMNS = (
    "id", "type", "fanin", "fanout", "level", "cc0", "cc1", "co",
    "toggle", "undet", "redun", "centr",
)

#: Never shrink the pool below this many gates -- past some point a smaller
#: prompt stops helping and just makes the ranking useless.
_MIN_POOL_SIZE = 8

#: Share of the pool given to each deterministic view once gates flagged by
#: ATPG (undetected/redundant) are force-included; whatever is left after
#: that is topped up from the rarity ranking, so these are floors.
_POOL_SHARE_RAREST = 0.6
_POOL_SHARE_HARDEST_TO_OBSERVE = 0.4


def _resolve_gate_limit(explicit: int | None) -> int:
    if explicit is not None:
        return max(0, int(explicit))
    raw = os.environ.get(RANKING_GATE_LIMIT_ENV_VAR, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_RANKING_GATE_LIMIT


def _resolve_token_budget() -> int:
    raw = os.environ.get(TOKEN_BUDGET_ENV_VAR, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_TOKEN_BUDGET


def output_token_budget(gate_count: int, template: str = "zero_shot") -> int:
    """Completion tokens to reserve for a reply ranking ``gate_count`` gates."""
    per_gate = TOKENS_PER_GATE_OUTPUT.get(template, TOKENS_PER_GATE_OUTPUT["zero_shot"])
    return gate_count * per_gate + OUTPUT_OVERHEAD_TOKENS


def estimate_tokens(prompt: str, gate_count: int, template: str = "zero_shot") -> int:
    """Pessimistic total-token estimate for a built prompt plus its reply.

    Not a real tokenizer -- there is no single one across providers -- just
    a conservative proxy good enough to decide whether to shrink the pool
    further before sending anything.
    """
    input_tokens = len(prompt) / _CHARS_PER_TOKEN_ESTIMATE
    return int(input_tokens + output_token_budget(gate_count, template))


def _select_candidate_pool(
    gates: list[GateFeature], limit: int
) -> tuple[list[GateFeature], str | None]:
    """Pick which gates a ranking prompt should carry.

    Every gate flagged undetected/redundant by ATPG is force-included (the
    prompt itself calls these out as a suspicion signal), then the rest of
    the budget is split between the rarest-toggling and hardest-to-observe
    views, topped up from the rarity ranking. Below the limit, every gate is
    sent and no note is produced.
    """
    total = len(gates)
    if limit <= 0 or total <= limit:
        return list(gates), None

    forced = [g for g in gates if g.undetected or g.redundant]
    rarest = sorted(gates, key=lambda g: g.toggle_rate)
    hardest = sorted(gates, key=lambda g: -g.co)

    budget = max(0, limit - len(forced))
    quotas = (
        (rarest, int(budget * _POOL_SHARE_RAREST)),
        (hardest, int(budget * _POOL_SHARE_HARDEST_TO_OBSERVE)),
    )

    chosen: dict[str, GateFeature] = {g.gate_id: g for g in forced}
    counts = []
    for ranked, quota in quotas:
        taken = 0
        for g in ranked:
            if taken >= quota:
                break
            if g.gate_id not in chosen:
                chosen[g.gate_id] = g
                taken += 1
        counts.append(taken)
    for g in rarest:
        if len(chosen) >= limit:
            break
        chosen.setdefault(g.gate_id, g)

    pool = sorted(chosen.values(), key=lambda g: g.toggle_rate)
    share = 100.0 * len(pool) / total if total else 0.0
    note = (
        f"Sent {len(pool)} of {total} gates to the model ({share:.1f}%): "
        f"{counts[0]} rarest-toggling, {counts[1]} hardest-to-observe"
        + (f", plus {len(forced)} flagged undetected/redundant by ATPG" if forced else "")
        + f", topped up from the rarity ranking. Set {RANKING_GATE_LIMIT_ENV_VAR}=0 "
        "to send every gate."
    )
    return pool, note


class PromptBuilder:
    """Converts a canonical FeatureSet into structured LLM prompts.

    Agnostic of LLM provider. Supports future templating.
    """

    #: Gates actually sent by the most recent :meth:`build` call, after any
    #: cap. Read alongside the prompt to size a completion-token budget --
    #: mirrors :attr:`LLMProvider.last_usage`'s "read it right after" pattern.
    last_gate_count: int = 0

    #: Template the most recent :meth:`build` call rendered, so a caller
    #: sizing ``max_output_tokens`` uses that template's per-gate cost.
    last_template: str = "zero_shot"

    def build(
        self, feature_set: FeatureSet, template: str = "zero_shot", max_gates: int | None = None
    ) -> str:
        if template not in TEMPLATE_FALLBACK_ORDER:
            raise ValueError(f"Unknown prompt template: {template}")

        # Filter out inputs/outputs, just like heuristic rankers
        internal_gates = [
            gf for gf in feature_set.gate_features
            if gf.role != GateRole.INPUT and gf.role != GateRole.OUTPUT
        ]
        limit = _resolve_gate_limit(max_gates)
        pool, note = _select_candidate_pool(internal_gates, limit)
        prompt_text = self._render(pool, note, template)

        # A fixed gate count is a poor proxy for prompt size -- real
        # standard-cell names and large SCOAP values cost several times a
        # short synthetic id -- so measure what actually got built and
        # shrink further if it's still too big, rather than trusting the
        # count alone. Skipped when the caller explicitly uncapped (0).
        if limit > 0:
            budget = _resolve_token_budget()
            while (
                estimate_tokens(prompt_text, len(pool), template) > budget
                and len(pool) > _MIN_POOL_SIZE
            ):
                shrunk = max(_MIN_POOL_SIZE, int(len(pool) * 0.7))
                if shrunk >= len(pool):
                    break
                pool, note = _select_candidate_pool(internal_gates, shrunk)
                prompt_text = self._render(pool, note, template)

        self.last_gate_count = len(pool)
        self.last_template = template
        return prompt_text

    def output_budget(self) -> int:
        """Completion tokens to reserve for the most recent :meth:`build`."""
        return output_token_budget(self.last_gate_count, self.last_template)

    def _render(self, pool: list[GateFeature], note: str | None, template: str) -> str:
        if template == "compact":
            return self._render_compact(pool, note)
        return self._render_zero_shot(pool, note)

    def _render_compact(self, pool: list[GateFeature], note: str | None) -> str:
        """The same gates and the same task, in about a third the tokens.

        For models whose context (or whose provider's per-minute budget)
        cannot take the labelled-JSON version: CSV rows under one header,
        the feature glossary collapsed to a single legend line, and reasons
        clipped to a few words. Same output schema, so the parser and every
        caller downstream are unchanged.
        """
        prompt = [
            "You are a hardware security engineer ranking gates of a logic circuit "
            "by how likely they are to belong to a Hardware Trojan trigger or payload.",
            "Suspicious = rarely toggling, hard to control (high cc0/cc1), hard to "
            "observe (high co), high fanin with low fanout, or flagged by ATPG.",
            "",
            "One gate per CSV line. cc0/cc1/co are SCOAP controllability-to-0/1 and "
            "observability (higher = harder). toggle is the fraction of patterns on "
            "which the net switched (lower = rarer). undet/redun are ATPG flags (1/0). "
            "centr is graph centrality.",
        ]
        if note:
            prompt.append(note)
        prompt.append("")
        prompt.append(",".join(_COMPACT_COLUMNS))
        for gf in pool:
            prompt.append(",".join((
                gf.gate_id,
                gf.gate_type,
                str(gf.fanin),
                str(gf.fanout),
                str(gf.level),
                str(gf.cc0),
                str(gf.cc1),
                str(gf.co),
                f"{gf.toggle_rate:.4f}",
                "1" if gf.undetected else "0",
                "1" if gf.redundant else "0",
                f"{gf.extra_features.get('centrality', 0.0):.4f}",
            )))
        prompt.extend([
            "",
            "Output ONLY this raw JSON object, no markdown, no prose. Include all "
            f"{len(pool)} gates exactly once, most suspicious first. Keep each "
            "'reason' under 8 words.",
            '{"ranking":[{"gate":"<id>","confidence":0.0-1.0,"reason":"<brief>"}]}',
        ])
        return "\n".join(prompt)

    def _render_zero_shot(self, pool: list[GateFeature], note: str | None) -> str:
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
        ]
        if note:
            prompt.append(note)
        prompt.append("Here are the gates to analyze (inputs/outputs are excluded):")
        prompt.append("```json")

        gates_to_rank = [
            {
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
                "centrality": round(gf.extra_features.get('centrality', 0.0), 4),
            }
            for gf in pool
        ]
        prompt.append(json.dumps(gates_to_rank, indent=2))
        prompt.append("```")

        prompt.extend([
            "",
            "### Output Format",
            "You MUST output exactly valid JSON matching the schema below. Do not wrap the JSON in Markdown formatting (no ```json). Output ONLY the raw JSON object.",
            f"Your 'ranking' array MUST contain all {len(pool)} gates listed above, each exactly once -- do not omit gates you consider unremarkable, rank them last instead.",
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
