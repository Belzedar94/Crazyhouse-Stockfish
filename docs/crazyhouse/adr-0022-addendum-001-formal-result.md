# ADR 0022 addendum 001: post-G15 publisher formal result

- Status: tooling formally qualified; G15 remains open
- Date: 2026-08-25
- Evidence class: `R4_RELEASE`
- Qualified source commit: `780b1da3b902bd6bcf666a50e43f92a5f79f2c19`
- Qualified source tree: `864258df83095f7b82b0eed2a1b4864e24f9cf05`
- Product `src` tree: `4649dfee96f7b164fc164ddd8713be2c684d3302`

## Result

Lease 360 passed the preregistered offline qualification for the one-shot
post-G15 publisher. Two independently extracted clean exports were tested in
normal and optimized Python profiles. Every profile passed the same eleven
unit tests, including three positive cases and 89 negative cases. The two
source archives were byte-identical, each export remained byte-identical to
its pre-test manifest, and all four canonical outcome summaries had SHA-256
`4455c3607b128684ab5eaba439699c055a0a7cddfea895083f2c8248beddd9c8`.

The formal environment installed a hash-controlled Python audit hook and an
empty executable search path for every profile. Its positive control proved
that both `subprocess.Popen` and `socket.connect` were rejected before use.
Each profile also required the audit-active marker. The publisher therefore
used only its deterministic in-process fake transport: zero publisher
subprocesses, network calls, GitHub writes, Git remote writes and OpenBench
calls occurred.

The independent verifier authenticated the clean exports, archive equality,
raw unit logs, canonical summaries, audit-hook bytes, positive control,
resource boundary and the preserved P7 supervisor. The completion receipt is
`f369af903e3603bc265580ed53039d2830986caea85bdd5dbf6602f6ec8cf433`;
the independent receipt is
`8fff2316114f3a2092ceabfa46ae42fdd826663245c4fd876501e449969dd4b2`.

## Authority correction

Incident CH-328 was resolved before formal execution. Draft assets are no
longer downloaded through a tag-based release lookup. The effective contract
requires the unique positive asset IDs returned by the authenticated draft
record and downloads each exact asset from
`GET /repos/Belzedar94/Crazyhouse-Stockfish/releases/assets/{asset_id}` with
`Accept: application/octet-stream`. Each destination is created exclusively,
flushed, synced, size-checked, SHA-256 checked and then passed to the existing
independent release-download verifier. Tag-based draft download, browser URLs,
shell redirection and clobber remain forbidden.

## Gate effect

This result qualifies tooling only. It does not record an owner decision,
authorize a public write, create or push `v1.0.0`, create a draft, publish a
release, grant strength evidence or close G15/G16. The read-only release
workflow guard remains the only dispatchable release workflow. Stable
publication still requires the exact owner decision after the candidate,
assets and local draft have passed all preceding gates.
