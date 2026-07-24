#!/bin/bash
echo "[PyATPG] Starting Atalanta Job for circuit"
chmod +x ./atalanta
# First generate a baseline
./atalanta -t circuit_baseline.ufaults circuit.bench > baseline_atalanta.log

# Now run targeted faults
./atalanta -f circuit.faults -t circuit.ufaults circuit.bench > targeted_atalanta.log

echo "[PyATPG] Done."
