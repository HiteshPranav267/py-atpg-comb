# PyATPG

**Structure-guided ATPG prioritisation for hardware Trojan triggers in gate-level netlists.**

PyATPG reads a netlist (ISCAS `.bench`, structural Verilog, or RTL it synthesises first), finds the circuit's rarely active logic, ranks it by how much it looks like a Trojan trigger, and has the [ATALANTA](https://github.com/hsluoyz/Atalanta) ATPG engine write a short test set aimed at the top-ranked logic. It also runs a deterministic Trojan detector and gives a verdict. It comes as a command-line tool, a REST API and a web app.

- **Inputs:** `.bench`, gate-level Verilog (primitives, SAED/GSCLib cells, scan flip-flops), RTL Verilog/SystemVerilog via Yosys, VHDL via GHDL.
- **Circuits:** combinational, full scan (every flip-flop a scan cell), or sequential without scan (a native time-frame-expansion engine).
- **No training data:** the ranking is a fixed structural score. A language model is optional.
- **Open source:** MIT licence. ATALANTA is fetched at build time and keeps its own licence (see [Licence](#licence)).

## Why

Rare-event Trojan testing assumes the trigger sits among the circuit's *rarest* signals and tests those first. An attacker can pick a trigger that is only moderately rare (true for 0.1-4% of random inputs). Hundreds of harmless rare signals then outrank it, and a rarity-ordered test list runs out before reaching it. PyATPG fills the list by taking rare logic cones in turn from rarity order and from a ranking by the *shape* of the logic: wide AND trees over unrelated, widely shared signals that feed one rare output.

On 56 Trojan-inserted test circuits across 10 ISCAS'85/'89 and ITC'99 hosts, none of them used for tuning:

| Ordering of the same-size list | Whole Trojan in the list |
|---|---|
| Rarity only (R) | 8 of 56 |
| Rarity + random order (R+Rand) | 8 of 56 |
| Rarity + structural score (R+S) | 28 of 56 |
| Rarity + LLM scores (R+L) | 31 of 56 |

The structural score and the LLM are statistically tied (9-6, p = 0.30), so the free, deterministic score is enough. Limits: on the two largest hosts (b15, b17) no ordering found any Trojan, one of the three held-out sets did not pass on its own, and the LLM ordering lost ground to rarity on one circuit whose trigger never fires. The test sets above are not in this repository; [`examples/`](examples/) has four of their circuits.

## Install

Needs Python 3.11+, git, make and a C++ compiler (Linux, macOS, or WSL on Windows). Node.js 20+ only for the web app.

```bash
git clone https://github.com/HiteshPranav267/py-atpg-comb.git
cd py-atpg-comb
pip install -e .            # installs the `pyatpg` command and the Python dependencies
```

Build ATALANTA into `Atalanta/`, where PyATPG looks for it. These are the steps `build.sh` runs, minus its static-linking check:

```bash
git clone https://github.com/hsluoyz/Atalanta.git
git -C Atalanta checkout a8e07fe4af80c55b0d4ca77e382731b03ad731dc
git -C Atalanta apply ../patches/atalanta.patch   # path is relative to Atalanta/
make -C Atalanta atalanta
```

On **Windows**, build ATALANTA inside WSL and point `ATALANTA_BIN` at a small wrapper script ([docs/configuration.md](docs/configuration.md#atalanta-on-windows)). An ATALANTA build elsewhere works too: set `ATALANTA_BIN` to its path.

## Quick start: command line

```bash
pyatpg examples/c3540_gen_uncommon_s10.v --no-llm
```

This runs standard ATPG over the whole circuit and targeted ATPG over the ranked fault list (here R+S, so all 12 Trojan gates are on it), then prints both, the verdict and a *Trojan check*. The Trojan check appears only because this example's Trojan gates are named `Tj_*`, which gives an answer key:

```
  Trojan check (Trojan gates recognised by their names)
    Trojan gates in the netlist   : 12
    Cone orderings at the same list size (Trojan gates in the list):
      R       (rarity order only)           :   2 / 12  (first Trojan cone: #123 of 160)
      R+Rand1 (rarity + random, seed 1)     :   2 / 12  (first Trojan cone: #92 of 160)
      ...
      R+S     (rarity + structural score)   :  12 / 12  (first Trojan cone: #1 of 160)
```

Circuits with flip-flops need `--full-scan` for scan testing:

```bash
pyatpg examples/s5378_gen_rare_s9.v --full-scan --no-llm -o s5378_report.json
```

Every option, what each output section means and the JSON report are in the **[CLI guide](docs/cli.md)**.

## Quick start: web app

```bash
python -m uvicorn pyatpg.api.app:app          # backend on http://127.0.0.1:8000 (API docs at /docs)
cd frontend && npm install && npm run dev     # web app on http://localhost:5173
```

Open http://localhost:5173/atpg, upload a netlist, choose **Compare**, and for circuits with flip-flops choose **Full scan**. The **[web app guide](docs/web-app.md)** covers the pages, settings, LLM keys and how to point the app at a hosted backend instead of your own machine.

## Using a language model (optional)

Without a key, the structural score ranks the rare cones (the R+S ordering above) and the detectors add their findings. With one, the model's scores take the structural score's place. In our experiments the two were tied, so a key is not needed for good results. Put a key in `.env` (copy [`.env.example`](.env.example)), or pass `--api-key`, or paste it in the web app's Settings. Supported: Groq (default, `openai/gpt-oss-120b`), Gemini, OpenAI, Anthropic, NVIDIA NIM and any OpenAI-compatible endpoint, such as Ollama for local models. Details: [docs/configuration.md](docs/configuration.md).

## Examples

[`examples/`](examples/) holds four Trojan-inserted circuits (1.7k-23k gates) with the results to expect from each, including one where the method fails. See [examples/README.md](examples/README.md).

To make your own, `pyatpg-trojan HOST.v --class uncommon --seed 1` inserts a Trojan with a trigger of chosen rarity (never, rare 0.1-1%, or uncommon 1-4% of random inputs) into a Trojan-free netlist. See the [CLI guide](docs/cli.md#make-your-own-test-circuits).

## Documentation

| Document | What it covers |
|---|---|
| [docs/cli.md](docs/cli.md) | Command-line options, how a file is routed, reading the output, the JSON report |
| [docs/web-app.md](docs/web-app.md) | Running the web app, its pages and settings, using a hosted backend |
| [docs/configuration.md](docs/configuration.md) | Environment variables, LLM keys, ATALANTA location, server settings |
| [docs/how-it-works.md](docs/how-it-works.md) | The analysis pipeline step by step: parsing, full scan, profiling, detectors, cone ranking, ATPG, verdict |
| [docs/architecture.md](docs/architecture.md) | Code layout and how the frontend and backend fit together |
| [db/README.md](db/README.md) | The optional PostgreSQL run database |
| [docs/changelog/changelog.md](docs/changelog/changelog.md) | Development log: what changed and why, with measured effects |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setting up for development and submitting changes |

## Project structure

```
pyatpg/
├── core/        parser, Circuit, RTL front end, full-scan model, graph analysis, settings, run database
├── atpg/        analysis service, profiling, rare cones and the structural score, targeting (+ LLM),
│                ATALANTA runner, fault simulation, Trojan detectors, verdict, Trojan metrics
├── seq/         sequential ATPG engine: time frames, D-algebra, PODEM, simulator
├── llm/         LLM helpers for the sequential engine
├── ranking/     gate rankers and the LLM provider layer (providers.py)
├── trojan/      Trojan insertion: rare_trigger.py (`pyatpg-trojan`) and the web app's dataset generator
├── analysis/    feature extraction
├── evaluation/  ranker evaluation and benchmark runner
├── api/         FastAPI app and routes
└── cli.py       the `pyatpg` command
frontend/        React + Vite + TailwindCSS web app
examples/        Trojan-inserted example circuits
patches/         our changes to ATALANTA, applied at build time
build.sh         server build: dependencies, Yosys warm-up, static ATALANTA build
```

## Deployment

The reference deployment runs the backend on Render (`bash build.sh`, then `uvicorn pyatpg.api.app:app --host 0.0.0.0 --port $PORT`) and the frontend on Vercel (root `frontend`, `npm run build`, `VITE_API_URL` set to the backend URL). See [docs/architecture.md](docs/architecture.md#3-production-deployment-workflow).

## Citing

If you use PyATPG in research, please cite it with the metadata in [CITATION.cff](CITATION.cff).

## Licence

PyATPG is released under the [MIT Licence](LICENSE). ATALANTA (Virginia Tech) is not part of this repository and keeps its own terms: teaching and research use, no commercial use without permission. The build fetches it from its public repository, and `patches/atalanta.patch` holds only our changes to it.

Developed as a B.Tech capstone project at PES University, Bengaluru, by Duggireddy Hitesh Pranav Reddy, Dibyadyuti Mohanty, H Architha and Manas Prasannakumar Kole, under the guidance of Dr. Vaishnavi Sankar, Dr. Nirmala Devi M and Anuros Thomas K.
