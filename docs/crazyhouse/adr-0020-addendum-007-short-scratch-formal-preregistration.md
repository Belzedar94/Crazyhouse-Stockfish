# ADR 0020 addendum 007: short-scratch formal replay

- Status: preregistered, not executed
- Date: 2026-08-24
- Evidence class: `E1_ENGINEERING`
- Source parent: `c28b26cf0d4e5dcc4f5ae73c0b2ff3d3aa355243`
- Reserved lease: 355

The source, verifier, fixtures, two-export matrix, normal/optimized profiles,
local Git authority and 900-second timeout remain byte-for-byte unchanged from
lease 354. The only variable is the scratch-root prefix.

Four fresh roots below `D:/Crazyhouse-Stockfish/tmp/p15-i355` reduce the
worst-case designed checkout path from 260 to 190 characters. The parent must
be absent before GO, each root must restore empty, and target plus scratch bytes
remain under the same 256 MiB ceiling.

A pass qualifies only synthetic identity tooling. It does not compile or run a
real engine, authenticate stable UCI/package bytes, select a candidate, grant
strength or OpenBench credit, create a draft or tag, authorize G15, or establish
a release.
