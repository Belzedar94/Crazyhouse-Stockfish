# ADR 0018 addendum 003: formal fixture tooling result

- Status: passed
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Lease: 341
- Tested commit: `96cbd63eb7d70615fab6da47035717d4701be0e0`

Two independent clean exports passed both Python 3.12 normal and optimized
profiles through the implemented Make target. Every profile executed five
positive and 49 mutation-negative cases. All four normalized summaries are
100 bytes with SHA-256
`1038efa7137b569575ca42945a3f50c4dc856ac65182e1182b3b92c7a2863bb6`.
There were zero nonzero exits, timeouts, stderr bytes, compiler/linker artifacts
or Python bytecode files.

The two source archives are byte-identical at 7,127,040 bytes with SHA-256
`015492dd992a251370ba25d9bdf461c7bf2ba1f07a52ac814e4c1abb805813f2`.
Only the three preregistered Stockfish Make parse metadata files appeared in
each export. P7 remained unchanged and every owned process, including the
supervisor console host, exited.

Completion is 9,389 bytes with SHA-256
`534fa8f84bdd3476318bc0667ba5ec544ddb0b104db540cbdc406eea32e5d229`.
The immutable end receipt is 3,442 bytes with SHA-256
`78c439b0388c94226f08ee6847f1fb33b51deaaa17897ebdedb473d5a2b0bdf3`.

This formally qualifies only synthetic global inventory, provenance,
copy-and-rehash assembly, deterministic manifest, strict checksums and
downloaded-byte verification. Real ZIP layout/network inspection, full engine
builds, product archive reproducibility, SBOM content, candidate selection,
strength, OpenBench, draft assets, tag, G15 and release remain open.

