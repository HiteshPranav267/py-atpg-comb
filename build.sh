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

# The pip Yosys (used to synthesize RTL uploads) is WebAssembly that is
# compiled to machine code on first use -- slow and memory-hungry. Do it here,
# into the project directory pyatpg/core/rtl.py reads it from, so the running
# server never has to (and never trips its own Yosys memory guard doing so).
YOWASP_CACHE_DIR="$PWD/.yowasp-cache" python -c \
    "import sys, yowasp_yosys; sys.exit(yowasp_yosys.run_yosys(['-V']))"

# Atalanta (Virginia Tech; teaching and research use only, see its README) is
# not part of this repository. It is fetched from its public repository at a
# pinned commit, and patches/atalanta.patch applies our changes: CPU time from
# getrusage (times() truncated short runs to 0) and debug prints only under
# -DDEBUG. An existing Atalanta/ (a local checkout) is built as it is.
ATALANTA_REPO=https://github.com/hsluoyz/Atalanta.git
ATALANTA_COMMIT=a8e07fe4af80c55b0d4ca77e382731b03ad731dc
if [ ! -d Atalanta ]; then
    git clone --quiet "$ATALANTA_REPO" Atalanta
    git -C Atalanta checkout --quiet "$ATALANTA_COMMIT"
    git -C Atalanta apply "$PWD/patches/atalanta.patch"
fi

cd Atalanta
rm -f atalanta ./*.o
make atalanta CFLAGS="-O -g -static"

echo "--- Atalanta build verification ---"
file atalanta || true

# -static can silently fall back to dynamic linking when a static libc is
# unavailable, which would reintroduce the glibc coupling. Fail the build
# rather than discover it on the first request.
#
# ldd exits non-zero precisely when the binary is NOT dynamic, which is the
# outcome we want, so its status is captured rather than piped: under
# `set -o pipefail` a successful static build would otherwise abort here.
ldd_output="$(ldd atalanta 2>&1 || true)"
if printf '%s' "$ldd_output" | grep -qi "not a dynamic executable"; then
    echo "OK: statically linked."
else
    echo "FATAL: atalanta is not statically linked; it would depend on the runtime image's glibc."
    printf '%s\n' "$ldd_output"
    exit 1
fi

# Prove the binary actually executes here and produces a real ATPG summary.
# A build that compiles but cannot run is worse than a failed build, because
# it only surfaces once a user submits a circuit.
smoke_dir="$(mktemp -d)"
trap 'rm -rf "$smoke_dir"' EXIT
# ISCAS-85 c17, inline: benchmarks/ is not part of the repository.
cat > "$smoke_dir/smoke.bench" <<'BENCH'
# c17
# 5 inputs
# 2 outputs
# 0 inverter
# 6 gates ( 6 NANDs )

INPUT(1)
INPUT(2)
INPUT(3)
INPUT(6)
INPUT(7)

OUTPUT(22)
OUTPUT(23)

10 = NAND(1, 3)
11 = NAND(3, 6)
16 = NAND(2, 11)
19 = NAND(11, 7)
22 = NAND(10, 16)
23 = NAND(16, 19)
BENCH
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
