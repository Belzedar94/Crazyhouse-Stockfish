# ADR 0016: explicit MinGW release reproducibility mode

- Status: accepted for local implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Base commit: `827acc5f4e21b49b0937cdb8838641f25b6a3532`
- Base tree: `fab809f1b1a162a423b0edbe8adb25c35c1adc75`
- Base `src` tree: `36fa1995e984899db1b42fcce3e18c6b419fc49e`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`

## Context

Crazyhouse's current official-Stockfish line has no source-visible switch that
forces deterministic PE timestamps. Its admitted Windows AVX2 gate executable
has COFF timestamp `1787474874`, and current formal product executables also
carry wall-clock link timestamps. An executable can therefore be functionally
correct while remaining unsuitable for the two-export R4 byte-reproducibility
gate.

GNU ld 2.45 in the pinned local MinGW toolchain exposes
`--no-insert-timestamp`. The current Makefile already centralizes Windows
linker flags behind `target_windows=yes`, including native MSYS2 GCC builds
that use `COMP=gcc` rather than the cross-compiler label `COMP=mingw`.

A historical implementation of a similarly named switch exists only on a
retired Fairy-derived branch. Commit
`f44bd6add3a6a95ca702c92b6f14e1b8a46c6858` is not an ancestor of this
product and will not be cherry-picked. It is incident-history evidence only;
the current implementation is derived independently from the present
official-Stockfish Makefile and linker behavior.

## Decision

Add `mingw_reproducible=yes|no`, defaulting to `no`. When and only when it is
`yes`, the target must be Windows and the final linker flags must contain
exactly one `-Wl,--no-insert-timestamp`. Invalid values and non-Windows use
fail before compilation. `config-sanity` must display and validate the mode.

A dedicated non-product probe target will expose the effective target, mode
and linker-flag count without building the engine. The independent verifier
will also compile one tiny object and link it twice through the pinned compiler
with the effective reproducible flag. Both PE timestamps must be zero and both
executables byte-identical. Disabled-mode controls must prove that the flag is
absent; invalid and non-Windows controls must fail closed.

This switch is opt-in so ordinary development and the frozen P7 candidate do
not change identity. Every eventual Windows release target must explicitly
enable it. Full engine/archive reproducibility remains a per-target R4 gate on
the exact release candidate; the tiny-link result cannot substitute for it.

## Admission sequence

1. Commit the frozen contract before implementation.
2. Observe the dedicated target absent in a clean export.
3. Implement only the Makefile mode, probe and independent verifier.
4. Run normal and optimized Python verification from two clean Git exports.
5. Require byte-identical normalized results and exact negative diagnostics.
6. Record the result additively; never repair a failed formal namespace.

## Boundary

This decision creates no target matrix, release candidate, champion, network
alias, archive, SBOM, tag, draft, GitHub write, OpenBench call or stable-release
authorization. It is release-build plumbing only. Legacy V1 remains default,
and the frozen P7 candidate and comparator identities remain unchanged.
