# ADR 0018 addendum 001: deterministic native-package interior

- Status: accepted expected-red contract
- Date: 2026-08-24
- Evidence class: `R4_RELEASE`
- Parent ADR: `docs/crazyhouse/adr-0018-release-bundle-authentication-v1.md`
- Decision parent: `0866cebcd2ee3fda94e7480c5f3a9e8d88fd8892`
- Product `src` tree at decision: `1058720318d4e0ad9c3f61e6ee48675f2dd13b6b`

## Context

ADR 0018 freezes the public asset set and the minimum native-package paths, but
the already-qualified tooling authenticates only synthetic outer assets. It
does not inspect ZIP members, prove the packaged network bytes, define the SPDX
relationships or make two native archives reproducible. Those are separate
release claims and need their own fail-closed contract before implementation.

Atomic-Stockfish and Horde-Stockfish remain method references only. No archive
layout, platform list, executable, network, toolchain, version, source commit or
release result is inherited from either project.

## Decision

Each native ZIP has exactly thirteen regular-file members below exactly one
ASCII top-level directory, `Crazyhouse-Stockfish-X.Y.Z/`. Directory entries are
forbidden. Member names use `/`, are NFC-stable ASCII, case-fold unique and may
not contain absolute, drive-qualified, empty, dot, dot-dot, control or trailing
segments. Links, devices, encrypted members, comments and extra fields are
forbidden.

The exact relative member inventory is:

```text
AUTHORS
CITATION.cff
Copying.txt
README.md
SOURCE.md
bin/crazyhouse-stockfish.exe
docs/RELEASE_NOTES_DRAFT.md
docs/RULE_PROFILE.md
inventory/FILES.json
inventory/SBOM.spdx.json
licenses/CC0-1.0-NOTICE.md
networks/README.md
networks/Crazyhouse_v1.nnue
```

Members are ordered bytewise by their full archive name, stored without
compression, have no per-member comment or extra bytes, and use a UTC DOS
timestamp derived from `SOURCE_DATE_EPOCH`, truncated down to an even second.
The executable mode is `0755`; every other member is `0644`. The ZIP creator
system is Unix so those modes are explicit and independently checkable. This
stored format trades archive size for cross-run determinism and simpler byte
authentication of the 58,534,811-byte network.

`inventory/FILES.json` is canonical UTF-8 JSON with LF termination. It lists,
in bytewise path order, every other member exactly once with relative path,
byte count, SHA-256, media type and executable flag. It never lists or hashes
itself. `inventory/SBOM.spdx.json` is canonical SPDX 2.3 JSON and describes two
packages and two files: the GPL engine executable and the CC0 legacy network.
It binds project, version, target, full candidate commit/tree, creation epoch,
file checksums, concluded/declared licenses and `DESCRIBES`/`CONTAINS`
relationships. The inventory hashes the completed SBOM, avoiding a cycle.

The native package assembler accepts only individually named, authenticated
regular unlinked input files. It creates a new output path, generates SBOM and
inventory in memory, writes the ZIP once and reopens it for verification. A
separate verifier must not import assembler code. It authenticates central and
local member metadata, exact member bytes, inventory, SPDX, network authority,
candidate identity and corresponding-source relationship before accepting the
archive.

The production verifier always requires the registered legacy network identity:
58,534,811 bytes and SHA-256
`8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.
A test-only small-network policy may exercise parser mutations, but it is
schema-distinct, cannot be selected by the production CLI and gives no credit
for network containment. Formal containment credit requires the real bytes
from the owner-provided legacy network path, independently rehashed before and
after both package builds.

## Qualification order

1. Commit this addendum and `p15-native-package-internal-v1.json` while the
   target and all three implementation paths are absent.
2. Prove the exact missing-target expected-red from a clean export.
3. Implement the assembler, independent verifier and mutation harness.
4. Run a low-cost test-policy diagnostic, commit the exact implementation and
   preregister the formal replay.
5. Defer the two-export real-network replay whenever P7 can run or any foreign
   timing-sensitive workload is active.
6. After a strength winner is frozen, replace the placeholder executable with
   the exact candidate and authenticate two full builds per target.

Expected-red, small fixtures and even real-network package fixtures do not
select a candidate, prove an engine build, prove runtime behavior, grant
strength credit, create a draft, authorize G15 or publish a release.
