# Web app guide

The web app is a React frontend (`frontend/`) for the PyATPG backend (`pyatpg/api/`). It runs the same analysis as the [command line](cli.md) and adds live progress, tables and charts.

## Run it on your own machine

You need the [install steps](../README.md#install) done (Python package and ATALANTA) and Node.js 20+.

```bash
# Terminal 1, from the repository root: the backend
python -m uvicorn pyatpg.api.app:app            # http://127.0.0.1:8000, API docs at /docs

# Terminal 2: the frontend
cd frontend
npm install                                     # first time only
npm run dev                                     # http://localhost:5173
```

In development the frontend sends every `/api` request to `http://localhost:8000` (see `frontend/vite.config.ts`). Nothing else needs configuring. Add `--reload` to the backend command while editing Python code.

On Windows, set `ATALANTA_BIN` in the backend's terminal before starting it ([configuration.md](configuration.md#atalanta-on-windows)).

## Run an analysis

1. **Circuits:** upload a netlist (`.bench`, `.v`, `.sv`, or a `.zip` of RTL). The page shows the parsed circuit and its properties, and it becomes the *active circuit* for the other pages.
2. **ATPG:** choose
   - the **analysis mode**: *Compare Both* (standard and targeted ATPG, side by side), *LLM Heuristic (Targeted)* or *ATPG Tool (Non-Targeted)*;
   - the **flip-flop model**: *Full scan* (default, every flip-flop is a scan cell, ATALANTA runs) or *No scan* (sequential engine, from reset). This only matters for circuits with flip-flops;
   - the number of **toggle simulation patterns** for profiling.
3. Click **Run analysis**. The page polls the backend every 1.5 s and shows the current stage and its elapsed time. With a language model, it also shows one line of what the model is reasoning about.
4. Read the results on the same page:
   - **Run reports:** coverage, patterns and faults per ATPG run.
   - **Comparison:** pattern reduction, and both pattern sets simulated over the same faults.
   - **Trojan verdict:** strong suspects and weaker leads, with the evidence for each.
   - **Trojan check:** only for netlists whose Trojan gates are named like Trojan logic (`Tj_*`, `HT_*`, Trust-Hub names). It shows rare-net activation, recall of each gate list, and the *cone orderings at the same budget* table (R, R+Rand, R+S, and R+L with a model).
   - **LLM usage:** calls and tokens, only with a model.

A language model is used whenever a key is configured, in Settings or on the server. Without one, the structural score ranks the rare cones (R+S) and the detectors add their findings. See [cli.md](cli.md#reading-the-report) for what every number means. The web panels show the same data as the CLI report.

## The other pages

| Page | What it is for |
|---|---|
| Dashboard | The active circuit at a glance and quick-start links |
| Circuits | Upload and inspect a netlist (properties, hierarchy) |
| Trojan Dataset | Insert simple Trojans into the active circuit to build test data. Strategies: random, rare-node or low-SCOAP placement. You choose trigger and payload gate types, the number of variants and a seed. Generated gates are named `HT_*`, so the Trojan check recognises them |
| Feature Analysis | Per-gate features (SCOAP, toggle rate, fan-in/out, level) of the active circuit |
| Ranking | Rank gates with one of the pluggable rankers (random, SCOAP, rare node, LLM) |
| Results | A ranker's suspect list next to the ATPG run's undetected faults |
| Reports | A summary of the session: the active circuit and its top suspects |
| Analytics | This server's usage: circuits parsed, ATPG runs by circuit kind, fault coverage over time, recent activity |
| Help | A short guide inside the app |
| Admin | Server status, queue, live settings, run history, storage and the run database. Needs the server's `PYATPG_ADMIN_TOKEN` |

## Settings (gear icon, top right)

Settings are stored in your browser only.

- **Backend Connection:** the backend this app talks to. Leave it blank to use the default: the local backend in development, or the URL the app was built with. Enter `http://localhost:8000` to use your own machine from a deployed frontend, or a hosted backend's URL to use that one. This is how you switch between local and hosted without rebuilding.
- **LLM provider, model and key:** Groq with `openai/gpt-oss-120b` is preselected. *Load from account* lists the models your key can use. A base URL can be set for OpenAI-compatible servers such as Ollama. **Backup keys** take over when the main key is rate-limited or out of credit.

Keys entered here are sent with each run and are not stored on the server.

## Use a hosted backend

The frontend reads its default backend URL from `VITE_API_URL` at build time (`frontend/.env.production` for production builds). Either:

- set **Settings → Backend Connection** to the hosted URL in a running app, or
- build your own frontend against it: `VITE_API_URL=https://your-backend.example.com npm run build`, and serve `frontend/dist/`.

A hosted backend on a small instance has limits that a local run does not. It rejects designs over 60,000 gates plus flip-flops and stops Yosys above 300 MB (`PYATPG_MAX_GATES`, `PYATPG_YOSYS_MAX_MB`; both apply only when the server runs on Render). It runs one analysis at a time, and the others queue. A free Render instance sleeps when idle, so the first request after a pause can take about a minute.

## Deploy your own

- **Backend:** any Linux host with Python 3.11+ and a C++ toolchain. Run `bash build.sh` (installs the requirements, warms up Yosys, builds ATALANTA statically and smoke-tests it), then `uvicorn pyatpg.api.app:app --host 0.0.0.0 --port $PORT`. Optional environment: an LLM key, `PYATPG_ADMIN_TOKEN` and `DATABASE_URL` ([configuration.md](configuration.md)).
- **Frontend:** any static host. Build from `frontend/` with `npm run build` and `VITE_API_URL` set to the backend. `frontend/vercel.json` holds the single-page-app routing for Vercel.

How requests flow between the two is described in [architecture.md](architecture.md#3-production-deployment-workflow).

## Frontend development

```bash
cd frontend
npm run dev      # development server with hot reload
npm run lint     # oxlint
npm run build    # type check (tsc) and production build
```

Key code: `src/services/api.ts` (API client), `src/pages/ATPG.tsx` (the analysis page), `src/components/atpg/` (result panels), `src/components/layout/SettingsModal.tsx` and `LlmKeyEditor.tsx` (settings).
