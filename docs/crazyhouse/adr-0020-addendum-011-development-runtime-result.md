# ADR 0020 addendum 011: development identity runtime result

- Status: passed
- Date: 2026-08-25
- Evidence class: `E1_ENGINEERING`
- Lease: 357
- Tested commit: `614c40ad92defe53c4542a91a00f02f144e53176`
- Result: `tests/crazyhouse/p15-development-engine-identity-runtime-v1.result.001.json`
- Result bytes: 8,099
- Result SHA-256: `b71b62340a2657958cc7f43c4bb3471649e83ef98a8bd5d3988bf6d5290bb1f6`

A Git-clean archive of the tested commit produced a warning-strict MinGW GCC
16.1.0 `windows-x86-64-avx2` build with empty compiler stderr. The resulting
103,072,105-byte executable has SHA-256
`e36798c69d3d79964aba5a55cbf3910311ec74a543c5a48bbfba1881fcc305c5`
and a zero PE/COFF timestamp.

The real engine emitted the Crazyhouse development identity
`Crazyhouse-Stockfish dev-20260825-nogit`, its project author line, one
`uciok`, and the complete 24-line option inventory. The capability handshake
passed inventory, positive legacy-network routing, one-shot readiness,
invalid-uppercase nonce, missing-network and standard-control cases. The
positive path bound the same 58,534,811-byte legacy network used by P7, with
SHA-256 `8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.

Independent verification rehashed all inputs and outputs, replayed the clean
source manifest, executable and transcript assertions, checked the capability
summary, proved all 75 owned process identities ended, and confirmed that the
P7 supervisor and every foreign-resource boundary were preserved.

Lease 356 remains an immutable rejected attempt under CH-322: its successful
build received no runtime credit because the source verifier was invoked with
mutually exclusive CLI modes and UCI was never executed. Lease 357 is a full
fresh replay with the corrected argv.

CH-323 records a derived-checksum encoding defect in the base preregistration.
That document hashed the two literal bytes `0x5c 0x6e` after each option line,
while the runtime inventory uses one LF byte `0x0a`. The exact 24 lines were
pinned before GO and stayed unchanged; both independent programs enforced
their equality and recomputed the correct 1,451-byte digest
`dad1204b3bf1cf1ce4e509964812e27bf238dd03c5fbdf2aeb45b3387c2c2c74`.

This qualifies the real development engine identity and the legacy Crazyhouse
runtime route. It does not authenticate a stable identity, a second clean
export, a final package, a release candidate, timing, strength, OpenBench,
publication, G14, G15 or G16. The exact P7 candidate remains unchanged and the
local Fairy-Stockfish strength ladder remains the next timing-sensitive gate.
