#!/usr/bin/env python3
"""Exercise the frozen Crazyhouse release assembly and download contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_TOOLS = REPO_ROOT / "tools" / "release"
sys.path.insert(0, str(RELEASE_TOOLS))

from crazyhouse_release_manifest import (  # noqa: E402
    CHECKSUM_NAME,
    MANIFEST_NAME,
    PROVENANCE_SUFFIX,
    ReleaseContractError,
    TARGETS,
    assemble,
    expected_asset_names,
    native_asset_name,
    sha256,
    source_asset_name,
)
from crazyhouse_release_provenance import (  # noqa: E402
    write_native_provenance,
    write_source_provenance,
)
from verify_crazyhouse_release_download import (  # noqa: E402
    ReleaseDownloadError,
    verify_release_download,
)


VERSION = "0.1.0"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
TREE = "89abcdef0123456789abcdef0123456789abcdef"
SOURCE_DATE_EPOCH = 1_787_529_600
CONTRACT = REPO_ROOT / "tests" / "crazyhouse" / "p15-release-bundle-contract-v1.json"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


TOOLCHAIN = {
    "id": "mingw-gcc-16.1.0-fixture",
    "compiler": "g++.exe",
    "compilerVersion": "16.1.0",
    "compilerBytes": 3_312_964,
    "compilerSha256": digest("compiler"),
    "make": "make.exe",
    "makeVersion": "4.4.1",
    "makeBytes": 229_856,
    "makeSha256": digest("make"),
}


class Harness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = 0
        self.positive: list[str] = []
        self.negative: list[str] = []

    def case_root(self, label: str) -> Path:
        self.index += 1
        safe = "".join(character if character.isalnum() else "-" for character in label)
        value = self.root / f"{self.index:03d}-{safe}"
        value.mkdir(parents=True)
        return value

    def pass_positive(self, label: str) -> None:
        self.positive.append(label)

    def expect_failure(
        self,
        label: str,
        operation: Callable[[], object],
        allowed: tuple[type[BaseException], ...] = (
            ReleaseContractError,
            ReleaseDownloadError,
            ValueError,
        ),
    ) -> None:
        try:
            operation()
        except allowed:
            self.negative.append(label)
            return
        raise AssertionError("negative case was accepted: " + label)


def build_command(target: str) -> list[str]:
    return [
        "make",
        "-j1",
        "build",
        "ARCH=" + TARGETS[target]["makeArch"],
        "COMP=gcc",
        "OS=Windows_NT",
        "EXTRACXXFLAGS=-Werror",
        "mingw_reproducible=yes",
    ]


def make_input(root: Path) -> Path:
    input_root = root / "input"
    source_dir = input_root / "source"
    source_dir.mkdir(parents=True)
    source = source_dir / source_asset_name(VERSION)
    source.write_bytes(b"fixture corresponding source\n")
    write_source_provenance(
        source,
        VERSION,
        COMMIT,
        TREE,
        SOURCE_DATE_EPOCH,
        ["git", "archive", "--format=tar", COMMIT],
    )

    for target in TARGETS:
        native_dir = input_root / target
        native_dir.mkdir(parents=True)
        asset = native_dir / native_asset_name(VERSION, target)
        asset.write_bytes(("fixture native archive " + target + "\n").encode("ascii"))
        write_native_provenance(
            asset,
            VERSION,
            COMMIT,
            TREE,
            SOURCE_DATE_EPOCH,
            target,
            dict(TOOLCHAIN),
            build_command(target),
            1_000 + len(target),
            digest("executable-" + target),
            digest("inventory-" + target),
            digest("sbom-" + target),
        )
    return input_root


def native_provenance(input_root: Path, target: str = "windows-x86-64-avx2") -> Path:
    asset = input_root / target / native_asset_name(VERSION, target)
    return asset.with_name(asset.name + PROVENANCE_SUFFIX)


def source_provenance(input_root: Path) -> Path:
    asset = input_root / "source" / source_asset_name(VERSION)
    return asset.with_name(asset.name + PROVENANCE_SUFFIX)


def rewrite_json(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def assembly_failure(
    harness: Harness,
    label: str,
    mutate: Callable[[Path, Path], None],
) -> None:
    root = harness.case_root(label)
    input_root = make_input(root)
    mutate(root, input_root)
    harness.expect_failure(
        label,
        lambda: assemble(
            input_root,
            root / "output",
            VERSION,
            COMMIT,
            TREE,
            SOURCE_DATE_EPOCH,
        ),
    )


def provenance_mutation(
    harness: Harness,
    label: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    source: bool = False,
) -> None:
    def apply(_: Path, input_root: Path) -> None:
        path = source_provenance(input_root) if source else native_provenance(input_root)
        rewrite_json(path, mutate)

    assembly_failure(harness, label, apply)


def make_assembled(root: Path) -> Path:
    input_root = make_input(root)
    output = root / "assembled"
    assemble(input_root, output, VERSION, COMMIT, TREE, SOURCE_DATE_EPOCH)
    return output


def download_failure(
    harness: Harness,
    label: str,
    mutate: Callable[[Path, Path], None],
) -> None:
    root = harness.case_root(label)
    local = make_assembled(root)
    downloaded = root / "downloaded"
    shutil.copytree(local, downloaded)
    mutate(local, downloaded)
    harness.expect_failure(
        label,
        lambda: verify_release_download(
            local,
            downloaded,
            VERSION,
            COMMIT,
            TREE,
            SOURCE_DATE_EPOCH,
        ),
    )


def mutate_both_checksums(transform: Callable[[bytes], bytes]) -> Callable[[Path, Path], None]:
    def apply(local: Path, downloaded: Path) -> None:
        for root in (local, downloaded):
            path = root / CHECKSUM_NAME
            path.write_bytes(transform(path.read_bytes()))

    return apply


def run_contract() -> dict[str, Any]:
    contract_bytes = CONTRACT.read_bytes()
    contract = json.loads(contract_bytes.decode("utf-8"))
    if contract["schema"] != "crazyhouse-p15-release-bundle-contract/v1":
        raise AssertionError("contract schema drift")
    if contract["network"]["delivery_mode"] != "external-file-in-each-native-archive":
        raise AssertionError("network delivery mode drift")
    if len(contract["public_inventory"]["payload_assets"]) != 3:
        raise AssertionError("public inventory drift")

    with tempfile.TemporaryDirectory(prefix="crazyhouse-release-contract-") as temporary:
        harness = Harness(Path(temporary))

        positive_root = harness.case_root("positive-exact")
        input_root = make_input(positive_root)
        output = positive_root / "output"
        manifest = assemble(
            input_root, output, VERSION, COMMIT, TREE, SOURCE_DATE_EPOCH
        )
        if {entry["name"] for entry in manifest["artifacts"]} != expected_asset_names(VERSION):
            raise AssertionError("assembled payload inventory drift")
        harness.pass_positive("exact-inventory-assembly")

        deterministic_root = harness.case_root("positive-deterministic")
        first = make_assembled(deterministic_root / "first")
        second = make_assembled(deterministic_root / "second")
        if (first / MANIFEST_NAME).read_bytes() != (second / MANIFEST_NAME).read_bytes():
            raise AssertionError("manifest is not deterministic")
        if (first / CHECKSUM_NAME).read_bytes() != (second / CHECKSUM_NAME).read_bytes():
            raise AssertionError("SHA256SUMS is not deterministic")
        harness.pass_positive("deterministic-manifest-and-checksums")

        for name in expected_asset_names(VERSION):
            source = next(input_root.rglob(name))
            copied = output / name
            if source.read_bytes() != copied.read_bytes() or sha256(source) != sha256(copied):
                raise AssertionError("authenticated copy drift: " + name)
        harness.pass_positive("copy-and-rehash-byte-identity")

        source_name = source_asset_name(VERSION)
        native_entries = [
            entry for entry in manifest["artifacts"] if entry["provenance"]["kind"] == "native"
        ]
        if any(
            entry["provenance"]["correspondingSourceAsset"] != source_name
            for entry in native_entries
        ):
            raise AssertionError("corresponding-source relationship drift")
        harness.pass_positive("one-corresponding-source-for-both-targets")

        downloaded = positive_root / "downloaded"
        shutil.copytree(output, downloaded)
        if verify_release_download(
            output, downloaded, VERSION, COMMIT, TREE, SOURCE_DATE_EPOCH
        ) != 5:
            raise AssertionError("download verification count drift")
        harness.pass_positive("downloaded-draft-byte-reauthentication")

        assembly_failure(
            harness,
            "missing-native-archive",
            lambda _root, value: [
                path.unlink()
                for path in (
                    value / "windows-x86-64" / native_asset_name(VERSION, "windows-x86-64"),
                    (value / "windows-x86-64" / native_asset_name(VERSION, "windows-x86-64")).with_name(
                        native_asset_name(VERSION, "windows-x86-64") + PROVENANCE_SUFFIX
                    ),
                )
            ],
        )
        assembly_failure(
            harness,
            "missing-source-archive",
            lambda _root, value: [
                path.unlink()
                for path in (
                    value / "source" / source_asset_name(VERSION),
                    source_provenance(value),
                )
            ],
        )

        def extra_asset(_: Path, value: Path) -> None:
            (value / "extra.bin").write_bytes(b"extra")

        assembly_failure(harness, "extra-asset", extra_asset)

        def case_collision(_: Path, value: Path) -> None:
            duplicate = value / "duplicate"
            duplicate.mkdir()
            source = value / "windows-x86-64" / native_asset_name(VERSION, "windows-x86-64")
            shutil.copy2(source, duplicate / source.name.upper())

        assembly_failure(harness, "case-colliding-asset-name", case_collision)
        assembly_failure(
            harness,
            "unsafe-asset-name",
            lambda _root, value: (value / "bad name.zip").write_bytes(b"unsafe"),
        )

        def linked_asset(root: Path, value: Path) -> None:
            asset = value / "windows-x86-64" / native_asset_name(VERSION, "windows-x86-64")
            os.link(asset, root / "external-hardlink.bin")

        assembly_failure(harness, "linked-asset", linked_asset)
        assembly_failure(
            harness,
            "missing-provenance",
            lambda _root, value: native_provenance(value).unlink(),
        )
        assembly_failure(
            harness,
            "orphaned-provenance",
            lambda _root, value: (value / ("orphan" + PROVENANCE_SUFFIX)).write_text(
                "{}\n", encoding="ascii"
            ),
        )

        def duplicate_key(_: Path, value: Path) -> None:
            path = native_provenance(value)
            payload = path.read_text(encoding="utf-8")
            payload = payload.replace('  "asset": ', '  "asset": "duplicate",\n  "asset": ', 1)
            path.write_text(payload, encoding="utf-8", newline="\n")

        assembly_failure(harness, "duplicate-provenance-json-key", duplicate_key)
        provenance_mutation(harness, "extra-provenance-key", lambda value: value.update({"extra": 1}))
        provenance_mutation(harness, "missing-provenance-key", lambda value: value.pop("sbomSha256"))
        provenance_mutation(harness, "wrong-version", lambda value: value.update(version="9.9.9"))
        provenance_mutation(harness, "wrong-tag", lambda value: value.update(tag="v9.9.9"))
        provenance_mutation(harness, "wrong-commit", lambda value: value.update(commit="f" * 40))
        provenance_mutation(harness, "wrong-tree", lambda value: value.update(tree="e" * 40))
        provenance_mutation(harness, "wrong-source-epoch", lambda value: value.update(sourceDateEpoch=1))
        provenance_mutation(harness, "wrong-target", lambda value: value.update(target="windows-x86-64"))
        provenance_mutation(harness, "wrong-architecture", lambda value: value.update(architecture="x86-64"))
        provenance_mutation(harness, "wrong-feature-floor", lambda value: value.update(featureFloor=["x86-64"]))
        provenance_mutation(harness, "wrong-network-alias", lambda value: value.update(networkAlias="legacy.nnue"))
        provenance_mutation(harness, "wrong-network-bytes", lambda value: value.update(networkBytes=1))
        provenance_mutation(harness, "wrong-network-digest", lambda value: value.update(networkSha256=digest("wrong-network")))
        provenance_mutation(harness, "wrong-network-license", lambda value: value.update(networkLicense="GPL-3.0-or-later"))
        provenance_mutation(harness, "wrong-network-asset-blob", lambda value: value.update(networkAssetBlob="f" * 40))
        provenance_mutation(harness, "wrong-network-license-blob", lambda value: value.update(networkLicenseBlob="f" * 40))
        provenance_mutation(harness, "wrong-testing-book-bytes", lambda value: value.update(testingBookBytes=1))
        provenance_mutation(harness, "wrong-testing-book-digest", lambda value: value.update(testingBookSha256=digest("wrong-book")))
        provenance_mutation(harness, "wrong-testing-book-roots", lambda value: value.update(testingBookRoots=1))
        provenance_mutation(harness, "wrong-testing-book-license", lambda value: value.update(testingBookLicense="GPL-3.0-or-later"))
        provenance_mutation(harness, "wrong-corresponding-source", lambda value: value.update(correspondingSourceAsset="other.tar.xz"))
        provenance_mutation(harness, "wrong-toolchain-keys", lambda value: value["toolchain"].pop("makeSha256"))
        provenance_mutation(harness, "toolchain-host-path", lambda value: value["toolchain"].update(compiler="C:/host/g++.exe"))
        provenance_mutation(harness, "build-command-missing-token", lambda value: value.update(buildCommand=["make", "build"]))
        provenance_mutation(harness, "zero-executable-bytes", lambda value: value.update(executableBytes=0))
        provenance_mutation(harness, "wrong-source-license", lambda value: value.update(license="MIT"), source=True)
        provenance_mutation(harness, "wrong-source-relationship", lambda value: value.update(correspondingSource=False), source=True)

        def tamper(_: Path, value: Path) -> None:
            asset = value / "windows-x86-64-avx2" / native_asset_name(VERSION, "windows-x86-64-avx2")
            asset.write_bytes(asset.read_bytes() + b"tampered")

        assembly_failure(harness, "tampered-payload-after-provenance", tamper)

        def existing_output(root: Path, _value: Path) -> None:
            (root / "output").mkdir()

        assembly_failure(harness, "existing-output-directory", existing_output)

        download_failure(
            harness,
            "download-missing-asset",
            lambda _local, downloaded_root: (
                downloaded_root / native_asset_name(VERSION, "windows-x86-64")
            ).unlink(),
        )
        download_failure(
            harness,
            "download-extra-asset",
            lambda _local, downloaded_root: (downloaded_root / "extra.bin").write_bytes(b"extra"),
        )
        download_failure(
            harness,
            "download-altered-byte",
            lambda _local, downloaded_root: (
                downloaded_root / native_asset_name(VERSION, "windows-x86-64-avx2")
            ).write_bytes(b"altered"),
        )
        download_failure(
            harness,
            "checksums-missing",
            lambda local, downloaded_root: [
                (root / CHECKSUM_NAME).unlink() for root in (local, downloaded_root)
            ],
        )
        download_failure(
            harness,
            "checksums-malformed-row",
            mutate_both_checksums(lambda payload: b"malformed\n" + payload.splitlines(keepends=True)[1]),
        )
        download_failure(
            harness,
            "checksums-duplicate-name",
            mutate_both_checksums(lambda payload: payload + payload.splitlines(keepends=True)[0]),
        )

        def path_name(payload: bytes) -> bytes:
            lines = payload.splitlines(keepends=True)
            digest_value = lines[0][:64]
            lines[0] = digest_value + b"  nested/file.bin\n"
            return b"".join(lines)

        download_failure(harness, "checksums-path-bearing-name", mutate_both_checksums(path_name))
        download_failure(
            harness,
            "checksums-file-list-drift",
            mutate_both_checksums(lambda payload: b"".join(payload.splitlines(keepends=True)[1:])),
        )

        def digest_mismatch(payload: bytes) -> bytes:
            lines = payload.splitlines(keepends=True)
            lines[0] = (b"0" * 64) + lines[0][64:]
            return b"".join(lines)

        download_failure(harness, "checksums-digest-mismatch", mutate_both_checksums(digest_mismatch))
        download_failure(
            harness,
            "checksums-non-ascii",
            mutate_both_checksums(lambda payload: payload + b"\xff"),
        )
        download_failure(
            harness,
            "checksums-crlf",
            mutate_both_checksums(lambda payload: payload.replace(b"\n", b"\r\n")),
        )

        if len(harness.positive) != 5:
            raise AssertionError("positive case count drift")
        if len(harness.negative) < 36:
            raise AssertionError("negative case count below frozen minimum")
        return {
            "schema": "crazyhouse-release-bundle-fixture-result/v1",
            "status": "PASS_RELEASE_BUNDLE_FIXTURES",
            "contract": {
                "bytes": len(contract_bytes),
                "sha256": hashlib.sha256(contract_bytes).hexdigest(),
            },
            "positive_count": len(harness.positive),
            "negative_count": len(harness.negative),
            "positive_cases": harness.positive,
            "negative_cases": harness.negative,
            "boundaries": {
                "synthetic_fixture_only": True,
                "package_internal_layout_verified": False,
                "full_engine_builds_executed": False,
                "release_archive_reproducible": False,
                "release_candidate_selected": False,
                "timing_evidence": False,
                "strength_credit": False,
                "openbench_used": False,
                "draft_created": False,
                "tag_created": False,
                "release_claimed": False,
            },
        }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = run_contract()
    if args.json_output is not None:
        payload = (
            json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8")
        with args.json_output.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    print(
        "PASS_RELEASE_BUNDLE_FIXTURES "
        f"positive={result['positive_count']} negative={result['negative_count']} "
        "synthetic_fixture_only=true release_claim=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
