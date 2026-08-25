#!/usr/bin/env python3
"""Exercise the frozen Crazyhouse native-package interior contract."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile
from typing import Any, Callable, Optional, Sequence
from unittest import mock
import warnings
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_TOOLS = REPO_ROOT / "tools" / "release"
sys.path.insert(0, str(RELEASE_TOOLS))

import crazyhouse_release_native_package as assembler  # noqa: E402
import verify_crazyhouse_native_package as verifier  # noqa: E402


VERSION = "1.2.3"
COMMIT = "1" * 40
TREE = "2" * 40
SOURCE_DATE_EPOCH = 1_787_529_600
EXECUTABLE_PAYLOAD = b"MZ\x90\x00crazyhouse-fixture-engine-v1\n"
NETWORK_PAYLOAD = b"crazyhouse-fixture-network-v1\n"
EXECUTABLE_SHA256 = hashlib.sha256(EXECUTABLE_PAYLOAD).hexdigest()
LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
CENTRAL_SIGNATURE = b"PK\x01\x02"


@dataclass
class MemberSpec:
    filename: str
    payload: bytes
    date_time: tuple[int, int, int, int, int, int]
    compress_type: int
    create_system: int
    create_version: int
    extract_version: int
    external_attr: int
    internal_attr: int
    flag_bits: int
    extra: bytes
    comment: bytes


class HarnessError(RuntimeError):
    pass


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def fixture_policies() -> tuple[assembler.NetworkPolicy, verifier.NetworkPolicy]:
    return (
        assembler.fixture_network_policy(NETWORK_PAYLOAD),
        verifier.fixture_network_policy(NETWORK_PAYLOAD),
    )


def write_input(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def make_inputs(
    root: Path,
    policy: assembler.NetworkPolicy,
    *,
    network_payload: bytes = NETWORK_PAYLOAD,
) -> dict[str, Path]:
    payloads: dict[str, bytes] = {
        "AUTHORS": b"Crazyhouse-Stockfish contributors\n",
        "CITATION.cff": b"cff-version: 1.2.0\ntitle: Crazyhouse-Stockfish fixture\n",
        "Copying.txt": b"GPL-3.0-or-later fixture notice\n",
        "README.md": b"# Crazyhouse-Stockfish fixture\n",
        "SOURCE.md": assembler.expected_source_markdown(VERSION, COMMIT, TREE),
        assembler.EXECUTABLE_MEMBER: EXECUTABLE_PAYLOAD,
        "docs/RELEASE_NOTES_DRAFT.md": b"# Release notes draft fixture\n",
        "docs/RULE_PROFILE.md": b"# Crazyhouse rule profile fixture\n",
        "licenses/CC0-1.0-NOTICE.md": assembler.expected_cc0_notice(policy),
        "networks/README.md": assembler.expected_network_readme(policy),
        assembler.NETWORK_MEMBER: network_payload,
    }
    return {
        member: write_input(root / member, payload)
        for member, payload in payloads.items()
    }


def build_fixture(
    root: Path,
    target: str,
    asm_policy: assembler.NetworkPolicy,
    *,
    inputs: Optional[dict[str, Path]] = None,
    version: str = VERSION,
    commit: str = COMMIT,
    tree: str = TREE,
    source_date_epoch: int = SOURCE_DATE_EPOCH,
    executable_bytes: int = len(EXECUTABLE_PAYLOAD),
    executable_sha256: str = EXECUTABLE_SHA256,
    output_preexisting: bool = False,
) -> tuple[Path, dict[str, object]]:
    root.mkdir(parents=True, exist_ok=True)
    if inputs is None:
        inputs = make_inputs(root / "inputs", asm_policy)
    output = root / assembler.archive_name(version, target)
    if output_preexisting:
        output.write_bytes(b"preexisting")
    result = assembler.build_native_package(
        inputs,
        output,
        version,
        commit,
        tree,
        source_date_epoch,
        target,
        executable_bytes,
        executable_sha256,
        network_policy=asm_policy,
    )
    return output, result


def verify_fixture(
    archive: Path,
    target: str,
    verify_policy: verifier.NetworkPolicy,
    *,
    version: str = VERSION,
    commit: str = COMMIT,
    tree: str = TREE,
    source_date_epoch: int = SOURCE_DATE_EPOCH,
    executable_bytes: int = len(EXECUTABLE_PAYLOAD),
    executable_sha256: str = EXECUTABLE_SHA256,
) -> dict[str, object]:
    return verifier.verify_native_package(
        archive,
        version,
        commit,
        tree,
        source_date_epoch,
        target,
        executable_bytes,
        executable_sha256,
        network_policy=verify_policy,
    )


def read_specs(archive: Path) -> list[MemberSpec]:
    specs: list[MemberSpec] = []
    with zipfile.ZipFile(archive, "r") as package:
        for info in package.infolist():
            specs.append(
                MemberSpec(
                    filename=info.filename,
                    payload=package.read(info),
                    date_time=info.date_time,
                    compress_type=info.compress_type,
                    create_system=info.create_system,
                    create_version=info.create_version,
                    extract_version=info.extract_version,
                    external_attr=info.external_attr,
                    internal_attr=info.internal_attr,
                    flag_bits=info.flag_bits,
                    extra=info.extra,
                    comment=info.comment,
                )
            )
    return specs


def write_specs(
    archive: Path,
    specs: list[MemberSpec],
    *,
    archive_comment: bytes = b"",
) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(
            archive,
            "x",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
            strict_timestamps=True,
        ) as package:
            package.comment = archive_comment
            for spec in specs:
                info = zipfile.ZipInfo(spec.filename, date_time=spec.date_time)
                info.compress_type = spec.compress_type
                info.create_system = spec.create_system
                info.create_version = spec.create_version
                info.extract_version = spec.extract_version
                info.external_attr = spec.external_attr
                info.internal_attr = spec.internal_attr
                info.flag_bits = spec.flag_bits
                info.extra = spec.extra
                info.comment = spec.comment
                package.writestr(
                    info,
                    spec.payload,
                    compress_type=spec.compress_type,
                )


def relative_name(spec: MemberSpec) -> str:
    return spec.filename.split("/", 1)[1] if "/" in spec.filename else spec.filename


def spec_index(specs: list[MemberSpec], relative: str) -> int:
    for index, spec in enumerate(specs):
        if relative_name(spec) == relative:
            return index
    raise HarnessError("member not found: " + relative)


def update_inventory_for_payload(
    specs: list[MemberSpec],
    relative: str,
    payload: bytes,
) -> None:
    target_index = spec_index(specs, relative)
    specs[target_index].payload = payload
    if relative == assembler.INVENTORY_MEMBER:
        return
    inventory_index = spec_index(specs, assembler.INVENTORY_MEMBER)
    inventory = json.loads(specs[inventory_index].payload.decode("utf-8"))
    for entry in inventory["files"]:
        if entry["path"] == relative:
            entry["bytes"] = len(payload)
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            break
    else:
        raise HarnessError("inventory entry not found: " + relative)
    specs[inventory_index].payload = canonical_json(inventory)


def patch_encrypted_flag(archive: Path) -> None:
    with zipfile.ZipFile(archive, "r") as package:
        first = package.infolist()[0]
        local_offset = first.header_offset
        central_offset = package.start_dir
    raw = bytearray(archive.read_bytes())
    local_flags = int.from_bytes(raw[local_offset + 6 : local_offset + 8], "little") | 1
    if raw[central_offset : central_offset + 4] != CENTRAL_SIGNATURE:
        raise HarnessError("central signature not found")
    central_flags = int.from_bytes(
        raw[central_offset + 8 : central_offset + 10], "little"
    ) | 1
    raw[local_offset + 6 : local_offset + 8] = local_flags.to_bytes(2, "little")
    raw[central_offset + 8 : central_offset + 10] = central_flags.to_bytes(
        2, "little"
    )
    archive.write_bytes(raw)


def patch_backslash_member(archive: Path) -> None:
    with zipfile.ZipFile(archive, "r") as package:
        first = package.infolist()[0]
        local_offset = first.header_offset
        central_offset = package.start_dir
        original_name = first.filename.encode("ascii")
    raw = bytearray(archive.read_bytes())
    local_fields = LOCAL_HEADER.unpack_from(raw, local_offset)
    local_name_length = local_fields[-2]
    local_name_start = local_offset + LOCAL_HEADER.size
    local_name = bytes(
        raw[local_name_start : local_name_start + local_name_length]
    )
    if local_name != original_name or b"/" not in original_name:
        raise HarnessError("local filename framing differs before backslash mutation")
    separator = original_name.index(b"/")
    raw[local_name_start + separator] = ord("\\")

    if raw[central_offset : central_offset + 4] != CENTRAL_SIGNATURE:
        raise HarnessError("central signature not found")
    central_name_length = int.from_bytes(
        raw[central_offset + 28 : central_offset + 30], "little"
    )
    central_name_start = central_offset + 46
    central_name = bytes(
        raw[central_name_start : central_name_start + central_name_length]
    )
    if central_name != original_name:
        raise HarnessError("central filename framing differs before backslash mutation")
    raw[central_name_start + separator] = ord("\\")
    archive.write_bytes(raw)
    mutated = archive.read_bytes()
    if mutated.count(original_name.replace(b"/", b"\\", 1)) < 2:
        raise HarnessError("backslash mutation was not serialized in both headers")


def tamper_first_payload_byte(archive: Path) -> None:
    with zipfile.ZipFile(archive, "r") as package:
        info = package.getinfo(
            f"{assembler.root_name(VERSION)}/AUTHORS"
        )
        offset = info.header_offset
    raw = bytearray(archive.read_bytes())
    fields = LOCAL_HEADER.unpack_from(raw, offset)
    name_length = fields[-2]
    extra_length = fields[-1]
    data_offset = offset + LOCAL_HEADER.size + name_length + extra_length
    raw[data_offset] ^= 1
    archive.write_bytes(raw)


def mutate_json_member(
    specs: list[MemberSpec],
    relative: str,
    mutator: Callable[[dict[str, Any]], None],
    *,
    update_inventory: bool,
) -> None:
    index = spec_index(specs, relative)
    value = json.loads(specs[index].payload.decode("utf-8"))
    mutator(value)
    payload = canonical_json(value)
    if update_inventory:
        update_inventory_for_payload(specs, relative, payload)
    else:
        specs[index].payload = payload


def run_contract(real_network: Optional[Path]) -> tuple[int, int, bool]:
    asm_policy, verify_policy = fixture_policies()
    negative_labels: list[str] = []
    case_counter = 0

    with tempfile.TemporaryDirectory(prefix="crazyhouse-native-package-") as temporary:
        root = Path(temporary)

        positive_x64, x64_pin = build_fixture(
            root / "positive-x64",
            "windows-x86-64",
            asm_policy,
        )
        x64_verify = verify_fixture(
            positive_x64,
            "windows-x86-64",
            verify_policy,
        )
        positive_avx2, avx2_pin = build_fixture(
            root / "positive-avx2",
            "windows-x86-64-avx2",
            asm_policy,
        )
        avx2_verify = verify_fixture(
            positive_avx2,
            "windows-x86-64-avx2",
            verify_policy,
        )
        repeat_x64, _ = build_fixture(
            root / "positive-x64-repeat",
            "windows-x86-64",
            asm_policy,
        )
        assembler.require_byte_identical(positive_x64, repeat_x64)
        if (
            x64_pin["packageInventorySha256"]
            != x64_verify["packageInventorySha256"]
            or x64_pin["sbomSha256"] != x64_verify["sbomSha256"]
            or avx2_pin["packageInventorySha256"]
            != avx2_verify["packageInventorySha256"]
            or avx2_pin["sbomSha256"] != avx2_verify["sbomSha256"]
        ):
            raise HarnessError("positive inventory or SPDX authentication differs")
        positive_count = 4

        base_specs = read_specs(positive_x64)

        def new_case(label: str) -> Path:
            nonlocal case_counter
            case_counter += 1
            path = root / f"negative-{case_counter:03d}"
            path.mkdir()
            return path

        def expect_negative(
            label: str,
            operation: Callable[[], object],
            required_fragment: Optional[str] = None,
        ) -> None:
            try:
                operation()
            except (
                assembler.NativePackageError,
                verifier.NativePackageVerificationError,
            ) as error:
                if required_fragment and required_fragment not in str(error):
                    raise HarnessError(
                        f"{label}: wrong rejection {type(error).__name__}: {error}"
                    ) from error
                negative_labels.append(label)
                return
            raise HarnessError(label + ": mutation was accepted")

        def builder_negative(
            label: str,
            mutate: Optional[
                Callable[
                    [
                        Path,
                        dict[str, Path],
                        assembler.NetworkPolicy,
                        dict[str, object],
                    ],
                    assembler.NetworkPolicy,
                ]
            ] = None,
            **overrides: object,
        ) -> None:
            case = new_case(label)
            inputs = make_inputs(case / "inputs", asm_policy)
            policy = asm_policy
            state: dict[str, object] = {}
            if mutate:
                policy = mutate(case, inputs, policy, state)
            target = str(overrides.get("target", "windows-x86-64"))
            version = str(overrides.get("version", VERSION))
            output = case / assembler.archive_name(version, target)
            if overrides.get("preexisting"):
                output.write_bytes(b"preexisting")
            assembler.build_native_package(
                inputs,
                output,
                version,
                str(overrides.get("commit", COMMIT)),
                str(overrides.get("tree", TREE)),
                int(overrides.get("source_date_epoch", SOURCE_DATE_EPOCH)),
                target,
                int(overrides.get("executable_bytes", len(EXECUTABLE_PAYLOAD))),
                str(overrides.get("executable_sha256", EXECUTABLE_SHA256)),
                network_policy=policy,
            )

        def write_mutant(
            label: str,
            mutate: Callable[[list[MemberSpec]], None],
            *,
            archive_comment: bytes = b"",
            postprocess: Optional[Callable[[Path], None]] = None,
        ) -> Path:
            case = new_case(label)
            specs = deepcopy(base_specs)
            mutate(specs)
            archive = case / assembler.archive_name(VERSION, "windows-x86-64")
            write_specs(archive, specs, archive_comment=archive_comment)
            if postprocess:
                postprocess(archive)
            return archive

        def verifier_negative(
            label: str,
            mutate: Callable[[list[MemberSpec]], None],
            *,
            archive_comment: bytes = b"",
            postprocess: Optional[Callable[[Path], None]] = None,
            required_fragment: Optional[str] = None,
        ) -> None:
            expect_negative(
                label,
                lambda: verify_fixture(
                    write_mutant(
                        label,
                        mutate,
                        archive_comment=archive_comment,
                        postprocess=postprocess,
                    ),
                    "windows-x86-64",
                    verify_policy,
                ),
                required_fragment,
            )

        def no_change(_: list[MemberSpec]) -> None:
            return None

        def remove_named(
            _case: Path,
            inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            _state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            inputs.pop("AUTHORS")
            return policy

        expect_negative(
            "missing named input",
            lambda: builder_negative("missing named input", remove_named),
            "named input inventory differs",
        )

        def extra_named(
            case: Path,
            inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            _state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            inputs["EXTRA"] = write_input(case / "extra", b"extra")
            return policy

        expect_negative(
            "extra named input",
            lambda: builder_negative("extra named input", extra_named),
            "named input inventory differs",
        )

        def linked_input(
            case: Path,
            inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            source = inputs["AUTHORS"]
            link = case / "linked-authors"
            try:
                os.symlink(source, link)
                inputs["AUTHORS"] = link
            except OSError:
                shutil.copyfile(source, link)
                inputs["AUTHORS"] = link
                state["emulate_symlink"] = link
            return policy

        case = new_case("linked input")
        linked_inputs = make_inputs(case / "inputs", asm_policy)
        linked_state: dict[str, object] = {}
        linked_policy = linked_input(case, linked_inputs, asm_policy, linked_state)
        if "emulate_symlink" in linked_state:
            emulated = Path(linked_state["emulate_symlink"])
            original_is_symlink = Path.is_symlink
            with mock.patch.object(
                Path,
                "is_symlink",
                lambda self: True if self == emulated else original_is_symlink(self),
            ):
                expect_negative(
                    "linked input",
                    lambda: assembler.build_native_package(
                        linked_inputs,
                        case / assembler.archive_name(VERSION, "windows-x86-64"),
                        VERSION,
                        COMMIT,
                        TREE,
                        SOURCE_DATE_EPOCH,
                        "windows-x86-64",
                        len(EXECUTABLE_PAYLOAD),
                        EXECUTABLE_SHA256,
                        network_policy=linked_policy,
                    ),
                    "regular unlinked",
                )
        else:
            expect_negative(
                "linked input",
                lambda: assembler.build_native_package(
                    linked_inputs,
                    case / assembler.archive_name(VERSION, "windows-x86-64"),
                    VERSION,
                    COMMIT,
                    TREE,
                    SOURCE_DATE_EPOCH,
                    "windows-x86-64",
                    len(EXECUTABLE_PAYLOAD),
                    EXECUTABLE_SHA256,
                    network_policy=linked_policy,
                ),
                "regular unlinked",
            )

        def hardlinked_input(
            case: Path,
            inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            _state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            os.link(inputs["AUTHORS"], case / "authors-second-link")
            return policy

        expect_negative(
            "multiply linked input",
            lambda: builder_negative("multiply linked input", hardlinked_input),
            "regular unlinked",
        )

        def case_collision(
            _case: Path,
            inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            _state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            inputs["readme.md"] = inputs["README.md"]
            return policy

        expect_negative(
            "case-colliding input",
            lambda: builder_negative("case-colliding input", case_collision),
            "case-colliding",
        )
        expect_negative(
            "wrong executable bytes",
            lambda: builder_negative(
                "wrong executable bytes",
                executable_bytes=len(EXECUTABLE_PAYLOAD) + 1,
            ),
            "executable identity",
        )
        expect_negative(
            "wrong executable digest",
            lambda: builder_negative(
                "wrong executable digest",
                executable_sha256="0" * 64,
            ),
            "executable identity",
        )

        def wrong_network_size(
            _case: Path,
            _inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            _state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            return replace(policy, bytes=policy.bytes + 1)

        expect_negative(
            "wrong network size",
            lambda: builder_negative("wrong network size", wrong_network_size),
            "network identity",
        )

        def wrong_network_digest(
            _case: Path,
            _inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            _state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            return replace(policy, sha256="0" * 64)

        expect_negative(
            "wrong network digest",
            lambda: builder_negative("wrong network digest", wrong_network_digest),
            "network identity",
        )

        def wrong_network_alias(
            _case: Path,
            inputs: dict[str, Path],
            policy: assembler.NetworkPolicy,
            _state: dict[str, object],
        ) -> assembler.NetworkPolicy:
            inputs["networks/crazyhouse_v1.nnue"] = inputs.pop(
                assembler.NETWORK_MEMBER
            )
            return policy

        expect_negative(
            "wrong network package alias",
            lambda: builder_negative(
                "wrong network package alias", wrong_network_alias
            ),
            "named input inventory differs",
        )
        expect_negative(
            "invalid version",
            lambda: builder_negative("invalid version", version="1.2"),
            "version",
        )
        expect_negative(
            "invalid commit",
            lambda: builder_negative("invalid commit", commit="1" * 39),
            "object IDs",
        )
        expect_negative(
            "invalid tree",
            lambda: builder_negative("invalid tree", tree="Z" * 40),
            "object IDs",
        )
        expect_negative(
            "negative source epoch",
            lambda: builder_negative("negative source epoch", source_date_epoch=-1),
            "non-negative",
        )
        expect_negative(
            "out-of-range DOS source epoch",
            lambda: builder_negative(
                "out-of-range DOS source epoch",
                source_date_epoch=1,
            ),
            "DOS ZIP range",
        )
        expect_negative(
            "unsupported target",
            lambda: builder_negative("unsupported target", target="windows-arm64"),
            "unsupported native target",
        )
        expect_negative(
            "existing output archive",
            lambda: builder_negative("existing output archive", preexisting=True),
            "already exists",
        )

        verifier_negative(
            "duplicate ZIP member",
            lambda specs: specs.append(deepcopy(specs[0])),
        )

        def rename_first(specs: list[MemberSpec], name: str) -> None:
            specs[0].filename = name

        root_prefix = assembler.root_name(VERSION)
        verifier_negative(
            "case-colliding ZIP member",
            lambda specs: specs.append(
                replace(
                    deepcopy(specs[0]),
                    filename=specs[0].filename.swapcase(),
                )
            ),
        )
        verifier_negative(
            "extra ZIP member",
            lambda specs: specs.append(
                replace(
                    deepcopy(specs[0]),
                    filename=f"{root_prefix}/EXTRA",
                )
            ),
        )
        verifier_negative(
            "missing ZIP member",
            lambda specs: specs.pop(0),
        )
        verifier_negative(
            "multiple top-level roots",
            lambda specs: rename_first(specs, "Other-Root/AUTHORS"),
        )

        def wrong_all_roots(specs: list[MemberSpec]) -> None:
            for spec in specs:
                spec.filename = "Wrong-Root/" + relative_name(spec)

        verifier_negative("wrong top-level root", wrong_all_roots)
        verifier_negative(
            "absolute member path",
            lambda specs: rename_first(specs, "/AUTHORS"),
        )
        verifier_negative(
            "drive-qualified member path",
            lambda specs: rename_first(specs, "C:/AUTHORS"),
        )
        verifier_negative(
            "dot-dot member segment",
            lambda specs: rename_first(specs, f"{root_prefix}/../AUTHORS"),
        )
        verifier_negative(
            "dot member segment",
            lambda specs: rename_first(specs, f"{root_prefix}/./AUTHORS"),
        )
        verifier_negative(
            "backslash member path",
            no_change,
            postprocess=patch_backslash_member,
        )
        verifier_negative(
            "empty member segment",
            lambda specs: rename_first(specs, f"{root_prefix}//AUTHORS"),
        )
        verifier_negative(
            "non-ASCII member path",
            lambda specs: rename_first(specs, f"{root_prefix}/AUTH\u00d6RS"),
        )
        verifier_negative(
            "control character member path",
            lambda specs: rename_first(specs, f"{root_prefix}/AUTH\x01ORS"),
        )
        verifier_negative(
            "directory entry",
            lambda specs: rename_first(specs, f"{root_prefix}/AUTHORS/"),
        )

        def set_mode(
            specs: list[MemberSpec],
            file_type: int,
            permissions: int,
            relative: str = "AUTHORS",
        ) -> None:
            index = spec_index(specs, relative)
            specs[index].external_attr = (file_type | permissions) << 16

        verifier_negative(
            "symbolic-link member mode",
            lambda specs: set_mode(specs, stat.S_IFLNK, 0o777),
        )
        verifier_negative(
            "non-regular member mode",
            lambda specs: set_mode(specs, stat.S_IFCHR, 0o644),
        )
        verifier_negative(
            "wrong executable mode",
            lambda specs: set_mode(
                specs,
                stat.S_IFREG,
                0o644,
                assembler.EXECUTABLE_MEMBER,
            ),
        )
        verifier_negative(
            "wrong data mode",
            lambda specs: set_mode(specs, stat.S_IFREG, 0o600),
        )
        verifier_negative(
            "wrong ZIP creator system",
            lambda specs: setattr(specs[0], "create_system", 0),
        )
        verifier_negative(
            "compressed member",
            lambda specs: setattr(specs[0], "compress_type", zipfile.ZIP_DEFLATED),
        )
        verifier_negative(
            "member extra field",
            lambda specs: setattr(specs[0], "extra", b"\xfe\xca\x00\x00"),
        )
        verifier_negative(
            "archive comment",
            no_change,
            archive_comment=b"forbidden",
        )
        verifier_negative(
            "member comment",
            lambda specs: setattr(specs[0], "comment", b"forbidden"),
        )
        verifier_negative(
            "wrong member timestamp",
            lambda specs: setattr(
                specs[0],
                "date_time",
                (
                    specs[0].date_time[0],
                    specs[0].date_time[1],
                    specs[0].date_time[2],
                    specs[0].date_time[3],
                    specs[0].date_time[4],
                    (specs[0].date_time[5] + 2) % 60,
                ),
            ),
        )
        verifier_negative(
            "wrong member order",
            lambda specs: specs.reverse(),
        )
        verifier_negative(
            "encrypted member flag",
            no_change,
            postprocess=patch_encrypted_flag,
        )
        verifier_negative(
            "tampered member payload",
            no_change,
            postprocess=tamper_first_payload_byte,
        )

        verifier_negative(
            "duplicate FILES.json key",
            lambda specs: setattr(
                specs[spec_index(specs, assembler.INVENTORY_MEMBER)],
                "payload",
                b'{"schema":"one","schema":"two"}\n',
            ),
            required_fragment="duplicate JSON key",
        )
        verifier_negative(
            "extra FILES.json key",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value.__setitem__("extra", True),
                update_inventory=False,
            ),
        )
        verifier_negative(
            "missing FILES.json key",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value.pop("tree"),
                update_inventory=False,
            ),
        )
        verifier_negative(
            "wrong FILES.json schema",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value.__setitem__("schema", "wrong/v1"),
                update_inventory=False,
            ),
        )
        verifier_negative(
            "unsorted FILES.json entries",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value["files"].reverse(),
                update_inventory=False,
            ),
        )
        verifier_negative(
            "FILES.json path drift",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value["files"][0].__setitem__("path", "WRONG"),
                update_inventory=False,
            ),
        )
        verifier_negative(
            "FILES.json byte-count drift",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value["files"][0].__setitem__(
                    "bytes", value["files"][0]["bytes"] + 1
                ),
                update_inventory=False,
            ),
        )
        verifier_negative(
            "FILES.json digest drift",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value["files"][0].__setitem__("sha256", "0" * 64),
                update_inventory=False,
            ),
        )
        verifier_negative(
            "FILES.json executable-flag drift",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value["files"][0].__setitem__(
                    "executable", not value["files"][0]["executable"]
                ),
                update_inventory=False,
            ),
        )

        def add_inventory_self(value: dict[str, Any]) -> None:
            entry = dict(value["files"][0])
            entry["path"] = assembler.INVENTORY_MEMBER
            value["files"].append(entry)
            value["files"].sort(key=lambda item: item["path"])

        verifier_negative(
            "FILES.json self entry",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                add_inventory_self,
                update_inventory=False,
            ),
        )
        verifier_negative(
            "FILES.json missing member entry",
            lambda specs: mutate_json_member(
                specs,
                assembler.INVENTORY_MEMBER,
                lambda value: value["files"].pop(),
                update_inventory=False,
            ),
        )

        def replace_spdx_raw(specs: list[MemberSpec], payload: bytes) -> None:
            update_inventory_for_payload(specs, assembler.SBOM_MEMBER, payload)

        verifier_negative(
            "duplicate SPDX JSON key",
            lambda specs: replace_spdx_raw(
                specs,
                b'{"SPDXID":"one","SPDXID":"two"}\n',
            ),
            required_fragment="duplicate JSON key",
        )
        verifier_negative(
            "extra SPDX document key",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value.__setitem__("extra", True),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX version",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value.__setitem__("spdxVersion", "SPDX-2.2"),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX data license",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value.__setitem__("dataLicense", "NOASSERTION"),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX document namespace",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value.__setitem__(
                    "documentNamespace", "https://example.invalid/wrong"
                ),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX creation timestamp",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value["creationInfo"].__setitem__(
                    "created", "1980-01-01T00:00:00Z"
                ),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "missing SPDX engine package",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value["packages"].pop(0),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX engine license",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value["packages"][0].__setitem__(
                    "licenseDeclared", "MIT"
                ),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX network license",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value["packages"][1].__setitem__(
                    "licenseDeclared", "MIT"
                ),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX file checksum",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value["files"][0]["checksums"][0].__setitem__(
                    "checksumValue", "0" * 64
                ),
                update_inventory=True,
            ),
        )
        verifier_negative(
            "wrong SPDX relationship",
            lambda specs: mutate_json_member(
                specs,
                assembler.SBOM_MEMBER,
                lambda value: value["relationships"][0].__setitem__(
                    "relationshipType", "DEPENDS_ON"
                ),
                update_inventory=True,
            ),
        )

        def replace_text_member(
            specs: list[MemberSpec],
            relative: str,
            old: bytes,
            new: bytes,
        ) -> None:
            index = spec_index(specs, relative)
            payload = specs[index].payload.replace(old, new)
            update_inventory_for_payload(specs, relative, payload)

        verifier_negative(
            "SOURCE.md corresponding-source drift",
            lambda specs: replace_text_member(
                specs,
                "SOURCE.md",
                b"-source.tar.xz",
                b"-wrong-source.tar.xz",
            ),
        )
        verifier_negative(
            "SOURCE.md commit drift",
            lambda specs: replace_text_member(
                specs,
                "SOURCE.md",
                COMMIT.encode("ascii"),
                ("3" * 40).encode("ascii"),
            ),
        )
        verifier_negative(
            "network README authority drift",
            lambda specs: replace_text_member(
                specs,
                "networks/README.md",
                asm_policy.asset_blob.encode("ascii"),
                b"wrong-fixture-authority",
            ),
        )
        verifier_negative(
            "testing book included",
            lambda specs: specs.append(
                replace(
                    deepcopy(specs[0]),
                    filename=f"{root_prefix}/books/testing.epd",
                    payload=b"must not ship\n",
                )
            ),
        )

        def nonreproducible_case() -> None:
            case = new_case("archives differ across independent roots")
            first, _ = build_fixture(case / "first", "windows-x86-64", asm_policy)
            second_inputs = make_inputs(case / "second" / "inputs", asm_policy)
            second_inputs["README.md"].write_bytes(b"different input\n")
            second, _ = build_fixture(
                case / "second",
                "windows-x86-64",
                asm_policy,
                inputs=second_inputs,
            )
            assembler.require_byte_identical(first, second)

        expect_negative(
            "archives differ across independent roots",
            nonreproducible_case,
            "not byte-identical",
        )

        contract = json.loads(
            (
                REPO_ROOT
                / "tests"
                / "crazyhouse"
                / "p15-native-package-internal-v1.json"
            ).read_text(encoding="utf-8")
        )
        expected_labels = contract["negative_matrix"]
        if contract["negative_count"] != 72 or expected_labels != negative_labels:
            raise HarnessError(
                "negative matrix drift "
                f"expected={len(expected_labels)} observed={len(negative_labels)}"
            )

        real_network_authenticated = False
        if real_network is not None:
            real_network = real_network.resolve(strict=True)
            if (
                real_network.stat().st_size != assembler.NETWORK_BYTES
                or assembler.sha256_file(real_network) != assembler.NETWORK_SHA256
            ):
                raise HarnessError("real network identity differs before packaging")
            production_verify_policy = verifier.PRODUCTION_NETWORK_POLICY
            real_archives: list[Path] = []
            for target in assembler.TARGETS:
                first_case = root / ("real-" + target + "-a")
                first_inputs = make_inputs(
                    first_case / "inputs",
                    assembler.PRODUCTION_NETWORK_POLICY,
                    network_payload=real_network.read_bytes(),
                )
                first_archive, _ = build_fixture(
                    first_case,
                    target,
                    assembler.PRODUCTION_NETWORK_POLICY,
                    inputs=first_inputs,
                )
                verify_fixture(
                    first_archive,
                    target,
                    production_verify_policy,
                )
                second_case = root / ("real-" + target + "-b")
                second_inputs = make_inputs(
                    second_case / "inputs",
                    assembler.PRODUCTION_NETWORK_POLICY,
                    network_payload=real_network.read_bytes(),
                )
                second_archive, _ = build_fixture(
                    second_case,
                    target,
                    assembler.PRODUCTION_NETWORK_POLICY,
                    inputs=second_inputs,
                )
                verify_fixture(
                    second_archive,
                    target,
                    production_verify_policy,
                )
                assembler.require_byte_identical(first_archive, second_archive)
                real_archives.extend([first_archive, second_archive])
            if (
                real_network.stat().st_size != assembler.NETWORK_BYTES
                or assembler.sha256_file(real_network) != assembler.NETWORK_SHA256
            ):
                raise HarnessError("real network identity differs after packaging")
            real_network_authenticated = True
            positive_count += 1

        return positive_count, len(negative_labels), real_network_authenticated


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-network", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    positive, negative, real_network = run_contract(args.real_network)
    print(
        "PASS_NATIVE_PACKAGE_FIXTURES "
        f"positive={positive} negative={negative} "
        f"real_network={'true' if real_network else 'false'} "
        "full_engine=false release_claim=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
