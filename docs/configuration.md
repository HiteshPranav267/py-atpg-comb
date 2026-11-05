# Configuration

PyATPG reads its configuration from environment variables. A `.env` file in the repository root is loaded automatically, and it never overrides a variable that is already set. Copy [`.env.example`](../.env.example) to `.env` to start. `.env` is git-ignored, so keys put there stay out of the repository.

The same variables apply to the CLI, the API server and the web app's backend.

## ATALANTA

| Variable | Default | Meaning |
|---|---|---|
| `ATALANTA_BIN` | `Atalanta/atalanta` (Linux, macOS), `Atalanta/data/Atalanta.exe` or `Atalanta/Atalanta.exe` (Windows) | Path to the ATALANTA executable or a wrapper script. Checked first, before the in-repo locations |
| `ATALANTA_JOBS_ROOT` | `<system temp>/atalanta_jobs` | Where each run's ATALANTA input, fault list, patterns and logs are written |

### ATALANTA on Windows

ATALANTA is a Unix program. Build it inside WSL (Ubuntu) with the commands in the [README](../README.md#install). Then create a wrapper, for example `atalanta-wsl.cmd`, with the path changed to your clone:

```bat
@echo off
rem Runs the Linux ATALANTA build in WSL, in the caller's folder.
wsl.exe --cd "%CD%" -- /mnt/c/path/to/py-atpg-comb/Atalanta/atalanta %*
```

Point PyATPG at it before running the CLI or the backend:

```powershell
$env:ATALANTA_BIN = "C:\path\to\atalanta-wsl.cmd"
```

Avoid Windows debug builds of ATALANTA: they open a modal "Debug Error" dialog that blocks the run.

## LLM keys

A language model is optional. The first key found is used, in this order:

1. a key given with the request: `--api-key` on the CLI, or the web app's Settings;
2. `LLM_API_KEY`, with `LLM_PROVIDER` naming the vendor;
3. a vendor's own variable: `GROQ_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `NVIDIA_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`.

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `groq` (default), `gemini`, `openai`, `anthropic`, `nvidia` or `openai_compatible`. Without it, a vendor's own variable names its vendor, and any other key is recognised from its prefix: `gsk_` Groq, `AIza` Gemini, `sk-ant-` Anthropic, `nvapi-` NVIDIA, `sk-` OpenAI |
| `LLM_MODEL` | Model id. Each provider has a default; Groq's is `openai/gpt-oss-120b` |
| `LLM_BASE_URL` | Endpoint for OpenAI-compatible servers, for example `http://localhost:11434/v1` for Ollama or an OpenRouter URL |
| `<KEY_VARIABLE>_2` ... `_5` | Backup keys for the same provider and model, for example `GROQ_API_KEY_2`. When one key is rate-limited, out of credit or rejected, the next takes over for the rest of the run |

Tuning the LLM requests (defaults suit Groq's free tier):

| Variable | Default | Meaning |
|---|---|---|
| `LLM_REQUEST_TOKEN_BUDGET` | `7000` | Ceiling on one request (prompt plus reply). Groq's free tier refuses requests over 8,000 tokens per minute. `0` turns the check off, which is fine for local or paid models |
| `LLM_RESULT_CACHE` | `1` | `0` skips the score cache (`outputs/comb_llm_cache.json`) and calls the model again. Use it when measuring run-to-run variation |
| `LLM_CONTEXT_GATES` | `300` | Most gates per prompt in the per-gate view (`PYATPG_LLM_VIEW=gates`). `0` sends every gate |
| `LLM_RANKING_GATE_LIMIT` | `60` | Most gates the Ranking page's LLM ranker sends. `0` sends all of them |
| `LLM_RANKING_TOKEN_BUDGET` | `6000` | Token ceiling for the Ranking page's LLM ranker |

## Analysis settings

Each of these is a *live setting*. On a server, the Admin page can change it while the server runs (until restart). Otherwise the environment variable sets it.

| Variable | Default | Meaning |
|---|---|---|
| `PYATPG_TARGET_GATES` | `0` | Size of the targeted fault list before detector findings are added. `0` scales with the circuit: 30% of its gates, within 20-200, capped by how many gates pass the structural heuristic |
| `PYATPG_LLM_SHARE` | `0.8` | Share of the list filled with whole rare cones, taken in turn from rarity order and a second ranking: the model's scores, or the structural score without a model. The structural heuristic's top picks fill the rest |
| `PYATPG_LLM_VIEW` | `cones` | What the model scores: `cones` (one row per rare cone) or `gates` (one row per gate, the older view) |
| `PYATPG_LLM_BATCHES` | `4` | Most requests one selection may be split into when the cones do not fit one request |
| `PYATPG_DETECTOR_IN_TARGETING` | `1` | `0` keeps detector findings out of the targeted list. Use it to measure what the ranking finds by itself. The verdict still uses the detectors |
| `PYATPG_LLM_TIMEOUT` | `90` | Seconds per LLM request. Retries and backup keys come on top |
| `PYATPG_ATALANTA_TIMEOUT` | `600` | Seconds per ATALANTA run |
| `PYATPG_ATALANTA_SEED` | `1` | ATALANTA's random seed, fixed so repeated runs give identical patterns. `0` seeds from the clock |
| `PYATPG_SEQ_TIME_BUDGET` | `40` | Seconds of search per sequential ATPG run |
| `PYATPG_PARALLEL_COMPARE` | on with several cores, off on Render | Run a sequential comparison's two passes in parallel processes |
| `PYATPG_YOSYS_TIMEOUT` | `3600` | Seconds allowed for RTL synthesis |

## Server settings

| Variable | Default | Meaning |
|---|---|---|
| `PYATPG_ADMIN_TOKEN` | unset | Enables the Admin page and `/api/admin/*`. Requests must send it in `X-Admin-Token` |
| `PYATPG_MAX_CONCURRENT_RUNS` | `1` | Analyses that may run at once. Others wait in a queue |
| `PYATPG_MAINTENANCE` | `0` | `1` rejects new analyses. Runs already going finish |
| `PYATPG_MAX_GATES` | `60000` | Largest design accepted (gates plus flip-flops). `0` means no limit. Applies only on Render (`RENDER` set) |
| `PYATPG_YOSYS_MAX_MB` | `300` | Memory limit for Yosys. `0` means no limit. Applies only on Render |
| `DATABASE_URL` | unset | PostgreSQL connection string. When set, every run is recorded ([db/README.md](../db/README.md)) |
| `PYATPG_RECORD_RUNS` | `1` | `0` stops recording runs even with `DATABASE_URL` set |
| `PYATPG_CLIENT_SALT` | `DATABASE_URL` | Salt for the anonymous client ids stored with each run |

## Frontend

| Variable | Where | Meaning |
|---|---|---|
| `VITE_API_URL` | Build time (`frontend/.env.production`, or the build environment) | The backend URL a production build talks to. A user can override it in **Settings → Backend Connection** |

In development (`npm run dev`) the frontend forwards `/api` to `http://localhost:8000` and needs no variable.
