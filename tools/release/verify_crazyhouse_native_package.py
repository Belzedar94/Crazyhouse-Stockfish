#!/usr/bin/env python3
"""Independently verify one Crazyhouse native release ZIP."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
from typing import Any, Optional, Sequence
import unicodedata
import zipfile


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
DRIVE_PATH = re.compile(r"^[A-Za-z]:")
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
LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
EOCD = struct.Struct("<IHHHHIIH")
LOCAL_SIGNATURE = 0x04034B50
EOCD_SIGNATURE = 0x06054B50


class NativePackageVerificationError(RuntimeError):
    """The archive differs from the frozen native-package contract."""


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
    archive: Path,
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
        raise NativePackageVerificationError("version must be X.Y.Z")
    if not OBJECT_ID.fullmatch(commit) or not OBJECT_ID.fullmatch(tree):
        raise NativePackageVerificationError(
            "commit and tree must be lowercase full object IDs"
        )
    if (
        isinstance(source_date_epoch, bool)
        or not isinstance(source_date_epoch, int)
        or source_date_epoch < 0
    ):
        raise NativePackageVerificationError(
            "source-date epoch must be a non-negative integer"
        )
    try:
        instant = datetime.fromtimestamp(source_date_epoch, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise NativePackageVerificationError(
            "source-date epoch is outside the ZIP range"
        ) from error
    if instant.year < 1980 or instant.year > 2107:
        raise NativePackageVerificationError(
            "source-date epoch is outside the DOS ZIP range"
        )
    if target not in TARGETS:
        raise NativePackageVerificationError("unsupported native target")
    if archive.name != archive_name(version, target):
        raise NativePackageVerificationError("archive name differs")
    if (
        isinstance(executable_bytes, bool)
        or not isinstance(executable_bytes, int)
        or executable_bytes < 1
        or not DIGEST.fullmatch(executable_sha256)
    ):
        raise NativePackageVerificationError("executable identity arguments differ")
    if (
        not isinstance(policy, NetworkPolicy)
        or policy.bytes < 1
        or not DIGEST.fullmatch(policy.sha256)
        or not policy.license
        or not policy.asset_blob
        or not policy.license_blob
    ):
        raise NativePackageVerificationError("network policy is invalid")
    return (
        instant.year,
        instant.month,
        instant.day,
        instant.hour,
        instant.minute,
        instant.second - instant.second % 2,
    )


def _reject_duplicate_keys(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise NativePackageVerificationError("duplicate JSON key: " + key)
        value[key] = item
    return value


def _load_canonical_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (
        UnicodeError,
        json.JSONDecodeError,
        NativePackageVerificationError,
    ) as error:
        if isinstance(error, NativePackageVerificationError):
            raise
        raise NativePackageVerificationError(label + " is not valid JSON") from error
    if not isinstance(value, dict):
        raise NativePackageVerificationError(label + " root is not an object")
    if canonical_json(value) != payload:
        raise NativePackageVerificationError(label + " is not canonical JSON")
    return value


def _safe_member_names(
    infos: list[zipfile.ZipInfo],
    version: str,
) -> list[str]:
    folded: set[str] = set()
    actual: list[str] = []
    expected_root = root_name(version)
    for info in infos:
        name = info.filename
        if info.orig_filename != name:
            raise NativePackageVerificationError(
                "member name was normalized while parsing: "
                + repr(info.orig_filename)
            )
        try:
            encoded = name.encode("ascii")
        except UnicodeEncodeError as error:
            raise NativePackageVerificationError(
                "member name is not ASCII"
            ) from error
        if (
            not encoded
            or name != unicodedata.normalize("NFC", name)
            or name.startswith("/")
            or DRIVE_PATH.match(name)
            or "\\" in name
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise NativePackageVerificationError("unsafe member name: " + repr(name))
        segments = name.split("/")
        if (
            any(segment in {"", ".", ".."} for segment in segments)
            or len(segments) < 2
            or segments[0] != expected_root
            or name.endswith("/")
        ):
            raise NativePackageVerificationError(
                "member root or path segments differ: " + repr(name)
            )
        folded_name = name.casefold()
        if folded_name in folded:
            raise NativePackageVerificationError(
                "duplicate or case-colliding member name"
            )
        folded.add(folded_name)
        actual.append("/".join(segments[1:]))
    expected = sorted(EXACT_MEMBERS)
    if actual != expected:
        if set(actual) != set(expected):
            raise NativePackageVerificationError("internal member inventory differs")
        raise NativePackageVerificationError("ZIP member order differs")
    return actual


def _verify_eocd(raw: bytes, package: zipfile.ZipFile, member_count: int) -> None:
    if len(raw) < EOCD.size:
        raise NativePackageVerificationError("ZIP is shorter than EOCD")
    fields = EOCD.unpack_from(raw, len(raw) - EOCD.size)
    (
        signature,
        disk_number,
        central_disk,
        entries_disk,
        entries_total,
        central_size,
        central_offset,
        comment_length,
    ) = fields
    if (
        signature != EOCD_SIGNATURE
        or disk_number != 0
        or central_disk != 0
        or entries_disk != member_count
        or entries_total != member_count
        or comment_length != 0
        or central_offset != package.start_dir
        or central_offset + central_size != len(raw) - EOCD.size
    ):
        raise NativePackageVerificationError("EOCD or central-directory framing differs")


def _verify_local_headers(
    raw: bytes,
    package: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
) -> None:
    for index, info in enumerate(infos):
        offset = info.header_offset
        if offset < 0 or offset + LOCAL_HEADER.size > len(raw):
            raise NativePackageVerificationError("local-header offset is invalid")
        (
            signature,
            extract_version,
            flags,
            compression,
            mod_time,
            mod_date,
            crc,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
        ) = LOCAL_HEADER.unpack_from(raw, offset)
        name_start = offset + LOCAL_HEADER.size
        name_end = name_start + name_length
        extra_end = name_end + extra_length
        data_end = extra_end + compressed_size
        next_offset = (
            infos[index + 1].header_offset
            if index + 1 < len(infos)
            else package.start_dir
        )
        raw_name = raw[name_start:name_end]
        if (
            signature != LOCAL_SIGNATURE
            or extract_version != info.extract_version
            or flags != info.flag_bits
            or compression != info.compress_type
            or crc != info.CRC
            or compressed_size != info.compress_size
            or uncompressed_size != info.file_size
            or raw_name != info.filename.encode("ascii")
            or raw[name_end:extra_end] != info.extra
            or data_end != next_offset
        ):
            raise NativePackageVerificationError(
                "local and central member headers differ: " + info.filename
            )
        expected_time = (
            (info.date_time[3] << 11)
            | (info.date_time[4] << 5)
            | (info.date_time[5] // 2)
        )
        expected_date = (
            ((info.date_time[0] - 1980) << 9)
            | (info.date_time[1] << 5)
            | info.date_time[2]
        )
        if mod_time != expected_time or mod_date != expected_date:
            raise NativePackageVerificationError(
                "local DOS timestamp differs: " + info.filename
            )


def _expected_spdx(
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
                    {
                        "algorithm": "SHA256",
                        "checksumValue": sha256_bytes(executable_payload),
                    }
                ],
                "copyrightText": "NOASSERTION",
                "fileName": "./" + EXECUTABLE_MEMBER,
                "licenseConcluded": ENGINE_LICENSE,
            },
            {
                "SPDXID": "SPDXRef-File-Network",
                "checksums": [
                    {
                        "algorithm": "SHA256",
                        "checksumValue": sha256_bytes(network_payload),
                    }
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


def _expected_inventory(
    contents: dict[str, bytes],
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
    target: str,
) -> dict[str, Any]:
    entries = []
    for path in sorted(set(EXACT_MEMBERS) - {INVENTORY_MEMBER}):
        payload = contents[path]
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


def verify_native_package(
    archive: Path,
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
    archive = Path(archive)
    timestamp = _validate_identity(
        archive,
        version,
        commit,
        tree,
        source_date_epoch,
        target,
        executable_bytes,
        executable_sha256,
        network_policy,
    )
    try:
        metadata = archive.lstat()
    except OSError as error:
        raise NativePackageVerificationError("archive is missing") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or archive.is_symlink()
        or metadata.st_nlink != 1
    ):
        raise NativePackageVerificationError(
            "archive must be one regular unlinked file"
        )
    raw = archive.read_bytes()
    try:
        with zipfile.ZipFile(archive, "r") as package:
            infos = package.infolist()
            if package.comment:
                raise NativePackageVerificationError("archive comment is forbidden")
            if len(infos) != len(EXACT_MEMBERS):
                raise NativePackageVerificationError("ZIP member count differs")
            relative_names = _safe_member_names(infos, version)
            _verify_eocd(raw, package, len(infos))
            _verify_local_headers(raw, package, infos)
            contents: dict[str, bytes] = {}
            for info, relative in zip(infos, relative_names):
                mode = info.external_attr >> 16
                expected_mode = 0o755 if relative == EXECUTABLE_MEMBER else 0o644
                if (
                    info.create_system != 3
                    or info.create_version != 20
                    or info.extract_version != 20
                    or info.flag_bits != 0
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.compress_size != info.file_size
                    or info.date_time != timestamp
                    or info.extra
                    or info.comment
                    or info.internal_attr != 0
                    or stat.S_IFMT(mode) != stat.S_IFREG
                    or stat.S_IMODE(mode) != expected_mode
                ):
                    raise NativePackageVerificationError(
                        "ZIP member metadata differs: " + relative
                    )
                try:
                    contents[relative] = package.read(info)
                except (RuntimeError, zipfile.BadZipFile, OSError) as error:
                    raise NativePackageVerificationError(
                        "ZIP member bytes or CRC differ: " + relative
                    ) from error
    except NativePackageVerificationError:
        raise
    except (OSError, zipfile.BadZipFile, UnicodeError) as error:
        raise NativePackageVerificationError("archive framing is invalid") from error

    executable_payload = contents[EXECUTABLE_MEMBER]
    if (
        len(executable_payload) != executable_bytes
        or sha256_bytes(executable_payload) != executable_sha256
    ):
        raise NativePackageVerificationError("executable identity differs")
    network_payload = contents[NETWORK_MEMBER]
    if (
        len(network_payload) != network_policy.bytes
        or sha256_bytes(network_payload) != network_policy.sha256
    ):
        raise NativePackageVerificationError("network identity differs")
    if contents["SOURCE.md"] != expected_source_markdown(version, commit, tree):
        raise NativePackageVerificationError("SOURCE.md relationship differs")
    if contents["networks/README.md"] != expected_network_readme(network_policy):
        raise NativePackageVerificationError("network README authority differs")
    if contents["licenses/CC0-1.0-NOTICE.md"] != expected_cc0_notice(
        network_policy
    ):
        raise NativePackageVerificationError("CC0 notice authority differs")

    inventory = _load_canonical_json(contents[INVENTORY_MEMBER], "FILES.json")
    expected_inventory = _expected_inventory(
        contents,
        version,
        commit,
        tree,
        source_date_epoch,
        target,
    )
    if inventory != expected_inventory:
        raise NativePackageVerificationError("FILES.json content differs")
    sbom = _load_canonical_json(contents[SBOM_MEMBER], "SPDX")
    expected_sbom = _expected_spdx(
        version,
        commit,
        tree,
        source_date_epoch,
        target,
        executable_payload,
        network_payload,
        network_policy,
    )
    if sbom != expected_sbom:
        raise NativePackageVerificationError("SPDX content differs")

    return {
        "schema": "crazyhouse-native-package-verification/v1",
        "status": "PASS_NATIVE_PACKAGE_VERIFICATION",
        "asset": archive.name,
        "bytes": archive.stat().st_size,
        "sha256": sha256_file(archive),
        "members": len(contents),
        "version": version,
        "target": target,
        "commit": commit,
        "tree": tree,
        "sourceDateEpoch": source_date_epoch,
        "executableBytes": executable_bytes,
        "executableSha256": executable_sha256,
        "networkPolicy": network_policy.schema,
        "releaseEvidenceNetwork": network_policy.release_evidence,
        "networkBytes": network_policy.bytes,
        "networkSha256": network_policy.sha256,
        "packageInventorySha256": sha256_bytes(contents[INVENTORY_MEMBER]),
        "sbomSha256": sha256_bytes(contents[SBOM_MEMBER]),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--executable-bytes", type=int, required=True)
    parser.add_argument("--executable-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = verify_native_package(
        args.archive,
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
