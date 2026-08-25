# Crazyhouse-Stockfish intelligence dossier

Snapshot: 2026-08-13

Evidence class: `D0_DISCOVERY`

Covered phases: P0-P2, with P3 control evidence
Current transition: `GO_NEXT_PHASE` within P3

This dossier records observed state after the source-baseline supersession. Discovery, a build, a parser, a perft, a network header, a canary, or upstream CI is never playing-strength evidence.

## Canonical workspace resolution

The supplied project directory was initially a documentation kit, not a Git worktree. Recursive repository and worktree checks found no pre-existing canonical `Crazyhouse-Stockfish` checkout. One canonical worktree was created and later reset, without deleting its evidence history, when the owner rejected Fairy-Stockfish as the source baseline.

| Field | Resolved value |
|---|---|
| Canonical path | `C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Crazyhouse Project\Crazyhouse-Stockfish` |
| Integration branch | `port/official-crazyhouse-core` |
| Required release ancestor | official Stockfish commit `5062aee519a1ba262d472d8ab139851ced56573e` |
| Baseline tree | `3b51a6c6d0e5d0fc44a4fde457d270340cb35280` |
| Source remote | `upstream = https://github.com/official-stockfish/Stockfish.git` |
| Tracking control | local `main` tracks `upstream/master` |
| Latest-dev verification | `git ls-remote upstream refs/heads/master` returned the pinned commit on 2026-08-13 |
| Planned public remote | `origin = https://github.com/Belzedar94/Crazyhouse-Stockfish.git` |
| Public repository status | `Missing Access` / repository unavailable; publication not authorized |
| Worktrees | One canonical worktree |
| Baseline status | Clean before the integration branch was created |
| Baseline tags | No Crazyhouse release tag |

The rejected Fairy implementation is retained at `retired/fairy-stockfish-baseline-no-go` (`8b10d9bd96b66772b6e592eb31f14e35b4fdf5be`). It is a read-only rules/protocol/evaluator donor and evidence archive. It is forbidden as a release base, release ancestor, or official-base test substitute.

## Operational admission

| Resource | Observation | Crazyhouse lease |
|---|---|---|
| Host | AMD Ryzen 9 5950X; about 31.92 GiB RAM | No standing CPU lease |
| GPU | NVIDIA RTX 3080 10 GiB | No lease; idle is not a handoff |
| System disk | Stop project writes below 40 GiB free | D0/P3 bounded artifacts only |
| Bulk disks | No production dataset admitted | No bulk-data write lease |
| Local ports | `18761-18763` reserved | Disposable Crazyhouse smoke only |
| OpenBench | Only `https://belzedar.duckdns.org` can produce official evidence | No campaign or worker lease |
| Foreign workers | Existing portfolio workers are foreign unless a handoff receipt says otherwise | Read-only observation only |

Reserved names are `crazyhouse-` for tests/campaigns, `crazyhouse-physical-v1-` for physical datasets, `crazyhouse-nnue-v2-` for training, and `crazyhouse-monitor-` for monitoring. The candidate alias `Crazyhouse_v1.nnue` is not an internal artifact identity.

No timing-sensitive benchmark, match, datagen campaign, training job, or OpenBench campaign is admitted without a separate resource receipt. No foreign process may be stopped, resumed, suspended, or reprioritized.

## Primary-source coverage and access gaps

- The source baseline is official Stockfish `5062aee...`; its raw LF archive has SHA-256 `5174848e703f90b4680aa86960f1018a44c2aa72f9f6f4529773f6366cea40de`, with 117/117 tracked blobs authenticated.
- Lichess Crazyhouse semantics are pinned to `lichess-org/scalachess@cbffc9d7e2c6f8ba33381c5403e1b4f992199626` and `lichess-org/lila@13895e5856db0f854f6ab76394fffce852ebd5c9`, plus the official Lichess variant page.
- Pocket FEN syntax is pinned to the Fairy-Stockfish chess-variant FEN standard as an interchange reference, not as the implementation baseline.
- Independent differential candidates are pinned at `niklasf/python-chess@9c24454dcea4f8a30259d811a2f10b26e911deb4` and `niklasf/chessops@736c40ced7130d453d85e7979c360b797474c9a7`.
- The legacy network is byte-identical to the pinned Lichess asset and has an asset-specific CC0 declaration.
- The local production referee bytes and documented source/build lineage were authenticated, but its Crazyhouse result policy is not certified.

No rule-source fetch returned HTTP 401 or 403. The planned GitHub origin returned `Missing Access` / unavailable. The local `fairy-vault` repository was inspected at `ccb72d7656fdd43c4c2538f6c425295bd9f8bc43`; meaningful message, thread, GitHub-entity, and embedding tables contained zero rows. That is an explicit history-coverage gap, not proof that no relevant discussion existed.

## Source candidate resolution

| Candidate | Frozen snapshot | Rule/state fit | Modern official core | License | Maintenance cost | Decision |
|---|---|---|---|---|---|---|
| Official Stockfish | `5062aee519a1ba262d472d8ab139851ced56573e` | No native Crazyhouse support | Exact latest verified development baseline | GPL-3.0-or-later | High focused port cost | **Selected mandatory source baseline** |
| Fairy-Stockfish | `c19b5f6c66894fdb0e88d0dd100e3885f744760a` | Native drops, pockets, provenance and legacy format | General variant fork; not the requested product core | GPL-3.0-or-later | Low rule-port cost, unacceptable baseline identity | **NO-GO as source; evidence/donor only** |
| CrazyAra | `bb3b5b65ba16200f743d68e3bb4da465732bc4ea` | Crazyhouse-capable | Different neural/search product family | GPL-3.0 | Product migration | Rejected as a Stockfish baseline; research reference only |

The source decision is an owner requirement, not an inference from port cost. No Horde source commit, rule, net, book, referee result, schema, benchmark, bound, time control, or campaign identity is inherited.

## Formal role inventory

| Role | Frozen identity | Allowed use | Certification state |
|---|---|---|---|
| Source | official Stockfish `5062aee519a1ba262d472d8ab139851ced56573e` | Only release implementation ancestor | Source, license, clean export, build and standard-chess runtime control authenticated; no Crazyhouse behavior yet |
| Rule authority | Lichess profile backed by scalachess `cbffc9d...` and lila `13895e5...` | Normative legality, state, result and product-facing wire decisions | P2 authority profile frozen; fixture completion remains G4 |
| Primary executable reference | Pinned scalachess adapter | Differential legality, state and result | Adapter integration incomplete |
| Independent references | python-chess `9c24454...`; chessops `736c40c...` | Independent legal-set and round-trip checks within declared authority | Existing portable corpus evidence; official-base engine projection pending |
| Evaluator | `crazyhouse_run15rl_e190_l03.nnue`, SHA-256 `8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43` | Mandatory legacy compatibility control | Bytes, headers and CC0 identity frozen; official-base parser/math/runtime certification open |
| Referee | patched cutechess 1.3.0-beta4, SHA-256 `1c0bbab69e15a277c0b68bf032848b513f706749999cd5f6d09a1fb60f05b8a6` | Candidate only after passing the same corpus | Not certified; orthodox terminal-policy defects remain |
| Book | None | Baseline/correctness work | Intentional absence |
| Quarantined book | local `crazyhouse.epd`, SHA-256 `1371e87ce3bdb875d922ad0061c96c4a123bc571daf4ae2bff24e5176287f0fa` | Forensic inspection only | Rejected pending provenance, license, legality, deduplication and coverage |
| History oracle | local `fairy-vault@ccb72d...` | Supplemental context only | Empty coverage gap; not authoritative |

## Material discoveries that are mandatory tests

1. Official Stockfish has no `UCI_Variant`, pockets, provenance, drop parser, Crazyhouse terminal policy, or legacy evaluator route. Its healthy build is only a standard-chess control.
2. A python-chess 1.11.2 fixture at `7k/8/8/8/8/8/4K3/8[PNBRQ] w - - 0 1` is valid and has 303 legal moves (295 drops and 8 board moves). Therefore the official `MAX_MOVES=256` ceiling is invalid for the admitted dialect; 303 is not asserted to be the global maximum.
3. Promoted-origin state must be serialized with `~`, follow the unit, demote to a pawn on capture, survive make/undo, and participate in full position identity.
4. Crazyhouse repetition cannot use the official `rule50` irreversibility bound, and the current rule50-adjusted TT key is not the target identity.
5. No 50/75-move or insufficient-material terminal draw applies to the announced profile. Syzygy must be bypassed in Crazyhouse.
6. The production referee recognizes variant moves but inherits orthodox result rules. A variant name is not referee certification.
7. The production OpenBench route has not proved explicit fail-closed Crazyhouse mapping; a missing mapping must never fall back to standard chess.
8. The legacy file is a `HalfKAv2Variants` container, not an official Stockfish NNUE. Compatibility cannot be inferred from `.nnue`; routing, bounded parsing and integer numerical parity are separate gates.
9. The local book has 599 rows, 489 unique rows and no established origin/license. It cannot select openings or enter a release.

## Current gaps

- The focused official-base state/move/search architecture is under advisory review and source verification; no Crazyhouse source code has been applied.
- The complete scalachess adapter and negative-reference harness remain incomplete.
- The actual referee has not passed legality, clocks, notation, result precedence and option-persistence fixtures.
- The official-base implementation has not passed Crazyhouse build, sanitizer, deterministic digest, special-state search or make/undo gates.
- The legacy evaluator has not passed exact positive and adversarial fail-closed loads or numerical parity on the official-base implementation.
- No public repository/CI, production OpenBench route, physical datagen schema, dataset, trainer, V2 network, strength evidence, independent panel, release draft or monitor exists.

## Baseline resolution

`NO_VALID_BASELINE` does not apply: the exact latest official development source, license, clean archive, pinned authorities/references, authenticated mandatory legacy bytes, and donor boundary are all known. G2 is satisfied by the corrected official-source evidence. The project remains in E1 engineering; later gates remain open and no strength or release claim is made.
