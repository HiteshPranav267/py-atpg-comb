# PyATPG — Hardware Trojan Localization Framework

> A modular Python framework for Hardware Trojan localization using ATPG testability metrics, structural circuit analysis, heuristic & LLM-based gate ranking — shipped as both a research benchmarking platform (FastAPI + React) and a standalone security-audit desktop app (Flask + pywebview). Combinational circuits run through the ATALANTA ATPG engine; circuits with flip-flops run through a native sequential ATPG engine (time-frame expansion + PODEM + deterministic multi-cycle fault simulation), both with optional context-reduced LLM assistance.

---

## Overview

PyATPG identifies Hardware Trojans inside gate-level Verilog/`.bench` netlists, **combinational or sequential**. It combines classic EDA testability analysis (SCOAP, toggle simulation) with a pluggable ranking engine (deterministic heuristics, statistical methods, LLM-based reasoning), and generates targeted test patterns for suspect gates — via ATALANTA (locally or on a remote lab server over SSH) for combinational circuits, and via a native sequential ATPG engine (`pyatpg/seq`) for circuits with flip-flops, since ATALANTA cannot levelize state-holding logic.

The repo currently contains **two applications** built on the same `pyatpg` core:

1. **Research/benchmarking platform** — a stateless FastAPI backend + React/Vite EDA-style frontend for running rankers across a dataset and comparing accuracy/MRR/search-space-reduction metrics. Deployed to Render (API) and Vercel (frontend).
2. **Security-audit desktop app** — a Flask + Socket.IO app (`app.py`) wrapped in a native `pywebview` window (`main.py`), running the full `Structural → AI → ATALANTA → Validation` pipeline against a single netlist and producing a pass/fail Trojan report. Packageable into a standalone executable via PyInstaller (`build_app.py`, `pyatpg.spec`).

---

## Architecture

### Core analysis pipeline (shared)

```
Verilog / .bench Netlist
      ↓
  VerilogParser  (pyatpg/core)
      ↓
    Circuit (NetworkX graph)
      ↓
  CircuitGraph  ←→  ATALANTA (ATPG)
  (SCOAP, toggle sim, centrality)
      ↓
  FeatureExtractor  (ATPGResult + GroundTruth)
      ↓
   FeatureSet
      ↓
  GateRanker  (RandomRanker | ScoapRanker | RareNodeRanker | LLMRanker)
      ↓
  RankingResult
```

### ATPG analysis (`pyatpg/atpg` + `pyatpg/seq`, used by the API & CLI)

`AtpgAnalysisService.run()` (`pyatpg/atpg/service.py`) is the single entry point for both
paths — it routes automatically based on whether the parsed circuit has flip-flops:

```
Combinational (no flip-flops)          Sequential (has flip-flops)
──────────────────────────────         ─────────────────────────────────
pyatpg/atpg/                           pyatpg/seq/
  structural.py   parse + SCOAP/toggle   timeframe.py   time-frame expansion (iterative array)
  targeting.py    LLM/heuristic gates    dalgebra.py    5-value (0,1,X,D,D') logic
  runner.py       run Atalanta           podem.py       PODEM search over the unrolled netlist
  parsing.py      read Atalanta output   simulator.py   deterministic multi-cycle fault sim (source of truth)
  circuit_analyst.py  LLM/structural     faultsim.py    checkpoint fault list + coverage calculation
  correlation.py  gates vs. faults       generator.py   random-fill / mutation candidates
                                         engine.py      orchestrates candidates -> minimal test-sequence set
                                         service.py     orchestration (mirrors atpg/service.py)
```

Combinational analysis modes:

| Mode | What runs |
| ---- | --------- |
| **LLM Heuristic (Targeted)** | Gates selected by the LLM (or the structural heuristic without an API key), then Atalanta over only those faults |
| **Atalanta (Non-Targeted)**  | Atalanta over the circuit's full collapsed fault list |
| **Compare Both**             | Both runs, plus a comparison |

Every ATPG metric shown — fault coverage, pattern counts, collapsed/redundant/aborted
faults, backtrackings, CPU time — is parsed from Atalanta's own output. A metric Atalanta
did not report is surfaced as *"Not reported by Atalanta"*, never as `0`. The LLM layer is
interpretive only and contributes no measurements.

**Sequential circuits** (any flip-flop, scan or not) never reach Atalanta — it is a purely
combinational engine and cannot levelize state-holding logic. They are routed to `pyatpg/seq`
instead, which unrolls the circuit into time frames, searches for input sequences that
activate and propagate each stuck-at fault (PODEM), and — critically — verifies every
candidate against a real, reset-driven, multi-cycle deterministic simulator before counting
it as detected. That simulator, never PODEM and never the LLM layer, is the sole source of
fault coverage. A circuit is only rejected if it has an asynchronous feedback loop not broken
by a clocked flip-flop (see `is_combinational_part_acyclic`), which neither engine can handle.
See [`pyatpg/llm/`](#llm-assisted-sequential-atpg) below for the optional LLM-assisted phase.

---

## Features

| Layer                       | Capability                                                                 |
| ---------------------------- | --------------------------------------------------------------------------- |
| **Parsing**             | Structural Verilog/`.bench` parser (gates, DFFs, scan chains)             |
| **Circuit Graph**       | NetworkX DAG with level, fanin/fanout, centrality                         |
| **SCOAP**               | Controllability (CC0/CC1) & Observability (CO) metrics                    |
| **Toggle Analysis**     | Simulated toggle rate per gate                                            |
| **ATPG Integration**    | ATALANTA wrapper — targeted vs. baseline fault coverage, redundancy info |
| **Sequential ATPG**     | Native engine (`pyatpg/seq`) for circuits with flip-flops — time-frame expansion, PODEM, deterministic multi-cycle fault simulation, minimal test-sequence generation |
| **Context-Reduced LLM** | Two-stage LLM-1 (cached circuit summary) / LLM-2 (compact per-call fault-prioritization context) layer for sequential ATPG, with token/call usage tracking (`pyatpg/llm`) |
| **Remote Execution**    | SSH job dispatch to a lab server (upload → run ATALANTA → pull results) |
| **Feature Extraction**  | Unified `FeatureSet` from structural + ATPG features                    |
| **Trojan Dataset**      | Automated Trojan insertion (`pyatpg/trojan`) with pluggable strategies  |
| **Ranking Engine**      | Pluggable `GateRanker` interface for benchmarking                       |
| **AI Prioritizer**      | LLM-based suspect-gate ranking for the audit pipeline (`pyatpg/agents`) |
| **LLM Ranker**          | Provider-agnostic (Gemini/OpenAI/Anthropic/NVIDIA/custom) ranker for benchmark evaluation |
| **Evaluation**          | Top-K Accuracy, MRR, Precision/Recall, Search Space Reduction             |
| **Benchmarking**        | Async batch benchmark runner over full dataset directories                |
| **REST API**            | FastAPI backend exposing all modules as stateless endpoints               |
| **Research Frontend**  | React + Vite desktop-style EDA application                                |
| **Desktop Audit App**  | Flask + Socket.IO + pywebview single-netlist Trojan audit tool            |
| **CLI**                 | `python -m pyatpg <netlist>` full-pipeline audit report in the terminal |
| **Standalone Build**    | PyInstaller packaging of the desktop app into a native executable         |

---

## Project Structure

```
py-atpg-comb/
├── pyatpg/
│   ├── core/               # Verilog/.bench parser, Circuit, graph analysis, toggle sim, memory
│   ├── analysis/           # FeatureExtractor, adapters, feature models
│   ├── trojan/             # Trojan insertion engine, triggers/payloads, dataset generator
│   ├── ranking/            # GateRanker interface, heuristic rankers, LLM provider abstraction
│   ├── agents/             # LLMAnalyzer (AI prioritizer) + RemoteRunner (SSH job dispatch)
│   ├── atpg/               # Atalanta integration (combinational): runner, parsers, targeting, analysis
│   ├── seq/                # Sequential ATPG engine: time-frame expansion, PODEM, deterministic fault sim
│   ├── llm/                # Context-reduced LLM-1/LLM-2 layer for sequential ATPG
│   ├── evaluation/         # Evaluator, BenchmarkRunner, metrics
│   ├── api/                # FastAPI app, routes, services, schemas (research platform)
│   └── cli.py               # `python -m pyatpg` terminal audit report
├── frontend/                # React + Vite + TailwindCSS EDA UI (research platform)
├── templates/, static/      # Flask templates/assets for the desktop audit app
├── app.py                   # Flask + Socket.IO server for the desktop audit app
├── main.py                  # pywebview wrapper that launches app.py as a native window
├── build_app.py, pyatpg.spec # PyInstaller packaging for the standalone desktop build
├── list_ssh.py, download_ssh.py # One-off scripts for pulling benchmark netlists from the lab server
├── generate_test.py, get_s35932.py, test_scan.py, test_s35932.py # Ad-hoc scan/trojan experiment scripts
├── tests/                   # Unit tests + real Atalanta output fixtures (tests/fixtures)
├── benchmarks/              # ISCAS85/89 benchmark circuits (.v / .bench)
├── Atalanta/                # ATALANTA 2.0 C++ source (compiled by build.sh at deploy time)
└── reference/               # Additional reference benchmark circuits
```

---

## Quickstart

### Prerequisites

- Python 3.11+
- Node.js 20+ (for the research frontend)
- ATALANTA compiled binary (inside `Atalanta/`)
- An LLM API key for LLM-based ranking/prioritization (optional; everything falls back to structural heuristics without one). Any of these work, in your environment or `.env`:
  - `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `NVIDIA_API_KEY` — the vendor is implied by the variable
  - or the generic `LLM_API_KEY` plus `LLM_PROVIDER` (`gemini` / `openai` / `anthropic` / `nvidia` / `openai_compatible`), with optional `LLM_MODEL` and, for OpenAI-compatible endpoints such as Ollama/Groq/OpenRouter, `LLM_BASE_URL`
  - or paste a key in the frontend's **Settings** dialog: pick a provider from the dropdown or leave it on *Auto* and the vendor is recognised from the key's prefix (`AIza` Gemini, `sk-ant-` Anthropic, `nvapi-` NVIDIA, `sk-` OpenAI). Per-request keys override the server's.

```bash
pip install -r requirements.txt
```

### Option A — Research/benchmarking platform (FastAPI + React)

```bash
# Backend
python -m uvicorn pyatpg.api.app:app --reload
# → http://127.0.0.1:8000  (docs at /docs)

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### Option B — Desktop security-audit app (Flask + pywebview)

```bash
python main.py       # native desktop window, served from a local Flask instance on :5005
# or
python app.py         # same app in your default browser
```

### Option C — CLI audit report

```bash
python -m pyatpg benchmarks/c432.v    # combinational -> Atalanta
python -m pyatpg benchmarks/s27.bench # sequential (has flip-flops) -> pyatpg/seq automatically
```

### Option D — Standalone executable

```bash
python build_app.py   # runs PyInstaller against pyatpg.spec → dist/pyatpg/
```

### Option E — Unattended batch runs (for collecting results across many circuits)

```bash
python scripts/run_suite.py atpg --circuits benchmarks/incoming --mode compare
```

Drop circuit files into `benchmarks/incoming/` (or point `--circuits` at any
folder/glob) and it works through all of them, retrying transient failures
(an LLM 503/429, a network blip) with backoff and skipping anything already
finished on a re-run — see [docs/automation.md](docs/automation.md) for
scheduling it unattended (Windows Task Scheduler / cron) and tuning retries.

---

## API Endpoints (FastAPI research platform)

| Method   | Route                          | Description                                     |
| -------- | ------------------------------- | ------------------------------------------------ |
| `GET`  | `/health`                      | Backend health check                              |
| `POST` | `/api/circuit/parse`           | Upload & parse a Verilog file                     |
| `POST` | `/api/features/extract`        | Extract full FeatureSet from Verilog              |
| `GET`  | `/api/ranking/rankers`         | List all available rankers dynamically            |
| `POST` | `/api/ranking/run`             | Run a ranker on a FeatureSet                      |
| `POST` | `/api/benchmark/start`         | Start an async benchmark job                      |
| `GET`  | `/api/benchmark/status/{id}`   | Poll benchmark job status                         |
| `GET`  | `/api/benchmark/result/{id}`   | Retrieve completed benchmark results              |
| `POST` | `/api/trojan/generate`         | Generate a Trojan dataset from a base circuit     |
| `GET`  | `/api/atpg/modes`              | List the selectable analysis modes (combinational)   |
| `POST` | `/api/atpg/run`                | Run an ATPG analysis on an uploaded circuit — routes to Atalanta or the sequential engine automatically based on whether it has flip-flops |
| `GET`  | `/api/atpg/jobs/{id}/artifacts/{file}` | Download a raw Atalanta artifact (BENCH, fault list, vectors, log) — combinational runs only |

Interactive docs available at `http://127.0.0.1:8000/docs`.

The desktop app (`app.py`) exposes its own parallel Flask routes (`/api/circuit/load`, `/api/circuit/analyze`, `/api/jobs/dispatch`, `/api/jobs/status`, `/api/circuit/graph`) plus Socket.IO events for live job progress, and can dispatch ATALANTA (combinational) or the sequential engine (circuits with flip-flops) either locally or to a remote lab server over SSH.

---

## LLM-Assisted Sequential ATPG

`pyatpg/seq` works standalone with no LLM at all (`use_llm=False`, or simply no API key configured — it degrades to deterministic random-fill + PODEM with no loss of correctness, just less help on the hardest faults). When an API key is available, `pyatpg/llm` adds two LLM stages on top of it, both deliberately built around **context reduction** so a run doesn't cost more tokens than it has to:

- **LLM-1** (`circuit_context.py`) runs once per circuit, reads only the structural stats already computed elsewhere (SCOAP, fan-in/out, gate mix, depth — never the raw Verilog), and produces a short summary that's cached to disk keyed by the circuit's content hash. A repeat run against the same netlist costs zero LLM calls.
- **LLM-2** (`vector_generator.py`) is consulted only when the deterministic phases (random-fill, then PODEM) still leave faults undetected. It never receives the full fault list, the full circuit, or the full simulation history — `store.py` retrieves only the current top-k hardest-to-detect faults, their immediate graph neighborhood, and history entries that touched those same nodes (`LocalContextStore.compact_fault_context`). The model's response is validated against the batch it was given before being trusted, and its output only reorders which fault to attempt next — the deterministic simulator in `pyatpg/seq/simulator.py` is the only thing that can ever mark a fault detected or report coverage.
- **Token/call accounting** (`token_tracker.py`) records every prompt/response size and cache hit, surfaced in the API response's `sequential.llm_usage` field, so the context-reduction strategy's savings are measurable rather than assumed.
- The `SequentialAtpgEngine` never imports `pyatpg/llm` — an LLM-2 ranker is injected as a plain callable (`candidate_ranker`), so prompts stay independent of the ATPG engine and the engine's own test suite (`tests/test_sequential_atpg.py`) needs no API key.

---

## Rankers

| Ranker             | Strategy                                           |
| ------------------- | ---------------------------------------------------- |
| `RandomRanker`   | Uniform random baseline                            |
| `ScoapRanker`    | Ranks by low controllability × high observability |
| `RareNodeRanker` | Ranks by lowest toggle rate (rare activation)      |
| `LLMRanker`      | Chain-of-thought reasoning via any configured LLM provider |

All rankers implement the same `GateRanker` interface and are auto-discovered by the API — no hardcoding required on the frontend.

---

## Trojan Dataset Generation

`pyatpg/trojan` inserts synthetic Hardware Trojans into clean benchmark netlists for evaluation:

```python
from pyatpg.trojan import TrojanDatasetGenerator, TrojanInserter, RandomStrategy

inserter = TrojanInserter(trigger="AND", payload="XOR", strategy=RandomStrategy())
generator = TrojanDatasetGenerator(inserter=inserter, output_dir="datasets")
generator.generate("benchmarks/c17.v", variants=3)
```

Insertion strategies: `RandomStrategy`, `LowScoapStrategy`, `RareNodeStrategy`.

---

## Evaluation Metrics

- **Top-1 / Top-5 / Top-10 Accuracy** — Is the true Trojan gate in the top-K suspects?
- **Recall@K / Precision@K** — Coverage of Trojan gates in top-K list
- **Mean Reciprocal Rank (MRR)** — Rank quality across the dataset
- **Search Space Reduction (SSR)** — % of gates eliminated before finding the Trojan

---

## Deployment (research platform)

- **Backend** — Render.com, built via `bash build.sh` (installs `requirements.txt` and compiles `Atalanta/atalanta` from source against Render's own build image — a prebuilt binary drifts out of sync with the runtime image's glibc), started via `uvicorn pyatpg.api.app:app --host 0.0.0.0 --port $PORT`. Reads `LLM_PROVIDER` / `LLM_API_KEY` (or a vendor-specific `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `NVIDIA_API_KEY`) from Render environment variables; users can also supply their own key and provider from the frontend's Settings dialog.
- **Frontend** — Vercel.com, root directory `frontend`, built via `npm run build`. Bakes `VITE_API_URL` (the Render backend URL) into the static build.

See [project_architecture.md](project_architecture.md) for the full request-flow writeup.

---

## Tech Stack

**Core:** Python · NetworkX · ATALANTA (combinational ATPG) · native sequential ATPG engine (`pyatpg/seq`)
**Research API:** FastAPI · Pydantic · Uvicorn
**Research Frontend:** React 19 · TypeScript · Vite · TailwindCSS · TanStack Query/Table · Zustand · Recharts
**Desktop Audit App:** Flask · Flask-SocketIO · pywebview · PyInstaller
**Remote Execution:** Paramiko (SSH job dispatch to a lab server)
**LLM Providers:** Google Gemini (`google-genai`) · OpenAI · Anthropic Claude · NVIDIA NIM · any OpenAI-compatible endpoint (Ollama, Groq, OpenRouter, vLLM...) — selectable per request

---

## License

Research use only. ATALANTA is subject to its own license terms.
