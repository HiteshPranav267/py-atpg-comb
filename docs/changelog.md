# PyATPG development log

What changed, why, and what it measurably did. Newest first. The LaTeX
version (`docs/changelog.tex`) is generated from this file by
`python docs/changelog_to_tex.py`; edit this file, not the `.tex`.

Numbers marked *detector only* come from running the trigger detector on the
full-scan model with no ATPG and no LLM (a few seconds per circuit). Numbers
from full runs say so.

## 2026-10-10: smaller report downloads

Prompted by the s38584-T300 report download: 14.8 MB, too large to analyse.

### Suspects reference vectors by index (`verdict.py`)

- **Problem.** Every one of the 1998 verdict suspects carried the same 3
  test patterns as full 1601-bit strings: 10.5 MB of the 14.8 MB file.
- **Change.** `activating_vectors` now holds indices into the run's
  `test_vectors.vectors`, where each pattern is stored once. Older reports
  with strings still load (the run DB only counts them).
- **Effect.** The same report, re-encoded: 14.8 MB → 5.2 MB.

### Summary export (`Reports.tsx`)

- **Problem.** Even at 5 MB the full report is too big to read or paste into
  an LLM, and most of it is long gate lists repeated across sections.
- **Change.** New *Download Summary* button next to the full export. Lists
  longer than 25 keep their first 25 entries plus a total count (suspects are
  sorted by confidence, so the head is the strongest); test-pattern bit
  strings become `<N-bit pattern>`. The full export is unchanged, for the
  aggregation scripts.
- **Effect.** s38584-T300: ~140 KB summary.

## 2026-10-09: payload muxes, measuring the LLM alone, fewer junk targets

Prompted by the s38584-T200 report (compare mode, full scan, LLM on): recall
258/260, verdict *Trojan suspected* (0.97), no LLM-only hits, targeted
precision 0.48, and 347 of the 419 redundant faults inside the Trojan.

### Payload multiplexer rule (`trigger_detector.py`)

- **Problem.** T200 and T300 both replace output `g10122` with a 2:1 mux
  whose select is the trigger. The parser expands a mux to NOT(s),
  AND(d0, NOT s), AND(d1, s), OR. The 3-gate payload walk from the trigger
  registers reached only part of it, so 2 (T200) to 3 (T300 fixed) mux gates
  were always missed.
- **Change.** New evidence `payload_mux` (0.80, payload family): a mux of
  that shape whose select is already a strong suspect is flagged whole. A
  register's own load mux (select = its clock net) is excluded; without
  that exclusion the rule matched 1024 load-mux gates on T300.
- **Effect (detector only).** T300 TjIn_fixed 2166 → 2169 of 2170 (the last
  miss is `g10122_Tj__ppo`, the host's original output signal). s15850-T100
  precision 0.78 → 0.80. Shipped T300 TjIn unchanged: its select is the
  undriven `Trigger_out`, a free input, so nothing upstream is flagged.

### `detector_in_targeting` setting: measure the LLM on its own

- **Problem.** With the detector forcing ~260 (T200) to ~2000 (T300) gates
  into the fault list, the LLM could add at most the 2 to 5 gates left over.
  `llm_only_hits` was always empty and said nothing about the LLM.
- **Change.** Live setting `detector_in_targeting` (env
  `PYATPG_DETECTOR_IN_TARGETING`, default on). Off: no detector gate is
  forced into the pool or the list, no marks appear in the LLM's table, the
  no-LLM baseline is the plain heuristic, and the run notes say so. The
  verdict still uses the detector. The LLM cache key includes the number of
  forced gates, so an off run never reuses an on run's answer.

### XOR-shape matches forced in only when nothing strong was found

- **Problem.** The older XOR-splice detector forced its matches into every
  targeted list. On T200 19 of its 134 gates were Trojan gates; on T300 181
  of 295. Every one of those was also a trigger-detector finding.
- **Check before changing it.** On the tool's own generated Trojans
  (c432, c1355, c2670, 18 circuits) the XOR shape finds 16 of 18 Trojan
  gates and the trigger detector finds 1. Dropping it everywhere would have
  wrecked those benchmarks.
- **Change.** Its matches are still reported and still verdict leads, but
  join the fault list only when the trigger detector has no finding of
  strength 0.7 or more. Expected effect on T200: the targeted list loses
  ~115 non-Trojan gates and no Trojan gates (not yet confirmed by a full run).

### T200: why its trigger can never fire

- ATPG proved `TrojanTrigger__n37 /0` redundant in both runs: the net
  clocking the Trojan counter can never be 1, so the counter never loads.
- It is **not** the scan enable held at 0: none of the trigger's input nets
  (`n2601`, `n5111`, `n1422`, `n3279`, `n3121`, `n3180`) read `test_se`
  (checked on the shared s38584 host netlist).
- Likely cause: `n3121` and `n3180`, two NANDs sharing input `n1288`, were
  never 0 together in 65,536 random full-scan patterns, while every other
  combination appeared. Not proven exhaustively (35-input cone); Atalanta's
  redundancy result is the proof that the clock is unreachable.

## 2026-10-09: dead triggers found structurally (`9ff5dc4`)

- The "trigger never fires" flag matched net names containing "trig", so
  T200's clock net `TrojanTrigger__n37` was missed and the report said
  `trigger_never_fires: false`.
- Now any stem stuck-at-0 on a net that clocks a logic-clocked register
  counts (the parser models it as Q' = clk ? D : Q, so clk = 1 is
  observable). The run notes report it with or without ground truth.
  Replayed on the T200 report: `true` for both runs.

## 2026-10-09: circuit-analysis prompt anonymised (`23f4f81`)

- The circuit-analysis LLM stage sent real gate ids, the file name and 3000
  characters of Verilog. Trust-Hub names its Trojan logic; on T300 the model
  reported a "Trojan payload carry chain".
- It now gets `g0..gN` aliases and statistics only; aliases in its answer
  are mapped back to real ids. On the next T200 run the model's own wording
  contained no Trojan, counter or module names.

## 2026-10-09: LLM shown gates the detector missed (`cdc0acd`)

- Flagged gates could fill the whole LLM pool: on T300 all 306 slots were
  flagged gates, so the LLM only re-confirmed the detector.
- Flagged gates now take at most 25% of the pool (minimum 20); the other
  views skip flagged gates. On T300 the model sees 230 unflagged gates,
  including one payload gate the detector missed.

## 2026-10-09: free-running timers; reproducible simulation (`8ce6325`)

- T300's 50-cycle window counter (32 registers on the ordinary clock, next
  state depends only on itself) matched no rule: 122 Trojan gates missed,
  recall 0.937.
- New evidence `autonomous_timer` (lead, 0.60) / `trojan_timer` (0.80 when
  it feeds other flagged logic). Zero matches on ~50 clean ISCAS'89
  netlists. Full T300 run afterwards: recall 0.937 → 0.9974.
- Random simulation iterated a set of inputs, so never-toggling counts
  changed between identical runs. Inputs are now sorted.

## Earlier history (2026-07-24 to 2026-10-09, 128 commits)

Summarised by phase from the commit history.

### Phase 1: framework and deployment (Jul 24 to Aug 11)

- Initial PyATPG framework with a FastAPI backend and React frontend.
- Hosted on Render (backend) and Vercel (frontend). Atalanta is compiled
  from source and statically linked at build time, after glibc mismatches
  between Render's build and runtime containers.
- Bench format import and export; Atalanta failures reported loudly instead
  of as 0% coverage; real CPU time and memory on Linux.
- First LLM ranker, a Trojan insertion tool, and removal of legacy ATPG code.

### Phase 2: sequential ATPG and honest comparisons (Aug 14 to Aug 30)

- Sequential ATPG engine with a context-reduced LLM fault prioritiser, and
  its results in the frontend.
- Compare mode made to actually compare deterministic vs LLM-assisted runs.
- Gate names anonymised before the targeting prompt (`c23c039`).
- Cyclic netlists stopped before reaching Atalanta; false
  "feedback loop" errors on dangling signals fixed.

### Phase 3: reliable Trojan generation and evaluation (Sep 2 to Sep 28)

- Trojan placements sampled instead of repeating; a trigger accepted only
  if it can actually activate; two variants never share a payload target.
- Redundant faults told apart from aborted ones.
- Multi-provider LLM support, unattended batch automation, a research
  pipeline, and zero-install runs for collaborators against the hosted API.
- Targeted coverage made honest: effort escalation, whole-circuit pattern
  simulation, compact LLM context, per-request token budgets.
- Exact parsing of synthesized hierarchical Verilog; a full-scan model for
  circuits with flip-flops; MUX payloads; an LLM judge for trigger/payload
  candidates with baselines.

### Phase 4: Trust-Hub designs and the Trojan detector (Oct 1 to Oct 9)

- Up to five rotating LLM keys; Groq `openai/gpt-oss-120b` as the default.
- RTL designs accepted via Yosys synthesis; size and timeout guards for the
  hosted backend.
- Server stays responsive during runs, with live progress, cancellation of
  disconnected runs, an admin page and a PostgreSQL run database.
- Logic-clocked registers modelled (Trust-Hub sequential triggers); trigger
  detector; scan enable held low under full scan; LLM batches that carry a
  shortlist forward.
- Payload splices, hand-edited gates, evidence families that do not double
  count, region completion aiming for every Trojan gate, and recall
  reported with and without the LLM.
