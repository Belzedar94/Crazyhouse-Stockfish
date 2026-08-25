# ADR 0020: release engine identity and exact-candidate boundary

- Status: accepted contract, stable implementation deferred until winner freeze
- Date: 2026-08-25
- Evidence class: `R4_RELEASE`
- Decision parent: `0bbacf027106b0fd444912075f268f0b1ca05361`
- Product `src` tree at decision: `9d769751748870c6b1e7af21dfa1ea12040fc2a4`

## Context

The exact executable preregistered for the local P7 same-network gate is
103,068,074 bytes with SHA-256
`aef7a64760c9f4f23cb15b4402130dd6a51c0843a3f8cb00af76e90bb813004b`.
Its authenticated P4 UCI transcript identifies it as
`Stockfish dev-20260823-nogit`. That identity is honest for the clean source
export from which it was built, but it neither names this product nor denotes a
stable release.

P7 is deliberately a pre-OpenBench strength filter. Its frozen contract says
that a pass is not a champion or release claim. Changing the P7 executable now
would discard its preregistered identity before any result exists. Conversely,
renaming only the file or archive after P7 would not change the UCI identity and
would misrepresent which bytes were tested.

Atomic-Stockfish's release-version header and exact-tag checklist were inspected
for method at local commit
`f8e9ea268317b1f85d6ae522e87d05e6e1e5f9cb`. No Atomic name, version value,
target, binary, network, strength result or release decision is inherited.

## Decision

The first prospective stable Crazyhouse-Stockfish release uses semantic version
`1.0.0`. This is derived locally as the first public stable version of this
dedicated product. It does not imply compatibility with, succession from, or
shared release state with another variant project.

One tracked source header, `src/crazyhouse_version.h`, is the sole native version
authority. It contains integer major, minor and patch constants plus one exact
string. The stable source must encode `1`, `0`, `0` and `1.0.0`. Build flags,
environment variables, archive names, tags and the surrounding Git checkout may
not override that value.

Native development builds identify themselves as:

```text
Crazyhouse-Stockfish dev-YYYYMMDD-SHA
```

or, for a Git-free source export, the honest `...-nogit` form. The prospective
stable binary must emit exactly:

```text
Crazyhouse-Stockfish 1.0.0 by the Crazyhouse-Stockfish developers (see AUTHORS file)
id name Crazyhouse-Stockfish 1.0.0
id author the Crazyhouse-Stockfish developers (see AUTHORS file)
```

The corresponding `AUTHORS` and source package must retain complete upstream
Stockfish authorship and GPL notices. Product naming is not permission to erase
or weaken upstream attribution.

## Exact-candidate boundary

The P7 executable and contract remain byte-for-byte unchanged. A valid P7 pass
may unlock only the preregistered OpenBench canary boundary for those exact
inputs. It cannot make the P7 `dev-...-nogit` executable a stable release.

After the final champion source is selected, release identity is introduced as
one isolated metadata commit. The full prospective stable commit is then built
from clean source and must pass all correctness, deterministic bench, runtime,
target and artifact gates. More importantly, the independent P14 panel must use
the exact stable executable bytes later placed in the native release packages.
No semantic-equivalence exception, filename-only alias or post-panel relink is
accepted.

The final stable commit must satisfy all of these ancestry relationships:

- the accepted P7 candidate is an ancestor;
- every accepted strength winner is an ancestor;
- the exact stable commit is an ancestor of the immutable `origin/main` commit
  admitted for release;
- the new annotated tag peels directly to that admitted commit.

Any executable-affecting source, compiler option, linked input or version change
after the final P14 panel invalidates exact-candidate strength credit. Packaging
changes that leave the executable bytes untouched do not require a new strength
panel, but they do require complete artifact reproducibility and downloaded-byte
reauthentication again.

## Qualification and gate effect

Qualification is intentionally expected-red while no champion exists. It will
require a duplicate-key-safe machine contract and an independent verifier that
authenticate the tracked version authority, exact source/tag/package agreement,
the real UCI transcript, executable digest, deterministic bench, package member
digest and winner ancestry. Normal and optimized verifier runs must agree from
two clean exports.

Passing that contract is only one R4 prerequisite. It does not close G14, G15 or
G16, authorize a public repository write, create a tag, publish a release, or
change the `Crazyhouse_v1.nnue` alias/default boundary. G15 still requires the
complete independently reauthenticated draft followed by the owner's one
explicit stable-publication decision.
