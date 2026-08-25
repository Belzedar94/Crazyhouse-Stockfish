# ADR 0014 addendum 001: formal legacy-control result

- Status: accepted local engineering checkpoint
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Formal source commit: `d83c2929af36f88b5d85bd1c6f573dbbdc5878e7`
- Formal source tree: `21c318b8336ce1981fac8fac989d679d7b6cada0`
- Formal `src` tree: `0a191443efb389fdb914861bfa8faded6c2ae691`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`

## Result

Fresh lease 332 passed the complete preregistered legacy-control matrix. Two
byte-identical clean source archives produced release and assertion-enabled
profiles. All four profiles generated the same 58,535,712-byte control
container with SHA-256
`f629614968c7f91e6c7267dfb6e811c3c7322031345628ac73009e875e353596`.
Their normalized conversion receipts, Python traces, C++ traces and row
manifests were identical.

Each independent verifier covered 43 cases, both perspectives, all eight
material buckets and 344 raw PSQT/positional component pairs. Each profile
passed 83 independent Python negative mutations, 84 C++ loader negatives and
eight C++ evaluation negatives. The new control executor, the registered
legacy evaluator and the frozen raw oracle agreed exactly.

The formal completion is 85,333 bytes with SHA-256
`5845143918f7a285569d27b6eda9b5d5c1200e004bf52f4f40d28b27b69e7124`.
The immutable resource end receipt is 707 bytes with SHA-256
`f03811442fd10517ab8e5920e8ced5db278bcf54c215b3c725612e796bab0bf4`.
The namespace used 1,091,685,016 bytes under its 2 GiB ceiling, all owned
resources were released, and the foreign P7 supervisor was unchanged.

## Preserved routing boundary

The ordinary engine remained on `legacy-v1`, authenticated network SHA-256
`8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`,
reproduced bench `113485`, contained no legacy-control marker and downloaded
no standard network. The productive scalar, SSE2 and incremental regression
receipts also passed, including byte-identical deterministic full/resumed
training artifacts.

## Preflight correction

The first read-only preflight stopped before namespace creation because it
expected the Windows process name `python` while `psutil` reported
`python.exe`. Incident `CH-301` records the false inference, cause and
prevention. The original start receipt was not rewritten; correction receipt
SHA-256 `37ea4f08ab3702e2a80d854a9b40e3bd825be76f67888da97f11a38196efc820`
pins the effective controller, and the complete preflight then passed before
the formal lease began.

## Gate effect and boundary

The authenticated legacy-control subgate is closed. G12 remains open only for
production-admissible physical data and training. This result is not a
productive V2 representation, training admission, model selection, timing,
strength, Fairy-Stockfish comparison, OpenBench authorization, release or
monitoring evidence. Legacy V1 remains the productive default.
