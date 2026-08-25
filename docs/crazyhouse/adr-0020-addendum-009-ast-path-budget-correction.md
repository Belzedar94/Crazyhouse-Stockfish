# ADR 0020 addendum 009: AST-derived path-budget correction

- Status: corrected before execution
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Reserved lease: 355

The CH-319 manual correction still omitted the multiline fixture label
`missing-upstream-attribution`. It is 28 characters and raises the unchanged
short-root maximum from 202 to 205 characters, still 34 below the conservative
239-character ceiling.

Lease 355 had not started and had no observed result. Its controller must now
derive all 25 negative labels from the harness AST, add both positive repository
fixtures, enumerate every tracked path from the exact tested commit and compute
the complete Cartesian maximum across all four scratch roots. Manual label
lists are no longer admissible. Source, roots, matrix, authority and timeout
remain unchanged.
