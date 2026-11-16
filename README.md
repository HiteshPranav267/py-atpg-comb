# PyATPG

**Structure-guided ATPG prioritisation for hardware Trojan triggers in gate-level netlists. A command-line tool.**

PyATPG reads a netlist (ISCAS `.bench`, structural Verilog, or RTL it synthesises first) and finds the circuit's rarely active logic. It ranks that logic by how much it looks like a Trojan trigger, then has the [ATALANTA](https://github.com/hsluoyz/Atalanta) ATPG engine write a short test set aimed at the top of the ranking. A deterministic Trojan detector also gives a verdict.

```
$ pyatpg rank examples/c3540_gen_uncommon_s10.v

  #  Root net    Gates  Taps  Fires  Rarity #  Score  Rare in  Picked by            In list  Trojan
  1  Tj_Trigger     11    16  3.30%       123   3.14     0.06  score template       yes          11
  2  N4859           2     2  0.32%         1   1.58     0.50  rarity order         yes
  3  N4076           2     2  0.39%         8   2.00     0.50  rare-input template  yes
  ...
  Ordering (same list size)     Trojan gates in list  First Trojan cone
  Rarity only                                 2 / 12        #123 of 160
  Rarity + structure (default)               12 / 12          #1 of 160
```

The trigger above fires in 3.3% of random patterns. That is rare, but 122 harmless cones are rarer, so a rarity-ordered test list never reaches it. Ranked by structure, it comes first.

- **Inputs:** `.bench`, gate-level Verilog (ISCAS primitives, SAED/GSCLib, LEDA 250 nm and SkyWater 130 cells, scan flip-flops), RTL Verilog/SystemVerilog via Yosys, VHDL via GHDL.
- **Circuits:** combinational, full scan (every flip-flop a scan cell), or sequential without scan (a native time-frame-expansion engine).
- **No training data:** the ranking is a fixed structural score. A language model is optional.
- **Open source:** MIT licence. ATALANTA is fetched at build time and keeps its own licence (see [Licence](#licence)).

## Why

Rare-event Trojan testing assumes the trigger sits among the circuit's *rarest* signals, and tests those first. An attacker can instead choose a trigger that is only moderately rare (true for 0.1-4% of random inputs). Hundreds of harmless rare signals then outrank it, and a rarity-ordered test list runs out before it gets there. PyATPG fills the list by taking rare logic cones in turn from rarity order and from a ranking by the cone's *shape*. The shape it looks for is a conjunction of many signals from across the design, or of signals that are already rare, feeding one rare output.

On 56 held-out Trojan-inserted circuits across 10 ISCAS'85/'89 and ITC'99 hosts:

| Ordering of the same-size list | Whole Trojan in the list |
|---|---|
| Rarity only | 8 of 56 |
| Rarity + random order | 8 of 56 |
| Rarity + structural score | 28 of 56 |
| Rarity + LLM scores | 31 of 56 |

The score and the LLM are statistically tied (9-6, p = 0.30), so the free, deterministic score is the default. These numbers come from the first version of the score. The default now adds a second template for triggers built from already-rare nets, and follows cones to their full depth so that chain-shaped triggers stay whole (see the [changelog](docs/changelog/changelog.md)). The gain is largest on circuits with tens to a few hundred rare cones. On circuits whose triggers already sit among the rarest nets, the method matches rarity order. The test sets are not in this repository. [`examples/`](examples/) has four of their circuits.

## Install

Needs Python 3.11+, git, make and a C++ compiler (Linux, macOS, or WSL on Windows).

```bash
git clone https://github.com/HiteshPranav267/py-atpg-comb.git
cd py-atpg-comb
pip install -e .            # installs the `pyatpg` command
```

Build ATALANTA into `Atalanta/`, where PyATPG looks for it:

```bash
git clone https://github.com/hsluoyz/Atalanta.git
git -C Atalanta checkout a8e07fe4af80c55b0d4ca77e382731b03ad731dc
git -C Atalanta apply ../patches/atalanta.patch   # path is relative to Atalanta/
make -C Atalanta atalanta
```

On **Windows**, build ATALANTA inside WSL and point `ATALANTA_BIN` at a small wrapper script ([docs/configuration.md](docs/configuration.md#atalanta-on-windows)). A build anywhere else works too: set `ATALANTA_BIN` to its path.

Then check everything:

```bash
pyatpg doctor
```

`pyatpg rank` and `pyatpg examples N --rank` work without ATALANTA.

## Use it

Run `pyatpg` on its own for an **interactive menu**: analyse, rank, insert a test Trojan, the examples, a setup check and settings, with results saved to `pyatpg_out/`. The same things as commands:

| Command | What it does |
|---|---|
| `pyatpg run NETLIST` | Standard vs targeted ATPG, pattern reduction, Trojan verdict, Trojan check. `pyatpg NETLIST` is short for it |
| `pyatpg rank NETLIST` | The ranked rare cones and the targeted fault list in seconds, with no ATPG |
| `pyatpg trojan HOST.v` | Insert a Trojan with a trigger of chosen rarity, to make test circuits with known answers |
| `pyatpg examples [N]` | List or run the four bundled example circuits |
| `pyatpg doctor` | Check ATALANTA (with a test run), Yosys, GHDL and the LLM key |
| `pyatpg serve` | Start the optional web backend |

```bash
pyatpg examples 1                                        # c3540 with an inserted Trojan
pyatpg run design.v --full-scan --no-llm                 # a circuit with flip-flops, scan testing
pyatpg run design.v --vectors-out patterns.txt --faults-out faults.txt
pyatpg run design.v --quiet                              # one summary line
pyatpg run design.v --json | jq .comparison              # the full report for scripts
pyatpg rank design.v --top 40 --cones-out cones.csv
pyatpg trojan c3540.v --class uncommon --seed 10         # 1-4% trigger, gates named Tj_*
```

A run prints the circuit, both ATPG runs (standard and targeted), the comparison, the *Trojan verdict* and, when the Trojan's gates are known by name, a *Trojan check* that compares the orderings at the same list size. Every option, every report section and the JSON report are in the **[CLI guide](docs/cli.md)**.

Batch over a folder:

```bash
for f in netlists/*.v; do pyatpg run "$f" --full-scan --no-llm --quiet -o "${f%.v}.json"; done
```

## Using a language model (optional)

Without a key, the structural score ranks the rare cones. With one, the model scores each cone and its ranking takes the score's place. In our experiments the two were tied, so a key is not needed for good results. Put a key in `.env` (copy [`.env.example`](.env.example)) or pass `--api-key`. `--no-llm` turns the model off even when a key is set. Supported: Groq (default, `openai/gpt-oss-120b`), Gemini, OpenAI, Anthropic, NVIDIA NIM, and any OpenAI-compatible endpoint, such as Ollama for local models. See [docs/configuration.md](docs/configuration.md).

## Examples

[`examples/`](examples/) holds four Trojan-inserted circuits (1.7k-23k gates) and the results to expect from each, including one where the method reaches its limit. See [examples/README.md](examples/README.md), or run `pyatpg examples`.

## Documentation

| Document | What it covers |
|---|---|
| [docs/cli.md](docs/cli.md) | Every command and option, the menu, how a file is routed, reading the output, the JSON report |
| [docs/configuration.md](docs/configuration.md) | Environment variables, LLM keys, ATALANTA location |
| [docs/how-it-works.md](docs/how-it-works.md) | The analysis pipeline step by step: parsing, full scan, profiling, detectors, cone ranking, ATPG, verdict |
| [docs/architecture.md](docs/architecture.md) | Code layout |
| [docs/web-app.md](docs/web-app.md) | The optional web interface |
| [docs/changelog/changelog.md](docs/changelog/changelog.md) | Development log: what changed and why, with measured effects |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setting up for development and submitting changes |

## Project structure

```
pyatpg/
├── cli/         the `pyatpg` command: subcommands, interactive menu, report printing
├── core/        parser, Circuit, RTL front end, full-scan model, graph analysis, settings
├── atpg/        analysis service, profiling, rare cones and the structural ranking, targeting (+ LLM),
│                ATALANTA runner, fault simulation, Trojan detectors, verdict, Trojan metrics
├── seq/         sequential ATPG engine: time frames, D-algebra, PODEM, simulator
├── llm/         LLM helpers for the sequential engine
├── ranking/     gate rankers and the LLM provider layer (providers.py)
├── trojan/      Trojan insertion: rare_trigger.py (`pyatpg trojan`) and the web app's dataset generator
├── analysis/    feature extraction
├── evaluation/  ranker evaluation and benchmark runner
└── api/         optional web backend (FastAPI)
web/             optional web interface (React + Vite)
examples/        Trojan-inserted example circuits
patches/         our changes to ATALANTA, applied at build time
build.sh         server build for the web backend: packages, Yosys warm-up, static ATALANTA build
```

## Web interface (optional)

A browser front end with the same analysis is in [`web/`](web/). It needs the server packages, `pip install -e ".[web]"`, and Node.js 20+. Start it with `pyatpg serve`, then run `npm install && npm run dev` in `web/`. See [docs/web-app.md](docs/web-app.md).

## Citing

If you use PyATPG in research, please cite it with the metadata in [CITATION.cff](CITATION.cff).

## Licence

PyATPG is released under the [MIT Licence](LICENSE). ATALANTA (Virginia Tech) is not part of this repository and keeps its own terms: teaching and research use, no commercial use without permission. The build fetches it from its public repository, and `patches/atalanta.patch` holds only our changes to it.

Developed as a B.Tech capstone project at PES University, Bengaluru, by Duggireddy Hitesh Pranav Reddy, Dibyadyuti Mohanty, H Architha and Manas Prasannakumar Kole, under the guidance of Dr. Vaishnavi Sankar, Dr. Nirmala Devi M and Anuros Thomas K.
