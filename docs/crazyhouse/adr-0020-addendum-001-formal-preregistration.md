# ADR 0020 addendum 001: formal identity-tooling preregistration

- Status: preregistered, not executed
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Implementation parent: `4c515136488ce0c17bf4ff47a68bf1142b9e8768`
- Reserved lease: 352

The formal replay uses two independent clean exports and runs the implemented
Make target under Python 3.12.0 normal and optimized modes in each export.
Every run must emit exactly:

```text
PASS_RELEASE_ENGINE_IDENTITY_FIXTURES positive=3 negative=25 proposed=true tagged=true public_mutation=false
```

The four normalized summaries and both source archives must be byte-identical.
Each owned profile temporary namespace must restore empty. Only the three
authenticated Stockfish Make parse metadata files may appear in an export.
Compiler, linker, engine and Python bytecode artifacts, timeouts, nonzero exits,
network calls and foreign process changes are forbidden.

Passing this replay qualifies only the synthetic development, proposed and
annotated-tag identity tooling plus its 25 mutation-negative fixtures. It does
not compile or run a real engine, authenticate stable UCI output or packaged
bytes, select a candidate, grant strength or OpenBench credit, create a draft
or tag, authorize G15, or establish a release.
