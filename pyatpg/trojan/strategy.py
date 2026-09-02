import random
from abc import ABC, abstractmethod
from typing import List, Optional

import networkx as nx

from pyatpg.core.netlist import Circuit
from pyatpg.core.graph_analysis import CircuitGraph

#: Fraction of a circuit's nets that a biased strategy treats as its "rare"
#: pool to sample triggers and payload targets from. The HT-insertion
#: literature sets its rareness thresholds (HTS/OCR, or switching activity)
#: so that roughly 5% of nets qualify, then picks trigger nets from within
#: that set -- it does not take the single most extreme net. Sampling is what
#: makes a *dataset* of trojans: with an argmax the same circuit yields the
#: same trojan every time.
RARE_NET_FRACTION = 0.05

#: The pool never shrinks below this, or the "rarest 5%" of a small circuit is
#: one or two nets and sampling degenerates back to a fixed choice.
MIN_RARE_POOL = 8


def _rare_pool(ranked: List[str], num_needed: int = 1) -> List[str]:
    """The rarest slice of an already-ranked (rarest-first) net list."""
    size = max(MIN_RARE_POOL, num_needed, round(len(ranked) * RARE_NET_FRACTION))
    return ranked[:size] or list(ranked)


def _circuit_digraph(circuit: Circuit) -> nx.DiGraph:
    """Signal-flow graph of the *clean* circuit: edge driver -> load."""
    g = nx.DiGraph()
    g.add_nodes_from(circuit.nodes)
    for out, gate in circuit.gates.items():
        for inp in gate.inputs:
            g.add_edge(inp, out)
    return g


def _acyclic_payload_candidates(
    circuit: Circuit, trigger_nets: List[str]
) -> List[str]:
    """Payload targets that will not close a combinational feedback loop.

    The payload splices in a gate whose fan-in includes the trigger output,
    and the trigger output is driven by ``trigger_nets``. So the insertion
    adds the path::

        trigger_nets -> ht_trigger_out -> payload_target -> (payload fan-out)

    If any trigger net is reachable from ``payload_target`` in the existing
    netlist, that path joins an existing ``payload_target -> ... -> trigger_net``
    path into a cycle. A cyclic "combinational" netlist is illegal, and
    ATALANTA does not reject it cleanly -- it segfaults during levelisation.

    Uniform-random selection almost never picks such a target, which is why
    ``RandomStrategy`` appeared to work; ``LowScoapStrategy`` and
    ``RareNodeStrategy`` pick the hardest-to-control net as a trigger (deep in
    the circuit) and the hardest-to-observe / rarest net as the payload
    (near the inputs), which is close to a worst case for this loop.
    """
    g = _circuit_digraph(circuit)
    forbidden = set(trigger_nets)
    for net in trigger_nets:
        if net in g:
            forbidden |= nx.ancestors(g, net)
    # Sorted, because ``circuit.nodes`` is a set: its iteration order varies
    # with the process's string-hash seed, which would make a seeded run
    # irreproducible across processes.
    return [
        n for n in sorted(circuit.nodes)
        if n not in circuit.inputs and n not in forbidden
    ]


class InsertionStrategy(ABC):
    """Chooses where a trojan goes.

    ``seed`` fixes the whole dataset: one strategy instance is reused for
    every variant, so its RNG advances between variants (each gets a
    different placement) while a re-run with the same seed reproduces the
    dataset exactly. ``seed=None`` draws from OS entropy.
    """

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)
        self._graph_cache: dict = {}

    def _graph(self, circuit: Circuit) -> CircuitGraph:
        """A ``CircuitGraph`` for ``circuit``, reused across variants.

        The generator hands every variant a fresh clone of the same clean
        circuit, so without this each variant recomputed SCOAP and re-ran the
        5000-pattern toggle simulation -- twice, once for the trigger pick and
        once for the payload -- on identical structure.
        """
        key = (
            len(circuit.nodes),
            len(circuit.gates),
            hash(frozenset(circuit.gates)),
        )
        graph = self._graph_cache.get(key)
        if graph is None:
            graph = CircuitGraph(circuit)
            self._graph_cache[key] = graph
        return graph

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        """Select `num_inputs` nets from the circuit to act as the Trojan trigger inputs."""
        pass
        
    @abstractmethod
    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        """Select a net from the circuit to act as the Trojan payload target.

        ``trigger_nets`` are the already-selected trigger inputs; the payload
        target must be chosen so that splicing the trigger output into it does
        not create a combinational feedback loop.
        """
        pass


class RandomStrategy(InsertionStrategy):
    """Selects trigger and payload nets purely randomly."""
    
    @property
    def name(self) -> str:
        return "random"
        
    def _get_valid_nets(self, circuit: Circuit) -> List[str]:
        # Exclude primary outputs from being triggers if they have no fan-out, 
        # but for randomness, any internal net or input is fine.
        # Actually, let's just pick any net except the ones we are about to inject.
        # Sorted for the same reproducibility reason as the payload candidates.
        return sorted(circuit.nodes)
        
    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        valid_nets = self._get_valid_nets(circuit)
        if len(valid_nets) < num_inputs:
            raise ValueError("Circuit too small to select triggers.")
        return self._rng.sample(valid_nets, num_inputs)

    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        # Avoid primary inputs as payload targets because we'd have to override
        # external pins, and avoid any net from which a trigger net is
        # reachable, which would make the circuit cyclic (see
        # _acyclic_payload_candidates).
        candidates = _acyclic_payload_candidates(circuit, trigger_nets or [])
        if not candidates:
            raise ValueError(
                "No acyclic payload target available for the selected trigger nets."
            )
        return self._rng.choice(candidates)


class LowScoapStrategy(RandomStrategy):
    """Selects trigger nets that are hard to control, and a payload target
    that is hard to observe, using SCOAP testability metrics.

    Real trojans are inserted on nets unlikely to be driven to a specific
    value (and effects unlikely to be observed) during ordinary test
    generation, so they survive functional/structural testing undetected.
    Uniform random selection has no such bias and is just as likely to land
    on an easy-to-test net, which is both unrealistic and -- because
    testability-based detectors (including this tool's own targeted-ATPG
    mode) rank gates by exactly these SCOAP metrics -- makes the inserted
    trojan indistinguishable from ordinary hard-to-test noise only by luck,
    rather than by the trojan's own placement being genuinely evasive.
    """

    @property
    def name(self) -> str:
        return "low_scoap"

    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        valid_nets = self._get_valid_nets(circuit)
        if len(valid_nets) < num_inputs:
            raise ValueError("Circuit too small to select triggers.")
        cc0, cc1, _co = self._graph(circuit).compute_scoap()
        # Highest combined CC0+CC1 = hardest to drive to either value. Sample
        # from that pool rather than taking the top `num_inputs`: an argmax
        # returns the same nets for every variant of the same circuit, and
        # the top few are usually adjacent nets in one deep cone -- which is
        # both a single trojan repeated N times and the narrowest possible
        # placement.
        ranked = sorted(
            valid_nets,
            key=lambda n: (-(cc0.get(n, 1) + cc1.get(n, 1)), n),
        )
        return self._rng.sample(_rare_pool(ranked, num_inputs), num_inputs)

    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        candidates = _acyclic_payload_candidates(circuit, trigger_nets or [])
        if not candidates:
            raise ValueError(
                "No acyclic payload target available for the selected trigger nets."
            )
        _cc0, _cc1, co = self._graph(circuit).compute_scoap()
        # Highest CO = hardest to propagate to an observable output; sampled
        # from the hardest-to-observe pool for the reason above.
        ranked = sorted(candidates, key=lambda n: (-co.get(n, 0), n))
        return self._rng.choice(_rare_pool(ranked))


class RareNodeStrategy(RandomStrategy):
    """Selects trigger nets with low simulated toggle rates, and a payload
    target that toggles rarely, so the trojan's activation condition and
    its effect both stay hidden from ordinary random/functional testing.

    See ``LowScoapStrategy`` for why unbiased random selection is an
    unrealistic and easily-detected placement.
    """

    @property
    def name(self) -> str:
        return "rare_node"

    def select_trigger_nets(self, circuit: Circuit, num_inputs: int) -> List[str]:
        valid_nets = self._get_valid_nets(circuit)
        if len(valid_nets) < num_inputs:
            raise ValueError("Circuit too small to select triggers.")
        toggle_data = self._graph(circuit).compute_toggle_rates()
        # Rarest-toggling first, then sampled -- see LowScoapStrategy for why
        # the top-`num_inputs` slice is not used.
        ranked = sorted(
            valid_nets,
            key=lambda n: (toggle_data.get(n, {}).get('toggle_rate', 0.5), n),
        )
        return self._rng.sample(_rare_pool(ranked, num_inputs), num_inputs)

    def select_payload_target(
        self, circuit: Circuit, trigger_nets: Optional[List[str]] = None
    ) -> str:
        candidates = _acyclic_payload_candidates(circuit, trigger_nets or [])
        if not candidates:
            raise ValueError(
                "No acyclic payload target available for the selected trigger nets."
            )
        toggle_data = self._graph(circuit).compute_toggle_rates()
        ranked = sorted(
            candidates,
            key=lambda n: (toggle_data.get(n, {}).get('toggle_rate', 0.5), n),
        )
        return self._rng.choice(_rare_pool(ranked))
