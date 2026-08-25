# ADR 0006: Local same-network strength gate

Status: accepted and preregistered; not yet executed

Date: 2026-08-23

## Context

Crazyhouse-Stockfish is derived from the pinned latest Stockfish development commit, not from Fairy-Stockfish. Before any OpenBench submission, the product candidate must beat the current Fairy-Stockfish implementation under the exact same legacy Crazyhouse network bytes.

The Fairy executable cannot satisfy the project's capability handshake directly. A qualified fail-closed adapter supplies that handshake and relays the UCI protocol without changing the Fairy search. Its fixed-work overhead must pass the separately frozen adapter-overhead gate; no clock compensation is allowed.

The owner explicitly selected the three-control local methodology used by the other mature variant projects: 2, 10 and 30 second base times; displayed LOS endpoints 0/100; and at least 50 games. This later project-specific authority replaces the earlier proposal to derive one time control from speed measurements. It does not import another variant's engines, networks, books, seeds, results or product source.

## Decision

The local gate is an ordered ladder:

| Rung | Equal time control | First decision boundary |
|---|---:|---:|
| VSTC | `2+0.02` | 50 games |
| STC | `10+0.1` | 50 games |
| LTC | `30+0.3` | 50 games |

Each rung starts with exactly 50 games, or 25 colour-swapped pairs. If the historical one-decimal WLD LOS display is neither `0.0%` nor `100.0%`, the runner adds fixed 16-game complete-pair batches. It stops at the first eligible completed batch that displays either endpoint:

- `100.0%` passes the rung and advances to the next control;
- `0.0%` rejects the candidate and stops the ladder;
- no endpoint after 2,048 games is an inconclusive result at the frozen cap and stops the ladder.

The 2,048-game cap is Crazyhouse-specific: the qualified book contains 1,024 unique roots, so one maximum rung uses every root exactly once as a colour-swapped pair without recycling an opening. There is no result-aware extension beyond an endpoint or the cap.

The stopping statistic reproduces the historical local runner's WLD Gaussian approximation and exact one-decimal display. Every batch also reports pentanomial WLD, Elo, 95% confidence interval and LOS using the OpenBench method. The latter is an independent audit statistic and does not silently replace the owner-selected stopping rule.

Both roles receive the exact same network bytes and settings: one thread, 64 MiB hash, no ponder, 25 ms move overhead, 250 ms referee time margin, no tablebases and no score adjudication. Openings are sequential and paired. A time loss, crash, illegal move, disconnect, stall, nonempty engine stderr or maximal-game-length adjudication invalidates the complete panel rather than becoming a normal score.

The runner executes in complete-pair batches and assigns each referee plus only its descendants to a Windows Job Object. Closing that exact job terminates any surviving local child without selecting processes by name or touching foreign workloads.

A two-game debug canary must first prove the exact Crazyhouse profile, per-game nonces, both evaluator routes, the shared network identity and one legal colour-swapped pair. Its score is never strength evidence.

An independent verifier must authenticate every input and receipt, reconstruct every command, parse every raw log, replay every PGN with the pinned `python-chess` Crazyhouse reference, match each game to its frozen opening, confirm legal terminal results, recompute both statistical methods and enforce the first-endpoint stopping rule.

## Admission and host boundary

The exact adapter must first pass its result-blind fixed-node overhead contract. The passing result is then bound to this contract by a fresh additive authorization record before any ladder game starts.

Timing and strength runs require a fresh host attestation. Existing Atomic, Horde, Alice, Spell or OpenBench work is never stopped, suspended, reprioritized or reassigned. A plumbing canary may use the explicitly authorized spare CPU, but no canary result can waive the clean-host condition for the strength ladder.

## Consequences

A verified `100.0%` endpoint at all three controls unlocks only the local prerequisite for an OpenBench canary using these exact identities. It is not official OpenBench evidence, a champion change, a release decision or post-release monitoring.

A `0.0%` endpoint, an inconclusive frozen cap or an infrastructure defect keeps OpenBench closed. All raw artifacts remain immutable; corrections use a fresh namespace and an additive hash-pinned record.

The normative machine-readable contracts are:

- `tests/crazyhouse/p7-local-adapter-overhead-v1.json`
- `tests/crazyhouse/p7-local-strength-panel-v2.json`
- `tools/strength/run_crazyhouse_local_strength_panel.py`
- `tools/strength/verify_crazyhouse_local_strength_panel.py`
