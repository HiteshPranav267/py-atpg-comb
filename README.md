# PyATPG — Hardware Trojan Localization Framework

> A Python framework for finding hardware Trojans in gate-level and RTL designs. It combines ATPG (the ATALANTA engine for combinational logic and full-scan designs, plus a native sequential engine), structural testability analysis, a deterministic Trojan detector, and an LLM that reviews the circuit's gates. It ships as a research platform (FastAPI backend + React frontend) with a CLI and batch scripts.

---

## Overview

PyATPG takes a netlist — ISCAS `.bench`, structural Verilog, or RTL that it synthesizes first — and answers two questions:

1. **Where is the Trojan?** Every gate is profiled (SCOAP testability, toggle rates, structure); a deterministic detector looks for the shapes Trojans are built from (rare trigger trees, logic-clocked registers, payloads spliced in front of outputs, hand-edited gates); and an LLM reviews the most suspicious gates and picks which faults to target. The goal is to flag **every** Trojan gate, and to show what the LLM adds over the run without it.
2. **How well do test patterns exercise it?** ATALANTA generates patterns for the whole circuit (non-targeted) and for the selected gates only (targeted); the patterns are then fault-simulated over the whole circuit and checked against the circuit's rare nets, where a trigger hides.

The repo is one application on the `pyatpg` core: a FastAPI backend (Render) and a React/Vite frontend (Vercel), plus `python -m pyatpg` for the terminal and unattended batch scripts under `scripts/`.

---

## How an analysis runs

`POST /api/atpg/run` (and the CLI) call `AtpgAnalysisService.run()` in `pyatpg/atpg/service.py`. Every step below reports its stage to the progress tracker the web page polls.

### 1. Input: what can be uploaded and how it is read

| Upload | What happens |
| --- | --- |
| `.bench` (ISCAS) | Parsed directly (`pyatpg/core/verilog_parser.py`, bench path) |
| Gate-level Verilog (`.v`) | Parsed directly: primitives (`and`, `nand`, …), standard cells (SAED `NAND2X0`, `AO22X1`, `MUX21X1`, `SDFFX1`, GSCLib `AOI21X1`, …) lowered to AND/OR/NAND/NOR/XOR/NOT/BUF, hierarchy flattened with `__` as the separator |
| RTL Verilog/SystemVerilog, or a `.zip`/folder of it | Synthesized to gates with Yosys first (see [RTL designs](#rtl-designs-trust-hub-benchmarks)) |
| VHDL | Converted with GHDL (when installed), then Yosys |

While parsing (`verilog_parser.py`):

- **Constants are folded** through the logic. Outputs tied to a constant are dropped (nothing to test). A flip-flop input tied to a constant gets a named tie-off net (`__const0`/`__const1`), never the literal `1'b1`.
- **Registers clocked by logic are modelled as load-enabled registers.** A flip-flop on the clock input (directly or through clock buffers/inverters) is clocked every cycle. A flip-flop whose clock pin is driven by logic — how Trust-Hub sequential triggers are built (s15850-T100: `DFFNX2 Tg1_Trigger (.CLK(Tg1_Trigger1), .D(1'b1))`) — becomes `Q' = clk ? D : Q`. Without this the trigger logic drove nothing and vanished from the model, and in sequential mode the Trojan armed itself on the first clock. These registers are recorded in `circuit.logic_clocks` (Q → clock net).
- For synthesized RTL: undriven alias nets are removed and combinational loops are cut with pseudo flip-flops.

The upload is then routed:

```
                    parsed circuit
                          │
         ┌────────────────┼──────────────────────────┐
   no flip-flops   flip-flops + "Full scan" ON   flip-flops + "Full scan" OFF
         │                │                          │
         │       full-scan model (core/scan.py)      │
         │                │                          │
         └──── combinational path (Atalanta) ────┘   sequential path (pyatpg/seq)
```

### 2. Full-scan model (`pyatpg/core/scan.py`)

Under full scan every flip-flop is a scan cell: the tester loads any state, clocks once, and unloads. So each flip-flop output becomes a **pseudo-input** and each flip-flop input a **pseudo-output**, leaving a combinational circuit Atalanta can process. In addition:

- **Scan enable is held at 0** — its value in the capture cycle a pattern tests — and constant-folded through any logic that reads it. (T100 disables its payload when `test_se = 1`; ATPG must not use a value the tester never applies.)
- Flip-flops whose D input is constant get no pseudo-output (nothing to observe; it only added untestable tie-off faults).
- Logic that reaches no output or pseudo-output is pruned.

### 3. Profiling (`pyatpg/atpg/structural.py`, `pyatpg/core/graph_analysis.py`)

For every gate: type, fan-in/fan-out, logic level, **SCOAP** CC0/CC1/CO, **toggle rate** (bit-parallel random simulation, 3000 patterns by default, fixed seed 20250822 so rankings reproduce), and **reconvergence** (how many gates a stem's fanout branches rejoin at). These profiles feed both the detectors and the LLM.

### 4. Trojan detectors (deterministic, no LLM)

**Trigger/payload detector** (`pyatpg/atpg/trigger_detector.py`). Each finding is a piece of evidence with a strength; evidence from *independent mechanisms* adds up (+0.05 per extra family), correlated evidence does not. A gate is a **strong suspect at ≥ 0.7**.

| Evidence | Family | Strength | What it means |
| --- | --- | --- | --- |
| `logic_clock` | clock | 0.90 | Logic that drives a register's clock, and the never-toggling tree feeding it (the walk stops at host signals the trigger only taps) |
| `payload_splice` | payload | 0.85 | A gate in front of an output whose side input comes from a fanout-free tree of ≥ 6 gates that changes the output's probability ≥ 5× (s38584-T100: 0.4% → 10.8%), plus that tree |
| `payload_mux` | payload | 0.80 | A 2:1 multiplexer (as the parser builds it: NOT(s), AND(d0, ¬s), AND(d1, s), OR) whose select `s` is already a strong suspect — s38584-T200/T300's payload in front of output `g10122`. A register's own load mux (select = its clock) is excluded |
| `trojan_timer` | timer | 0.80 | An `autonomous_timer` (below) whose logic feeds gates already flagged by another mechanism (s38584-T300's 50-cycle window counter feeds its flagged trigger logic) |
| `logic_clock_payload` | clock | 0.70 | Logic within 3 gates downstream of a logic-clocked register |
| `trojan_region` | region | 0.70 | Completes a found Trojan: gates whose every consumer is flagged (2 levels back), and logic-clocked registers with their load logic. Never crosses into host nets (tapped signals, a payload's original signal, outputs) |
| `unactivated_cluster` | rarity | 0.65 (lead) | Connected rare nets that the complete (non-targeted) ATPG run never drove to their rare value |
| `duplicate_input` | hand edit | 0.65 (lead) | A gate reading the same net twice — synthesis never emits this (s38584-T100's `NOR(Tj_OUT1, Tj_OUT1, …)`) |
| `never_toggling_cluster` | rarity | 0.60 (lead) | ≥ 5 connected gates that never changed value in 16384 random patterns (also occurs in clean designs, e.g. a 36-gate group in ISCAS s38584) |
| `autonomous_timer` | timer | 0.60 (lead) | ≥ 4 registers whose next state reads nothing but the group, with the logic computing it: a free-running counter on the ordinary clock, which every other sign misses. Needs the full-scan model. None in the ~50 clean ISCAS'89 netlists in `benchmarks/`; benign designs can still have one, hence a lead |

**XOR-splice detector** (`pyatpg/atpg/suspects.py`). A rare conjunction feeding a 2-input XOR spliced into a path — the shape this tool's own Trojan generator inserts. It matches benign logic in real designs (the same 63 gates in clean and infected s15850), so on its own it is only a weak lead (0.35). Its matches are forced into the targeted fault list **only when the trigger detector found nothing strong**: on s38584-T200/T300 every Trojan gate it caught (19 of 134, 181 of 295) was also a trigger-detector finding, while on the generated c432–c2670 Trojans it is the detector that works (16 of 18 Trojan gates; the trigger detector finds 1).

Measured on the circuits used to calibrate it (without the LLM):

| Circuit | Verdict | Trojan gates flagged | Precision |
| --- | --- | --- | --- |
| s15850 clean | Inconclusive | — | — |
| s15850-T100 | Trojan suspected (0.97) | 36 / 37 | 0.84 |
| s38584 clean (ISCAS) | Inconclusive | — | — |
| s38584-T100 | Trojan suspected (0.93) | 15 / 15 | 1.00 |
| s38584-T300 (TjIn as shipped)\* | Trojan suspected | 1932 / 1937 | 0.97 |
| s38584-T300 (TjIn_fixed)\* | Trojan suspected | 2169 / 2170 | 0.97 |

\* Trigger detector alone on the full-scan model, before ATPG. Before the timer rule, a full T300 run missed 122 Trojan gates (recall 0.937), almost all of them the 32-bit window counter and its incrementer/compare logic. In the shipped TjIn the trigger output drives nothing (the payload reads an undriven `Trigger_out`), so the timer stays a lead there; in TjIn_fixed it feeds the trigger and is a `trojan_timer`.

### 5. LLM gate selection (targeted and compare modes; `pyatpg/atpg/targeting.py`, `llm_context.py`)

1. **Candidate pool.** The model cannot read 10,000 gates, so a pool (default 300, `LLM_CONTEXT_GATES`) is built from several views: the rarest-toggling (35%), hardest-to-observe (15%), most out of place for their neighbourhood (20%) and most reconvergent (30%) gates. Detector findings go in first, strongest first, but take at most 25% of the pool (at least 20 gates); the views skip flagged gates. Every flagged gate enters the fault list anyway, so the model's slots go to gates the detector missed. Previously the flagged gates could fill the whole pool: on s38584-T300 all 306 slots were flagged gates, and recall with and without the LLM was identical. To measure the LLM on its own, turn the live setting `detector_in_targeting` off (`PYATPG_DETECTOR_IN_TARGETING=0`): the fault list is then the model's picks alone, no marks are shown to it, the no-LLM baseline is the plain heuristic, and `llm_only_hits` is what the LLM found that the heuristic did not. The verdict still uses the detector.
2. **Anonymised gate table.** Gate names are replaced by `g0`, `g1`, … so the model reasons on structure, not names (Trojan gates are often literally named `Trojan_*`). Each gate is one CSV row: `id,type,role,fan_in,fan_out,level,cc0,cc1,co,toggle_rate,reconv,mark`. The `mark` column carries the detector's findings (`trig`, `clk`, `pay`, `dup`).
3. **Prompt.** Instructions to weigh testability and structural anomaly, four worked examples (a rare-AND trigger, an XOR payload, a benign deep comparator, and a sequential trigger tree driving a register clock), and an output format of one `gate,confidence` line per gate.
4. **Token budget and batching.** Groq's free tier refuses a request over ~8000 tokens, so each request is kept under `LLM_REQUEST_TOKEN_BUDGET` (default 7000) — about 100 gates. When the pool does not fit, the review is split into up to `llm_batches` requests (default 4): the first batch is the rarest gates, and each later batch carries the shortlist the model kept so far plus the next new candidates. The selection is the final shortlist, then earlier picks. On s38584 this reviews 306 of 11,088 gates in 4 requests.
5. **Answer.** Lines below confidence 0.3 are dropped, aliases are mapped back to real gate names, and unknown ids are discarded. **Every detector finding is then added to the list**, so the LLM can only add Trojan gates, never lose one.
6. **Cache.** The answer is cached per netlist + model + pool + prompt version (`outputs/comb_llm_cache.json`); an identical re-run costs no tokens. `LLM_RESULT_CACHE=0` disables it.

Without an LLM key the **structural heuristic** selects instead (rarely toggling, high fan-in, hard-to-observe gates), again plus every detector finding. On LLM runs this no-LLM selection is also computed, so each run reports recall **without the LLM vs with the LLM** and which Trojan gates only the LLM found.

### 6. How LLM calls run (`pyatpg/ranking/providers.py`)

- **Providers:** Groq (default, `openai/gpt-oss-120b`), Gemini, OpenAI, Anthropic, NVIDIA NIM, or any OpenAI-compatible endpoint. The provider is chosen per request (frontend Settings) or from the environment, and recognised from the key prefix (`gsk_` Groq, `AIza` Gemini, `sk-ant-` Anthropic, `nvapi-` NVIDIA, `sk-` OpenAI). A custom endpoint pointing at Groq is treated as Groq.
- **Streaming and live reasoning:** in a web request, OpenAI-compatible providers stream. Reasoning models (gpt-oss on Groq, DeepSeek-style servers) send their reasoning beside the answer; the ATPG page shows **one line of it while the model is generating**, and nothing once it finishes.
- **Limits and retries:** each request is bounded (`max_tokens` reserved per answer; `reasoning_effort=low` for gpt-oss so reasoning cannot eat the answer budget), times out after `llm_timeout_s` (90 s; 3× that in total while streaming), and is retried up to 3 times on transient errors. A rate-limit error that says how long to wait ("try again in 12.8s") is waited out (up to 65 s); account problems (bad key, no credit) fail at once.
- **Backup keys:** up to 5 keys; when one fails (rate limit, quota, rejected), the next takes over for the rest of the run.
- **Never silent:** with no key the heuristics run and the report says so; a key whose provider cannot be reached stops the run with `LLM could not be run: <reason>` instead of silently substituting the heuristic.
- **Accounting:** every call is recorded in `llm_usage` — calls, cache hits, tokens (the provider's own count when reported, `tokens_exact: true`), time, per stage.

### 7. ATPG (`pyatpg/atpg/runner.py`)

- **Non-targeted:** ATALANTA over the circuit's full collapsed fault list — the plain-ATPG baseline.
- **Targeted:** ATALANTA with `-f` over the selected gates' stuck-at-0/1 faults. Because that list holds the hardest faults, a run with aborted faults is repeated at `-b 1000 -B 1000`, then `-b 10000 -B 10000 -L`, keeping the attempt with the fewest aborted faults (never for redundant faults, which are proven untestable).
- **Fixed seed** (`-s`, setting `atalanta_seed`, default 1) so repeated runs give identical patterns; per-run timeout `atalanta_timeout_s` (600 s).
- Every ATPG figure (coverage, patterns, redundant/aborted faults, backtracks, CPU time) is parsed from ATALANTA's own output; a value it did not report shows as *not reported*, never 0. A targeted run's coverage is over its own short fault list, so the report also gives `targeted_circuit_wide_coverage`.

### 8. After the runs

- **Whole-circuit pattern coverage** (`pyatpg/atpg/faultsim.py`): each run's patterns are fault-simulated over every gate-output and input stuck-at fault, the same list for both runs, so targeted and non-targeted patterns compare like for like. The simulator cross-checks its fault-free responses against ATALANTA's and refuses to report a number on any disagreement.
- **Rare-net activation** (`pyatpg/atpg/trojan_metrics.py`): nets whose rarer value appears in < 5% of 4096 random patterns are the rare nets a trigger is built from. For each run: how many the patterns drive to their rare value at least once, and ≥ 5 times (N-detect), and the rarest ones never activated.
- **Never-activated evidence**: rare nets the complete run never activated are added to the detector's evidence (`unactivated_cluster`).
- **Verdict** (`pyatpg/atpg/verdict.py`): **Trojan suspected** when a gate reaches 0.7 (a logic-clocked register, a payload splice, or two independent weaker signs); **inconclusive** when only weak leads exist; **no Trojan indicated** when nothing was found.
- **Ground truth** (when the design's Trojan gates are recognisable by name — Trust-Hub `Tg*`/`Tj*`/`Trojan*`/`Trigger*`/`*Payload*`, generated `HT_*`): recall and precision of the verdict, the targeted list, the targeted list without the LLM, and the detectors; Trojan gates found only with the LLM; and Trojan faults ATALANTA proved untestable (a trigger stuck-at-0 among them means **the trigger can never fire**, as for s15850-T100).
- **Circuit analysis** (`pyatpg/atpg/circuit_analyst.py`): one more LLM call writes an architecture summary, observations and suspicious regions (JSON); without a key a structural summary is written instead. `correlation.py` reports overlap between those regions and the gates with undetected faults — co-occurrence only, never a measurement.

### 9. Sequential path (`pyatpg/seq`, "Full scan" off)

Circuits with flip-flops that are not run under full scan never reach ATALANTA. The native engine:

1. unrolls the circuit into time frames (`timeframe.py`) and uses 5-valued D-algebra (`dalgebra.py`);
2. runs phases within a time budget (`seq_time_budget_s`, 40 s): **random test sequences**, **PODEM across clock cycles** (up to 6 frames, 150 backtracks, incremental implication), then a **deep search** — LLM-guided when a key is configured, plain otherwise;
3. counts a fault as detected **only** when a reset-driven, multi-cycle deterministic simulation (`simulator.py`) confirms it. Neither PODEM nor the LLM can mark a fault detected.

With an LLM: **LLM-1** (`pyatpg/llm/circuit_context.py`) writes a short circuit summary from the structural statistics (never the raw netlist), cached per circuit; **LLM-2** (`vector_generator.py`) reorders the hardest remaining faults, seeing only up to 6 of them at a time with their local neighbourhood (`store.py`). In compare mode the deterministic run and the LLM-assisted run use the same seed (1234) and the same search effort, so any difference comes from the LLM's ordering; on a multi-core machine the two run in parallel processes. The same trigger detector runs on the circuit's full-scan view to give the sequential verdict.

Analysis modes (combinational/full scan):

| Mode | What runs |
| --- | --- |
| **LLM Heuristic (Targeted)** | Gate selection (LLM, or the structural heuristic without a key), then ATALANTA over those faults only |
| **ATPG Tool (Non-Targeted)** | ATALANTA over the full collapsed fault list |
| **Compare Both** | Both, plus a comparison |

---

## Features

| Layer | Capability |
| --- | --- |
| **Parsing** | `.bench` and structural Verilog (primitives, SAED/GSCLib cells, scan flip-flops, hierarchy), constant folding, logic-clocked registers |
| **RTL front end** | Verilog/SystemVerilog (file, `.zip` or folder) synthesized with Yosys; VHDL via GHDL (`pyatpg/core/rtl.py`) |
| **Full-scan model** | Pseudo-inputs/outputs, scan enable held at 0, constant flip-flop inputs dropped |
| **Profiling** | SCOAP CC0/CC1/CO, toggle rates, levels, reconvergence |
| **Trojan detectors** | Trigger/payload detector with evidence families and region completion; XOR-splice shape detector |
| **LLM gate selection** | Anonymised compact gate table, detector marks, batched review within a per-request token budget, cached answers, live reasoning line |
| **ATPG** | ATALANTA (combinational/full scan) with search-effort escalation and fixed seed; native sequential engine (time-frame expansion, PODEM, deterministic fault simulation) |
| **Trojan checks** | Whole-circuit pattern coverage, rare-net activation, verdict, ground-truth recall/precision with vs without the LLM, untriggerable-Trojan detection |
| **Run database** | Every run recorded in PostgreSQL (Neon) with classification, results and LLM usage (`docs/run-database.md`) |
| **Admin page** | Queue, cancel runs, live settings, maintenance mode, run history, storage, run database browser with CSV export |
| **Trojan dataset** | Automated Trojan insertion (`pyatpg/trojan`) with pluggable strategies |
| **Rankers** | Pluggable `GateRanker` interface (random, SCOAP, rare node, LLM) |
| **CLI** | `python -m pyatpg <netlist>` full-pipeline audit report |

---

## Project Structure

```
py-atpg-comb/
├── pyatpg/
│   ├── core/       # parser, Circuit, RTL front end (rtl.py), full scan (scan.py), graph analysis,
│   │               # progress tracker, live settings, run database (run_db.py)
│   ├── atpg/       # combinational/full-scan analysis: service, structural profiling, targeting + LLM
│   │               # context, Atalanta runner/parsing, fault sim, trigger detector, Trojan metrics, verdict
│   ├── seq/        # sequential ATPG engine: time frames, D-algebra, PODEM, simulator, engine, service
│   ├── llm/        # LLM-1 circuit summary / LLM-2 fault prioritization for the sequential engine
│   ├── ranking/    # GateRanker interface, rankers, LLM provider layer (providers.py)
│   ├── trojan/     # Trojan insertion and dataset generation
│   ├── analysis/   # FeatureExtractor and feature models
│   ├── evaluation/ # evaluator, benchmark runner, metrics
│   ├── api/        # FastAPI app, routes, services, concurrency (analysis slots, cancellation)
│   └── cli.py      # `python -m pyatpg`
├── frontend/       # React + Vite + TailwindCSS UI
├── db/schema.sql   # run database schema
├── docs/           # run database, automation, friend quick start
├── scripts/        # batch runners
├── tests/          # unit tests and fixtures
└── Atalanta/       # ATALANTA 2.0 source (compiled by build.sh)
```

---

## Quickstart

### Prerequisites

- Python 3.11+, Node.js 20+ (frontend), a compiled ATALANTA binary (`Atalanta/`; `build.sh` compiles it).
- An LLM key is optional — without one the structural heuristic and detectors do the selection. Set any of these in the environment or `.env`:
  - `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `NVIDIA_API_KEY` (vendor implied by the variable), or
  - `LLM_API_KEY` + `LLM_PROVIDER` (`groq` / `gemini` / `openai` / `anthropic` / `nvidia` / `openai_compatible`), optional `LLM_MODEL`, and `LLM_BASE_URL` for OpenAI-compatible endpoints (Ollama, OpenRouter, …).
  - Backup keys: the same variable with `_2` … `_5`.
  - Or paste a key in the frontend's **Settings**: Groq with `openai/gpt-oss-120b` is preselected; *Load from account* lists the models the key can use.

```bash
pip install -r requirements.txt
```

### Option A — Web platform

```bash
python -m uvicorn pyatpg.api.app:app --reload     # http://127.0.0.1:8000 (docs at /docs)
cd frontend && npm install && npm run dev         # http://localhost:5173
```

### Option B — CLI

```bash
python -m pyatpg benchmarks/c432.v     # combinational -> Atalanta
python -m pyatpg benchmarks/s27.bench  # has flip-flops -> pyatpg/seq
python -m pyatpg AES-T900.zip          # RTL archive -> Yosys -> gates
```

### RTL designs (Trust-Hub benchmarks)

Most Trust-Hub chip-level Trojan benchmarks (AES-T*, BasicRSA-T*, RS232-T*, PIC16F84-T*, wb_conmax-T*, …) ship as RTL. Upload the downloaded `.zip` as-is, or a single RTL `.v`/`.sv` file. PyATPG then:

1. unpacks the archive and drops testbenches (portless top modules);
2. when the archive holds both `TjFree` and `TjIn` copies, synthesizes the Trojan-inserted `TjIn` one (zip `TjFree` alone for the golden circuit);
3. synthesizes with Yosys (`yowasp-yosys` pip package, or a native `yosys`) onto simple cells the parser reads, from the auto-detected top module; files that are already gate-level netlists, and the cell library's own models, are not re-synthesized;
4. makes asynchronous set/reset synchronous and models latches as register + mux;
5. cuts combinational loops (e.g. AES-T900's `always @(rst, out) Counter <= Counter + 1`) with a pseudo flip-flop.

Every decision is listed in the run's notes; synthesis results are cached. VHDL needs GHDL on `PATH`. Layout-level and board-level benchmarks are not netlists and cannot be analysed.

Large designs are slow: AES-T900 synthesizes to ~190k gates and ~6.2k flip-flops; raise `PYATPG_ATALANTA_TIMEOUT` for it.

**Size guard on the hosted backend.** On Render, designs over 60,000 gates + flip-flops are rejected, and Yosys is stopped above 300 MB, so an upload cannot run the 512 MB instance out of memory. Change them live on the Admin page, or with `PYATPG_MAX_GATES` / `PYATPG_YOSYS_MAX_MB` (`0` = unlimited). Local runs are unlimited.

### Option C — Unattended batch runs

```bash
python scripts/run_via_api.py --circuits benchmarks/incoming --api-key YOUR_KEY   # drives the deployed API
python scripts/run_suite.py research --circuits benchmarks/incoming                 # fully local
```

See [docs/automation.md](docs/automation.md) and, for collaborators, [docs/friend-quickstart.md](docs/friend-quickstart.md).

What changed and why, with measured effects, is in the development log: [docs/changelog.md](docs/changelog.md) (LaTeX: `docs/changelog.tex`, regenerated with `python docs/changelog_to_tex.py`).

---

## Running it hosted

- **One analysis at a time.** Analyses run on worker threads behind a slot pool (`max_concurrent_runs`, default 1); extra requests wait and the page says what they are waiting for. A run whose browser disconnects is cancelled at its next checkpoint (including mid-LLM-request), so it frees the slot.
- **Progress.** The page polls `/api/atpg/progress/{id}` every 1.5 s for the current stage, its elapsed time and the LLM's current reasoning line.
- **Live settings** (Admin page, or environment): `maintenance`, `max_concurrent_runs`, `max_gates`, `yosys_max_mb`, `atalanta_timeout_s`, `atalanta_seed`, `llm_batches`, `detector_in_targeting`, `llm_timeout_s`, `seq_time_budget_s`, `yosys_timeout_s`. Admin changes last until the server restarts.
- **Admin page** (needs `PYATPG_ADMIN_TOKEN` on the server): server status, queue (cancel one or all runs), live settings, recent runs, storage cleanup, restart, and the run database.
- **Run database:** set `DATABASE_URL` (PostgreSQL, e.g. Neon) and every run is recorded — circuit classification, parameters, LLM, outcome, timings and per-engine results ([docs/run-database.md](docs/run-database.md)). `PYATPG_RECORD_RUNS=0` turns it off.

---

## API Endpoints

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `POST` | `/api/atpg/run` | Run an analysis (routes to Atalanta or the sequential engine) |
| `GET` | `/api/atpg/progress/{id}` | Live stage, timings and LLM reasoning line of a running analysis |
| `GET` | `/api/atpg/modes` | Analysis modes |
| `GET` | `/api/atpg/jobs/{id}/artifacts/{file}` | Download a raw artifact (bench, fault list, patterns, logs) |
| `POST` | `/api/circuit/parse` | Parse a netlist |
| `POST` | `/api/features/extract` | Extract the FeatureSet |
| `GET` / `POST` | `/api/ranking/rankers`, `/api/ranking/run` | List / run rankers |
| `POST` | `/api/trojan/generate` | Generate a Trojan dataset |
| `GET` / `POST` | `/api/llm/providers`, `/api/llm/models` | Providers, and the models a key can use |
| `POST` | `/api/benchmark/start` (+ status/result) | Batch ranker benchmark (API only; no page in the UI) |
| `*` | `/api/admin/*` | Admin: runs, cancel, status, settings, history, storage, restart, run database (`X-Admin-Token`) |

---

## Rankers, datasets and metrics

Rankers (`RandomRanker`, `ScoapRanker`, `RareNodeRanker`, `LLMRanker`) implement one `GateRanker` interface and are auto-discovered by the API. `pyatpg/trojan` inserts synthetic Trojans (`RandomStrategy`, `LowScoapStrategy`, `RareNodeStrategy`) and records the ground truth in `metadata.json`. Ranker metrics: Top-K accuracy, precision/recall at K, MRR, search-space reduction.

---

## Deployment

- **Backend — Render.** `bash build.sh` installs `requirements.txt`, precompiles Yosys (WebAssembly) and compiles ATALANTA against the build image; started with `uvicorn pyatpg.api.app:app --host 0.0.0.0 --port $PORT`. Environment: an LLM key (optional), `PYATPG_ADMIN_TOKEN`, `DATABASE_URL`.
- **Frontend — Vercel.** Root directory `frontend`, `npm run build`, with `VITE_API_URL` pointing at the backend.

See [project_architecture.md](project_architecture.md) for the request flow.

---

## Tech Stack

**Core:** Python · NetworkX · ATALANTA · Yosys (yowasp) · native sequential ATPG engine
**API:** FastAPI · Pydantic · Uvicorn · psycopg (PostgreSQL)
**Frontend:** React 19 · TypeScript · Vite · TailwindCSS · TanStack Query · Zustand · Recharts
**LLM providers:** Groq (default) · Google Gemini · OpenAI · Anthropic · NVIDIA NIM · any OpenAI-compatible endpoint

---

## License

Research use only. ATALANTA is subject to its own license terms.
