# Crazyhouse differential references

These adapters expose one fail-closed JSONL contract while preserving the role of each implementation:

- `scalachess` is the pinned Lichess rules and result authority.
- `python-chess` and `chessops` are independent differential references. Their native insufficient-material behavior is retained as a diagnostic and never promoted to Lichess Crazyhouse authority.

Every request carries schema `crazyhouse-reference-request/v1`, authority profile `LICHESS_CRAZYHOUSE_2026_08_12`, a unique `id`, and one operation: `capabilities`, `inspect`, `transition`, or `perft`. Every response is one JSON object with the same ID. Unknown profiles, invalid or lossy FENs, illegal moves, dirty/wrong checkouts, and unsupported operations fail closed.

Canonical output uses bracket pockets ordered `PNBRQpnbrq`, preserves `~`, emits only a legally capturable en-passant square, and represents standard castling with the king destination. Native serialization is included separately where useful.

The executable corpus is `tests/crazyhouse/reference-cases.json`. Run it with `tests/crazyhouse_reference_contract.py` and explicit paths to the three pinned checkouts and toolchains. `--skip-scalachess` is only a development check and cannot satisfy G4.

The scalachess harness authenticates the pinned Git checkout, creates an exact LF-preserving `git archive`, validates a Crazyhouse perft blob byte-for-byte, and builds the external project from that disposable export under its native settings. The forked adapter receives the original checkout separately for runtime commit/tree/clean authentication. Archive identity and cleanup evidence are printed to the harness log; any unsafe tar entry, source mismatch, dirty reference, or cleanup failure aborts the run. The original checkout is never a compiler output directory.
