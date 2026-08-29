# ADR 0026: opt-in large-V2 normal-engine routing

- Status: preregistered before implementation
- Date: 2026-08-29
- Evidence class: `E1_ENGINEERING`
- Architecture: `CH-NNUE-V2-LARGE-K64G1-SFNNV16`
- Product default: Legacy Crazyhouse V1

## Context

The large-A0 container, scalar evaluator, SIMD evaluator and transactional
incremental accumulator exist, but they are currently reachable only from
dedicated verification binaries. The normal UCI engine owns and dispatches
only the registered Legacy Crazyhouse V1 network. A trainable V2 artifact
therefore cannot enter `go`, `bench` or a strength runner yet.

## Decision

The normal engine will gain one explicit `large-v2-a0` evaluator backend while
keeping `legacy-v1` as the default. Selecting V2 requires an external file, its
exact full-file SHA-256 and all six provenance digests already authenticated by
the container. The route transaction commits only after all identities and the
container pass validation. There is no silent fallback to V1.

Each search worker owns a network-bound V2 accumulator stack. A frame is
authenticated against complete physical K64/G1 inventories. Evaluation uses
the nearest valid ancestor for an incremental update or performs a full
refresh. Move, undo, null and backend-epoch boundaries keep the stack aligned
with the position. The initial implementation optimizes for exactness; speed
and Elo remain separate later gates.

The UCI surface is frozen in
`tests/crazyhouse/p12-nnue-v2-large-engine-routing-v1.json`. The deterministic
fixture is 126,406,688 bytes with full-file SHA-256
`e305c386080c3d802deb23fad322ee04689d360d9b04526f7e5608e9fc055311`.

## Acceptance

Acceptance requires:

- legacy-default route and search behavior remain unchanged;
- exact V2 route identity, provenance and backend truth are observable;
- normal `go` and `bench` execute the selected V2 evaluator deterministically;
- incremental and full-refresh values match through the frozen physical-state
  transition corpus;
- malformed, missing, wrong, truncated and corrupt inputs fail closed without
  committing a backend or admitting search; and
- warning-strict Linux/Windows builds and sanitizers pass on the exact head.

## Boundary

Passing this engineering contract does not admit production data, a trained
network, model selection, Elo, OpenBench strength, G12 closure, a default
change or release evidence. Legacy V1 remains productive until an exact
quantized V2 artifact wins every later gate.
