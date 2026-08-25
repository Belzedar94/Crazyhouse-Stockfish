# ADR 0021 addendum 001: formal workflow-guard result

- Status: passed
- Date: 2026-08-25
- Evidence class: `R4_RELEASE`
- Lease: 358
- Tested commit: `79e17bfb70afb5d01c69e6f9c42f8bb5f49935e1`
- Result: `tests/crazyhouse/p15-release-workflow-guard-v1.addendum.004.json`
- Result bytes: 5,042
- Result SHA-256: `b601a39ade2180acdc9d22cfb4a68f1b8e016d7ef7638c221bb13332f9262efc`

The inherited 4,448-byte Stockfish release workflow first produced the exact
expected-red. The preregistered verifier identified all thirteen forbidden
surfaces, including write permission, tag/ref operations, upstream repository
and tag dialects, Universal/ARM jobs and binary upload plumbing. No network or
public write was performed.

The one-variable correction replaced only that workflow with the canonical
498-byte G15 guard. Two byte-identical clean source archives then ran normal
and optimized Python profiles per export. All four verifier executions and all
four nine-test mutation suites passed; each suite rejected all thirteen former
surfaces plus byte drift, CRLF, BOM, missing LF, linked workflow, duplicate-key
contract and contract-digest mutations. Both export manifests remained
unchanged and all verifier summaries were byte-identical.

Independent verification rehashed the completion, archives, source manifests,
qualified workflow, contract, verifier, unit suite and every stream. It also
confirmed the 64 MiB namespace ceiling, zero network/GitHub/OpenBench calls,
zero surviving owned supervisor and preservation of P7.

CH-324 records why repository-name conditions did not make the inherited
workflow authoritative. CH-325 records a pre-process PowerShell launch error;
the corrected handoff bound `BelowNormal` to the captured owned PID before GO,
and the complete formal matrix then ran once in the still-fresh lease 358.

This pass proves only that the inherited release path is fail-closed. It does
not implement the future publisher, create a draft or tag, authorize G15,
publish a release, grant timing or strength credit, or close G15/G16.
