# Stockfish-dev specialization reference audit

- Status: read-only implementation reference
- Date: 2026-08-13
- Evidence class: `D0_DISCOVERY`
- Crazyhouse source authority: none

## Scope

Atomic-Stockfish and Horde-Stockfish are successful local examples of specializing an official Stockfish development snapshot. They are admitted only as implementation-pattern references. They are not Crazyhouse rule authorities, source baselines, evaluators, referees, books, networks, limits, schemas, search parameters or release identities.

No code was copied during this audit. Any later adaptation must be rewritten against the frozen Crazyhouse official source, receive its own fixture first, and preserve the exact standard-chess control until the relevant behavior gate opens.

## Atomic-Stockfish

- Canonical local repository: `Atomic Project/Atomic-Stockfish`
- Audited ref: `origin/main`
- Commit: `70ea2218cec918ddb393055b8929d4df7e0d9711`
- Tree: `87ba54681784e90672b513afb0ab99bdd63b6821`
- Remote `main` matched the local ref during the audit.
- Latest audited stable tag: annotated `v1.0.3` object `acc3c9b8e0d55ddec6bf86debba7ec91a402bfd3`, peeled commit `d5d5504035c24666f7a70b8b356ebfa2e2c2823d`.
- Official-Stockfish merge base: `eca43a97efd2cf0c9b7153c71b85f35e0fd1f5ca`.
- License file blob: `f288702d2fa16d3cdf0035b15a9fcbc552cd88e7` (GPLv3-family Stockfish license text).
- Worktree state: clean, but its checked-out branch is not the audited canonical ref; all findings below are Git-object-bound to `origin/main`.

Useful demonstrated patterns:

- expose `UCI_Variant` as a single-value combo so a specialized binary cannot select unsupported rules;
- keep position admission typed and fail-closed with `PositionSetError`;
- put move-persistent variant state in the copied `StateInfo` prefix and reversible move deltas in the non-copied suffix;
- verify the selected evaluator before search starts;
- exercise rules, incremental evaluation, ISA parity and the exact release tag with distinct gates.

Selected audited blobs:

| Purpose | Git blob |
| --- | --- |
| Engine/UCI boundary, `src/engine.cpp` | `ff86af061af110784e961f517d5c9008c1d4d57d` |
| State layout, `src/position.h` | `4a23589e2b5e8768a9e234971825d26fac78a596` |
| Rule harness, `tests/atomic_rules.py` | `f1e7de063d6ba8ece7dccf216047bef7ef79af0a` |
| Exact-tag release gate | `dc7515bf5ece0ff8d29c1236ec7568357506be73` |

Rejected inheritance includes Atomic explosion/check semantics, blast deltas, material values, pruning constants, tablebase policy, NNUE features and every campaign result. Its official merge base also predates the Crazyhouse baseline, so even a useful structural pattern must be reconciled with current upstream APIs.

## Horde-Stockfish

- Canonical local repository: `Horde Project/Horde-Stockfish`
- Audited ref: `origin/main`
- Commit and stable `v1.0.0` peeled commit: `83521c3b9ff2c9e195b8fe75c3b8ec4917bd0e02`
- Tree: `c75e00cd8746f8d8915eae76b72fdf901f67e5f9`
- Remote `main` and stable tag matched the local refs during the audit.
- Annotated `v1.0.0` tag object: `b1cb2eeddb0f2041e4de6b66c4c6f42bbb7286cd`.
- Official-Stockfish merge base: `762dd1da9a5db458180b2c5db6c53dc40ec61e1a`.
- License file blob: `f288702d2fa16d3cdf0035b15a9fcbc552cd88e7`.
- Worktree state: clean on `main`.

Useful demonstrated patterns:

- expose a fixed single-value `UCI_Variant` and reject incompatible options with explicit diagnostics;
- disable orthodox tablebases at the option boundary when their state model is invalid;
- separate typed terminal reasons from result values;
- keep a physical, evaluator-independent data format and authenticate chunk/container provenance;
- keep legacy and candidate evaluator paths distinct and test decoder, integer container, scalar/incremental state, deterministic resume and release contracts independently;
- merge a strength winner only after checking winner ancestry and post-merge state.

Selected audited blobs:

| Purpose | Git blob |
| --- | --- |
| Engine/UCI boundary, `src/engine.cpp` | `749ebb404a013fbfb2a4af3d6d16e01ef4478bff` |
| State/outcome contract, `src/position.h` | `3d96bc9495d046687926da53394017745e3aa8bd` |
| Rule harness, `tests/horde_rules.py` | `3dad64afa2167624a7e23a18703207c385ea457a` |
| Release contract | `94d4fc0915240ef69a5616b196967110908bf075` |

Rejected inheritance includes Horde role asymmetry, kingless legality, extinction/fortress results, material bounds, physical schema, V1/V2 network formats, search tuning, books, time controls and campaign gates.

## Crazyhouse consequence

The references reinforce, but do not alter, the frozen official-base architecture: a single-purpose Crazyhouse binary, typed transactional position admission, complete reversible state, fail-closed evaluator routing, physical data, and exact release-artifact verification. Crazyhouse-specific capacity is different from both references: the authenticated 303-legal-move fixture requires a non-truncating growable live move path while orthodox Stockfish retains its fixed 256-entry path.
