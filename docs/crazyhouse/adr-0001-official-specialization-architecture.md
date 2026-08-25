# ADR-0001: Focused Crazyhouse specialization on official Stockfish

- Status: Accepted for implementation
- Date: 2026-08-13
- Evidence class: `E1_ENGINEERING`
- Source baseline: official Stockfish `5062aee519a1ba262d472d8ab139851ced56573e`
- Source tree: `3b51a6c6d0e5d0fc44a4fde457d270340cb35280`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`

## Context

The product must start from the latest verified development revision of official Stockfish. Fairy-Stockfish is explicitly rejected as a source base. Its rules, protocol behavior, legacy network implementation and frozen fixtures may be inspected as evidence or donor material, but its generic variant framework, search architecture and source ancestry are not admitted.

The official source has a compact 16-bit move, fixed 256-entry move arrays, orthodox-only `Position` state, rule50-adjusted TT identity, orthodox repetition shortcuts, one current NNUE architecture and no variant protocol. A valid independent Crazyhouse fixture already has 303 legal moves, so retaining the official fixed move-list ceiling would be a demonstrated correctness bug.

The architecture was reviewed through an authenticated ChatGPT Pro browser session after a dry-run/bounded orchestration sequence. The advice is consultative. Every accepted point below was checked against the pinned official source.

## Decision 1: source and product boundary

The only permitted release ancestry is official Stockfish `5062aee519a1ba262d472d8ab139851ced56573e` followed by reviewable Crazyhouse-specific commits.

The implementation introduces exactly two rule modes:

```cpp
enum class Ruleset : std::uint8_t {
    CHESS,
    CRAZYHOUSE
};
```

This is a focused specialization, not a generic variant framework. `Ruleset` is immutable for a committed `Position`. Changing the UCI variant stops and joins search, invalidates the committed position epoch, binds the required evaluator, rebuilds worker-local evaluation state, and clears TT and histories before readiness can succeed.

## Decision 2: retain the 16-bit move ABI

`Move` remains 16 bits. Crazyhouse uses the canonical internal encoding `CH_DROP16_V1`:

| Bits | Meaning | Required value |
|---|---|---|
| 15-14 | Existing orthodox move class | `00` |
| 13-12 | Drop marker | `11` |
| 11-9 | Fixed synthetic-source prefix | `111` |
| 8-6 | Pocket piece code | `000` pawn, `001` knight, `010` bishop, `011` rook, `100` queen |
| 5-0 | Destination | `0..63` |

Constants and canonical ranges:

```cpp
constexpr std::uint16_t DROP_TAG         = 0x3000;
constexpr int           DROP_SOURCE_BASE = 56;

// Pawn   0x3E00..0x3E3F
// Knight 0x3E40..0x3E7F
// Bishop 0x3E80..0x3EBF
// Rook   0x3EC0..0x3EFF
// Queen  0x3F00..0x3F3F
//        0x3F40..0x3FFF are reserved and invalid
```

Canonical normal moves use bits 12-15 as zero. Promotion, en-passant and castling have an orthodox class in bits 14-15. `Move::none()` and `Move::null()` do not overlap the drop ranges.

Because the official `type_of()` masks only bits 14-15, a drop would otherwise look normal. The semantic API is therefore:

```cpp
enum class MoveKind : std::uint8_t {
    INVALID,
    NORMAL,
    PROMOTION,
    EN_PASSANT,
    CASTLING,
    DROP
};
```

Required operations are `Move::make_drop(PieceType, Square)`, `Move::is_drop()`, `Move::drop_piece_type()`, `Move::kind()` and a ruleset-aware structural validator. `from_sq()` debug-asserts that the semantic kind has an origin. `promotion_type()` debug-asserts promotion. No drop may reach code that reads a synthetic A8-E8 origin.

Structural validation rejects all noncanonical raw forms, including normal-class values with marker payload `01` or `10`, reserved drop payloads, drops in chess mode, nonzero promotion payload on en-passant/castling, and same-square normal moves except the two explicit sentinels.

Widening to 32 bits is rejected because it would alter TT move packing, `ExtMove`, histories, root storage and sorting code without solving a rule problem that the canonical 16-bit encoding cannot represent.

## Decision 3: Crazyhouse move storage is growable and non-truncating

Orthodox `MAX_MOVES == 256` remains isolated to the unchanged chess fast path. It is not a Crazyhouse limit.

Crazyhouse uses a contiguous small-vector-style `CrazyhouseMoveBuffer<T, 256>`:

- 256 inline entries are an optimization, not a correctness ceiling;
- growth spills into contiguous dynamic storage;
- `512` may be used only as a reserve hint;
- every append is checked; raw unbounded `*end++` is forbidden on the Crazyhouse path;
- allocation failure, `size_t` overflow or impossible growth aborts rather than truncating or falling back;
- stage boundaries are indices, not pointers retained across reallocation;
- each live `MovePicker` owns its buffer, or a worker arena is partitioned by search ply so parent and child pickers cannot alias;
- debug builds reject duplicate raw moves in a generated set.

The following capacities are decoupled and audited separately: `MoveList`, every `MovePicker` stage, root moves, legal filtering, qsearch/evasions, perft, UCI replay matching, capture/quiet partitions, move-count-indexed reductions and the accidental `MultiPV == MAX_MOVES` option maximum.

The 303-move fixture proves `required capacity >= 303` and is sufficient to freeze growable storage. It does not prove a numeric maximum. A fixed Crazyhouse array is forbidden until a mathematical bound over the accepted FEN domain is independently proved.

Before drop generation, a synthetic capacity gate must append, score, partition, sort and iterate 303, 512 and at least 1,024 distinct sentinels; force nested parent/child spill; and pass memory instrumentation without truncation, overwrite, invalidation or stage corruption. Chess must still select the fixed path and retain the pinned deterministic control signature.

## Decision 4: one canonical per-ply Crazyhouse state

`Position` is the semantic owner and invariant enforcer. Current pocket counts and promoted provenance live exactly once in the prefix of `StateInfo` that official `do_move()` copies before `StateInfo::key`:

```cpp
struct PocketCounts {
    std::uint8_t count[COLOR_NB][5]; // pawn, knight, bishop, rook, queen
};

struct CrazyhouseState {
    PocketCounts pockets;
    Bitboard     promoted;
    Key          pocketKey;
    Key          promotedKey;
};
```

`StateInfo` remains trivially copyable. `Position` exposes const accessors and checked mutation helpers used by FEN setup, `do_move()` and test reconstruction. No second mutable pocket/provenance copy is allowed in `Position`.

This makes normal moves and null moves inherit state, undo restore it by selecting `st->previous`, and setup history retain exact variant state. Undo never attempts inverse pocket arithmetic.

Required invariants include:

- pockets contain only pawn, knight, bishop, rook and queen;
- kings are never pocketed or dropped;
- counts satisfy ADR-0002's physical-domain limits;
- `promoted` is a subset of occupied non-pawn, non-king squares;
- dropped units begin without a promoted marker;
- a newly promoted pawn gains the marker and it follows the unit;
- capture of a marked unit clears the marker and pockets a pawn;
- en-passant pockets a pawn;
- a promoted-origin or dropped rook cannot authenticate castling rights.

## Decision 5: full identity includes the ruleset, pockets and provenance

Crazyhouse adds independent Zobrist components:

- one nonzero Crazyhouse ruleset salt;
- exact pocket count by color, type and admitted count;
- promoted-origin marker by square.

Chess adds no salt and retains its current identity path.

The raw Crazyhouse key is the orthodox raw board key XOR the ruleset, pocket and promoted components. Pockets are not folded into `materialKey`, `pawnKey`, `minorPieceKey` or `nonPawnKey`, whose official consumers retain orthodox meanings.

For Crazyhouse:

- `Position::key()` returns the complete raw key without rule50 adjustment;
- TT prefetch occurs only after the complete board/pocket/provenance transition is known;
- speculative `key_after()` is disabled until it exactly matches full transition reconstruction;
- repetition compares complete raw keys;
- repetition history scans to `pliesFromNull`, not `rule50`;
- the orthodox cuckoo `upcoming_repetition()` shortcut is disabled until a Crazyhouse proof exists;
- TT, histories and accumulator generations are cleared after ruleset or evaluator changes.

Debug builds recompute and compare every component after setup, make, undo and null transitions.

## Decision 6: make, undo, null and feature dirties

`do_move()` classifies and structurally validates before any origin-square access. It derives the complete transition from the old board/state, copies the StateInfo prefix, updates board and Crazyhouse component keys, assigns the final raw key, and only then permits TT prefetch.

Undo restores physical board squares using the current move/captured piece, then selects `st->previous`; it does not edit pockets or provenance inversely.

Null move copies pockets, provenance and component keys unchanged, clears en-passant, toggles side, resets `pliesFromNull`, and emits no pocket or square-state dirty event. Null moves are search devices and do not enter game-history adjudication.

The official external accumulator stack remains outside `StateInfo`. Variant deltas extend the current `Dirties` boundary with exact before/after pocket counts and before/after piece/provenance square states. The initial legacy evaluator ignores incremental deltas and performs scalar full refresh. A later incremental backend must consume these deltas without inventing a source square for a drop.

## Decision 7: evaluator routing is explicit and separate

There are two incompatible backends:

| Ruleset | Required backend | UCI file option | Initial accumulator policy |
|---|---|---|---|
| `CHESS` | current official `Eval::NNUE::Network` | `EvalFile` | current official stack/caches |
| `CRAZYHOUSE` | `LegacyCrazyhouseNetworkV1` | `CrazyhouseEvalFile` | scalar full refresh; dedicated stack later |

The current official network class is not made polymorphic and does not contain legacy weights. An engine-level non-owning evaluator binding selects one immutable backend before workers start. Worker-local evaluation context owns backend-specific stacks and caches.

Crossed or failed routes are fatal: official bytes in `CrazyhouseEvalFile`, legacy bytes in `EvalFile`, unknown header, missing file, wrong length/hash, corrupt/truncated/oversized input, incompatible dimensions or scalar golden-vector mismatch. Failure never retains an earlier backend and never falls back to official NNUE, classical material or another file.

The mandatory legacy identity is:

- 58,534,811 bytes;
- SHA-256 `8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`;
- container `0x7af32f20`;
- architecture `0x3c103e72`;
- transformer `0x5f2348b8`;
- feature family `HalfKAv2Variants`.

Headers are parser identity, not numerical compatibility. Feature indexing, orientation, pocket rows, provenance rows, tensor dimensions, signed integer order, clipping, shifts, rounding, output scale and side-to-move perspective require an independent numerical ADR and golden vectors before evaluator behavior is added.

The official evaluation wrapper's board-material blend, rule50 damping and WDL calibration do not silently post-process the legacy output.

## Decision 8: protocol and committed configuration

The public product exposes only `chess` and `crazyhouse` through `UCI_Variant`. The specialized product default is `crazyhouse`; `CrazyhouseEvalFile` defaults to the packaged legacy identity/byte-identical alias once packaging exists. Chess regression controls explicitly select `chess` and retain `EvalFile`.

`setoption` changes are staged while idle and committed at `isready`. `go` enforces the same readiness gate. A variant or evaluator change during search first stops and joins the exact search. A failed `position` command is transactional: it never partially replaces state, latches the current epoch invalid, and rejects `go` until a later valid position commits.

Rule-only legal-list, perft, hashing and round-trip commands do not require a loaded evaluator. Search always requires the exact ruleset/backend route.

The candidate release alias `Crazyhouse_v1.nnue` is accepted only when byte-identical to the frozen legacy artifact. An alias never changes champion, source default or internal identity.

## Decision 9: conservative initial search boundary

The following are rule-correctness changes, not experiments: drop-aware legality/check/evasions, full repetition identity, raw TT key, qsearch drop evasions, terminal predicates and no-move handling.

The following are disabled in Crazyhouse until independently justified: Syzygy root/interior probes, 50/75-move and insufficient-material outcomes, rule50 TT adjustment, orthodox upcoming-cycle shortcuts, and any speculative key helper that omits pocket/provenance state.

SEE pruning, board-material-conditioned pruning, drop ordering, checking drops outside in-check qsearch and drop-specific histories begin conservatively. Their later restoration or tuning is one-variable P7/P14 experimental work, never folded into the rule port.

WDL output remains disabled until a Crazyhouse calibration is frozen.

## Implementation order

1. Capacity-only growable-buffer mechanics and recursive ownership, with chess control unchanged.
2. Move ABI, exhaustive raw classification and UCI drop codec.
3. Transactional FEN parsing plus canonical pockets/provenance state.
4. Full state identity and recomputation parity.
5. Make/undo/null for every physical transition.
6. Drop generation, checks, evasions, legality and perft.
7. Repetition and terminal policy; explicit Syzygy/draw bypass.
8. Standalone bounded legacy container parser.
9. Independently derived scalar full-refresh numerical parity.
10. Search binding with conservative Crazyhouse policies.
11. Incremental evaluator parity.
12. Paired engineering performance, then separately preregistered strength.

Mechanical refactors and behavior changes use separate commits. No behavior slice advances without its fixture and digest first.

## Stop-ship conditions

Stop the gate if any legal set can overflow, truncate or silently cap; a drop reaches an origin-square consumer; complete keys collide across pocket/provenance states; make/undo/null fails exact state equality; incremental and reconstructed keys differ; TT prefetch sees incomplete identity; repetition still depends on rule50/cuckoo shortcuts; a stale position can be searched after parse failure; Crazyhouse invokes Syzygy or orthodox terminal draws; legacy bytes can enter the official network; any evaluator failure falls back; numerical parity is absent; or evidence classes are conflated.

## Evidence boundary

Fixtures and differential tests establish rules. Exact integer comparisons establish evaluator compatibility. Repeated paired measurements establish engineering performance. Only preregistered matches establish strength. This ADR establishes none of those later results by itself.
