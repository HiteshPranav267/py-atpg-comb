# LLM-targeted vs standard ATPG: test set

Circuits for measuring whether LLM-guided fault targeting beats both standard
ATPG and the same targeting without the LLM, on Trojans the deterministic
detector cannot see. Every file is a Trust-Hub full-scan netlist (SAED cell
style) and runs on the ATPG page with **Full scan** on and **Compare Both**.

## What is here

`variants/` holds 15 netlists and `manifest.json` (source, class, seed,
measured trigger probability and Trojan nets of each).

| Files | What they are | Why |
|---|---|---|
| `s35932-T200_fixed`, `s38417-T100_fixed`, `s38417-T200_fixed` | Trust-Hub's own Trojans with one typo repaired: `Trojan1234_NOT` read `Tj_OUT1` twice and never read `Tj_OUT2`; the second read now goes to `Tj_OUT2` | The detector's combinational rule anchors on that typo; without it the detector flags nothing strong |
| `s38417_gen_never_s{1,2}`, `s38584_gen_never_s{1,2}` | A clean-written combinational Trojan (16 tapped host nets, 8 two-input leaves, two 4-input ANDs, one root, XOR payloads) whose trigger never fires in 4096 random patterns | Rare enough that ranking cones by rarity already finds it |
| `s38417_gen_rare_s{1,2}`, `s38584_gen_rare_s{1,2}` | Same shape, trigger 1 in 0.1-1% of patterns | Rare, but 27-98 rarer cones come first |
| `s38417_gen_uncommon_s{1,2}`, `s38584_gen_uncommon_s{1,2}` | Same shape, trigger 1 in 1-4% of patterns | Rare, but 91-224 rarer cones come first |

Seed 1 files have 4 payload gates, seed 2 files have 1. Hosts are the
Trust-Hub Trojan-free s38417 and s38584 scan netlists.

The trigger class was fixed by a rule before any run: taps are drawn at random
until the trigger's estimated probability falls in the band. Two files landed
just outside it once re-simulated (`s38584_gen_rare_s2` at 0.098%,
`s38584_gen_uncommon_s2` at 4.03%); the manifest records the measured value.

Checked before release, with no model involved:

- The detector flags nothing strong on any of the 15 (it flags 0 of the
  generated Trojans' gates at all).
- Ranked by root rarity, the Trojan's cone is 1st-3rd of ~200 for the typo-fixed
  and never-firing files, and 27th-224th for the rare and uncommon ones. With the
  default budget the no-LLM list expands 4 cones on s38417 and 19 on s38584.
  Its Trojan recall, computed locally from the selection alone: 100% on the 7
  typo-fixed and never-firing files, **0% on all 8 rare and uncommon files**.
  Those 8 are where any recall with the LLM is something the rest of the
  pipeline does not find.

## Regenerating

```
python scripts/make_trojan_variants.py \
  --typo "<s35932-T200 scan .v>=s35932-T200_fixed" \
  --typo "<s38417-T100 scan .v>=s38417-T100_fixed" \
  --typo "<s38417-T200 scan .v>=s38417-T200_fixed" \
  --host "<s38417 TjFree scan .v>=s38417" \
  --host "<s38584 TjFree scan .v>=s38584" \
  --out experiments/llm-vs-standard/variants
```

## Runs

Each run reports, under **Standard vs targeted ATPG**, the three test sets side
by side: patterns, tester cycles, generation time, whole-circuit coverage, and
how many Trojan faults each detects and how many patterns it takes to detect
them all. The Trojan check panel reports recall with and without the LLM.

1. **Main round** (default settings): all 15 files, plus the Trojan-free
   s38417, s38584 and s35932 scan netlists as controls.
2. **Spread**: with `LLM_RESULT_CACHE=0` on the backend, the 8 rare and
   uncommon files twice more. Without that setting every repeat reuses the
   first run's cached scores.
3. **Tight budget**: `target_gates` = 50 (Admin page), the 8 rare and uncommon
   files once. Choosing well matters most when the list is short.

What would show the LLM helps: across the rare and uncommon files, higher
Trojan recall with the LLM than without, or the same recall with fewer patterns
to detect all Trojan faults, in most files and repeats, not one.
