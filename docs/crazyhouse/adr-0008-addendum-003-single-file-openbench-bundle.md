# ADR 0008 addendum 003: Single-file OpenBench bundle V1

- Status: accepted before any self-play build or run
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Corrects: incomplete sidecar transport in addendum 001

## Defect found during transport review

The physical chunk header stores SHA-256 bindings for capability and provenance, while G9 publishes those canonical JSON documents as separate sidecars. Generic OpenBench DATAGEN compresses and uploads exactly one `{OUT}` file. If `{OUT}` were only the `.chp1` chunk, the server receipt would preserve the hashes but not the bytes needed to resolve them. A successful upload would therefore be structurally incomplete evidence.

No self-play build, result or upload had been observed when this defect was found.

## Additive decision

Search-backed generation writes one deterministic `crazyhouse-datagen-bundle-v1` as `{OUT}`. Its exact binary schema is `schemas/crazyhouse-datagen-bundle-v1.schema.json`, 5,778 LF-only bytes, SHA-256 `27138d4049e2c6b2ad75f85d05fc799442cbf9f91a6e4a1c27c546c2eb9ecf5b`.

The bundle contains exactly three ordered sections: the challenged self-play capability JSON, canonical provenance JSON and one unchanged `crazyhouse-physical-v1` chunk. A fixed 256-byte header binds every section length and SHA-256, the concatenated payload SHA-256 and the bundle-schema SHA-256. A fixed 128-byte footer repeats total/payload identities and binds the complete header. Header and footer use CRC32C; reserved bytes are zero; trailing bytes are forbidden.

The producer completes and validates all three sections in memory before exclusive-creating a bundle partial. It fsyncs and rereads the partial, validates every nested binding, then atomically publishes `{OUT}` without replacement. A killed partial remains quarantined. Direct G9 trajectory replay retains its original three-file transaction and byte identity.

The OpenBench worker must validate the uncompressed bundle and nested physical chunk before bzip2 compression. The server continues to treat the upload as an opaque authenticated blob. Offline publication must decompress, validate the bundle, extract all three sections and independently reauthenticate the producer, contract, provenance and records.

This container resolves transport completeness only. It changes no physical record, label, rule, teacher, book, network, search setting or authorization boundary.

