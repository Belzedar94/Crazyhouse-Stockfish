# ADR 0019: post-release monitoring and immutable rollback

- Status: accepted contract, implementation expected-red pending
- Date: 2026-08-24
- Evidence class: `P5_POST_RELEASE`
- Decision parent: `c80713b755eeb6d72977a8884b42207fd0d14062`
- Product `src` tree at decision: `fef67c6603c479439c33baaec496115dba293a0c`

## Context

A stable release is not complete when asset upload succeeds. The public tag,
release metadata, downloaded bytes, internal package inventory, network route
and runtime behavior can still differ from the authenticated local draft. A
later edit, CDN anomaly, loader regression, illegal move, crash, protocol
failure or rule-authority change also needs a fail-closed response that does
not rewrite history.

The release bundle tooling already authenticates local and downloaded bytes.
It does not define a checkpoint schedule, an append-only health state, terminal
eligibility, issue triage, rollback ownership or the response to a moved tag.
Those are separate `P5_POST_RELEASE` claims.

## Decision

Every stable Crazyhouse release gets one isolated monitor namespace and one
append-only hash chain of canonical JSON checkpoints. The namespace prefix is
`crazyhouse-monitor-<version>-`; no dataset, network cache, OpenBench campaign
or monitor state from another variant is accepted.

The initial schedule is measured from the authenticated GitHub publication
timestamp:

| Checkpoint | Due time | Allowed lateness | Purpose |
| --- | ---: | ---: | --- |
| `T0` | publication transaction | 5 minutes | tag, release metadata and first downloaded-byte verification |
| `T+15m` | 15 minutes | 5 minutes | CDN propagation and independent fresh download |
| `T+1h` | 1 hour | 10 minutes | early loader, runtime and issue signal |
| `T+6h` | 6 hours | 30 minutes | broader client and download exposure |
| `T+24h` | 24 hours | 1 hour | first daily health boundary |
| `T+72h` | 72 hours | 4 hours | extended compatibility boundary |
| `T+168h` | 168 hours | 12 hours | close the initial monitoring window |

No missed checkpoint is silently backdated. A late observation records its
real capture time and enters `DEGRADED_INVESTIGATING` until the gap is resolved.
Download counts are telemetry only; zero or many downloads cannot make an
otherwise invalid release healthy.

`RELEASED_MONITORED` becomes eligible only after `T0`, `T+15m` and `T+1h` all
pass, the next checkpoints through `T+168h` are scheduled under an identified
owner, the rollback decision path is active, and no critical signal is open.
The monitor remains active after that terminal transition. `T+168h` closes the
initial window but never makes a failed earlier checkpoint disappear.

## Required observation at every checkpoint

Each checkpoint must authenticate all of the following against the final G15
draft and publication receipts:

- repository, immutable tag name, tag object and peeled full commit;
- release ID, stable/non-draft/non-prerelease state and publication timestamp;
- exact advertised asset inventory, names, sizes and SHA-256 values;
- fresh downloads into a new directory with no reused bytes;
- strict `SHA256SUMS`, global manifest and provenance relationships;
- corresponding source asset and full candidate commit/tree relationship;
- both native ZIPs through the independent package verifier;
- byte-identical `Crazyhouse_v1.nnue` member, license authority and no fallback;
- the checkpoint-appropriate UCI capability, option, evaluator-route and
  deterministic runtime smoke frozen in the final draft receipt;
- public issues and reports for crash, illegal move, loader, protocol, GUI or
  rule drift, with query success distinguished from an empty result;
- monitor owner, rollback owner, previous checkpoint digest and next due time.

The runtime smoke is correctness evidence, never a speed or Elo measurement.
AVX2 runtime is attempted only on a host whose frozen feature probe satisfies
that target; absence of a compatible host is a monitoring defect, not a pass.

## State and critical signals

The only monitor states are `HEALTHY`, `DEGRADED_INVESTIGATING`,
`ROLLBACK_RECOMMENDED`, `CORRECTIVE_RELEASE_ACTIVE` and
`INITIAL_WINDOW_COMPLETE`. State transitions are append-only and each record
hashes its predecessor. A missing predecessor, duplicate checkpoint ID,
non-monotonic capture time or altered record invalidates the chain.

The following signals require at least `ROLLBACK_RECOMMENDED` and block
`RELEASED_MONITORED`:

- moved, recreated, deleted or differently peeled stable tag;
- missing, extra, replaced or hash-drifted asset;
- manifest, checksum, provenance, SBOM, license or corresponding-source drift;
- network alias mismatch or any evaluator fallback;
- reproducible crash, illegal move, corrupt state, wrong result or protocol
  regression on the released bytes;
- a material Crazyhouse rule-authority change that invalidates the frozen rule
  profile;
- a security or licensing defect affecting distribution or safe execution.

An isolated HTTP timeout is recorded and retried within the checkpoint; it is
not an outage by itself. Persistent inability to query or download is
`DEGRADED_INVESTIGATING`, never a fabricated healthy observation.

## Rollback contract

Rollback never moves or recreates a tag, overwrites an asset, rewrites a
receipt or silently changes the champion network. The monitor performs no
automatic public mutation. It emits a signed-off recommendation with the exact
symptom, affected bytes, severity, owner and safest action.

The owner response is one of:

1. document a false alarm with an additive receipt and resume monitoring;
2. publish a public known-issue notice while a correction is prepared;
3. nominate the last independently healthy stable version, when one exists;
4. build, verify and publish a new immutable corrective version and tag.

For the first stable release, the absence of a previous Crazyhouse version is
explicit. A critical defect therefore requires a known-issue notice and a new
corrective release; it never authorizes tag movement or asset replacement.

## Qualification boundary

Before stable publication, a synthetic monitor must prove canonical state,
hash chaining, schedule enforcement, exact asset reauthentication, independent
package-verifier composition, runtime-result ingestion and forty fail-closed
mutations from clean exports. Synthetic success does not monitor a real
release. Real `P5_POST_RELEASE` credit begins only with the final G15 bytes and
the newly published immutable tag.

This ADR does not select a candidate, authorize G15, create a tag, publish a
release, make a strength claim or declare `RELEASED_MONITORED`.
