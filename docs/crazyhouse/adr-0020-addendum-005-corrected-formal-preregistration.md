# ADR 0020 addendum 005: corrected formal identity replay

- Status: preregistered, not executed
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Implementation parent: `165e96d57c9e87a374150d7c400be6768e9dc7e3`
- Reserved lease: 354

After the independently verified lease 353 closed CH-316, the formal replay
returns to two independent clean exports. Each export runs Python 3.12.0 normal
and optimized profiles, using the same explicit local Git authority at the
exact tested source commit and with network fallback forbidden.

Every profile must complete the unchanged three-positive and 25-negative
matrix. All four normalized summaries and both source archives must be
byte-identical. Every scratch namespace must restore empty; only the three
admitted Make parse files may appear; compiler, linker, engine and bytecode
artifacts are forbidden.

A pass qualifies only the synthetic identity tooling. It does not compile or
run a real engine, authenticate stable UCI/package bytes, select a candidate,
grant strength or OpenBench credit, create a draft or tag, authorize G15, or
establish a release.
