#!/usr/bin/env python3
"""Assemble the exact authenticated Crazyhouse-Stockfish release inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Optional, Sequence


MANIFEST_NAME = "crazyhouse-stockfish-release-manifest.json"
CHECKSUM_NAME = "SHA256SUMS"
PROVENANCE_SUFFIX = ".provenance.json"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")

NETWORK = {
    "alias": "Crazyhouse_v1.nnue",
    "bytes": 58_534_811,
    "sha256": "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43",
    "license": "CC0-1.0",
    "assetBlob": "ad269c33db13ecae295ec66ee9f438462498c623",
    "licenseBlob": "c94bf53d0cd54599d899a51f0aa4c1e01e4f0b94",
    "deliveryMode": "external-file-in-each-native-archive",
}
TESTING_BOOK = {
    "distributed": False,
    "bytes": 100_204,
    "sha256": "a8976a380a6cc4b3a1a6aae3bf14249b2ab6d1bac6cf4a2715625d7c01747603",
    "roots": 1_024,
    "license": "CC0-1.0",
}
TARGETS: dict[str, dict[str, Any]] = {
    "windows-x86-64": {
        "platform": "windows",
        "architecture": "x86-64",
        "makeArch": "x86-64",
        "featureFloor": ["x86-64", "sse2"],
    },
    "windows-x86-64-avx2": {
        "platform": "windows",
        "architecture": "x86-64-avx2",
        "makeArch": "x86-64-avx2",
        "featureFloor": [
            "x86-64",
            "avx2",
            "bmi1",
            "popcnt",
            "sse4.1",
            "ssse3",
            "sse2",
        ],
    },
}
NATIVE_KEYS = {
    "schemaVersion",
    "asset",
    "version",
    "tag",
    "commit",
    "tree",
    "sourceDateEpoch",
    "kind",
    "target",
    "platform",
    "architecture",
    "makeArch",
    "featureFloor",
    "toolchain",
    "buildCommand",
    "executableBytes",
    "executableSha256",
    "packageInventorySha256",
    "sbomSha256",
    "networkAlias",
    "networkBytes",
    "networkSha256",
    "networkLicense",
    "networkAssetBlob",
    "networkLicenseBlob",
    "testingBookBytes",
    "testingBookSha256",
    "testingBookRoots",
    "testingBookLicense",
    "correspondingSourceAsset",
    "sha256",
}
SOURCE_KEYS = {
    "schemaVersion",
    "asset",
    "version",
    "tag",
    "commit",
    "tree",
    "sourceDateEpoch",
    "kind",
    "target",
    "license",
    "correspondingSource",
    "archiveCommand",
    "sha256",
}
TOOLCHAIN_KEYS = {
    "id",
    "compiler",
    "compilerVersion",
    "compilerBytes",
    "compilerSha256",
    "make",
    "makeVersion",
    "makeBytes",
    "makeSha256",
}


class ReleaseContractError(RuntimeError):
    """The candidate bundle violates the frozen Crazyhouse release contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_asset_name(version: str) -> str:
    return f"crazyhouse-stockfish-{version}-source.tar.xz"


def native_asset_name(version: str, target: str) -> str:
    if target not in TARGETS:
        raise ReleaseContractError("unsupported Crazyhouse release target: " + target)
    return f"crazyhouse-stockfish-{version}-{target}.zip"


def expected_asset_names(version: str) -> set[str]:
    return {source_asset_name(version)} | {
        native_asset_name(version, target) for target in TARGETS
    }


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseContractError("duplicate JSON key in provenance: " + key)
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseContractError(f"invalid provenance {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseContractError("provenance must be one JSON object: " + str(path))
    return value


def _is_regular_unlinked(path: Path) -> bool:
    try:
        value = path.lstat()
        return (
            stat.S_ISREG(value.st_mode)
            and not path.is_symlink()
            and value.st_nlink == 1
        )
    except OSError:
        return False


def _require_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ReleaseContractError(label + " must be one lowercase SHA-256")


def _require_positive_integer(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ReleaseContractError(label + " must be a positive integer")


def _require_command(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(part, str) or not part for part in value)
    ):
        raise ReleaseContractError(label + " must contain non-empty arguments")
    return value


def _asset_identity(asset_name: str, version: str) -> tuple[str, Optional[str]]:
    if asset_name == source_asset_name(version):
        return "source", None
    for target in TARGETS:
        if asset_name == native_asset_name(version, target):
            return "native", target
    raise ReleaseContractError("unexpected release asset name: " + asset_name)


def validate_provenance(
    value: dict[str, Any],
    asset_name: str,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
) -> None:
    kind, target = _asset_identity(asset_name, version)
    expected_keys = NATIVE_KEYS if kind == "native" else SOURCE_KEYS
    if set(value) != expected_keys:
        missing = sorted(expected_keys - set(value))
        extra = sorted(set(value) - expected_keys)
        raise ReleaseContractError(
            f"provenance keys differ for {asset_name} (missing={missing} extra={extra})"
        )
    common = {
        "schemaVersion": 1,
        "asset": asset_name,
        "version": version,
        "tag": "v" + version,
        "commit": commit,
        "tree": tree,
        "sourceDateEpoch": source_date_epoch,
        "kind": kind,
    }
    for key, expected in common.items():
        if value[key] != expected:
            raise ReleaseContractError(
                f"{asset_name} provenance {key} mismatch: {value[key]!r} != {expected!r}"
            )
    _require_digest(value["sha256"], asset_name + " provenance sha256")

    if kind == "source":
        expected_source = {
            "target": "corresponding-source",
            "license": "GPL-3.0-or-later",
            "correspondingSource": True,
        }
        for key, expected in expected_source.items():
            if value[key] != expected:
                raise ReleaseContractError(
                    f"{asset_name} source provenance {key} mismatch"
                )
        _require_command(value["archiveCommand"], "archiveCommand")
        return

    assert target is not None
    target_contract = TARGETS[target]
    expected_native = {
        "target": target,
        "platform": target_contract["platform"],
        "architecture": target_contract["architecture"],
        "makeArch": target_contract["makeArch"],
        "featureFloor": target_contract["featureFloor"],
        "networkAlias": NETWORK["alias"],
        "networkBytes": NETWORK["bytes"],
        "networkSha256": NETWORK["sha256"],
        "networkLicense": NETWORK["license"],
        "networkAssetBlob": NETWORK["assetBlob"],
        "networkLicenseBlob": NETWORK["licenseBlob"],
        "testingBookBytes": TESTING_BOOK["bytes"],
        "testingBookSha256": TESTING_BOOK["sha256"],
        "testingBookRoots": TESTING_BOOK["roots"],
        "testingBookLicense": TESTING_BOOK["license"],
        "correspondingSourceAsset": source_asset_name(version),
    }
    for key, expected in expected_native.items():
        if value[key] != expected:
            raise ReleaseContractError(
                f"{asset_name} native provenance {key} mismatch: {value[key]!r} != {expected!r}"
            )

    toolchain = value["toolchain"]
    if not isinstance(toolchain, dict) or set(toolchain) != TOOLCHAIN_KEYS:
        raise ReleaseContractError("toolchain keys differ for " + asset_name)
    for key in ("id", "compiler", "compilerVersion", "make", "makeVersion"):
        if not isinstance(toolchain[key], str) or not toolchain[key].strip():
            raise ReleaseContractError("empty toolchain field: " + key)
        if "/" in toolchain[key] or "\\" in toolchain[key]:
            raise ReleaseContractError("toolchain field contains a host path: " + key)
    for key in ("compilerBytes", "makeBytes"):
        _require_positive_integer(toolchain[key], "toolchain." + key)
    for key in ("compilerSha256", "makeSha256"):
        _require_digest(toolchain[key], "toolchain." + key)

    command = _require_command(value["buildCommand"], "buildCommand")
    command_text = " ".join(command)
    required_tokens = (
        "make",
        "-j1",
        "build",
        "ARCH=" + target_contract["makeArch"],
        "COMP=gcc",
        "OS=Windows_NT",
        "EXTRACXXFLAGS=-Werror",
        "mingw_reproducible=yes",
    )
    if any(token not in command_text for token in required_tokens):
        raise ReleaseContractError("buildCommand omits a frozen recipe token")
    _require_positive_integer(value["executableBytes"], "executableBytes")
    for key in (
        "executableSha256",
        "packageInventorySha256",
        "sbomSha256",
    ):
        _require_digest(value[key], key)


def discover_assets(
    input_root: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
) -> list[tuple[Path, dict[str, Any]]]:
    root = input_root.resolve(strict=True)
    if not root.is_dir():
        raise ReleaseContractError("release input root is not a directory")
    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and not path.name.endswith(PROVENANCE_SUFFIX)
        ),
        key=lambda item: (item.name.casefold(), item.as_posix()),
    )
    if not candidates:
        raise ReleaseContractError("release input contains no assets")

    seen: set[str] = set()
    discovered: list[tuple[Path, dict[str, Any]]] = []
    for asset in candidates:
        name = asset.name
        folded = name.casefold()
        if (
            name in {MANIFEST_NAME, CHECKSUM_NAME}
            or not SAFE_NAME.fullmatch(name)
            or folded in seen
            or not _is_regular_unlinked(asset)
        ):
            raise ReleaseContractError("unsafe, linked, or duplicate asset: " + name)
        seen.add(folded)
        try:
            asset.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise ReleaseContractError("release asset escapes input root: " + name) from error

        provenance_path = asset.with_name(name + PROVENANCE_SUFFIX)
        if not _is_regular_unlinked(provenance_path):
            raise ReleaseContractError("missing regular unlinked provenance for " + name)
        provenance = _load_json(provenance_path)
        validate_provenance(
            provenance, name, version, commit, tree, source_date_epoch
        )
        if sha256(asset) != provenance["sha256"]:
            raise ReleaseContractError("provenance SHA-256 mismatch for " + name)
        discovered.append((asset, provenance))

    expected = expected_asset_names(version)
    actual = {asset.name for asset, _ in discovered}
    if actual != expected:
        raise ReleaseContractError(
            "release inventory mismatch "
            f"(missing={sorted(expected - actual)} extra={sorted(actual - expected)})"
        )
    orphaned = sorted(
        path.name
        for path in root.rglob("*" + PROVENANCE_SUFFIX)
        if path.is_file()
        and path.name[: -len(PROVENANCE_SUFFIX)].casefold() not in seen
    )
    if orphaned:
        raise ReleaseContractError("orphaned provenance descriptors: " + repr(orphaned))
    return discovered


def _copy_authenticated(source: Path, destination: Path) -> tuple[int, str]:
    before = source.lstat()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(source), flags)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
    ):
        os.close(descriptor)
        raise ReleaseContractError("release asset changed before copying: " + source.name)

    source_digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as reader, destination.open("xb") as writer:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                source_digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    after = source.lstat()
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
    )
    copied_digest = sha256(destination)
    if (
        before_identity != after_identity
        or copied_digest != source_digest.hexdigest()
        or destination.stat().st_size != before.st_size
    ):
        destination.unlink(missing_ok=True)
        raise ReleaseContractError("release asset changed while copying: " + source.name)
    return before.st_size, copied_digest


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def assemble(
    input_root: Path,
    output_dir: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
) -> dict[str, Any]:
    if not SEMVER.fullmatch(version):
        raise ReleaseContractError("release version must be X.Y.Z")
    commit = commit.lower()
    tree = tree.lower()
    if not OBJECT_ID.fullmatch(commit) or not OBJECT_ID.fullmatch(tree):
        raise ReleaseContractError("commit and tree must be full lowercase object IDs")
    if source_date_epoch < 0:
        raise ReleaseContractError("source-date epoch must be non-negative")

    assets = discover_assets(
        input_root, version, commit, tree, source_date_epoch
    )
    output = output_dir.resolve()
    if output.exists():
        raise ReleaseContractError("release output already exists: " + str(output))
    output.mkdir(parents=True, exist_ok=False)

    try:
        entries: list[dict[str, Any]] = []
        for source, provenance in assets:
            size, digest = _copy_authenticated(source, output / source.name)
            if digest != provenance["sha256"]:
                raise ReleaseContractError(
                    "asset changed after provenance authentication: " + source.name
                )
            entries.append(
                {
                    "name": source.name,
                    "bytes": size,
                    "sha256": digest,
                    "provenance": provenance,
                }
            )
        entries.sort(key=lambda item: item["name"])
        manifest: dict[str, Any] = {
            "schemaVersion": 1,
            "project": "Crazyhouse-Stockfish",
            "version": version,
            "tag": "v" + version,
            "commit": commit,
            "tree": tree,
            "sourceDateEpoch": source_date_epoch,
            "network": NETWORK,
            "testingBook": TESTING_BOOK,
            "artifacts": entries,
        }
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8")
        _write_new(output / MANIFEST_NAME, manifest_bytes)

        checksums = [(item["name"], item["sha256"]) for item in entries]
        checksums.append((MANIFEST_NAME, sha256(output / MANIFEST_NAME)))
        checksum_bytes = "".join(
            f"{digest}  {name}\n" for name, digest in sorted(checksums)
        ).encode("ascii")
        _write_new(output / CHECKSUM_NAME, checksum_bytes)
        return manifest
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    manifest = assemble(
        args.input_root,
        args.output_dir,
        args.version,
        args.commit,
        args.tree,
        args.source_date_epoch,
    )
    print(
        f"assembled {len(manifest['artifacts'])} authenticated "
        f"Crazyhouse-Stockfish {manifest['version']} payload assets"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
