# ADR 0020 addendum 002: clean-export fixture-source failure

- Status: stopped without repair
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Lease: 352
- Tested commit: `e9a88e73ab17f6c6f5950ffb5cecda7c3bf047e7`

The first normal profile stopped before executing any fixture. The exact Make
target reached the harness, which then tried to clone its own clean-export root.
That root came from `git archive` and correctly contained no `.git` repository,
so local Git returned exit 128. This is a harness portability defect, not a
compiler, engine, timeout or host-outage verdict.

The lease preserves stdout, stderr, failure and source-archive hashes. It ended
with no owned descendants, empty profile scratch, a clean source worktree and
P7 still in `WAITING_HOST_TIMING_CLEAN`. Lease 352 will not be reused.

CH-316 permits one correction only: when running from a Git-free export, the
harness may consume an explicit local Git fixture authority whose path and
source identity are frozen by the controller. That authority supplies ancestry
objects only. The verifier, mutations, expected 3/25 matrix, identity rules and
public-mutation boundary remain unchanged. A fresh diagnostic must precede a
new formal replay.
