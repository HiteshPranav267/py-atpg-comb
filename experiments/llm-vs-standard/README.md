# LLM-targeted vs standard ATPG

Circuits for measuring whether LLM-guided fault targeting beats both standard
ATPG and the same targeting without the LLM, on Trojans the deterministic
detector cannot see. Every file is a Trust-Hub full-scan netlist (SAED cell
style) and runs on the ATPG page with **Full scan** on and **Compare Both**.

| Folder | Role |
|---|---|
| `dev/` | The first 15 circuits. Already run once; the selection method was changed after looking at those results, so they are the development set. |
| `heldout/` | 12 fresh circuits from new seeds, generated after the method was fixed and not run before. The comparison is measured here. |

Each folder has a `manifest.json` (source, class, seed, measured trigger
probability, the Trojan's nets).

## The circuits

**Typo-fixed** (`dev/` only): Trust-Hub s35932-T200 and s38417-T100/T200, whose
`Trojan1234_NOT` read `Tj_OUT1` twice and never read `Tj_OUT2`; the second read
now goes to `Tj_OUT2`. The detector's combinational rule anchors on that typo
and finds nothing strong without it.

**Generated**: a clean-written combinational Trojan in the Trust-Hub
Trojan-free s38417 or s38584: 16 tapped host nets, 8 two-input leaves, two
4-input ANDs, one root (`Tj_Trigger`), XOR payloads (4 for odd seeds, 1 for
even). Three trigger classes, fixed by rule before any run:

| Class | Trigger is 1 in | Trigger's cone by rarity (held-out) |
|---|---|---|
| `never` | no pattern of 4096 | 2nd-3rd, except `s38584_gen_never_s4`: 116th (many host cones never toggle either, and are larger) |
| `rare` | 0.1-1% | 41st-112th |
| `uncommon` | 1-4% | 96th-233rd |

Checked on `heldout/` with no model: the detector flags none of the 12
Trojans' gates, and the no-LLM list finds the whole Trojan in 3 `never` files
and at most 1 gate in the other 9.

The class is checked on the built netlist's own simulation, and Atalanta must
detect `Tj_Trigger` stuck-at-0, i.e. the trigger can fire and its effect reaches
an output; otherwise the candidate is drawn again. That check came after two
`dev/` files (`s38584_gen_never_s1/s2`) turned out to have triggers that can
never fire (a tap tied to a constant): standard ATPG detected only 11 of 30 and
5 of 24 of their Trojan faults. Count them out of any `dev/` total.

## What the method does

Both targeted lists test the same number of gates. The structural heuristic's
top picks fill 20% (`llm_share` 0.8); the rest is filled by whole rare cones
(a rare net and the logic under it, plus up to 4 gates its root drives).
Without the LLM the cones come in root-rarity order. With it, from the LLM's
ranking and root-rarity order in turn.

Why in turn: on `dev/`, the LLM's ranking alone found 121 of 200 Trojan gates
and rarity order alone 120, each finding Trojans the other missed. Replaying
the LLM's recorded scores with the two orderings in turn found 169, losing to
rarity on no file except one gate of `s38417-T100_fixed`. Those choices were
made on `dev/`, so `heldout/` is the measurement.

## Regenerating

```
python scripts/make_trojan_variants.py \
  --typo "<s35932-T200 scan .v>=s35932-T200_fixed" \
  --typo "<s38417-T100 scan .v>=s38417-T100_fixed" \
  --typo "<s38417-T200 scan .v>=s38417-T200_fixed" \
  --host "<s38417 TjFree scan .v>=s38417" --host "<s38584 TjFree scan .v>=s38584" \
  --seeds 1 2 --out experiments/llm-vs-standard/dev

python scripts/make_trojan_variants.py \
  --host "<s38417 TjFree scan .v>=s38417" --host "<s38584 TjFree scan .v>=s38584" \
  --seeds 3 4 --out experiments/llm-vs-standard/heldout
```

(`dev/` was generated before the band and fire checks existed; rerunning the
first command today would give different files.)

## Runs on `heldout/`

Each run's *Standard vs targeted ATPG* panel gives patterns, tester cycles,
generation time, whole-circuit coverage, Trojan faults detected and patterns
needed to detect them all, for standard ATPG and both targeted lists; the
*Trojan check* panel gives Trojan recall with and without the LLM.

1. **Main**: all 12, default settings.
2. **Spread**: with `LLM_RESULT_CACHE=0` set on the backend (Render
   environment), the 8 `rare` and `uncommon` files twice more.
3. **Tight budget**: *Targeted fault-list size* = 50 (Admin page), the 8 `rare`
   and `uncommon` files once.

What would show the LLM helps: on the `rare` and `uncommon` files, higher
Trojan recall with the LLM than without, in most files and repeats, with no
loss on the `never` files.
