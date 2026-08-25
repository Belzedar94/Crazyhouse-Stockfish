# Crazyhouse authority resolution

Authority profile: `LICHESS_CRAZYHOUSE_2026_08_12`

Announced dialect: Lichess Crazyhouse as observed on 2026-08-12
Evidence class: `D0_DISCOVERY`

## Authority order

Material conflicts are resolved in this order:

1. `lichess-org/scalachess@cbffc9d7e2c6f8ba33381c5403e1b4f992199626` defines core legality, variant state, and game-end semantics.
2. `lichess-org/lila@13895e5856db0f854f6ab76394fffce852ebd5c9` and the official Lichess Crazyhouse page define product-facing claims, persistence, and published assets.
3. The Fairy-Stockfish chess-variant FEN standard defines the canonical bracket-pocket interchange form used by this project.
4. Pinned python-chess and chessops builds are independent differential references. They do not override scalachess where custom-position insufficient-material or result behavior differs.
5. Engine, referee, runner, PGN consumer, and book behavior are implementations under test, never rule authority.

Primary links:

- [Lichess Crazyhouse](https://lichess.org/variant/crazyhouse)
- [Pinned scalachess source](https://github.com/lichess-org/scalachess/tree/cbffc9d7e2c6f8ba33381c5403e1b4f992199626)
- [Pinned lila source](https://github.com/lichess-org/lila/tree/13895e5856db0f854f6ab76394fffce852ebd5c9)
- [Chess-variant FEN standard](https://fairy-stockfish.github.io/chess-variant-standards/fen.html)

## Board, pockets, and ownership

- The board is orthodox 8x8 chess with normal colored pieces and exactly one royal king per side in reachable games.
- Each side owns a pocket containing counts of pawn, knight, bishop, rook, and queen. Kings cannot enter or leave a pocket.
- Capturing transfers the captured unit to the capturer's pocket with the capturer's color.
- If the captured board unit originated as a promoted pawn, it becomes a pawn in the capturer's pocket. Therefore board piece type alone is insufficient state.
- Pocket order is not semantically significant. Parsers normalize counts by owner and piece type.

Canonical project FEN output uses bracket pockets after the board field, for example `.../RNBQKBNR[PNq] w KQkq - 0 1`. The parser may accept the Lichess slash-pocket form as an input dialect, but adapters must normalize it to bracket form before hashing or fixture comparison. A promoted board unit is marked with `~` immediately after its piece character. No adapter may discard `~`.

## Drops and check

- A pocket unit may be dropped only onto an empty square.
- A pawn may not be dropped on rank 1 or rank 8. No file-based doubled-pawn restriction applies.
- A dropped pawn may later move two squares from its normal starting rank if the ordinary conditions hold.
- Drop check and drop checkmate are legal. There is no shogi-style pawn-drop-mate prohibition.
- A drop may answer check if the resulting position is legal. Against a single sliding check, a blocking drop is limited to squares strictly between checker and king. A double check cannot be answered by a drop.
- A move or drop leaving the moving side's king in check is illegal.
- Checkmate requires that the checked side have no legal board move and no legal drop. Stalemate requires no legal board move or drop while not in check.

The UCI wire dialect is compatible with the established Fairy-Stockfish variant protocol, but it is implemented on the mandatory official Stockfish source base: `setoption name UCI_Variant value crazyhouse`. Drops use `<piece>@<square>`, such as `P@e4`. Protocol compatibility does not make Fairy-Stockfish a source ancestor. Each fresh engine process and every match game must prove the variant option and starting state before play; relying on a prior process setting is forbidden.

## Promotion provenance

Promotion changes the board type but preserves an origin flag. This flag:

- is serialized with `~`;
- follows the piece through ordinary moves and castling-independent board transformations;
- is cleared when the unit is captured;
- controls conversion to a pocket pawn rather than the visible promoted type;
- must survive make/undo, FEN/EPD/PGN round trips, datagen framing, symmetry, repetition identity, and transposition hashing.

Two positions that differ only by a promoted-origin flag are materially different because a later capture produces a different pocket. Their full state keys must differ.

## Castling and en-passant

Orthodox castling legality and rights apply. Captured or moved original kings/rooks affect rights normally. A dropped rook never restores a lost right. Canonical FEN persists `KQkq`-style rights.

Orthodox en-passant applies. Canonical FEN stores an en-passant target only when required by the selected serialization policy, and repetition identity includes it only when a legal en-passant capture exists. An en-passant capture transfers the captured pawn to the capturer's pocket. Make/undo must restore board, pocket, en-passant, clocks, keys, and provenance exactly.

## Draws, claims, and precedence

Core Lichess Crazyhouse has no 50-move or 75-move terminal draw and no insufficient-material draw. Halfmove and fullmove fields are still persisted for interchange, diagnostics, and deterministic round trips; they do not create a terminal result.

Scalachess provides an automatic fivefold-repetition draw. Lila exposes threefold repetition as a player claim and may claim automatically for configured clients or bots. Because an engine match has no human claim UI, the match contract may use automatic third occurrence as a symmetric immediate-claim proxy. That proxy must be preregistered in the referee/runner manifest and must not be presented as the core rule.

Terminal evaluation follows this precedence:

1. Checkmate.
2. Any Crazyhouse-specific variant terminal condition, if one is introduced by a future authority revision.
3. Stalemate.
4. Automatic draw conditions such as fivefold repetition.
5. Optional claims such as threefold repetition under the declared match policy.

A claim or draw condition cannot override a checkmate delivered on the same completed move. Result fixtures must cover the boundary explicitly.

## Repetition identity and minimum history

Repetition identity includes:

- every board piece and color;
- side to move;
- castling rights;
- legally relevant en-passant state;
- both pockets by owner and piece type;
- promoted-origin flags.

Current physical state is sufficient for legal-move generation when it contains board, pockets, provenance, side, castling, en-passant, and counters. Result computation additionally needs the repetition sequence and claim availability. Training records must also contain terminal reason, game/trajectory identity, move and result labels, and complete production provenance. Opening-root identity alone is not a valid split boundary because trajectories can transpose.

## Interchange contract

| Surface | Contract |
|---|---|
| FEN | Bracket pockets are canonical output; slash-pocket input may be normalized; `~` is mandatory for promoted origin; six standard fields remain present |
| EPD | FEN-compatible physical state plus explicit operation fields; operations may not hide pocket/provenance state |
| PGN | `Variant "Crazyhouse"`, authenticated starting FEN when non-standard, legal SAN/drop notation, clocks and terminal reason retained |
| UCI | `UCI_Variant=crazyhouse`; drops as `P@e4`; `isready` after option and evaluator changes |
| Match persistence | Variant option, evaluator identity, FEN, colors, clocks, adjudication, and terminal reason logged for every game |
| Physical datagen | Evaluator-independent board, pockets, provenance, side, rights, en-passant, counters/history contract, move, result, terminal reason, trajectory IDs, and provenance |

## Evaluator compatibility authority

The legacy evaluator is the exact 58,534,811-byte file with SHA-256 `8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`. The local file is byte-identical to `public/lifat/nnue/crazyhouse-8ebf84784ad2.nnue` at the pinned lila commit (Git blob `ad269c33db13ecae295ec66ee9f438462498c623`). The asset-specific lila README at blob `c94bf53d0cd54599d899a51f0aa4c1e01e4f0b94` designates the variant networks in that directory as CC0.

Its container header is version `0x7af32f20`, architecture `0x3c103e72`, and transformer `0x5f2348b8`. The selected source expects `HalfKAv2Variants` with base transformer hash `0x5f234cb8`; serialization XORs the 1024 doubled output dimensions, yielding the observed value. This proves format-level consistency, not safe runtime behavior. G5 still requires a successful exact load and fail-closed missing, wrong, corrupt, truncated, and incompatible loads with no classical or alternate-network fallback.

The candidate public name `Crazyhouse_v1.nnue` may only be a byte-identical release alias. It cannot silently change the source default, network bytes, champion identity, or license receipt.

## Symmetry contract

The initially admitted universal augmentation is vertical rank reflection combined with color swap. It transforms:

- board colors and ranks;
- side to move;
- pocket ownership and piece colors;
- `KQkq` rights by color while retaining king/queen side;
- en-passant rank;
- promoted-origin squares;
- moves, checks, terminal reason, and result perspective.

An absolute White result changes sign; a side-to-move label remains defined by its documented perspective. File reflection, 180-degree rotation, and other symmetries are not admitted for positions with castling rights unless separately proved against the authority corpus. Every admitted transform must be involutive and pass byte-level round-trip and legal-move equivalence tests.

## Required differential fixture families

The shared corpus must include at least:

1. Pocket parse, ownership, ordering, bracket/slash normalization, and empty-pocket round trips.
2. Every piece drop, occupied-square rejection, pawn rank rejection, drop check, block check, double-check rejection, drop mate, and stalemate-with-pocket boundary.
3. Capture-to-hand for each type, promoted capture demotion, chained capture/drop, and make/undo restoration.
4. Ordinary and under-promotion provenance, moved promoted units, FEN `~`, provenance-only key inequality, repetition, and transposition cases.
5. Both castlings, lost rights, dropped rooks, rook captures, and checks through castling paths.
6. En-passant creation, legal relevance, capture-to-pocket, pinned/illegal en-passant, serialization, key, and undo.
7. Threefold claim proxy, fivefold automatic draw, no 50/75-move draw, no insufficient-material draw, checkmate precedence, and stalemate draw.
8. FEN/EPD/PGN/UCI round trips, option persistence across games, clocks, result text, and terminal reason.
9. Safe symmetry with pockets, rights, en-passant, promoted flags, moves, and labels.
10. Perft roots chosen from each materially distinct transition, with counts frozen only after agreement among authority adapter and independently implemented paths.

The actual match referee must consume and pass the same relevant legality/result corpus. A passing engine corpus cannot compensate for a referee mismatch.
