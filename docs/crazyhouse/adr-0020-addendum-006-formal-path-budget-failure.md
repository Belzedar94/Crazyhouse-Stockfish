# ADR 0020 addendum 006: formal scratch path-budget failure

- Status: stopped without repair
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Lease: 354
- Tested commit: `6cc2a9b0dd6f1f410f601bc8cc450fad59facc55`

The first profile passed the clean-export and local-authority preflights, then a
later fixture clone failed during checkout with Git for Windows `Filename too
long`. The formal target prefix, per-profile scratch name and fixture nesting
placed the longest tracked path at exactly 260 characters. Lease 353 used a
shorter scratch prefix and had already passed the unchanged matrix.

This is an operational namespace defect, not a verifier regression, compiler
failure, engine failure, timeout or outage. Lease 354 is immutable and grants
no formal credit.

CH-318 permits no source or matrix change. A fresh preregistration may allocate
four dedicated short scratch roots below `D:/Crazyhouse-Stockfish/tmp/p15-i355`,
must prove the designed worst-case path remains below 240 characters, require
each root absent before use and empty after use, and count their bytes against
the resource ceiling.
