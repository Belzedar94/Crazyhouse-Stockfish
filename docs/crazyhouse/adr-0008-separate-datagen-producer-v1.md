# ADR 0008: Separate Crazyhouse physical datagen producer V1

- Status: accepted before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Physical schema: `crazyhouse-physical-v1`

## Context

The normal Crazyhouse UCI executable is a match engine. Giving it an implicit datagen mode would make a missing option, stale wrapper, or wrong executable capable of falling back to ordinary UCI or standard chess while still producing plausible files. The frozen G8 contract therefore requires a separate artifact whose role, source, bytes, schema and transaction capabilities are challenged before any output is opened.

Atomic-Stockfish was inspected only as a method reference for target separation, private object ownership and crash-safe publication. No Atomic rule value, record format, campaign setting, network, book, target identity or variant result is inherited. This producer is specified only by the Crazyhouse rule profile, G8 schema and the fixtures frozen below.

## Decision

The production artifact is `crazyhouse-stockfish-datagen`, built by a dedicated Make target with its own `main` and datagen implementation. The normal `stockfish` target excludes those sources and must reject the datagen capability request. The producer links the production Crazyhouse `Position`, legal move generator, make/undo, repetition and terminal code; it owns no alternative move-legality implementation.

The first generation ingress is a strict, LF-only TSV trajectory stream. It contains fresh-root complete trajectories, explicit game and trajectory UUIDs, claim policy, absolute-White result, terminal reason, nonstandard-root declaration, exact root FEN, every UCI move and one synthetic G0 teacher score token per physical record. Missing, duplicate, malformed, illegal, discontinuous or count-mismatched input aborts before output. This ingress is evaluator-independent and is not a feature-row format.

For every outgoing move the producer:

1. resolves it through the production legal move list;
2. snapshots canonical FEN, key, pockets, promoted mask and repetition count;
3. makes and undoes the move and requires exact restoration;
4. makes the same move persistently in the trajectory state;
5. derives the next raw en-passant target from the physical double-pawn move while taking the effective target from `Position`.

Checkmate, stalemate, fivefold and the declared immediate-threefold proxy must agree with the production terminal API. Resignation and draw-adjudication markers are admitted only while the engine status remains ongoing. They are G0 fixtures, not engine-discovered outcomes.

## Capability and identity

The only positive capability invocation is:

`--datagen-capabilities-v1 --challenge <32-lowercase-hex>`

It emits exactly one canonical JSON line and binds the executable's own bytes and SHA-256, full clean source commit/tree/src-tree, build recipe, toolchain, rule profile, schema, record enums and transaction semantics. A dirty build reports itself honestly and is inadmissible. Generation recomputes the same response for its fresh challenge; the response digest enters both canonical provenance and the chunk header.

The checked-in capability contract SHA-256 remains `dc6af06c3d18fb2ff06e27e35ab691e35555ef03a5948b23cb2a198e6b89eb96`. The schema SHA-256 remains `c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55`.

## Transaction

Admission and complete engine replay occur before any output path is opened. A successful attempt then:

- creates chunk, capability and provenance partials with exclusive-create semantics;
- writes exact bytes, flushes and fsyncs every file;
- verifies framing, CRC32C and all SHA-256 bindings from the partial chunk;
- publishes both sidecars without replacement;
- atomically publishes the `.chp1` chunk last as the commit point.

The producer never overwrites or deletes a prior final or partial. A killed attempt remains quarantined. Reusing its chunk ID is rejected; retry requires a new chunk ID and fresh output namespace. A G0-only pause hook is admitted only when an explicit fault-injection environment variable is present, so the kill boundary can be observed deterministically.

## Frozen G0 corpus

`tests/crazyhouse/data/crazyhouse-datagen-g0-trajectories-v1.tsv` is 1,613 bytes with SHA-256 `4113b930d08d6037de8667b9919f8944882d527856b860aaf92bbf1088aa0cdd`. It contains 11 complete trajectories and 42 records covering:

- a standard root and raw-but-ineffective en-passant after `e2e4`;
- drop consumption and pocket ownership;
- promoted-origin movement and capture demotion;
- checkmate and stalemate;
- legal en-passant and capture-to-pocket;
- orthodox castling-right consumption;
- promotion provenance;
- fresh-history fivefold and an explicitly separate immediate-threefold proxy.

The opening-selection policy is the LF-terminated UTF-8 statement `Crazyhouse G0 trajectories v1: frozen authority fixtures; no engine selection; no match-result selection; complete trajectory only; training inadmissible.` Its SHA-256 is `e5b39bd15c78b00ce0f6acc01da49103e71685c95f7b6fbde09334933d8bfb18`.

## G0 acceptance

G9 remains closed until a clean-export build passes all of the following with immutable raw logs and hashes:

- challenged positive capability and every frozen negative case;
- normal-engine negative control and no shared datagen entry point;
- exact 11/42 count, 256/256/128 framing, CRC32C and digest verification;
- byte round-trip through the G8 codec plus an engine-independent verifier;
- legal replay, make/undo restoration, terminal/result agreement and rare-state coverage;
- duplicate game, trajectory, chunk and output rejection;
- deterministic kill, retained partial quarantine, same-ID rejection and new-ID retry;
- complete producer, input, output, sidecar, source, toolchain and recipe hashes.

No G0 artifact is training-admissible. It carries no Elo, model-selection, official OpenBench or release claim.

## Rejected alternatives

- A datagen option in the normal UCI engine.
- Python-only production generation that does not execute the product `Position` transition path.
- NNUE feature rows or board-only records as canonical source.
- Treating filename, extension, process exit or a capability response as legality proof.
- Deleting interrupted partials or retrying under the same chunk ID.
- Copying another variant's schema, referee, book, network or campaign constants.

