#!/usr/bin/env python3
"""Independently reauthenticate a downloaded Crazyhouse release draft."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Optional, Sequence


MANIFEST_NAME = "crazyhouse-stockfish-release-manifest.json"
CHECKSUM_NAME = "SHA256SUMS"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
TARGETS = {
    "windows-x86-64": {
        "architecture": "x86-64",
        "makeArch": "x86-64",
        "featureFloor": ["x86-64", "sse2"],
    },
    "windows-x86-64-avx2": {
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
MANIFEST_KEYS = {
    "schemaVersion",
    "project",
    "version",
    "tag",
    "commit",
    "tree",
    "sourceDateEpoch",
    "network",
    "testingBook",
    "artifacts",
}
ARTIFACT_KEYS = {"name", "bytes", "sha256", "provenance"}


class ReleaseDownloadError(RuntimeError):
    """The downloaded draft differs from the exact authenticated candidate."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_names(version: str) -> set[str]:
    return {
        f"crazyhouse-stockfish-{version}-windows-x86-64.zip",
        f"crazyhouse-stockfish-{version}-windows-x86-64-avx2.zip",
        f"crazyhouse-stockfish-{version}-source.tar.xz",
    }


def _inventory(root: Path) -> dict[str, tuple[int, str]]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ReleaseDownloadError("release asset root is not a directory")
    inventory: dict[str, tuple[int, str]] = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_nlink != 1
            or path.name.casefold() in {name.casefold() for name in inventory}
        ):
            raise ReleaseDownloadError(
                "release root must contain unique regular unlinked files: " + path.name
            )
        inventory[path.name] = (metadata.st_size, _sha256(path))
    if not inventory:
        raise ReleaseDownloadError("release asset root is empty")
    return inventory


def _checksum_contract(
    root: Path, inventory: dict[str, tuple[int, str]], expected_names: set[str]
) -> None:
    checksum_path = root / CHECKSUM_NAME
    if CHECKSUM_NAME not in inventory:
        raise ReleaseDownloadError("draft omits SHA256SUMS")
    declared: dict[str, str] = {}
    try:
        payload = checksum_path.read_bytes()
        text = payload.decode("ascii")
    except (OSError, UnicodeError) as error:
        raise ReleaseDownloadError("SHA256SUMS is not strict ASCII") from error
    if not text.endswith("\n") or "\r" in text:
        raise ReleaseDownloadError("SHA256SUMS must use LF and end with one LF")
    for line in text.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise ReleaseDownloadError("invalid SHA256SUMS line")
        digest, name = line[:64], line[66:]
        if (
            not DIGEST.fullmatch(digest)
            or not name
            or "/" in name
            or "\\" in name
            or name in declared
        ):
            raise ReleaseDownloadError("invalid SHA256SUMS entry")
        declared[name] = digest
    if set(declared) != expected_names:
        raise ReleaseDownloadError("SHA256SUMS file list differs from the draft")
    for name, expected_digest in declared.items():
        if inventory.get(name, (-1, ""))[1] != expected_digest:
            raise ReleaseDownloadError("SHA256SUMS mismatch for " + name)


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseDownloadError("duplicate manifest JSON key: " + key)
        value[key] = item
    return value


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseDownloadError("invalid global manifest") from error
    if not isinstance(value, dict) or set(value) != MANIFEST_KEYS:
        raise ReleaseDownloadError("global manifest keys differ")
    return value


def _validate_manifest(
    root: Path,
    inventory: dict[str, tuple[int, str]],
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
) -> None:
    manifest = _load_manifest(root / MANIFEST_NAME)
    expected_common = {
        "schemaVersion": 1,
        "project": "Crazyhouse-Stockfish",
        "version": version,
        "tag": "v" + version,
        "commit": commit,
        "tree": tree,
        "sourceDateEpoch": source_date_epoch,
        "network": NETWORK,
        "testingBook": TESTING_BOOK,
    }
    for key, expected in expected_common.items():
        if manifest[key] != expected:
            raise ReleaseDownloadError("global manifest mismatch: " + key)
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 3:
        raise ReleaseDownloadError("global manifest artifact count differs")

    source_name = f"crazyhouse-stockfish-{version}-source.tar.xz"
    seen: set[str] = set()
    for entry in artifacts:
        if not isinstance(entry, dict) or set(entry) != ARTIFACT_KEYS:
            raise ReleaseDownloadError("global manifest artifact keys differ")
        name = entry["name"]
        if not isinstance(name, str) or name in seen:
            raise ReleaseDownloadError("global manifest duplicate artifact")
        seen.add(name)
        if name not in inventory:
            raise ReleaseDownloadError("manifest names an absent artifact")
        size, digest = inventory[name]
        if entry["bytes"] != size or entry["sha256"] != digest:
            raise ReleaseDownloadError("manifest byte identity mismatch for " + name)
        provenance = entry["provenance"]
        if not isinstance(provenance, dict):
            raise ReleaseDownloadError("embedded provenance is not an object")
        for key, expected in {
            "schemaVersion": 1,
            "asset": name,
            "version": version,
            "tag": "v" + version,
            "commit": commit,
            "tree": tree,
            "sourceDateEpoch": source_date_epoch,
            "sha256": digest,
        }.items():
            if provenance.get(key) != expected:
                raise ReleaseDownloadError(
                    "embedded provenance mismatch for " + name + ": " + key
                )
        if name == source_name:
            expected_source = {
                "kind": "source",
                "target": "corresponding-source",
                "license": "GPL-3.0-or-later",
                "correspondingSource": True,
            }
            if any(provenance.get(k) != v for k, v in expected_source.items()):
                raise ReleaseDownloadError("source provenance relationship differs")
        else:
            target = name[
                len(f"crazyhouse-stockfish-{version}-") : -len(".zip")
            ]
            target_contract = TARGETS.get(target)
            if target_contract is None:
                raise ReleaseDownloadError("native target is not frozen")
            expected_native = {
                "kind": "native",
                "target": target,
                "platform": "windows",
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
                "correspondingSourceAsset": source_name,
            }
            if any(provenance.get(k) != v for k, v in expected_native.items()):
                raise ReleaseDownloadError("native provenance relationship differs")
    if seen != _payload_names(version):
        raise ReleaseDownloadError("global manifest payload inventory differs")


def verify_release_download(
    local: Path,
    downloaded: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
) -> int:
    if not SEMVER.fullmatch(version):
        raise ReleaseDownloadError("version must be X.Y.Z")
    commit = commit.lower()
    tree = tree.lower()
    if not OBJECT_ID.fullmatch(commit) or not OBJECT_ID.fullmatch(tree):
        raise ReleaseDownloadError("commit and tree must be full object IDs")
    if source_date_epoch < 0:
        raise ReleaseDownloadError("source-date epoch must be non-negative")

    local_inventory = _inventory(local)
    downloaded_inventory = _inventory(downloaded)
    expected_all = _payload_names(version) | {MANIFEST_NAME, CHECKSUM_NAME}
    if set(local_inventory) != expected_all or set(downloaded_inventory) != expected_all:
        raise ReleaseDownloadError("release draft inventory differs from frozen set")
    if local_inventory != downloaded_inventory:
        if set(local_inventory) != set(downloaded_inventory):
            raise ReleaseDownloadError("downloaded file list differs from local draft")
        differing = sorted(
            name
            for name in local_inventory
            if local_inventory[name] != downloaded_inventory[name]
        )
        raise ReleaseDownloadError(
            "downloaded bytes differ for: " + ", ".join(differing)
        )
    checksum_names = _payload_names(version) | {MANIFEST_NAME}
    _checksum_contract(downloaded.resolve(strict=True), downloaded_inventory, checksum_names)
    _validate_manifest(
        downloaded.resolve(strict=True),
        downloaded_inventory,
        version,
        commit,
        tree,
        source_date_epoch,
    )
    return len(downloaded_inventory)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--downloaded", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    count = verify_release_download(
        args.local,
        args.downloaded,
        args.version,
        args.commit,
        args.tree,
        args.source_date_epoch,
    )
    print(f"authenticated {count} downloaded Crazyhouse release files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
