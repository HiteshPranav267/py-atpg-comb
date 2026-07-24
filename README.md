# PyATPG

PyATPG is a Python framework for structural analysis of gate-level circuits and
AI-assisted Hardware Trojan candidate prioritization. It parses `.bench` and
gate-level Verilog netlists, builds a directed circuit graph, computes
testability/security features, ranks suspicious gates, and can generate Cadence
Genus/Modus job bundles for targeted ATPG validation.

The project currently has two ways to use it:

- A Flask + Socket.IO web UI for loading circuits, visualizing topology,
  running Trojan analysis, and dispatching generated Modus jobs over SSH.
- A four-phase CLI pipeline for batch analysis, bundle generation, and
  post-Modus report validation.

## What It Does

PyATPG's core flow is:

1. Parse a `.bench`, `.v`, or `.verilog` netlist.
2. Convert the circuit into a NetworkX directed graph.
3. Compute structural and activity features:
   - SCOAP controllability: `CC0`, `CC1`
   - SCOAP observability: `CO`
   - logic level and fan-in/fan-out
   - approximate random-vector toggle rate
   - betweenness centrality
4. Rank suspicious gates using either:
   - an API-backed LLM analyzer, or
   - an offline heuristic fallback when no API key is configured.
5. Generate targeted Cadence Genus/Modus scripts and a fault list for the
   flagged gates.
6. Optionally parse Modus reports and cross-reference ATPG results against the
   AI/heuristic suspect list.

## Repository Layout

```text
pyatpg-git/
├── app.py                  # Flask web server entry point
├── pyatpg_app.py           # Flask app variant used by desktop/packaged mode
├── main.py                 # pywebview desktop wrapper around pyatpg_app.py
├── build_app.py            # desktop packaging helper
├── pyatpg/
│   ├── core/
│   │   ├── netlist.py      # .bench parser and Circuit/Gate models
│   │   ├── verilog_parser.py
│   │   ├── graph_analysis.py
│   │   ├── simulator.py
│   │   └── memory.py       # exact-netlist cache
│   ├── agents/
│   │   ├── llm_analyzer.py # OpenAI/Anthropic/Kimi/Ollama/custom + offline fallback
│   │   ├── remote_agent.py # web UI SSH/SFTP bridge
│   │   └── agent.py        # CLI SSH/SFTP bridge
│   ├── exporters/
│   │   ├── modus_exporter.py
│   │   └── modus_log_parser.py
│   └── utils/
├── scripts/
│   ├── run_phase1.py       # feature extraction
│   ├── run_phase2.py       # Gemini/offline prioritization
│   ├── run_phase3.py       # Modus bundle generation / optional SSH run
│   ├── run_phase4.py       # Modus report validation
│   └── run_pipeline.py     # phase 1 -> 4 orchestration
├── templates/              # web UI HTML
├── static/                 # web UI CSS/JS
├── benchmarks/             # ISCAS-style benchmark circuits
├── reference/              # reference Cadence outputs and scripts
├── legacy/                 # older ATPG/prototype code kept for reference
├── modus_jobs/             # generated Modus job bundle zips
├── docs/                   # additional notes and workflow docs
└── requirements.txt
```

Generated files are normally written to `outputs/` and `modus_jobs/`. The
`outputs/` directory may not exist in a fresh checkout; scripts create it when
needed.

## Installation

Use Python 3.10+ if possible.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The web UI loads some browser libraries from CDNs, so the page needs internet
access unless those assets are vendored locally.

## Run The Web UI

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

The web UI currently supports:

- SSH login to a remote machine through Paramiko.
- Netlist paste/load through `/api/circuit/load`.
- Trojan analysis through `/api/circuit/analyze`.
- Cytoscape graph visualization through `/api/circuit/graph`.
- Remote job dispatch through `/api/jobs/dispatch`.

Notes:

- The upload tab exists in the UI, but the current JavaScript load handler uses
  pasted text. File upload is supported by the Flask route, but not fully wired
  in the frontend.
- The "Export Modus Job Bundle" button is shown after analysis, but the current
  frontend does not attach a click handler to it. Use the CLI `--local-only`
  flow to generate a local bundle.
- Modus log comparison/parsing exists in the CLI code, not as a complete web UI
  workflow.

## Run The Desktop Wrapper

`main.py` starts the Flask app on port `5005` and opens it in a pywebview
window:

```bash
python main.py
```

If the native window cannot start, it falls back to opening the local web app in
the default browser.

## CLI Pipeline

The CLI scripts are useful when you want reproducible files on disk or want to
run without the web UI.

### Full local pipeline through bundle generation

```bash
python scripts/run_pipeline.py benchmarks/c432.v --local-only
```

This runs:

1. Phase 1: generate `outputs/gate_profiles.json`
2. Phase 2: generate `outputs/targeted_faults.txt`
3. Phase 3: create a zip bundle under `modus_jobs/`

`--local-only` stops before SSH execution.

### Individual phases

```bash
python scripts/run_phase1.py benchmarks/c432.v \
  -o outputs/gate_profiles.json \
  -p 3000

python scripts/run_phase2.py outputs/gate_profiles.json \
  -o outputs/targeted_faults.txt

python scripts/run_phase3.py benchmarks/c432.v outputs/targeted_faults.txt \
  --local-only
```

After running Cadence Modus externally and collecting reports, run Phase 4:

```bash
python scripts/run_phase4.py \
  --targeted-log targeted_coverage.rpt \
  --baseline-log baseline_coverage.rpt \
  --fault-detail-log fault_detail.rpt \
  --ai-suspects outputs/targeted_faults.txt \
  --phase1-json outputs/gate_profiles.json \
  -o outputs/security_report.json
```

`--baseline-log` and `--fault-detail-log` are optional. Without a fault detail
report, Phase 4 can parse coverage but cannot confidently classify specific
suspect gates as cleared or Trojan candidates.

## LLM Configuration

The web app uses `LLMAnalyzer` when either `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` is present. If neither key exists, it automatically uses
`OfflineLLMAnalyzer`.

Supported analyzer providers in `pyatpg/agents/llm_analyzer.py`:

- `openai`, default model `gpt-4o`, env var `OPENAI_API_KEY`
- `anthropic`, default model `claude-sonnet-4-20250514`, env var
  `ANTHROPIC_API_KEY`
- `kimi`, default model `moonshot-v1-32k`, env var `MOONSHOT_API_KEY`
- `ollama`, default model `llama3.1`, OpenAI-compatible local endpoint
  `http://localhost:11434/v1`
- `custom`, using `LLM_API_KEY` and an OpenAI-compatible `base_url`

Example:

```bash
export OPENAI_API_KEY="your_key_here"
python app.py
```

The CLI `run_phase2.py` is separate from the web analyzer. It supports Gemini
through `--api-key` if `google-genai` is installed; otherwise it uses its own
offline heuristic.

## Modus Job Bundles

`pyatpg/exporters/modus_exporter.py` writes zip bundles containing:

- the source netlist as `<circuit>.v`
- `dft.tcl`
- `run_targeted.atpg.tcl`
- `run_baseline.atpg.tcl`
- `<circuit>_targeted.flt`
- `job_metadata.json`
- `run_all.sh`

Default library paths inside generated scripts are currently:

```text
/home/cmos/s1196_capstone/saed90nm_typ.lib
/home/cmos/s1196_capstone/saed90nm.v
```

Change those by constructing `ModusExporter` with `lib_path` and `tech_v_path`,
or by editing the generated scripts before running them on the Cadence server.

## Benchmarks

The `benchmarks/` directory contains ISCAS-style combinational and sequential
examples in `.bench` and `.v` forms, including circuits such as `c17`, `c432`,
`c880`, `s27`, `s1196`, `s1238`, `s35932`, and others. There is also a
`trojan_example.bench` for quick experimentation.

The `reference/` and parts of `legacy/` contain saved Cadence outputs, generated
test scripts, and older prototypes. They are useful for understanding the
project history, but the main maintained package is under `pyatpg/`.

## Testing And Smoke Checks

There is no formal pytest suite yet. The existing `test_scan.py` and
`test_s35932.py` are script-style smoke checks that parse benchmark netlists and
print graph/scan information.

Useful checks:

```bash
python test_scan.py
python test_s35932.py
python scripts/run_pipeline.py benchmarks/c432.v --local-only --patterns 50
```

The last command uses fewer random patterns so it completes faster during a
sanity check.

## Current Limitations

- The Verilog parser is intended for gate-level netlists and common standard
  cell instantiations. It is not a full Verilog/SystemVerilog frontend.
- Sequential behavior is represented structurally through DFF/SDFF nodes; the
  random toggle estimator primarily evaluates combinational propagation.
- The web UI has a few incomplete controls, especially local export and file
  upload wiring.
- Remote execution assumes a compatible Cadence/Unix environment and may need
  path changes for a different server.
- Several hardcoded demo/default SSH values exist in UI/script files. Treat
  them as local lab defaults, not production-safe configuration.

## Authors

- Duggireddy Hitesh Pranav Reddy
- Dibyadyuti Mohanty
- H Architha
- Manas Prasannakumar Kole
