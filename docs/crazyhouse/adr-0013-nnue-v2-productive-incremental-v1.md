# ADR 0013: Crazyhouse NNUE V2 productive transactional accumulator

- Status: accepted and preregistered before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Base commit: `30df1d4db2ff4987c80ebcea852f3d1ef66d4268`
- Base tree: `7086ed00bc24fc9de5460bef2642ab078a5a2cd1`
- Base `src` tree: `d3c328edfac440fbb2ec125e1a3fec85e2cef7c4`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`
- External advisory review: explicitly waived by the owner; no API, credits, fallback, or alternate model is used

## Context

The productive scalar evaluator and deterministic engineering trainer passed
their formal replay in lease 311. The productive SSE2 transformer then passed
exact scalar parity in lease 315. The earlier 17-lane incremental probe remains
test-only under CH-265 and cannot prove incremental correctness for the actual
512-lane productive transformer or its dense topology.

The frozen Crazyhouse feature inventory is the authority for this rung. It
represents board occupancy, pocket ownership and count slots, and
promoted-origin provenance for both perspectives. Therefore an incremental
transition is defined as the exact set difference between complete validated
source and target inventories, not as an orthodox move-type shortcut.

Atomic-Stockfish commit `13a1cf845c51eee3507ed6baa080895014de9e8b`
and Horde-Stockfish commit `83521c3b9ff2c9e195b8fe75c3b8ec4917bd0e02`
were inspected read-only. Only their method of comparing committed incremental
state with a fresh evaluation after make, undo, null, and failure paths is
admitted. No foreign rule, feature, topology, width, network, fixture, bound,
counter, or performance value is inherited.

## Decision

Add one network-bound productive accumulator that owns exact feature
membership, active counts, and the two 512-lane int32 transformer states. A
refresh validates both perspectives and commits a candidate only after all
transformer lanes are proven in range. An update first authenticates that the
committed state exactly matches the supplied source inventory, then applies
all removals and additions in signed int64 working lanes and commits only after
the complete target is valid and every final lane fits int32.

The accumulator exposes evaluation only after authenticating the supplied
current inventory. It passes the stored side-to-move and opponent transformer
states through the existing productive dense path; topology, activation,
quantization, container bytes, provenance identities, and output units remain
unchanged. Every failed refresh, update, or evaluation is fail-closed and
leaves committed membership and lanes unchanged.

This rung deliberately uses complete source/target inventories. It avoids
assuming that orthodox `DirtyPiece` fields encode Crazyhouse pocket and
promoted-origin transitions. A later engine-stack integration may optimize
how authenticated deltas are transported, but it must reproduce this
inventory-difference oracle exactly.

## Frozen transition and negative matrix

The thirteen already frozen Crazyhouse transition walks are consumed by exact
hash, not copied from another variant. They cover quiet and king moves,
ordinary and promoted-origin captures into pockets, white and black drops,
successive pocket consumption, en-passant, promotion, promoted-marker motion,
castling, capture/drop chaining, all reverse undos, and null/null-undo.

Across 49 position checkpoints and both side-to-move orders, the full
productive trace has 2,178 values. The matrix therefore compares 213,444
values for scalar versus productive SSE2 and another 213,444 values for
incremental versus scalar. It also requires exact FEN completion and root
restoration, 13 refreshes, 36 source-to-target updates, deterministic replay,
and exact accumulator membership at every checkpoint.

Ten operation failures cover uninitialized state, network mismatch, stale
source, invalid source and target status, over-capacity target, invalid index,
duplicate feature, not-ready network, and failed refresh of a committed
accumulator. Four evaluation failures cover uninitialized state, invalid
side-to-move, stale inventory, and invalid inventory. Each negative snapshots
and reauthenticates all committed state after rejection.

## Admission and boundaries

Admission requires an expected-red observation of the absent dedicated Make
target before implementation, followed by warning-clean release and
debug-assertion builds from two independent clean exports. The new verifier,
the complete productive SIMD verifier, the scalar/container/trainer verifier,
and the normal legacy engine control must all pass. Cross-export/profile
results, the engineering checkpoint, and the quantized network must remain
byte-identical.

This decision changes only productive incremental transformer state. It does
not change the SSE2 backend, dense path, trainer, physical schema, feature
contract, architecture identity, quantization identity, network bytes, normal
engine routing, or legacy default. It does not admit production data, select a
model, prove speed or Elo, authorize OpenBench, close G12, or support release.
