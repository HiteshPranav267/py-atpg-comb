# PyATPG System Architecture & Context

The blueprint of the PyATPG framework: what each part of the code does, how an analysis flows through it, and how the frontend and backend run in production. The analysis pipeline itself (input handling, full scan, detectors, LLM, ATPG, verdict) is described step by step in [README.md → How an analysis runs](README.md#how-an-analysis-runs).

---

## 1. High-Level Overview

PyATPG finds hardware Trojans in gate-level and RTL designs. It parses (or synthesizes) a netlist, profiles every gate (SCOAP testability, toggle rates, structure), runs a deterministic trigger/payload detector, lets an LLM review the most suspicious gates and choose which faults to target, generates test patterns with ATALANTA (combinational logic and full-scan designs) or a native sequential engine (designs run without scan), and checks how the patterns exercise the circuit's rare nets and the suspected Trojan. Every run reports recall of the Trojan's gates with and without the LLM when the ground truth is known.

### Tech Stack
- **Frontend:** React 19, TypeScript, Vite, TailwindCSS, TanStack Query, Zustand (Vercel)
- **Backend:** Python 3, FastAPI, NetworkX, Uvicorn, psycopg (Render)
- **Engines:** ATALANTA 2.0 (C++, compiled at deploy), Yosys via `yowasp-yosys` (RTL synthesis), the native sequential engine in `pyatpg/seq`
- **Data:** PostgreSQL run database (Neon), optional

---

## 2. Directory & File Structure

### Backend (`pyatpg/`)

* **`api/` — web layer**
  * `app.py`: FastAPI app, CORS, routers, `/health`.
  * `routes/`: `atpg.py` (run, progress, artifacts), `circuit.py`, `feature.py`, `ranking.py`, `trojan.py`, `llm.py` (providers, model listing), `benchmark.py` (API only), `admin.py` (queue, settings, status, history, storage, restart, run database), `analytics.py`.
  * `concurrency.py`: runs each analysis on a worker thread behind a slot pool (`max_concurrent_runs`), reports queueing, cancels a run whose browser disconnected, and records every run in the run database.
  * `services/`: thin orchestration for the non-ATPG routes.

* **`core/` — circuits and infrastructure**
  * `verilog_parser.py`: `.bench` and structural Verilog (primitives and standard cells lowered to basic gates, hierarchy flattened, constants folded, logic-clocked registers modelled as load-enabled registers, recorded in `circuit.logic_clocks`).
  * `rtl.py`: RTL front end — upload decoding (zip/folder bundles), TjIn/TjFree selection, Yosys synthesis with a memory guard and cache, GHDL for VHDL, dead-logic removal, combinational-loop cutting, design size guard.
  * `scan.py`: full-scan model — pseudo-inputs/outputs, scan enable held at 0 with constant folding, constant flip-flop inputs dropped.
  * `netlist.py`: `Circuit`, gates, flip-flops.
  * `graph_analysis.py`: NetworkX graph, SCOAP, bit-parallel toggle simulation, reconvergence.
  * `progress.py`: per-request progress tracker (stage, timings, the LLM's current reasoning line) and cancellation.
  * `settings.py`: live settings (admin overrides over environment variables).
  * `run_db.py`: records every run in PostgreSQL on a background thread (`db/schema.sql`).

* **`atpg/` — combinational and full-scan analysis**
  * `service.py`: the single entry point (`AtpgAnalysisService.run`): parse → full scan or sequential routing → profile → detectors → gate selection → ATALANTA runs → pattern simulation → rare-net activation → verdict → ground truth → circuit analysis.
  * `structural.py`: parsing wrapper and gate profiles.
  * `trigger_detector.py`: trigger/payload evidence (logic clocks, payload splices, duplicate inputs, never-toggling groups, never-activated rare nets), evidence families, region completion.
  * `suspects.py`: XOR-splice shape detector (weak lead).
  * `targeting.py` + `llm_context.py`: candidate pool, anonymised compact gate table with detector marks, prompt, token budget, batched review carrying a shortlist, answer parsing, answer cache.
  * `runner.py` / `parsing.py`: ATALANTA execution (fixed seed, timeout, cancellable) and output parsing.
  * `faultsim.py`: whole-circuit fault simulation of a run's patterns.
  * `trojan_metrics.py`: rare-net activation, ground-truth gate recognition, recall/precision.
  * `verdict.py`: the Trojan verdict from the evidence.
  * `circuit_analyst.py` / `correlation.py`: LLM (or structural) circuit analysis and its overlap with undetected faults.

* **`seq/` — sequential ATPG engine** (designs run without full scan)
  * `timeframe.py` (time-frame expansion), `dalgebra.py` (5-valued logic), `podem.py` (PODEM with incremental implication), `simulator.py` (reset-driven multi-cycle simulation — the only source of detection), `generator.py` (random/mutation candidates), `engine.py` (phases within a time budget), `service.py` (orchestration, deterministic vs LLM-assisted comparison).

* **`llm/` — LLM layer for the sequential engine**
  * `circuit_context.py` (LLM-1, cached circuit summary), `vector_generator.py` (LLM-2, reorders the hardest faults, at most 6 per call), `store.py` (compact local context retrieval), `token_tracker.py` (calls and tokens per stage).

* **`ranking/`**: `GateRanker` rankers and `providers.py`, the provider layer every LLM call goes through (Groq default; Gemini, OpenAI, Anthropic, NVIDIA, OpenAI-compatible; key-prefix detection; backup keys; timeouts; retries that honour the provider's wait time; streaming of reasoning).

* **`trojan/`, `analysis/`, `evaluation/`**: Trojan dataset generation, feature extraction, ranker evaluation.

### Frontend (`frontend/src/`)

* `services/api.ts`: Axios client pointed at `VITE_API_URL` (or the backend URL saved in Settings).
* `pages/ATPG.tsx`: upload, mode, full-scan and LLM options, live progress with the LLM's reasoning line, results.
* `components/atpg/`: run reports, comparison, verdict, Trojan check panel (rare nets, ground truth, recall with vs without the LLM), LLM usage.
* `pages/Admin.tsx`: queue, settings, history, storage, run database browser.
* `components/layout/LlmKeyEditor.tsx`: provider, model (*Load from account*), base URL and key, with backup keys.

---

## 3. Production Deployment Workflow

### The Backend (Render.com)
1. On every push to `main`, Render runs `bash build.sh`: installs `requirements.txt`, precompiles Yosys (WebAssembly) into `.yowasp-cache`, compiles `Atalanta/atalanta` in Render's own build image, and smoke-tests it on c17.
2. It starts `python -m uvicorn pyatpg.api.app:app --host 0.0.0.0 --port $PORT`.
3. Environment variables: an LLM key (optional — users can paste their own), `PYATPG_ADMIN_TOKEN` (enables the Admin page), `DATABASE_URL` (run database). Render sets `RENDER`, which enables the size guard (60,000 gates + flip-flops, Yosys 300 MB) for the 512 MB instance.
4. The API is live at `https://py-atpg-backend.onrender.com`. The free plan sleeps after 15 minutes idle; the first request then takes about a minute.

### The Frontend (Vercel.com)
1. On every push, Vercel builds the `frontend` directory with `npm run build`, baking in `VITE_API_URL`.
2. The app is live at `https://py-atpg-comb.vercel.app`.

### How They Talk
When a user runs an analysis:
1. The ATPG page sends the file, mode, options, LLM settings and a `progress_id` as `multipart/form-data` to `POST /api/atpg/run`, and polls `GET /api/atpg/progress/{progress_id}` every 1.5 s.
2. The backend queues the run for an analysis slot, then runs `AtpgAnalysisService.run()` on a worker thread, reporting each stage (and the LLM's current reasoning line) to the tracker.
3. If the browser disconnects, the run is cancelled at its next checkpoint.
4. The result is returned as JSON, rendered on the page, and recorded in the run database in the background.
