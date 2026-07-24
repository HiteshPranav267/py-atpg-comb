# PyATPG — Hardware Trojan Localization Framework

> A modular Python framework for Hardware Trojan localization using ATPG testability metrics, structural circuit analysis, heuristic & LLM-based gate ranking, and a React/FastAPI desktop-style EDA frontend.

---

## Overview

PyATPG is a research platform for identifying Hardware Trojans inside gate-level Verilog netlists. It combines classic EDA testability analysis (SCOAP, toggle simulation) with a pluggable ranking engine that supports deterministic heuristics, statistical methods, and LLM-based reasoning — all evaluated through a unified benchmark pipeline.

---

## Architecture

```
Verilog Netlist
      ↓
  VerilogParser
      ↓
    Circuit (NetworkX graph)
      ↓
  StructuralAnalysis  ←→  ATALANTA (ATPG)
      ↓
  FeatureExtractor  (ATPGResult + GroundTruth)
      ↓
   FeatureSet
      ↓
  GateRanker  (RandomRanker | ScoapRanker | RareNodeRanker | LLMRanker)
      ↓
  RankingResult
      ↓
  Evaluator  →  EvaluationResult
      ↓
  BenchmarkRunner  →  Report
```

---

## Features

| Layer | Capability |
|---|---|
| **Parsing** | Structural Verilog parser (gates, DFFs, scan chains) |
| **Circuit Graph** | NetworkX-based DAG with level, fanin/fanout, centrality |
| **SCOAP** | Controllability (CC0/CC1) & Observability (CO) metrics |
| **Toggle Analysis** | Simulated toggle rate per gate |
| **ATPG Integration** | ATALANTA wrapper — fault coverage & redundancy info |
| **Feature Extraction** | Unified `FeatureSet` from structural + ATPG features |
| **Trojan Dataset** | Automated Trojan insertion with configurable strategies |
| **Ranking Engine** | Pluggable `GateRanker` interface |
| **LLM Ranker** | Gemini/OpenAI provider abstraction via `LLMProvider` |
| **Evaluation** | Top-K Accuracy, MRR, Precision/Recall, Search Space Reduction |
| **Benchmarking** | Async batch benchmark runner over full dataset directories |
| **REST API** | FastAPI backend exposing all modules as stateless endpoints |
| **EDA Frontend** | React + Vite desktop-style application |

---

## Project Structure

```
py-atpg/
├── pyatpg/
│   ├── core/               # Verilog parser, Circuit, graph analysis
│   ├── analysis/           # FeatureExtractor, adapters, models
│   ├── trojan/             # Trojan insertion engine & dataset generator
│   ├── ranking/            # GateRanker interface + heuristic rankers
│   ├── llm/                # LLMProvider abstraction + GeminiProvider
│   ├── evaluation/         # Evaluator, BenchmarkRunner
│   └── api/                # FastAPI app, routes, services, schemas
├── frontend/               # React + Vite + TailwindCSS EDA UI
├── tests/                  # Unit & integration tests
├── Atalanta/               # ATALANTA ATPG tool (submodule)
└── reference/              # Benchmark circuits (ISCAS85 etc.)
```

---

## Quickstart

### Prerequisites

- Python 3.11+
- Node.js 20+
- ATALANTA compiled binary (inside `Atalanta/`)

### Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Start the API server
uvicorn pyatpg.api.app:app --reload
# → http://127.0.0.1:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

---

## API Endpoints

| Method | Route | Description |
|---|---|---|
| `GET` | `/health` | Backend health check |
| `POST` | `/api/circuit/parse` | Upload & parse a Verilog file |
| `POST` | `/api/features/extract` | Extract full FeatureSet from Verilog |
| `GET` | `/api/ranking/rankers` | List all available rankers dynamically |
| `POST` | `/api/ranking/run` | Run a ranker on a FeatureSet |
| `POST` | `/api/benchmark/start` | Start an async benchmark job |
| `GET` | `/api/benchmark/status/{id}` | Poll benchmark job status |
| `GET` | `/api/benchmark/result/{id}` | Retrieve completed benchmark results |
| `POST` | `/api/trojan/generate` | Generate a Trojan dataset from a base circuit |

Interactive docs available at `http://127.0.0.1:8000/docs`.

---

## Rankers

| Ranker | Strategy |
|---|---|
| `RandomRanker` | Uniform random baseline |
| `ScoapRanker` | Ranks by low controllability × high observability |
| `RareNodeRanker` | Ranks by lowest toggle rate (rare activation) |
| `LLMRanker` | Chain-of-thought reasoning via Gemini / OpenAI |

All rankers implement the same `GateRanker` interface and are auto-discovered by the API — no hardcoding required on the frontend.

---

## Evaluation Metrics

- **Top-1 / Top-5 / Top-10 Accuracy** — Is the true Trojan gate in the top-K suspects?
- **Recall@K / Precision@K** — Coverage of Trojan gates in top-K list
- **Mean Reciprocal Rank (MRR)** — Rank quality across the dataset
- **Search Space Reduction (SSR)** — % of gates eliminated before finding the Trojan

---

## Tech Stack

**Backend:** Python · FastAPI · Pydantic · NetworkX · ATALANTA  
**Frontend:** React · TypeScript · Vite · TailwindCSS · shadcn/ui · TanStack Table · Zustand · React Query

---

## License

Research use only. ATALANTA is subject to its own license terms.
