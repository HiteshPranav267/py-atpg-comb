#!/bin/bash
echo "[PyATPG] Starting Atalanta Job for c17"
chmod +x ./atalanta
# First generate a baseline
./atalanta -t c17_baseline.ufaults c17.bench > baseline_atalanta.log

# Now run targeted faults
./atalanta -f c17.faults -t c17.ufaults c17.bench > targeted_atalanta.log

echo "[PyATPG] Done."
