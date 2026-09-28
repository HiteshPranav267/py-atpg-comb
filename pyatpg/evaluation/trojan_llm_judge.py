"""LLM judge for trigger-payload candidates.

Given anonymised local contexts from
:func:`pyatpg.evaluation.trojan_candidates.candidate_context`, the model
returns, for each candidate, the probability that it is an inserted trojan
trigger-payload pair rather than ordinary rare-control logic.

Judgements are cached on disk, keyed by model, prompt version and the
candidate's canonical context, so an identical structure seen in another
variant of the same circuit is never paid for twice -- and a re-run of the
evaluation costs nothing for what it already judged.
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Callable

#: Bump when the prompt text changes: cached judgements under an older
#: version are then ignored rather than silently mixed with new ones.
PROMPT_VERSION = "v2"

_INSTRUCTIONS = """You are auditing a gate-level digital circuit for hardware trojans.

A deterministic pre-filter flagged the candidate structures below. Each one is
a rarely-active gate [suspected-trigger] whose single output feeds -- directly
or through one inverter/buffer [between-trigger-and-payload] -- a 2-input gate
[suspected-payload]. The payload's other input [payload-other-input] carries
an existing signal.

Most candidates are ORDINARY logic: rarely-active enables, decoder terms and
control conditions are everywhere in real circuits and routinely feed AND/OR
gates. A hardware trojan is logic that was ADDED: a trigger built to fire only
under a rare condition, spliced into an existing signal path so that when it
fires it corrupts that signal (flips it, forces it to 1, or forces it to 0).
The payload gate can be of any type (XOR, OR, AND, ...).

Signs of an inserted trigger-payload pair:
- the trigger (almost) never switches -- toggle 0 or close to it -- yet feeds
  a live signal path. This is strong evidence, NOT "dead logic": synthesis
  propagates constants and removes logic that can never change, so a gate
  that never activates but still gates a live signal is exactly what a
  stealthy trigger looks like;
- the trigger exists only to feed this payload and its inputs come from
  unrelated places (very different depths, no shared neighbours) rather than
  from a coherent decoding structure;
- the payload sits in the middle of an otherwise normal path: its other input
  is an ordinary signal (often from a very different depth) whose path the
  payload simply continues;
- removing the trigger and payload would leave a normal, simpler circuit.
Signs of legitimate logic:
- the rare gate is one of several similar gates that share its inputs and
  whose outputs are used together (a decoder, comparator or state condition);
- the payload's other input is just as rare, or the rare signal belongs to a
  larger consistent control structure.
Do not dismiss a candidate merely because its gates look like common gate
types or sit near ordinary logic: an inserted trojan is built from ordinary
gates and is spliced into ordinary logic by design.

Each candidate is a table, one row per gate: id; mark (T = suspected
trigger, V = inverter/buffer between trigger and payload, P = suspected
payload, O = the payload's other input, - = surrounding gate); type; where
(PI primary input, PO primary output, int internal); toggle = fraction of
random input patterns on which the net switched (0 = never); fan_in /
fan_out = total connections, including ones outside the shown
neighbourhood; depth = logic levels below the shallowest gate shown;
inputs = the shown gates driving it ("kext" = k inputs not shown). Some
candidates add SCOAP controllability (cc0~2^, cc1~2^) and observability
(co~2^) as powers of two (higher = harder), and reconv = gates where this
net's fanout branches reconverge.

In a typical circuit at most one candidate, and often none, is a trojan.

Reply with JSON only:
{"results": [{"id": "<candidate id>", "p": <probability 0..1 that it is an
inserted trojan trigger-payload pair>, "why": "<at most 12 words>"}]}
with exactly one entry per candidate, using the ids given."""


class TrojanCandidateJudge:
    def __init__(
        self,
        complete: Callable[[str], str],
        model_name: str,
        cache_path: Path,
        batch_size: int = 20,
        prompt_token_budget: int = 5200,
        seed: int = 0,
        log: Callable[[str], None] = print,
    ):
        """``complete(prompt, n_candidates)`` sends one prompt and returns the raw reply text."""
        self.complete = complete
        self.model_name = model_name
        self.cache_path = Path(cache_path)
        self.batch_size = batch_size
        self.prompt_token_budget = prompt_token_budget
        self.rng = random.Random(seed)
        self.log = log
        self.calls = 0
        self.failed = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache: dict[str, dict] = {}
        if self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _ckey(self, key: str) -> str:
        return f"{self.model_name}|{PROMPT_VERSION}|{key}"

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache), encoding="utf-8")
        tmp.replace(self.cache_path)

    def judge(self, contexts: dict[str, str]) -> dict[str, float | None]:
        """Probability per context key. None only if the model never answered for it."""
        todo = [k for k in contexts if self._ckey(k) not in self.cache]
        self.rng.shuffle(todo)
        # Pack batches by estimated size, not a fixed count: providers cap a
        # single request (Groq's free tier: 8000 tokens including the reply
        # reservation), and contexts vary a lot in length.
        batch: dict[str, str] = {}
        size = _tokens(_INSTRUCTIONS)
        for k in todo:
            t = _tokens(contexts[k]) + 8
            if batch and (len(batch) >= self.batch_size or size + t > self.prompt_token_budget):
                self._judge_batch(batch)
                batch, size = {}, _tokens(_INSTRUCTIONS)
            batch[k] = contexts[k]
            size += t
        if batch:
            self._judge_batch(batch)
        return {k: (self.cache.get(self._ckey(k)) or {}).get("p") for k in contexts}

    def _judge_batch(self, batch: dict[str, str], attempt: int = 0) -> None:
        ids = {f"c{j + 1}": k for j, k in enumerate(batch)}
        body = "\n\n".join(f"### Candidate {cid}\n{batch[k]}" for cid, k in ids.items())
        prompt = f"{_INSTRUCTIONS}\n\n{body}"
        started = time.monotonic()
        try:
            reply = self.complete(prompt, len(batch)) or ""
        except Exception as exc:  # noqa: BLE001 - only a size rejection is handled here
            if _is_too_large(exc) and len(batch) > 1:
                # Our estimate undershot the provider's tokenizer: halve and retry.
                items = list(batch.items())
                half = len(items) // 2
                self._judge_batch(dict(items[:half]), attempt)
                self._judge_batch(dict(items[half:]), attempt)
                return
            raise
        self.calls += 1
        usage = getattr(self.complete, "last_usage", None)
        if callable(usage):
            usage = usage()
        if isinstance(usage, dict):
            self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        parsed = _parse(reply)
        answered = 0
        for cid, key in ids.items():
            entry = parsed.get(cid)
            if entry is not None:
                self.cache[self._ckey(key)] = {**entry, "s": round(time.monotonic() - started, 2)}
                answered += 1
        self._save()
        missing = {k: batch[k] for cid, k in ids.items() if cid not in parsed}
        if missing:
            if attempt < 1:
                # One retry, one candidate per call, for anything the reply
                # dropped or mangled -- a partial answer is common for long batches.
                for k, text in missing.items():
                    self._judge_batch({k: text}, attempt + 1)
            else:
                self.failed += len(missing)
                self.log(f"    judge: no usable answer for {len(missing)} candidate(s)")


def _tokens(text: str) -> int:
    """Conservative token estimate (these tables tokenize densely: ~3 chars/token)."""
    return len(text) // 3 + 1


def _is_too_large(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "413" in text or "too large" in text or "reduce your message size" in text


def _parse(reply: str) -> dict[str, dict]:
    """{"results": [{"id","p","why"}]} -> {id: {"p": float, "why": str}}, tolerant of fences/prose."""
    text = reply.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for item in data.get("results", []) if isinstance(data, dict) else []:
        try:
            cid = str(item["id"]).strip()
            p = float(item["p"])
        except (KeyError, TypeError, ValueError):
            continue
        out[cid] = {"p": min(1.0, max(0.0, p)), "why": str(item.get("why", ""))[:160]}
    return out
