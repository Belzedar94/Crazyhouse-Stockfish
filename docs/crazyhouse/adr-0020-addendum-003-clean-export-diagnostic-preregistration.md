# ADR 0020 addendum 003: clean-export correction diagnostic

- Status: preregistered, not executed
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Implementation parent: `3f2fe5e399ed1b4f52d222272f42102a477320a2`
- Reserved lease: 353

The diagnostic changes only the synthetic fixture Git authority. The tested
harness and verifier come from one clean export. The harness receives an
explicit local absolute worktree path, and it must authenticate the frozen
official Stockfish commit as an ancestor of P7 and P7 as an ancestor of that
worktree's exact HEAD before cloning it. Network fallback is forbidden.

One Python 3.12.0 normal profile must complete all three positives and all 25
unchanged negatives and emit the exact frozen summary. Scratch must restore
empty; only the three admitted Make parse files may appear; no compiler,
linker, engine or bytecode artifact is allowed.

A pass closes only CH-316 and permits a new formal preregistration. It does not
qualify the tooling formally or grant runtime, package, strength, OpenBench,
draft, tag, G15, G16 or release credit.
