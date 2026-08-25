#!/usr/bin/env python3
"""Independent trainer-side reference for the Crazyhouse V2 scalar probe.

This module deliberately does not import the production physical codec, C++
bindings, or engine code. It authenticates the physical bytes needed by the
probe, enumerates the frozen sparse rows, and owns its container arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Sequence


RECORD_BYTES = 256
POSITION_DOMAIN = b"Crazyhouse-Stockfish physical repetition identity v1\0"
NO_SQUARE = 255
POCKET_MAXIMUMS = (16, 4, 4, 4, 2, 16, 4, 4, 4, 2)
POCKET_TYPE_BASE = (0, 34, 44, 54, 64)
POCKET_WIDTHS = (17, 5, 5, 5, 3)

FEATURE_DIMENSIONS = 902
MAXIMUM_ACTIVE = 138
OUTPUT_LANES = 17
HEADER_BYTES = 256
WEIGHTS_OFFSET = 256
WEIGHT_ELEMENTS = FEATURE_DIMENSIONS * OUTPUT_LANES
WEIGHTS_BYTES = WEIGHT_ELEMENTS * 2
BIASES_OFFSET = WEIGHTS_OFFSET + WEIGHTS_BYTES
BIASES_BYTES = OUTPUT_LANES * 4
PAYLOAD_BYTES = WEIGHTS_BYTES + BIASES_BYTES
FILE_BYTES = HEADER_BYTES + PAYLOAD_BYTES

MAGIC = bytes.fromhex("43484e4e554556325245463100000000")
BYTE_ORDER_MARKER = 0x01020304
VERSION_MAJOR = 1
VERSION_MINOR = 0
COMMITTED_FLAG = 1
INPUT_SEMANTICS = 1
WEIGHT_TYPE = 1
BIAS_TYPE = 2
ACCUMULATOR_TYPE = 2

RULE_PROFILE_SHA256 = bytes.fromhex(
    "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
)
PHYSICAL_SCHEMA_SHA256 = bytes.fromhex(
    "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
)
FEATURE_CONTRACT_SHA256 = bytes.fromhex(
    "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6"
)
ARCHITECTURE_SHA256 = bytes.fromhex(
    "e71d819a1d568979ec4fe99b6a004359768c31f618c91da7a309386f3bf732bb"
)


class ReferenceError(RuntimeError):
    """Base class for fail-closed reference errors."""


class PhysicalRecordError(ReferenceError):
    """The supplied physical record failed independent authentication."""


class ProbeFormatError(ReferenceError):
    """The supplied scalar probe container failed with a stable error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalState:
    board: tuple[int, ...]
    promoted_mask: int
    pockets: tuple[int, ...]
    side_to_move: int
    castling_rights: int
    effective_en_passant_square: int
    position_identity_sha256: bytes


@dataclass(frozen=True)
class ScalarProbeNetwork:
    weights: tuple[int, ...]
    biases: tuple[int, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReferenceError(message)


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _packed_board(board: Sequence[int]) -> bytes:
    if len(board) != 64:
        raise PhysicalRecordError("board width")
    output = bytearray(32)
    for square, code in enumerate(board):
        if code not in {0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14}:
            raise PhysicalRecordError("piece code")
        output[square // 2] |= code << (4 * (square & 1))
    return bytes(output)


def position_identity(
    board: Sequence[int],
    side_to_move: int,
    castling_rights: int,
    effective_en_passant_square: int,
    pockets: Sequence[int],
    promoted_mask: int,
) -> bytes:
    return hashlib.sha256(
        POSITION_DOMAIN
        + _packed_board(board)
        + bytes((side_to_move, castling_rights, effective_en_passant_square))
        + bytes(pockets)
        + struct.pack("<Q", promoted_mask)
    ).digest()


def decode_physical_record(payload: bytes) -> PhysicalState:
    """Independently authenticate framing, CRC, physical state, and identity."""

    if len(payload) != RECORD_BYTES:
        raise PhysicalRecordError("record size")
    if payload[:4] != b"CHR1":
        raise PhysicalRecordError("record magic")
    if struct.unpack_from("<HH", payload, 4) != (1, RECORD_BYTES):
        raise PhysicalRecordError("record version")
    if payload[245:252] != bytes(7):
        raise PhysicalRecordError("record reserved bytes")
    if struct.unpack_from("<I", payload, 252)[0] != crc32c(payload[:252]):
        raise PhysicalRecordError("record CRC32C")
    if payload[16:32] == bytes(16) or payload[32:48] == bytes(16):
        raise PhysicalRecordError("zero record identity")

    ply, flags = struct.unpack_from("<II", payload, 48)
    if flags & ~0x7F or bool(flags & (1 << 5)) != (ply == 0):
        raise PhysicalRecordError("record flags")

    board: list[int] = []
    for value in payload[56:88]:
        board.extend((value & 0x0F, value >> 4))
    allowed = {0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14}
    if any(code not in allowed for code in board):
        raise PhysicalRecordError("piece code")
    if sum(code == 6 for code in board) != 1 or sum(code == 14 for code in board) != 1:
        raise PhysicalRecordError("king count")
    if any((code & 7) == 1 and square // 8 in {0, 7} for square, code in enumerate(board)):
        raise PhysicalRecordError("pawn promotion rank")

    promoted_mask = struct.unpack_from("<Q", payload, 88)[0]
    occupied_mask = sum((1 << square) for square, code in enumerate(board) if code)
    forbidden_mask = sum(
        (1 << square)
        for square, code in enumerate(board)
        if code and (code & 7) in {1, 6}
    )
    if promoted_mask & ~occupied_mask or promoted_mask & forbidden_mask:
        raise PhysicalRecordError("promoted mask")

    pockets = tuple(payload[96:106])
    if any(value > maximum for value, maximum in zip(pockets, POCKET_MAXIMUMS)):
        raise PhysicalRecordError("pocket bounds")
    side_to_move = payload[106]
    castling_rights = payload[107]
    raw_ep = payload[108]
    repetition = payload[109]
    claim_policy = payload[110]
    terminal_reason = payload[111]
    effective_ep = payload[244]
    if side_to_move not in {0, 1}:
        raise PhysicalRecordError("side to move")
    if castling_rights & ~0x0F:
        raise PhysicalRecordError("castling rights")
    if raw_ep not in range(64) and raw_ep != NO_SQUARE:
        raise PhysicalRecordError("raw en passant")
    if effective_ep not in {NO_SQUARE, raw_ep}:
        raise PhysicalRecordError("effective en passant")
    if repetition not in range(1, 6) or claim_policy not in {0, 1}:
        raise PhysicalRecordError("history scalar")
    if terminal_reason not in range(7):
        raise PhysicalRecordError("terminal reason")
    if struct.unpack_from("<I", payload, 116)[0] == 0:
        raise PhysicalRecordError("fullmove number")
    if payload[148:180] == bytes(32) or payload[180:212] == bytes(32) or payload[212:244] == bytes(32):
        raise PhysicalRecordError("zero digest")

    expected_identity = position_identity(
        board, side_to_move, castling_rights, effective_ep, pockets, promoted_mask
    )
    if payload[148:180] != expected_identity:
        raise PhysicalRecordError("position identity")
    return PhysicalState(
        board=tuple(board),
        promoted_mask=promoted_mask,
        pockets=pockets,
        side_to_move=side_to_move,
        castling_rights=castling_rights,
        effective_en_passant_square=effective_ep,
        position_identity_sha256=expected_identity,
    )


def feature_rows(state: PhysicalState, perspective: int) -> tuple[int, ...]:
    if perspective not in {0, 1}:
        raise ReferenceError("perspective")
    rows: list[int] = []
    for square, code in enumerate(state.board):
        if code == 0:
            continue
        piece_type = code & 7
        owner = code >> 3
        oriented_square = square if perspective == 0 else square ^ 56
        plane = 2 * (piece_type - 1) + int(owner != perspective)
        rows.append(plane * 64 + oriented_square)
    for piece_type in range(5):
        for relative_owner in range(2):
            absolute_owner = perspective ^ relative_owner
            count = state.pockets[absolute_owner * 5 + piece_type]
            rows.append(
                768
                + POCKET_TYPE_BASE[piece_type]
                + relative_owner * POCKET_WIDTHS[piece_type]
                + count
            )
    for square in range(64):
        if state.promoted_mask & (1 << square):
            rows.append(838 + (square if perspective == 0 else square ^ 56))
    if len(rows) > MAXIMUM_ACTIVE:
        raise ReferenceError("active feature overflow")
    if len(rows) != len(set(rows)):
        raise ReferenceError("duplicate feature row")
    if any(row not in range(FEATURE_DIMENSIONS) for row in rows):
        raise ReferenceError("feature row out of range")
    return tuple(rows)


def synthetic_network() -> ScalarProbeNetwork:
    weights = tuple(
        ((row * 131 + lane * 17 + 23) % 257) - 128
        for row in range(FEATURE_DIMENSIONS)
        for lane in range(OUTPUT_LANES)
    )
    biases = tuple(((lane * 1009 + 7) % 2001) - 1000 for lane in range(OUTPUT_LANES))
    return ScalarProbeNetwork(weights=weights, biases=biases)


def serialize_network(network: ScalarProbeNetwork) -> bytes:
    if len(network.weights) != WEIGHT_ELEMENTS or len(network.biases) != OUTPUT_LANES:
        raise ReferenceError("network tensor shape")
    if any(value < -32768 or value > 32767 for value in network.weights):
        raise ReferenceError("weight type range")
    if any(value < -2147483648 or value > 2147483647 for value in network.biases):
        raise ReferenceError("bias type range")
    payload = struct.pack(f"<{WEIGHT_ELEMENTS}h", *network.weights) + struct.pack(
        f"<{OUTPUT_LANES}i", *network.biases
    )
    _require(len(payload) == PAYLOAD_BYTES, "payload width")
    header = bytearray(HEADER_BYTES)
    header[:16] = MAGIC
    struct.pack_into(
        "<IHHHHI",
        header,
        16,
        BYTE_ORDER_MARKER,
        HEADER_BYTES,
        VERSION_MAJOR,
        VERSION_MINOR,
        COMMITTED_FLAG,
        FILE_BYTES,
    )
    struct.pack_into(
        "<IIIHHHHIIIII",
        header,
        32,
        FEATURE_DIMENSIONS,
        MAXIMUM_ACTIVE,
        OUTPUT_LANES,
        INPUT_SEMANTICS,
        WEIGHT_TYPE,
        BIAS_TYPE,
        ACCUMULATOR_TYPE,
        WEIGHTS_OFFSET,
        WEIGHTS_BYTES,
        BIASES_OFFSET,
        BIASES_BYTES,
        PAYLOAD_BYTES,
    )
    header[80:112] = RULE_PROFILE_SHA256
    header[112:144] = PHYSICAL_SCHEMA_SHA256
    header[144:176] = FEATURE_CONTRACT_SHA256
    header[176:208] = ARCHITECTURE_SHA256
    header[208:240] = hashlib.sha256(payload).digest()
    struct.pack_into("<I", header, 252, crc32c(header[:252]))
    output = bytes(header) + payload
    _require(len(output) == FILE_BYTES, "file width")
    return output


def _reject(code: str) -> None:
    raise ProbeFormatError(code)


def parse_network(payload: bytes) -> ScalarProbeNetwork:
    if len(payload) != FILE_BYTES:
        _reject("WRONG_SIZE")
    if payload[:16] != MAGIC:
        _reject("MAGIC")
    if struct.unpack_from("<I", payload, 16)[0] != BYTE_ORDER_MARKER:
        _reject("BYTE_ORDER")
    if struct.unpack_from("<H", payload, 20)[0] != HEADER_BYTES:
        _reject("HEADER_SIZE")
    if struct.unpack_from("<HH", payload, 22) != (VERSION_MAJOR, VERSION_MINOR):
        _reject("VERSION")
    if struct.unpack_from("<H", payload, 26)[0] != COMMITTED_FLAG:
        _reject("FLAGS")
    if struct.unpack_from("<I", payload, 28)[0] != FILE_BYTES:
        _reject("FILE_SIZE")
    if struct.unpack_from("<I", payload, 32)[0] != FEATURE_DIMENSIONS:
        _reject("FEATURE_DIMENSIONS")
    if struct.unpack_from("<I", payload, 36)[0] != MAXIMUM_ACTIVE:
        _reject("MAXIMUM_ACTIVE")
    if struct.unpack_from("<I", payload, 40)[0] != OUTPUT_LANES:
        _reject("OUTPUT_LANES")
    if struct.unpack_from("<H", payload, 44)[0] != INPUT_SEMANTICS:
        _reject("INPUT_SEMANTICS")
    if struct.unpack_from("<H", payload, 46)[0] != WEIGHT_TYPE:
        _reject("WEIGHT_TYPE")
    if struct.unpack_from("<H", payload, 48)[0] != BIAS_TYPE:
        _reject("BIAS_TYPE")
    if struct.unpack_from("<H", payload, 50)[0] != ACCUMULATOR_TYPE:
        _reject("ACCUMULATOR_TYPE")
    if struct.unpack_from("<I", payload, 52)[0] != WEIGHTS_OFFSET:
        _reject("WEIGHTS_OFFSET")
    if struct.unpack_from("<I", payload, 56)[0] != WEIGHTS_BYTES:
        _reject("WEIGHTS_BYTES")
    if struct.unpack_from("<I", payload, 60)[0] != BIASES_OFFSET:
        _reject("BIASES_OFFSET")
    if struct.unpack_from("<I", payload, 64)[0] != BIASES_BYTES:
        _reject("BIASES_BYTES")
    if struct.unpack_from("<I", payload, 68)[0] != PAYLOAD_BYTES:
        _reject("PAYLOAD_BYTES")
    if payload[72:80] != bytes(8) or payload[240:252] != bytes(12):
        _reject("RESERVED_BYTES")
    if payload[80:112] != RULE_PROFILE_SHA256:
        _reject("RULE_PROFILE_IDENTITY")
    if payload[112:144] != PHYSICAL_SCHEMA_SHA256:
        _reject("PHYSICAL_SCHEMA_IDENTITY")
    if payload[144:176] != FEATURE_CONTRACT_SHA256:
        _reject("FEATURE_CONTRACT_IDENTITY")
    if payload[176:208] != ARCHITECTURE_SHA256:
        _reject("ARCHITECTURE_IDENTITY")
    if struct.unpack_from("<I", payload, 252)[0] != crc32c(payload[:252]):
        _reject("HEADER_CRC32C")
    if payload[208:240] != hashlib.sha256(payload[HEADER_BYTES:]).digest():
        _reject("PAYLOAD_SHA256")
    weights = struct.unpack_from(f"<{WEIGHT_ELEMENTS}h", payload, WEIGHTS_OFFSET)
    biases = struct.unpack_from(f"<{OUTPUT_LANES}i", payload, BIASES_OFFSET)
    return ScalarProbeNetwork(weights=tuple(weights), biases=tuple(biases))


def evaluate(network: ScalarProbeNetwork, rows: Sequence[int]) -> tuple[int, ...]:
    if len(rows) > MAXIMUM_ACTIVE:
        raise ReferenceError("active feature overflow")
    if len(rows) != len(set(rows)):
        raise ReferenceError("duplicate feature row")
    if any(row not in range(FEATURE_DIMENSIONS) for row in rows):
        raise ReferenceError("feature row out of range")
    accumulator = list(network.biases)
    for row in rows:
        base = row * OUTPUT_LANES
        for lane in range(OUTPUT_LANES):
            accumulator[lane] += network.weights[base + lane]
    if any(value < -2147483648 or value > 2147483647 for value in accumulator):
        raise ReferenceError("int32 accumulator overflow")
    return tuple(accumulator)


def synthetic_network_bytes() -> bytes:
    return serialize_network(synthetic_network())


__all__ = [
    "ARCHITECTURE_SHA256",
    "BIASES_OFFSET",
    "FILE_BYTES",
    "FEATURE_DIMENSIONS",
    "HEADER_BYTES",
    "MAXIMUM_ACTIVE",
    "OUTPUT_LANES",
    "PAYLOAD_BYTES",
    "ProbeFormatError",
    "ReferenceError",
    "ScalarProbeNetwork",
    "WEIGHTS_OFFSET",
    "crc32c",
    "decode_physical_record",
    "evaluate",
    "feature_rows",
    "parse_network",
    "serialize_network",
    "synthetic_network",
    "synthetic_network_bytes",
]
