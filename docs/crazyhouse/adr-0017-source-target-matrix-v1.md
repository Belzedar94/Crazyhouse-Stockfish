# ADR 0017: initial Crazyhouse source target matrix

- Status: accepted source-only decision
- Date: 2026-08-24
- Evidence class: `R4_RELEASE`
- Decision parent: `71c645b842df76437c24bacec787cba27bc9d271`
- Product `src` tree at decision: `15e5245b0910bbb5ffa79b3bb67943b8bff24803`

## Context

The official-Stockfish Makefile exposes many architecture labels. That list is
not a release support statement for Crazyhouse. A target enters this matrix only
when its evaluator layout, option routing, deterministic build mechanism and
runtime prerequisites are all expressible by Crazyhouse-owned evidence.

The current Windows engineering line has a pinned native MinGW GCC toolchain,
an admitted SSE2/AVX2 legacy-evaluator design boundary, a real AVX2 product
runtime, and a passing source-visible deterministic PE-link mode. It does not
have Crazyhouse-specific release evidence for BMI2/PEXT, AVX-512/VNNI, Linux,
macOS, ARM, WebAssembly, 32-bit or universal dispatch.

Atomic and Horde release layouts were inspected for method only. Their target
counts, ISA choices, build images, runners, networks, test results and package
names are not inherited.

## Decision

Freeze two prospective native Windows source targets:

| Target ID | Make `ARCH` | Minimum CPU contract | Product backend |
| --- | --- | --- | --- |
| `windows-x86-64` | `x86-64` | x86-64 and SSE2 | unchanged default scalar Crazyhouse legacy backend |
| `windows-x86-64-avx2` | `x86-64-avx2` | AVX2, BMI1, POPCNT, SSE4.1, SSSE3 and SSE2 | unchanged default scalar Crazyhouse legacy backend |

Both targets use native MinGW GCC with `COMP=gcc`, one build thread,
`EXTRACXXFLAGS=-Werror` and the opt-in `mingw_reproducible=yes` mode. Neither
recipe sets `CRAZYHOUSE_LEGACY_BACKEND`; a release-build decision cannot change
the production evaluator backend indirectly.

The intended Windows archive patterns are
`crazyhouse-stockfish-${VERSION}-windows-x86-64.zip` and
`crazyhouse-stockfish-${VERSION}-windows-x86-64-avx2.zip`. These are frozen
patterns, not existing assets. The exact version, candidate commit, executable
bytes and archive hashes remain unset until the independent strength panel and
exact-candidate R4 build gates pass.

The registered legacy network remains 58,534,811 bytes with SHA-256
`8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.
Lila blob `ad269c33db13ecae295ec66ee9f438462498c623` and its pinned directory README
blob `c94bf53d0cd54599d899a51f0aa4c1e01e4f0b94` establish the asset-specific CC0
declaration. `Crazyhouse_v1.nnue` is permitted only as a byte-identical
distribution alias. This matrix does not choose between external delivery and
an exact-byte embedded release build, and it does not alter the source default.

## Explicit exclusions

- `x86-64-bmi2` and every PEXT lane: no Crazyhouse-specific target fixture,
  runtime qualification or exact-candidate panel exists.
- AVX-VNNI, AVX-512 and VNNI-512: Makefile support and inherited accumulator
  operations do not establish independent evaluator or runtime credit.
- Linux x86-64/AVX2: no pinned Crazyhouse release toolchain, two-export full
  build, package reproduction or runtime receipt exists.
- macOS, Apple Silicon, ARM, RISC-V, PowerPC, WebAssembly, universal and 32-bit:
  no Crazyhouse target-specific evaluator, packaging and runtime evidence exists.

Adding any excluded target requires an additive ADR and a result-blind fixture
before its first build result is observed. Removal is also additive; published
assets and tags are never silently redefined.

## Exact-candidate admission still required

For each target, two isolated clean exports must produce byte-identical full
executables and final archives under a digest-pinned toolchain and deterministic
build epoch. Each executable must then pass its own PE/ISA inspection, UCI
identity and option inventory, Crazyhouse capability handshake, positive legacy
network load, missing/wrong/corrupt/incompatible negatives, correctness corpus,
special-state searches, deterministic bench/digest, runtime smoke and package
path test. Target telemetry is compared only within the same target.

The final packages must include per-target provenance, inventory/SBOM, GPL
corresponding source, licenses and network attribution, plus a global manifest
and `SHA256SUMS`. The complete draft must be downloaded and independently
reauthenticated before G15.

## Boundary

This ADR freezes source target names, feature floors, recipes and exclusions.
It does not build an engine, prove a full executable/archive reproducible,
select a candidate, grant strength credit, authorize OpenBench, create assets,
open a draft, authorize G15, create a tag or publish a release.
