# ADR 0018: release bundle authentication and external legacy network

- Status: accepted contract, implementation expected-red pending
- Date: 2026-08-24
- Evidence class: `R4_RELEASE`
- Decision parent: `36bcf6a8c2a51e29aade6dccb97cbc79d28c5da1`
- Product `src` tree at decision: `15e5245b0910bbb5ffa79b3bb67943b8bff24803`

## Context

ADR 0017 freezes two prospective Windows targets but deliberately leaves the
network delivery mode and global artifact contract open. The normal engine's
official-chess `EvalFileDefaultName` is not a Crazyhouse network, while the
Crazyhouse route is transactional and requires an explicitly authenticated
`CrazyhouseEvalFile`. Packaging must preserve that separation.

Atomic-Stockfish and Horde-Stockfish release tooling was inspected for method:
exact inventories, per-asset provenance, copy-and-rehash assembly, deterministic
manifests, strict checksum parsing, non-overwrite output and downloaded-byte
reauthentication. Their target values, asset counts, package contents, networks,
toolchains, source commits and release results are not inherited.

## Decision

The initial Crazyhouse candidate bundle has exactly three public payload assets:

- `crazyhouse-stockfish-X.Y.Z-windows-x86-64.zip`;
- `crazyhouse-stockfish-X.Y.Z-windows-x86-64-avx2.zip`; and
- `crazyhouse-stockfish-X.Y.Z-source.tar.xz`.

The assembled directory also contains
`crazyhouse-stockfish-release-manifest.json` and `SHA256SUMS`. Sibling
`.provenance.json` files are authenticated assembly inputs embedded into the
global manifest; they are not separate advertised downloads.

Both native ZIPs distribute the registered legacy bytes only as
`networks/Crazyhouse_v1.nnue`. This is an external, byte-identical alias with
size 58,534,811 and SHA-256
`8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.
The release build must not embed these bytes, change `EvalFileDefaultName`, make
the alias a source default or select a new champion. Runtime smoke must set
`CrazyhouseEvalFile` to the extracted alias explicitly and authenticate the
engine's reported legacy route. Missing, corrupt, incompatible, wrong-size and
wrong-digest files remain fatal with no standard-network fallback.

Each native provenance descriptor binds the full candidate commit and tree,
source epoch, target ID, feature floor, toolchain identity, complete build
command, executable bytes, archive bytes, package inventory, SPDX SBOM, network
identity and license authority. The source descriptor binds the same commit,
tree and epoch and declares GPL corresponding source. The manifest must prove
that both native assets refer to that one source asset.

The strength opening corpus is not shipped. Final release evidence must still
record its exact identity and CC0 dedication: 100,204 bytes, SHA-256
`a8976a380a6cc4b3a1a6aae3bf14249b2ab6d1bac6cf4a2715625d7c01747603`,
1,024 roots. A different independent-panel book requires an additive contract
update before results are inspected.

## Package boundary

Every native ZIP must have one top-level directory and contain at least:

```text
Crazyhouse-Stockfish-X.Y.Z/
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

`FILES.json` authenticates every other packaged file without recursively
hashing itself. The SPDX document records the engine, network and their license
relationships. `SOURCE.md` names the exact corresponding-source asset and
commit. The network README records the pinned Lila asset and license-declaration
blob identities. Release notes may remain a draft only until the exact-candidate
panel, build and asset receipts replace every placeholder.

## Assembly and independent verification

The assembler rejects missing, extra, case-colliding, linked, unsafe, orphaned,
tampered or schema-drifted inputs. It creates a new output directory only,
copies each authenticated asset through a no-follow regular-file descriptor,
re-hashes the destination, writes canonical JSON, then writes strict ASCII
`SHA256SUMS` last. The checksum file covers the three payload assets and the
global manifest, never itself.

A separate download verifier compares names, sizes and SHA-256 values between
the locally assembled draft and a freshly downloaded draft, then independently
parses and verifies every checksum entry. Missing, extra, altered, duplicated,
path-bearing, non-ASCII or malformed checksum rows are fatal.

## Qualification order

1. Freeze this ADR and `p15-release-bundle-contract-v1.json` with tooling absent.
2. Prove the exact missing-target expected-red from a clean export.
3. Implement the assembler, provenance writers and independent download
   verifier with deterministic synthetic fixtures and mutation negatives.
4. Replay the complete fixture contract from two clean exports.
5. Only after a winner is frozen, build and authenticate the real three-asset
   candidate twice per target and create the complete draft.

Fixture success qualifies tooling only. It does not build a product engine,
prove archive reproducibility, select a candidate, grant strength credit,
create a GitHub draft, authorize G15 or publish a release.

