#!/usr/bin/env bash
# Render build command. Compiling atalanta from source here (rather than
# shipping a prebuilt binary) avoids shipping a binary linked against a
# glibc newer than some runtime image has. Statically linking on top of
# that means it doesn't matter whether Render's build container and
# runtime container even agree on a glibc version -- the binary carries
# its own libc and doesn't dynamically resolve it at all.
set -e

pip install -r requirements.txt

cd Atalanta
rm -f atalanta *.o
make atalanta CFLAGS="-O -g -static"

echo "--- atalanta binary check ---"
file atalanta
if ldd atalanta 2>&1 | grep -qi "not a dynamic executable"; then
    echo "Confirmed: atalanta is statically linked."
else
    echo "FATAL: atalanta is NOT statically linked (static link silently fell back to dynamic)."
    ldd atalanta
    exit 1
fi
echo "-----------------------------"

cd ..
