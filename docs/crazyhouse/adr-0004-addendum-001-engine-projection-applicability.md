# ADR-0004 Addendum 001: Official-engine projection applicability

- Status: Frozen before implementation
- Date: 2026-08-14
- Evidence class: `E1_ENGINEERING`
- Profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Advisory review receipt: `../../../receipts/private/p4-engine-projection-consultation-038.json`

## Decision

The official-base engine must project the entire 48-case G4 corpus through two independent observation paths. A direct test executable owns typed physical state, complete history, repetition counts and terminal results. The exact production UCI executable owns option routing, route acknowledgement, FEN serialization, check state, legal roots, perft and the explicit Crazyhouse search refusal. Both participants execute every case; neither can rescue a missing or invalid observation from the other.

The static applicability and field-ownership contract is `tests/crazyhouse/g4-engine-projection-applicability-v1.json`, 10,011 bytes, SHA-256 `bc6cc255beada0adb7a8139441debfbb8d4d5d4e9d93c4c8cda2dbb395260c7c`. It was frozen before official-engine output was observed. Runtime skips, inferred non-applicable fields, adaptive retries and expectation generation from engine output are forbidden.

No permanent state/result UCI command and no test-only UCI-shaped substitute will be added. The direct participant links only production rule and state code. The UCI participant uses the unchanged production dispatcher and the command allowlist frozen in the applicability contract.

## Profile routing

Production UCI adds one string option, `CrazyhouseProfile`, with the exact default token `LICHESS_CRAZYHOUSE_2026_08_12@d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68`. A Crazyhouse route commits only with that exact ID and profile-file digest. Empty, unknown-ID and known-ID/wrong-hash values fail closed with stable errors before a position can be accepted. Standard chess remains operational regardless of the retained Crazyhouse profile value.

Every successful Crazyhouse route acknowledgement carries `profile=LICHESS_CRAZYHOUSE_2026_08_12` and `profile_sha256=d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68`. A standard-chess route carries `profile=none profile_sha256=none`. `ucinewgame` preserves the configured variant and profile. Changing the profile dirties the transactional route and cannot reuse an earlier backend or position.

The profile routing fixture is `tests/crazyhouse_profile_routing.cpp`, 4,632 bytes, SHA-256 `7daa9062844d8f0fc6713e590d360ab0010437bfab6edc24785be0b581884ea9`. Its first admitted build is required to fail specifically because the declared profile API does not yet exist.

## Capacity and controls

Both projection paths must return the exact 303 legal moves in the frozen capacity position: 295 drops and eight non-drops, without duplicates or truncation. A mutation restoring an effective 256-move ceiling must fail.

Each participant runs twice with byte-identical normalized output. The exact source commit, executable identity, profile, corpus, applicability contract and capacity hashes must match before launch. Standard-chess direct controls and the frozen 2,884,956-node UCI bench remain mandatory and are not timing evidence.

## Gate boundary

Passing this addendum closes only the official-base direct-plus-production-UCI projection subgate of G4. It does not certify the referee, enable Crazyhouse search, provide strength evidence, authorize OpenBench work or advance release readiness.
