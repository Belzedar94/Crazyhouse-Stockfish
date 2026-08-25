#!/usr/bin/env python3
"""Independent verifier for the purpose-marked Crazyhouse V2 legacy control."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from typing import Any, NoReturn

import numpy as np


LEGACY_BASENAME = "crazyhouse_run15rl_e190_l03.nnue"
LEGACY_BYTES = 58_534_811
LEGACY_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
PRODUCTIVE_BYTES = 960_324
PRODUCTIVE_SHA256 = "ef209a669eeaa72ec48eaca115154c94556fe90064b751ac4c623e01823098fa"
CONTRACT_BYTES = 13_923
CONTRACT_SHA256 = "1d738d8c956c9d15a74f44dcf145d33aa72579da83d8be7421ca29050ad04759"
LEGACY_CONTRACT_BYTES = 2_121
LEGACY_CONTRACT_SHA256 = "82b4b5dafa9e280479ea47057da88625d2bdfa40801e5dcd51ba861a52c30f00"
GOLDEN_BYTES = 58_102
GOLDEN_SHA256 = "53866d1139a85ac5e982e6ffd74ce6d0c154abdc7ea46b68fe238aa4ea822eb6"
RULE_PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"

MAGIC = b"CHNNUEV2LC1" + bytes(5)
TRACE_MAGIC = b"CHLC_TRACE_V1" + bytes(3)
ENDIAN_TAG = 0x01020304
HEADER_BYTES = 1_024
PAYLOAD_BYTES = 58_534_688
FILE_BYTES = 58_535_712
DIRECTORY_OFFSET = 384
DIRECTORY_ENTRY_BYTES = 64
CRC_OFFSET = 1_020
FEATURE_DIMENSIONS = 55_296
MAXIMUM_ACTIVE = 128
TRANSFORMER_LANES = 512
PSQT_BUCKETS = 8
LAYER_STACKS = 8
DENSE0_INPUTS = 1_024
DENSE0_OUTPUTS = 16
DENSE0_PADDED_OUTPUTS = 32
DENSE1_INPUTS = 32
DENSE1_OUTPUTS = 32
OUTPUT_INPUTS = 32


class VerificationError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise VerificationError(message)


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


def canonical_hex(value: str, digits: int, label: str) -> str:
    require(len(value) == digits, f"{label} length mismatch")
    require(value != "0" * digits, f"{label} is zero")
    require(all(character in "0123456789abcdef" for character in value),
            f"{label} is not canonical lowercase hexadecimal")
    return value


def crc32c(payload: bytes | bytearray | memoryview) -> int:
    value = 0xFFFFFFFF
    for octet in payload:
        value ^= int(octet)
        for _ in range(8):
            value = (value >> 1) ^ (0x82F63B78 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


def canonical_json(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


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


@dataclass(frozen=True)
class SectionSpec:
    section_id: int
    name: str
    dtype_id: int
    dtype: str
    shape: tuple[int, ...]
    offset: int
    byte_count: int


SECTIONS = (
    SectionSpec(1, "transformer_bias", 2, "<i2", (512,), 1_024, 1_024),
    SectionSpec(2, "transformer_weights", 2, "<i2", (55_296, 512), 2_048, 56_623_104),
    SectionSpec(3, "psqt_weights", 3, "<i4", (55_296, 8), 56_625_152, 1_769_472),
    SectionSpec(4, "dense0_bias", 3, "<i4", (8, 16), 58_394_624, 512),
    SectionSpec(5, "dense0_weights", 1, "i1", (8, 16, 1_024), 58_395_136, 131_072),
    SectionSpec(6, "dense1_bias", 3, "<i4", (8, 32), 58_526_208, 1_024),
    SectionSpec(7, "dense1_weights", 1, "i1", (8, 32, 32), 58_527_232, 8_192),
    SectionSpec(8, "output_bias", 3, "<i4", (8,), 58_535_424, 32),
    SectionSpec(9, "output_weights", 1, "i1", (8, 32), 58_535_456, 256),
)


@dataclass(frozen=True)
class ExpectedProvenance:
    converter_sha256: str
    source_commit: str
    source_tree: str


@dataclass
class ParsedControl:
    file_sha256: str
    payload_sha256: str
    header_crc32c: int
    provenance: ExpectedProvenance
    tensors: dict[str, np.ndarray]
    section_digests: tuple[str, ...]


def parse_container(
    payload: bytes | bytearray, expected: ExpectedProvenance
) -> ParsedControl:
    converter = canonical_hex(expected.converter_sha256, 64, "expected converter SHA-256")
    commit = canonical_hex(expected.source_commit, 40, "expected source commit")
    tree = canonical_hex(expected.source_tree, 40, "expected source tree")
    require(len(payload) == FILE_BYTES, "container file byte count mismatch")
    require(struct.unpack_from("<I", payload, CRC_OFFSET)[0] == crc32c(memoryview(payload)[:CRC_OFFSET]),
            "container header CRC32C mismatch")
    require(payload[:16] == MAGIC, "container magic mismatch")
    require(
        struct.unpack_from("<IHHIQQBBBB", payload, 16)
        == (ENDIAN_TAG, 1, 0, HEADER_BYTES, PAYLOAD_BYTES, FILE_BYTES, 1, 1, 1, 0),
        "container framing mismatch",
    )
    require(
        struct.unpack_from("<13I", payload, 48)
        == (
            FEATURE_DIMENSIONS,
            MAXIMUM_ACTIVE,
            TRANSFORMER_LANES,
            2,
            PSQT_BUCKETS,
            LAYER_STACKS,
            DENSE0_INPUTS,
            DENSE0_OUTPUTS,
            DENSE0_PADDED_OUTPUTS,
            DENSE1_INPUTS,
            DENSE1_OUTPUTS,
            OUTPUT_INPUTS,
            1,
        ),
        "container architecture mismatch",
    )
    require(
        struct.unpack_from("<HHII4B", payload, 100)
        == (len(SECTIONS), DIRECTORY_ENTRY_BYTES, DIRECTORY_OFFSET, HEADER_BYTES, 1, 1, 1, 1),
        "container directory or arithmetic contract mismatch",
    )
    require(payload[116:144] == bytes(28), "reserved range 116 is nonzero")
    require(payload[376:384] == bytes(8), "reserved range 376 is nonzero")
    require(payload[960:1020] == bytes(60), "reserved range 960 is nonzero")
    require(payload[176:208].hex() == RULE_PROFILE_SHA256, "rule profile identity mismatch")
    require(payload[208:240].hex() == LEGACY_CONTRACT_SHA256,
            "legacy feature contract identity mismatch")
    require(payload[240:272].hex() == CONTRACT_SHA256, "container contract identity mismatch")
    require(payload[272:304].hex() == LEGACY_SHA256, "origin artifact identity mismatch")
    require(payload[304:336].hex() == converter, "converter provenance mismatch")
    require(payload[336:356].hex() == commit, "source commit provenance mismatch")
    require(payload[356:376].hex() == tree, "source tree provenance mismatch")

    tensors: dict[str, np.ndarray] = {}
    section_digests: list[str] = []
    expected_offset = HEADER_BYTES
    for index, spec in enumerate(SECTIONS):
        entry = DIRECTORY_OFFSET + index * DIRECTORY_ENTRY_BYTES
        shape = spec.shape + (0,) * (3 - len(spec.shape))
        require(
            struct.unpack_from("<HBBQQIII", payload, entry)
            == (
                spec.section_id,
                spec.dtype_id,
                len(spec.shape),
                spec.offset,
                spec.byte_count,
                *shape,
            ),
            f"{spec.name} directory mismatch",
        )
        require(spec.offset == expected_offset, f"{spec.name} is not contiguous")
        declared_digest = payload[entry + 32:entry + 64].hex()
        require(declared_digest != "0" * 64, f"{spec.name} section digest is zero")
        section = memoryview(payload)[spec.offset:spec.offset + spec.byte_count]
        require(sha256_bytes(section) == declared_digest, f"{spec.name} section digest mismatch")
        count = int(np.prod(spec.shape, dtype=np.int64))
        tensor = np.frombuffer(payload, dtype=np.dtype(spec.dtype), count=count, offset=spec.offset)
        require(tensor.nbytes == spec.byte_count, f"{spec.name} tensor byte layout mismatch")
        tensors[spec.name] = tensor.reshape(spec.shape)
        section_digests.append(declared_digest)
        expected_offset += spec.byte_count
    require(expected_offset == FILE_BYTES, "section directory does not consume strict EOF")

    payload_sha256 = sha256_bytes(memoryview(payload)[HEADER_BYTES:])
    require(payload[144:176].hex() == payload_sha256, "payload SHA-256 mismatch")
    return ParsedControl(
        file_sha256=sha256_bytes(payload),
        payload_sha256=payload_sha256,
        header_crc32c=struct.unpack_from("<I", payload, CRC_OFFSET)[0],
        provenance=expected,
        tensors=tensors,
        section_digests=tuple(section_digests),
    )


def parse_container_path(path: Path, expected: ExpectedProvenance) -> ParsedControl:
    require(path.is_file(), "container path is missing or is not a regular file")
    return parse_container(path.read_bytes(), expected)


def reserialize(parsed: ParsedControl) -> bytes:
    sections = [parsed.tensors[spec.name].tobytes(order="C") for spec in SECTIONS]
    for spec, section in zip(SECTIONS, sections, strict=True):
        require(len(section) == spec.byte_count, f"reserialized {spec.name} byte count mismatch")
    payload = b"".join(sections)
    require(len(payload) == PAYLOAD_BYTES, "reserialized payload byte count mismatch")
    header = bytearray(HEADER_BYTES)
    header[:16] = MAGIC
    struct.pack_into(
        "<IHHIQQBBBB", header, 16, ENDIAN_TAG, 1, 0, HEADER_BYTES,
        PAYLOAD_BYTES, FILE_BYTES, 1, 1, 1, 0,
    )
    struct.pack_into(
        "<13I", header, 48, FEATURE_DIMENSIONS, MAXIMUM_ACTIVE, TRANSFORMER_LANES,
        2, PSQT_BUCKETS, LAYER_STACKS, DENSE0_INPUTS, DENSE0_OUTPUTS,
        DENSE0_PADDED_OUTPUTS, DENSE1_INPUTS, DENSE1_OUTPUTS, OUTPUT_INPUTS, 1,
    )
    struct.pack_into(
        "<HHII4B", header, 100, len(SECTIONS), DIRECTORY_ENTRY_BYTES,
        DIRECTORY_OFFSET, HEADER_BYTES, 1, 1, 1, 1,
    )
    header[144:176] = hashlib.sha256(payload).digest()
    header[176:208] = bytes.fromhex(RULE_PROFILE_SHA256)
    header[208:240] = bytes.fromhex(LEGACY_CONTRACT_SHA256)
    header[240:272] = bytes.fromhex(CONTRACT_SHA256)
    header[272:304] = bytes.fromhex(LEGACY_SHA256)
    header[304:336] = bytes.fromhex(parsed.provenance.converter_sha256)
    header[336:356] = bytes.fromhex(parsed.provenance.source_commit)
    header[356:376] = bytes.fromhex(parsed.provenance.source_tree)
    for index, (spec, section) in enumerate(zip(SECTIONS, sections, strict=True)):
        entry = DIRECTORY_OFFSET + index * DIRECTORY_ENTRY_BYTES
        shape = spec.shape + (0,) * (3 - len(spec.shape))
        struct.pack_into(
            "<HBBQQIII", header, entry, spec.section_id, spec.dtype_id,
            len(spec.shape), spec.offset, spec.byte_count, *shape,
        )
        header[entry + 32:entry + 64] = hashlib.sha256(section).digest()
    struct.pack_into("<I", header, CRC_OFFSET, crc32c(header[:CRC_OFFSET]))
    return bytes(header) + payload


def verify_conversion_receipt(
    path: Path,
    receipt_payload: bytes,
    converter_path: Path,
    converter_payload: bytes,
    contract_path: Path,
    legacy_contract_path: Path,
    legacy_path: Path,
    container_path: Path,
    container_payload: bytes,
    parsed: ParsedControl,
) -> str:
    try:
        receipt = json.loads(receipt_payload)
    except json.JSONDecodeError as error:
        raise VerificationError("conversion receipt is invalid JSON") from error
    require(receipt["schema"] == "crazyhouse-nnue-v2-legacy-control-conversion-receipt/v1",
            "conversion receipt schema mismatch")
    require(receipt["status"] == "PASS_AUTHENTICATED_LEGACY_CONTROL_CONVERSION",
            "conversion receipt status mismatch")
    require(receipt["evidence_class"] == "E1_ENGINEERING", "conversion evidence class mismatch")
    require(receipt["project"] == "Crazyhouse-Stockfish" and receipt["variant"] == "crazyhouse",
            "conversion project identity mismatch")
    require(receipt["purpose"] == "LEGACY_V1_CONTROL", "conversion purpose mismatch")
    require(receipt["origin_kind"] == "AUTHENTICATED_LEGACY_V1", "conversion origin mismatch")
    require(receipt["source"] == {
        "commit": parsed.provenance.source_commit,
        "tree": parsed.provenance.source_tree,
        "clean": True,
    }, "conversion source identity mismatch")
    require(receipt["contracts"]["container"] == {
        "basename": contract_path.name,
        "bytes": CONTRACT_BYTES,
        "sha256": CONTRACT_SHA256,
    }, "conversion container contract receipt mismatch")
    require(receipt["contracts"]["legacy_parser"] == {
        "basename": legacy_contract_path.name,
        "bytes": LEGACY_CONTRACT_BYTES,
        "sha256": LEGACY_CONTRACT_SHA256,
    }, "conversion legacy contract receipt mismatch")
    require(receipt["contracts"]["rule_profile_sha256"] == RULE_PROFILE_SHA256,
            "conversion rule profile receipt mismatch")
    require(receipt["origin"] == {
        "basename": legacy_path.name,
        "bytes": LEGACY_BYTES,
        "sha256": LEGACY_SHA256,
        "authenticated_before_tensor_access": True,
    }, "conversion origin receipt mismatch")
    require(receipt["converter"] == {
        "basename": converter_path.name,
        "bytes": len(converter_payload),
        "sha256": sha256_bytes(converter_payload),
    }, "conversion converter receipt mismatch")
    expected_sections = [
        {
            "id": spec.section_id,
            "name": spec.name,
            "offset": spec.offset,
            "bytes": spec.byte_count,
            "sha256": digest,
        }
        for spec, digest in zip(SECTIONS, parsed.section_digests, strict=True)
    ]
    require(receipt["container"] == {
        "basename": container_path.name,
        "bytes": FILE_BYTES,
        "sha256": sha256_bytes(container_payload),
        "payload_bytes": PAYLOAD_BYTES,
        "payload_sha256": parsed.payload_sha256,
        "header_crc32c": f"{parsed.header_crc32c:08x}",
        "sections": expected_sections,
    }, "conversion container receipt mismatch")
    require(receipt["transaction"] == {
        "exclusive_create": True,
        "container_and_receipt_complete_or_container_removed": True,
    }, "conversion transaction receipt mismatch")
    require(receipt["claims"] == {
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
    }, "conversion claim boundary mismatch")
    require(isinstance(receipt.get("created_utc"), str) and receipt["created_utc"].endswith("Z"),
            "conversion receipt timestamp is missing")
    normalized = copy.deepcopy(receipt)
    del normalized["created_utc"]
    return sha256_bytes(canonical_json(normalized))


def repair_header_crc(candidate: bytearray) -> None:
    struct.pack_into("<I", candidate, CRC_OFFSET, crc32c(memoryview(candidate)[:CRC_OFFSET]))


def changed_identity(value: str) -> str:
    return ("1" if value[0] == "0" else "0") + value[1:]


class FailClosedSlot:
    def __init__(self) -> None:
        self.current: ParsedControl | None = None

    def load(self, payload: bytes | bytearray, expected: ExpectedProvenance) -> None:
        self.current = None
        self.current = parse_container(payload, expected)


def verify_negative_matrix(
    valid: bytes,
    legacy: bytes,
    productive: bytes,
    expected: ExpectedProvenance,
    container_path: Path,
) -> tuple[int, str]:
    names: list[str] = []

    def reject(name: str, payload: bytes | bytearray, provenance: ExpectedProvenance = expected) -> None:
        try:
            parse_container(payload, provenance)
        except VerificationError:
            names.append(name)
        else:
            fail(f"independent parser accepted negative: {name}")

    candidate = bytearray(valid)

    def reject_header(name: str, offset: int) -> None:
        candidate[:HEADER_BYTES] = valid[:HEADER_BYTES]
        candidate[offset] ^= 1
        repair_header_crc(candidate)
        reject(name, candidate)

    reject_header("magic", 0)
    reject_header("magic-padding", 11)
    for name, offset in (
        ("endian", 16), ("version-major", 20), ("version-minor", 22),
        ("header-bytes", 24), ("payload-bytes", 28), ("file-bytes", 36),
        ("committed", 44), ("purpose", 45), ("origin", 46), ("dirty-source", 47),
    ):
        reject_header(name, offset)
    for offset in range(48, 100, 4):
        reject_header(f"architecture-{offset}", offset)
    for name, offset in (
        ("section-count", 100), ("directory-entry-bytes", 102),
        ("directory-offset", 104), ("payload-offset", 108),
        ("transformer-arithmetic", 112), ("psqt-arithmetic", 113),
        ("dense-arithmetic", 114), ("activation", 115),
    ):
        reject_header(name, offset)
    for name, offset in (("reserved-116", 116), ("reserved-376", 376), ("reserved-960", 960)):
        reject_header(name, offset)
    for name, offset in (
        ("payload-identity", 144), ("rule-identity", 176), ("feature-identity", 208),
        ("contract-identity", 240), ("origin-identity", 272),
        ("converter-identity", 304), ("source-commit-identity", 336),
        ("source-tree-identity", 356),
    ):
        reject_header(name, offset)
    for name, offset in (
        ("directory-id", 384), ("directory-dtype", 386), ("directory-rank", 387),
        ("directory-offset-field", 388), ("directory-bytes-field", 396),
        ("directory-shape0", 400), ("directory-shape1", 404), ("directory-shape2", 408),
    ):
        reject_header(name, offset)
    for index in range(len(SECTIONS)):
        reject_header(f"section-digest-{index + 1}",
                      DIRECTORY_OFFSET + index * DIRECTORY_ENTRY_BYTES + 32)

    candidate[:HEADER_BYTES] = valid[:HEADER_BYTES]
    candidate[CRC_OFFSET] ^= 1
    reject("header-crc", candidate)
    candidate[:HEADER_BYTES] = valid[:HEADER_BYTES]
    for spec in SECTIONS:
        candidate[spec.offset] ^= 1
        reject(f"payload-section-{spec.section_id}", candidate)
        candidate[spec.offset] ^= 1
    reject("truncated", valid[:-1])
    reject("extended", valid + b"\0")
    reject("registered-legacy-artifact", legacy)
    reject("productive-v2-artifact", productive)
    reject("unrelated-exact-size", bytes(FILE_BYTES))

    missing = container_path.with_name(container_path.name + ".missing-independent")
    require(not missing.exists(), "negative missing path unexpectedly exists")
    for name, path in (("missing-path", missing), ("non-regular-path", container_path.parent)):
        try:
            parse_container_path(path, expected)
        except VerificationError:
            names.append(name)
        else:
            fail(f"independent path parser accepted negative: {name}")

    reject("converter-requirement", valid, ExpectedProvenance(
        changed_identity(expected.converter_sha256), expected.source_commit, expected.source_tree))
    reject("commit-requirement", valid, ExpectedProvenance(
        expected.converter_sha256, changed_identity(expected.source_commit), expected.source_tree))
    reject("tree-requirement", valid, ExpectedProvenance(
        expected.converter_sha256, expected.source_commit, changed_identity(expected.source_tree)))
    reject("noncanonical-requirement", valid, ExpectedProvenance(
        expected.converter_sha256, "A" + expected.source_commit[1:], expected.source_tree))

    slot = FailClosedSlot()
    slot.load(valid, expected)
    require(slot.current is not None, "fail-closed slot positive load failed")
    replacement = bytearray(valid)
    replacement[0] ^= 1
    repair_header_crc(replacement)
    try:
        slot.load(replacement, expected)
    except VerificationError:
        require(slot.current is None, "failed replacement retained parsed tensors")
        names.append("failed-replacement-unloads")
    else:
        fail("fail-closed slot accepted failed replacement")

    require(len(names) >= 62, "independent negative matrix is below frozen minimum")
    require(len(names) == len(set(names)), "independent negative names are not unique")
    return len(names), sha256_bytes(canonical_json(names))


def signed32(value: int) -> int:
    bits = value & 0xFFFFFFFF
    return bits if bits < 0x80000000 else bits - 0x100000000


def trunc_divide_two(value: int) -> int:
    return value // 2 if value >= 0 else -((-value) // 2)


def activation(values: np.ndarray) -> np.ndarray:
    positive = np.maximum(values.astype(np.int64), 0)
    return np.minimum(positive // 64, 127).astype(np.uint8)


@dataclass
class ScalarTrace:
    board_piece_count: int
    side_to_move: int
    selected_bucket: int
    active: tuple[tuple[int, ...], tuple[int, ...]]
    transformer_bits: tuple[np.ndarray, np.ndarray]
    psqt_bits: tuple[np.ndarray, np.ndarray]
    transformed: np.ndarray
    dense0: tuple[np.ndarray, ...]
    dense0_activation: tuple[np.ndarray, ...]
    dense1: tuple[np.ndarray, ...]
    dense1_activation: tuple[np.ndarray, ...]
    output: tuple[int, ...]
    psqt: tuple[int, ...]


def scalar_trace(parsed: ParsedControl, observation: dict[str, Any]) -> ScalarTrace:
    active = (
        tuple(int(value) for value in observation["active_white"]),
        tuple(int(value) for value in observation["active_black"]),
    )
    require(all(len(rows) <= MAXIMUM_ACTIVE for rows in active), "golden active count overflow")
    require(all(0 <= feature < FEATURE_DIMENSIONS for rows in active for feature in rows),
            "golden feature index overflow")
    require(all(len(rows) == len(set(rows)) for rows in active), "golden duplicate feature")
    side_to_move = 0 if observation["side_to_move"] == "white" else 1
    require(observation["side_to_move"] in ("white", "black"), "golden side to move invalid")
    board_piece_count = int(observation["board_piece_count"])
    selected_bucket = (board_piece_count - 1) * LAYER_STACKS // 32
    require(2 <= board_piece_count <= 32 and 0 <= selected_bucket < LAYER_STACKS,
            "golden board piece count invalid")
    require(selected_bucket == int(observation["correct_bucket"]), "golden bucket formula mismatch")

    bias_bits = parsed.tensors["transformer_bias"].view(np.uint16).astype(np.uint64)
    transformer_bits: list[np.ndarray] = []
    psqt_bits: list[np.ndarray] = []
    for rows in active:
        indices = np.asarray(rows, dtype=np.intp)
        weight_bits = parsed.tensors["transformer_weights"][indices].view(np.uint16)
        accumulated = (bias_bits + weight_bits.astype(np.uint64).sum(axis=0, dtype=np.uint64)) & 0xFFFF
        transformer_bits.append(accumulated.astype(np.uint16))
        psqt_weight_bits = parsed.tensors["psqt_weights"][indices].view(np.uint32)
        psqt_accumulated = psqt_weight_bits.astype(np.uint64).sum(axis=0, dtype=np.uint64) & 0xFFFFFFFF
        psqt_bits.append(psqt_accumulated.astype(np.uint32))

    transformed_halves: list[np.ndarray] = []
    for perspective in (side_to_move, 1 - side_to_move):
        bits = transformer_bits[perspective].astype(np.int64)
        signed = np.where(bits < 0x8000, bits, bits - 0x10000)
        transformed_halves.append(np.clip(signed, 0, 127).astype(np.uint8))
    transformed = np.concatenate(transformed_halves)

    dense0_rows: list[np.ndarray] = []
    dense0_activations: list[np.ndarray] = []
    dense1_rows: list[np.ndarray] = []
    dense1_activations: list[np.ndarray] = []
    output_rows: list[int] = []
    psqt_rows: list[int] = []
    transformed_i64 = transformed.astype(np.int64)
    for bucket in range(LAYER_STACKS):
        affine0_unwrapped = (
            parsed.tensors["dense0_bias"][bucket].astype(np.int64)
            + parsed.tensors["dense0_weights"][bucket].astype(np.int64) @ transformed_i64
        )
        affine0 = np.asarray([signed32(int(value)) for value in affine0_unwrapped], dtype=np.int32)
        active0 = activation(affine0)
        dense1_input = np.zeros(DENSE1_INPUTS, dtype=np.int64)
        dense1_input[:DENSE0_OUTPUTS] = active0.astype(np.int64)
        affine1_unwrapped = (
            parsed.tensors["dense1_bias"][bucket].astype(np.int64)
            + parsed.tensors["dense1_weights"][bucket].astype(np.int64) @ dense1_input
        )
        affine1 = np.asarray([signed32(int(value)) for value in affine1_unwrapped], dtype=np.int32)
        active1 = activation(affine1)
        output_unwrapped = (
            int(parsed.tensors["output_bias"][bucket])
            + int(parsed.tensors["output_weights"][bucket].astype(np.int64) @ active1.astype(np.int64))
        )
        output_rows.append(signed32(output_unwrapped))
        difference = (
            int(psqt_bits[side_to_move][bucket]) - int(psqt_bits[1 - side_to_move][bucket])
        ) & 0xFFFFFFFF
        psqt_rows.append(trunc_divide_two(signed32(difference)))
        dense0_rows.append(affine0)
        dense0_activations.append(active0)
        dense1_rows.append(affine1)
        dense1_activations.append(active1)

    return ScalarTrace(
        board_piece_count=board_piece_count,
        side_to_move=side_to_move,
        selected_bucket=selected_bucket,
        active=active,
        transformer_bits=(transformer_bits[0], transformer_bits[1]),
        psqt_bits=(psqt_bits[0], psqt_bits[1]),
        transformed=transformed,
        dense0=tuple(dense0_rows),
        dense0_activation=tuple(dense0_activations),
        dense1=tuple(dense1_rows),
        dense1_activation=tuple(dense1_activations),
        output=tuple(output_rows),
        psqt=tuple(psqt_rows),
    )


def serialize_trace(trace: ScalarTrace) -> bytes:
    output = bytearray(TRACE_MAGIC)
    output.extend(struct.pack(
        "<IBBH", trace.board_piece_count, trace.side_to_move, trace.selected_bucket, 0
    ))
    for perspective in range(2):
        output.extend(struct.pack("<I", len(trace.active[perspective])))
        for feature in trace.active[perspective]:
            output.extend(struct.pack("<I", feature))
        output.extend(trace.transformer_bits[perspective].astype("<u2", copy=False).tobytes())
        output.extend(trace.psqt_bits[perspective].astype("<u4", copy=False).tobytes())
    output.extend(trace.transformed.tobytes())
    for bucket in range(LAYER_STACKS):
        for value in trace.dense0[bucket]:
            output.extend(struct.pack("<I", int(value) & 0xFFFFFFFF))
        output.extend(trace.dense0_activation[bucket].tobytes())
        for value in trace.dense1[bucket]:
            output.extend(struct.pack("<I", int(value) & 0xFFFFFFFF))
        output.extend(trace.dense1_activation[bucket].tobytes())
        output.extend(struct.pack("<I", trace.output[bucket] & 0xFFFFFFFF))
        output.extend(struct.pack("<I", trace.psqt[bucket] & 0xFFFFFFFF))
    return bytes(output)


def parse_vector(text: str, case_id: str, component: str) -> list[int]:
    try:
        values = [int(value) for value in text.split(",")]
    except ValueError as error:
        raise VerificationError(f"{case_id} emitted malformed {component}") from error
    require(len(values) == LAYER_STACKS, f"{case_id} emitted wrong {component} bucket count")
    require(all(-(1 << 31) <= value < (1 << 31) for value in values),
            f"{case_id} emitted out-of-range {component}")
    return values


def binary_environment() -> dict[str, str]:
    environment = os.environ.copy()
    prefixes = [r"C:\msys64\mingw64\bin", r"C:\msys64\usr\bin"]
    environment["PATH"] = os.pathsep.join(prefixes + [environment.get("PATH", "")])
    return environment


def run_cpp(
    evaluator: Path,
    container: Path,
    legacy: Path,
    productive: Path,
    expected: ExpectedProvenance,
    cases: list[dict[str, Any]],
) -> subprocess.CompletedProcess[bytes]:
    protocol = "".join(case["observation"]["fen"] + "\n" for case in cases).encode("utf-8")
    try:
        return subprocess.run(
            [
                str(evaluator), str(container), str(legacy), str(productive),
                expected.converter_sha256, expected.source_commit, expected.source_tree,
            ],
            input=protocol,
            capture_output=True,
            timeout=300,
            check=False,
            env=binary_environment(),
        )
    except subprocess.TimeoutExpired as error:
        raise VerificationError("C++ legacy-control verifier timed out") from error


def verify_oracles(
    parsed: ParsedControl,
    evaluator: Path,
    container: Path,
    legacy: Path,
    productive: Path,
    expected: ExpectedProvenance,
    golden: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, str, int, int]:
    cases = golden["cases"]
    require(len(cases) == 43 and golden["case_count"] == 43, "golden case count mismatch")
    python_rows: list[dict[str, Any]] = []
    python_protocol = bytearray()
    python_trace_hashes: dict[str, str] = {}
    for case in cases:
        case_id = case["id"]
        observation = case["observation"]
        first = scalar_trace(parsed, observation)
        second = scalar_trace(parsed, observation)
        first_bytes = serialize_trace(first)
        second_bytes = serialize_trace(second)
        require(first_bytes == second_bytes, f"{case_id} Python trace replay differs")
        require(list(first.psqt) == observation["raw_psqt"], f"{case_id} Python PSQT oracle mismatch")
        require(list(first.output) == observation["raw_positional"],
                f"{case_id} Python positional oracle mismatch")
        trace_sha256 = sha256_bytes(first_bytes)
        python_trace_hashes[case_id] = trace_sha256
        python_protocol.extend(f"{case_id}\t{trace_sha256}\n".encode("utf-8"))
        python_rows.append({
            "id": case_id,
            "selected_bucket": first.selected_bucket,
            "raw_psqt": list(first.psqt),
            "raw_positional": list(first.output),
            "trace_sha256": trace_sha256,
        })

    first_run = run_cpp(evaluator, container, legacy, productive, expected, cases)
    second_run = run_cpp(evaluator, container, legacy, productive, expected, cases)
    for index, run in enumerate((first_run, second_run), start=1):
        require(run.returncode == 0,
                f"C++ verifier replay {index} exited {run.returncode}: {run.stderr!r}")
        require(run.stderr == b"", f"C++ verifier replay {index} emitted stderr")
    require(first_run.stdout == second_run.stdout, "C++ verifier replays are not byte-identical")
    lines = first_run.stdout.decode("utf-8").splitlines()
    require(len(lines) == len(cases) + 1, "C++ verifier protocol row count mismatch")
    meta_fields = lines[0].split("\t")
    require(meta_fields[0] == "META", "C++ verifier META row missing")
    meta = dict(field.split("=", 1) for field in meta_fields[1:])
    loader_negatives = int(meta["loader_negatives"])
    eval_negatives = int(meta["eval_negatives"])
    require(loader_negatives >= 62, "C++ loader negative count below frozen minimum")
    require(eval_negatives >= 8, "C++ evaluation negative count below frozen minimum")
    require(meta["container_sha256"] == parsed.file_sha256, "C++ container identity mismatch")
    require(meta["converter_sha256"] == expected.converter_sha256, "C++ converter identity mismatch")
    require(meta["source_commit"] == expected.source_commit, "C++ source commit mismatch")
    require(meta["source_tree"] == expected.source_tree, "C++ source tree mismatch")

    for case, line in zip(cases, lines[1:], strict=True):
        case_id = case["id"]
        observation = case["observation"]
        fields = line.split("\t")
        require(len(fields) == 6 and fields[0] == "OK", f"{case_id} C++ protocol malformed")
        require(fields[1] == observation["fen"], f"{case_id} C++ canonical FEN mismatch")
        require(int(fields[2]) == observation["correct_bucket"], f"{case_id} C++ bucket mismatch")
        require(parse_vector(fields[3], case_id, "PSQT") == observation["raw_psqt"],
                f"{case_id} C++ PSQT oracle mismatch")
        require(parse_vector(fields[4], case_id, "positional") == observation["raw_positional"],
                f"{case_id} C++ positional oracle mismatch")
        require(fields[5] == python_trace_hashes[case_id],
                f"{case_id} Python/C++ full trace SHA-256 mismatch")
    return (
        python_rows,
        sha256_bytes(python_protocol),
        sha256_bytes(first_run.stdout),
        loader_negatives,
        eval_negatives,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--converter", required=True, type=Path)
    parser.add_argument("--container", required=True, type=Path)
    parser.add_argument("--conversion-receipt", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--legacy-contract", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--cpp-evaluator", required=True, type=Path)
    parser.add_argument("--legacy-network", required=True, type=Path)
    parser.add_argument("--productive-v2-network", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    try:
        require(arguments.cpp_evaluator.is_file(), "C++ evaluator executable is missing")
        converter_payload = arguments.converter.read_bytes()
        require(arguments.converter.is_file() and converter_payload,
                "converter is missing, empty, or not a regular file")
        converter_sha256 = sha256_bytes(converter_payload)
        expected = ExpectedProvenance(
            canonical_hex(converter_sha256, 64, "converter SHA-256"),
            canonical_hex(arguments.source_commit, 40, "source commit"),
            canonical_hex(arguments.source_tree, 40, "source tree"),
        )
        authenticated_bytes(arguments.contract, CONTRACT_BYTES, CONTRACT_SHA256, "container contract")
        authenticated_bytes(
            arguments.legacy_contract, LEGACY_CONTRACT_BYTES,
            LEGACY_CONTRACT_SHA256, "legacy parser contract",
        )
        golden_payload = authenticated_bytes(
            arguments.golden, GOLDEN_BYTES, GOLDEN_SHA256, "frozen raw oracle"
        )
        legacy_payload = authenticated_bytes(
            arguments.legacy_network, LEGACY_BYTES, LEGACY_SHA256, "registered legacy network"
        )
        require(arguments.legacy_network.name == LEGACY_BASENAME, "legacy network basename mismatch")
        productive_payload = authenticated_bytes(
            arguments.productive_v2_network, PRODUCTIVE_BYTES,
            PRODUCTIVE_SHA256, "productive V2 network",
        )
        require(arguments.container.is_file(), "control container is missing")
        container_payload = arguments.container.read_bytes()
        require(arguments.conversion_receipt.is_file(), "conversion receipt is missing")
        receipt_payload = arguments.conversion_receipt.read_bytes()
        parsed = parse_container(container_payload, expected)
        require(reserialize(parsed) == container_payload,
                "independent parse/reserialize is not byte-identical")
        normalized_receipt_sha256 = verify_conversion_receipt(
            arguments.conversion_receipt,
            receipt_payload,
            arguments.converter,
            converter_payload,
            arguments.contract,
            arguments.legacy_contract,
            arguments.legacy_network,
            arguments.container,
            container_payload,
            parsed,
        )
        negative_count, negative_manifest_sha256 = verify_negative_matrix(
            container_payload, legacy_payload, productive_payload, expected, arguments.container
        )
        try:
            golden = json.loads(golden_payload)
        except json.JSONDecodeError as error:
            raise VerificationError("frozen raw oracle is invalid JSON") from error
        rows, python_protocol_sha256, cpp_protocol_sha256, cpp_loader_negatives, cpp_eval_negatives = (
            verify_oracles(
                parsed,
                arguments.cpp_evaluator,
                arguments.container,
                arguments.legacy_network,
                arguments.productive_v2_network,
                expected,
                golden,
            )
        )
        result = {
            "schema": "crazyhouse-nnue-v2-legacy-control-independent-result/v1",
            "status": "PASS_AUTHENTICATED_LEGACY_CONTROL_PARITY",
            "evidence_class": "E1_ENGINEERING",
            "project": "Crazyhouse-Stockfish",
            "variant": "crazyhouse",
            "source": {
                "commit": expected.source_commit,
                "tree": expected.source_tree,
                "clean": True,
            },
            "identities": {
                "converter": {"bytes": len(converter_payload), "sha256": converter_sha256},
                "container": {"bytes": len(container_payload), "sha256": parsed.file_sha256},
                "conversion_receipt": {
                    "bytes": len(receipt_payload),
                    "sha256": sha256_bytes(receipt_payload),
                    "normalized_sha256": normalized_receipt_sha256,
                },
                "legacy_network": {"bytes": LEGACY_BYTES, "sha256": LEGACY_SHA256},
                "productive_v2_network": {
                    "bytes": PRODUCTIVE_BYTES,
                    "sha256": PRODUCTIVE_SHA256,
                },
                "container_contract": {"bytes": CONTRACT_BYTES, "sha256": CONTRACT_SHA256},
                "legacy_contract": {
                    "bytes": LEGACY_CONTRACT_BYTES,
                    "sha256": LEGACY_CONTRACT_SHA256,
                },
                "golden": {"bytes": GOLDEN_BYTES, "sha256": GOLDEN_SHA256},
            },
            "container": {
                "payload_bytes": PAYLOAD_BYTES,
                "payload_sha256": parsed.payload_sha256,
                "header_crc32c": f"{parsed.header_crc32c:08x}",
                "parse_reserialize_byte_equal": True,
                "section_sha256": list(parsed.section_digests),
            },
            "verification": {
                "cases": len(rows),
                "perspectives": len(rows) * 2,
                "buckets_per_case": LAYER_STACKS,
                "raw_component_pairs": len(rows) * LAYER_STACKS,
                "independent_python_negative_count": negative_count,
                "independent_python_negative_manifest_sha256": negative_manifest_sha256,
                "cpp_loader_negative_count": cpp_loader_negatives,
                "cpp_evaluation_negative_count": cpp_eval_negatives,
                "python_trace_protocol_sha256": python_protocol_sha256,
                "cpp_protocol_sha256": cpp_protocol_sha256,
                "python_replay_byte_equal": True,
                "cpp_replay_byte_equal": True,
                "python_cpp_full_trace_sha256_equal": True,
                "registered_legacy_raw_oracle_equal": True,
                "rows": rows,
            },
            "claims": {
                "legacy_control_proven_for_frozen_matrix": True,
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
        output_payload = canonical_json(result)
        require(not arguments.output.exists(), "independent result output already exists")
        write_exclusive(arguments.output, output_payload)
        require(arguments.output.read_bytes() == output_payload,
                "persisted independent result byte mismatch")
        print(
            "PASS crazyhouse_v2_legacy_control_verify "
            f"cases={len(rows)} raw_pairs={len(rows) * LAYER_STACKS} "
            f"python_negatives={negative_count} cpp_negatives={cpp_loader_negatives} "
            f"container_sha256={parsed.file_sha256} result_sha256={sha256_bytes(output_payload)}"
        )
        return 0
    except (VerificationError, OSError, KeyError, TypeError, ValueError, struct.error) as error:
        print(f"FAIL crazyhouse_v2_legacy_control_verify: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
