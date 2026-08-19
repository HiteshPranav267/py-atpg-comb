"""Provider-agnostic LLM layer for ATPG, built around context reduction.

Two logical stages, matching the two different jobs an LLM is asked to do:

    LLM-1 (:mod:`circuit_context`)  -- one call per circuit. Reads the full
        circuit/feature data already computed by the rest of the project
        (SCOAP, toggle rates, graph stats) and produces a short cached
        summary. Never repeated for the same circuit.

    LLM-2 (:mod:`vector_generator`) -- one call per batch of hard faults.
        Never receives the full circuit, the full fault list, or the full
        vector history -- only a compact, retrieval-built context for the
        faults currently in play (:mod:`context`), plus the LLM-1 summary
        and a short delta of what changed since the last call.

:mod:`store` holds the full circuit/feature/fault/history data locally so
none of it is lost -- it is simply never all sent to the model at once.
:mod:`token_tracker` counts every call and every character sent/received so
the context-reduction strategy's savings are measurable, not assumed.

Nothing here computes fault coverage. The engine's deterministic simulator
remains the only source of truth (see pyatpg.seq.engine / faultsim).
"""
