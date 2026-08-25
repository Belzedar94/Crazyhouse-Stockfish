# ADR 0018 addendum 002: formal fixture replay preregistration

- Status: preregistered, not executed
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Implementation parent: `681bb2418716cba040d1667f7b8857a55658c9c1`

The formal replay uses two independent clean exports and runs the implemented
Make target under Python 3.12 normal and optimized modes in each export. Every
run must emit exactly:

```text
PASS_RELEASE_BUNDLE_FIXTURES positive=5 negative=49 synthetic_fixture_only=true release_claim=false
```

The four summaries and the two source archives must be byte-identical. Only the
three authenticated Stockfish Make parse metadata files may appear in an
export. Compiler, linker and Python bytecode artifacts, timeouts, nonzero exits,
network calls and foreign process changes are forbidden.

Passing this replay qualifies synthetic global inventory, provenance,
copy-and-rehash assembly, checksums and downloaded-byte verification. It does
not inspect a real native ZIP's internal layout or network, build a full engine,
prove a product archive reproducible, select a candidate, grant strength or
OpenBench credit, create a draft or authorize G15.

