# ADR 0007: Crazyhouse physical datagen V1

- Status: accepted and frozen before production data
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Product source identity: `c0c11978abbe0cc7c4d80c90426b00eae0aa712c`

## Context

NNUE feature rows are evaluator-dependent projections. They cannot preserve enough information to change architectures, reproduce rule decisions, audit labels, or distinguish board-identical Crazyhouse states whose pockets or promoted-origin markers differ. The canonical training source therefore has to be physical Crazyhouse state with complete trajectory and production identity.

The selected rule profile also makes ordinary chess shortcuts unsafe. A repetition identity includes pockets and promoted provenance; the halfmove clock does not bound history; a pseudo en-passant target can normalize away; a captured promoted unit becomes a pocket pawn; and threefold is a claim unless a separate immediate-claim proxy is declared.

## Decision

The first on-disk format is `crazyhouse-physical-v1`. Its exact schema bytes have SHA-256 `c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55`.

| Frame | Bytes | Integrity and purpose |
|---|---:|---|
| Header | 256 | Magic/version, exact count, chunk/campaign IDs, rule/schema/provenance/payload/capability digests, CRC32C |
| Record | 256 | Physical state, outgoing move or terminal marker, labels, search metadata, position/history/provenance digests, CRC32C |
| Footer | 128 | Commit marker, count and payload length, payload/header digests, chunk ID, CRC32C |

All integers are little-endian. Unknown flags, enums, nonzero reserved bytes, partial frames, appended bytes, digest drift and CRC drift are invalid. An empty chunk is invalid.

### Physical record

Each record contains:

- a nibble board in `a1` through `h8` order;
- ten absolute-color pocket counts in White `P N B R Q`, then Black `p n b r q` order;
- a 64-bit promoted-origin mask independent of the visible board type;
- side to move, orthodox castling rights, raw and effective en-passant targets, halfmove/fullmove counters, repetition occurrences and claim policy;
- an engine-ABI-independent four-byte move wire for normal, promotion, en-passant, castling or drop moves;
- absolute-White and side-to-move result labels, terminal reason, exact side-to-move teacher score and search metadata;
- global sequence, game ID, trajectory ID, ply, physical-position digest, ordered-history digest and chunk-provenance digest.

Pawns on ranks one or eight are invalid. A castling right requires the corresponding unpromoted orthodox king and rook. A raw en-passant target must be physically consistent with the preceding double pawn move. Only a target with at least one fully legal capture enters `effective_en_passant_square` and repetition identity. The producer must still prove complete move legality at G0; the byte codec's basic coherence checks are not a legal-move oracle.

For standard-start trajectories, pawn-origin and unpromoted piece-origin conservation is enforced across board, both pockets and promoted markers. An explicitly authorized nonstandard-root flag is required outside that domain.

### History and results

Every trajectory is wholly contained in one chunk. It starts at `ply=0` with a fresh repetition history and occurrence count one, contains every subsequent ply in order, and ends exactly once with a terminal record. Hidden pre-root history, gaps, post-terminal records and cross-chunk continuation are forbidden.

The position digest covers board, side, castling, effective en-passant, pockets and promoted mask. The ordered history chain covers every position digest and outgoing move from the fresh root. The stored repetition occurrence count must equal the count reconstructed from that physical history. This makes fivefold and an optional threefold proxy auditable without using the halfmove clock.

`game_result_white` is always from White's perspective. `result_side_to_move` equals that value for White to move and its negation for Black to move. Teacher scores are always side-to-move values. Every nonterminal record requires an exact teacher label; terminal records forbid a teacher label. A record's network-teacher bit must agree with the chunk provenance.

Crashes, illegal moves, time losses, stalls, disconnects and safety-limit exits never become terminal training labels. Their complete games are quarantined.

### Provenance

One canonical UTF-8 JSON provenance object is bound to the header and every record by SHA-256. It includes full clean source commit/tree identities; producer artifact bytes and digest; challenged capability response; toolchain and build-recipe digests; teacher artifact/settings and network-use declaration; network bytes/format/license when used; opening identity and selection policy; campaign/chunk/seed/settings; adjudication; and invalid-game policy.

A classical teacher must name its exact executable and explicitly declare no network. An unused network has zero bytes and null path, format, license and digest. A path is never artifact identity. A `.nnue` extension is never compatibility evidence.

### Producer capability boundary

The normal UCI engine is not a datagen producer. A separate artifact must answer the nonce-bound contract in `tests/crazyhouse/datagen-capability-v1.json` before any output file is opened. The response is canonical one-line JSON and binds its own bytes, clean source, toolchain, rule profile, physical schema, supported flags/enums and transaction capabilities. The exact response digest enters both provenance and the chunk header.

Missing, stale, malformed, dirty, wrong-role or mismatched capability responses abort before output. A production role must advertise fsync, atomic rename, partial quarantine, unique retry chunk IDs and production authorization. The checked-in golden response deliberately identifies a schema reference codec, sets those production capabilities false and sets `production_generation_authorized=false`.

### Transaction and symmetry

Production writers create a fresh `.partial` file exclusively, write records while hashing, fsync, finalize the exact committed header, append the binding footer, fsync, independently verify, then atomically rename to `.chp1`. They never append or overwrite. Failed and interrupted attempts remain quarantined; a retry receives a new chunk ID.

The only initially admitted augmentation is vertical rank reflection plus color swap. It transforms board colors/ranks, pocket owners, side, castling-color bits, raw/effective en-passant, promoted-mask squares, move squares and absolute-White result. Side-to-move result and teacher score remain unchanged. Position and history digests are recomputed. Other transforms require a new proved contract.

## Golden evidence

The frozen manifest contains 42 records in 11 complete trajectories. It covers standard material, drops, pocket ownership, promoted-marker motion, promoted capture demotion, legal and normalized-away en-passant, castling, promotion, checkmate, stalemate, resignation, fresh-history fivefold and the separately declared threefold proxy. The exact chunk SHA-256 is `379f6c7df217cae74c1da075af7d37a82719792fd99a9c9118fe9e7b93ee08aa`.

The reference codec and its adversarial unit suite are checked by a second implementation that imports neither. That verifier independently reconstructs all record bytes, histories, labels, CRC32C values, header/footer and chunk hashes using only the standard library.

Normative inputs at this freeze are:

- schema: `c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55`;
- reference codec: `04876106c165f29ab6ee511fc02a3b2790cf9030bdfb216dbe7cafc44ce54d98`;
- capability contract: `dc6af06c3d18fb2ff06e27e35ab691e35555ef03a5948b23cb2a198e6b89eb96`;
- golden capability response: `601dcf0529387c435422a7a045c9d42eb45d4a5514283a8fa1128acfa6261343`;
- golden provenance: `6564942cbffad222ba94dc2ad62a20ea0d6e1d822b9d0279a515c8d2ae5ae11f`;
- golden manifest: `94cd50961d8e51478e55a82cd4e0770d418a30483b3c5d120a470f7eb2efccac`.

## Rejected alternatives

- NNUE feature rows as canonical data: evaluator-dependent and unable to preserve all variant state.
- A board-only record: loses pockets, promoted provenance and result-relevant history.
- Engine-native move integers: unstable ABI rather than a versioned wire format.
- Opening-root-only split identity: transpositions can cross splits.
- Hidden history summarized by a rule50 counter or unverified repetition number: incorrect for Crazyhouse.
- A datagen mode in the normal engine with implicit fallback: violates producer separation and fail-closed routing.
- Treating the golden reference codec as a production producer: its capability response explicitly denies that role.

## Consequences and next gate

This decision can close G8 only after the exact committed files and an independent verification receipt are authenticated. It does not close G9. P9 must still implement the separate producer and pass the full local G0 matrix: real engine make/undo and legal replay, exact count/framing, terminal replay, rare-state coverage, duplicate and cross-split audits, kill/retry, partial quarantine and artifact hashes. No data generated by the schema goldens is training-admissible, and no result here is strength, OpenBench, model-selection or release evidence.
