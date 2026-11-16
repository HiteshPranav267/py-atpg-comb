# Command-line guide

`pyatpg` is the main way to use PyATPG. It has an interactive menu and six commands:

| Command | What it does | Needs ATALANTA |
|---|---|---|
| `pyatpg` (no arguments) | Interactive menu for everything below | for analyses |
| [`pyatpg run NETLIST`](#pyatpg-run) | Standard vs targeted ATPG, comparison, Trojan verdict, Trojan check. `pyatpg NETLIST` is short for it | yes |
| [`pyatpg rank NETLIST`](#pyatpg-rank) | The ranked rare cones and the targeted fault list, in seconds | no |
| [`pyatpg trojan HOST.v`](#make-your-own-test-circuits) | Insert a Trojan with a trigger of chosen rarity | yes (fire check) |
| [`pyatpg examples [N]`](#pyatpg-examples) | List or run the bundled example circuits | for a full run |
| [`pyatpg doctor`](#pyatpg-doctor) | Check the installation | no |
| [`pyatpg serve`](web-app.md) | Start the optional web backend | yes |

`pyatpg --version` prints the version, and `pyatpg COMMAND -h` lists a command's options.

## Before you start

1. Install the package from the repository root: `pip install -e .`. This puts `pyatpg` on your `PATH`. Without installing, `python -m pyatpg` works the same from the repository root.
2. Build ATALANTA into `Atalanta/` (see the [README](../README.md#install)) or set `ATALANTA_BIN` to a build. Without it, combinational and full-scan runs stop with a message listing every path tried. The sequential engine does not need ATALANTA.

Check the install:

```bash
pyatpg doctor            # Python, ATALANTA (with a test run on c17), Yosys, GHDL, LLM key
pyatpg examples 1        # about 10 seconds
```

## The interactive menu

`pyatpg` with no arguments, in a terminal, opens a numbered menu:

```
   1  Analyse a netlist        standard vs targeted ATPG, verdict, Trojan check
   2  Rank rare logic cones    the targeted fault list in seconds, no ATPG
   3  Insert a test Trojan     into a clean netlist, with a trigger of chosen rarity
   4  Bundled examples         four Trojan circuits with known answers
   5  Check setup              ATALANTA, Yosys, LLM key
   6  Settings                 full scan, language model, patterns, output folder
   7  Command reference        the same things as one-line commands
   8  Web interface            optional: start the web backend
   q  Quit
```

Each item asks for what it needs (a path can be pasted with or without quotes) and runs the same code as the matching command. Results go to the output folder, `pyatpg_out/` by default: `<name>_report.json`, `<name>_patterns.txt` and `<name>_fault_list.txt` for an analysis, and `<name>_cones.csv` and `<name>_fault_list.txt` for a ranking. The settings last for the session. In the menu, full scan is on and the language model is off unless you turn it on, so no tokens are spent by accident. Ctrl+C stops the current task and returns to the menu.

## pyatpg run

```
pyatpg run NETLIST [--mode {compare,targeted,non_targeted}] [--full-scan] [--patterns N]
                   [-o REPORT.json] [--json] [-q] [--vectors-out FILE] [--faults-out FILE]
                   [--no-progress]
                   [--no-llm] [--api-key KEY] [--llm-provider P] [--llm-model M] [--llm-base-url URL]
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
| `-o`, `--output` | `atpg_report.json` | Where the JSON report is written. With `--json`, no file is written unless `-o` is given |
| `--json` | off | Print the JSON report to stdout instead of the text report, for `jq` and scripts. Messages go to stderr |
| `-q`, `--quiet` | off | Print one summary line: patterns standard -> targeted, verdict, Trojan gates in the list |
| `--vectors-out` | | Write the targeted run's test patterns (the standard run's with `--mode non_targeted`) as plain text: a header naming the inputs and outputs in bit order, then one `<input bits> <output bits>` line per pattern |
| `--faults-out` | | Write the targeted fault list in ATALANTA's `-f` format (`GATE /0` and `GATE /1` per gate) |
| `--no-progress` | off | No live progress line. The line is only shown when stderr is a terminal |

While a run works, one line on stderr shows the current stage and the elapsed time. The exit code is `1` if the file cannot be read or the analysis fails (the reason is printed), `130` if you stop it with Ctrl+C, and `0` otherwise.

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

## Examples of run

```bash
# Combinational circuit, no model: compare standard and targeted ATPG
pyatpg run examples/c3540_gen_uncommon_s10.v --no-llm

# Save the targeted patterns and fault list for another tool
pyatpg run examples/c3540_gen_uncommon_s10.v --no-llm --vectors-out c3540.pat --faults-out c3540.flt

# A folder of netlists, one line each, a report per file
for f in netlists/*.v; do pyatpg run "$f" --full-scan --no-llm --quiet -o "${f%.v}.json"; done

# Pattern reduction of every report, with jq
pyatpg run examples/c3540_gen_uncommon_s10.v --no-llm --json | jq .comparison.pattern_reduction_pct

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

## pyatpg rank

```
pyatpg rank NETLIST [--top N] [--patterns N] [--json] [--faults-out FILE] [--cones-out FILE]
```

Runs the analysis up to the fault list and stops there: parse, full-scan model, profile, detectors, rare cones, then the structural ranking taken in turn with rarity order. No ATALANTA and no model are used, so it takes seconds, even on circuits with tens of thousands of gates. The fault list is the one `pyatpg run --no-llm` hands ATALANTA. Circuits with flip-flops are always ranked on their full-scan view, because rare cones are defined on the combinational core.

```
  #  Root net    Gates  Taps  Fires  Rarity #  Score  Rare in  Picked by            In list  Trojan
  1  Tj_Trigger     11    16  3.30%       123   3.14     0.06  score template       yes          11
  2  N4859           2     2  0.32%         1   1.58     0.50  rarity order         yes
  3  N4076           2     2  0.39%         8   2.00     0.50  rare-input template  yes
```

| Column | Meaning |
|---|---|
| `#` | Place in the ranked order. The fault list takes cones from the top until it is full |
| Root net | The rare net the cone ends in |
| Gates / Taps | Gates in the cone, and nets that feed it from outside |
| Fires | How often the root takes its rarer value under random patterns |
| Rarity # | The cone's place in plain rarity order (1 = rarest). A trigger with a high number here is one rarity-first testing misses |
| Score | The first template: a wide conjunction of widely shared signals from across the design |
| Rare in | The second template: the share of the cone's taps that are rare nets themselves |
| Picked by | Which of the two templates, or rarity order, put the cone at its place |
| In list | Whether the cone's root is in the targeted fault list |
| Trojan | Known Trojan gates in the cone, shown only when the netlist names them (see *Trojan check* below) |

When Trojan gates are known by name, a *Trojan check* table follows. It shows, for each ordering at the same list size (rarity only, rarity + random for three seeds, rarity + structure), how many Trojan gates reach the list and where the first Trojan cone sits.

| Option | Default | What it does |
|---|---|---|
| `--top` | `25` | Cones to print, `0` for all |
| `--patterns` | `3000` | Random patterns for the toggle-rate profile |
| `--json` | off | Print the circuit summary, every ranked cone, the fault list and the Trojan check as JSON |
| `--faults-out` | | Write the fault list in ATALANTA's `-f` format |
| `--cones-out` | | Write every ranked cone as CSV, one row per cone with the columns above |

## pyatpg examples

```
pyatpg examples                 # list the four bundled circuits and what to expect
pyatpg examples 1               # full analysis of example 1 (full scan where needed, no model)
pyatpg examples 2 --rank        # rank only, in seconds
pyatpg examples b17 --llm       # by file name; --llm uses a model if a key is set
```

The circuits and their expected results are described in [examples/README.md](../examples/README.md).

## pyatpg doctor

Checks, in order: the Python version, ATALANTA (found, then a test run on ISCAS'85 c17 that must reach 100% coverage), Yosys for RTL input, GHDL for VHDL input, the language-model key (provider and model only, never the key) and the optional web packages. It exits with `1` when ATALANTA cannot be used and `0` otherwise. `--quick` skips the test run. On Windows it warns, without running it, when the ATALANTA found is a Windows build that may open debug dialogs.

## Make your own test circuits

`pyatpg trojan` (also installed as `pyatpg-trojan`) inserts a combinational Trojan of chosen rarity into a Trojan-free gate-level Verilog netlist (ISCAS primitives or SAED-style named-pin cells). It is the generator behind the project's test sets and the [examples](../examples/README.md).

```bash
pyatpg trojan HOST.v [--class {never,rare,uncommon}] [--payloads N] [--seed N] [-o OUT.v]
```

| Option | Default | What it does |
|---|---|---|
| `--class` | `uncommon` | How often the trigger is true under random inputs: `never` (no hit in 4,096 patterns), `rare` (0.1-1%), `uncommon` (1-4%) |
| `--payloads` | `1` | How many host nets the trigger flips (one XOR each) |
| `--seed` | `1` | Random seed. The same host, class, payload count and seed always give the same file |
| `-o` | `<host>_gen_<class>_s<seed>.v` | Output netlist. A `.json` file beside it records the taps, payload hosts, measured trigger probability and the Trojan's nets |

The trigger taps 16 host nets through eight 2-input ANDs, two 4-input ANDs and an AND root (`Tj_Trigger`). Taps are drawn until the built netlist's simulated trigger probability falls in the class's band, and ATALANTA must be able to detect `Tj_Trigger` stuck-at-0, which proves the trigger can fire. So ATALANTA must be available. All Trojan gates are named `Tj_*`, so `pyatpg run` and `pyatpg rank` print a *Trojan check* for the result.

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
| Something else looks wrong | Run `pyatpg doctor`: it tests each part of the installation |
| `LLM could not be run: <reason>` | The key was rejected, is out of credit or is rate-limited. Fix the key, add backup keys, or use `--no-llm` |
| ATALANTA times out on a large design | Raise `PYATPG_ATALANTA_TIMEOUT` |
| No *Trojan check* section | The netlist has no gates named like Trojan logic, which is normal for a real design. Use the *Trojan verdict* |
