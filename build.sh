#!/usr/bin/env bash
# Render build command. Compiling atalanta from source here (rather than
# shipping a prebuilt binary) guarantees it links against whatever glibc
# the build/runtime image actually has, instead of drifting out of sync
# with it (see: GLIBC_2.38 not found on the runtime image).
set -e

pip install -r requirements.txt

cd Atalanta
rm -f atalanta *.o
make atalanta
cd ..
