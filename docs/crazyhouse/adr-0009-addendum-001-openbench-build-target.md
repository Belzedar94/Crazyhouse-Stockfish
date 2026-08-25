# ADR-0009 Addendum 001: OpenBench worker build target

- Status: Accepted before target enforcement and clean exports
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Parent ADR: `adr-0009-openbench-onboarding-v1.md`, 6,062 bytes, SHA-256 `b9c3bc514c74179b4dd7cd0d02af822270ae5b93e1da4f3d694c8521294bc29d`
- Source before this decision: commit `09b2e8bdb67853bfcdea3ec82ebc6f78a369bd64`, tree `4bcbddfd0c78a1e56605c48a41959437a75951d9`
- Oracle: waived by the owner

## Discovered worker contract

The pinned production OpenBench client at commit
`e20f0d9432f88fed1706d83fc93469be1a2a2cec` constructs a public build as a
bare `make -j` invocation. It forwards the requested executable name, the full
source commit, the selected `g++` compiler and the absolute authenticated
network path. It does not forward a Make target, `ARCH` or `COMP`. The inspected
`Client/utils.py` is 20,066 bytes with SHA-256
`b0547739fecfca5db285f11ffe33048dfb92dbc8a894b4ec125179df6db5a470`.

Leaving the Stockfish default architecture unresolved would select `native`
independently on every worker. That would make the effective target dependent
on host capabilities and would not implement the preregistered Crazyhouse
`windows-x86-64` and `linux-x86-64` scope.

## Decision

The Crazyhouse OpenBench play shim shall convert the bare worker invocation to
this explicit internal matrix:

| Worker system | `ARCH` | `COMP` | Legacy evaluator | Required CPU baseline |
| --- | --- | --- | --- | --- |
| Windows | `x86-64` | `mingw` | `incremental-scalar` | x86-64/SSE2 |
| Linux | `x86-64` | `gcc` | `incremental-scalar` | x86-64/SSE2 |

The recursive play build uses the normal optimized non-PGO `all` target with
`OPENBENCH_PLAY_BUILD=1`, `CRAZYHOUSE_LEGACY_BACKEND=scalar`, the worker's
requested `EXE` and compiler, and the exact full source identity. The separate
DATAGEN role remains governed by its producer target and cannot emit the play
artifact.

`x86-64` is a Crazyhouse selection, not an inherited Horde or Atomic value. It
matches the current product build profile and the exact local-strength
candidate class. The accepted SIMD ADR explicitly keeps scalar as production
dispatch until a later speed and routing decision. Its 4,813-byte source has
SHA-256 `798f413c961bd27ce8f50735481151aa510589b0ad2b241745522e7ee9b7ac52`.
The frozen P7 contract is 15,979 bytes with SHA-256
`c82f08981b09494aa09ccdc6ed8ce7ca56c26cd7b659a9fb0bcccb9ced46b44e`
and authenticates the candidate as `incremental-scalar`.

Atomic-Stockfish commit `95a5257c90835af81a2ff751bb738015250a262f`
was inspected only for the method of translating a bare worker invocation into
an explicit internal target. No Atomic target, network, bench, rule, option,
time control or result is inherited. The inspected Makefile is 61,547 bytes
with SHA-256 `9eb0b1b6bb505d4bc3483081a94d08b1a222d2bc873d6ff2172d6ce69892a314`.

## Acceptance and claim boundary

Source tests must prove the exact platform mapping, architecture and scalar
backend. The original network path with spaces must pass the build contract.
Two independent clean exports of one committed source identity must each pass
UCI inventory, challenged route acknowledgement, exact embedded-network
identity, missing-override rejection and two deterministic corrected-corpus
benches. Only then may the node signature be frozen by another hash-pinned
addendum and a commit message containing `Bench: <nodes>`.

This decision is build provenance only. It does not authorize a public
repository, an official OpenBench canary, worker resources, strength, model
selection or release.
