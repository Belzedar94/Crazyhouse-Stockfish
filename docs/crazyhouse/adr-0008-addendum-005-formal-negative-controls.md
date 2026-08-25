# ADR-0008 Addendum 005: Formal negative controls for live-search DATAGEN

- Status: accepted before implementation and before the formal P11 G0 run
- Date: 2026-08-24
- Evidence class: E1_ENGINEERING

## Context

The authenticated P11 G0 book has two mate-in-one roots. It can prove the positive ordinary-move and drop-move paths, but it cannot naturally reach missing-PV, illegal-PV, or nonterminal safety-limit rejection without changing an authenticated scientific input. Those negative rows were frozen before the self-play implementation.

## Decision

The separate producer may accept `--test-candidate-fault` only when `CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION=1` is present. The only values are `missing-pv`, `illegal-pv`, and `safety-limit`.

Each control deforms one copied runtime value and then enters the existing rejection path:

- `missing-pv` removes exact/PV data from a completed search result before teacher-label admission.
- `illegal-pv` replaces the selected principal move with `Move::none()` after a valid exact label.
- `safety-limit` changes only the test-effective maximum ply to zero and enters the ordinary nonterminal limit check.

The option is disabled by default, is not advertised by the capability response, is forbidden in OpenBench commands, and must never appear in a positive generation. Every injected candidate must be quarantined and the exact-quota check must abort before any output is opened.

## Consequences

The formal harness can authenticate the three fail-closed branches without creating a second book authority or weakening the frozen book/network identities. Receipts must identify these observations as injected negative controls; they do not measure natural failure incidence and have no strength, model-selection, production, or release meaning.
