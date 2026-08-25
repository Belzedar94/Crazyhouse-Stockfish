# ADR-0001 Addendum 003: Exact official upstream synchronization

- Status: Accepted for implementation
- Date: 2026-08-22
- Evidence class: `D0_DISCOVERY` transitioning to `E1_ENGINEERING`
- Product head before this decision record: `0d32ce48a883285297efaf34a318f095bfccaac4`
- Product tree before this decision record: `8f64e0e798cbbf55e9d2f9cd9439c9db6177e2df`
- Original official base: `5062aee519a1ba262d472d8ab139851ced56573e`
- Selected official upstream: `229f6339e537a097a79831cd06dbfdb3e623d4ac`
- Selected official tree: `11ff8e5aeaa5c0d19c085cdd92b5c8f9321199e6`

## Context

The product started from the latest authenticated official Stockfish development revision available at its 2026-08-13 selection boundary. A new read-only freshness check and an exact fetch from `https://github.com/official-stockfish/Stockfish.git` now place official `master` at `229f6339e537a097a79831cd06dbfdb3e623d4ac`, committed 2026-08-19. The original base is the exact merge base. Upstream is 19 commits ahead and zero commits behind that base; the product has 128 commits after the same base.

The upstream delta changes 26 paths with 1,097 insertions and 885 deletions. Seven paths were also changed by the Crazyhouse port:

- `src/engine.cpp`
- `src/engine.h`
- `src/movegen.cpp`
- `src/position.cpp`
- `src/search.cpp`
- `src/search.h`
- `src/syzygy/tbprobe.cpp`

An authenticated three-tree preflight reports that six of those paths merge automatically. `src/search.cpp` has one content-conflict boundary. Additional upstream changes touch NNUE accumulation, network parsing, memory allocation, time management and tablebase hardening, so selective cherry-picking would not represent the selected official development revision.

Official license identity remains `Copying.txt`, Git blob `f288702d2fa16d3cdf0035b15a9fcbc552cd88e7`, 35,149 bytes.

## Decision

Synchronize the product through a non-fast-forward merge of the exact selected official commit on dedicated branch `sync/official-229f6339`.

The merge must have exactly these parents:

1. the clean product decision-record tip pinned by the immutable merge-start receipt, containing this ADR and no source change after `0d32ce48a883285297efaf34a318f095bfccaac4`;
2. official upstream `229f6339e537a097a79831cd06dbfdb3e623d4ac`.

Rebase, squash, history replacement and selective upstream cherry-picks are rejected. Existing evidence pins product commit identities; rewriting them would break provenance. A merge preserves every receipt-pinned product commit while making the complete selected official development revision an ancestor.

The synchronization is one engineering variable. Conflict resolution may only reconcile the upstream search changes with already accepted Crazyhouse rule routing and state contracts. It may not introduce a new search heuristic, evaluator architecture, rule interpretation, time control, network, book or referee behavior.

For `src/search.cpp`, resolution must preserve all of these independently testable properties:

- upstream root-PV capacity and current search behavior for standard chess;
- Crazyhouse rule routing, terminal semantics and repetition behavior already admitted by existing fixtures;
- growable Crazyhouse move storage with no fixed 256-move correctness ceiling;
- no accidental tablebase applicability in Crazyhouse;
- no change to the accepted legacy-evaluator boundary.

## Integration procedure

1. Require a clean canonical worktree at the decision-record tip and authenticate the selected upstream commit, tree, merge base, remote and license blob.
2. Pin that exact decision-record commit and tree in the immutable merge-start receipt, then create `sync/official-229f6339` from it.
3. Merge the exact 40-character upstream commit with `--no-ff --no-commit`.
4. Resolve only genuine merge conflicts. Record the staged path set and reject any unplanned generated artifact or network byte.
5. Run whitespace, conflict-marker, parent, ancestry, branch and clean-status checks before admitting the merge commit.
6. Keep the synchronization branch and its evidence if validation fails; do not rewrite or move prior product history.

## Required replay

The merge is not admitted by textual conflict resolution alone. Before it can replace the product source identity, all affected controls must pass from a clean source export with pinned tools:

- clean MinGW build and native UCI handshake;
- deterministic standard-chess bench/digest control;
- standard move-generation and search invariance controls affected by the upstream delta;
- Crazyhouse legal-move, result, FEN round-trip, make/undo/null, pockets, promoted provenance, castling, en-passant, repetition and terminal-precedence fixtures;
- the 303-move and forced-spill capacity controls;
- UCI variant routing, option persistence, position replay and special-state searches;
- referee engine-projection corpus on the actual match-path contract;
- legacy evaluator positive load plus missing, wrong, corrupt and incompatible negative loads;
- sanitizer or equivalent memory/undefined-behavior controls for the changed rule/search/state paths.

The replay must record exact executable, network, fixture, toolchain and harness identities. Prior results are comparison controls, not inherited passes. Speed, Elo, OpenBench, model-selection and release claims are expressly out of scope for this synchronization.

## Gate effect

G2 is reopened only for the upstream-source synchronization boundary. It remains in progress until the merge ancestry and required affected replay are authenticated. G3, G4 and G5 retain their existing incomplete status and cannot be promoted by this decision.

“Latest development” means the exact authenticated official revision at a recorded selection boundary, not an unpinned moving ref. Freshness must be checked again before strength preregistration and before the release-candidate freeze. Later drift requires another additive decision; no stable tag or product history may move silently.
