# ADR 0020 addendum 012: public-lineage identity correction

- Status: accepted correction
- Date: 2026-08-29
- Evidence class: `E1_ENGINEERING`
- Superseded machine contract: `tests/crazyhouse/p15-release-engine-identity-v1.json`
- Superseded contract bytes: 7,187
- Superseded contract SHA-256: `7b916381d9c8dae951d32923cac3f7e5ef3acb1f32a975305ddc37a5bcc8c7c2`

## Defect

The immutable v1 identity contract names
`c0c11978abbe0cc7c4d80c90426b00eae0aa712c` as its synthetic P7 ancestry
anchor. That commit belongs to an abandoned pre-public engineering line. It is
neither reachable from the curated public repository nor an ancestor of the B1
source line, so a public clone cannot satisfy the fixture authority check and
must fail before exercising the identity matrix.

The original preregistration was correct for its private checkout at the time.
It is retained byte-for-byte as historical evidence; it is not silently
rewritten and it supplies no ancestry authority for the curated public line.

## Correction

Version 2 uses the exact B1 engine-source commit
`4482bb403bf19b7e8dde6ef316c27769cde31ca8` as the public development-chain
anchor. Its complete tree is
`9c554b4d686e30761675c64755e38e9ff8e9ef3b`, its `src` tree is
`d01af0408fdeb642810fbe3aa76896f9110dacbe`, and official Stockfish commit
`229f6339e537a097a79831cd06dbfdb3e623d4ac` is its authenticated ancestor.
The admitted public `origin/main` commit at discovery,
`5883acbeffd53138d31b278894d1fee451adffe8`, is a descendant of this anchor.

The compatibility field remains named `observed_p7_identity` because the
independent release verifier and its evidence schema use that key. In v2 the
field explicitly means a development ancestry anchor, not a passed P7 result,
an accepted OpenBench winner, a champion, or a release candidate.

## Gate effect

The correction restores executable CI coverage for synthetic R4 identity
tooling on the public B1 line. It changes no engine source, evaluator, network,
book, referee, time control, strength result, champion, package, tag, release,
G15 decision, or monitoring state. Stable publication remains unauthorized.
