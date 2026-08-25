# ADR 0017 addendum 001: formal source-matrix result

- Status: accepted source-only result
- Date: 2026-08-24
- Evidence class: `R4_RELEASE`
- Qualified decision commit: `4bdf6923bc726a422047182db24e4d23912cb9f7`
- Passing formal lease: `339`

Lease 339 authenticated two clean Git exports and replayed the matrix verifier
under normal and optimized Python. All four normalized results were
byte-identical. Each profile probed enabled and disabled deterministic-link
routing for `x86-64` and `x86-64-avx2`; the enabled routes contained exactly
one deterministic PE linker flag and the disabled routes contained none. No
compiler, linker object or engine executable was produced.

The formal completion is 9,792 bytes with SHA-256
`f95e721d24bde4ade9936fa8b38bae64fb36ae756c408d69977407090353aa89`.
The normalized result is 6,743 bytes with SHA-256
`0ddaf3ce25972ec3899f4d55c1ba4c38f3786af6495e20abadbbe193f74faab1`.
Both 7,034,880-byte source archives have SHA-256
`4d88e1facf8d191b62c314c01c72ea67d32b0991d6696705dacb0e567eb6d5c0`.

The registered 58,534,811-byte legacy network was reauthenticated as
`8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43`.
No target value was inherited from Atomic or Horde.

This result freezes only prospective source targets and their explicit
exclusions. It does not qualify full engine or archive reproducibility, select
a release candidate, claim timing or strength, authorize OpenBench, create a
draft or tag, authorize G15, or publish a release.
