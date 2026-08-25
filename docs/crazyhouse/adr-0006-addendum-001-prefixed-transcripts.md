# ADR 0006 Addendum 001: Timestamp-prefixed protocol transcripts

Status: accepted before strength execution

Date: 2026-08-24

Lease 277 remains an invalid plumbing canary with no gate credit. Its two games completed and its PGN replay was legal, but both frozen nonce parsers rejected the routing proof. The raw authenticated log contains four nonce commands and four matching acknowledgements. Cutechess prefixes debug protocol lines with a numeric elapsed-time field, while the parsers used whole-string anchors without multiline mode.

The runner and independent verifier now make the same narrow correction:

- enable multiline anchors;
- admit an optional numeric cutechess debug timestamp before `>` or `<`;
- admit both LF and CRLF line endings;
- retain the exact engine role, option name, nonce length and acknowledgement structure.

A shared unit fixture exercises both implementations with prefixed CRLF and unprefixed LF records. The old lease is not reinterpreted; a fresh namespace is required.

No candidate, comparator, network, referee, rule profile, opening, time control, seed, resource setting, adjudication, invalidation, statistic or stopping rule changed. The adapter-overhead gate, clean-host gate and OpenBench prohibition remain intact.

The machine-readable authority is `tests/crazyhouse/p7-local-strength-panel-v2.addendum.001.json`.
