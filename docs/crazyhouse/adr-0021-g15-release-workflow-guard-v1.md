# ADR 0021: G15 release-workflow guard

- Status: accepted contract, expected-red pending
- Date: 2026-08-25
- Evidence class: `R4_RELEASE`
- Decision parent: `820bbaeabd4669957c3f172122004520d02c2f66`
- Product `src` tree: `4649dfee96f7b164fc164ddd8713be2c684d3302`

## Context

The inherited `.github/workflows/official_release.yml` is an upstream
Stockfish release workflow, not a Crazyhouse release authority. Its current
write jobs are restricted to `official-stockfish/Stockfish`, but a manual run
in another repository still launches upstream universal and ARM compilation
jobs. It also expects `master`, accepts `sf_*` tags, and creates a tag before
creating a draft. Those targets, names and ordering are outside the accepted
Crazyhouse target matrix and G15 contract.

GitHub CLI documents that `gh release create` automatically creates a missing
tag unless `--verify-tag` is used. GitHub also documents that draft releases
remain mutable and that release immutability applies only after publication.
Therefore a nominal draft workflow is not a substitute for the project's
explicit immutable-tag and owner-publication gates.

Primary references:

- <https://cli.github.com/manual/gh_release_create>
- <https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments>

Atomic-Stockfish release orchestration was inspected for method only. No
Atomic tag, target, runner, workflow, artifact, decision or result is inherited.

## Decision

Before public repository recovery, replace the inherited workflow with one
byte-canonical manual guard. It has repository `contents: read`, no action,
secret, matrix, reusable workflow, build, upload, tag, release or publication
operation, and one job that exits nonzero with an English G15 boundary message.

The exact 498-byte workflow is frozen in
`tests/crazyhouse/p15-release-workflow-guard-v1.json`. A duplicate-key-safe
verifier reconstructs those bytes from the contract, authenticates their
SHA-256, rejects links and encoding drift, and then requires exact equality.
Mutation tests must reject every former write/build path and every one-byte
change.

This guard is intentionally not the future publisher. After the exact champion
and complete byte-reauthenticated draft exist, a separate additive publication
contract may replace it. That future path must consume an explicit owner G15
decision, require the already existing annotated `v1.0.0` tag with
`--verify-tag`, prove it peels to the admitted `origin/main` commit, upload only
the frozen asset set, publish once, and activate P5 monitoring.

## Gate effect

A pass proves only that the inherited workflow cannot spend excluded release
CI or create public release state. It does not recover the public repository,
create a draft, authorize G15, create a tag, publish a release, authenticate a
candidate, grant strength credit or close G15/G16.
