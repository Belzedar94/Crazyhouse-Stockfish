# ADR 0024: OpenBench worker-thread transport for DATAGEN

- Status: accepted and frozen before implementation
- Date: 2026-08-29
- Evidence class: `E1_ENGINEERING`
- Base commit: `6b8a6822eb26763c711a5c43720a3994e164f2f6`
- Base tree: `233dd5afb687de96e5b79ce5c0594f29edc0e182`
- Base `src` tree: `2cbed40340451781c1cecdc5ac66942802fb4eaf`
- OpenBench base: `8193a13f0bb5022aaed999e14179dbc8933f9a4d`
- External advisory review: waived by the owner

## Context

OpenBench publication protocol 41 requires every DATAGEN command template to
contain `{THREADS}`. The worker replaces that placeholder with the assigned
machine thread capacity. Crazyhouse production generation deliberately uses
one search thread and rejects any `--threads` value other than `1`. Binding the
placeholder directly to `--threads` would therefore either reject a normal
multi-core lease or silently change the frozen scientific generation contract.

The clean base producer authenticates this missing seam by rejecting
`--openbench-worker-threads 12` with exit code 1 and
`unknown self-play argument: --openbench-worker-threads`.

## Decision

Add the production-only required argument
`--openbench-worker-threads <positive-u32>`. OpenBench will bind `{THREADS}` to
this argument. It records assigned capacity only; it does not create threads,
change search, enter the search-settings digest or affect records.

Keep `--threads 1` independently required and fail closed on every other value.
Emit the transport value in a separate top-level provenance object:

```json
{"openbench_assignment":{"worker_threads_capacity":12}}
```

The strict production provenance validator must require exactly this object
and a positive integer while continuing to require
`generation_settings.threads == 1`.

## Verification

1. Preserve the clean-base expected-red receipt.
2. Reject a missing transport argument and zero capacity.
3. Accept a representative twelve-thread assignment while retaining one
   scientific search thread in provenance and the search-settings digest.
4. Replay both production roles, exact counts, physical framing, partitioning,
   legacy G0 control and V2 training admission.
5. Require the complete public correctness workflow before OpenBench config can
   advertise the generator role or any Crazyhouse DATAGEN preset.

## Boundary

This is infrastructure transport, not a search or model experiment. It changes
no physical record byte, rule, evaluator, network, book, teacher setting,
partition, label, Elo, champion or release identity. It grants no DATAGEN,
dataset, training, model-selection, strength or release credit by itself.
