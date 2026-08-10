# PyATPG — Hardware Trojan Localization Framework

> A modular Python framework for Hardware Trojan localization using ATPG testability metrics, structural circuit analysis, heuristic & LLM-based gate ranking — shipped as both a research benchmarking platform (FastAPI + React) and a standalone security-audit desktop app (Flask + pywebview) built on the ATALANTA ATPG engine.

---

## Overview

PyATPG identifies Hardware Trojans inside gate-level Verilog/`.bench` netlists. It combines classic EDA testability analysis (SCOAP, toggle simulation) with a pluggable ranking engine (deterministic heuristics, statistical methods, LLM-based reasoning), and can drive ATALANTA — either locally or on a remote lab server over SSH — to generate targeted test patterns for suspect gates.

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

### ATPG analysis (`pyatpg/atpg`, used by the API & CLI)

```
structural.py       parse Verilog/BENCH + SCOAP/toggle profiling
targeting.py        choose gates for the targeted fault list (LLM or structural heuristic)
runner.py           execute Atalanta in an isolated job workspace
parsing.py          read the summary, test vectors and undetected-fault report
circuit_analyst.py  interpretive circuit analysis (LLM or computed structural)
correlation.py      overlap between analysis gates and undetected faults
service.py          orchestration
```

Three analysis modes:

| Mode | What runs |
| ---- | --------- |
| **LLM Heuristic (Targeted)** | Gates selected by the LLM (or the structural heuristic without an API key), then Atalanta over only those faults |
| **Atalanta (Non-Targeted)**  | Atalanta over the circuit's full collapsed fault list |
| **Compare Both**             | Both runs, plus a comparison |

Every ATPG metric shown — fault coverage, pattern counts, collapsed/redundant/aborted
faults, backtrackings, CPU time — is parsed from Atalanta's own output. A metric Atalanta
did not report is surfaced as *"Not reported by Atalanta"*, never as `0`. The LLM layer is
interpretive only and contributes no measurements.

Combinational circuits (no flip-flops) run through ATALANTA directly on `.bench` netlists. Circuits with scan flip-flops are still supported end-to-end by the parser/graph layer (see `benchmarks/*_scan.v`, `test_scan.py`).

---

## Features

| Layer                       | Capability                                                                 |
| ---------------------------- | --------------------------------------------------------------------------- |
| **Parsing**             | Structural Verilog/`.bench` parser (gates, DFFs, scan chains)             |
| **Circuit Graph**       | NetworkX DAG with level, fanin/fanout, centrality                         |
| **SCOAP**               | Controllability (CC0/CC1) & Observability (CO) metrics                    |
| **Toggle Analysis**     | Simulated toggle rate per gate                                            |
| **ATPG Integration**    | ATALANTA wrapper — targeted vs. baseline fault coverage, redundancy info |
| **Remote Execution**    | SSH job dispatch to a lab server (upload → run ATALANTA → pull results) |
| **Feature Extraction**  | Unified `FeatureSet` from structural + ATPG features                    |
| **Trojan Dataset**      | Automated Trojan insertion (`pyatpg/trojan`) with pluggable strategies  |
| **Ranking Engine**      | Pluggable `GateRanker` interface for benchmarking                       |
| **AI Prioritizer**      | LLM-based suspect-gate ranking for the audit pipeline (`pyatpg/agents`) |
| **LLM Ranker**          | Gemini/OpenAI/Anthropic provider abstraction for benchmark evaluation     |
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
│   ├── atpg/               # Atalanta integration: runner, parsers, targeting, analysis
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
- `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in your environment or `.env` for LLM-based ranking/prioritization

```bash
pip install -r requirements.txt
```

### Option A — Research/benchmarking platform (FastAPI + React)

```bash
# Backend
uvicorn pyatpg.api.app:app --reload
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
python -m pyatpg benchmarks/c432.v
```

### Option D — Standalone executable

```bash
python build_app.py   # runs PyInstaller against pyatpg.spec → dist/pyatpg/
```

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
| `GET`  | `/api/atpg/modes`              | List the selectable analysis modes                |
| `POST` | `/api/atpg/run`                | Run an Atalanta ATPG analysis on an uploaded circuit |
| `GET`  | `/api/atpg/jobs/{id}/artifacts/{file}` | Download a raw Atalanta artifact (BENCH, fault list, vectors, log) |

Interactive docs available at `http://127.0.0.1:8000/docs`.

The desktop app (`app.py`) exposes its own parallel Flask routes (`/api/circuit/load`, `/api/circuit/analyze`, `/api/jobs/dispatch`, `/api/jobs/status`, `/api/circuit/graph`) plus Socket.IO events for live job progress, and can dispatch ATALANTA runs either locally or to a remote lab server over SSH.

---

## Rankers

| Ranker             | Strategy                                           |
| ------------------- | ---------------------------------------------------- |
| `RandomRanker`   | Uniform random baseline                            |
| `ScoapRanker`    | Ranks by low controllability × high observability |
| `RareNodeRanker` | Ranks by lowest toggle rate (rare activation)      |
| `LLMRanker`      | Chain-of-thought reasoning via Gemini / OpenAI / Anthropic |

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

- **Backend** — Render.com, built via `bash build.sh` (installs `requirements.txt` and compiles `Atalanta/atalanta` from source against Render's own build image — a prebuilt binary drifts out of sync with the runtime image's glibc), started via `uvicorn pyatpg.api.app:app --host 0.0.0.0 --port $PORT`. Reads `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` from Render environment variables.
- **Frontend** — Vercel.com, root directory `frontend`, built via `npm run build`. Bakes `VITE_API_URL` (the Render backend URL) into the static build.

See [project_architecture.md](project_architecture.md) for the full request-flow writeup.

---

## Tech Stack

**Core:** Python · NetworkX · ATALANTA
**Research API:** FastAPI · Pydantic · Uvicorn
**Research Frontend:** React 19 · TypeScript · Vite · TailwindCSS · TanStack Query/Table · Zustand · Recharts
**Desktop Audit App:** Flask · Flask-SocketIO · pywebview · PyInstaller
**Remote Execution:** Paramiko (SSH job dispatch to a lab server)
**LLM Providers:** Google Gemini (`google-genai`) · OpenAI · Anthropic

---

## License

Research use only. ATALANTA is subject to its own license terms.
