# ADR-0001 Addendum 005: conservative initial Crazyhouse search policy

- Status: accepted for fixture-first implementation
- Date: 2026-08-23
- Evidence class: `E1_ENGINEERING`
- Governing ADR: `adr-0001-official-specialization-architecture.md`, Decision 9
- Source lineage: official Stockfish; Fairy-Stockfish is not an allowed source base

## Context

Crazyhouse rule, state, evaluator and Worker plumbing now reach the official Stockfish search. The initial search-primitives gate made `Position::see_ge()` return pass for Crazyhouse. That is conservative for callers that prune on `!see_ge()`, but it has the opposite effect in the ProbCut MovePicker: every capture passes the positive eligibility filter. The current search also still consumes the persisted halfmove counter in orthodox TT score downgrades and cutoff suppression, even though the frozen Crazyhouse profile has no 50- or 75-move terminal rule.

Two other selective mechanisms are admitted by orthodox on-board non-pawn material: null-move pruning and the shared shallow-pruning block. Pockets, promoted provenance and variant-specific tactical resources are not represented by that admission predicate. These mechanisms require strength evidence before they can be trusted as Crazyhouse defaults.

## Decision

The initial correct baseline keeps official Stockfish search intact except for the smallest variant-dependent boundary below.

1. TT score decode receives an explicit typed rule50-policy flag. Crazyhouse skips all `100 - r50c` mate/TB downgrades. Passing `r50c == 0` is rejected as an implementation because it would still downgrade mates longer than 100 plies.
2. The high-halfmove TT cutoff suppression applies only to orthodox chess. A persisted Crazyhouse counter remains available for notation and round trip but has no terminal or TT-policy effect.
3. Crazyhouse does not enter null-move pruning.
4. Crazyhouse does not enter full ProbCut while its exchange predicate is the conservative pass implementation.
5. Crazyhouse does not enter the shared shallow-pruning block guarded by orthodox on-board non-pawn material. This disables its move-count, capture-futility, continuation-history, parent-futility and SEE cuts as one initial conservative boundary.
6. Chess retains the exact official conditions and deterministic signature.

The following are deliberately unchanged:

- generic razoring and child futility that are not admitted by the orthodox material predicate;
- TT, alpha-beta, PV, LMR and singular-search mechanics not identified as variant-state omissions by this map;
- qsearch captures when not in check and complete evasions, including drop blocks, when in check;
- the intentional absence of checking drops outside check in qsearch;
- move ordering and generic correction/history topology.

Each disabled mechanism may be restored only as a separately preregistered P7 hypothesis after correctness, fixed-work and repeated paired-speed gates. No mechanism is restored merely because the conservative baseline is slower or because a legacy donor used it.

## Polarity rule for conservative sentinels

A sentinel return value is not classified once at the callee. Every caller must be classified by polarity:

- `if (!see_ge(...)) continue` becomes conservative when `see_ge()` always passes, because the move is retained;
- `select(move => see_ge(move, threshold))` becomes permissive when it always passes, because every move is admitted to the downstream selective cutoff;
- ordering bonuses and good/bad partitions do not remove moves and therefore remain strength-only observations.

Any future replacement for SEE must re-audit every caller rather than changing only `Position::see_ge()`.

## Required evidence

The fixture-first verifier must first reject the mapped source for exactly these policy gaps while proving that its existing special-state search pipeline is otherwise live. A clean implementation build must then prove:

- the explicit rule50-policy flag at both full-search and qsearch TT decode sites;
- no Crazyhouse high-counter TT cutoff suppression;
- typed Crazyhouse guards for null-move, full ProbCut and the complete shared shallow-pruning block;
- unchanged SEE negative-filter, qsearch, Syzygy, upcoming-repetition and shuffling boundaries;
- root checkmate, root stalemate, a pocket stalemate escape, a forced drop evasion, halfmove 100 without a draw, promoted capture, en passant, promotion, castling and repeated high-halfmove mate search;
- warning-strict clean build, exact legacy-network identity, zero stderr, no timeout, no fallback and no owned residual process;
- exact standard-chess control against the pinned official comparator.

This evidence establishes an initial engineering boundary only. It is not speed or Elo evidence.
