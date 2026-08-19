# PyATPG System Architecture & Context

This document serves as the master blueprint for the PyATPG framework. It outlines the technology stack, the role of each directory/file, and how the frontend and backend interact in production.

---

## 1. High-Level Overview

PyATPG is a structural feature-driven framework for pre-silicon Hardware Trojan detection in both combinational and sequential circuits. It extracts gate-level features (SCOAP testability, toggle rates, topological centrality) and utilizes heuristics and Large Language Models (LLMs) to prioritize suspicious gates. It then generates test patterns targeting those specific high-risk gates: Atalanta ATPG for combinational circuits, and a native sequential engine (`pyatpg/seq`, time-frame expansion + PODEM + a deterministic multi-cycle fault simulator) for circuits with flip-flops.

### Tech Stack
- **Frontend:** React, TypeScript, Vite, TailwindCSS (Hosted on Vercel)
- **Backend:** Python 3, FastAPI, NetworkX, Uvicorn (Hosted on Render)
- **Core CLI/EDA Engine:** ATALANTA (C-based binary for test pattern generation)

---

## 2. Directory & File Structure

The project is split into the frontend client and the Python backend engine. 

### Backend (`/py-atpg/pyatpg/`)
The backend is completely stateless and designed as a REST API using FastAPI.

* **`api/` (FastAPI Web Layer)**
  * `app.py`: The main entry point for the backend. Configures CORS, mounts routers, and defines the `/health` check.
  * `routes/`: Contains FastAPI route definitions (e.g., `circuit.py`, `feature.py`, `ranking.py`). These files define the HTTP methods (GET/POST) and handle Pydantic schema validation.
  * `services/`: Contains the business logic layer (e.g., `circuit_service.py`). Routes pass validated data here, and services orchestrate the core python classes to get results.

* **`core/` (Hardware/Graph Logic)**
  * `verilog_parser.py`: Custom parser that reads raw `.v` netlists and identifies inputs, outputs, logic gates, and flip-flops.
  * `netlist.py`: Data classes representing the raw circuit elements.
  * `graph_analysis.py`: Converts the parsed netlist into a `NetworkX` Directed Acyclic Graph (DAG). It runs Monte Carlo toggle simulations, calculates structural centrality, and computes SCOAP (Controllability/Observability) metrics.

* **`agents/` & `ranking/` (AI & Heuristics)**
  * Contains the logic for the `GateRanker` interface. This includes baseline heuristic rankers (ScoapRanker, RareNodeRanker) as well as the `LLMAnalyzer`/`LLMRanker` which connects to OpenAI/Anthropic APIs via the keys in your `.env` file to semantically rank hardware Trojans.

* **`seq/` (Sequential ATPG Engine)**
  * Native Python engine for circuits with flip-flops, since Atalanta is combinational-only. `timeframe.py` unrolls the circuit into an iterative array (time-frame expansion); `dalgebra.py` supplies 5-value (0/1/X/D/D') logic; `podem.py` is a PODEM search over the unrolled netlist; `simulator.py` is a deterministic, reset-driven, multi-cycle functional/fault simulator — the sole source of truth for detection and coverage; `engine.py` orchestrates candidate generation (PODEM + random-fill + mutation, optionally LLM-prioritized) into a minimal test-sequence set; `service.py` mirrors `atpg/service.py`'s orchestration shape. `atpg/service.py` routes any circuit with flip-flops here automatically instead of rejecting it.

* **`llm/` (Context-Reduced LLM Layer for Sequential ATPG)**
  * Two-stage, provider-agnostic LLM support built around context reduction: `circuit_context.py` (LLM-1) produces a short, cached, once-per-circuit summary; `vector_generator.py` (LLM-2) prioritizes which hard-to-detect faults to attempt next, but only ever receives a compact retrieval-built context (top-k difficult faults, their local graph neighborhood, relevant history) via `store.py` — never the full circuit, fault list, or vector history. `token_tracker.py` records API calls and estimated token usage. The `seq` engine never imports this package directly; an LLM-2 ranker is injected as a plain callable, keeping prompts independent of the ATPG engine.

* **Root Level Files**
  * `Atalanta/`: Directory containing the compiled C binary for Atalanta ATPG.
  * `requirements.txt`: Defines the Python dependencies (FastAPI, NetworkX, python-multipart, etc.) required for Render to build the environment. (Note: Desktop GUI libraries like PyQt6 were removed to ensure headless server compatibility).

### Frontend (`/py-atpg/frontend/`)
The frontend is a standard Vite-powered React application.

* **`src/services/api.ts`**: The core Axios client. It dynamically points to `import.meta.env.VITE_API_URL` so that in production (Vercel), it routes requests to your Render backend URL, and locally it can fall back to `/api` (which Vite proxies to `localhost:8000`).
* **`vite.config.ts`**: The local development configuration. It contains a proxy rule so that local React components can make requests to `/api/circuit/parse` and Vite will automatically forward them to your local `uvicorn` instance.

---

## 3. Production Deployment Workflow

Because PyATPG relies on both a modern web interface and heavy Python/C backend processing, it is deployed across two separate cloud providers.

### The Backend (Render.com)
Render acts as a traditional Linux server.
1. When you push to GitHub, Render detects the push and runs the Build Command: `bash build.sh`.
2. `build.sh` installs `requirements.txt` and compiles `Atalanta/atalanta` from source in Render's own build container, so the binary's glibc requirement always matches whatever image actually runs it.
3. It executes the Start Command: `python -m uvicorn pyatpg.api.app:app --host 0.0.0.0 --port $PORT`.
4. It reads the `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from the Render environment variables dashboard (taking the place of your local `.env` file).
5. The API is now live at `https://py-atpg-backend.onrender.com`.

### The Frontend (Vercel.com)
Vercel is optimized for static sites and serverless edge delivery.
1. When you push to GitHub, Vercel detects the push and looks at the **Root Directory** setting (`frontend`).
2. It runs `npm install` and `npm run build` using the Vite framework preset.
3. During the build, it reads `VITE_API_URL` from the Vercel environment variables dashboard and bakes your Render backend URL into the optimized static HTML/JS files.
4. The React app is now live at `https://py-atpg-comb.vercel.app`.

### How They Talk
When a user visits your Vercel link and uploads a Verilog file:
1. React Query intercepts the action and calls `api.uploadCircuit()` in `api.ts`.
2. The file is packaged as `FormData` (multipart/form-data) and an HTTP POST request is fired across the internet to `https://py-atpg-backend.onrender.com/api/circuit/parse`.
3. Render's FastAPI server receives the file, hands it to `circuit_service.py`, which invokes `verilog_parser.py`.
4. The structural statistics are computed via `graph_analysis.py`, packed into a JSON dictionary, and returned as an HTTP 200 response to the user's browser.
