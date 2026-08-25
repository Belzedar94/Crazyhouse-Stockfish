#!/usr/bin/env python3
"""Verify the exact Crazyhouse-Stockfish release engine identity chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Iterable, Sequence


EVIDENCE_SCHEMA = "crazyhouse-release-engine-identity-evidence/v1"
PANEL_SCHEMA = "crazyhouse-final-panel-identity-result/v1"
TARGETS = ("windows-x86-64", "windows-x86-64-avx2")
OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
HEADER_INTEGER = {
    "major": re.compile(r"^inline constexpr int CrazyhouseVersionMajor = ([0-9]+);$", re.M),
    "minor": re.compile(r"^inline constexpr int CrazyhouseVersionMinor = ([0-9]+);$", re.M),
    "patch": re.compile(r"^inline constexpr int CrazyhouseVersionPatch = ([0-9]+);$", re.M),
}
HEADER_STRING = re.compile(
    r'^inline constexpr std::string_view CrazyhouseVersionString = "([^"]+)";$',
    re.M,
)
FILE_PIN_KEYS = {"path", "bytes", "sha256"}
EVIDENCE_KEYS = {
    "schema",
    "evidenceMode",
    "publicationState",
    "project",
    "variant",
    "version",
    "tag",
    "repository",
    "releaseManifest",
    "releaseNotes",
    "panelResult",
    "targets",
    "publicMutationPerformed",
}
REPOSITORY_KEYS = {
    "stableCommit",
    "tree",
    "srcTree",
    "officialAncestor",
    "originMain",
    "p7Candidate",
    "winners",
    "tagObject",
}
TARGET_KEYS = {
    "target",
    "executable",
    "panelExecutable",
    "packageExecutable",
    "packageVerification",
    "transcript",
    "bench",
}
BENCH_KEYS = {"runs"}
BENCH_RUN_KEYS = {"nodes", "signatureSha256"}


class EngineIdentityError(RuntimeError):
    """The release identity evidence violates the frozen contract."""


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EngineIdentityError("duplicate JSON key: " + key)
        value[key] = item
    return value


def require_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EngineIdentityError(label + " must be one JSON object")
    return value


def require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise EngineIdentityError(
            f"{label} keys differ: missing={sorted(expected - actual)!r} "
            f"extra={sorted(actual - expected)!r}"
        )


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except EngineIdentityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EngineIdentityError(f"invalid {label}: {error}") from error
    return require_object(value, label)


def regular_unlinked(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise EngineIdentityError(label + " is missing") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_nlink != 1
    ):
        raise EngineIdentityError(label + " must be one regular unlinked file")
    return metadata


def pinned_file(value: object, base: Path, label: str) -> Path:
    pin = require_object(value, label)
    require_keys(pin, FILE_PIN_KEYS, label)
    raw = pin["path"]
    size = pin["bytes"]
    digest = pin["sha256"]
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise EngineIdentityError(label + " path differs")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise EngineIdentityError(label + " byte count differs")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise EngineIdentityError(label + " digest differs")
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    path = Path(os.path.abspath(path))
    metadata = regular_unlinked(path, label)
    if metadata.st_size != size or sha256_file(path) != digest:
        raise EngineIdentityError(label + " bytes differ")
    return path


def git(repository: Path, *arguments: str, allowed: Iterable[int] = (0,)) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if completed.returncode not in set(allowed):
        raise EngineIdentityError(
            "git command failed: " + " ".join(arguments) + f" ({completed.returncode})"
        )
    try:
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise EngineIdentityError("git output is not UTF-8") from error
    if completed.returncode == 0 and stderr:
        raise EngineIdentityError("git command emitted stderr: " + " ".join(arguments))
    return stdout.strip()


def full_object(value: object, label: str) -> str:
    if not isinstance(value, str) or not OBJECT_ID.fullmatch(value):
        raise EngineIdentityError(label + " must be one lowercase full object ID")
    return value


def one_match(pattern: re.Pattern[str], text: str, label: str) -> str:
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise EngineIdentityError(label + " must occur exactly once")
    return matches[0]


def parse_version_header(text: str) -> tuple[int, int, int, str]:
    if re.search(r"^#define CRAZYHOUSE_VERSION(?!_H_INCLUDED)", text, re.M) or "getenv(" in text:
        raise EngineIdentityError("version authority permits an override")
    values = [
        int(one_match(HEADER_INTEGER[name], text, "version " + name))
        for name in ("major", "minor", "patch")
    ]
    version = one_match(HEADER_STRING, text, "version string")
    return values[0], values[1], values[2], version


def validate_misc(text: str) -> None:
    required = (
        '#include "crazyhouse_version.h"',
        "constexpr std::string_view version = CrazyhouseVersionString;",
        'ss << "Crazyhouse-Stockfish " << version',
        '"the Crazyhouse-Stockfish developers (see AUTHORS file)"',
    )
    for fragment in required:
        if text.count(fragment) != 1:
            raise EngineIdentityError("misc identity fragment differs: " + fragment)
    if 'ss << "Stockfish " << version' in text:
        raise EngineIdentityError("upstream-only engine name remains active")


def validate_authors(text: str) -> None:
    if "Crazyhouse-Stockfish contributors" not in text:
        raise EngineIdentityError("Crazyhouse contributor attribution is absent")
    if "complete upstream author" not in text or "Stockfish" not in text:
        raise EngineIdentityError("upstream Stockfish attribution boundary is absent")


def validate_development_source(source_root: Path) -> dict[str, object]:
    header = (source_root / "src" / "crazyhouse_version.h").read_text(
        encoding="utf-8"
    )
    misc = (source_root / "src" / "misc.cpp").read_text(encoding="utf-8")
    authors = (source_root / "AUTHORS").read_text(encoding="utf-8")
    if parse_version_header(header) != (0, 0, 0, "dev"):
        raise EngineIdentityError("development version authority is not 0.0.0/dev")
    validate_misc(misc)
    validate_authors(authors)
    return {
        "status": "PASS_DEVELOPMENT_ENGINE_IDENTITY_SOURCE",
        "stable": False,
        "version": "dev",
    }


def is_ancestor(repository: Path, ancestor: str, descendant: str, label: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", ancestor, descendant],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if completed.returncode != 0 or completed.stdout or completed.stderr:
        raise EngineIdentityError(label + " ancestry differs")


def validate_stable_source(
    repository: Path,
    commit: str,
    expected: tuple[int, int, int, str],
) -> None:
    header = git(repository, "show", commit + ":src/crazyhouse_version.h")
    misc = git(repository, "show", commit + ":src/misc.cpp")
    authors = git(repository, "show", commit + ":AUTHORS")
    if parse_version_header(header) != expected:
        raise EngineIdentityError("stable source version authority differs")
    validate_misc(misc)
    validate_authors(authors)


def validate_tag(
    repository: Path,
    tag: str,
    commit: str,
    state: str,
    tag_object: object,
) -> bool:
    ref = "refs/tags/" + tag
    if state == "PROPOSED":
        completed = subprocess.run(
            ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", ref],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if completed.returncode != 1 or completed.stdout or completed.stderr:
            raise EngineIdentityError("proposed stable tag already exists or tag query failed")
        if tag_object is not None:
            raise EngineIdentityError("proposed tag object must be null")
        return False
    if state != "TAGGED":
        raise EngineIdentityError("publicationState differs")
    expected_object = full_object(tag_object, "tag object")
    if git(repository, "cat-file", "-t", ref) != "tag":
        raise EngineIdentityError("stable tag is not annotated")
    actual_object = full_object(git(repository, "rev-parse", ref), "observed tag object")
    if actual_object != expected_object:
        raise EngineIdentityError("tag object identity differs")
    payload = git(repository, "cat-file", "-p", ref).splitlines()
    header: dict[str, str] = {}
    for line in payload:
        if not line:
            break
        key, separator, value = line.partition(" ")
        if not separator or key in header:
            raise EngineIdentityError("annotated tag header differs")
        header[key] = value
    if header.get("object") != commit or header.get("type") != "commit":
        raise EngineIdentityError("tag is nested or targets a different commit")
    if header.get("tag") != tag:
        raise EngineIdentityError("annotated tag name differs")
    if git(repository, "rev-parse", ref + "^{}") != commit:
        raise EngineIdentityError("peeled tag commit differs")
    return True


def transcript_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw:
                raise EngineIdentityError("transcript contains an empty record")
            value = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
            row = require_object(value, "transcript row")
            require_keys(row, {"direction", "line", "sequence"}, "transcript row")
            if row["direction"] not in {"in", "out"}:
                raise EngineIdentityError("transcript direction differs")
            if not isinstance(row["line"], str):
                raise EngineIdentityError("transcript line differs")
            if row["sequence"] != len(rows):
                raise EngineIdentityError("transcript sequence differs")
            rows.append(row)
    except EngineIdentityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EngineIdentityError("invalid transcript: " + str(error)) from error
    return rows


def verify_transcript(path: Path, version: str) -> None:
    rows = transcript_lines(path)
    output = [(row["sequence"], row["line"]) for row in rows if row["direction"] == "out"]
    expected = (
        f"Crazyhouse-Stockfish {version} by the Crazyhouse-Stockfish developers (see AUTHORS file)",
        f"id name Crazyhouse-Stockfish {version}",
        "id author the Crazyhouse-Stockfish developers (see AUTHORS file)",
        "uciok",
    )
    positions: list[int] = []
    for line in expected:
        matches = [sequence for sequence, observed in output if observed == line]
        if len(matches) != 1:
            raise EngineIdentityError("transcript identity line count differs: " + line)
        positions.append(matches[0])
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise EngineIdentityError("transcript identity order differs")
    if any(" dev-" in line or "nogit" in line for _, line in output):
        raise EngineIdentityError("development identity appears in stable transcript")


def verify_bench(value: object) -> tuple[int, str]:
    bench = require_object(value, "bench")
    require_keys(bench, BENCH_KEYS, "bench")
    runs = bench["runs"]
    if not isinstance(runs, list) or len(runs) < 2:
        raise EngineIdentityError("bench needs at least two runs")
    identities: list[tuple[int, str]] = []
    for run_value in runs:
        run = require_object(run_value, "bench run")
        require_keys(run, BENCH_RUN_KEYS, "bench run")
        nodes = run["nodes"]
        signature = run["signatureSha256"]
        if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes < 1:
            raise EngineIdentityError("bench nodes differ")
        if not isinstance(signature, str) or not DIGEST.fullmatch(signature):
            raise EngineIdentityError("bench signature differs")
        identities.append((nodes, signature))
    if len(set(identities)) != 1:
        raise EngineIdentityError("deterministic bench identity differs between runs")
    return identities[0]


def verify_release_manifest(
    path: Path,
    version: str,
    tag: str,
    commit: str,
    tree: str,
) -> dict[str, dict[str, object]]:
    value = load_json(path, "release manifest")
    for key, expected in {
        "schemaVersion": 1,
        "project": "Crazyhouse-Stockfish",
        "version": version,
        "tag": tag,
        "commit": commit,
        "tree": tree,
    }.items():
        if value.get(key) != expected:
            raise EngineIdentityError("release manifest " + key + " differs")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        raise EngineIdentityError("release manifest artifacts differ")
    native: dict[str, dict[str, object]] = {}
    for raw in artifacts:
        artifact = require_object(raw, "release artifact")
        provenance = require_object(artifact.get("provenance"), "release provenance")
        if provenance.get("kind") != "native":
            continue
        target = provenance.get("target")
        size = provenance.get("executableBytes")
        digest = provenance.get("executableSha256")
        if target not in TARGETS or target in native:
            raise EngineIdentityError("release native target inventory differs")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise EngineIdentityError("release executable size differs")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise EngineIdentityError("release executable digest differs")
        if (
            provenance.get("version") != version
            or provenance.get("tag") != tag
            or provenance.get("commit") != commit
            or provenance.get("tree") != tree
        ):
            raise EngineIdentityError("release native provenance identity differs")
        asset_name = artifact.get("name")
        asset_bytes = artifact.get("bytes")
        asset_digest = artifact.get("sha256")
        if not isinstance(asset_name, str) or not asset_name:
            raise EngineIdentityError("release native asset name differs")
        if isinstance(asset_bytes, bool) or not isinstance(asset_bytes, int) or asset_bytes < 1:
            raise EngineIdentityError("release native asset size differs")
        if not isinstance(asset_digest, str) or not DIGEST.fullmatch(asset_digest):
            raise EngineIdentityError("release native asset digest differs")
        native[target] = {
            "executable": (size, digest),
            "asset": (asset_name, asset_bytes, asset_digest),
        }
    if set(native) != set(TARGETS):
        raise EngineIdentityError("release manifest native targets differ")
    return native


def verify_package_result(
    path: Path,
    version: str,
    target: str,
    commit: str,
    tree: str,
    expected: dict[str, object],
) -> None:
    value = load_json(path, "native package verification")
    executable = expected["executable"]
    asset = expected["asset"]
    if not isinstance(executable, tuple) or not isinstance(asset, tuple):
        raise EngineIdentityError("release manifest package projection differs")
    checks = {
        "schema": "crazyhouse-native-package-verification/v1",
        "status": "PASS_NATIVE_PACKAGE_VERIFICATION",
        "asset": asset[0],
        "bytes": asset[1],
        "sha256": asset[2],
        "version": version,
        "target": target,
        "commit": commit,
        "tree": tree,
        "executableBytes": executable[0],
        "executableSha256": executable[1],
    }
    for key, expected_value in checks.items():
        if value.get(key) != expected_value:
            raise EngineIdentityError("native package verification " + key + " differs")


def verify_release_notes(path: Path, version: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise EngineIdentityError("release notes are unreadable") from error
    if text.splitlines()[:1] != [f"# Crazyhouse-Stockfish {version}"]:
        raise EngineIdentityError("release notes title differs")
    forbidden = ("X.Y.Z", "PLACEHOLDER", "TODO", "TBD")
    if any(marker in text for marker in forbidden):
        raise EngineIdentityError("release notes retain a placeholder")


def verify_panel_result(
    path: Path,
    version: str,
    commit: str,
    tree: str,
) -> dict[str, tuple[int, str]]:
    value = load_json(path, "panel result")
    expected_keys = {
        "schema",
        "status",
        "version",
        "candidateCommit",
        "candidateTree",
        "independentlyVerified",
        "allRungsPassed",
        "defects",
        "targets",
    }
    require_keys(value, expected_keys, "panel result")
    if (
        value["schema"] != PANEL_SCHEMA
        or value["status"] != "PASS"
        or value["version"] != version
        or value["candidateCommit"] != commit
        or value["candidateTree"] != tree
        or value["independentlyVerified"] is not True
        or value["allRungsPassed"] is not True
        or value["defects"] != 0
    ):
        raise EngineIdentityError("final panel identity or verdict differs")
    raw_targets = require_object(value["targets"], "panel targets")
    if set(raw_targets) != set(TARGETS):
        raise EngineIdentityError("panel targets differ")
    targets: dict[str, tuple[int, str]] = {}
    for target, raw in raw_targets.items():
        identity = require_object(raw, "panel target")
        require_keys(identity, {"bytes", "sha256"}, "panel target")
        size = identity["bytes"]
        digest = identity["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise EngineIdentityError("panel executable size differs")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise EngineIdentityError("panel executable digest differs")
        targets[target] = (size, digest)
    return targets


def verify_release_identity(
    contract_path: Path,
    repository: Path,
    evidence_path: Path,
) -> dict[str, object]:
    repository = repository.resolve(strict=True)
    evidence_path = Path(os.path.abspath(evidence_path))
    contract_path = Path(os.path.abspath(contract_path))
    regular_unlinked(contract_path, "identity contract")
    regular_unlinked(evidence_path, "identity evidence")
    contract = load_json(contract_path, "identity contract")
    evidence = load_json(evidence_path, "identity evidence")
    require_keys(evidence, EVIDENCE_KEYS, "identity evidence")

    prospective = require_object(
        contract.get("prospective_stable_identity"), "prospective identity"
    )
    version = prospective.get("semantic_version")
    tag = prospective.get("tag")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise EngineIdentityError("contract stable version differs")
    if tag != "v" + version:
        raise EngineIdentityError("contract tag differs")
    if (
        evidence["schema"] != EVIDENCE_SCHEMA
        or evidence["project"] != "Crazyhouse-Stockfish"
        or evidence["variant"] != "crazyhouse"
        or evidence["version"] != version
        or evidence["tag"] != tag
    ):
        raise EngineIdentityError("top-level identity differs")
    if evidence["evidenceMode"] not in {"SYNTHETIC", "REAL"}:
        raise EngineIdentityError("evidence mode differs")
    if evidence["publicMutationPerformed"] is not False:
        raise EngineIdentityError("identity verifier evidence performed a public mutation")

    repo = require_object(evidence["repository"], "repository identity")
    require_keys(repo, REPOSITORY_KEYS, "repository identity")
    stable = full_object(repo["stableCommit"], "stable commit")
    tree = full_object(repo["tree"], "stable tree")
    src_tree = full_object(repo["srcTree"], "stable src tree")
    official = full_object(repo["officialAncestor"], "official ancestor")
    origin_main = full_object(repo["originMain"], "origin main")
    p7 = full_object(repo["p7Candidate"], "P7 candidate")
    if git(repository, "cat-file", "-t", stable) != "commit":
        raise EngineIdentityError("stable object is not a commit")
    if git(repository, "rev-parse", stable + "^{tree}") != tree:
        raise EngineIdentityError("stable tree differs")
    if git(repository, "rev-parse", stable + ":src") != src_tree:
        raise EngineIdentityError("stable src tree differs")
    if git(repository, "rev-parse", "refs/remotes/origin/main") != origin_main:
        raise EngineIdentityError("origin/main identity differs")
    observed = require_object(contract.get("observed_p7_identity"), "observed P7")
    if p7 != observed.get("source_commit"):
        raise EngineIdentityError("P7 candidate differs from the frozen contract")
    frozen_official = require_object(
        contract.get("candidate_exactness"), "candidate exactness"
    )
    del frozen_official  # The exactness object is required even though ancestry is checked below.
    if official != "229f6339e537a097a79831cd06dbfdb3e623d4ac":
        raise EngineIdentityError("official Stockfish ancestor differs")
    is_ancestor(repository, official, stable, "official Stockfish")
    is_ancestor(repository, p7, stable, "P7 candidate")
    is_ancestor(repository, stable, origin_main, "stable origin/main")
    winners = repo["winners"]
    if not isinstance(winners, list) or not winners:
        raise EngineIdentityError("winner inventory is empty")
    normalized_winners = [full_object(item, "winner") for item in winners]
    if len(set(normalized_winners)) != len(normalized_winners):
        raise EngineIdentityError("winner inventory contains duplicates")
    for winner in normalized_winners:
        is_ancestor(repository, winner, stable, "accepted winner")

    source_authority = require_object(
        prospective.get("source_authority"), "stable source authority"
    )
    expected_version = (
        source_authority["major_constant"]["value"],
        source_authority["minor_constant"]["value"],
        source_authority["patch_constant"]["value"],
        source_authority["string_constant"]["value"],
    )
    if expected_version != (1, 0, 0, version):
        raise EngineIdentityError("stable source authority contract differs")
    validate_stable_source(repository, stable, expected_version)
    tag_authenticated = validate_tag(
        repository,
        tag,
        stable,
        evidence["publicationState"],
        repo["tagObject"],
    )

    base = evidence_path.parent
    manifest_path = pinned_file(evidence["releaseManifest"], base, "release manifest")
    notes_path = pinned_file(evidence["releaseNotes"], base, "release notes")
    panel_path = pinned_file(evidence["panelResult"], base, "panel result")
    manifest_targets = verify_release_manifest(manifest_path, version, tag, stable, tree)
    verify_release_notes(notes_path, version)
    panel_targets = verify_panel_result(panel_path, version, stable, tree)

    raw_targets = evidence["targets"]
    if not isinstance(raw_targets, list) or len(raw_targets) != len(TARGETS):
        raise EngineIdentityError("target evidence inventory differs")
    observed_targets: set[str] = set()
    target_results: list[dict[str, object]] = []
    for raw in raw_targets:
        target_value = require_object(raw, "target evidence")
        require_keys(target_value, TARGET_KEYS, "target evidence")
        target = target_value["target"]
        if target not in TARGETS or target in observed_targets:
            raise EngineIdentityError("target evidence name differs")
        observed_targets.add(target)
        executable = pinned_file(target_value["executable"], base, target + " executable")
        panel_executable = pinned_file(
            target_value["panelExecutable"], base, target + " panel executable"
        )
        package_executable = pinned_file(
            target_value["packageExecutable"], base, target + " package executable"
        )
        package_verification = pinned_file(
            target_value["packageVerification"],
            base,
            target + " package verification",
        )
        transcript = pinned_file(target_value["transcript"], base, target + " transcript")
        identities = [
            (path.stat().st_size, sha256_file(path))
            for path in (executable, panel_executable, package_executable)
        ]
        if len(set(identities)) != 1:
            raise EngineIdentityError(target + " panel/package executable bytes differ")
        identity = identities[0]
        manifest_identity = manifest_targets[target]["executable"]
        if identity != manifest_identity or identity != panel_targets[target]:
            raise EngineIdentityError(target + " manifest/panel executable identity differs")
        verify_package_result(
            package_verification,
            version,
            target,
            stable,
            tree,
            manifest_targets[target],
        )
        verify_transcript(transcript, version)
        nodes, signature = verify_bench(target_value["bench"])
        target_results.append(
            {
                "target": target,
                "executableBytes": identity[0],
                "executableSha256": identity[1],
                "benchNodes": nodes,
                "benchSignatureSha256": signature,
            }
        )
    if observed_targets != set(TARGETS):
        raise EngineIdentityError("target evidence set differs")

    return {
        "schema": "crazyhouse-release-engine-identity-verification/v1",
        "status": "PASS_RELEASE_ENGINE_IDENTITY",
        "evidenceMode": evidence["evidenceMode"],
        "publicationState": evidence["publicationState"],
        "project": "Crazyhouse-Stockfish",
        "variant": "crazyhouse",
        "version": version,
        "tag": tag,
        "tagAuthenticated": tag_authenticated,
        "stableCommit": stable,
        "tree": tree,
        "srcTree": src_tree,
        "originMain": origin_main,
        "p7Candidate": p7,
        "winners": normalized_winners,
        "targets": sorted(target_results, key=lambda item: str(item["target"])),
        "boundaries": {
            "identityChainVerified": True,
            "strengthResultRecomputed": False,
            "publicMutationPerformed": False,
            "stablePublicationAuthorized": False,
            "releasedMonitored": False,
        },
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--contract", type=Path)
    value.add_argument("--repository", type=Path)
    value.add_argument("--evidence", type=Path)
    value.add_argument("--output", type=Path)
    value.add_argument("--development-source", type=Path)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.development_source is not None:
        if any(item is not None for item in (args.contract, args.repository, args.evidence, args.output)):
            raise EngineIdentityError("development-source mode takes no release arguments")
        result = validate_development_source(args.development_source.resolve(strict=True))
    else:
        if args.contract is None or args.repository is None or args.evidence is None:
            raise EngineIdentityError("contract, repository and evidence are required")
        result = verify_release_identity(args.contract, args.repository, args.evidence)
    payload = canonical(result)
    if args.output is None:
        print(payload.decode("utf-8"), end="")
    else:
        output = args.output.resolve(strict=False)
        if output.exists():
            raise EngineIdentityError("output already exists")
        output.write_bytes(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EngineIdentityError as error:
        print("ERROR: " + str(error), file=__import__("sys").stderr)
        raise SystemExit(2)
