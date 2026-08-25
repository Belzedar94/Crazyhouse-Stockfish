# ADR-0001 Addendum 004: Crazyhouse MultiPV protocol boundary

- Status: accepted for fixture-first implementation
- Date: 2026-08-23
- Evidence class: `E1_ENGINEERING`
- Governing ADR: `adr-0001-official-specialization-architecture.md`, Decision 3
- Source lineage: official Stockfish; Fairy-Stockfish is not an allowed source base

## Context

Official Stockfish advertises `MultiPV` as a spin option with default `1`, minimum `1`, and maximum `MAX_MOVES == 256`. Crazyhouse already owns a complete growable root vector. The authenticated fixture `MAX_MOVES_OVERFLOW_303_V1` has 303 legal root moves, so the orthodox option range cannot request every known-valid Crazyhouse root line. The fixture does not establish a global legal-move maximum.

The product must expose one static UCI inventory, preserve the orthodox chess contract, remain compatible with clients that only know `MultiPV`, and reject malformed extended requests without silently searching with a retained prior value.

## Decision

The existing option remains unchanged:

```text
option name MultiPV type spin default 1 min 1 max 256
```

The product adds exactly one static variant-specific option:

```text
option name CrazyhouseMultiPV type spin default 0 min 0 max 2147483647
```

`CrazyhouseMultiPV` has these semantics:

1. `0` inherits the current valid `MultiPV` value. Existing UCI clients therefore retain their current Crazyhouse behavior for requests from 1 through 256.
2. A positive value is an explicit Crazyhouse override.
3. The advertised maximum is the signed UCI spin representation boundary, not a claimed Crazyhouse legal-move ceiling.
4. Search clamps the effective request to the actual growable root-vector size, including a `searchmoves` subset. No fixed Crazyhouse move-count bound is introduced.
5. Chess always uses `MultiPV`. `CrazyhouseMultiPV` is inert in chess, but its value persists across ruleset changes.
6. The effective option is selected from the committed root position's typed ruleset, never merely from a pending `UCI_Variant` value.

## Invalid assignment boundary

An invalid `CrazyhouseMultiPV` assignment includes an empty value, non-decimal input, a negative value, or a decimal value above `2147483647`.

- The engine emits `info string ERROR setoption code=crazyhouse_multipv_invalid option=CrazyhouseMultiPV`.
- The last valid numeric value is not replaced.
- A sticky invalid state blocks evaluator-backed Crazyhouse search with route error code `crazyhouse_multipv_invalid` and no `bestmove`.
- A later valid assignment, including `0`, clears the sticky state.
- `isready`, position parsing, rule-only perft, and chess search remain available. An invalid evaluator-independent setting must not corrupt the rules route or the orthodox backend.
- Invalid assignments to the official `MultiPV` option retain official Stockfish behavior. This addendum does not change generic spin-option semantics.

The retained value is therefore never mistaken for an accepted extended request: the failed assignment is visible immediately and Crazyhouse search cannot proceed until correction.

## Rejected alternatives

- Raising `MultiPV` globally changes the orthodox inventory contract.
- Treating 303, 512, or another observed capacity as a legal maximum turns a fixture into an unproved rule bound.
- A ruleset-dependent inventory makes GUI discovery order-dependent.
- A string option discards standard spin metadata and makes GUI integration weaker.
- Defaulting the new option to `1` would ignore existing clients' `MultiPV` requests in Crazyhouse.
- Silently retaining the prior override after malformed input permits a misleading search.

## Required evidence

The implementation is admitted only if one clean binary proves all of the following:

- one exact instance of each frozen option line;
- 303 indexed depth-one PV lines for the authenticated fixture, with complete, duplicate-free root moves matching the fixture digest;
- dynamic clamping to 20 start-position roots and to a two-move `searchmoves` subset;
- inheritance from `MultiPV` when the override is zero;
- persistence and isolation across Crazyhouse/chess/Crazyhouse routing;
- immediate invalid-assignment telemetry, sticky Crazyhouse-search refusal, rule-only perft availability, and recovery after a valid assignment;
- unchanged orthodox `MultiPV` range and exact standard-search control against the pinned official comparator;
- warning-strict clean build, zero stderr, no timeout, no fallback, and no residual owned process.

The 303-line observation is engineering correctness only. It is not Elo, a strength result, or a global move-capacity proof.
