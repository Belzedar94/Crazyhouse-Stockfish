# ADR 0014: Crazyhouse NNUE V2 authenticated legacy control

- Status: accepted and preregistered before implementation
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Rule profile: `LICHESS_CRAZYHOUSE_2026_08_12`
- Base commit: `455df26087ea86056fd21e5efb81a76fcbbfb21b`
- Base tree: `d4edf9c0e1a0680cf4b2936305430de4655d3f12`
- Base `src` tree: `630ebd4629e4361bb3cce3d56e442945be0b6b0e`
- Official Stockfish ancestor: `229f6339e537a097a79831cd06dbfdb3e623d4ac`
- External advisory review: explicitly waived by the owner; no API, credits, fallback, or alternate model is used

## Context

The productive V2 scalar, trainer, SSE2 transformer, transactional
incremental accumulator, and sanitizer matrix have passed their bounded E1
gates. G12 still requires the authenticated legacy evaluator to pass through a
newly implemented datapath before production data or model work can be
admitted.

The registered legacy artifact is 58,534,811 bytes with SHA-256
`8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.
It has 55,296 king-relative feature rows, 512 transformer lanes, an eight-lane
PSQT branch, eight material buckets, and a 1024-to-16-to-32-to-1 dense stack.
The productive V2 format instead has 902 global physical-state rows, no PSQT
branch or material stacks, and a 1024-to-32-to-32-to-1 topology. The legacy
path also uses wrapping integer arithmetic while productive V2 uses checked
working sums. These are incompatible contracts, not two files that merely
share an `.nnue` suffix.

Three-check commit `4f7944cb1068aec17185cf4b6cf59453bf351366`
was inspected read-only. Its compatible board rows could be copied and its
new counter rows zero-filled. Crazyhouse cannot use that construction because
both the feature coordinates and the network topology differ. Only the method
of authenticating an origin, marking a control purpose, parsing fail-closed,
and comparing against the registered evaluator is retained. No Three-check,
Atomic, Horde, or Fairy rule, feature, topology, tensor, fixture, result, or
bound is inherited.

## Decision

Introduce a local-only container with purpose `LEGACY_V1_CONTROL` and origin
`AUTHENTICATED_LEGACY_V1`. It preserves every legacy tensor byte and tensor
shape while replacing the historical framing with the independently frozen
Crazyhouse control container. It does not masquerade as a productive V2
representation or candidate.

The 1,024-byte header binds the exact payload, rule profile, legacy feature
contract, container contract, origin artifact, converter, source commit, and
source tree. A fixed nine-entry directory binds every tensor section by id,
dtype, shape, offset, length, and SHA-256. CRC32C protects the complete header
except its checksum field. All reserved bytes are zero and the payload is
contiguous through strict EOF. The frozen contract is
`schemas/crazyhouse-nnue-v2-legacy-control-container-v1.json`, 13,923 bytes,
SHA-256 `1d738d8c956c9d15a74f44dcf145d33aa72579da83d8be7421ca29050ad04759`.

The converter must authenticate the complete registered legacy artifact
before accessing tensor offsets. It writes the container and receipt
transactionally with exclusive creation and removes an incomplete container
if the receipt cannot be committed. Tensor values cannot change. Historical
version and architecture marker words are framing and are not copied into the
payload.

The new C++ loader and scalar executor are dedicated test components. They do
not call the existing legacy network parser or propagation code. They may
consume the already certified `LegacyCrazyhouseFeaturesV1::Result`, because
the controlled variable is tensor transport and execution, not feature
extraction. An independent Python parser, reserializer, and integer reference
must reach the same full trace.

## Frozen oracle and verification

The immutable 43-case oracle is
`tests/crazyhouse/legacy-numeric-goldens-v1.json`, 58,102 bytes, SHA-256
`53866d1139a85ac5e982e6ffd74ce6d0c154abdc7ea46b68fe238aa4ea822eb6`.
It covers both perspectives, all eight material buckets, 344 raw PSQT and
positional component pairs, pockets, drops, captures, en-passant, castling,
promotions, and promoted-origin state. The existing registered evaluator must
independently reproduce the same oracle in the formal replay.

Admission requires an expected-red observation of the absent dedicated Make
target before implementation. The implementation then requires two clean
exports, warning-strict release and assertion-enabled builds, byte-identical
normalized results, independent parse-reserialize identity, complete Python
and C++ trace parity, deterministic replay, and named fail-closed mutations of
every identity class, directory field class, reserved range, payload, CRC,
truncation, extension, wrong artifact type, and missing path. A failed load
cannot retain a previous network or expose partial tensors.

The normal engine must still report `legacy-v1`, authenticate the registered
legacy SHA-256, reproduce its frozen bench, and contain no control-container
magic. The dedicated control module must remain absent from normal engine
sources and objects.

## Boundaries

This gate proves only authenticated legacy tensor transport and independent
integer execution through a new control datapath. It does not prove that the
productive V2 topology can represent legacy strength, admit data, select or
train a model, improve speed, beat Fairy-Stockfish, authorize OpenBench, or
support release. V1 remains the productive default. G12 remains open after
this control until production-admissible data and training are separately
proved.
