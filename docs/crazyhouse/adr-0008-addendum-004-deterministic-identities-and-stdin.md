# ADR 0008 addendum 004: Deterministic identities and stdin framing

- Status: accepted before the first clean self-play build or search
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Corrects: the preselected G0 chunk UUID only

## Identity correction

The G0 preregistration preselected chunk UUID `c586b8a0-b63e-5d95-9ff3-3eee8f7a376c` before a byte-level derivation existed. That value is rejected and is never reinterpreted as an observed result. The additive correction is frozen in `tests/crazyhouse/p11-local-selfplay-datagen-g0-v1.addendum.003.json`.

Self-play V1 derives chunk, game and trajectory identities with SHA-256 over a NUL-separated domain, the raw campaign UUID, little-endian chunk index and little-endian candidate index. The first 16 digest bytes become the identifier after setting RFC 4122 display version/variant bits. This is not UUIDv5/SHA-1. For the unchanged G0 campaign and chunk index zero, the corrected chunk UUID is `58d026fb-e85f-511a-a97e-55b2ed48cd45`.

The chunk index remains `assigned_seed - base_seed`. The capability challenge uses a separate SHA-256 domain over campaign, derived chunk, assigned seed and producer digest. Paths never contribute identity.

## Official stdin join

The audited generic OpenBench worker launches the producer with no arguments, writes exactly one rendered command followed by `quit`, flushes and closes stdin. It uses a Python text-mode pipe. Linux presents LF; Windows may translate those two line endings to CRLF.

The producer therefore admits exactly two uniform framing forms: LF/LF or CRLF/CRLF. It rejects a BOM, NUL, bare CR, mixed newline forms, missing `quit`, extra lines and trailing data. It normalizes the admitted transport before strict quote/token parsing and never invokes a shell. Output capability, result and diagnostics remain raw canonical LF because the Windows producer switches stdout and stderr to binary mode before writing.

This portability decision changes no command token, placeholder, identity, search, rule, label, book, network, quota or authorization boundary.
