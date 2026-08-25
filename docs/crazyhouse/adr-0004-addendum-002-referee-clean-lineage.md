# ADR-0004 Addendum 002: Clean referee lineage

- Status: Accepted before referee behavior implementation
- Date: 2026-08-14
- Evidence class: `E1_ENGINEERING`
- Profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Advisory review receipt: `../../../receipts/private/p4-referee-lineage-consultation-039.json`

## Superseding lineage decision

This addendum supersedes only ADR-0004's phrase "a constrained descendant of the existing production fork." Discovery proved that the existing production fork includes a Horde-specific referee commit, which the owner contract forbids the Crazyhouse referee from inheriting.

The certified Crazyhouse referee lineage must start from a fresh, non-shallow checkout of the authoritative AndyGrant/CuteChess repository at commit `24d4301152fb92ac442425e083a2658225f80720`, tree `289867c000147a84b2a48eff5ce1aa2fbd85e168`. Every candidate commit above that root is project-authored, linear, fixture-mapped and Crazyhouse-specific.

Horde commit `12fe19fb52646b48e626690a744c0cb2f177177c`, tree `e1645d72e119cbe2f5e112453cfc7a99619486e0`, and all of its descendants are forbidden ancestors and forbidden code, data, schema, build-metadata and expected-value donors. The commit modifies HordeBoard, WesternBoard, Horde tests/data and build metadata; it is not a generic transport patch. Atomic and Horde implementations may inform evidence method only.

The deployed `cutechess-ob.exe`, 7,511,040 bytes, SHA-256 `1c0bbab69e15a277c0b68bf032848b513f706749999cd5f6d09a1fb60f05b8a6`, remains a hash-pinned `KNOWN_NONCONFORMING` runtime comparator. It may demonstrate red behavior and operational invocation shape. It may not enter the candidate source graph, build tree, runtime dependency graph, `PATH`, match fallback or expectation-generation path.

## Mandatory provenance gates

Before candidate work, the clean root must be authenticated by commit and tree, be non-shallow, contain no replace refs or grafts, and use independently pinned submodules, generated sources, dependencies and Qt runtime inputs. No candidate checkout may be created by switching the Horde working tree to the upstream base.

Every candidate tip must satisfy all of the following:

- the clean upstream root is an ancestor;
- the forbidden Horde commit is not an ancestor;
- the merge base with the forbidden commit is exactly the clean upstream root;
- the post-root history contains no merge commits;
- every post-root commit maps to frozen Crazyhouse requirements or fixtures;
- patch IDs, changed blobs and suspiciously similar hunks are screened against the forbidden Horde change, with human provenance review remaining decisive.

The complete ordered commit list, parent IDs, tree IDs, changed symbols, motivating fixture IDs and no-copy disposition must be included in referee receipts.

## One executable and comparator isolation

Embedded conformance mode and ordinary match mode must use the same optimized executable SHA-256. They share the same board factory, FEN/pocket/promoted-state parsing, move containers, make/history, repetition, terminal policy, clocks, notation, result and PGN paths. A debug-only probe or a second rule implementation cannot satisfy the join.

The executable must emit the clean root commit/tree, candidate commit/tree, profile ID and SHA-256, conformance schema and build/toolchain identity. The negative comparator can be launched only by an isolated red-baseline harness after exact path and hash authentication; its output cannot repair or vote on the certified candidate.

## Fixture closure before behavior code

The existing 14 referee cases remain immutable. A hash-pinned addendum must freeze missing capability-acknowledgement failures, exact deadline equality/event ordering and deterministic history-digest output before candidate behavior is changed. The participant matrix also requires a hash-pinned provenance correction and explicit same-executable join; `tests/crazyhouse/g4-participant-matrix-v1.addendum.001.json` supplies that correction without rewriting v1.

Generic production integration behavior may be reimplemented only from an independently stated neutral requirement and fixture. No Horde hunk, default, control, book, value or build metadata is inherited merely because the deployed comparator has it.

## Gate boundary

This addendum resolves source lineage only. It does not certify a referee, enable Crazyhouse search, provide strength evidence, authorize OpenBench activity or change publication authority. Local work can reach at most `G4-LOCAL-CERTIFIED-REFEREE-CANDIDATE`; deployment still requires the separately owner-authorized two-game non-strength canary.
