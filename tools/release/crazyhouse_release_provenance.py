#!/usr/bin/env python3
"""Write one frozen provenance descriptor for a Crazyhouse release asset."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
from typing import Any, Optional, Sequence

try:
    from .crazyhouse_release_manifest import (
        NETWORK,
        OBJECT_ID,
        PROVENANCE_SUFFIX,
        SAFE_NAME,
        SEMVER,
        TARGETS,
        TESTING_BOOK,
        native_asset_name,
        sha256,
        source_asset_name,
        validate_provenance,
    )
except ImportError:
    from crazyhouse_release_manifest import (
        NETWORK,
        OBJECT_ID,
        PROVENANCE_SUFFIX,
        SAFE_NAME,
        SEMVER,
        TARGETS,
        TESTING_BOOK,
        native_asset_name,
        sha256,
        source_asset_name,
        validate_provenance,
    )


def _asset_path(asset: Path) -> Path:
    value = Path(os.path.abspath(asset))
    try:
        metadata = value.lstat()
    except OSError as error:
        raise ValueError("asset must be one regular unlinked file") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or value.is_symlink()
        or metadata.st_nlink != 1
        or not SAFE_NAME.fullmatch(value.name)
    ):
        raise ValueError("asset must be one regular unlinked file with a safe basename")
    return value


def _common(
    asset: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    kind: str,
) -> dict[str, Any]:
    commit = commit.lower()
    tree = tree.lower()
    if not SEMVER.fullmatch(version):
        raise ValueError("version must be X.Y.Z")
    if not OBJECT_ID.fullmatch(commit) or not OBJECT_ID.fullmatch(tree):
        raise ValueError("commit and tree must be full lowercase object IDs")
    if source_date_epoch < 0:
        raise ValueError("source-date epoch must be non-negative")
    return {
        "schemaVersion": 1,
        "asset": asset.name,
        "version": version,
        "tag": "v" + version,
        "commit": commit,
        "tree": tree,
        "sourceDateEpoch": source_date_epoch,
        "kind": kind,
    }


def _write(asset: Path, value: dict[str, Any]) -> Path:
    output = asset.with_name(asset.name + PROVENANCE_SUFFIX)
    payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    with output.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return output


def write_native_provenance(
    asset: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    target: str,
    toolchain: dict[str, Any],
    build_command: Sequence[str],
    executable_bytes: int,
    executable_sha256: str,
    package_inventory_sha256: str,
    sbom_sha256: str,
) -> Path:
    asset = _asset_path(asset)
    if target not in TARGETS or asset.name != native_asset_name(version, target):
        raise ValueError("asset basename does not match the selected native target")
    value = _common(asset, version, commit, tree, source_date_epoch, "native")
    target_contract = TARGETS[target]
    value.update(
        {
            "target": target,
            "platform": target_contract["platform"],
            "architecture": target_contract["architecture"],
            "makeArch": target_contract["makeArch"],
            "featureFloor": target_contract["featureFloor"],
            "toolchain": toolchain,
            "buildCommand": list(build_command),
            "executableBytes": executable_bytes,
            "executableSha256": executable_sha256,
            "packageInventorySha256": package_inventory_sha256,
            "sbomSha256": sbom_sha256,
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
            "sha256": sha256(asset),
        }
    )
    validate_provenance(value, asset.name, version, commit, tree, source_date_epoch)
    return _write(asset, value)


def write_source_provenance(
    asset: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    archive_command: Sequence[str],
) -> Path:
    asset = _asset_path(asset)
    if asset.name != source_asset_name(version):
        raise ValueError("asset basename does not match corresponding-source contract")
    value = _common(asset, version, commit, tree, source_date_epoch, "source")
    value.update(
        {
            "target": "corresponding-source",
            "license": "GPL-3.0-or-later",
            "correspondingSource": True,
            "archiveCommand": list(archive_command),
            "sha256": sha256(asset),
        }
    )
    validate_provenance(value, asset.name, version, commit, tree, source_date_epoch)
    return _write(asset, value)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="kind", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--asset", type=Path, required=True)
    common.add_argument("--version", required=True)
    common.add_argument("--commit", required=True)
    common.add_argument("--tree", required=True)
    common.add_argument("--source-date-epoch", type=int, required=True)

    source = subparsers.add_parser("source", parents=[common])
    source.add_argument("archive_command", nargs=argparse.REMAINDER)

    native = subparsers.add_parser("native", parents=[common])
    native.add_argument("--target", choices=tuple(TARGETS), required=True)
    native.add_argument("--toolchain-json", type=Path, required=True)
    native.add_argument("--executable-bytes", type=int, required=True)
    native.add_argument("--executable-sha256", required=True)
    native.add_argument("--package-inventory-sha256", required=True)
    native.add_argument("--sbom-sha256", required=True)
    native.add_argument("build_command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def _after_separator(arguments: list[str]) -> list[str]:
    return arguments[1:] if arguments[:1] == ["--"] else arguments


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.kind == "source":
        output = write_source_provenance(
            args.asset,
            args.version,
            args.commit,
            args.tree,
            args.source_date_epoch,
            _after_separator(args.archive_command),
        )
    else:
        toolchain = json.loads(args.toolchain_json.read_text(encoding="utf-8"))
        output = write_native_provenance(
            args.asset,
            args.version,
            args.commit,
            args.tree,
            args.source_date_epoch,
            args.target,
            toolchain,
            _after_separator(args.build_command),
            args.executable_bytes,
            args.executable_sha256,
            args.package_inventory_sha256,
            args.sbom_sha256,
        )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
