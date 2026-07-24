import time
import random

from fault_simulator import FaultSimulator


class HybridATPG:
    def __init__(self, podem, brute):
        self.podem = podem
        self.brute = brute

    def generate(self, faults, status_interval=25, callback=None,
                 random_patterns=None):
        circuit = self.podem.circuit
        fault_sim = FaultSimulator(circuit)
        n_inputs = len(circuit.inputs)

        # Scale random patterns with circuit size
        if random_patterns is None:
            random_patterns = max(2000, 64 * n_inputs)
        patterns = []
        detected = set()
        redundant = set()
        remaining_faults = list(faults)

        start_time = time.time()
        total = len(faults)

        # -------------------------------------------------------
        # PHASE 1: Random pattern generation with fault dropping
        # -------------------------------------------------------
        print(f"[ATPG] Phase 1: Random patterns ({random_patterns} vectors, "
              f"{n_inputs} inputs)")

        for i in range(random_patterns):
            if not remaining_faults:
                break
            vec = tuple(random.randint(0, 1) for _ in range(n_inputs))
            newly = fault_sim.simulate_faults_bulk(vec, remaining_faults)
            if newly:
                patterns.append(vec)
                detected |= newly
                # Fault dropping: remove detected faults
                remaining_faults = [f for f in remaining_faults
                                    if str(f) not in detected]

            if (i + 1) % 500 == 0:
                elapsed = time.time() - start_time
                print(f"[ATPG]   Random {i+1}/{random_patterns} | "
                      f"Detected: {len(detected)}/{total} | "
                      f"Time: {elapsed:.1f}s")

        elapsed = time.time() - start_time
        print(f"[ATPG] Phase 1 done: {len(detected)}/{total} faults detected "
              f"({100*len(detected)/total:.1f}%) in {elapsed:.1f}s")

        # -------------------------------------------------------
        # PHASE 2: Deterministic PODEM for remaining faults
        # -------------------------------------------------------
        print(f"[ATPG] Phase 2: PODEM for {len(remaining_faults)} remaining faults")

        phase2_count = len(remaining_faults)
        podem_failed = []
        for idx, fault in enumerate(list(remaining_faults), 1):
            if str(fault) in detected:
                continue

            vec = self.podem.generate_test(fault)

            if vec:
                patterns.append(vec)
                # Fault drop: check what else this pattern detects
                newly = fault_sim.simulate_faults_bulk(vec, remaining_faults)
                detected |= newly
                detected.add(str(fault))
                remaining_faults = [f for f in remaining_faults
                                    if str(f) not in detected]
            else:
                podem_failed.append(fault)

            if idx % status_interval == 0 or idx == phase2_count:
                elapsed = time.time() - start_time
                progress = {
                    'current': total - len(remaining_faults),
                    'total': total,
                    'detected': len(detected),
                    'redundant': len(podem_failed),
                    'elapsed': round(elapsed, 1),
                    'percent': round(((total - len(remaining_faults)) / total) * 100, 1)
                }

                print(
                    f"[ATPG]   PODEM {idx}/{phase2_count} | "
                    f"Detected: {len(detected)}/{total} | "
                    f"Redundant: {len(podem_failed)} | "
                    f"Time: {elapsed:.1f}s"
                )

                if callback:
                    callback(progress)

        # -------------------------------------------------------
        # PHASE 3: Targeted random for faults PODEM couldn't solve
        # -------------------------------------------------------
        still_undetected = [f for f in podem_failed if str(f) not in detected]
        if still_undetected:
            targeted_count = max(5000, 200 * n_inputs)
            print(f"[ATPG] Phase 3: Targeted random for {len(still_undetected)} "
                  f"hard faults ({targeted_count} patterns each)")

            for fault in list(still_undetected):
                if str(fault) in detected:
                    continue
                found = False
                for _ in range(targeted_count):
                    vec = tuple(random.randint(0, 1) for _ in range(n_inputs))
                    if fault_sim.detects(vec, fault):
                        patterns.append(vec)
                        newly = fault_sim.simulate_faults_bulk(vec, still_undetected)
                        detected |= newly
                        detected.add(str(fault))
                        still_undetected = [f for f in still_undetected
                                            if str(f) not in detected]
                        found = True
                        break
                if not found:
                    redundant.add(str(fault))

            elapsed = time.time() - start_time
            print(f"[ATPG] Phase 3 done: {len(detected)}/{total} detected, "
                  f"{len(redundant)} redundant | {elapsed:.1f}s")
        else:
            redundant = set(str(f) for f in podem_failed)

        elapsed = time.time() - start_time
        coverage = 100 * len(detected) / total if total else 0
        print(f"[ATPG] Done: {len(detected)}/{total} ({coverage:.1f}%) | "
              f"{len(patterns)} patterns | {elapsed:.1f}s")

        return patterns, detected, redundant
