# Example circuits

Four circuits from the project's third held-out test set. Each is a public benchmark host with one inserted Trojan:

- a **trigger:** 16 signals tapped from across the circuit and combined by an AND tree into one net, true for 0.1-4% of random inputs;
- one to four **payloads:** XOR gates that flip real wires when the trigger fires.

The Trojan's gates are named `Tj_*`, so PyATPG's *Trojan check* can score each fault list against them. None of these circuits was used to design or tune the method.

| File | Host | Gates | Flip-flops | Trojan | Trigger true in | Payloads |
|---|---|---|---|---|---|---|
| `c3540_gen_uncommon_s10.v` | ISCAS'85 c3540 | 1,681 | 0 | uncommon | 3.9% of inputs | 1 |
| `s5378_gen_rare_s9.v` | ISCAS'89 s5378 | 2,794 | 179 | rare | 0.68% | 4 |
| `s9234_gen_uncommon_s10.v` | ISCAS'89 s9234 | 5,609 | 211 | uncommon | 2.4% | 1 |
| `b17_gen_rare_s9.v` | ITC'99 b17 (full-scan version) | 22,660 | 0 | rare | 0.78% | 4 |

The trigger rarity was measured over 4,096 random patterns when the Trojan was inserted.

## Run them

```bash
pyatpg examples/c3540_gen_uncommon_s10.v --no-llm
pyatpg examples/s5378_gen_rare_s9.v      --no-llm --full-scan
pyatpg examples/s9234_gen_uncommon_s10.v --no-llm --full-scan
pyatpg examples/b17_gen_rare_s9.v        --no-llm      # about 2 minutes
```

Or let PyATPG pick the flags: `pyatpg examples` lists them, `pyatpg examples 2` runs the second, and `pyatpg examples 2 --rank` shows only the ranked cones, in seconds and without ATALANTA.

## What to expect

These are measured with `--no-llm`, ATALANTA at its default fixed seed, the default list size and the default ranking (PyATPG 1.1). *Patterns* compares standard ATPG with the targeted run. *Trojan gates in the list* comes from the report's *Trojan check* section, or from `pyatpg rank`.

| File | Trojan gates | Rarity only | Rarity + random (3 seeds) | Rarity + structure | Trigger cone's place: rarity / structure | Patterns: standard -> targeted |
|---|---|---|---|---|---|---|
| `c3540_gen_uncommon_s10.v` | 12 | 2 | 2, 2, 3 | **12** | 123rd / **1st** | 270 -> 57 (79% fewer) |
| `s5378_gen_rare_s9.v` | 15 | 0 | 0, 0, 0 | **15** | 50th / **17th** | 649 -> 83 (87% fewer) |
| `s9234_gen_uncommon_s10.v` | 12 | 2 | 2, 2, 2 | 2 | 167th / 32nd | 957 -> 75 (92% fewer) |
| `b17_gen_rare_s9.v` | 15 | 5 | 5, 5, 5 | 5 | 242nd / 374th | 6,439 -> 163 (97% fewer) |

How to read them:

- **c3540 and s5378:** the trigger is moderately rare. Dozens to over a hundred harmless rare cones are rarer than it, so rarity order reaches its cone only long after the list is full. The structural ranking puts it 1st or 17th, inside the list (48 and 34 cones), and the whole Trojan enters it. On c3540 the 2 Trojan gates that rarity order finds are the trigger's root and its payload XOR, which the XOR-shape detector adds to every list.
- **s9234: a trade-off.** The structural ranking lifts the trigger from 167th to 32nd, but the list holds 29 cones, so it stops just short. The default ranking takes two trigger templates in turn: a wide conjunction of widely shared signals (this trigger's shape) and a conjunction of nets that are rare themselves (the shape of TRIT's Trojans, where the first template alone did not beat rarity order). Sharing the places costs this trigger a few. With the first template alone, `PYATPG_SCORE_MODE=single pyatpg examples 3`, it is 16th and all 12 Trojan gates are in the list, as in PyATPG 1.0.
- **b17:** the limit of the method. The circuit has more than 900 rare cones, and the list holds only a few of them, so no ordering reaches the trigger. All orderings keep only the detector's 5 gates. Use this circuit to test scale, not the ranking.

Without a language model, the targeted run's own fault list is the *rarity + structure* list, so the report's *In the targeted fault list* line matches that row. With an LLM key, the model's ranking replaces the structural one and an R+L row is added. In the project's experiments, the two were statistically tied.

A run with flip-flops but without `--full-scan` goes to the sequential engine instead. For `s9234_gen_uncommon_s10.v` it reaches 13% fault coverage with 10 test sequences (40 clock cycles), and the report has no ranked fault list.

## Where they come from

The hosts are public benchmarks: ISCAS'85, ISCAS'89 and ITC'99. b17 is the ITC'99 full-scan release, converted from `.bench` to primitive Verilog. The Trojans were inserted with `pyatpg-trojan` (`pyatpg/trojan/rare_trigger.py`), and the same command rebuilds each file byte for byte from its host. For example:

```bash
pyatpg-trojan c3540.v --class uncommon --seed 10 --payloads 1   # -> c3540_gen_uncommon_s10.v
pyatpg-trojan s5378.v --class rare --seed 9 --payloads 4        # -> s5378_gen_rare_s9.v
```

The hosts themselves are not included; any copy of the ISCAS'85/'89 primitive-style Verilog works the same way. See [docs/cli.md](../docs/cli.md#make-your-own-test-circuits) to build other test circuits.
