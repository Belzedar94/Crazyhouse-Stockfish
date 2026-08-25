# ADR 0019 addendum 001: synthetic monitor qualification

- Status: synthetic implementation qualified; real monitoring not started
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`; no `P5_POST_RELEASE` credit
- Implementation commit: `bea2a26cbdb8171de0243ab39a7a9b7184bffec2`
- Formal source commit: `84bf9ff287d8cac7d82a0ba551565b070e152fa3`
- Formal result: `tests/crazyhouse/p16-monitoring-rollback-v1.addendum.004.json`, 3,613 bytes, SHA-256 `b9c2b1fb339dcf9454fee131d234c3849454125afd0dba2118521dd90ce8e458`
- End receipt: `receipts/resources/p16-monitoring-formal-t1-end-350.json`, 4,947 bytes, SHA-256 `0a59395ffb0046f8932cc490c61883b3b864a306b7928222c26d02a705126641`

The checkpoint creator, independent chain verifier and Make/CI routing passed
the frozen synthetic matrix from two clean, byte-identical source exports.
Four harness executions authenticated 24 positive case executions and 160
negative case executions with no stderr, timeout, compiler, linker, engine or
bytecode artifact. The two Make-routed transcripts and the two optimized
direct transcripts matched the preregistered normalized summary exactly.

The implementation now enforces canonical duplicate-key-safe JSON, immutable
predecessor digests, contiguous schedule checkpoints, non-regressing capture
times, unique fresh-download namespaces, explicit monitor and rollback owners,
critical-signal lifecycle and no automatic public mutation. Production mode
also requires a regular unlinked G15 publication receipt whose exact size and
SHA-256 are authenticated independently by both the checkpoint creator and
the chain verifier.

This result does not authenticate a GitHub repository, release, tag, asset,
download, runtime or issue query. It does not authorize stable publication,
does not satisfy G16 and does not make `RELEASED_MONITORED` eligible. Real
`P5_POST_RELEASE` evidence starts only after the final G15 transaction and its
new immutable public bytes exist.
