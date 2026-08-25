# ADR-0002: Lichess Crazyhouse rule, state and serialization profile

- Status: Accepted for implementation
- Date: 2026-08-13
- Profile ID: `LICHESS_CRAZYHOUSE_2026_08_12`
- Evidence class: `D0_DISCOVERY` / `E1_ENGINEERING` contract
- Primary authority: scalachess `cbffc9d7e2c6f8ba33381c5403e1b4f992199626`
- Product snapshot: lila `13895e5856db0f854f6ab76394fffce852ebd5c9`

## Scope and authority

This profile is Lichess Crazyhouse on an orthodox 8x8 board. Scalachess decides core legality, physical state and game-end semantics. Lila and the official Lichess variant page decide product-facing claims and published assets. The bracket-pocket FEN standard decides canonical project interchange. Pinned python-chess and chessops are independent differential references, not authorities when they conflict with the pinned primary source.

Engine, referee, runner, book and donor behavior are implementations under test.

## Initial state

Canonical Crazyhouse start FEN:

```text
rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1
```

`startpos` under `UCI_Variant=crazyhouse` resolves to this state. The empty bracket is emitted explicitly.

## Admitted searchable position domain

The release engine accepts physically conservative standard-start Crazyhouse states, including states with missing captured material, not arbitrary unbounded synthetic inventories.

Required validation:

- exactly one king of each color;
- no king in a pocket;
- no pawn on rank 1 or rank 8;
- no more than 64 occupied squares and no overlapping pieces;
- every promoted marker belongs to an occupied non-pawn, non-king square;
- `board pawns + pocket pawns + promoted markers <= 16`;
- unpromoted board knights plus pocket knights `<= 4`;
- unpromoted board bishops plus pocket bishops `<= 4`;
- unpromoted board rooks plus pocket rooks `<= 4`;
- unpromoted board queens plus pocket queens `<= 2`;
- kings plus those five physical-unit totals `<= 32`;
- each individual pocket count fits the authenticated legacy feature domain; pawn counts therefore never exceed 16 and other types inherit their tighter physical limits;
- side-to-move and checker state are legal under the same strict position contract;
- castling and en-passant fields pass the rules below.

This domain contains every position reachable from the standard start and the frozen 303-move stress fixture. Syntactically parseable inventories outside it are rejected with a stable diagnostic before evaluator routing. Rule-only test tools may carry explicitly marked malformed fixtures but cannot commit or search them.

The permissiveness of a third-party analysis FEN parser is not adopted as an unbounded memory or evaluator contract.

## Pockets and captures

Each side owns counts of pawn, knight, bishop, rook and queen. Pocket order is not semantic.

Capturing transfers the captured physical unit to the capturer's pocket with the capturer's color. A captured unit with promoted-pawn provenance becomes a pawn in the pocket, regardless of its visible board type. A captured king is never legal.

En-passant transfers the captured pawn to the capturer's pocket.

## Drops and check

- A pocket unit may be dropped only on an empty square.
- A pawn may not be dropped on rank 1 or rank 8.
- There is no doubled-pawn file restriction.
- There is no pawn-drop-mate prohibition.
- A dropped pawn may later make the ordinary two-square move from its starting rank when the orthodox conditions hold.
- A drop may give check or mate.
- A drop may answer one sliding check only on a square strictly between checker and king.
- A drop cannot answer double check or a non-sliding check.
- Any drop leaving the moving king checked is illegal.
- Checkmate and stalemate consider board moves and drops together.

## Promotion provenance

Promotion changes visible board type and sets a promoted-origin marker. The marker follows the unit on ordinary moves, is serialized as `~` immediately after the piece character, and is cleared on capture while causing the captured unit to enter the pocket as a pawn.

The marker survives FEN/EPD/PGN, make/undo, history, hashing, datagen records and admitted symmetries. Board-identical states that differ only in a marker are different physical and repetition states.

## Castling

Orthodox castling legality applies. Rights require the eligible original, unpromoted king and rook on their authenticated squares, a clear path and the ordinary unattacked king path.

A dropped rook or a promoted-origin rook never creates or authenticates a castling right. Moving/capturing the eligible king or rook removes rights normally. A FEN claiming an ineligible right is rejected rather than silently trusted.

Canonical output uses `KQkq` ordering. Chess960 is outside this Crazyhouse profile.

## En-passant

Orthodox en-passant legality applies. The parser accepts an en-passant target only when it is consistent with the immediately preceding double pawn step and at least one legal en-passant capture exists for the side to move. Otherwise the field is rejected; canonical committed output never retains a pseudo-only target.

Repetition identity includes only the legally relevant committed en-passant target. Make/undo restores board, pocket, target, counters, keys and provenance exactly.

## Counters

Six FEN fields are always present.

The halfmove counter is persisted but never creates a 50/75-move result. It updates as follows:

- reset to zero on a capture;
- reset to zero on an ordinary pawn move;
- reset to zero on a pawn drop;
- increment on a non-pawn drop;
- increment on every other non-capturing, non-pawn board move.

The fullmove number starts at one and increments after Black completes a move or drop.

Counters are diagnostics/interchange state. They are excluded from Crazyhouse TT/repetition identity and terminal adjudication.

## Draws, terminal result and precedence

The profile has no 50-move draw, no 75-move draw and no insufficient-material draw. Syzygy is not applicable.

Stalemate is a draw. Automatic fivefold repetition is a draw. Lichess exposes threefold repetition as a claim; an engine match may use automatic third occurrence only as a preregistered symmetric immediate-claim proxy.

Terminal precedence after a completed legal move is:

1. checkmate;
2. any future profile-specific win condition introduced by an authority revision;
3. stalemate;
4. automatic fivefold repetition;
5. optional threefold claim proxy under the frozen match policy.

A draw predicate cannot override checkmate delivered by the same completed move.

## Repetition identity and history

Repetition identity contains board pieces/colors, side to move, castling rights, legally relevant en-passant, both pockets by owner/type, and all promoted-origin markers.

Legality needs current physical state. Result computation additionally needs the ordered full-state repetition sequence and claim policy. A rule50 counter cannot bound the history because capture-to-pocket followed by drops can restore a complete earlier state.

## FEN

Canonical output places bracket pockets immediately after the board field. Inside brackets, output order is repeated White `P N B R Q`, then repeated Black `p n b r q`; zero counts are omitted. Empty pockets are `[]`.

The parser also accepts the pinned Lichess slash-pocket input form and normalizes it to bracket output. It accepts ASCII pocket letters only, ignores no unknown character, and rejects kings. Promoted board units require `~` after the piece character. Input is transactional and bounded.

## EPD

EPD begins with the same canonical physical FEN fields. Operations are explicit key/value fields and may not hide pockets, provenance, terminal reason or counters. Unknown required operations fail closed.

## PGN

PGN uses `Variant "Crazyhouse"`. A non-start initial state requires authenticated `SetUp "1"` and canonical `FEN`. Drop SAN/UCI notation, clocks, result and terminal reason are retained. Replay is transactional and must reproduce the same final physical state and result.

## UCI

- `setoption name UCI_Variant value crazyhouse` selects this profile.
- Canonical drop output is uppercase `<PIECE>@<square>`, for example `P@e4`.
- Input accepts upper/lower ASCII role letters and normalizes output to uppercase.
- `position startpos` is variant-dependent.
- `position fen ... moves ...` parses and replays into temporary storage, then commits atomically.
- A failed command latches the current position epoch invalid; `go` is rejected until a new valid position commits.
- Every fresh process and every match game proves variant, evaluator and starting state; prior process state is never assumed.

## Option persistence

Variant/evaluator configuration persists only within the current process after an `isready` commit. Match runners must set and verify it for every game. Changing variant or evaluator stops search, invalidates the position, clears TT/histories/evaluation generations and requires a new `isready`.

## Admitted symmetry

The first admitted augmentation is vertical rank reflection plus color swap. It transforms board colors/ranks, side to move, pocket ownership, castling color, en-passant rank, promoted squares, move, result and terminal reason. It is involutive.

File reflection, 180-degree rotation and other transforms are not admitted with castling rights until separately proved. Opening-root identity alone never establishes dataset split independence because trajectories can transpose.

## Mandatory fixture families

The shared engine/reference/referee corpus covers pockets and both FEN dialects; every drop type and restriction; check blocks/double checks/drop mates; capture-to-hand and promoted demotion; promotions and marker motion; both castlings and ineligible dropped/promoted rooks; en-passant creation/capture/pins/key/undo; counter updates by move class; threefold proxy/fivefold/no-50/no-insufficient/checkmate precedence/stalemate; FEN/EPD/PGN/UCI round trips and option persistence; admitted symmetry; make/undo/null; and perft roots for every material transition.

The actual match referee must pass the same relevant corpus. An engine pass cannot compensate for referee disagreement.
