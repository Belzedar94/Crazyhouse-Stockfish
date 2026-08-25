# ADR 0008 addendum 001: Search-backed Crazyhouse self-play ingress V1

- Status: accepted before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Parent: `ADR 0008`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Physical schema: `crazyhouse-physical-v1`

## Context

G9 authenticated the separate producer, physical codec, legality replay and transaction boundary with frozen trajectories. That result intentionally did not produce training data. The producer still cannot satisfy OpenBench's generic DATAGEN launch contract: OpenBench starts the generator without arguments and writes one rendered command followed by `quit` to stdin, while the G9 artifact accepts only direct CLI arguments and requires an already completed trajectory TSV.

The P11 bridge must generate complete physical trajectories itself, using the exact product Crazyhouse position and search implementation, while preserving the normal engine's lack of any DATAGEN command. It must not reinterpret `{COUNT}` as games, silently truncate a trajectory, label a safety stop as a draw, or accept a merely named `.nnue` file.

Atomic-Stockfish and Horde-Stockfish were inspected only for the method of linking a synchronous search entry point into a separate generator build. No rule value, format, network, book, search setting, campaign count, randomization setting, adjudication rule or result was inherited.

## Decision

The existing `crazyhouse-stockfish-datagen` artifact gains a second, search-backed ingress. Its stdin command is `crazyhouse_generate_physical_v1`; direct CLI capability and frozen-trajectory G0 modes remain available and byte-compatible. The normal `stockfish` target still compiles no generator entry point and must reject this command.

Only the separate target defines `CRAZYHOUSE_DATA_GENERATOR`. Under that define, `Search::Worker` exposes a synchronous fixed-depth or fixed-node root search that returns the raw exact value, PV, node count, completed depth, selective depth and bound status without UCI callbacks or time management. The Crazyhouse legacy incremental accumulator is reset at every independent root and follows the same make/undo stack used by product search. Any root or MultiPV bound, absent PV, illegal selected move, route drift or accumulator failure aborts the attempt.

The producer authenticates and loads the registered legacy network before opening output. V1 admits only SHA-256 `8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`, 58,534,811 bytes. The teacher is the clean generator executable plus that exact network; `network_used=true`. Non-mate values are stored as exact side-to-move centipawns through the product `Score` conversion. Mate values are stored as signed mate plies. Terminal records have no teacher label.

Each candidate game starts from one authenticated book root with fresh repetition history. The producer searches and advances until the production terminal API reports checkmate, stalemate or fivefold repetition. V1 uses `AUTOMATIC_ONLY`; it does not claim threefold. A max-ply exit, crash, stop, illegal move, missing exact score or nonterminal exhaustion quarantines the entire candidate game and never becomes a terminal label.

`{COUNT}` remains an exact physical-record target. A complete candidate trajectory is accepted only if all of its records fit the remaining quota. Oversize and invalid candidates are discarded whole. The run has an explicit maximum-candidate budget and fails closed without publishing if no exact packing is reached. It never truncates a trajectory. This bounded streaming policy is sufficient for the preregistered two-game G0; production chunk sizing and any higher-throughput packing algorithm require a later hash-pinned pilot decision.

V1 is deliberately single-thread deterministic. The rendered `{THREADS}` value is authenticated and must equal one. Parallel generation, scheduling-independent ordering and resume are separate engineering gates; they are not inferred from OpenBench worker concurrency.

## OpenBench command boundary

The worker renders a single quoted command containing `{SEED}`, `{COUNT}`, `{OUT}`, `{THREADS}`, `{BOOK}`, `{BOOK_SHA256}`, `{NETWORK}`, `{NETWORK_SHA256}` and `{PRODUCER_SHA256}`. The producer parses exactly one command line, requires a following `quit`, rejects extra commands and never invokes a shell.

The campaign UUID and base seed are literals in the command. Chunk index is `assigned_seed - base_seed`; the chunk UUID, game UUIDs, trajectory UUIDs and capability challenge are deterministic SHA-256-derived identities over the campaign, chunk and candidate indices. A distinct seed therefore yields a distinct challenged capability response before output. The embedded provenance binds source, producer bytes, capability response, network, book, seed, record target, search settings, terminal policy and invalid-game policy. Paths remain metadata only.

## Frozen local G0

The first live-search gate is training-inadmissible and uses one thread, 16 MiB hash, depth 1, no node limit, no exploration, automatic terminal rules, a four-ply safety ceiling and an exact target of four records. Its seed is `8964207305086120581`, derived by clearing the high bit of the first 64 bits of the selection-policy SHA-256.

`tests/crazyhouse/data/crazyhouse-selfplay-g0-openings-v1.epd` is 158 LF-only bytes with SHA-256 `f99f8211316813924e52fb13fbb65a5bc27dcd585e2e32a86d90db0d113fd2f6`. It contains two independently checked mate-in-one roots. The exact current product candidate with the registered network returns `d8h4` and `Q@b7` at depth 1; both lead to production checkmate. The expected result is two complete two-record trajectories, one ordinary move and one drop, with opposite absolute-White results.

The LF-terminated selection policy is `Crazyhouse P11 self-play G0 v1: two independently verified mate-in-one roots; deterministic book order by frozen seed; no match-result selection; exact complete-trajectory packing; training inadmissible.` Its SHA-256 is `fc67430cb09eb28531889a6b8f99a02f4b033c5bd71cbef7d2e9add8a7d573c6`.

## Acceptance and claim boundary

The local gate requires a clean-export build, challenged capability, exact stdin launch, authenticated network and book, two exact searches, four records, complete trajectory replay, make/undo restoration, record/header/footer verification, byte-deterministic replay, kill/quarantine behavior and an independent decoder. Negative cases cover every identity input, malformed stdin, extra commands, bounds, illegal moves, unreachable quota, safety exhaustion, existing outputs and corrupted bytes. The frozen G9 fixture must remain byte-identical, and the normal product build must retain its option inventory and bench `113485`.

Passing this gate proves local search-to-physical-data plumbing only. It does not make the two-root artifact training-admissible, close G11, authorize OpenBench, select production search settings, approve CPU/disk cost, prove a split, train a model, establish Elo or support release.

## Deferred production decisions

- Dedicated training and validation opening corpora, generated independently from the strength panel.
- Label-blind trajectory-level split and post-generation transposition audit.
- Depth/nodes, exploration, adjudication, quota-packing and chunk-duration parameters derived from Crazyhouse pilots.
- Deterministic multi-thread generation and authenticated resume.
- Worker-side `CRAZYHOUSE_PHYSICAL_V1` semantic validation before compression/upload.
- Exact authorized campaign totals, resource handoff and official OpenBench canary.

