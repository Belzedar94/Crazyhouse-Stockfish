# ADR-0001 Addendum 002: preserve upstream state for chess-only Chess960 toggles

- Status: accepted for implementation; clean-export G5 admission pending
- Date: 2026-08-14
- Evidence class: `E1_ENGINEERING`
- Parent: `adr-0001-addendum-001-transactional-routing.md`
- Source baseline: official Stockfish dev `5062aee519a1ba262d472d8ab139851ced56573e`
- Search boundary: Crazyhouse worker search remains disabled

## Context

The parent routing transaction treated every `UCI_Chess960` change like an evaluator replacement: it advanced the configuration epoch, invalidated the position, removed the active backend, loaded a fresh official network and cleared TT, histories and thread state. That policy was fail-closed, but it did not preserve official Stockfish behavior.

The official benchmark list changes `UCI_Chess960` before its final two positions and resets it afterward. Staging the internal option without applying the route made the next search hit the `pendingDirty` admission invariant after position 49. Applying the full replacement transaction completed all 51 positions but changed the frozen result from 2,884,956 to 2,886,930 nodes and changed the final bestmoves. Preserving search state for the exact chess-only toggle restored the official node count and ordered-bestmove signature in three fresh processes.

The mandatory browser advisory was dry-run with nine authenticated files and then attempted through the configured, visible, copied-profile and project-isolated browser paths. Every attempt failed before prompt submission; no recommendation was recovered and no API, credits, alternate model or foreign browser attachment was used. The immutable unavailability record is `receipts/private/p5-uci-routing-chess960-consultation-unavailable-030.json`. This addendum therefore relies only on the earlier accepted Engine-ownership advice, the official source behavior and the frozen regression.

## Decision

`Engine::apply_pending_route()` has one state-preserving fast path. It is eligible only when all of these predicates are true:

- the current snapshot satisfies every routing invariant;
- a route change is pending and no pending option error exists;
- both active and requested rulesets are `chess`;
- the active backend is `official-chess`, ready and bound to the current epoch;
- active and requested `EvalFile` strings are identical;
- active and requested `CrazyhouseEvalFile` strings are identical;
- `UCI_Chess960` is the sole differing route field.

The predicate is public within `EngineRouting` so its positive case and the evaluator/stale-backend exclusions can be tested without loading a network.

After the exact active search has stopped, an eligible transition:

1. verifies that the physical official route is installed, a selected official file exists and no legacy backend is retained;
2. advances the checked configuration epoch;
3. consumes the complete requested route as active;
4. clears pending and active errors and invalidates the position epoch;
5. logically rebinds the unchanged authenticated official backend to the new epoch;
6. rechecks the complete snapshot and backend-epoch invariants.

It deliberately does not reload the official network and does not clear TT, histories, threads or network replicas. This matches the pinned upstream `UCI_Chess960` option semantics. Any other changed field, ruleset, failed backend or stale epoch takes the existing full replacement/failure path.

Internal `bench` and `speedtest` scripts do not bypass routing. They stage options through the ordinary callback and invoke the same apply barrier before consuming the next position or search command. External UCI continues to stage route options until `isready` or a gated command applies them.

## Rejected alternatives

- A benchmark-only mutation of active Chess960 state would create a second authority and bypass the epoch contract.
- Accepting a new benchmark digest would hide a standard-chess regression instead of preserving the selected source baseline.
- Clearing TT only after the benchmark would still alter the two Chess960 searches and would not match upstream semantics.
- Treating evaluator option strings as irrelevant while chess is active would allow an unvalidated future backend request to enter the fast path.

## Required proof

- Strict routing fixture: accept the sole Chess960 delta; reject changed official or legacy evaluator strings and a stale backend epoch.
- Frozen UCI corpus: all ten route/error/recovery scenarios pass with the same protocol digest; Crazyhouse search remains disabled.
- Routed standard control: 21 ordered options, three route commits per bench, three fresh 51-position benches at exactly 2,884,956 nodes and signature `78751f6d2a1146c15dac46875b52c4548deb3ca87475f478eebef5906f2d9259`.
- Routed speedtest smoke: enter with active Chess960, return to standard chess through the ordinary transaction, retain one official backend identity, search positive nodes and make no timing claim.
- Clean export: every tracked blob matches the committed tree and the canonical Stockfish Makefile supplies the x86-64 product profile.

No result in this addendum is Elo, referee certification, OpenBench evidence, packaging or release readiness.
