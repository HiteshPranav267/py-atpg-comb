"""
Graph Analysis Module for PyATPG
Converts parsed circuits into NetworkX graphs,
computes SCOAP testability metrics, structural features,
and exports data for visualization and LLM analysis.
"""

import networkx as nx
import random
import json


class CircuitGraph:
    """Converts a parsed Circuit into a NetworkX directed graph with analysis."""

    def __init__(self, circuit):
        self.circuit = circuit
        self.G = nx.DiGraph()
        self._build()

        # Cached analysis results
        self._levels = None
        self._scoap = None

    # ------------------------------------------------------------------ build
    def _build(self):
        inputs = self._as_set(getattr(self.circuit, 'inputs', []))
        outputs = self._as_set(getattr(self.circuit, 'outputs', []))
        gate_outputs = set(str(k) for k in self.circuit.gates)

        # Primary inputs
        for inp in inputs:
            self.G.add_node(inp, node_type='PI', gate_type='INPUT')

        # Gates
        for name, gate in self.circuit.gates.items():
            name_s = str(name)
            gtype = getattr(gate, 'gtype', getattr(gate, 'type', 'UNKNOWN')).upper()
            ntype = 'PO' if name_s in outputs else 'gate'
            self.G.add_node(name_s, node_type=ntype, gate_type=gtype)

            for inp in gate.inputs:
                inp_s = str(inp)
                if inp_s not in self.G:
                    if inp_s in inputs:
                        self.G.add_node(inp_s, node_type='PI', gate_type='INPUT')
                    elif inp_s in gate_outputs:
                        pass  # will be added when that gate is processed
                    else:
                        self.G.add_node(inp_s, node_type='wire', gate_type='WIRE')
                self.G.add_edge(inp_s, name_s)

        # Flip-flops
        ffs = getattr(self.circuit, 'flipflops', None)
        if ffs:
            for ff in ffs:
                q, d = str(ff.q), str(ff.d)
                is_scan = getattr(ff, 'is_scan', False)
                node_t = 'SDFF' if is_scan else 'DFF'
                if q not in self.G:
                    self.G.add_node(q, node_type=node_t, gate_type=node_t)
                else:
                    self.G.nodes[q]['gate_type'] = node_t
                    self.G.nodes[q]['node_type'] = node_t
                if d not in self.G:
                    self.G.add_node(d, node_type='wire', gate_type='WIRE')
                self.G.add_edge(d, q)
        else:
            dffs = getattr(self.circuit, 'dffs', {})
            if isinstance(dffs, dict):
                for q, d in dffs.items():
                    qs, ds = str(q), str(d)
                    self.G.add_node(qs, node_type='DFF', gate_type='DFF')
                    if ds not in self.G:
                        self.G.add_node(ds, node_type='wire', gate_type='WIRE')
                    self.G.add_edge(ds, qs)

        # Mark remaining output nodes
        for out in outputs:
            if out in self.G and self.G.nodes[out].get('node_type') not in ('PO',):
                self.G.nodes[out]['is_output'] = True

    @staticmethod
    def _as_set(collection):
        return set(str(x) for x in collection)

    # --------------------------------------------------------------- levels
    def compute_levels(self):
        if self._levels:
            return self._levels
        levels = {}
        try:
            for node in nx.topological_sort(self.G):
                preds = list(self.G.predecessors(node))
                levels[node] = (max(levels.get(p, 0) for p in preds) + 1) if preds else 0
        except nx.NetworkXUnfeasible:
            for node in self.G.nodes:
                levels[node] = 0
        self._levels = levels
        return levels

    # --------------------------------------------------------------- SCOAP
    def compute_scoap(self):
        if self._scoap:
            return self._scoap

        cc0, cc1, co = {}, {}, {}

        # -- forward pass (controllability) --
        try:
            topo = list(nx.topological_sort(self.G))
        except nx.NetworkXUnfeasible:
            topo = list(self.G.nodes)

        for node in topo:
            data = self.G.nodes[node]
            ntype = data.get('node_type', '')
            gtype = data.get('gate_type', '')
            preds = list(self.G.predecessors(node))

            if ntype == 'PI' or ntype == 'DFF' or ntype == 'SDFF' or not preds:
                cc0[node] = 1
                cc1[node] = 1
            elif gtype == 'AND':
                cc1[node] = sum(cc1.get(p, 1) for p in preds) + 1
                cc0[node] = min(cc0.get(p, 1) for p in preds) + 1
            elif gtype == 'OR':
                cc0[node] = sum(cc0.get(p, 1) for p in preds) + 1
                cc1[node] = min(cc1.get(p, 1) for p in preds) + 1
            elif gtype == 'NAND':
                cc0[node] = sum(cc1.get(p, 1) for p in preds) + 1
                cc1[node] = min(cc0.get(p, 1) for p in preds) + 1
            elif gtype == 'NOR':
                cc1[node] = sum(cc0.get(p, 1) for p in preds) + 1
                cc0[node] = min(cc1.get(p, 1) for p in preds) + 1
            elif gtype == 'NOT':
                p = preds[0]
                cc0[node] = cc1.get(p, 1) + 1
                cc1[node] = cc0.get(p, 1) + 1
            elif gtype == 'XOR' and len(preds) == 2:
                a, b = preds
                cc0[node] = min(cc0.get(a, 1) + cc0.get(b, 1),
                                cc1.get(a, 1) + cc1.get(b, 1)) + 1
                cc1[node] = min(cc0.get(a, 1) + cc1.get(b, 1),
                                cc1.get(a, 1) + cc0.get(b, 1)) + 1
            elif gtype == 'BUF':
                p = preds[0]
                cc0[node] = cc0.get(p, 1) + 1
                cc1[node] = cc1.get(p, 1) + 1
            else:
                cc0[node] = 1
                cc1[node] = 1

        # -- backward pass (observability) --
        co = {n: float('inf') for n in self.G.nodes}
        outputs = self._as_set(getattr(self.circuit, 'outputs', []))
        for n in self.G.nodes:
            d = self.G.nodes[n]
            if d.get('node_type') == 'PO' or str(n) in outputs or self.G.out_degree(n) == 0:
                co[n] = 0
            else:
                for succ in self.G.successors(n):
                    if self.G.nodes[succ].get('node_type') in ('DFF', 'SDFF'):
                        co[n] = 0
                        break

        for node in reversed(topo):
            gtype = self.G.nodes[node].get('gate_type', '')
            preds = list(self.G.predecessors(node))

            for pred in preds:
                others = [p for p in preds if p != pred]
                if gtype in ('AND', 'NAND'):
                    cost = co[node] + sum(cc1.get(p, 1) for p in others) + 1
                elif gtype in ('OR', 'NOR'):
                    cost = co[node] + sum(cc0.get(p, 1) for p in others) + 1
                elif gtype in ('NOT', 'BUF'):
                    cost = co[node] + 1
                elif gtype in ('DFF', 'SDFF'):
                    cost = 0  # Should already be 0, but this acts as an absolute sink
                elif gtype == 'XOR' and others:
                    cost = co[node] + min(min(cc0.get(p, 1), cc1.get(p, 1)) for p in others) + 1
                else:
                    cost = co[node] + 1
                co[pred] = min(co[pred], cost)

        finite = [v for v in co.values() if v != float('inf')]
        cap = (max(finite) + 1) if finite else 1
        co = {k: (v if v != float('inf') else cap) for k, v in co.items()}

        self._scoap = (cc0, cc1, co)
        return cc0, cc1, co

    # -------------------------------------------------------- toggle rates
    def compute_toggle_rates(self, num_patterns=5000):
        """Simulate num_patterns random vectors, counting 0->1 and 1->0 flips per gate.

        Returns dict: gate_name -> {
            'toggle_rate': float 0.0-1.0,
            'transitions_01': int,
            'transitions_10': int,
            'total_transitions': int,
        }
        Low toggle rates (< 0.05) are prime Hardware Trojan indicators.
        """
        from pyatpg.core.simulator import Simulator
        try:
            sim = Simulator(self.circuit)
        except Exception:
            return {}

        input_list = sorted(self._as_set(getattr(self.circuit, 'inputs', [])))
        if not input_list:
            return {}

        # Track per-gate value history
        prev_values = {}
        transitions_01 = {}  # 0 -> 1
        transitions_10 = {}  # 1 -> 0
        valid_patterns = 0

        for _ in range(num_patterns):
            vec = tuple(random.randint(0, 1) for _ in input_list)
            try:
                # We need all internal gate values, not just outputs
                values = dict(zip(self.circuit.inputs, vec))
                unresolved = dict(self.circuit.gates)
                while unresolved:
                    progress = False
                    for name, g in list(unresolved.items()):
                        try:
                            ins = [values[i] for i in g.inputs]
                        except KeyError:
                            continue
                        gt = g.gtype
                        if gt == "AND":
                            values[name] = int(all(ins))
                        elif gt == "OR":
                            values[name] = int(any(ins))
                        elif gt == "NAND":
                            values[name] = int(not all(ins))
                        elif gt == "NOR":
                            values[name] = int(not any(ins))
                        elif gt == "NOT":
                            values[name] = int(not ins[0])
                        elif gt == "BUF":
                            values[name] = ins[0]
                        elif gt == "XOR":
                            values[name] = int(sum(ins) % 2)
                        else:
                            values[name] = 0
                        del unresolved[name]
                        progress = True
                    if not progress:
                        break
                    if len(unresolved) > 1000: # Safety break for huge circuits
                         break

                valid_patterns += 1

                # Count transitions
                if prev_values:
                    for gate_name in values:
                        curr = values[gate_name]
                        prev = prev_values.get(gate_name)
                        if prev is not None:
                            if prev == 0 and curr == 1:
                                transitions_01[gate_name] = transitions_01.get(gate_name, 0) + 1
                            elif prev == 1 and curr == 0:
                                transitions_10[gate_name] = transitions_10.get(gate_name, 0) + 1

                prev_values = dict(values)

            except Exception:
                continue

        if valid_patterns < 2:
            return {}

        # Build result dict
        max_possible = valid_patterns - 1  # transitions happen between consecutive patterns
        result = {}
        all_signals = set(list(transitions_01.keys()) + list(transitions_10.keys()))
        for gate in all_signals:
            t01 = transitions_01.get(gate, 0)
            t10 = transitions_10.get(gate, 0)
            total = t01 + t10
            rate = total / (2 * max_possible) if max_possible > 0 else 0.0
            result[gate] = {
                'toggle_rate': round(rate, 6),
                'transitions_01': t01,
                'transitions_10': t10,
                'total_transitions': total,
            }

        return result

    # -------------------------------------------------------- stats
    def get_stats(self):
        gate_types = {}
        node_counts = {'PI': 0, 'PO': 0, 'gate': 0, 'DFF': 0, 'wire': 0}
        for _, data in self.G.nodes(data=True):
            ntype = data.get('node_type', 'other')
            node_counts[ntype] = node_counts.get(ntype, 0) + 1
            gt = data.get('gate_type', '')
            if gt and gt not in ('INPUT', 'OUTPUT', 'WIRE'):
                gate_types[gt] = gate_types.get(gt, 0) + 1

        levels = self.compute_levels()
        max_level = max(levels.values()) if levels else 0

        fan_ins = [self.G.in_degree(n) for n in self.G.nodes]
        fan_outs = [self.G.out_degree(n) for n in self.G.nodes]

        return {
            'total_nodes': self.G.number_of_nodes(),
            'total_edges': self.G.number_of_edges(),
            'primary_inputs': node_counts.get('PI', 0),
            'primary_outputs': node_counts.get('PO', 0),
            'gates': node_counts.get('gate', 0) + node_counts.get('PO', 0),
            'flip_flops': node_counts.get('DFF', 0),
            'max_level': max_level,
            'gate_types': gate_types,
            'max_fan_in': max(fan_ins) if fan_ins else 0,
            'max_fan_out': max(fan_outs) if fan_outs else 0,
            'avg_fan_in': round(sum(fan_ins) / len(fan_ins), 2) if fan_ins else 0,
            'avg_fan_out': round(sum(fan_outs) / len(fan_outs), 2) if fan_outs else 0,
            'is_dag': nx.is_directed_acyclic_graph(self.G),
        }

    # ------------------------------------------------ per-gate features
    def get_gate_features(self):
        cc0, cc1, co = self.compute_scoap()
        levels = self.compute_levels()
        max_level = max(levels.values()) if levels else 1
        toggle_data = self.compute_toggle_rates(num_patterns=3000)
        
        # Advanced: Betweenness Centrality
        centrality = nx.betweenness_centrality(self.G, k=min(len(self.G), 100), normalized=True)

        features = {}
        for node, data in self.G.nodes(data=True):
            preds = list(self.G.predecessors(node))
            succs = list(self.G.successors(node))
            
            fan_in = len(preds)
            fan_out = len(succs)
            fan_ratio = fan_in / (fan_out + 1)

            neighbor_types = set()
            for n in preds + succs:
                gt = self.G.nodes[n].get('gate_type')
                if gt: neighbor_types.add(gt)

            t = toggle_data.get(node, {})
            features[node] = {
                'gate_type': data.get('gate_type', ''),
                'node_type': data.get('node_type', ''),
                'fan_in': fan_in,
                'fan_out': fan_out,
                'fan_ratio': fan_ratio,
                'centrality': centrality.get(node, 0),
                'level': levels.get(node, 0),
                'level_to_output': max_level - levels.get(node, 0),
                'cc0': cc0.get(node, 0),
                'cc1': cc1.get(node, 0),
                'co': co.get(node, 0),
                'toggle_rate': t.get('toggle_rate', 0.5),
                'neighbor_diversity': len(neighbor_types)
            }
        return features

    # ------------------------------------------- LLM feature profile
    def to_llm_feature_profiles(self):
        """Generate raw structural profiles for every gate."""
        features = self.get_gate_features()
        profiles = []
        
        for gate_id, f in features.items():
            profile = (
                f"Gate: {gate_id}, Type: {f['gate_type']}, Role: {f['node_type']}\n"
                f"  SCOAP Metrics: CC0={f['cc0']}, CC1={f['cc1']}, CO={f['co']}\n"
                f"  Dynamic Activity: Toggle Rate={f['toggle_rate']:.6f}\n"
                f"  Graph Topology: Fan-in={f['fan_in']}, Fan-out={f['fan_out']}, "
                f"Centrality={f['centrality']:.2e}, Level={f['level']}\n"
                f"  Structural: Fan-Ratio={f['fan_ratio']:.2f}, Neighborhood Diversity={f['neighbor_diversity']}\n"
            )
            profiles.append({
                'gate_id': gate_id, 
                'profile': profile, 
                'features': f
            })
        return profiles

    # ------------------------------------------- Cytoscape JSON export
    def to_cytoscape_json(self):
        profiles = self.to_llm_feature_profiles()
        profiles_map = {p['gate_id']: p for p in profiles}

        nodes = []
        for node, data in self.G.nodes(data=True):
            p = profiles_map.get(node, {})
            f = p.get('features', {})
            
            nodes.append({
                'data': {
                    'id': node,
                    'label': node,
                    'node_type': data.get('node_type', 'gate'),
                    'gate_type': data.get('gate_type', ''),
                    'role': data.get('node_type', 'Internal'), 
                    'fan_in': f.get('fan_in', 0),
                    'fan_out': f.get('fan_out', 0),
                    'fan_ratio': f.get('fan_ratio', 0.0),
                    'centrality': f.get('centrality', 0.0),
                    'cc0': f.get('cc0', 0),
                    'cc1': f.get('cc1', 0),
                    'co': f.get('co', 0),
                    'toggle_rate': f.get('toggle_rate', 0.5),
                    'suspicion_score': 0.0,
                }
            })

        edges = []
        for i, (src, tgt) in enumerate(self.G.edges):
            edges.append({
                'data': {
                    'id': f'e{i}',
                    'source': src,
                    'target': tgt,
                }
            })

        return {'nodes': nodes, 'edges': edges}

    def generate_circuit_summary_for_llm(self):
        """Creates a narrative summary highlighting critical testability and security areas."""
        stats = self.get_stats()
        summary = (
            f"Hardware Security Audit Context: {self.circuit.name if hasattr(self.circuit, 'name') else 'Netlist'}\n"
            f"--------------------------------------------------\n"
            f"Netlist Profile: {stats['total_nodes']} nodes, {stats['max_level']} logic levels.\n"
            f"Gate Distribution: {stats['gate_types']}\n"
            f"Connectivity Signature: Avg Fan-in {stats['avg_fan_in']}, Avg Fan-out {stats['avg_fan_out']}.\n\n"
            
            f"YOUR TASK: You are the Prioritizer. Analyze these structural and dynamic features "
            f"using few-shot reasoning from your knowledge of Hardware Trojans (like those in Trust-Hub). "
            f"Identify gates that exhibit 'Stealth' through low observability or anomalous topology. "
            f"Your output targets the Atalanta ATPG tool for high-effort pattern generation."
        )
        return summary
