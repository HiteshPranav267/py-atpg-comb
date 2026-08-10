#!/usr/bin/env bash
# Render build command.
#
# Atalanta is compiled from the C++ sources in Atalanta/ rather than shipped
# as a prebuilt binary: a binary built elsewhere carries that machine's glibc
# requirement, which is how this deployment previously failed at runtime with
# "GLIBC_2.38 not found". Linking statically on top of that removes the
# dependency on the runtime image's glibc entirely.
set -euo pipefail

pip install -r requirements.txt

cd Atalanta
rm -f atalanta ./*.o
make atalanta CFLAGS="-O -g -static"

echo "--- Atalanta build verification ---"
file atalanta

# -static can silently fall back to dynamic linking when a static libc is
# unavailable, which would reintroduce the glibc coupling. Fail the build
# rather than discover it on the first request.
if ldd atalanta 2>&1 | grep -qi "not a dynamic executable"; then
    echo "OK: statically linked."
else
    echo "FATAL: atalanta is not statically linked; it would depend on the runtime image's glibc."
    ldd atalanta || true
    exit 1
fi

# Prove the binary actually executes here and produces a real ATPG summary.
# A build that compiles but cannot run is worse than a failed build, because
# it only surfaces once a user submits a circuit.
smoke_dir="$(mktemp -d)"
trap 'rm -rf "$smoke_dir"' EXIT
cp ../benchmarks/c17.bench "$smoke_dir/smoke.bench"
if ! ./atalanta -t "$smoke_dir/smoke.vec" "$smoke_dir/smoke.bench" > "$smoke_dir/smoke.log" 2>"$smoke_dir/smoke.err"; then
    echo "FATAL: Atalanta exited non-zero on the smoke circuit."
    cat "$smoke_dir/smoke.err"
    exit 1
fi
# Atalanta exits 0 even on a fatal netlist error, so check for the summary it
# only prints after generating patterns.
if ! grep -q "SUMMARY OF TEST PATTERN GENERATION RESULTS" "$smoke_dir/smoke.log"; then
    echo "FATAL: Atalanta ran but produced no results summary."
    cat "$smoke_dir/smoke.err"
    exit 1
fi
grep "Fault coverage" "$smoke_dir/smoke.log"
echo "OK: smoke test generated patterns."
echo "-----------------------------------"

cd ..
