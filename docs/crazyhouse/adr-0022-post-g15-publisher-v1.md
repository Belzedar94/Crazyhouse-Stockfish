# ADR 0022: one-shot post-G15 stable publisher

- Status: accepted contract, expected-red pending
- Date: 2026-08-25
- Evidence class: `R4_RELEASE`
- Decision parent: `a26900dd30479679d5eccc6b16ad9d30c26a7062`
- Product `src` tree: `4649dfee96f7b164fc164ddd8713be2c684d3302`

## Context

The qualified `.github/workflows/official_release.yml` is intentionally a
read-only refusal guard. It is not a publisher. G15 still needs a separately
qualified path that cannot create a tag or stable release before the owner has
authorized one exact candidate and one exact local draft.

GitHub CLI documents that `gh release create` creates a missing tag unless
`--verify-tag` is supplied, and that a release made from an annotated tag
requires creating and pushing that tag first. It also documents that draft
releases and their assets remain mutable until publication. GitHub's release
API defines changing `draft` from true to false as publication. These semantics
make a generic release workflow, a mutable draft ID or a filename list
insufficient publication authority.

Primary references:

- <https://cli.github.com/manual/gh_release_create>
- <https://cli.github.com/manual/gh_release_edit>
- <https://docs.github.com/en/rest/releases/releases>
- <https://docs.github.com/en/rest/git/tags>
- <https://docs.github.com/en/rest/git/refs>

Atomic-Stockfish and Horde-Stockfish publisher layouts are method references
only. No tag, asset, repository, command, recovery policy or authorization
record is inherited.

## Decision

Implement one local, explicit, one-shot publisher at
`tools/release/crazyhouse_g15_publisher.py`. It is never invoked by a GitHub
workflow and does not replace the read-only G15 guard. Its default mode is a
read-only plan. Public mutation additionally requires `--execute` and one
strict, duplicate-key-free owner decision record whose exact bytes are frozen
by a separately authenticated G15 receipt.

The complete draft required before the owner decision is local and immutable:
the three payload assets, global manifest and `SHA256SUMS`, plus final release
notes. The existing independent download verifier must authenticate a fresh
local copy of the five assets before the decision record can be admitted. No
remote tag or GitHub release is created merely to obtain G15.

After authorization, the publisher executes this order exactly:

1. Reauthenticate the decision record, repository, clean Git state, exact
   candidate/tag target ancestry, admitted `origin/main`, notes and five local
   assets. Require that the local and remote tag and GitHub release do not
   exist. Every check through this point is read-only.
2. Create one deterministic annotated local `v1.0.0` tag object from the
   decision's tagger identity, UTC timestamp and exact message; require its
   peeled commit to equal the admitted target.
3. Push only `refs/tags/v1.0.0` to the configured `origin`, then re-read both
   the tag object and peeled commit from the remote. Force, deletion and tag
   update operations are forbidden.
4. Run `gh release create` with `--draft`, `--verify-tag`, exact repository,
   title, notes file and five individually named assets. Globs, generated
   notes, `--clobber` and extra assets are forbidden.
5. Download the remote draft into a fresh directory and run the independent
   release-download verifier against the frozen local draft. Re-query release
   ID, tag, title, draft/prerelease state and exact asset inventory.
6. Publish once with `gh release edit v1.0.0 --draft=false --latest
   --verify-tag`, re-query the stable state and emit the T0 monitoring handoff.

The publisher writes an append-only canonical stage journal before and after
each mutation. If any step fails after tag creation, it preserves the tag,
draft and journal and stops as
`PARTIAL_PUBLICATION_REQUIRES_ADDITIVE_RECOVERY`. It never deletes, moves,
recreates, overwrites or rolls back public state. Recovery is a separate owner
decision and additive contract, not an automatic retry.

## Authorization record

The decision binds the owner action `AUTHORIZE_STABLE_PUBLICATION`, repository,
version, tag, full candidate and tag-target commits/trees, admitted
`origin/main`, exact G15 draft-verification receipt, exact notes bytes, exact
five-asset inventory, deterministic annotated-tag payload, public title and
monitor owner. Boolean consent without these identities is rejected.

Secrets, tokens, environment dumps and raw authenticated command lines never
enter the record, journal or result. The tool consumes only credentials already
configured for `git` and `gh`; qualification uses a deterministic in-process
fake transport and performs zero network calls or public writes.

## Qualification and gate effect

Qualification starts with a clean-export missing-target expected red, then two
clean exports and normal/optimized Python profiles. Tests cover the green
read-only plan and simulated transaction plus at least: missing or duplicate
decision fields, wrong project/repository/version/tag, false authorization,
candidate/tree/ancestry drift, dirty checkout, moved `origin/main`, existing
local or remote tag, existing release, notes or asset drift, extra/missing
asset, malformed remote tag, draft metadata drift, download-verifier failure,
publication-state drift, mutation-order drift, retry/force/clobber/delete
surfaces, partial failure and receipt reuse.

A pass qualifies tooling only. It does not create a local or remote tag, draft
or release; does not authorize public repository writes or G15; does not prove
the final candidate or assets; and does not close G15/G16. The existing guard
remains the only dispatchable release workflow until the exact owner decision.
