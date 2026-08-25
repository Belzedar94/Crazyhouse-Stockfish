# ADR-0002 addendum 001: en-passant load normalization

- Status: Accepted; supersedes one ingress sentence in ADR-0002
- Date: 2026-08-13
- Parent ADR SHA-256 before addendum: `b7528c77705a55c668368152aa5cb30b257ae87f3c17eb375308b84aab88a3cf`
- Evidence class: `E1_ENGINEERING` contract correction

## Corrected decision

ADR-0002 said that a single syntactically valid en-passant target with no legal capture would be rejected. That is incorrect for the frozen Crazyhouse serialization corpus.

The parser accepts one syntactically and physically plausible standard FEN en-passant target into temporary setup state. After the complete board, side, kings, check state and pins are available, it retains the target only if at least one fully legal en-passant capture exists. Otherwise a successful transactional load commits `-` as the canonical target. This is state normalization, not a display-only rewrite.

Examples:

- a legal `d6` target with `e5d6` available remains `d6`;
- a pinned or horizontal-discovery target loads successfully and canonicalizes to `-`;
- if two pawns can capture one target and only one capture is legal, the target remains;
- creation after a double pawn step applies the same full-legality filter before repetition identity is computed.

Input containing multiple concatenated candidate targets is not part of the selected standard Lichess Crazyhouse FEN dialect and is rejected. The historical donor fixture for multi-target normalization remains evidence about a generic Fairy parser, not a requirement inherited by this official-base specialization.

Hash identity includes only the normalized legally capturable target. Therefore a loaded pinned pseudo-target and the same state with `-` receive the same complete Crazyhouse key.

## Prevention

Serialization decisions must be checked against the frozen case-level corpus, not only against prose summaries. The engine, independent adapters and referee receive explicit legal, pinned, horizontal-discovery, mixed-source, loaded-state, transition, key-equality and make/undo fixtures before G4.
