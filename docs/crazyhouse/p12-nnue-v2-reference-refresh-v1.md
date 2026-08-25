# P12 NNUE V2 reference refresh

- Date: 2026-08-24
- Status: read-only refresh completed with a declared vault gap
- Evidence class: `D0_DISCOVERY`
- Crazyhouse rule or feature authority supplied by references: none

## Fairy-Vault

The local checkout at `fairy-vault` was inspected under its read-only search
instructions. Its checked-out identity was commit
`ccb72d7656fdd43c4c2538f6c425295bd9f8bc43`, tree
`27bd04ef63138a948208c8a0b8c0d4b0e956a260`. The checkout already contained
uncommitted and untracked work, so no file was modified. The searched database
snapshot was 126,976 bytes with SHA-256
`d4e4bd18fd2bff4ec2b185c403540940122d0fab466850c740cb48bec8d97cdc`.

Read-only full-text searches for `Crazyhouse`, `pockets` and `NNUE` each
returned zero hits. No embedding provider, API credit or network fallback was
used. Fairy-Vault therefore supplies no P12 authority or implementation fact;
this absence is an explicit research gap.

## Atomic-Stockfish

- Checkout commit: `13a1cf845c51eee3507ed6baa080895014de9e8b`
- Tree: `b1469059f3686f45a80ba5ceadce2d7ddb7a0bfe`
- Checked-out branch: `agent/hito-07-data-generator-core`
- `docs/atomic/hito7-validation.md` blob: `7aa2d8e797e3b9c82a37ed2bda1ea91bbc8dbbd9`
- `src/data/legacy_atomic_v1.cpp` blob: `fe64bb5527e2ebd8a81a38df3041f3d6ac3c91eb`

Admitted method only: version the physical wire, keep generator and playing
roles separate, reject unsupported records before partial output, and prove
decoder/trainer/engine parity with exact artifacts. Rejected inheritance:
Atomic rules, 72-byte legacy schema, move wire, features, dimensions, networks,
search behavior and every validation count or speed result.

## Horde-Stockfish

- Checkout commit: `83521c3b9ff2c9e195b8fe75c3b8ec4917bd0e02`
- Tree: `c75e00cd8746f8d8915eae76b72fdf901f67e5f9`
- Checked-out branch: `main`
- `docs/horde/nnue-v2-design.md` blob: `0467db40add76b0c8d3a9071d8a9be590b771f94`
- `src/nnue/horde_v2_features.h` blob: `e6964eb4b77b7ac166713b4401aa7eb5d138b1db`
- `src/nnue/horde_v2_full_refresh.h` blob: `e545e68dc09396fedd989ebdb31ee1da02ac730c`

Admitted method only: deterministic physical-square enumeration, explicit
capacity bounds, typed fail-closed full-refresh results, scalar-first parity
and separation of legacy/default from the candidate path. Rejected
inheritance: asymmetric roles, kingless state, feature rows, buckets, widths,
topology, container, shifts, trainer settings and all campaign evidence.

## Crazyhouse consequence

The implementation is derived only from the frozen Crazyhouse physical schema,
goldens and live `Position` state. Donor values are not inputs. Any later
container, trainer or SIMD work requires its own Crazyhouse preregistration.

