"""What the combinational LLM stages are allowed to see, and in what form.

Two levers keep the prompt inside a sane context window regardless of
circuit size, both applied before ``FaultTargetSelector`` and
``CircuitAnalyst`` build their prompts:

* **Encoding.** The per-gate profile from ``CircuitGraph`` is a five-line,
  labelled paragraph (~60 tokens). The same numbers as one CSV row under a
  single header are ~18 tokens, so every gate sent costs a third as much.

* **Candidate pool.** Sending every gate scales the prompt with the circuit
  -- c7552 came to roughly 220k tokens, past most models' context and far
  past anything a ranking step needs. The pool is capped, but *not* by
  truncating a single ranking: that was tried, and a planted gate ranked
  just below the cutoff became invisible to the model. Instead the pool is
  the union of several deterministic views the prompt asks the model to
  reason about anyway -- rarest toggling, hardest to observe, and gates
  whose activity is anomalous for their neighbourhood -- plus everything
  the structural trojan detector flags. A gate that is interesting on any
  axis is in the pool; a gate that is unremarkable on every axis is the one
  left out.

Nothing here decides anything about a gate. It only decides what the model
is shown, and says so in the notes it returns.
"""
from __future__ import annotations

import os
from statistics import median
from typing import Any

#: Default cap on how many gates a prompt carries. About 6k tokens of CSV
#: at the compact encoding. ``LLM_CONTEXT_GATES`` in the environment
#: overrides it; 0 means "send every gate", the pre-reduction behaviour.
DEFAULT_CONTEXT_GATE_LIMIT = 300
CONTEXT_GATE_LIMIT_ENV_VAR = "LLM_CONTEXT_GATES"

#: Share of the pool given to each deterministic view. They overlap heavily
#: on most circuits, and whatever is left after de-duplication is filled
#: from the rarity ranking, so these are floors rather than exact splits.
_POOL_SHARE_RAREST = 0.35
_POOL_SHARE_HARDEST_TO_OBSERVE = 0.15
_POOL_SHARE_ANOMALOUS = 0.2
#: Reconverging fanout stems: the structural origin of most redundant and
#: hard-to-test faults (see CircuitGraph.compute_reconvergence), and on the
#: ISCAS benchmarks a far better predictor of the faults Atalanta cannot
#: detect than toggle rate or SCOAP alone.
_POOL_SHARE_RECONVERGENT = 0.3

#: Columns of the compact encoding, in order. The header names them once;
#: each row is bare values.
COMPACT_COLUMNS = (
    "id", "type", "role", "fan_in", "fan_out", "level", "cc0", "cc1", "co", "toggle_rate",
    "reconv", "mark",
)
COMPACT_HEADER = ",".join(COMPACT_COLUMNS)

#: What the header means, for the model. Kept short: it is sent once.
COMPACT_LEGEND = (
    "One gate per line, CSV. role is PI (primary input), PO (primary output) "
    "or gate. cc0/cc1/co are SCOAP controllability-to-0/1 and observability "
    "(higher = harder). toggle_rate is the fraction of random patterns on "
    "which the net switched (lower = rarer). reconv is how many gates this "
    "net's fanout branches reconverge at (0 = not a reconverging stem); "
    "reconvergent fanout is where redundant and hard-to-test faults arise. "
    "mark flags what a deterministic analysis of the whole netlist found: "
    "trig = part of a connected group of 5+ gates that never changed value in "
    "16384 random patterns; clk = logic that drives a register's clock; pay = "
    "logic driven by such a logic-clocked register. Blank = nothing found."
)


#: Ceiling on one request's prompt-plus-reply tokens, used by any stage
#: that can send less when it must. This is a property of the provider and
#: tier rather than of the circuit: Groq's free tier refuses a single
#: request over 8000 tokens per minute, while Gemini or a local Ollama has
#: no comparable limit. 0 disables the check entirely.
#: ``LLM_REQUEST_TOKEN_BUDGET`` overrides the default.
DEFAULT_REQUEST_TOKEN_BUDGET = 7000
REQUEST_TOKEN_BUDGET_ENV_VAR = "LLM_REQUEST_TOKEN_BUDGET"

#: Characters per token, deliberately on the low (= token-count-high) side
#: so the estimate errs toward sending less. Measured against what the
#: provider actually billed, not a local tokenizer: a c7552 targeting
#: prompt came back at 4313 real tokens for what 2.3 chars/token had
#: estimated at 3620, so the compact CSV encoding -- short numeric fields,
#: many separators -- runs denser than prose. Under-counting here is the
#: dangerous direction, since it is what lets an over-budget request
#: through to be refused.
_CHARS_PER_TOKEN = 1.9


def request_token_budget(explicit: int | None = None) -> int:
    """Resolve the per-request ceiling: explicit, else environment, else default."""
    if explicit is not None:
        return max(0, int(explicit))
    raw = os.environ.get(REQUEST_TOKEN_BUDGET_ENV_VAR, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_REQUEST_TOKEN_BUDGET


def estimate_prompt_tokens(text: str) -> int:
    """Pessimistic token count for a built prompt.

    Not a real tokenizer -- there is no single one across providers -- just
    a conservative proxy, good enough to decide whether to send less before
    the provider refuses the request outright.
    """
    return int(len(text) / _CHARS_PER_TOKEN)


#: Headroom added to every stage's reply budget for a reasoning model's
#: hidden trace. gpt-oss spends real tokens thinking before it answers
#: (~700 measured on Groq) and that trace counts against the same budget
#: even though it never appears in the response, so a reply bound sized
#: only to the visible answer truncates it mid-output.
REPLY_OVERHEAD_TOKENS = 1200


def reply_budget(answer_tokens: int) -> int:
    """Completion bound for a stage expecting ``answer_tokens`` of answer.

    Every stage passes one. Left unset, some endpoints reserve a large
    default against a per-minute token budget and refuse the request
    outright before the model runs -- Groq's free tier rejects a ~6k-token
    prompt that way, reporting ~9.9k "requested" against an 8k limit.
    """
    return max(0, int(answer_tokens)) + REPLY_OVERHEAD_TOKENS


#: Where cached model outputs for the combinational stages live. Same
#: on-disk pattern as the sequential path's ``seq_llm1_cache.json``.
DEFAULT_CACHE_PATH = os.path.join(os.getcwd(), "outputs", "comb_llm_cache.json")
CACHE_ENV_VAR = "LLM_RESULT_CACHE"


def cache_enabled() -> bool:
    """``LLM_RESULT_CACHE=0`` turns the cache off for a fresh model call."""
    return os.environ.get(CACHE_ENV_VAR, "1").strip().lower() not in ("0", "false", "no", "off")


class LLMResultCache:
    """Remembers a stage's model output for an identical netlist and setup.

    A repeat run of the same circuit through the same model with the same
    prompt inputs would get, at best, the same answer at full price. The
    key is the netlist text plus everything else the prompt depends on, so
    a change to any of them is a miss, never a stale hit.
    """

    def __init__(self, storage_path: str | None = None):
        from pyatpg.core.memory import CircuitMemory

        self._memory = CircuitMemory(storage_path=storage_path or DEFAULT_CACHE_PATH)

    @staticmethod
    def _key(stage: str, source_text: str, **params: Any) -> str:
        settings = ",".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{stage}|{settings}\n{source_text}"

    def get(self, stage: str, source_text: str, **params: Any) -> Any:
        hit = self._memory.find_match(self._key(stage, source_text, **params))
        return hit.get("results") if hit else None

    def put(self, stage: str, source_text: str, value: Any, **params: Any) -> None:
        self._memory.remember(self._key(stage, source_text, **params), value, {"stage": stage})


def context_gate_limit(explicit: int | None = None) -> int:
    """Resolve the pool cap: explicit value, else environment, else default."""
    if explicit is not None:
        return max(0, int(explicit))
    raw = os.environ.get(CONTEXT_GATE_LIMIT_ENV_VAR, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_CONTEXT_GATE_LIMIT


def compact_profile_line(gate_id: str, features: dict[str, Any]) -> str:
    """One gate as a CSV row matching :data:`COMPACT_HEADER`."""
    role = features.get("node_type") or "gate"
    return ",".join(
        (
            str(gate_id),
            str(features.get("gate_type", "")),
            str(role),
            str(features.get("fan_in", 0)),
            str(features.get("fan_out", 0)),
            str(features.get("level", 0)),
            str(features.get("cc0", 0)),
            str(features.get("cc1", 0)),
            str(features.get("co", 0)),
            f"{float(features.get('toggle_rate', 0.0)):.4f}",
            str(features.get("reconvergence", 0)),
            str(features.get("mark", "")),
        )
    )


def compact_profile_block(rows: list[tuple[str, dict[str, Any]]]) -> str:
    """Header plus one row per (id, features) pair."""
    return "\n".join([COMPACT_HEADER, *(compact_profile_line(g, f) for g, f in rows)])


def rarity_key(profile: dict[str, Any]) -> tuple[float, float]:
    """Rarest-toggling first, hardest-to-observe breaking ties.

    The ordering every stage has always used; kept in one place so the
    pool, the prompt and the tests agree on it.
    """
    features = profile.get("features", {}) or {}
    return (features.get("toggle_rate", 1.0), -features.get("co", 0))


def _anomaly_score(profile: dict[str, Any], by_id: dict[str, dict], graph: Any) -> float:
    """How out of place a gate's activity is among the gates around it.

    Zero for a gate that looks like its neighbours; larger the further its
    toggle rate sits below the median of the nets feeding it and the nets it
    feeds. This is the "rare relative to its neighbourhood, not merely rare"
    signal the targeting prompt asks the model to look for -- a trigger is
    built to fire almost never while its inputs are ordinary nets, and a
    payload XOR sees one ordinary input and one almost-silent one.
    """
    if graph is None or not hasattr(graph, "G"):
        return 0.0
    gid = str(profile["gate_id"])
    features = profile.get("features", {}) or {}
    own = float(features.get("toggle_rate", 0.5))
    g = graph.G
    if gid not in g:
        return 0.0
    neighbours = list(g.predecessors(gid)) + list(g.successors(gid))
    rates = [
        float(by_id[n]["features"].get("toggle_rate", 0.5))
        for n in neighbours
        if n in by_id
    ]
    if not rates:
        return 0.0
    ref = median(rates)
    if ref <= 0:
        return 0.0
    # Ratio of neighbourhood activity to own activity, minus 1 so an
    # ordinary gate scores 0; a gate an order of magnitude quieter than
    # its surroundings scores ~9.
    score = ref / max(own, 1e-6) - 1.0

    # A 2-input XOR/XNOR whose inputs toggle at very different rates is
    # the payload signature, whatever its own rate looks like.
    if features.get("gate_type") in ("XOR", "XNOR"):
        preds = [
            float(by_id[n]["features"].get("toggle_rate", 0.5))
            for n in g.predecessors(gid)
            if n in by_id
        ]
        if len(preds) == 2 and min(preds) > 0:
            score += max(preds) / min(preds) - 1.0
    return max(0.0, score)


def build_candidate_pool(
    profiles: list[dict[str, Any]],
    graph: Any,
    limit: int,
    must_include: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pick which gate profiles a prompt should carry.

    Returns the chosen profiles in rarity order and a list of notes saying
    how the pool was built. With ``limit`` of 0, or a circuit no bigger than
    the limit, every profile is returned and the notes say so.
    """
    total = len(profiles)
    if limit <= 0 or total <= limit:
        return sorted(profiles, key=rarity_key), [
            f"All {total} gate profiles were sent to the model."
        ]

    by_id = {str(p["gate_id"]): p for p in profiles}
    # Forced gates come first, strongest first, but never more than the pool
    # holds: an uncapped list (hundreds of flagged gates on a large design)
    # kept every pool over the request budget, so the caller's shrink loop
    # never terminated -- one s38417 run spent an hour rebuilding prompts.
    forced = list(dict.fromkeys(g for g in (must_include or []) if g in by_id))[:limit]

    rarest = sorted(profiles, key=rarity_key)
    hardest = sorted(
        profiles, key=lambda p: -(p.get("features", {}) or {}).get("co", 0)
    )
    scored = [(p, _anomaly_score(p, by_id, graph)) for p in profiles]
    anomalous = [p for p, s in sorted(scored, key=lambda ps: -ps[1]) if s > 0]
    reconvergent = sorted(
        (p for p in profiles if (p.get("features", {}) or {}).get("reconvergence", 0) > 0),
        key=lambda p: -(p.get("features", {}) or {}).get("reconvergence", 0),
    )

    budget = max(0, limit - len(forced))
    quotas = (
        (rarest, int(budget * _POOL_SHARE_RAREST)),
        (hardest, int(budget * _POOL_SHARE_HARDEST_TO_OBSERVE)),
        (anomalous, int(budget * _POOL_SHARE_ANOMALOUS)),
        (reconvergent, int(budget * _POOL_SHARE_RECONVERGENT)),
    )

    chosen: dict[str, dict[str, Any]] = {g: by_id[g] for g in forced}
    counts = []
    for ranked, quota in quotas:
        taken = 0
        for p in ranked:
            if taken >= quota:
                break
            gid = str(p["gate_id"])
            if gid not in chosen:
                chosen[gid] = p
                taken += 1
        counts.append(taken)
    # Top up from the rarity ranking so the pool is always exactly `limit`
    # when the circuit allows it.
    for p in rarest:
        if len(chosen) >= limit:
            break
        chosen.setdefault(str(p["gate_id"]), p)

    pool = sorted(chosen.values(), key=rarity_key)
    share = 100.0 * len(pool) / total if total else 0.0
    notes = [
        f"Sent {len(pool)} of {total} gate profiles to the model ({share:.1f}%): "
        f"{counts[0]} rarest-toggling, {counts[1]} hardest-to-observe, "
        f"{counts[2]} anomalous for their neighbourhood, "
        f"{counts[3]} most-reconvergent fanout stems"
        + (f", plus {len(forced)} flagged by the structural detector" if forced else "")
        + f", topped up from the rarity ranking. Set {CONTEXT_GATE_LIMIT_ENV_VAR}=0 "
        "to send every gate."
    ]
    return pool, notes
