#!/usr/bin/env python3
"""Build the local-only Crazyhouse V2 authenticated legacy control container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn


LEGACY_BASENAME = "crazyhouse_run15rl_e190_l03.nnue"
LEGACY_BYTES = 58_534_811
LEGACY_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
LEGACY_VERSION = 0x7AF32F20
LEGACY_NETWORK_HASH = 0x3C103E72
LEGACY_TRANSFORMER_HASH = 0x5F2348B8
LEGACY_ARCHITECTURE_HASH = 0x633376CA
LEGACY_DESCRIPTION = (
    b"Network trained with the https://github.com/glinscott/nnue-pytorch trainer."
)

CONTRACT_BYTES = 13_923
CONTRACT_SHA256 = "1d738d8c956c9d15a74f44dcf145d33aa72579da83d8be7421ca29050ad04759"
LEGACY_CONTRACT_BYTES = 2_121
LEGACY_CONTRACT_SHA256 = "82b4b5dafa9e280479ea47057da88625d2bdfa40801e5dcd51ba861a52c30f00"
RULE_PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"

MAGIC = b"CHNNUEV2LC1" + bytes(5)
ENDIAN_TAG = 0x01020304
VERSION_MAJOR = 1
VERSION_MINOR = 0
HEADER_BYTES = 1_024
PAYLOAD_BYTES = 58_534_688
FILE_BYTES = 58_535_712
DIRECTORY_OFFSET = 384
DIRECTORY_ENTRY_BYTES = 64
CRC_OFFSET = 1_020

FEATURE_DIMENSIONS = 55_296
MAXIMUM_ACTIVE = 128
TRANSFORMER_LANES = 512
PERSPECTIVE_COUNT = 2
PSQT_BUCKETS = 8
LAYER_STACKS = 8
DENSE0_INPUTS = 1_024
DENSE0_OUTPUTS = 16
DENSE0_PADDED_OUTPUTS = 32
DENSE1_INPUTS = 32
DENSE1_OUTPUTS = 32
OUTPUT_INPUTS = 32
OUTPUT_OUTPUTS = 1


class ControlError(RuntimeError):
    """A fail-closed conversion error."""


def fail(message: str) -> NoReturn:
    raise ControlError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def sha256_bytes(payload: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(payload).hexdigest()


def authenticated_bytes(path: Path, expected_bytes: int, expected_sha256: str, label: str) -> bytes:
    require(path.is_file(), f"{label} is missing or not a regular file")
    payload = path.read_bytes()
    require(len(payload) == expected_bytes, f"{label} byte count mismatch")
    require(sha256_bytes(payload) == expected_sha256, f"{label} SHA-256 mismatch")
    return payload


def parse_git_identity(text: str, label: str) -> bytes:
    require(len(text) == 40, f"{label} is not a full SHA-1 object identity")
    try:
        value = bytes.fromhex(text)
    except ValueError as error:
        raise ControlError(f"{label} is not hexadecimal") from error
    require(value != bytes(20), f"{label} is zero")
    return value


def digest_bytes(text: str, label: str) -> bytes:
    require(len(text) == 64, f"{label} is not a SHA-256 identity")
    try:
        value = bytes.fromhex(text)
    except ValueError as error:
        raise ControlError(f"{label} is not hexadecimal") from error
    require(value != bytes(32), f"{label} is zero")
    return value


def crc32c(payload: bytes | bytearray | memoryview) -> int:
    value = 0xFFFFFFFF
    for octet in payload:
        value ^= int(octet)
        for _ in range(8):
            value = (value >> 1) ^ (0x82F63B78 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


@dataclass(frozen=True)
class SectionSpec:
    section_id: int
    name: str
    dtype_id: int
    shape: tuple[int, ...]
    offset: int
    byte_count: int


SECTIONS = (
    SectionSpec(1, "transformer_bias", 2, (512,), 1_024, 1_024),
    SectionSpec(2, "transformer_weights", 2, (55_296, 512), 2_048, 56_623_104),
    SectionSpec(3, "psqt_weights", 3, (55_296, 8), 56_625_152, 1_769_472),
    SectionSpec(4, "dense0_bias", 3, (8, 16), 58_394_624, 512),
    SectionSpec(5, "dense0_weights", 1, (8, 16, 1_024), 58_395_136, 131_072),
    SectionSpec(6, "dense1_bias", 3, (8, 32), 58_526_208, 1_024),
    SectionSpec(7, "dense1_weights", 1, (8, 32, 32), 58_527_232, 8_192),
    SectionSpec(8, "output_bias", 3, (8,), 58_535_424, 32),
    SectionSpec(9, "output_weights", 1, (8, 32), 58_535_456, 256),
)


def extract_legacy_sections(legacy: bytes) -> list[bytes]:
    """Parse tensors only after the complete legacy artifact was authenticated."""
    require(len(legacy) == LEGACY_BYTES, "authenticated legacy bytes changed before parsing")
    version, network_hash, description_length = struct.unpack_from("<III", legacy, 0)
    require(version == LEGACY_VERSION, "legacy version mismatch after authentication")
    require(network_hash == LEGACY_NETWORK_HASH, "legacy network hash mismatch after authentication")
    require(description_length == len(LEGACY_DESCRIPTION), "legacy description length mismatch")
    require(legacy[12:87] == LEGACY_DESCRIPTION, "legacy description mismatch")
    require(struct.unpack_from("<I", legacy, 87)[0] == LEGACY_TRANSFORMER_HASH,
            "legacy transformer hash mismatch")

    cursor = 91

    def take(count: int, label: str) -> bytes:
        nonlocal cursor
        end = cursor + count
        require(end <= len(legacy), f"legacy {label} is truncated")
        value = legacy[cursor:end]
        cursor = end
        return value

    transformer_bias = take(1_024, "transformer bias")
    transformer_weights = take(56_623_104, "transformer weights")
    psqt_weights = take(1_769_472, "PSQT weights")
    require(cursor == 58_393_691, "legacy transformer section boundary mismatch")

    dense0_bias: list[bytes] = []
    dense0_weights: list[bytes] = []
    dense1_bias: list[bytes] = []
    dense1_weights: list[bytes] = []
    output_bias: list[bytes] = []
    output_weights: list[bytes] = []
    for stack in range(LAYER_STACKS):
        require(struct.unpack_from("<I", legacy, cursor)[0] == LEGACY_ARCHITECTURE_HASH,
                f"legacy architecture hash mismatch in stack {stack}")
        cursor += 4
        dense0_bias.append(take(64, f"dense0 bias stack {stack}"))
        dense0_weights.append(take(16_384, f"dense0 weights stack {stack}"))
        dense1_bias.append(take(128, f"dense1 bias stack {stack}"))
        dense1_weights.append(take(1_024, f"dense1 weights stack {stack}"))
        output_bias.append(take(4, f"output bias stack {stack}"))
        output_weights.append(take(32, f"output weights stack {stack}"))

    require(cursor == LEGACY_BYTES, "legacy parser did not consume strict EOF")
    sections = [
        transformer_bias,
        transformer_weights,
        psqt_weights,
        b"".join(dense0_bias),
        b"".join(dense0_weights),
        b"".join(dense1_bias),
        b"".join(dense1_weights),
        b"".join(output_bias),
        b"".join(output_weights),
    ]
    for spec, payload in zip(SECTIONS, sections, strict=True):
        require(len(payload) == spec.byte_count, f"{spec.name} byte count mismatch")
    return sections


def build_container(
    sections: list[bytes], converter_sha256: str, source_commit: str, source_tree: str
) -> tuple[bytes, list[str]]:
    require(len(sections) == len(SECTIONS), "section count mismatch")
    payload = b"".join(sections)
    require(len(payload) == PAYLOAD_BYTES, "payload byte count mismatch")
    section_digests = [sha256_bytes(section) for section in sections]
    payload_digest = sha256_bytes(payload)

    header = bytearray(HEADER_BYTES)
    header[0:16] = MAGIC
    struct.pack_into("<IHHIQQBBBB", header, 16, ENDIAN_TAG, VERSION_MAJOR, VERSION_MINOR,
                     HEADER_BYTES, PAYLOAD_BYTES, FILE_BYTES, 1, 1, 1, 0)
    struct.pack_into(
        "<13I",
        header,
        48,
        FEATURE_DIMENSIONS,
        MAXIMUM_ACTIVE,
        TRANSFORMER_LANES,
        PERSPECTIVE_COUNT,
        PSQT_BUCKETS,
        LAYER_STACKS,
        DENSE0_INPUTS,
        DENSE0_OUTPUTS,
        DENSE0_PADDED_OUTPUTS,
        DENSE1_INPUTS,
        DENSE1_OUTPUTS,
        OUTPUT_INPUTS,
        OUTPUT_OUTPUTS,
    )
    struct.pack_into("<HHII4B", header, 100, len(SECTIONS), DIRECTORY_ENTRY_BYTES,
                     DIRECTORY_OFFSET, HEADER_BYTES, 1, 1, 1, 1)
    header[144:176] = digest_bytes(payload_digest, "payload SHA-256")
    header[176:208] = digest_bytes(RULE_PROFILE_SHA256, "rule profile SHA-256")
    header[208:240] = digest_bytes(LEGACY_CONTRACT_SHA256, "legacy feature contract SHA-256")
    header[240:272] = digest_bytes(CONTRACT_SHA256, "container contract SHA-256")
    header[272:304] = digest_bytes(LEGACY_SHA256, "origin artifact SHA-256")
    header[304:336] = digest_bytes(converter_sha256, "converter SHA-256")
    header[336:356] = parse_git_identity(source_commit, "source commit")
    header[356:376] = parse_git_identity(source_tree, "source tree")

    expected_offset = HEADER_BYTES
    for index, (spec, section_digest) in enumerate(zip(SECTIONS, section_digests, strict=True)):
        require(spec.offset == expected_offset, f"{spec.name} is not contiguous")
        entry = DIRECTORY_OFFSET + index * DIRECTORY_ENTRY_BYTES
        shape = list(spec.shape) + [0] * (3 - len(spec.shape))
        struct.pack_into("<HBBQQIII", header, entry, spec.section_id, spec.dtype_id,
                         len(spec.shape), spec.offset, spec.byte_count, *shape)
        header[entry + 32:entry + 64] = digest_bytes(section_digest, f"{spec.name} SHA-256")
        expected_offset += spec.byte_count
    require(expected_offset == FILE_BYTES, "section directory does not consume the file")

    struct.pack_into("<I", header, CRC_OFFSET, crc32c(header[:CRC_OFFSET]))
    container = bytes(header) + payload
    require(len(container) == FILE_BYTES, "container size mismatch")
    return container, section_digests


def verify_built_container(container: bytes, expected_sections: list[bytes]) -> None:
    require(len(container) == FILE_BYTES, "post-build container size mismatch")
    require(container[:16] == MAGIC, "post-build magic mismatch")
    require(struct.unpack_from("<I", container, CRC_OFFSET)[0] == crc32c(container[:CRC_OFFSET]),
            "post-build header CRC32C mismatch")
    require(sha256_bytes(container[HEADER_BYTES:]) == container[144:176].hex(),
            "post-build payload SHA-256 mismatch")
    for index, (spec, expected) in enumerate(zip(SECTIONS, expected_sections, strict=True)):
        entry = DIRECTORY_OFFSET + index * DIRECTORY_ENTRY_BYTES
        section_id, dtype_id, rank, offset, count, shape0, shape1, shape2 = struct.unpack_from(
            "<HBBQQIII", container, entry
        )
        require((section_id, dtype_id, rank, offset, count)
                == (spec.section_id, spec.dtype_id, len(spec.shape), spec.offset, spec.byte_count),
                f"post-build {spec.name} directory mismatch")
        require((shape0, shape1, shape2) == tuple(list(spec.shape) + [0] * (3 - len(spec.shape))),
                f"post-build {spec.name} shape mismatch")
        actual = container[offset:offset + count]
        require(actual == expected, f"post-build {spec.name} tensor bytes changed")
        require(sha256_bytes(actual) == container[entry + 32:entry + 64].hex(),
                f"post-build {spec.name} SHA-256 mismatch")
    require(container[116:144] == bytes(28), "post-build reserved range 116 is nonzero")
    require(container[376:384] == bytes(8), "post-build reserved range 376 is nonzero")
    require(container[960:1020] == bytes(60), "post-build reserved range 960 is nonzero")


def write_exclusive(path: Path, payload: bytes) -> None:
    require(path.parent.is_dir(), f"output parent does not exist: {path.parent}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def convert(arguments: argparse.Namespace) -> dict[str, object]:
    require(arguments.source_clean == "true", "dirty-source conversion is forbidden")
    require(arguments.legacy.name == LEGACY_BASENAME, "legacy origin basename mismatch")
    require(not arguments.output.exists(), "container output already exists")
    require(not arguments.receipt.exists(), "conversion receipt already exists")

    authenticated_bytes(arguments.contract, CONTRACT_BYTES, CONTRACT_SHA256, "container contract")
    authenticated_bytes(
        arguments.legacy_contract,
        LEGACY_CONTRACT_BYTES,
        LEGACY_CONTRACT_SHA256,
        "legacy parser contract",
    )
    legacy = authenticated_bytes(arguments.legacy, LEGACY_BYTES, LEGACY_SHA256, "legacy origin")
    converter_path = Path(__file__).resolve()
    converter_payload = converter_path.read_bytes()
    converter_sha256 = sha256_bytes(converter_payload)

    sections = extract_legacy_sections(legacy)
    container, section_digests = build_container(
        sections, converter_sha256, arguments.source_commit, arguments.source_tree
    )
    verify_built_container(container, sections)
    container_sha256 = sha256_bytes(container)

    receipt: dict[str, object] = {
        "schema": "crazyhouse-nnue-v2-legacy-control-conversion-receipt/v1",
        "created_utc": utc_now(),
        "status": "PASS_AUTHENTICATED_LEGACY_CONTROL_CONVERSION",
        "evidence_class": "E1_ENGINEERING",
        "project": "Crazyhouse-Stockfish",
        "variant": "crazyhouse",
        "purpose": "LEGACY_V1_CONTROL",
        "origin_kind": "AUTHENTICATED_LEGACY_V1",
        "source": {
            "commit": arguments.source_commit,
            "tree": arguments.source_tree,
            "clean": True,
        },
        "contracts": {
            "container": {
                "basename": arguments.contract.name,
                "bytes": CONTRACT_BYTES,
                "sha256": CONTRACT_SHA256,
            },
            "legacy_parser": {
                "basename": arguments.legacy_contract.name,
                "bytes": LEGACY_CONTRACT_BYTES,
                "sha256": LEGACY_CONTRACT_SHA256,
            },
            "rule_profile_sha256": RULE_PROFILE_SHA256,
        },
        "origin": {
            "basename": arguments.legacy.name,
            "bytes": LEGACY_BYTES,
            "sha256": LEGACY_SHA256,
            "authenticated_before_tensor_access": True,
        },
        "converter": {
            "basename": converter_path.name,
            "bytes": len(converter_payload),
            "sha256": converter_sha256,
        },
        "container": {
            "basename": arguments.output.name,
            "bytes": len(container),
            "sha256": container_sha256,
            "payload_bytes": PAYLOAD_BYTES,
            "payload_sha256": sha256_bytes(container[HEADER_BYTES:]),
            "header_crc32c": f"{struct.unpack_from('<I', container, CRC_OFFSET)[0]:08x}",
            "sections": [
                {
                    "id": spec.section_id,
                    "name": spec.name,
                    "offset": spec.offset,
                    "bytes": spec.byte_count,
                    "sha256": digest,
                }
                for spec, digest in zip(SECTIONS, section_digests, strict=True)
            ],
        },
        "transaction": {
            "exclusive_create": True,
            "container_and_receipt_complete_or_container_removed": True,
        },
        "claims": {
            "tensor_values_changed": False,
            "legacy_control_only": True,
            "productive_v2_representation": False,
            "training_admissible": False,
            "model_selection": False,
            "timing": False,
            "strength": False,
            "openbench": False,
            "release": False,
            "legacy_v1_remains_default": True,
        },
    }
    receipt_payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")

    try:
        write_exclusive(arguments.output, container)
        require(arguments.output.read_bytes() == container, "persisted container byte mismatch")
        write_exclusive(arguments.receipt, receipt_payload)
        require(arguments.receipt.read_bytes() == receipt_payload, "persisted receipt byte mismatch")
    except Exception:
        arguments.output.unlink(missing_ok=True)
        arguments.receipt.unlink(missing_ok=True)
        raise
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("convert")
    command.add_argument("--legacy", required=True, type=Path)
    command.add_argument("--contract", required=True, type=Path)
    command.add_argument("--legacy-contract", required=True, type=Path)
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--receipt", required=True, type=Path)
    command.add_argument("--source-commit", required=True)
    command.add_argument("--source-tree", required=True)
    command.add_argument("--source-clean", required=True, choices=("true", "false"))
    arguments = parser.parse_args()

    try:
        if arguments.command == "convert":
            receipt = convert(arguments)
            print(json.dumps(receipt, indent=2, sort_keys=True))
            return 0
        fail("unknown command")
    except (ControlError, OSError, struct.error, ValueError) as error:
        print(f"FAIL crazyhouse_v2_legacy_control: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
