# ADR 0020 addendum 008: pre-execution path-budget correction

- Status: corrected before execution
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Reserved lease: 355

The initial short-scratch preregistration calculated 190 characters using the
fixture name observed in lease 354. Full enumeration shows that
`negative-stable-not-on-origin-main` is longer. With the same frozen scratch
roots and the longest tracked relative path, the actual designed maximum is
202 characters, leaving 37 characters below the conservative 239-character
ceiling.

No lease 355 process or result existed when this correction was issued. The
controller must pin both the original preregistration and this addendum and
recompute the complete path budget before GO. Source, scratch roots, matrix,
authority, timeout and resource rules are unchanged.
