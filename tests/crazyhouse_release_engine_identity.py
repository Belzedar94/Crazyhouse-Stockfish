#!/usr/bin/env python3
"""Exercise the frozen Crazyhouse release engine-identity contract."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "release"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import verify_crazyhouse_engine_identity as identity  # noqa: E402


CONTRACT = REPO_ROOT / "tests" / "crazyhouse" / "p15-release-engine-identity-v2.json"
VERSION = "1.0.0"
TAG = "v1.0.0"
P7 = "4482bb403bf19b7e8dde6ef316c27769cde31ca8"
OFFICIAL = "229f6339e537a097a79831cd06dbfdb3e623d4ac"
TARGETS = identity.TARGETS
FIXTURE_SOURCE_ENV = "CRAZYHOUSE_IDENTITY_FIXTURE_REPOSITORY"
STABLE_HEADER = """/* Crazyhouse-Stockfish stable release version. */

#ifndef CRAZYHOUSE_VERSION_H_INCLUDED
#define CRAZYHOUSE_VERSION_H_INCLUDED

#include <string_view>

namespace Stockfish {

inline constexpr int CrazyhouseVersionMajor = 1;
inline constexpr int CrazyhouseVersionMinor = 0;
inline constexpr int CrazyhouseVersionPatch = 0;

inline constexpr std::string_view CrazyhouseVersionString = "1.0.0";

}  // namespace Stockfish

#endif  // CRAZYHOUSE_VERSION_H_INCLUDED
"""


class HarnessError(RuntimeError):
    """The synthetic identity matrix did not prove its frozen contract."""


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical(value))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HarnessError("fixture JSON is not an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin(path: Path) -> dict[str, object]:
    return {
        "path": path.resolve().as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "-C",
            str(repository),
            *arguments,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Crazyhouse fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Crazyhouse fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2026-08-25T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-08-25T00:00:00Z",
        },
    )
    if completed.returncode != 0:
        raise HarnessError(
            "git fixture command failed: "
            + " ".join(arguments)
            + "\n"
            + completed.stderr.decode("utf-8", errors="replace")
        )
    return completed.stdout.decode("utf-8", errors="strict").strip()


def fixture_source_repository() -> Path:
    raw = os.environ.get(FIXTURE_SOURCE_ENV)
    if raw is None:
        candidate = REPO_ROOT
    else:
        if not raw or "\x00" in raw:
            raise HarnessError("explicit fixture Git authority path differs")
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise HarnessError("explicit fixture Git authority must be a local absolute path")
    try:
        metadata = candidate.lstat()
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise HarnessError("fixture Git authority is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise HarnessError("fixture Git authority must be one real local directory")
    if git(candidate, "rev-parse", "--is-inside-work-tree") != "true":
        raise HarnessError("fixture Git authority is not a worktree")
    head = git(candidate, "rev-parse", "HEAD")
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise HarnessError("fixture Git authority HEAD differs")
    if git(candidate, "cat-file", "-t", OFFICIAL) != "commit":
        raise HarnessError("fixture Git authority lacks the official ancestor")
    if git(candidate, "cat-file", "-t", P7) != "commit":
        raise HarnessError("fixture Git authority lacks the P7 ancestor")
    completed = subprocess.run(
        ["git", "-C", str(candidate), "merge-base", "--is-ancestor", OFFICIAL, P7],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0 or completed.stdout or completed.stderr:
        raise HarnessError("fixture Git authority official/P7 ancestry differs")
    completed = subprocess.run(
        ["git", "-C", str(candidate), "merge-base", "--is-ancestor", P7, head],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0 or completed.stdout or completed.stderr:
        raise HarnessError("fixture Git authority P7/HEAD ancestry differs")
    return candidate


def engine_payload(target: str) -> bytes:
    return ("synthetic-crazyhouse-stockfish-1.0.0-" + target + "\n").encode("ascii")


def transcript_payload() -> bytes:
    lines = [
        "Crazyhouse-Stockfish 1.0.0 by the Crazyhouse-Stockfish developers (see AUTHORS file)",
        "uci",
        "id name Crazyhouse-Stockfish 1.0.0",
        "id author the Crazyhouse-Stockfish developers (see AUTHORS file)",
        "option name UCI_Variant type combo default chess var chess var crazyhouse",
        "uciok",
    ]
    rows = []
    for sequence, line in enumerate(lines):
        rows.append(
            {
                "direction": "in" if line == "uci" else "out",
                "line": line,
                "sequence": sequence,
            }
        )
    return b"".join(
        json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
        for row in rows
    )


def stable_source(
    repository: Path,
    *,
    header: str = STABLE_HEADER,
    misc_transform: Callable[[str], str] | None = None,
    authors_transform: Callable[[str], str] | None = None,
) -> tuple[str, str, str, str]:
    (repository / "src" / "crazyhouse_version.h").write_text(
        header, encoding="utf-8", newline="\n"
    )
    misc = (REPO_ROOT / "src" / "misc.cpp").read_text(encoding="utf-8")
    authors = (REPO_ROOT / "AUTHORS").read_text(encoding="utf-8")
    if misc_transform is not None:
        misc = misc_transform(misc)
    if authors_transform is not None:
        authors = authors_transform(authors)
    (repository / "src" / "misc.cpp").write_text(misc, encoding="utf-8", newline="\n")
    (repository / "AUTHORS").write_text(authors, encoding="utf-8", newline="\n")
    git(repository, "add", "src/crazyhouse_version.h", "src/misc.cpp", "AUTHORS")
    git(repository, "commit", "--no-gpg-sign", "-m", "fixture: stable identity")
    stable = git(repository, "rev-parse", "HEAD")
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    src_tree = git(repository, "rev-parse", "HEAD:src")
    winner = git(repository, "rev-parse", "HEAD^")
    git(repository, "update-ref", "refs/remotes/origin/main", stable)
    return stable, tree, src_tree, winner


def build_fixture(
    root: Path,
    fixture_repository: Path,
    *,
    tagged: bool = False,
    header: str = STABLE_HEADER,
    misc_transform: Callable[[str], str] | None = None,
    authors_transform: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    repository = root / "repository"
    completed = subprocess.run(
        [
            "git",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "clone",
            "-q",
            "--no-hardlinks",
            str(fixture_repository),
            str(repository),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise HarnessError(
            "local fixture clone failed for "
            + root.name
            + f" (exit {completed.returncode}): stdout="
            + repr(completed.stdout.decode("utf-8", errors="replace"))
            + " stderr="
            + repr(completed.stderr.decode("utf-8", errors="replace"))
        )
    stable, tree, src_tree, winner = stable_source(
        repository,
        header=header,
        misc_transform=misc_transform,
        authors_transform=authors_transform,
    )
    tag_object: str | None = None
    if tagged:
        git(repository, "tag", "-a", TAG, "-m", "synthetic stable tag", stable)
        tag_object = git(repository, "rev-parse", "refs/tags/" + TAG)

    artifacts = root / "artifacts"
    artifacts.mkdir()
    targets: list[dict[str, Any]] = []
    native_artifacts: list[dict[str, Any]] = []
    panel_targets: dict[str, object] = {}
    for target in TARGETS:
        target_root = artifacts / target
        target_root.mkdir()
        executable = target_root / "built.exe"
        panel = target_root / "panel.exe"
        package = target_root / "package.exe"
        package_verification = target_root / "package-verification.json"
        transcript = target_root / "uci.jsonl"
        payload = engine_payload(target)
        for path in (executable, panel, package):
            path.write_bytes(payload)
        transcript.write_bytes(transcript_payload())
        size = len(payload)
        digest = hashlib.sha256(payload).hexdigest()
        bench_signature = hashlib.sha256((target + "-bench").encode("ascii")).hexdigest()
        asset_name = f"crazyhouse-stockfish-{VERSION}-{target}.zip"
        asset_bytes = size + 100
        asset_digest = hashlib.sha256((target + "-archive").encode("ascii")).hexdigest()
        write_json(
            package_verification,
            {
                "schema": "crazyhouse-native-package-verification/v1",
                "status": "PASS_NATIVE_PACKAGE_VERIFICATION",
                "asset": asset_name,
                "bytes": asset_bytes,
                "sha256": asset_digest,
                "members": 12,
                "version": VERSION,
                "target": target,
                "commit": stable,
                "tree": tree,
                "sourceDateEpoch": 1_788_000_000,
                "executableBytes": size,
                "executableSha256": digest,
                "networkPolicy": "crazyhouse-release-network-policy/v1",
                "releaseEvidenceNetwork": True,
                "networkBytes": 58_534_811,
                "networkSha256": "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43",
                "packageInventorySha256": hashlib.sha256((target + "-inventory").encode("ascii")).hexdigest(),
                "sbomSha256": hashlib.sha256((target + "-sbom").encode("ascii")).hexdigest(),
            },
        )
        targets.append(
            {
                "target": target,
                "executable": pin(executable),
                "panelExecutable": pin(panel),
                "packageExecutable": pin(package),
                "packageVerification": pin(package_verification),
                "transcript": pin(transcript),
                "bench": {
                    "runs": [
                        {"nodes": 123456, "signatureSha256": bench_signature},
                        {"nodes": 123456, "signatureSha256": bench_signature},
                    ]
                },
            }
        )
        panel_targets[target] = {"bytes": size, "sha256": digest}
        native_artifacts.append(
            {
                "name": asset_name,
                "bytes": asset_bytes,
                "sha256": asset_digest,
                "provenance": {
                    "kind": "native",
                    "target": target,
                    "version": VERSION,
                    "tag": TAG,
                    "commit": stable,
                    "tree": tree,
                    "executableBytes": size,
                    "executableSha256": digest,
                },
            }
        )

    manifest = artifacts / "crazyhouse-stockfish-release-manifest.json"
    write_json(
        manifest,
        {
            "schemaVersion": 1,
            "project": "Crazyhouse-Stockfish",
            "version": VERSION,
            "tag": TAG,
            "commit": stable,
            "tree": tree,
            "sourceDateEpoch": 1_788_000_000,
            "network": {"alias": "Crazyhouse_v1.nnue"},
            "testingBook": {"distributed": False},
            "artifacts": native_artifacts,
        },
    )
    notes = artifacts / "RELEASE_NOTES.md"
    notes.write_text(
        "# Crazyhouse-Stockfish 1.0.0\n\nSynthetic release identity fixture.\n",
        encoding="utf-8",
        newline="\n",
    )
    panel_result = artifacts / "panel-result.json"
    write_json(
        panel_result,
        {
            "schema": identity.PANEL_SCHEMA,
            "status": "PASS",
            "version": VERSION,
            "candidateCommit": stable,
            "candidateTree": tree,
            "independentlyVerified": True,
            "allRungsPassed": True,
            "defects": 0,
            "targets": panel_targets,
        },
    )
    evidence_path = root / "identity-evidence.json"
    evidence = {
        "schema": identity.EVIDENCE_SCHEMA,
        "evidenceMode": "SYNTHETIC",
        "publicationState": "TAGGED" if tagged else "PROPOSED",
        "project": "Crazyhouse-Stockfish",
        "variant": "crazyhouse",
        "version": VERSION,
        "tag": TAG,
        "repository": {
            "stableCommit": stable,
            "tree": tree,
            "srcTree": src_tree,
            "officialAncestor": OFFICIAL,
            "originMain": stable,
            "p7Candidate": P7,
            "winners": [winner],
            "tagObject": tag_object,
        },
        "releaseManifest": pin(manifest),
        "releaseNotes": pin(notes),
        "panelResult": pin(panel_result),
        "targets": targets,
        "publicMutationPerformed": False,
    }
    write_json(evidence_path, evidence)
    return {
        "root": root,
        "repository": repository,
        "evidence_path": evidence_path,
        "evidence": evidence,
        "manifest": manifest,
        "notes": notes,
        "panel_result": panel_result,
    }


def save_evidence(fixture: dict[str, Any]) -> None:
    write_json(fixture["evidence_path"], fixture["evidence"])


def repin(fixture: dict[str, Any], field: str, path: Path) -> None:
    fixture["evidence"][field] = pin(path)
    save_evidence(fixture)


def rewrite_fixture_json(
    fixture: dict[str, Any],
    field: str,
    path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    value = load_json(path)
    mutate(value)
    write_json(path, value)
    repin(fixture, field, path)


def verify(fixture: dict[str, Any]) -> dict[str, object]:
    return identity.verify_release_identity(
        CONTRACT,
        fixture["repository"],
        fixture["evidence_path"],
    )


def expect_failure(label: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except identity.EngineIdentityError:
        return
    raise HarnessError("negative case was accepted: " + label)


def remove_fixture_tree(root: Path) -> None:
    def clear_readonly(function: Callable[..., object], path: str, _: object) -> None:
        os.chmod(path, stat.S_IWRITE)
        function(path)

    shutil.rmtree(root, onexc=clear_readonly)


def run_negative(
    root: Path,
    fixture_repository: Path,
    label: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    fixture_options: dict[str, Any] | None = None,
) -> None:
    case = root / ("negative-" + label)
    fixture = build_fixture(case, fixture_repository, **(fixture_options or {}))
    mutate(fixture)
    expect_failure(label, lambda: verify(fixture))
    remove_fixture_tree(case)


def main() -> int:
    positive: list[str] = []
    negative: list[str] = []
    development = identity.validate_development_source(REPO_ROOT)
    if development["status"] != "PASS_DEVELOPMENT_ENGINE_IDENTITY_SOURCE":
        raise HarnessError("development source identity did not pass")
    positive.append("development-source")
    fixture_repository = fixture_source_repository()

    with tempfile.TemporaryDirectory(prefix="crazyhouse-identity-") as temporary:
        root = Path(temporary)
        proposed = build_fixture(root / "positive-proposed", fixture_repository)
        proposed_result = verify(proposed)
        if proposed_result["tagAuthenticated"] is not False:
            raise HarnessError("proposed tag was authenticated")
        positive.append("proposed")
        tagged = build_fixture(root / "positive-tagged", fixture_repository, tagged=True)
        tagged_result = verify(tagged)
        if tagged_result["tagAuthenticated"] is not True:
            raise HarnessError("annotated tag was not authenticated")
        positive.append("tagged")

        cases: list[tuple[str, Callable[[dict[str, Any]], None], dict[str, Any] | None]] = []

        def evidence_mutator(label: str, mutate: Callable[[dict[str, Any]], None]) -> None:
            def apply(fixture: dict[str, Any]) -> None:
                mutate(fixture["evidence"])
                save_evidence(fixture)
            cases.append((label, apply, None))

        evidence_mutator("wrong-project", lambda value: value.__setitem__("project", "Stockfish"))
        evidence_mutator("wrong-version", lambda value: value.__setitem__("version", "1.0.1"))
        evidence_mutator("public-mutation", lambda value: value.__setitem__("publicMutationPerformed", True))
        evidence_mutator("empty-winners", lambda value: value["repository"].__setitem__("winners", []))
        evidence_mutator("duplicate-target", lambda value: value["targets"].__setitem__(1, deepcopy(value["targets"][0])))
        evidence_mutator("bench-drift", lambda value: value["targets"][0]["bench"]["runs"][1].__setitem__("nodes", 123457))

        def duplicate_evidence(fixture: dict[str, Any]) -> None:
            raw = fixture["evidence_path"].read_text(encoding="utf-8")
            fixture["evidence_path"].write_text(
                raw.replace(
                    '  "project": "Crazyhouse-Stockfish",',
                    '  "project": "Crazyhouse-Stockfish",\n  "project": "Crazyhouse-Stockfish",',
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
        cases.append(("duplicate-evidence-key", duplicate_evidence, None))

        def manifest_version(fixture: dict[str, Any]) -> None:
            rewrite_fixture_json(
                fixture,
                "releaseManifest",
                fixture["manifest"],
                lambda value: value.__setitem__("version", "1.0.1"),
            )
        cases.append(("manifest-version", manifest_version, None))

        def manifest_executable(fixture: dict[str, Any]) -> None:
            rewrite_fixture_json(
                fixture,
                "releaseManifest",
                fixture["manifest"],
                lambda value: value["artifacts"][0]["provenance"].__setitem__(
                    "executableSha256", "f" * 64
                ),
            )
        cases.append(("manifest-executable", manifest_executable, None))

        def notes_placeholder(fixture: dict[str, Any]) -> None:
            fixture["notes"].write_text(
                "# Crazyhouse-Stockfish 1.0.0\n\nTODO\n", encoding="utf-8", newline="\n"
            )
            repin(fixture, "releaseNotes", fixture["notes"])
        cases.append(("notes-placeholder", notes_placeholder, None))

        def panel_defect(fixture: dict[str, Any]) -> None:
            rewrite_fixture_json(
                fixture,
                "panelResult",
                fixture["panel_result"],
                lambda value: value.__setitem__("defects", 1),
            )
        cases.append(("panel-defect", panel_defect, None))

        def panel_digest(fixture: dict[str, Any]) -> None:
            rewrite_fixture_json(
                fixture,
                "panelResult",
                fixture["panel_result"],
                lambda value: value["targets"][TARGETS[0]].__setitem__("sha256", "e" * 64),
            )
        cases.append(("panel-digest", panel_digest, None))

        def package_bytes(fixture: dict[str, Any]) -> None:
            target = fixture["evidence"]["targets"][0]
            path = Path(target["packageExecutable"]["path"])
            path.write_bytes(b"different packaged executable\n")
            target["packageExecutable"] = pin(path)
            save_evidence(fixture)
        cases.append(("package-byte-drift", package_bytes, None))

        def package_verifier_digest(fixture: dict[str, Any]) -> None:
            target = fixture["evidence"]["targets"][0]
            path = Path(target["packageVerification"]["path"])
            value = load_json(path)
            value["executableSha256"] = "d" * 64
            write_json(path, value)
            target["packageVerification"] = pin(path)
            save_evidence(fixture)
        cases.append(("package-verifier-digest", package_verifier_digest, None))

        def hardlinked_panel(fixture: dict[str, Any]) -> None:
            target = fixture["evidence"]["targets"][0]
            executable = Path(target["executable"]["path"])
            panel = Path(target["panelExecutable"]["path"])
            panel.unlink()
            os.link(executable, panel)
            target["executable"] = pin(executable)
            target["panelExecutable"] = pin(panel)
            save_evidence(fixture)
        cases.append(("hardlinked-panel", hardlinked_panel, None))

        def dev_transcript(fixture: dict[str, Any]) -> None:
            target = fixture["evidence"]["targets"][0]
            path = Path(target["transcript"]["path"])
            path.write_bytes(
                transcript_payload().replace(
                    b"Crazyhouse-Stockfish 1.0.0",
                    b"Crazyhouse-Stockfish dev-20260825-nogit",
                )
            )
            target["transcript"] = pin(path)
            save_evidence(fixture)
        cases.append(("development-transcript", dev_transcript, None))

        def origin_behind(fixture: dict[str, Any]) -> None:
            git(fixture["repository"], "update-ref", "refs/remotes/origin/main", P7)
            fixture["evidence"]["repository"]["originMain"] = P7
            save_evidence(fixture)
        cases.append(("stable-not-on-origin-main", origin_behind, None))

        def unrelated_winner(fixture: dict[str, Any]) -> None:
            tree = git(fixture["repository"], "rev-parse", fixture["evidence"]["repository"]["tree"])
            completed = subprocess.run(
                ["git", "-C", str(fixture["repository"]), "commit-tree", tree, "-m", "unrelated"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "Crazyhouse fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Crazyhouse fixture",
                    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                },
            )
            if completed.returncode != 0:
                raise HarnessError("could not create unrelated winner")
            fixture["evidence"]["repository"]["winners"] = [
                completed.stdout.decode("ascii").strip()
            ]
            save_evidence(fixture)
        cases.append(("unrelated-winner", unrelated_winner, None))

        def proposed_tag_exists(fixture: dict[str, Any]) -> None:
            stable = fixture["evidence"]["repository"]["stableCommit"]
            git(fixture["repository"], "tag", "-a", TAG, "-m", "unexpected", stable)
        cases.append(("proposed-tag-exists", proposed_tag_exists, None))

        def lightweight_tag(fixture: dict[str, Any]) -> None:
            stable = fixture["evidence"]["repository"]["stableCommit"]
            git(fixture["repository"], "tag", TAG, stable)
            fixture["evidence"]["publicationState"] = "TAGGED"
            fixture["evidence"]["repository"]["tagObject"] = stable
            save_evidence(fixture)
        cases.append(("lightweight-tag", lightweight_tag, None))

        def nested_tag(fixture: dict[str, Any]) -> None:
            stable = fixture["evidence"]["repository"]["stableCommit"]
            git(fixture["repository"], "tag", "-a", "inner-tag", "-m", "inner", stable)
            git(fixture["repository"], "tag", "-a", TAG, "-m", "outer", "inner-tag")
            fixture["evidence"]["publicationState"] = "TAGGED"
            fixture["evidence"]["repository"]["tagObject"] = git(
                fixture["repository"], "rev-parse", "refs/tags/" + TAG
            )
            save_evidence(fixture)
        cases.append(("nested-tag", nested_tag, None))

        bad_dev_header = STABLE_HEADER.replace("1.0.0", "dev")
        cases.append(("stable-header-dev", lambda fixture: None, {"header": bad_dev_header}))
        override_header = STABLE_HEADER + "\n#define CRAZYHOUSE_VERSION_OVERRIDE 1\n"
        cases.append(("version-override", lambda fixture: None, {"header": override_header}))
        cases.append(
            (
                "wrong-engine-name",
                lambda fixture: None,
                {
                    "misc_transform": lambda text: text.replace(
                        'ss << "Crazyhouse-Stockfish " << version',
                        'ss << "Stockfish " << version',
                    )
                },
            )
        )
        cases.append(
            (
                "missing-upstream-attribution",
                lambda fixture: None,
                {"authors_transform": lambda text: "Crazyhouse-Stockfish contributors\n"},
            )
        )

        for label, mutate, options in cases:
            run_negative(
                root,
                fixture_repository,
                label,
                mutate,
                fixture_options=options,
            )
            negative.append(label)

    expected_positive = 3
    expected_negative = 25
    if len(positive) != expected_positive or len(negative) != expected_negative:
        raise HarnessError(
            f"fixture counts differ: positive={len(positive)} negative={len(negative)}"
        )
    print(
        "PASS_RELEASE_ENGINE_IDENTITY_FIXTURES "
        f"positive={len(positive)} negative={len(negative)} "
        "proposed=true tagged=true public_mutation=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HarnessError, identity.EngineIdentityError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(1)
