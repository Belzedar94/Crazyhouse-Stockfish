# ADR 0020 addendum 010: formal identity-tooling result

- Status: passed
- Date: 2026-08-25
- Evidence class: `E1_ENGINEERING`
- Lease: 355
- Tested commit: `f734bd5d9dbd6a4e9088da0f2da26f9e67c51d9b`

Two independent Git-free clean exports passed Python 3.12.0 normal and optimized
profiles through the Make target. Every profile completed three positives and
25 mutation negatives: 12 positive and 100 negative executions in total. All
four normalized summaries are byte-identical; stderr is empty in every run.

The two 7,598,080-byte source archives are byte-identical. Only the three
admitted Make parse files appeared in each export. Four AST/Git-derived short
scratch roots restored empty, with a 205-character worst-case path, 34-character
margin and 166,365,627-byte observed combined peak below the 256 MiB ceiling.

Independent verification rehashed every stream, both archives and completion;
recomputed the 25-label/27-case/587-path path budget, export inventories,
scratch/resource accounting, process restoration and P7 boundary. CH-318,
CH-319 and CH-320 are closed.

This qualifies synthetic release-engine identity tooling only. Stable runtime
identity, full engines, final P14 strength, real candidate packages, OpenBench,
draft, tag, G15, G16 and release remain open.
