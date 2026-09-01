# Crazyhouse NNUE V2 large A0 production campaign

Status: preregistered; official production dataset pending.

This campaign converts the completed `CH-NNUE-V2-LARGE-K64G1-SFNNV16`
engineering path into a Crazyhouse-specific training and model-selection
procedure. It does not authorize fixture data, local OpenBench, or a default
network change.

## What is frozen

The canonical contract is
[`p13-nnue-v2-large-a0-production-campaign-v1.json`](../../tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.json).
It freezes the architecture identities, production-only admission boundary,
three paired seeds, sample-order policy, optimizer recipe, stopping rules and a
predesignated playing seed before any production metric exists.

The recipe deliberately does not copy a chess, Atomic or Horde training
configuration. The exact-score scale is discovered from the authenticated
Crazyhouse training role only: its nearest-rank 75th percentile absolute
centipawn score is mapped to probability 0.75. Validation rows and model output
do not participate in this derivation. A scale outside the preregistered safe
interval rejects the run and requires a public addendum before training.

The target mixes exact teacher probability and physical game result equally
and uses squared probability error. Sparse K64/G1 rows use a higher update rate
than the always-active dense trunks. Every admitted record is consumed exactly
eight times under the trainer's authenticated Feistel permutation.

## Why three seeds do not select the network

All three seeds must finish, export, reload and pass scalar, SIMD and
incremental parity. Their metrics establish stability and detect a seed-local
failure. They do not create three chances to pick the lowest loss: seed index
zero is the playing artifact by preregistration, and it cannot be replaced
after metrics are visible.

The actual quantized seed-zero artifact first faces the exact legacy-V1 control
through the new datapath. Fixed-work verification precedes equal-time STC and
LTC. Only playing evidence can change the productive default.

## Current boundary

The official OpenBench client change needed for two-role Crazyhouse DATAGEN is
merged but cannot be deployed while an unrelated active worker would be
restarted or reassigned. The local Fairy Vault snapshot contains no indexed
messages or GitHub entities, so it provides no Crazyhouse training evidence;
that access gap is explicit in the contract.

Until the official canaries, aggregate dataset admission and materialized
training configurations exist, G12 and G13 remain open and legacy V1 remains
the only productive evaluator.
