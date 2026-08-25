# ADR 0009: Fail-closed Crazyhouse OpenBench onboarding

- Status: accepted before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Product source boundary: official-Stockfish descendant only

## Context

OpenBench public-engine builds invoke `make` without an explicit target, pass the requested executable name through `EXE`, the full source identity through `GIT_SHA_FULL`, and the assigned network through `EVALFILE`. The current Crazyhouse product has no matching default target, requires an external `CrazyhouseEvalFile`, and deliberately rejects the orthodox Stockfish benchmark while Crazyhouse is active. A successful compilation under that mismatch would not prove that OpenBench can load the registered legacy evaluator or obtain a Crazyhouse-specific deterministic signature.

The deployed `Client/cutechess-ob` artifacts in the current OpenBench tree are the Horde-specific builds. Their hashes and ancestry are already classified as `KNOWN_NONCONFORMING` for Crazyhouse. The qualified Crazyhouse referee instead descends from clean upstream Cute Chess commit `24d4301152fb92ac442425e083a2658225f80720` through project-authored Crazyhouse commits. No Horde referee commit, patch, binary, rule, test value or result may enter the Crazyhouse path.

## Decision

An OpenBench play build is admitted only when all of these inputs are present and exact:

1. `GIT_SHA_FULL` contains forty hexadecimal characters and, when Git metadata is available, equals the checked-out clean source commit.
2. `EVALFILE` names a regular file of exactly 58,534,811 bytes whose SHA-256 is `8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.
3. The build role is `play`; a true `OPENBENCH_DATAGEN=1` selects the separate physical producer and can never link or rename the normal UCI executable into that role.
4. The resulting play executable carries the exact legacy bytes as a dedicated Crazyhouse embedded object. It loads those bytes only through the already certified `LegacyCrazyhouseNetworkV1::load_bytes` parser. Missing, malformed, incompatible or wrong-digest bytes abort the build or route with no filesystem, orthodox-NNUE or evaluation fallback.
5. The requested `EXE` is the only admitted output name. The ordinary developer targets and external `CrazyhouseEvalFile` workflow remain available outside the OpenBench build contract.

The embedded evaluator is selected by a private, explicit route token exposed as the default `CrazyhouseEvalFile` value only when those bytes are compiled into the executable. An ordinary build without an embedded legacy artifact retains the empty default and therefore retains the existing fail-closed behavior.

The no-argument Crazyhouse `bench` command is bound to the twelve positions and depth in `tests/crazyhouse/p10-openbench-onboarding-v1.json`. It runs at one thread and 16 MiB hash through the real Crazyhouse search and registered legacy evaluator. Orthodox chess keeps the upstream benchmark corpus and defaults. The observed node total is not chosen in advance; it becomes an immutable expected signature only in a hash-pinned post-implementation addendum after two clean exports agree. The implementation commit message must record `Bench: <nodes>`.

## Referee and routing

OpenBench must add `LICHESS_CRAZYHOUSE_2026_08_12` as an explicit server and client contract. A book or engine name may help diagnose routing, but neither is authority: the persisted contract must agree across the workload, both engines and the book. Crazyhouse without that contract, an unknown contract, or any inferred/declared conflict is rejected instead of falling through to standard chess.

Contract-specific referee selection uses a path under `Client/referees/<contract>/<platform>/`, not the shared Horde `cutechess-ob` path. The Windows pin is the already qualified local executable SHA-256 `f465025b2ad21526e2cbab2b7da1a231ff3d64f6e8a01a0be5963f525a0bddae`. No Linux pin exists yet, so Linux workers must refuse Crazyhouse until a clean, reproducible Linux build passes the same corpus and is recorded by an addendum. The referee must challenge both engines with the exact Crazyhouse capability nonce handshake before each game.

The candidate public opening name is `CRAZYHOUSE_openings_v1.epd`. It may only be a byte-identical alias of the frozen 1,024-root local corpus, 100,204 bytes, SHA-256 `a8976a380a6cc4b3a1a6aae3bf14249b2ab6d1bac6cf4a2715625d7c01747603`. Renaming does not create new scientific provenance.

## Activation boundary

The public repository `Belzedar94/Crazyhouse-Stockfish` returned HTTP 404 at the frozen boundary. Local engine/OpenBench branches, fixtures, tests, clean exports and receipts are authorized preparation. Configuration activation, repository creation or publication, referee artifact publication and the first official production canary remain absent until their owner/resource publication decisions. The P7 same-network local strength ladder must pass all three owner-fixed rungs before any OpenBench workload is submitted.

G10 requires a production canary at `https://belzedar.duckdns.org` that proves the exact source, network, contract, referee, book and `-variant crazyhouse` routing in its assignment, raw logs and PGN. A queued, cancelled, locally simulated or standard-routed workload has no G10 credit.

## Rejected alternatives

- Reusing or replacing the shared Horde referee binary.
- Treating Cute Chess's advertised `crazyhouse` token as referee conformance.
- Supplying the legacy network only by filename, extension, runtime working directory or optional UCI configuration.
- Making a corrupt embedded network fall back to the orthodox evaluator or an external file.
- Running the orthodox Stockfish benchmark and labelling its node count Crazyhouse.
- Letting `OPENBENCH_DATAGEN=1` produce the match engine.
- Activating a draft engine/book configuration before public source and artifact authentication.
- Assigning strength, DATAGEN or release credit to the build or canary.

