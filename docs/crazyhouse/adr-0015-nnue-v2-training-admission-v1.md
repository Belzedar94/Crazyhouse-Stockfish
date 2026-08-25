# ADR 0015: Crazyhouse NNUE V2 physical training admission

- Status: accepted and frozen before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Base commit: `6ad4f16b16d43f05ac9ef0accd066da49e9a6c8e`
- Base tree: `ae89aeaa6b2b64a37acf6a6af0ca01048bcc0023`
- Base `src` tree: `0a191443efb389fdb914861bfa8faded6c2ae691`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`
- External advisory review: explicitly waived by the owner; no API, credits or fallback is used

## Context

The productive V2 scalar, trainer kernel, SSE2 transformer, transactional
incremental accumulator, sanitizers and authenticated legacy control have
passed their bounded engineering gates. The current trainer deliberately
consumes only 42 schema goldens and synthetic identity-derived targets. That
micro-fit proves deterministic mechanics, not data or training admission.

Crazyhouse physical records already preserve board state, pockets, promoted
provenance, rights, effective en-passant state, counters, history, move,
result, terminal reason, trajectory identity and full producer provenance.
They remain the canonical source. Persisted NNUE feature rows are not accepted
as source data.

Atomic V3 commit `2d50867516a87a7431794b6b1f112711d578b9d1`
was inspected read-only for its evidence method: role-separated manifests,
label-free trajectory partitioning, exact full-scan set intersections and a
separate semantic replay gate. No Atomic feature, threshold, seed, topology,
record, result or campaign value is inherited. Official nnue-pytorch commit
`b8512291deb4cd18afa67003bb6bc53dd522cbf0` was inspected only to confirm the
methodological separation between raw score/result transport and a
run-specific loss configuration.

## Decision

Freeze `schemas/crazyhouse-nnue-v2-training-admission-v1.json` as the dataset
admission contract. A training dataset is an authenticated manifest over
complete `crazyhouse-physical-v1` chunk bundles and immutable per-chunk and
aggregate receipts. Train and validation are distinct roles; the playing
panel remains external and cannot be selected by training loss.

Partitioning is by complete trajectory using a domain-separated hash whose
seed and threshold must be frozen before generation. Labels do not participate
in the split. A trajectory split alone is insufficient: after generation, an
exact full scan must also show zero train/validation intersection for raw
records, physical position identities, V2 model-input identities, game IDs and
trajectory IDs. If a validation trajectory shares a position or model input
with train, the complete validation trajectory is quarantined and replacement
data is generated under the unchanged config. Records are never silently
moved after results are visible and partial trajectories are never retained.

The loader transports raw result and teacher fields with their declared
perspective. It does not hardcode a teacher/result blend, mate mapping,
score-to-probability scale or loss. Every production training run must freeze
those values, the optimizer, schedule, budgets, RNG, sample order,
augmentation, precision, validation cadence and stopping rule before metrics
exist. This keeps dataset admission separate from model selection and prevents
training loss from becoming a production decision.

## Verification sequence

1. Prove the dedicated admission target is absent before implementation.
2. Implement a fail-closed streaming loader and a separately implemented
   verifier that imports neither the producer codec nor the loader.
3. Exercise framing, provenance, raw-label perspectives, feature projection,
   deterministic partitioning, all cross-role identity sets, transactional
   output and adversarial mutations in explicit fixture mode.
4. Preserve fixture mode as non-admissible. A real PASS requires an authorized
   production chunk set, exact aggregate receipt, structural full scan and
   engine-backed replay.
5. Only then preregister and run a deterministic production training replay.

## Boundary

This ADR does not authorize OpenBench, workers, CPU/GPU allocation or a
production campaign. It does not admit the existing four-record self-play G0
bundle or the 42 schema goldens for training. It grants no model-selection,
timing, Elo, Fairy-Stockfish, release or monitoring credit. Legacy V1 remains
the productive default, and G12 remains open until real data and deterministic
production training pass their separate receipts.
