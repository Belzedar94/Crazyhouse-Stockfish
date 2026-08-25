# ADR 0016 addendum 001: formal MinGW plumbing result

- Status: accepted engineering result
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Qualified source commit: `e0f10878eec9bcc2276691a573f6829de0ff3357`
- Qualified `src` tree: `15e5245b0910bbb5ffa79b3bb67943b8bff24803`
- Passing formal lease: `338`

## Result

The explicit `mingw_reproducible=yes` route is qualified for later exact
release-candidate builds. The option remains disabled by default. Enabled
Windows probes expose exactly one `-Wl,--no-insert-timestamp`; disabled probes
expose none. Invalid values and enabled non-Windows use fail before compilation.

Lease 338 replayed the verifier with normal and optimized Python from two clean
Git exports. Both source archives were byte-identical. All four normalized
results were byte-identical. Each profile compiled and linked tiny x86-64 and
x86-64-avx2 controls twice. Every PE COFF timestamp was zero, and each
same-architecture executable pair was byte-identical. Missing-flag and
duplicate-flag mutations were both detected.

The canonical completion is 10,165 bytes with SHA-256
`3592fd4c7a86b8973d58d124172b13f1e62b56c93b6e5cf85dd695e89f6b9237`.
The normalized result is 10,214 bytes with SHA-256
`fae31f3cbea1f0421b32a0f86bc8ca58fe7922fbd62bd901c4c759847a3b09e8`.

## Failed predecessor and correction

Lease 337 remains failed and immutable. Although all four source profiles had
passed, its terminal harness required the supervisor to have no descendants.
A hidden Windows process with redirected streams owns one direct
`C:/Windows/System32/conhost.exe` until the parent exits. An isolated diagnostic
observed that exact child at 0, 25, 100, 500 and 2,000 ms after a passing
verifier, with no compiler, linker, Make, shell or verifier process remaining.

The correction was preregistered before lease 338. It permits zero or one exact
direct System32 console host created no earlier than the supervisor and rejects
every other descendant. The passing terminal audit observed one allowed console
host and zero disallowed descendants; it disappeared naturally when the
supervisor exited.

## Boundary

This result does not prove a full engine or release archive reproducible. It
does not freeze a target matrix, select a release candidate, qualify strength,
authorize OpenBench, create a draft, authorize G15, create a tag or publish a
release. Full executable/archive reproducibility and runtime authentication
remain exact-candidate per-target gates. The frozen P7 candidate did not change.
