# Fairy Crazyhouse comparator adapter

This directory contains the Windows-only protocol supervisor used to qualify the exact external Fairy-Stockfish comparator for the local same-network gate. Fairy-Stockfish is not product ancestry, and this adapter is not linked into the Crazyhouse-Stockfish engine.

The v1 binary accepts only the Fairy executable and legacy network identities frozen in `tests/crazyhouse/p6-fairy-comparator-adapter-v1.json` and its addenda. It owns the child in a kill-on-close Job Object, performs a main-thread NNUE probe before exposing `uciok`, implements the referee capability handshake, and gates every non-perft search until the exact NNUE marker appears. A rejection terminates only the owned process tree and exits nonzero.

## Reproducible Windows build

From the repository root with the pinned MSYS2 MinGW64 toolchain on `PATH`:

```powershell
& C:\msys64\mingw64\bin\g++.exe `
  -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror `
  -static -static-libgcc -static-libstdc++ `
  -Wl,--no-insert-timestamp `
  '-ffile-prefix-map=<absolute-repository-root>=.' `
  tools\comparator_adapter\fairy_crazyhouse_adapter.cpp `
  -ladvapi32 -o fairy-crazyhouse-adapter.exe
```

Formal qualification performs this build twice in clean directories and requires byte-identical executables.

## Invocation

```text
fairy-crazyhouse-adapter.exe --engine <pinned-stockfish.exe> --network <approved-network.nnue>
```

The adapter speaks UCI on standard input and output. `--version` prints the adapter version plus its frozen engine and network SHA-256 values. The exact engine, network, profile, option transformations, fixed-node corpus, negative cases, and lifecycle requirements are normative in the frozen fixture; this README does not replace them.

Passing the adapter tests proves only comparator plumbing and functional transparency. It is not timing, Elo, OpenBench, champion, packaging, or release evidence.
