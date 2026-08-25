#!/usr/bin/env python3
"""Build a deterministic, fail-closed Crazyhouse native release ZIP."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Optional, Sequence
import zipfile


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
TARGETS = ("windows-x86-64", "windows-x86-64-avx2")
EXECUTABLE_MEMBER = "bin/crazyhouse-stockfish.exe"
NETWORK_MEMBER = "networks/Crazyhouse_v1.nnue"
INVENTORY_MEMBER = "inventory/FILES.json"
SBOM_MEMBER = "inventory/SBOM.spdx.json"
EXACT_MEMBERS = (
    "AUTHORS",
    "CITATION.cff",
    "Copying.txt",
    "README.md",
    "SOURCE.md",
    EXECUTABLE_MEMBER,
    "docs/RELEASE_NOTES_DRAFT.md",
    "docs/RULE_PROFILE.md",
    INVENTORY_MEMBER,
    SBOM_MEMBER,
    "licenses/CC0-1.0-NOTICE.md",
    "networks/README.md",
    NETWORK_MEMBER,
)
REQUIRED_INPUT_MEMBERS = tuple(
    member for member in EXACT_MEMBERS if member not in {INVENTORY_MEMBER, SBOM_MEMBER}
)
MEDIA_TYPES = {
    "AUTHORS": "text/plain; charset=utf-8",
    "CITATION.cff": "application/yaml; charset=utf-8",
    "Copying.txt": "text/plain; charset=utf-8",
    "README.md": "text/markdown; charset=utf-8",
    "SOURCE.md": "text/markdown; charset=utf-8",
    EXECUTABLE_MEMBER: "application/vnd.microsoft.portable-executable",
    "docs/RELEASE_NOTES_DRAFT.md": "text/markdown; charset=utf-8",
    "docs/RULE_PROFILE.md": "text/markdown; charset=utf-8",
    SBOM_MEMBER: "application/spdx+json",
    "licenses/CC0-1.0-NOTICE.md": "text/markdown; charset=utf-8",
    "networks/README.md": "text/markdown; charset=utf-8",
    NETWORK_MEMBER: "application/octet-stream",
}
ENGINE_LICENSE = "GPL-3.0-or-later"
NETWORK_ALIAS = "Crazyhouse_v1.nnue"
NETWORK_BYTES = 58_534_811
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
NETWORK_LICENSE = "CC0-1.0"
NETWORK_ASSET_BLOB = "ad269c33db13ecae295ec66ee9f438462498c623"
NETWORK_LICENSE_BLOB = "c94bf53d0cd54599d899a51f0aa4c1e01e4f0b94"


class NativePackageError(RuntimeError):
    """The native package input or output violates the frozen contract."""


@dataclass(frozen=True)
class NetworkPolicy:
    schema: str
    bytes: int
    sha256: str
    license: str
    asset_blob: str
    license_blob: str
    release_evidence: bool


PRODUCTION_NETWORK_POLICY = NetworkPolicy(
    schema="crazyhouse-native-production-network-policy/v1",
    bytes=NETWORK_BYTES,
    sha256=NETWORK_SHA256,
    license=NETWORK_LICENSE,
    asset_blob=NETWORK_ASSET_BLOB,
    license_blob=NETWORK_LICENSE_BLOB,
    release_evidence=True,
)


def fixture_network_policy(payload: bytes) -> NetworkPolicy:
    """Return a schema-distinct policy unavailable from the production CLI."""

    return NetworkPolicy(
        schema="crazyhouse-native-test-network-policy/v1",
        bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        license=NETWORK_LICENSE,
        asset_blob="fixture-asset-not-release-evidence",
        license_blob="fixture-license-not-release-evidence",
        release_evidence=False,
    )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def root_name(version: str) -> str:
    return f"Crazyhouse-Stockfish-{version}"


def archive_name(version: str, target: str) -> str:
    return f"crazyhouse-stockfish-{version}-{target}.zip"


def source_asset_name(version: str) -> str:
    return f"crazyhouse-stockfish-{version}-source.tar.xz"


def expected_source_markdown(version: str, commit: str, tree: str) -> bytes:
    return (
        "# Corresponding source\n"
        "\n"
        f"This package was built from Crazyhouse-Stockfish commit {commit} "
        f"(tree {tree}).\n"
        f"The complete corresponding source is distributed as "
        f"{source_asset_name(version)}.\n"
        f"License: {ENGINE_LICENSE}.\n"
    ).encode("ascii")


def expected_network_readme(policy: NetworkPolicy) -> bytes:
    return (
        "# Crazyhouse legacy network\n"
        "\n"
        f"Package alias: {NETWORK_ALIAS}\n"
        f"Bytes: {policy.bytes}\n"
        f"SHA-256: {policy.sha256}\n"
        f"License: {policy.license}\n"
        f"Lila asset blob: {policy.asset_blob}\n"
        f"Lila license declaration blob: {policy.license_blob}\n"
        f"Policy schema: {policy.schema}\n"
        f"Release evidence: {'true' if policy.release_evidence else 'false'}\n"
        "\n"
        "The package alias is byte-identical to the authenticated source bytes.\n"
        "Set CrazyhouseEvalFile to this extracted file explicitly; no fallback "
        "is permitted.\n"
    ).encode("ascii")


def expected_cc0_notice(policy: NetworkPolicy) -> bytes:
    return (
        "# CC0 1.0 notice\n"
        "\n"
        "The packaged Crazyhouse legacy network is made available under "
        f"{policy.license}.\n"
        f"License declaration blob: {policy.license_blob}.\n"
        "This notice does not change the GPL license of the engine executable "
        "or corresponding source.\n"
    ).encode("ascii")


def _validate_identity(
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    target: str,
    executable_bytes: int,
    executable_sha256: str,
    policy: NetworkPolicy,
) -> tuple[int, int, int, int, int, int]:
    if not SEMVER.fullmatch(version):
        raise NativePackageError("version must be X.Y.Z")
    if not OBJECT_ID.fullmatch(commit) or not OBJECT_ID.fullmatch(tree):
        raise NativePackageError("commit and tree must be lowercase full object IDs")
    if isinstance(source_date_epoch, bool) or not isinstance(source_date_epoch, int):
        raise NativePackageError("source-date epoch must be an integer")
    if source_date_epoch < 0:
        raise NativePackageError("source-date epoch must be non-negative")
    try:
        instant = datetime.fromtimestamp(source_date_epoch, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise NativePackageError("source-date epoch is outside the ZIP range") from error
    if instant.year < 1980 or instant.year > 2107:
        raise NativePackageError("source-date epoch is outside the DOS ZIP range")
    if target not in TARGETS:
        raise NativePackageError("unsupported native target")
    if (
        isinstance(executable_bytes, bool)
        or not isinstance(executable_bytes, int)
        or executable_bytes < 1
    ):
        raise NativePackageError("executable byte count must be positive")
    if not DIGEST.fullmatch(executable_sha256):
        raise NativePackageError("executable SHA-256 must be lowercase hex")
    if (
        not isinstance(policy, NetworkPolicy)
        or policy.bytes < 1
        or not DIGEST.fullmatch(policy.sha256)
        or not policy.license
        or not policy.asset_blob
        or not policy.license_blob
    ):
        raise NativePackageError("network policy is invalid")
    return (
        instant.year,
        instant.month,
        instant.day,
        instant.hour,
        instant.minute,
        instant.second - instant.second % 2,
    )


def _regular_unlinked_bytes(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise NativePackageError("required input is missing: " + path.as_posix()) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_nlink != 1
    ):
        raise NativePackageError(
            "input must be one regular unlinked file: " + path.as_posix()
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise NativePackageError("input could not be read: " + path.as_posix()) from error


def _validate_inputs(input_paths: Mapping[str, Path]) -> dict[str, bytes]:
    names = list(input_paths)
    folded: set[str] = set()
    for name in names:
        if not isinstance(name, str) or name.casefold() in folded:
            raise NativePackageError("input member names are case-colliding")
        folded.add(name.casefold())
    actual = set(names)
    expected = set(REQUIRED_INPUT_MEMBERS)
    if actual != expected:
        raise NativePackageError(
            "named input inventory differs "
            f"(missing={sorted(expected - actual)} extra={sorted(actual - expected)})"
        )
    return {
        name: _regular_unlinked_bytes(Path(input_paths[name]))
        for name in REQUIRED_INPUT_MEMBERS
    }


def _spdx(
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    target: str,
    executable_payload: bytes,
    network_payload: bytes,
    policy: NetworkPolicy,
) -> dict[str, Any]:
    created = datetime.fromtimestamp(source_date_epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    engine_digest = sha256_bytes(executable_payload)
    network_digest = sha256_bytes(network_payload)
    namespace = (
        "https://github.com/Belzedar94/Crazyhouse-Stockfish/releases/download/"
        f"v{version}/spdx/{target}/{commit}"
    )
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: Crazyhouse native package contract v1"],
        },
        "dataLicense": "CC0-1.0",
        "documentNamespace": namespace,
        "files": [
            {
                "SPDXID": "SPDXRef-File-Engine",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": engine_digest}
                ],
                "copyrightText": "NOASSERTION",
                "fileName": "./" + EXECUTABLE_MEMBER,
                "licenseConcluded": ENGINE_LICENSE,
            },
            {
                "SPDXID": "SPDXRef-File-Network",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": network_digest}
                ],
                "copyrightText": "NOASSERTION",
                "fileName": "./" + NETWORK_MEMBER,
                "licenseConcluded": policy.license,
            },
        ],
        "name": f"Crazyhouse-Stockfish-{version}-{target}",
        "packages": [
            {
                "SPDXID": "SPDXRef-Package-Crazyhouse-Stockfish",
                "comment": (
                    f"commit={commit} tree={tree} target={target} "
                    f"sourceDateEpoch={source_date_epoch}"
                ),
                "copyrightText": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": ENGINE_LICENSE,
                "licenseDeclared": ENGINE_LICENSE,
                "name": "Crazyhouse-Stockfish",
                "versionInfo": version,
            },
            {
                "SPDXID": "SPDXRef-Package-Crazyhouse-Legacy-Network",
                "comment": (
                    f"alias={NETWORK_ALIAS} assetBlob={policy.asset_blob} "
                    f"licenseBlob={policy.license_blob} policy={policy.schema} "
                    f"releaseEvidence={'true' if policy.release_evidence else 'false'}"
                ),
                "copyrightText": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": policy.license,
                "licenseDeclared": policy.license,
                "name": "Crazyhouse legacy network",
                "versionInfo": "legacy-v1",
            },
        ],
        "relationships": [
            {
                "relatedSpdxElement": "SPDXRef-Package-Crazyhouse-Stockfish",
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
            },
            {
                "relatedSpdxElement": "SPDXRef-Package-Crazyhouse-Legacy-Network",
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
            },
            {
                "relatedSpdxElement": "SPDXRef-File-Engine",
                "relationshipType": "CONTAINS",
                "spdxElementId": "SPDXRef-Package-Crazyhouse-Stockfish",
            },
            {
                "relatedSpdxElement": "SPDXRef-File-Network",
                "relationshipType": "CONTAINS",
                "spdxElementId": "SPDXRef-Package-Crazyhouse-Legacy-Network",
            },
        ],
        "spdxVersion": "SPDX-2.3",
    }


def _inventory(
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    target: str,
    contents_without_inventory: Mapping[str, bytes],
) -> dict[str, Any]:
    entries = []
    for path in sorted(contents_without_inventory):
        payload = contents_without_inventory[path]
        entries.append(
            {
                "bytes": len(payload),
                "executable": path == EXECUTABLE_MEMBER,
                "mediaType": MEDIA_TYPES[path],
                "path": path,
                "sha256": sha256_bytes(payload),
            }
        )
    return {
        "commit": commit,
        "files": entries,
        "project": "Crazyhouse-Stockfish",
        "root": root_name(version),
        "schema": "crazyhouse-native-files/v1",
        "sourceDateEpoch": source_date_epoch,
        "target": target,
        "tree": tree,
        "version": version,
    }


def _zip_info(
    full_name: str,
    timestamp: tuple[int, int, int, int, int, int],
    executable: bool,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(full_name, date_time=timestamp)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
    info.internal_attr = 0
    info.flag_bits = 0
    info.extra = b""
    info.comment = b""
    return info


def _reopen_self_check(
    archive: Path,
    version: str,
    timestamp: tuple[int, int, int, int, int, int],
    contents: Mapping[str, bytes],
) -> None:
    expected_names = [f"{root_name(version)}/{name}" for name in sorted(contents)]
    try:
        with zipfile.ZipFile(archive, "r") as package:
            infos = package.infolist()
            if package.comment or [info.filename for info in infos] != expected_names:
                raise NativePackageError("assembled ZIP inventory or order differs")
            if package.testzip() is not None:
                raise NativePackageError("assembled ZIP CRC self-check failed")
            for info in infos:
                relative = info.filename[len(root_name(version)) + 1 :]
                expected_mode = 0o755 if relative == EXECUTABLE_MEMBER else 0o644
                mode = info.external_attr >> 16
                if (
                    info.date_time != timestamp
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.create_system != 3
                    or info.extra
                    or info.comment
                    or stat.S_IFMT(mode) != stat.S_IFREG
                    or stat.S_IMODE(mode) != expected_mode
                    or package.read(info) != contents[relative]
                ):
                    raise NativePackageError(
                        "assembled ZIP member self-check failed: " + relative
                    )
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, NativePackageError):
            raise
        raise NativePackageError("assembled ZIP could not be reopened") from error


def build_native_package(
    input_paths: Mapping[str, Path],
    output_archive: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    target: str,
    executable_bytes: int,
    executable_sha256: str,
    *,
    network_policy: NetworkPolicy = PRODUCTION_NETWORK_POLICY,
) -> dict[str, object]:
    """Build one new deterministic native ZIP and return its authenticated pin."""

    timestamp = _validate_identity(
        version,
        commit,
        tree,
        source_date_epoch,
        target,
        executable_bytes,
        executable_sha256,
        network_policy,
    )
    output_archive = Path(output_archive)
    if output_archive.name != archive_name(version, target):
        raise NativePackageError("output archive name differs from the frozen name")
    if output_archive.exists():
        raise NativePackageError("output archive already exists")
    if not output_archive.parent.is_dir():
        raise NativePackageError("output archive parent does not exist")

    contents = _validate_inputs(input_paths)
    executable_payload = contents[EXECUTABLE_MEMBER]
    if (
        len(executable_payload) != executable_bytes
        or sha256_bytes(executable_payload) != executable_sha256
    ):
        raise NativePackageError("executable identity differs")
    network_payload = contents[NETWORK_MEMBER]
    if (
        len(network_payload) != network_policy.bytes
        or sha256_bytes(network_payload) != network_policy.sha256
    ):
        raise NativePackageError("network identity differs")
    if contents["SOURCE.md"] != expected_source_markdown(version, commit, tree):
        raise NativePackageError("SOURCE.md differs from the candidate relationship")
    if contents["networks/README.md"] != expected_network_readme(network_policy):
        raise NativePackageError("network README differs from the authority contract")
    if contents["licenses/CC0-1.0-NOTICE.md"] != expected_cc0_notice(
        network_policy
    ):
        raise NativePackageError("CC0 notice differs from the authority contract")

    sbom_payload = canonical_json(
        _spdx(
            version,
            commit,
            tree,
            source_date_epoch,
            target,
            executable_payload,
            network_payload,
            network_policy,
        )
    )
    contents[SBOM_MEMBER] = sbom_payload
    inventory_payload = canonical_json(
        _inventory(
            version,
            commit,
            tree,
            source_date_epoch,
            target,
            contents,
        )
    )
    contents[INVENTORY_MEMBER] = inventory_payload
    if set(contents) != set(EXACT_MEMBERS):
        raise NativePackageError("internal member inventory construction drift")

    try:
        with zipfile.ZipFile(
            output_archive,
            mode="x",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
            strict_timestamps=True,
        ) as package:
            package.comment = b""
            for relative in sorted(contents):
                full_name = f"{root_name(version)}/{relative}"
                package.writestr(
                    _zip_info(
                        full_name,
                        timestamp,
                        relative == EXECUTABLE_MEMBER,
                    ),
                    contents[relative],
                    compress_type=zipfile.ZIP_STORED,
                )
        _reopen_self_check(output_archive, version, timestamp, contents)
    except Exception:
        try:
            if output_archive.exists():
                output_archive.unlink()
        except OSError:
            pass
        raise

    return {
        "schema": "crazyhouse-native-package-pin/v1",
        "asset": output_archive.name,
        "version": version,
        "target": target,
        "commit": commit,
        "tree": tree,
        "sourceDateEpoch": source_date_epoch,
        "networkPolicy": network_policy.schema,
        "releaseEvidenceNetwork": network_policy.release_evidence,
        "bytes": output_archive.stat().st_size,
        "sha256": sha256_file(output_archive),
        "executableBytes": executable_bytes,
        "executableSha256": executable_sha256,
        "networkBytes": network_policy.bytes,
        "networkSha256": network_policy.sha256,
        "packageInventorySha256": sha256_bytes(inventory_payload),
        "sbomSha256": sha256_bytes(sbom_payload),
    }


def require_byte_identical(first: Path, second: Path) -> None:
    if (
        first.stat().st_size != second.stat().st_size
        or sha256_file(first) != sha256_file(second)
        or first.read_bytes() != second.read_bytes()
    ):
        raise NativePackageError("native archives are not byte-identical")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--executable-bytes", type=int, required=True)
    parser.add_argument("--executable-sha256", required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--authors", type=Path, required=True)
    parser.add_argument("--citation", type=Path, required=True)
    parser.add_argument("--copying", type=Path, required=True)
    parser.add_argument("--readme", type=Path, required=True)
    parser.add_argument("--source-md", type=Path, required=True)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument("--rule-profile", type=Path, required=True)
    parser.add_argument("--cc0-notice", type=Path, required=True)
    parser.add_argument("--network-readme", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    inputs = {
        "AUTHORS": args.authors,
        "CITATION.cff": args.citation,
        "Copying.txt": args.copying,
        "README.md": args.readme,
        "SOURCE.md": args.source_md,
        EXECUTABLE_MEMBER: args.executable,
        "docs/RELEASE_NOTES_DRAFT.md": args.release_notes,
        "docs/RULE_PROFILE.md": args.rule_profile,
        "licenses/CC0-1.0-NOTICE.md": args.cc0_notice,
        "networks/README.md": args.network_readme,
        NETWORK_MEMBER: args.network,
    }
    result = build_native_package(
        inputs,
        args.output,
        args.version,
        args.commit,
        args.tree,
        args.source_date_epoch,
        args.target,
        args.executable_bytes,
        args.executable_sha256,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
