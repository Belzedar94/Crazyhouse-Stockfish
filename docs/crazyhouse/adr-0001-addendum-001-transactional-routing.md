# ADR-0001 Addendum 001: transactional UCI and evaluator routing

- Status: accepted for the P5 routing-only slice
- Date: 2026-08-14
- Evidence class: `E1_ENGINEERING`
- Parent: `adr-0001-official-specialization-architecture.md`
- Search boundary: Crazyhouse worker search remains disabled

## Scope

This addendum freezes the command and ownership contract needed to connect the already certified standalone legacy parser, scalar full refresh and value adapter to an engine-level route. It does not bind the legacy evaluator to search workers or expose it through `eval`. It does not admit OpenBench, strength testing, packaging or release claims.

Atomic-Stockfish and Horde-Stockfish demonstrate useful fixed-variant option and incompatible-service guards. Their rules, networks, evaluator APIs and single-value routing are not inherited. Crazyhouse retains both `crazyhouse` and explicit `chess` routes because the pinned upstream control is a mandatory regression boundary.

## Requested configuration is not active configuration

`OptionsMap` keeps its upstream mutation semantics and stores the values requested by UCI. It is not authoritative for an active route. No route-affecting option callback may load a network, change the committed position, clear TT or histories, initialize tablebases, or expose a backend to workers.

Engine owns one authoritative routing aggregate with:

- pending and active `Ruleset`, Chess960 flag, `EvalFile` and `CrazyhouseEvalFile`;
- a pending-dirty flag and structured pending error;
- an optional active ruleset and structured active error;
- a checked 64-bit configuration epoch;
- an optional committed-position epoch;
- backend kind `NONE`, `OFFICIAL_CHESS` or `LEGACY_CRAZYHOUSE_V1`;
- backend readiness `NONE`, `READY` or `FAILED`;
- backend epoch and diagnostic identity.

The official NUMA-replicated network and the single legacy V1 object remain separate Engine-owned storage. The official `Network` is not made polymorphic. There is no common evaluator base, generic variant registry, tensor variant, legacy NUMA replication or legacy data inside the official network object.

The following invariants are mandatory:

```text
backend READY  => backend epoch == configuration epoch
position valid => position epoch == configuration epoch
official backend => active ruleset == chess
legacy backend => active ruleset == crazyhouse && Chess960 == false
pending dirty/error => no search admission
active crazyhouse => no worker, official eval/trace, Syzygy or WDL entry
crazyhouse search ready => false for this slice
```

Epoch exhaustion is a fatal internal invariant failure. Expected option, file and position failures are structured command errors and do not terminate the process.

## Frozen option inventory and startup state

- `UCI_Variant` is a combo with default `crazyhouse` and exactly the values `crazyhouse` and `chess`.
- `CrazyhouseEvalFile` is a string whose pre-packaging default is empty. `Crazyhouse_v1.nnue` is not advertised as a default until its packaged bytes and location are authorized and authenticated.
- `EvalFile` retains the pinned official Stockfish default and remains the chess-only network option.
- `UCI_Chess960` is staged in the same pending route aggregate. `crazyhouse + true` is rejected only when the complete pending snapshot is applied.
- Both evaluator option strings and the Chess960 request persist across variant switches within one process. A new process restores declared defaults.

At startup no route, backend or position is committed. The requested default Crazyhouse configuration is pending. The physically constructed official objects are not an active binding. A rule or search command cannot use them until a successful route commit.

A relative `CrazyhouseEvalFile` path is resolved exactly once against `binaryDirectory`. No working-directory or multi-directory probing is allowed. Diagnostics expose the registered digest and, when needed, a basename; they do not need to expose a full local path.

## Route staging and application

Changing `UCI_Variant`, `UCI_Chess960`, `EvalFile` or `CrazyhouseEvalFile` first stops and joins the exact active search, then updates only the pending aggregate. Unsupported route-option input latches a pending error without changing active state. A later valid assignment that supersedes that field clears the corresponding error.

`isready` applies the complete pending snapshot. Non-perft `go`, `eval`, `bench`, `speedtest` and `export_net` enforce the same gate before admission. Application order is:

1. stop and join the exact search;
2. reject a pending parse/option error without mutating the active route;
3. snapshot the complete request;
4. revoke search admission and the logical old backend binding;
5. validate the ruleset/Chess960 combination;
6. load only the selected backend into temporary ownership;
7. consume the request into one complete active state;
8. clear the position epoch, TT, histories and route-specific worker state;
9. remove every inactive or stale backend;
10. emit a committed success or committed failure diagnostic.

A successful commit advances the configuration epoch, installs exactly the matching backend at that epoch, clears errors and emits `readyok`.

A rule-valid backend-load failure also consumes the requested configuration and advances the epoch, but commits backend `FAILED`, kind `NONE`, no usable backend and no valid position. It never restores the old backend and emits no `readyok`. The active ruleset remains available so a later valid `position` can drive evaluator-independent rule and perft checks.

An invalid combination such as Crazyhouse plus Chess960 advances into an invalid active route with no active ruleset, no backend and no valid position. It emits no `readyok`.

Repeated `isready` after a backend failure retries the same requested file. Another failure does not advance the epoch or invalidate a position built only for rule/perft work. A later successful retry advances the epoch and invalidates that position. Repeated `isready` after a healthy unchanged route is a no-op and emits `readyok`.

## Transactional position ownership

Engine owns the committed position through a heap-swappable slot containing a `Position` and its complete `StateInfo` deque. `Position` is not made broadly movable or copyable.

`position startpos` selects the exact start FEN from the active ruleset. Crazyhouse uses:

```text
rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1
```

Chess uses the pinned official `StartFEN`. `startpos` must end or be followed by the literal `moves` token.

For both startpos and explicit FEN, a candidate slot is constructed with the active ruleset, parsed with the explicit ruleset overload and replayed completely. Only complete success swaps the candidate into Engine and assigns the current configuration epoch. A bad FEN, malformed command, illegal move or illegal drop leaves the earlier physical slot unchanged but clears its position epoch, so it cannot be used until a later valid position command commits.

## Command admission

`go perft N` consumes any pending route change first, then requires a valid active ruleset and position at the current epoch. It does not require an evaluator. Perft runs directly from the committed position or through an explicit `(fen, chess960, ruleset)` fixture overload; it never reconstructs through a chess-default helper and never calls network verification.

For the Crazyhouse route, all of the following remain explicitly refused even with the exact legacy file loaded:

- `eval`;
- non-perft `go`;
- `bench` and `speedtest`;
- `export_net`;
- every path into `ThreadPool::start_thinking`, official NNUE evaluation/trace, Syzygy or WDL formatting.

The stable refusal code for search is `crazyhouse_search_not_bound`. `eval` uses `crazyhouse_eval_not_bound`. Bypass commands use a command-specific `crazyhouse_<command>_not_bound` code.

Chess search requires a valid position epoch, official backend kind and readiness at the current configuration epoch. The existing worker path remains otherwise unchanged.

`ucinewgame` stops and joins search and clears TT/histories. It does not change route options, epochs, backend readiness or position validity. Tablebase configuration is applied only for an active chess route; Crazyhouse unmaps or bypasses tablebases.

## Candidate backend proof

The legacy candidate is a fresh `LegacyCrazyhouseNetworkV1`. Success requires parser status `Success`, `loaded()`, the registered SHA-256 and the registered description. A failed candidate is destroyed. The full numerical corpus remains a build/test gate and is not rerun on every `isready`.

The official candidate is a fresh official `Network` with fresh `EvalFile` metadata. It must prove that the official loader selected the canonical requested file before NUMA installation. `Network::verify()` cannot represent a recoverable route failure because it terminates; `modify_and_replicate()` cannot be the candidate boundary because it begins from existing state. The official content hash is diagnostic, not a cryptographic artifact identity. Any future requirement for arbitrary official-network SHA-256 is a separate ADR extension.

At the beginning of replacement, logical admission is revoked. A failed parser, allocation or NUMA installation leaves backend `FAILED`; it cannot restore a previous official or legacy backend.

## Stable diagnostic shape

Expected failures use one-line UCI information records:

```text
info string ERROR <command> code=<stable_code> ruleset=<value> epoch=<n> backend=<value> position=<valid|invalid>
```

Position replay also identifies `move_index` and `token`. Route success reports ruleset, backend, epoch and authenticated identity, followed by `readyok`. Route failure reports `READY state=failed readyok_withheld=1` and does not invent a non-UCI `readyerror` token.

The initial stable code families are:

- `invalid_variant`, `crazyhouse_chess960_rejected` and `route_pending`;
- `crazyhouse_eval_file_empty` plus one snake-case code for each legacy parser status;
- `official_eval_not_loaded` for a fresh official candidate that did not select the request;
- `position_requires_committed_route`, `invalid_fen`, `malformed_position` and `illegal_move`;
- `position_epoch_invalid`, `backend_not_ready` and `backend_route_mismatch`;
- the Crazyhouse refusal codes defined above.

Exact messages and option order are frozen by transcript fixtures before behavior code.

## Required fixture-first sequence

1. Freeze option inventory/defaults/order, routing snapshots, diagnostics, startpos, crossed loads, failed replacement, transactional position, evaluator-free perft, hard Crazyhouse refusals and explicit-chess controls.
2. Add Engine-owned pending/active state, epochs, candidate loaders and heap-swappable position ownership without UCI or worker binding.
3. Integrate staged UCI options, `isready`, route-aware position/perft and expected-error recovery.
4. Close `bench`, `speedtest`, `export_net`, `flip`, Syzygy and debug bypasses; rerun the exact chess UCI, network, perft, search, bench and digest controls.
5. Bind a dedicated Crazyhouse worker evaluator only in a later separately gated change.

No stage may enable Crazyhouse worker search before the routing corpus and the remaining correctness/referee boundaries pass.
