# Command-line guide

The `pyatpg` command runs one full analysis on one netlist, prints a report and writes the full results as JSON. It runs the same code as the web app and the REST API.

## Before you start

1. Install the package from the repository root: `pip install -e .`. This puts `pyatpg` on your `PATH`. Without installing, `python -m pyatpg` works the same from the repository root.
2. Build ATALANTA into `Atalanta/` (see the [README](../README.md#install)) or set `ATALANTA_BIN` to a build. Without it, combinational and full-scan runs stop with a message listing every path tried. The sequential engine does not need ATALANTA.

Check the install:

```bash
pyatpg --help
pyatpg examples/c3540_gen_uncommon_s10.v --no-llm     # about 10 seconds
```

## Usage

```
pyatpg NETLIST [--mode {compare,targeted,non_targeted}] [--full-scan] [--no-llm]
               [--api-key KEY] [--llm-provider P] [--llm-model M] [--llm-base-url URL]
               [--patterns N] [-o REPORT.json]
```

| Option | Default | What it does |
|---|---|---|
| `NETLIST` | | A `.bench` file, a gate-level or RTL `.v`/`.sv` file, or a `.zip` or folder of RTL sources (Verilog, SystemVerilog or VHDL) |
| `--mode` | `compare` | `compare` runs both ATPG runs below and compares them. `targeted` runs ATALANTA on the ranked fault list only. `non_targeted` runs ATALANTA on every fault (standard ATPG) |
| `--full-scan` | off | Treat every flip-flop as a scan cell, so ATALANTA tests the combinational core (industry-standard scan testing). Without it, a circuit with flip-flops goes to the sequential engine. No effect on combinational circuits |
| `--no-llm` | off | Never call a language model, even if a key is set in the environment or `.env` |
| `--api-key` | | LLM key for this run. Without it, a key from the environment or `.env` is used if one is set |
| `--llm-provider` | `auto` | `groq`, `gemini`, `openai`, `anthropic`, `nvidia` or `openai_compatible`. `auto` recognises the vendor from the key's prefix |
| `--llm-model` | provider default | Model id, for example `openai/gpt-oss-120b` on Groq |
| `--llm-base-url` | | Endpoint for OpenAI-compatible servers, for example `http://localhost:11434/v1` for Ollama |
| `--patterns` | `3000` | Random patterns for the toggle-rate profile. ATALANTA's own patterns are not affected |
| `-o`, `--output` | `atpg_report.json` | Where the JSON report is written |

The exit code is `1` if the file cannot be read or the analysis fails (the reason is printed), and `0` otherwise.

## What happens to your file

| Input | Path through the tool |
|---|---|
| Circuit without flip-flops | Profiled, ranked, then ATALANTA |
| Circuit with flip-flops, `--full-scan` | Converted to its full-scan model (flip-flop outputs become inputs, their inputs become outputs, scan enable held at 0), then as above |
| Circuit with flip-flops, no `--full-scan` | The native sequential engine: time-frame expansion, PODEM across clock cycles, and detection confirmed by multi-cycle simulation from reset. The report shows coverage, sequences and clock cycles, with no ranked fault list |
| RTL, a `.zip` or a folder | Synthesised with Yosys (the `yowasp-yosys` pip package, or a native `yosys`) into gates first, then as above. VHDL needs GHDL on `PATH` |

When a run takes the sequential path, a note in the report says so. Add `--full-scan` if you meant scan testing.

## Language model or not

A model is consulted when a key is found: `--api-key` first, then the environment, then `.env` (see [configuration.md](configuration.md#llm-keys)). `--no-llm` turns it off. Without a model:

- The fault list is the structural heuristic's top picks, then whole rare cones taken in turn from the structural score's ranking and from rarity order (the R+S ordering), plus the detectors' findings.
- The rarity-only (R), random (R+Rand) and structural-score (R+S) orderings are still computed and scored when the Trojan is known (see the Trojan check below). They make no model call and no extra ATALANTA run.

With a model, it scores each rare cone 0-100, and its ranking takes the structural score's place: its top cones and the rarest cones are taken in turn (R+L), and an R+L row is added to the Trojan check. Scores are cached per netlist, model and prompt in `outputs/comb_llm_cache.json` (under the folder you run from), so a repeat run costs no tokens. Set `LLM_RESULT_CACHE=0` to force a fresh call. If the key is rejected or the provider cannot be reached, the run stops with `LLM could not be run: <reason>` instead of quietly running without the model.

## Reading the report

The sections are printed in this order. Any value ATALANTA did not report shows as *Not reported by the ATPG tool*, never as 0.

**Circuit.** Inputs, outputs, gates, flip-flops and logic depth, as read. Under `--full-scan`, flip-flops show as 0 because they have become pseudo inputs and outputs.

**ATPG Tool (Non-Targeted).** Standard ATPG over every collapsed fault: fault coverage, patterns before and after compaction, detected, redundant (proven untestable) and aborted faults, CPU time. *Whole-circuit pattern coverage* is the result of fault-simulating these patterns over every stuck-at fault of the circuit.

**LLM Heuristic (Targeted).** The same figures for the ranked fault list only. The label stays the same when no model was used. A targeted run's *fault coverage* is a percentage of its own short list, not of the circuit. If faults abort, the run is repeated with a larger search budget, and each attempt is listed.

**Comparison.**
- *Pattern reduction* is how many fewer patterns the targeted set needs.
- The two *fault coverage* lines measure different fault lists, so do not subtract one from the other to judge the method.
- The *Whole-circuit, same fault list* lines simulate both pattern sets over the same faults. Those two numbers can be compared directly.

**LLM usage** (only with a model). Calls, cache hits and tokens, per stage.

**Circuit analysis.** A short structural summary, written by the model when one is used. It lists the hardest-to-observe gates, the rarest-toggling nets and the high fan-out hubs.

**Trojan verdict.** The tool's own answer, with no answer key needed:
- *Trojan suspected* when some gate reaches confidence 0.7: a logic-clocked register, a payload spliced in front of an output, or two independent weaker signs on the same gate.
- *Inconclusive* when only weak leads exist.
- *No Trojan indicated* when nothing was found. This means no evidence was found, not that the circuit is proven clean.

The section lists the strong suspects (up to 10 by name) and the number of weaker leads. How each kind of evidence is scored: [how-it-works.md](how-it-works.md#4-trojan-detectors-deterministic-no-llm).

**Trojan check.** Printed only when the netlist's Trojan gates can be recognised by name: Trust-Hub `Tg*`, `Trojan*`, `Trigger*`, `*Payload*`, `Tj_*` (as in the [examples](../examples/README.md)), or the `HT_*` names of the web app's Trojan Dataset generator. It is an answer key for experiments, not detection.
- *In the targeted fault list* counts the Trojan gates the run's own list contains.
- *Cone orderings* gives, for each way of ordering the rare cones at the same list size, how many Trojan gates end up in the list. It also gives the place of the first cone holding a Trojan gate.

**Correlated insights.** Overlap between the circuit analysis's regions and the faults that stayed undetected. This shows co-occurrence only, not a measurement.

## The JSON report

The file written with `-o` holds everything the printout shows, and more. Top-level fields:

| Field | Contents |
|---|---|
| `circuit` | Name, format, inputs, outputs, gates, flip-flops, depth |
| `runs.non_targeted`, `runs.targeted` | Per ATPG run: `results` (coverage, patterns, faults), `timing`, `test_vectors`, `faults` (undetected faults), `pattern_coverage`, `rare_nodes` (rare nets the patterns drive to their rare value), `attempts` |
| `comparison` | Coverage, pattern reduction and the same-fault-list figures, with notes on how to read them |
| `targeting` | The ranked list: gates chosen, the heuristic floor, the model's picks and scores (`context`), detector findings |
| `verdict` | `verdict`, `confidence`, `suspects` (gate, confidence, evidence, reasons, ATPG status), `summary`, `caveats` |
| `trojan_ground_truth` | Only when Trojan gates are known by name: recall and precision of each list, `orderings` (per ordering: `gates`, `trojan_hits`, `recall`, `first_trojan_cone`, `cones`), Trojan faults proven untestable |
| `three_way` | Compare mode with a model only: standard, targeted without the model and targeted with it, side by side (patterns, tester cycles, time, coverage, Trojan faults detected) |
| `sequential` | Sequential runs only: the engine's results and notes |
| `llm_usage` | Calls, tokens, cache hits, time per stage |
| `notes` | Every decision the tool made about this input (routing, synthesis, scan conversion, fallbacks) |

ATALANTA's raw files for each run (the `.bench` it read, the fault list, the patterns and its log) are kept under `atalanta_jobs/` in the system temporary folder, or under `ATALANTA_JOBS_ROOT` if that is set.

## Examples

```bash
# Combinational circuit, no model: compare standard and targeted ATPG
pyatpg examples/c3540_gen_uncommon_s10.v --no-llm

# Circuit with flip-flops, tested under full scan
pyatpg examples/s9234_gen_uncommon_s10.v --full-scan --no-llm -o s9234.json

# The same circuit on the sequential engine (no scan)
pyatpg examples/s9234_gen_uncommon_s10.v --no-llm

# Only the targeted run, with a Groq key and a chosen model
pyatpg examples/s5378_gen_rare_s9.v --full-scan --mode targeted \
       --api-key "$GROQ_API_KEY" --llm-model openai/gpt-oss-120b

# A local model through Ollama
pyatpg examples/c3540_gen_uncommon_s10.v --llm-provider openai_compatible \
       --llm-base-url http://localhost:11434/v1 --llm-model llama3.1 --api-key ollama

# An RTL archive (for example a Trust-Hub download), synthesised first
pyatpg AES-T900.zip --full-scan
```

The results to expect from each example circuit are in [examples/README.md](../examples/README.md).

## Make your own test circuits

`pyatpg-trojan` inserts a combinational Trojan of chosen rarity into a Trojan-free gate-level Verilog netlist (ISCAS primitives or SAED-style named-pin cells). It is the generator behind the project's test sets and the [examples](../examples/README.md).

```bash
pyatpg-trojan HOST.v [--class {never,rare,uncommon}] [--payloads N] [--seed N] [-o OUT.v]
```

| Option | Default | What it does |
|---|---|---|
| `--class` | `uncommon` | How often the trigger is true under random inputs: `never` (no hit in 4,096 patterns), `rare` (0.1-1%), `uncommon` (1-4%) |
| `--payloads` | `1` | How many host nets the trigger flips (one XOR each) |
| `--seed` | `1` | Random seed. The same host, class, payload count and seed always give the same file |
| `-o` | `<host>_gen_<class>_s<seed>.v` | Output netlist. A `.json` file beside it records the taps, payload hosts, measured trigger probability and the Trojan's nets |

The trigger taps 16 host nets through eight 2-input ANDs, two 4-input ANDs and an AND root (`Tj_Trigger`). Taps are drawn until the built netlist's simulated trigger probability falls in the class's band, and ATALANTA must be able to detect `Tj_Trigger` stuck-at-0, which proves the trigger can fire. So ATALANTA must be available. All Trojan gates are named `Tj_*`, so `pyatpg` prints a *Trojan check* for the result.

The web app's *Trojan Dataset* page is a different, simpler generator (one trigger and one payload gate per variant, named `HT_*`).

## Useful settings for command-line runs

These environment variables apply to the CLI as well as to the server. The full list is in [configuration.md](configuration.md).

| Variable | Default | Use |
|---|---|---|
| `ATALANTA_BIN` | `Atalanta/atalanta` | Path to an ATALANTA build (or a wrapper script) |
| `PYATPG_ATALANTA_TIMEOUT` | `600` | Seconds per ATALANTA run. Raise it for designs over ~100k gates |
| `PYATPG_TARGET_GATES` | `0` (scale with the circuit) | Fixed size for the targeted fault list |
| `PYATPG_SEQ_TIME_BUDGET` | `40` | Seconds of search per sequential run |
| `LLM_RESULT_CACHE` | `1` | `0` forces a fresh model call instead of the cached scores |

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `No runnable Atalanta executable found ... Tried: ...` | Build ATALANTA into `Atalanta/` or set `ATALANTA_BIN` |
| A Windows dialog "Debug Error" pops up | A Windows debug build of ATALANTA is being used. Build it under WSL and use the wrapper in [configuration.md](configuration.md#atalanta-on-windows) |
| `Note: Sequential circuit (no-scan model): routed to the native time-frame-expansion ATPG engine` | The circuit has flip-flops and `--full-scan` was not given |
| `LLM could not be run: <reason>` | The key was rejected, is out of credit or is rate-limited. Fix the key, add backup keys, or use `--no-llm` |
| ATALANTA times out on a large design | Raise `PYATPG_ATALANTA_TIMEOUT` |
| No *Trojan check* section | The netlist has no gates named like Trojan logic, which is normal for a real design. Use the *Trojan verdict* |
