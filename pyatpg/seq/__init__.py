"""Sequential ATPG engine.

Everything here is additive to the existing combinational (Atalanta-backed)
pipeline in ``pyatpg.atpg`` -- nothing in that package is modified by this
one. A circuit with flip-flops never reaches Atalanta (it cannot levelize a
netlist with state elements); instead it is routed to this package.

Architecture, in one paragraph: :mod:`timeframe` unrolls a sequential
circuit into a flat multi-cycle combinational netlist reusing the existing
``Circuit``/``Gate`` model; :mod:`dalgebra` supplies the 5-value (0,1,X,D,D')
algebra as good/faulty ternary pairs; :mod:`podem` searches the unrolled
netlist for an input sequence that activates and propagates one fault;
:mod:`simulator` is a real, reset-driven, multi-cycle functional/fault
simulator that is the *only* thing allowed to claim a fault is detected or
to report coverage -- PODEM candidates are proposals, never truth; and
:mod:`engine` orchestrates fault-list construction, candidate generation
(PODEM + random-fill + mutation, optionally reordered by an LLM), and
deterministic verification into a minimal test-sequence set.
"""
