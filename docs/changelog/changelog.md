# PyATPG development log

What changed, why, and what it measurably did. Newest first. Older entries
refer to research scripts (`scripts/`), experiment netlists (`experiments/`)
and results that the team keeps outside this repository.

## 2026-11-05: prepared for open-source use

- **Docs.** The README is now a short user guide. New: `docs/cli.md`,
  `docs/web-app.md`, `docs/configuration.md`, `docs/how-it-works.md` (the
  pipeline internals moved out of the README), `CONTRIBUTING.md`,
  `CITATION.cff`, `.env.example`, and `examples/` with four Trojan-inserted
  circuits and their expected results.
- **CLI.** `--full-scan` (circuits with flip-flops could only take the
  sequential path from the command line before), `--no-llm`, and keys from
  the environment or `.env` are now used as the help text already said
  (only `--api-key` was). The report now prints the Trojan verdict and, when
  Trojan gates are known by name, the *Trojan check* with the cone orderings.
- **R+S is the default ordering without a model.** The no-model fault list
  used to fill its cone slots in root-rarity order alone (R); it now takes
  cones in turn from `structural_score`'s ranking and root-rarity order,
  exactly as the R+S control. On the held-out sets R+S put the whole Trojan
  in the list on 28 of 56 circuits against 8 for R, and tied the LLM (9-6).
  On the examples the targeted list now holds every Trojan gate on c3540,
  s5378 and s9234 (was 2, 0 and 2), with 59, 93 and 80 patterns. With a
  model, the no-model baseline it is compared with is now R+S, so
  `llm_only_hits` measures the model against the score, not against
  rarity.
- **Packaging.** `pyproject.toml`: `pip install -e .` installs the `pyatpg`
  and `pyatpg-trojan` commands.
- **Trojan generator in the package.** The held-out sets' generator
  (`scripts/make_trojan_variants.py`, kept outside the repository) is now
  `pyatpg/trojan/rare_trigger.py` with the `pyatpg-trojan` command, the same
  algorithm (regenerating c3540_gen_uncommon_s10 and s5378_gen_rare_s9 from
  their hosts gives byte-identical files). The Trust-Hub typo fix it also
  applied to three Trust-Hub files is not included.
- **Removed** (unused by the app and the CLI): `pyatpg/automation` (batch
  runs driven by scripts not in this repository), six research-only
  `pyatpg/evaluation` modules, `docs/automation.md`, the generated LaTeX
  changelog and its converter, and frontend template leftovers (`App.tsx`,
  `App.css`, Vite/React logos, a duplicate logo, a third-party character
  image on the 404 page).

Numbers marked *detector only* come from running the trigger detector on the
full-scan model with no ATPG and no LLM (a few seconds per circuit). Numbers
from full runs say so.

## 2026-10-27: docs and database files regrouped

- `project_architecture.md` moved to `docs/architecture.md`; the run-database
  description moved from `docs/run-database.md` to `db/README.md`, next to
  `db/schema.sql`; this log and its LaTeX generator moved to `docs/changelog/`
  (the generator resolves paths from its own folder, so only its docstring
  changed). Links updated in `README.md`, `docs/`, `db/` and the `run_db.py`
  docstring.
- `.gitignore`: the team's local material now lives in one ignored `archive/`
  folder; the entries for folders that no longer exist were removed.

## 2026-10-27: MIT licence; ATALANTA fetched at build time, not tracked

The repository is to go public as PyATPG's open-source release, so it now
carries only the team's own code under the MIT licence (`LICENSE`).

- **ATALANTA removed from the repository** (57 files, including the Windows
  debug `Atalanta.exe` that pops a modal dialog). Its README limits it to
  teaching and research use, so it is not ours to relicense. `build.sh` now
  clones [hsluoyz/Atalanta](https://github.com/hsluoyz/Atalanta) at commit
  `a8e07fe` and applies `patches/atalanta.patch`, our only changes to it (CPU
  time from `getrusage`, since `times()` printed 0 for short runs; debug prints
  only under `-DDEBUG`). An existing local `Atalanta/` is built as it is and
  stays on disk, now git-ignored.
- **Checked under WSL (Linux, as on Render):** the build's clone, patch,
  static link and c17 smoke test pass; with a fixed seed (`-s 1`) the fetched
  build writes byte-identical test files to the previous build on s38417,
  s35932 and pci.

## 2026-10-23: guide feedback; definition, LLM layer, formula checks, scale, generalisation

Five points from the guide's review, answered in the paper
(`Capstone/presentation_stuff/mock_paper.tex`, backup `.bak-2026-10-23`) with
new measurements; no model call, the held-out results are unchanged.

- **"Moderately rare" defined** (new Section III-B): relative to the host and
  budget, `p_h(B) < p < theta`, with `place(p) >= 1 + N_b(p)`.
  `scripts/analyse_rarity_horizon.py` -> `results/rarity_horizon.json`: the
  rarity-ordered list stops inside cones of rarity 0 (s13207, s15850, s38417)
  or 1/4096 (s38584), because each host has 16-47 benign never-toggling cones;
  benign cones rarer than 0.1 / 1 / 4%: 38-114 / 73-246 / 185-322. Best/worst
  case table by trigger probability. Validation: Clopper-Pearson intervals
  (0 of 4096 -> p < 0.09%), a second 4096-pattern sample moves triggers by at
  most 0.78 pp (6 of 28 labels flip at a boundary, none crosses 5%; two
  uncommon triggers at 4.1 and 4.6%), the independence product is off by
  0.60-2.19x, and the Atalanta liveness check.
- **LLM layer described** (new Section IV-C and table): keys (env/.env,
  per-request keys never logged or stored, fallback chain), token sizing,
  batching, parsing, retries, timeouts, cache, Groq's per-organisation limits.
  Measured: the chars/1.9 estimator is within -4 to +1% of billed tokens on
  full cone batches, *not* pessimistic as the code comment says (1.8x high only
  on a one-cone prompt); the 1,000-token margin under 8k TPM absorbs it.
- **Formulas checked against code and sources**: SCOAP (Goldstein) and Eq. 1
  (`structural_score`) match the code; every sign-test p in the verdict table
  recomputed (all correct; 0.062 now printed as 0.0625); sign-test formula and
  minimum win counts added; Bonferroni note (pooled still passes at 0.025,
  set 1's 0.031 would not); tester cycles cited to Bushnell-Agrawal (exact
  formula changes reductions by <= 0.12 pp); detector strength combination
  stated as hand-set. Corrected: the list-size rule (Eq. 6: 200 on the four
  hosts, 20 on s35932, not "30% of the circuit"), stale set-1 ranges (XOR
  matches 77-247, lists 246-436, benign matches 76-242), and two wrong
  explanations: s38584 never s4 is ranked 116th because its trigger sits inside
  a rare payload's cone (host net 0.76%), not because of larger benign cones;
  of set 1's single-payload uncommon Trojans only s38417's is inside a host
  cone. References: jayasena2023 volume/pages removed (DOI only); added
  Parker-McCluskey, Clopper-Pearson, ITC'99, IWLS 2005, Groq rate limits.
- **Runtime and scalability** (new Section V-G, two tables).
  `scripts/measure_scaling.py` -> `results/scaling/`: no-model selection
  takes 1.4-3 s on the hosts, 191 s and 4.7 GB on vga_lcd (253k gates); rare
  cones grow to 17,819 there, so the 4-request cap shows the model 1.8% of
  them. LLM time per request (36-48 s) equals tokens / 8,000 TPM: the rate
  limit, not the model, sets it.
- **Generalisation** (new Section VI-B and table): unseen trigger instances on
  the development hosts 11 of 16 (6/8 then 5/8), unseen hosts 3 of 8 (LLM's
  median trigger rank 56 vs 7), unseen shapes untested; the floor holds for any
  trigger by construction.
- **Score weights** (new Section V-F; post hoc, nothing adopted).
  `scripts/score_sensitivity.py` -> `results/score_sensitivity.json`: each of
  the 9 weights dropped / halved / doubled, R+S rebuilt on all 42 held-out
  netlists (shipped weights reproduce the reported lists exactly). Pooled R+S
  still beats both controls under 22 of 27 changes (whole Trojans 6-19 vs 11),
  no loss on `never` under any. Failures: `leaf_lvl` or `leaves` dropped or
  halved, `leaf_fo` doubled; the other six terms barely matter. Dropping
  `leaf_fo` would give 19 -- found on the test set, so not adopted.
- Workflow figure text updated (5 hosts, 77-247 matches). Paper now 12 pages.

## 2026-10-18: rarity blind spot as the claim; random and structural controls

The project's claim is now the rarity blind spot (C1) and whether a
structural ranking fixes it (C2) and the LLM beyond its own rules (C3); see
`experiments/llm-vs-standard/README.md`. Changes for measuring that, made
before any held-out run:

- **Leaky worked example removed** (`targeting.py`, prompt `c3`). The cone
  prompt's worked trigger was `AND2 AND4x2 NOR2x8, leaves 16`, the shape of
  the generated test Trojans (`AND2x9 AND4x2`, 16 leaves). The dev LLM figures
  of 2026-10-12 (5 of 8 `rare`/`uncommon` wins; 169 alternating) were made with
  it and overstate the model. The new example has another size and gate mix;
  a test guards it.
- **Control orderings** (`targeting.py`, `cones.py`). Every cone-view
  selection also builds rarity-only (R), rarity + random order (R+Rand, seeds
  1-3) and rarity + `structural_score` (R+S, the prompt's rules as a hand-set
  formula) at the same budget and floor. Scored in the report under
  `trojan_ground_truth.orderings`, shown in the *Trojan check* panel.
- **`scripts/summarize_orderings.py`** applies the decision rule to a folder of
  downloaded reports.
- **Dev check, no model** (`dev/`, selection only): on the 8 `rare`/`uncommon`
  files R+S found more Trojan gates than R on 3 (15 vs 5, 12 vs 2, 15 vs 5) and
  the same on 5; R+Rand's median never beat R; no ordering lost anything on the
  `never` files. That most files tie is why the rule became a sign test
  (p ≤ 0.05, ties dropped) instead of "more on 6 of 8".

## 2026-10-12: first test-set run; LLM and rarity cones in turn; a held-out set

Prompted by the first run of the 15-file test set (`experiments/llm-vs-standard`,
now `dev/`): compare mode, full scan, Groq gpt-oss-120b, cone view.

### What the 15 runs showed

- **Standard vs targeted.** 92.8-96.1% fewer patterns and tester cycles
  (5.0-5.5M → 0.21-0.38M); standard ATPG needed 1,164-4,085 of its patterns,
  in order, to detect every Trojan fault, the LLM-targeted set 67-199.
  Generation time 4-6 s for standard ATPG, 13-219 s with the LLM.
- **With vs without the LLM: no net gain.** 121 of 200 Trojan gates with the
  LLM's cones in its slots, 120 with rarity-ordered cones. The LLM won 5 of the
  8 `rare`/`uncommon` files (1.00 vs 0.17-0.33 recall) and tied 3; it lost 6 of
  the 7 typo-fixed/`never` files (0.13-0.92 vs 1.00). It ranked triggers that
  fire in 0.1-4% of patterns 1st-7th where rarity ranked them 27th-224th, and
  never-firing triggers 8th-40th where rarity ranked them 1st-3rd.
- **The circuit-analysis hints were noise.** They took the model's slots first:
  83 gates over 15 runs, none a Trojan gate.
- **Two dead Trojans.** In `s38584_gen_never_s1/s2` Atalanta proves
  `Tj_Trigger` stuck-at-0 redundant: a tap is tied to a constant in that host,
  so the trigger can never fire (standard ATPG detected 11/30 and 5/24 of their
  Trojan faults).

### Changes (all chosen on those 15 files)

- **Cones in turn** (`targeting.py`). With the LLM, the slots take whole cones
  from its ranking and from root-rarity order alternately; without it, from
  rarity order only. Replaying the 15 runs' recorded LLM scores: 126 of 200 at
  `llm_share` 0.5.
- **`llm_share` default 0.5 → 0.8.** At 0.5 the cones had ~95 slots and the two
  rarest s38417 cones are 44-gate host blocks, so neither ordering got far.
  Replayed at 0.8: **169 of 200** with the LLM vs 120 with rarity order, below
  rarity on no file except one gate of `s38417-T100_fixed`.
- **`root_reg` in the cone row**, and a line in the prompt: a cone whose root
  is itself a register input is usually next-state logic. In 2 of the 15 runs
  the cones ranked above the Trojan were all such cones (rank 8 → 1 and 24 → 7
  without them). Not replayable: it changes what the model sees.
- **Circuit-analysis hints removed** from targeting (both views).
- **Generator** (`make_trojan_variants.py`): the trigger class is checked on
  the built netlist's simulation, not the tap-independence estimate, and
  Atalanta must detect `Tj_Trigger` stuck-at-0 (60 s limit) or the candidate is
  drawn again.

### Held-out set (`experiments/llm-vs-standard/heldout/`)

- 12 circuits from seeds 3 and 4 (s38417 and s38584 hosts × never / rare /
  uncommon), generated after the changes above and not run before. Because
  every change here was chosen on `dev/`, the 169-vs-120 above is not a
  result; the held-out runs are.
- All 12 triggers in band and able to fire; generating them rejected 9 dead
  candidates (all on s38584 `never`) and 2 out of band.
- Checked with no model: the detector flags none of the Trojans' gates. The
  trigger's cone ranks 2nd-3rd by rarity in 3 `never` files and 41st-233rd in
  the other 9 (including `s38584_gen_never_s4` at 116th: many s38584 host cones
  never toggle either). The no-LLM list finds the whole Trojan in those 3 and
  at most 1 gate in the 9.

## 2026-10-12: cones for the LLM, standard-vs-targeted comparison, a test set

Prompted by the question the paper has to answer: is LLM-targeted ATPG better
than standard ATPG, and is the LLM the reason. The runs below this entry
could answer neither: the LLM was never shown a Trojan gate, the no-LLM list
was never run through ATPG, and the detector found every Trojan on its own.

### Standard vs targeted, measured three ways (`efficiency.py`, `service.py`)

- **Problem.** A compare run produced standard ATPG and the LLM-targeted set.
  The no-LLM list was scored for recall but never run, so "what did the LLM
  change in patterns and coverage" had no answer. Nothing measured how many
  patterns each set needs to detect the Trojan's faults.
- **Change.** On a compare run with the LLM, the no-LLM list also goes through
  ATALANTA (same budget, same search ladder, its own artifact names). A new
  report section `three_way` (and page panel) lists the three sets: patterns,
  tester cycles (patterns × (scan cells + 1)), generation time (ATALANTA, plus
  feature extraction and LLM time where used), whole-circuit coverage, and for
  a design with known Trojan gates: Trojan stuck-at faults detected, patterns
  in order needed to detect them all, and an oracle minimum (greedy cover over
  the ground truth). New `faultsim.detection_masks` gives, per fault, which
  patterns detect it.
- **Effect (from the 10-11 reports' own patterns).** s38417-T200: standard
  ATPG needs its first 2,406 of 3,227 patterns to detect all 28 Trojan faults;
  the LLM-targeted set 107 of 118; oracle minimum 8. s35932-T200: 1,956 of
  3,077 vs 194 of 194 (17 of 20 detectable); oracle minimum 5.

### The LLM scores rare cones, not gate rows (`cones.py`, `targeting.py`)

- **Problem.** The model was shown one row of numbers per gate, the same
  numbers the heuristic ranks by. It never saw which gate connects to which,
  so it could not judge structure, the one thing it might do better.
- **Change.** New setting `llm_view` (default `cones`). Every rare net (rarer
  value in under 5% of 4096 random patterns) with the fanout-free logic under
  it (4 levels, 40 gates) is a cone, summarised in one anonymised row: size,
  depth, gate mix, tapped nets and how common, shared and spread across the
  circuit's depth they are, root rarity, and what the root drives. The model
  scores each cone; its slots take whole cones (plus up to 4 gates the root
  drives), highest first. Without the model the same slots take whole cones
  in root-rarity order, so the two lists differ only in the cones chosen.
- **Effect (detector only, no model).** s38417: ~198 cones, all scored in 3
  requests of 70. Trust-Hub's s38417-T100/T200 trigger cones rank 3rd by
  rarity; s35932-T200's is the circuit's only rare cone.

### A test set where the detector and rarity order fail (`experiments/llm-vs-standard/`)

- **Problem.** On every Trust-Hub circuit run so far the detector found the
  whole Trojan, so the LLM had nothing left to find.
- **Change.** New script `scripts/make_trojan_variants.py` and 15 netlists:
  Trust-Hub s35932-T200 and s38417-T100/T200 with the `Tj_OUT1`/`Tj_OUT2`
  typo repaired, and clean-written combinational Trojans (16 tapped nets,
  AND tree, XOR payloads, no duplicate input) in the Trust-Hub Trojan-free
  s38417 and s38584, two seeds each of three trigger classes fixed by rule:
  never fires, 0.1-1%, 1-4% (two files landed at 0.098% and 4.03%).
- **Effect (no model).** The detector flags nothing strong on any of the 15,
  and none of the generated Trojans' gates at all. The no-LLM cone list
  finds every Trojan gate in the 7 typo-fixed and never-firing files and none
  in the 8 rare and uncommon ones, whose cones rank 27th-224th by rarity.
  With a stand-in model that favours widely spread taps (not an LLM),
  s38584_gen_uncommon_s1 gave recall 1.00 vs 0.33 and 30/30 vs 24/30 Trojan
  faults detected: the measurement can show a win; whether the LLM produces
  one is what the next runs decide.

### Admin page

- Settings rendered every value in a number box, so an on/off setting showed
  empty (`true` is not a number) and a text setting could not be entered.
  On/off settings now get a selector and text settings a text box.

## 2026-10-12: first runs on the 10-11 code

Seven full runs (compare mode, full scan, LLM on, Groq gpt-oss-120b), all
on commit `3519624` (their reports carry the new `targeting.context` fields).

### Combinational triggers: found whole

- s35932-T200: recall 0.20 → 1.00, verdict inconclusive → Trojan suspected
  (0.73). 14 gates flagged strong: the 10 named Trojan gates and the 4
  payload gates `WX6484/6999/7783/8266`, each `NOR3(..., Tj_Trigger)`.
  The netlist does not name them as Trojan, so the report's precision of
  0.71 is really 14/14.
- s38417-T100: recall 0.91 → 1.00, flagged 11/11 (precision 1.00),
  including `n4263_Tj_Payload`.
- s38417-T200: recall 0.36 / 0.71 (with / without LLM) → 1.00 / 1.00,
  flagged 14/14 (precision 1.00), including the 4 `*_Tj_Payload` gates.

### No false alarms on the Trojan-free netlists

- s38417 (Trust-Hub TjFree): inconclusive (7 never-toggling leads, 232
  XOR-shape leads), nothing strong.
- s38584 (Trust-Hub TjFree): inconclusive (20 never-toggling leads, 111
  XOR-shape leads), nothing strong.
- s35932: no Trojan indicated, but the upload was the ISCAS non-scan
  netlist (16,065 gates, 89.8% coverage), not the Trust-Hub TjFree scan
  version of the T-variants' host (~6,800 gates). It needs rerunning with
  that file.

### The hand edit the rule relies on is a typo in Trust-Hub's netlists

- In s35932-T200/T300 and s38417-T100/T200 the duplicate input is
  `AND4X1 Trojan1234_NOT (.IN1(Tj_OUT1), .IN2(Tj_OUT1), .IN3(Tj_OUT3),
  .IN4(Tj_OUT4))`: `Tj_OUT1` twice and `Tj_OUT2` never read. `Tj_OUT2` is
  then pruned as dead logic under full scan, which is also why it is never
  in the ground truth.
- So the `trigger_tree` rule anchors on a benchmark artifact. A Trojan
  written without that slip has no duplicate input and is not found (the
  synthetic check of 10-11 showed the same). The paper has to say so; the
  combinational-trigger result is not evidence the rule generalises.

### s35932-T300: the Trojan is pruned before analysis

- The netlist does contain it: the same `Tj_OUT1..8` trigger tree and a
  ring-oscillator payload (`TjPayload1..24`, inverter chains `Stage1..4`
  joined by muxes). Two more Trust-Hub typos break it: the mux selects read
  `TjTrigger` while the trigger net is `Tj_Trigger`, and the third chain ends
  at `Stage3_16` while the next mux reads `Stage3`. Both undriven nets
  become free inputs.
- Nothing in the Trojan reaches an output or a register (its trigger cone is
  20 gates, all dead ends), so the full-scan conversion prunes all 44 of its
  gates (`pruned_gates: 44`), silently, and the report has no ground truth.
- A payload that drives nothing observable is outside what stuck-at ATPG can
  test by definition. Dead logic is a structural sign, though: pruned gates
  per netlist are 0 (s35932 ISCAS, s38417 TjFree), 12 (s38584 TjFree), 10
  (ISCAS s1238_scan), against 44 for s35932-T300 and 251 for s38584-T300
  TjIn. Not yet acted on.

### LLM: no measurable contribution, and none possible with the detector on

- `llm_only_hits` and `baseline_only_hits` are empty on every run, and
  recall with and without the LLM is identical (1.00 on all three Trojan
  runs).
- No Trojan gate was ever in the pool the model scored: every one was
  already a forced detector finding, and those are excluded from the pool.
  With the detector in targeting, the LLM cannot affect Trojan recall.
  Round 2 (`detector_in_targeting` off) is the only run that measures it.
- The scores are selective (on s38417-T200, 317 of 444 gates scored 0-9;
  95 scored 30+), every pool gate was scored, and 6-10 gates per run came
  from the circuit analysis's regions. About 25k tokens and 130 s of model
  time per run (5 requests).

### Unchanged

- s38417 still aborts 16-54 faults on host nets; targeted coverage 92-96%.

## 2026-10-11: combinational triggers, LLM selection on a heuristic floor

Prompted by the first held-out runs (compare mode, full scan, LLM on):
s35932-T100/T200/T300, s38417-T100/T200/T300, s38584-T100/T200/T300.
s38584 and s35932-T100 were found whole; the rest showed four problems.
Recall with / without the LLM:

- s35932-T200: 0.20 / 0.20, verdict inconclusive.
- s38417-T100: 0.91 / 0.91, Trojan suspected (0.78).
- s38417-T200: 0.36 / 0.71, Trojan suspected (0.78).

### Combinational trigger trees (`trigger_detector.py`)

- **Problem.** s35932-T200 and s38417-T100/T200 have no trigger register:
  Tj_OUT1..8 feed only Tj_OUT1234/5678, which feed only Tj_Trigger, which
  drives the payload gates. Every strong rule needs a register or an output
  splice. On s35932-T200 the leaves are 1 in ~40% of random full-scan
  patterns, so no rarity sign sees the tree; on s38417-T200 Tj_OUT5 toggles 4
  times in 16384 patterns and split the never-toggling cluster. The payload
  gates (`n4263_Tj_Payload`, `n4122/4149/4160/4161_Tj_Payload`, probability
  0.06 to 0.44) were in no list at all.
- **Check before changing it.** A fanout-free tree of rare gates is not
  evidence: clean ISCAS s38584 has a 19-gate one with a root that never
  toggles, s838 several 11-gate ones. What the three trees share is the hand
  edit inside them, `NOR(Tj_OUT1, Tj_OUT1, ...)`. Duplicate-input gates do
  occur in clean ISCAS-85 (5 gates over c1908, c2670, c3540), so the edit
  alone stays a lead.
- **Change.** New evidence `trigger_tree` (0.70) and `trigger_payload` (0.70,
  payload family). From a duplicate-input gate, follow the single consumer
  while it is a conjunction over it (its rare value needs this gate's rare
  value; an XOR or OR payload is not), to a root rare in under 5% of
  patterns. The root and the fanout-free tree under it (at least 5 gates, at
  most 3 levels) are the trigger; the gates the root drives are the payload,
  and their other inputs are host signals the region never grows into.
- **Effect (detector only, synthetic).** The Trust-Hub netlists for
  s35932/s38417 are not in the repository, so the rule was checked on their
  shape inserted into clean ISCAS s38417, s35932, s15850 and s38584 (rare
  and common leaves, 1 or 4 payload gates): every Trojan gate flagged strong,
  no host gate flagged strong. Without the duplicate input nothing is found,
  which is the rule's limit. Unchanged: clean s15850, s38417, s35932,
  s38584 (ISCAS and Trust-Hub TjFree), the clean ISCAS-85 circuits with
  duplicate inputs (nothing strong), s38584-T300 TjIn/TjIn_fixed (identical
  output), and all 120 generated Trojan netlists under `atpg testing/c*`.

### LLM selection: heuristic floor, same budget, every gate scored (`targeting.py`)

- **Problem.** On s38417-T200 recall *with* the LLM was 0.36 against 0.71
  without, for three reasons visible in the report.
- **Cause 1: the model's picks replaced the heuristic's.** The heuristic
  ranks all 9386 gates; the model saw 306 (3.3%), and Tj_OUT5..8 were not
  among what it kept.
- **Cause 2: the two lists were not the same size.** 163 gates with the LLM,
  204 without (T100: 131 vs 206), so part of the gap was budget, not choice.
- **Cause 3: each batch kept only its best 34 gates for the next.** A gate
  dropped there was gone, and the report did not record the pool, so which
  of the two happened could not be told.
- **Missed signal.** The circuit-analysis model, which sees the 120 rarest
  gates, named Tj_OUT5678, Tj_OUT7 and Tj_OUT8 as a suspicious region in the
  same run, but it ran after ATPG and fed nothing.
- **Change: same budget, heuristic floor.** Both modes test the same number
  of gates (the existing 30% / 20-200 budget, or the new `target_gates`
  setting). LLM runs keep the heuristic's top picks and the model fills the
  remaining `llm_share` of the list (new setting, default 0.5); slots it
  leaves empty (no gate scored 30+) go to the heuristic's next picks. An LLM
  run can lose at most the heuristic's last picks, and `llm_only_hits` is
  exactly what the model added.
- **Change: every gate scored.** The model scores every gate it is shown
  (0-100), in independent batches with no trimming between them; scores are
  ranked globally, and gates left out of an answer are asked for once more.
  It is shown only gates outside the floor and the forced detector findings.
- **Change: circuit analysis first.** It now runs before selection, and the
  gates of its suspicious regions of at most 25 gates take the model's slots
  first (it often returns one region spanning its whole 120-gate input,
  which says nothing). The no-LLM list never gets them.
- **Change: logged.** The report's `targeting.context` holds the pool, every
  score, the floor, the model's picks and the heuristic fill; the ATPG page
  shows how many gates the model scored and how its slots were filled.
- **Effect (fake model, s38584-T300 TjIn).** 444 of 13,010 gates scored in 4
  requests of 111 gates under the 7000-token budget (306 seen before); both
  lists 200 gates with the same 100-gate floor. Not yet measured with a real
  model.

### Ground truth leaves out undriven nets (`trojan_metrics.py`)

- s38584-T300 ships its trigger output `Trigger_out` undriven; it was promoted
  to a free input and counted as a Trojan gate no rule could find. T300 TjIn
  ground truth: 1937 → 1936 gates.
- s35932-T300 had no ground truth at all: it reads undriven `TjTrigger` and
  `Stage3`, and no net in the parsed netlist has a Trojan name. The Trojan's
  own module was not in the upload (or drives no output and was removed as
  dead logic); the run needs the whole Trust-Hub folder.

### Verdict suspects (`verdict.py`)

- XOR-shape-only matches are left out of the suspect list when the trigger
  detector has a strong finding: on the s35932/s38417/s38584 runs they hit no
  Trojan gate in 7 of 7 scored runs, and up to 232 of them diluted the list
  (s38417-T100 verdict-suspect precision 0.04).
- The "inconclusive" summary said every lead was an XOR-splice match even
  when the leads were never-toggling groups or duplicate inputs; it now
  counts each kind.

### Not fixed

- s38417 keeps 16 to 36 targeted faults aborted on host nets (`n2107`,
  `n2005`, ...) even at `-b 10000 -L`, holding targeted coverage at 85-89%.
  None are Trojan faults.
- The s38584-T200 rerun selected 385 gates against 389 on 9 Oct (13 and 9
  gates differ), precision 0.67 both times, so the "~115 fewer non-Trojan
  gates" expected from the XOR-shape change on 9 Oct did not appear.
  Whether the 9 Oct run was already on that code is not recorded in the
  report (`runs.backend_commit` in the run database has it).

### Next runs (none of today's changes is measured yet)

- Rerun s35932-T200 and s38417-T100/T200 (compare mode, full scan, LLM on).
  These now count as tuned circuits: report their numbers before and after
  this entry, not as held-out results.
- Re-upload s35932-T300 as the whole Trust-Hub folder.
- Run the Trojan-free versions (s35932, s38417, s38584 TjFree) to confirm the
  new rule raises no false alarm on the real netlists.
- Recall at several budgets: the same circuits at `target_gates` 50, 100 and
  200.
- Repeat runs for run-to-run spread need `LLM_RESULT_CACHE=0` on the
  backend; otherwise every repeat reuses the first run's cached scores.
- Round 2 (`detector_in_targeting` off) after the runs above.

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
