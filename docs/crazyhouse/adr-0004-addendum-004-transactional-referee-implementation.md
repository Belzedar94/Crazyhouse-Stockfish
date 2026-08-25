# ADR-0004 Addendum 004: Transactional referee implementation boundary

- Status: Accepted before production behavior implementation
- Date: 2026-08-21
- Evidence class: `E1_ENGINEERING`
- Profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Profile SHA-256: `d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68`

## Decision

The clean CuteChess candidate will implement the frozen Crazyhouse board policy through a fresh exact-type staging board and an enumerated, non-failing state commit. Public `Board::setFenString()` remains non-virtual. It dispatches to a new protected full-load hook; the default hook calls the prior parser body, moved unchanged to an explicitly in-place helper. Only exact registered variant identity `crazyhouse` receives the strict transactional override.

No whole-object assignment, base-subobject assignment, object-wide swap or validation followed by a second live parse is admissible. Those approaches either slice derived state, transfer configuration and ownership that are not FEN state, or reintroduce a fallible mutation after validation.

## Policy-neutral seams

The first production commit is restricted to generic seams that preserve current behavior:

- `Board::setFenString()` delegates to protected virtual `vLoadFenString(const QString&)`.
- The previous `Board::setFenString()` body becomes `setFenStringInPlace(const QString&)` without parser-policy changes.
- `Board::fenString()` delegates reserve payload construction to protected virtual `vReserveFenString() const`; the default implementation preserves the existing reserve order and `-` empty spelling byte for byte.
- `Board::swapFenState(Board&)` transfers only base state written or reset by a successful FEN load.
- `WesternBoard::swapFenState(WesternBoard&)` extends that transfer with only Western FEN-derived state.

The base state inventory is side to move, starting side, starting FEN, position key, squares, move history and both reserves. The Western extension is sign, both king squares, en-passant square and target, ply offset, reversible-move count, Western history and castling rights. Initialization flags, dimensions, piece metadata, variant configuration, castling targets, Zobrist ownership and virtual identity are not transferred.

Because the protected virtual changes the board vtable, every observation after the seam commit requires a clean library and test rebuild. Stale-object or incremental ABI evidence is inadmissible.

## Exact Crazyhouse transaction

`CrazyhouseBoard` centralizes one identity predicate. If the registered variant is not exactly `crazyhouse`, the full-load, reserve-serialization, result and timeout-material paths delegate to upstream behavior. `LoopBoard`, `ChessgiBoard` and all orthodox boards receive no behavior edit.

The exact-profile loader owns only the pocket envelope and alphabet:

- accepted ingress is an eight-rank board followed by either `[pocket]` or an ingress-only ninth slash and `pocket`;
- bracket form has one opening bracket, one closing bracket and no trailing characters in the placement field;
- slash form has exactly the ninth slash as the pocket delimiter and contains no brackets;
- pocket characters are repeated literal `P`, `N`, `B`, `R`, `Q`, `p`, `n`, `b`, `r`, or `q` only;
- kings, promoted markers, digits, hyphens, whitespace, brackets and every other character are rejected;
- accepted pockets are reconstructed in fixed `PNBRQpnbrq` order, with `[]` as the empty canonical form.

All board-rank, side, castling, en-passant, counter and legality validation remains owned by the existing Board and Western parsers. The normalized six-field string is parsed once into a fresh `CrazyhouseBoard`. Only after full validation succeeds may the live board initialize and commit the staged Board and Western state through the enumerated swap helpers. A false return leaves every observable live field, legal-move set, result, key and undo history unchanged.

The exact serializer uses fixed `PNBRQpnbrq` reserve order and canonical empty `[]`. The exact default FEN changes from `[-]` to `[]`; descendant defaults remain unchanged.

## Terminal and timeout policy

For exact Crazyhouse only, no legal move retains checkmate or stalemate semantics. Automatic fivefold repetition remains a draw. Orthodox insufficient-material, 50-move, 75-move and automatic threefold results are suppressed, and fourfold remains non-terminal. `winPossible()` is true for either side in every exact Crazyhouse physical material state, so a timeout remains a loss even with kings only. Descendants continue to delegate to `WesternBoard`.

## Frozen evidence before behavior

The referee fixture expansion is commit `2d16dfb8cd833faa6e048b9cf1bd789fb3fa03b2`, tree `06a08bd3f22aa81f08472b81cf5f36783e185f07`. Its 14,262-byte test source has Git blob `ffd7ba707f421d2f465595a8bc5e09ab3d3411e6` and SHA-256 `f929a70c3992a8973f42ab14f0d5bd334f3c3348059f753e41c88fe50d80d707`. It freezes canonical and round-trip pockets, early and late rejection atomicity, complete state snapshots, key parity, valid replacement, make/undo restoration, halfmove thresholds, threefold through fivefold, mate, stalemate, timeout material and exact descendant isolation.

Lease 151 produced a clean fresh build but is rejected because its preregistered inventory predicted 40 failures and 7 passes while the unchanged executable produced 39 and 8. The valid-replacement case was already a legitimate green control. Lease 152 reran the exact 32,940,359-byte executable, SHA-256 `88d8879790c8311470652398a58cdc35978660da1f45181b54fa04b9fe5c2df7`, in a fresh output namespace with the corrected frozen inventory. It observed exactly 39 failures, 8 passes, no skips and empty stderr. The normalized 47-line inventory SHA-256 is `14998e6a8518a337ba795cecf77f028c464f5a18458778dc9aac63cc53baacab`.

This is the expanded expected-red prerequisite, not implementation certification. The eight passing controls are expected upstream behavior; the 39 failures define the exact policy delta.

## Advisory and local verification

The browser-Pro advisory session used the required dry-run and browser-only `gpt-5-pro` path without API or fallback. Its response completed in the recorded conversation, but the controller failed to finalize after Chrome became unreachable. The exact complete DOM transcript was recovered before shutdown and sealed at 20,956 bytes, SHA-256 `9cdfb9040797f3c100a4e077178fef55e4b532aef75f9234f29362a03dfa23a7`. Receipt `../../../receipts/private/p4-referee-transaction-consultation-046.json`, 7,659 bytes, SHA-256 `ff649470e8b07df510a76d9b722fccd8a6b481cdb0f7eb165bc1d860326ee44c`, records the transport boundary and adopted advice.

The swap inventory and exact-identity requirement were independently checked against every relevant write in the pinned Board, WesternBoard and CrazyhouseBoard sources. The advisory opinion does not replace compilation, runtime or corpus evidence.

## Gate and resource boundary

G4 remains `IN_PROGRESS`. No referee behavior commit exists at this boundary. The next admissible step is the policy-neutral seam commit followed by a clean D:-only T1 build proving the same 39-failure/8-pass inventory. The exact Crazyhouse FEN and terminal implementations must follow as separate commits and observations.

C: had 6,057,066,496 free bytes at the latest read-only admission check, below the portfolio's normal 40 GiB stop. Any new build requires a fresh emergency-control-plane admission, with all compiler temporary files and outputs on D:, a 4 GiB C: emergency floor and a 1 TiB D: floor. The two observed foreign Horde processes remain owned by another project and are not mutated.

This addendum authorizes no worker start, OpenBench write, strength claim, packaging or release action.
