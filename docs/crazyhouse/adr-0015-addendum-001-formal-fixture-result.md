# ADR 0015 addendum 001: formal fixture admission result

- Status: accepted local engineering checkpoint
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Formal source commit: `ddf2d58beb0f22519a3ab0cf3b602f6af6295c4b`
- Formal source tree: `6732bf50ca7b16554adb4956bdaa0c4b30f999f5`
- Formal `src` tree: `36fa1995e984899db1b42fcce3e18c6b419fc49e`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`

## Result

Fresh lease 335 passed the complete preregistered fixture-only training-input
admission matrix. Two byte-identical clean Git exports each ran the independent
verifier under normal and optimized Python, for four successful profiles. All
four profiles produced the same normalized 1,729-byte result with SHA-256
`ac5daac718b6660a1e836fba258ab442994cfe8431b3e69b63e5fe568586af1c`.

Each profile independently scanned 44 physical records in 12 complete
trajectories, exercised 40 fail-closed negative cases and ran five exact
identity-intersection self-tests. The actual train/validation intersections
were zero for raw record, physical position, V2 model input, game and
trajectory identities. Required variant-state and label-perspective coverage
was complete, and failed output transactions left no admitted partial result.

The formal completion is 34,458 bytes with SHA-256
`7ad1cc842d3a562385832ad90c342e865210e8cc2124ec06bf2b9fb3fcd90caa`.
The immutable resource end receipt is 716 bytes with SHA-256
`7e6a8da8f0663e079562abc083572f527d3bc0c6416d8f1c405a88cd56e99377`.
The namespace used 27,246,104 bytes under its 512 MiB ceiling, all owned
resources were released, and the foreign P7 supervisor was unchanged.

## Physical and label boundary

Physical Crazyhouse records remain canonical. Evaluator-specific JSONL rows
are disposable projections. The loader transports raw labels and does not
freeze or apply a score/result blend, loss mapping or mate-score mapping.
Complete trajectories are partitioned before projection, and the independent
verifier imports neither the producer codec nor the loader.

Fixture construction is explicit and always nonadmissible for production
training. Production mode remains fail-closed on campaign authorization,
capability, OpenBench origin, aggregate receipts and semantic-audit evidence.

## Rejected predecessor

Lease 334 was preserved as `FAIL_STOPPED_NO_REPAIR_IN_PLACE`. Its first target
run passed, but the controller then rejected three deterministic files emitted
while GNU Make parsed the Stockfish Makefile: `.build_sha.txt`,
`.build_date.txt` and `.build_diffindex.txt`. The harness had incorrectly
equated every generated file with a tracked-source mutation. No source or
target defect was observed, and the failed namespace was not reused.

Incident `CH-302` records the correction. Lease 335 authenticated an exact
allowlist including each file's bytes and digest, while continuing to reject
any tracked mutation or additional generated path.

## Gate effect and boundary

The fixture-tested loader, raw-label transport, physical-to-V2 projection,
complete-trajectory partition and exact disk-backed split audit are qualified.
G12 remains open for an authorized real production chunk set, its exact
aggregate receipt, semantic engine replay, real-corpus split audit and a
separately preregistered deterministic production-training replay.

This result is not real-dataset admission, production-training authorization,
model selection, timing, strength, Fairy-Stockfish comparison, OpenBench,
release or monitoring evidence. Legacy V1 remains the productive default.
