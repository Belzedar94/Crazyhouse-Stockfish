# ADR 0023: Production physical DATAGEN V1

- Status: accepted and frozen before implementation
- Date: 2026-08-29
- Evidence class: `E1_ENGINEERING`
- Base commit: `ee30a4cbada2f29837f1f5798f70480023354613`
- Base tree: `981c4c6bf001acff3d6bd9ca000e59138af29cf9`
- Base `src` tree: `9e339934a45e0bfaffacae3bef267db61d344a18`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`
- External advisory review: waived by the owner

## Context

The existing search-backed producer is deliberately a four-record G0 fixture.
It accepts only the 158-byte two-root book and frozen fixture selection policy,
and its provenance states `training_admissible=false`. It therefore cannot be
renamed, reinterpreted or routed into production training.

OpenBench protocol v41 can bind the exact public source commit, producer bytes,
network, book, command, role, cohort, counts, seeds, leases and uploaded chunks.
The engine must still supply the variant-specific physical format, capability
handshake, exact-quota semantics and independent semantic audit.

## Decision

Add a separate `crazyhouse_generate_physical_production_v1` stdin command and
`--datagen-production-capabilities-v1` handshake to the DATAGEN-only artifact.
The existing G0 command and response remain unchanged controls. The normal play
engine continues to expose neither command.

Production V1 accepts only the registered legacy teacher network and the exact
official 599-root Crazyhouse EPD. It requires the producer SHA-256 placeholder,
an explicit OpenBench publication protocol value of 41, campaign/external
workload/role/cohort identities, and the hash-pinned tracked selection policy.
All of these identities are embedded in the bundle provenance.

Complete trajectories are assigned to `train` or `validation` before search by
the already frozen label-free partition formula. A campaign-specific addendum
must freeze both campaign UUIDs, their set digest, split seed, threshold and
partition digest before generation. Production V1 uses a validation threshold
of `2^61` (one eighth of the unsigned 64-bit domain); no post-hoc balancing is
allowed.

Each search uses one thread, 128 MiB hash, a depth cap of 64 and a fixed work
limit of 16,384 nodes. During the first eight plies, a SHA-256-derived choice
may select one of at most four exact MultiPV lines whose score is within 256
internal units of the best exact line. Thereafter the best exact line is used.
The selected line's own exact score and PV become the teacher label and move.
No wall-time value enters a record.

OpenBench `{COUNT}` remains an exact physical-record quota. The producer never
truncates a trajectory. It generates role-eligible complete candidates and
uses a deterministic descending exact-subset dynamic program over trajectory
record counts. It stops at the first candidate prefix that makes the exact
quota reachable, reconstructs the lexicographically earliest admitted subset,
and writes those complete trajectories in candidate order. Failure to make the
quota reachable within the frozen candidate budget aborts the chunk.

Infrastructure failures, non-exact teacher bounds, absent or illegal PVs,
nonterminal safety exits and incomplete games are quarantined and recorded in
provenance; they are never converted into draws or labels. Publication is
transactional and retains the existing physical 256-byte record plus
header/footer, CRC32C, SHA-256, capability and provenance bindings.

## Verification sequence

1. Authenticate the expected-red absence of the production command, capability
   response and dedicated test target from a clean base export.
2. Implement the separate mode without changing G0 bytes or normal play UCI.
3. Exercise both roles, partition recomputation, MultiPV selection, exact-subset
   reconstruction, exact count, complete trajectories and adversarial inputs.
4. Pass the full existing G0 and physical DATAGEN regression suites.
5. Publish a public non-draft PR and require green CI before adding a DATAGEN
   artifact role or preset to OpenBench.
6. Freeze an official protocol-v41 two-role canary campaign in an additive
   receipt; only its successful independent download/replay may unlock a larger
   production campaign.

## Boundary

This ADR implements production-capable plumbing but does not itself authorize
or create a DATAGEN workload, admit a dataset, train a network, select a model,
claim Elo, change the default legacy evaluator or support a release. Real G12
credit still requires official OpenBench receipts, exact aggregate totals,
engine-backed replay, a full split/duplicate audit and separately
preregistered deterministic production training.
