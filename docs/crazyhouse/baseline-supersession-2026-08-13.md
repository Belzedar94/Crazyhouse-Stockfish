# Source baseline supersession — 2026-08-13

## Decision

Fairy-Stockfish is rejected as the release source baseline for Crazyhouse-Stockfish. The mandatory source baseline is the latest verified development commit of `official-stockfish/Stockfish`.

At the decision boundary:

- official Stockfish `master`: commit `5062aee519a1ba262d472d8ab139851ced56573e`, tree `3b51a6c6d0e5d0fc44a4fde457d270340cb35280`, committed 2026-08-10;
- Fairy-Stockfish `master`: commit `c19b5f6c66894fdb0e88d0dd100e3885f744760a`, tree `5f243edc1ec2498610b3ed40923cf99718104fc8`, committed 2026-07-23;
- the prior P2 comparison rejected official Stockfish primarily because of porting and maintenance cost. It did not demonstrate equivalence to the latest official search, evaluation, NNUE, and platform core. That criterion is insufficient under the clarified product requirement.

## Evidence classification

The Fairy-Stockfish line is retained under `retired/fairy-stockfish-baseline-no-go` as an auditable donor and migration oracle. It is not a release ancestor and must not be used as a comparator identity for an official-base release.

The following evidence remains portable:

- the announced Crazyhouse rule profile and primary-source authority resolution;
- the independent 48-case legality/state/perft corpus and reference identities;
- the exact legacy network bytes, license investigation, and negative-load requirements;
- process/resource receipts, incident prevention rules, and the referee defect analysis.

The following evidence is superseded for gate admission:

- Fairy-Stockfish executable identities, build reproducibility, bench/search signatures, sanitizer results, UCI projection results, and evaluator-loader acceptance;
- G2, G3, G4, and G5 claims that depend on the Fairy-Stockfish implementation or binary.

No playing-strength, model-selection, release, publication, tag, or OpenBench claim was made from the rejected baseline.

## Restart boundary

G2 is reopened. Canonical `main` must start at the verified official Stockfish development commit before any Crazyhouse implementation is applied. Fairy-Stockfish code may be ported only in reviewable rule/state/protocol/evaluator slices, each covered first by the frozen Crazyhouse fixtures. The legacy network remains a mandatory compatibility target until a stronger replacement passes all later gates.
