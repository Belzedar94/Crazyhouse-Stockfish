#!/usr/bin/env python3
"""Independent integer reference for the productive Crazyhouse NNUE V2 format.

The module does not import the engine, its C++ codec, or PyTorch.  It owns the
fixed container, quantization, physical-state projection used by the
engineering micro-fit, static interval proofs, and scalar layer trace.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import math
import struct
import sys
from typing import Sequence


RECORD_BYTES = 256
NO_SQUARE = 255
POSITION_DOMAIN = b"Crazyhouse-Stockfish physical repetition identity v1\0"
MICROFIT_TARGET_DOMAIN = b"Crazyhouse-Stockfish NNUE V2 engineering microfit target v1\0"

FEATURE_DIMENSIONS = 902
MAXIMUM_ACTIVE = 138
TRANSFORMER_LANES = 512
PERSPECTIVE_COUNT = 2
DENSE0_INPUTS = 1024
DENSE0_OUTPUTS = 32
DENSE1_INPUTS = 32
DENSE1_OUTPUTS = 32
OUTPUT_INPUTS = 32
OUTPUT_OUTPUTS = 1

HEADER_BYTES = 512
PAYLOAD_BYTES = 959_812
FILE_BYTES = 960_324
MAGIC = bytes.fromhex("43484e4e5545563250524f4431000000")
BYTE_ORDER_MARKER = 0x01020304
VERSION_MAJOR = 1
VERSION_MINOR = 0
COMMITTED_FLAG = 1

TRANSFORMER_SCALE = 127
DENSE_WEIGHT_SCALE = 64
OUTPUT_WEIGHT_SCALE = 64
OUTPUT_VALUE_SCALE_CP = 600
DENSE_ACTIVATION_DIVISOR = 64
OUTPUT_DIVISOR = 8128

RULE_PROFILE_SHA256 = bytes.fromhex(
    "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
)
PHYSICAL_SCHEMA_SHA256 = bytes.fromhex(
    "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
)
FEATURE_CONTRACT_SHA256 = bytes.fromhex(
    "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6"
)
ARCHITECTURE_CONTRACT_SHA256 = bytes.fromhex(
    "76ebf73988d21fdd3dbf3c34420be0abe6a587419c9f170c16fa3acde4c112b6"
)
QUANTIZATION_CONTRACT_SHA256 = bytes.fromhex(
    "0a9d811ce76509ab58c1eec02fd87cef9df3804d76eb2fe2ae156183b23311a3"
)

TENSOR_LAYOUT = (
    ("transformer_weights", 512, 923_648, "h", FEATURE_DIMENSIONS * TRANSFORMER_LANES),
    ("transformer_biases", 924_160, 2_048, "i", TRANSFORMER_LANES),
    ("dense0_weights", 926_208, 32_768, "b", DENSE0_OUTPUTS * DENSE0_INPUTS),
    ("dense0_biases", 958_976, 128, "i", DENSE0_OUTPUTS),
    ("dense1_weights", 959_104, 1_024, "b", DENSE1_OUTPUTS * DENSE1_INPUTS),
    ("dense1_biases", 960_128, 128, "i", DENSE1_OUTPUTS),
    ("output_weights", 960_256, 64, "h", OUTPUT_INPUTS),
    ("output_bias", 960_320, 4, "i", 1),
)

POCKET_MAXIMUMS = (16, 4, 4, 4, 2, 16, 4, 4, 4, 2)
POCKET_TYPE_BASE = (0, 34, 44, 54, 64)
POCKET_WIDTHS = (17, 5, 5, 5, 3)

INT8_MIN, INT8_MAX = -(1 << 7), (1 << 7) - 1
INT16_MIN, INT16_MAX = -(1 << 15), (1 << 15) - 1
INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1


class ProductiveReferenceError(RuntimeError):
    """Base class for stable fail-closed reference errors."""


class ProductiveFormatError(ProductiveReferenceError):
    """The supplied container failed with a stable parser code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProductiveExportError(ProductiveReferenceError):
    """A float or integer network could not be exported."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class MicrofitRecordError(ProductiveReferenceError):
    """A golden physical state failed the label-blind micro-fit projection."""


@dataclass(frozen=True)
class ExpectedProvenance:
    dataset_manifest_sha256: bytes
    training_config_sha256: bytes


@dataclass(frozen=True)
class MicrofitState:
    board: tuple[int, ...]
    promoted_mask: int
    pockets: tuple[int, ...]
    side_to_move: int
    castling_rights: int
    effective_en_passant_square: int
    position_identity_sha256: bytes


@dataclass
class QuantizedNetwork:
    transformer_weights: Sequence[int]
    transformer_biases: Sequence[int]
    dense0_weights: Sequence[int]
    dense0_biases: Sequence[int]
    dense1_weights: Sequence[int]
    dense1_biases: Sequence[int]
    output_weights: Sequence[int]
    output_bias: Sequence[int]
    provenance: ExpectedProvenance


@dataclass
class FloatNetwork:
    transformer_weights: Sequence[float]
    transformer_biases: Sequence[float]
    dense0_weights: Sequence[float]
    dense0_biases: Sequence[float]
    dense1_weights: Sequence[float]
    dense1_biases: Sequence[float]
    output_weights: Sequence[float]
    output_bias: Sequence[float]
    provenance: ExpectedProvenance


@dataclass(frozen=True)
class ProductiveTrace:
    transformer_stm: tuple[int, ...]
    transformer_opponent: tuple[int, ...]
    transformer_stm_activation: tuple[int, ...]
    transformer_opponent_activation: tuple[int, ...]
    dense0: tuple[int, ...]
    dense0_activation: tuple[int, ...]
    dense1: tuple[int, ...]
    dense1_activation: tuple[int, ...]
    output_raw: int
    output_centipawns: int


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _require_digest(value: bytes, code: str, *, nonzero: bool = True) -> None:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ProductiveReferenceError(code)
    if nonzero and value == bytes(32):
        raise ProductiveReferenceError(code)


def _packed_board(board: Sequence[int]) -> bytes:
    if len(board) != 64:
        raise MicrofitRecordError("BOARD_WIDTH")
    output = bytearray(32)
    allowed = {0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14}
    for square, code in enumerate(board):
        if code not in allowed:
            raise MicrofitRecordError("PIECE_CODE")
        output[square // 2] |= code << (4 * (square & 1))
    return bytes(output)


def physical_position_identity(state: MicrofitState) -> bytes:
    return hashlib.sha256(
        POSITION_DOMAIN
        + _packed_board(state.board)
        + bytes(
            (
                state.side_to_move,
                state.castling_rights,
                state.effective_en_passant_square,
            )
        )
        + bytes(state.pockets)
        + struct.pack("<Q", state.promoted_mask)
    ).digest()


def decode_microfit_state(payload: bytes, expected_record_sha256: str) -> MicrofitState:
    """Authenticate and project only state bytes; label fields are never decoded."""

    if len(payload) != RECORD_BYTES:
        raise MicrofitRecordError("RECORD_SIZE")
    if hashlib.sha256(payload).hexdigest() != expected_record_sha256:
        raise MicrofitRecordError("RECORD_SHA256")
    if payload[:4] != b"CHR1" or struct.unpack_from("<HH", payload, 4) != (1, RECORD_BYTES):
        raise MicrofitRecordError("RECORD_FRAMING")
    if payload[245:252] != bytes(7):
        raise MicrofitRecordError("RESERVED_BYTES")
    if struct.unpack_from("<I", payload, 252)[0] != crc32c(payload[:252]):
        raise MicrofitRecordError("CRC32C")

    board: list[int] = []
    for value in payload[56:88]:
        board.extend((value & 0x0F, value >> 4))
    packed = _packed_board(board)
    if sum(code == 6 for code in board) != 1 or sum(code == 14 for code in board) != 1:
        raise MicrofitRecordError("KING_COUNT")
    if any((code & 7) == 1 and square // 8 in {0, 7} for square, code in enumerate(board)):
        raise MicrofitRecordError("PAWN_PROMOTION_RANK")

    promoted_mask = struct.unpack_from("<Q", payload, 88)[0]
    occupied = sum(1 << square for square, code in enumerate(board) if code)
    forbidden = sum(
        1 << square
        for square, code in enumerate(board)
        if code and (code & 7) in {1, 6}
    )
    if promoted_mask & ~occupied or promoted_mask & forbidden:
        raise MicrofitRecordError("PROMOTED_MASK")
    pockets = tuple(payload[96:106])
    if any(value > maximum for value, maximum in zip(pockets, POCKET_MAXIMUMS)):
        raise MicrofitRecordError("POCKET_BOUNDS")
    side_to_move = payload[106]
    castling_rights = payload[107]
    effective_ep = payload[244]
    if side_to_move not in {0, 1}:
        raise MicrofitRecordError("SIDE_TO_MOVE")
    if castling_rights & ~0x0F:
        raise MicrofitRecordError("CASTLING_RIGHTS")
    if effective_ep not in range(64) and effective_ep != NO_SQUARE:
        raise MicrofitRecordError("EFFECTIVE_EN_PASSANT")
    expected_identity = hashlib.sha256(
        POSITION_DOMAIN
        + packed
        + bytes((side_to_move, castling_rights, effective_ep))
        + bytes(pockets)
        + struct.pack("<Q", promoted_mask)
    ).digest()
    if payload[148:180] != expected_identity:
        raise MicrofitRecordError("POSITION_IDENTITY")
    return MicrofitState(
        board=tuple(board),
        promoted_mask=promoted_mask,
        pockets=pockets,
        side_to_move=side_to_move,
        castling_rights=castling_rights,
        effective_en_passant_square=effective_ep,
        position_identity_sha256=expected_identity,
    )


def feature_rows(state: MicrofitState, perspective: int) -> tuple[int, ...]:
    if perspective not in {0, 1}:
        raise ProductiveReferenceError("PERSPECTIVE")
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
    _validate_rows(rows)
    return tuple(rows)


def engineering_target(position_identity_sha256: bytes) -> float:
    _require_digest(position_identity_sha256, "POSITION_IDENTITY", nonzero=False)
    digest = hashlib.sha256(MICROFIT_TARGET_DOMAIN + position_identity_sha256).digest()
    return ((struct.unpack_from("<I", digest)[0] % 1201) - 600) / 600.0


def _validate_rows(rows: Sequence[int]) -> None:
    if len(rows) > MAXIMUM_ACTIVE:
        raise ProductiveReferenceError("ACTIVE_OVERFLOW")
    if len(rows) != len(set(rows)):
        raise ProductiveReferenceError("DUPLICATE_FEATURE")
    if any(row < 0 or row >= FEATURE_DIMENSIONS for row in rows):
        raise ProductiveReferenceError("FEATURE_INDEX")


def _tensor_values(network: QuantizedNetwork) -> tuple[Sequence[int], ...]:
    return (
        network.transformer_weights,
        network.transformer_biases,
        network.dense0_weights,
        network.dense0_biases,
        network.dense1_weights,
        network.dense1_biases,
        network.output_weights,
        network.output_bias,
    )


def _validate_shapes_and_ranges(network: QuantizedNetwork) -> None:
    values = _tensor_values(network)
    bounds = (
        (INT16_MIN, INT16_MAX),
        (INT32_MIN, INT32_MAX),
        (INT8_MIN, INT8_MAX),
        (INT32_MIN, INT32_MAX),
        (INT8_MIN, INT8_MAX),
        (INT32_MIN, INT32_MAX),
        (INT16_MIN, INT16_MAX),
        (INT32_MIN, INT32_MAX),
    )
    for (name, _offset, _bytes, _typecode, count), tensor, (lower, upper) in zip(
        TENSOR_LAYOUT, values, bounds
    ):
        if len(tensor) != count:
            raise ProductiveExportError(f"{name.upper()}_SHAPE")
        if any(not isinstance(value, int) or value < lower or value > upper for value in tensor):
            raise ProductiveExportError(f"{name.upper()}_RANGE")
    _require_digest(network.provenance.dataset_manifest_sha256, "DATASET_IDENTITY")
    _require_digest(network.provenance.training_config_sha256, "TRAINING_CONFIG_IDENTITY")
    _validate_static_intervals(network, ProductiveExportError)


def _validate_static_intervals(network: QuantizedNetwork, error_type: type[Exception]) -> None:
    for lane in range(TRANSFORMER_LANES):
        column = sorted(
            network.transformer_weights[row * TRANSFORMER_LANES + lane]
            for row in range(FEATURE_DIMENSIONS)
        )
        lower = network.transformer_biases[lane] + sum(
            value for value in column[:MAXIMUM_ACTIVE] if value < 0
        )
        upper = network.transformer_biases[lane] + sum(
            value for value in reversed(column[-MAXIMUM_ACTIVE:]) if value > 0
        )
        if lower < INT32_MIN or upper > INT32_MAX:
            raise error_type("TRANSFORMER_INTERVAL")

    for name, weights, biases, inputs, outputs in (
        ("DENSE0_INTERVAL", network.dense0_weights, network.dense0_biases, 1024, 32),
        ("DENSE1_INTERVAL", network.dense1_weights, network.dense1_biases, 32, 32),
        ("OUTPUT_INTERVAL", network.output_weights, network.output_bias, 32, 1),
    ):
        for output in range(outputs):
            row = weights[output * inputs : (output + 1) * inputs]
            lower = biases[output] + 127 * sum(value for value in row if value < 0)
            upper = biases[output] + 127 * sum(value for value in row if value > 0)
            if lower < INT32_MIN or upper > INT32_MAX:
                raise error_type(name)


def _pack_array(typecode: str, values: Sequence[int]) -> bytes:
    packed = array(typecode, values)
    expected_itemsize = {"b": 1, "h": 2, "i": 4}[typecode]
    if packed.itemsize != expected_itemsize:
        raise ProductiveReferenceError("HOST_ARRAY_WIDTH")
    if sys.byteorder != "little" and packed.itemsize > 1:
        packed.byteswap()
    return packed.tobytes()


def _unpack_array(typecode: str, payload: bytes) -> array:
    values = array(typecode)
    values.frombytes(payload)
    expected_itemsize = {"b": 1, "h": 2, "i": 4}[typecode]
    if values.itemsize != expected_itemsize:
        raise ProductiveReferenceError("HOST_ARRAY_WIDTH")
    if sys.byteorder != "little" and values.itemsize > 1:
        values.byteswap()
    return values


def serialize_network(network: QuantizedNetwork) -> bytes:
    _validate_shapes_and_ranges(network)
    payload_parts: list[bytes] = []
    for (_name, _offset, expected_bytes, typecode, _count), tensor in zip(
        TENSOR_LAYOUT, _tensor_values(network)
    ):
        packed = _pack_array(typecode, tensor)
        if len(packed) != expected_bytes:
            raise ProductiveReferenceError("PAYLOAD_LAYOUT")
        payload_parts.append(packed)
    payload = b"".join(payload_parts)
    if len(payload) != PAYLOAD_BYTES:
        raise ProductiveReferenceError("PAYLOAD_BYTES")

    header = bytearray(HEADER_BYTES)
    header[:16] = MAGIC
    struct.pack_into("<I", header, 16, BYTE_ORDER_MARKER)
    struct.pack_into("<H", header, 20, HEADER_BYTES)
    struct.pack_into("<HHH", header, 22, VERSION_MAJOR, VERSION_MINOR, COMMITTED_FLAG)
    struct.pack_into("<I", header, 28, FILE_BYTES)
    struct.pack_into(
        "<10I",
        header,
        32,
        FEATURE_DIMENSIONS,
        MAXIMUM_ACTIVE,
        TRANSFORMER_LANES,
        PERSPECTIVE_COUNT,
        DENSE0_INPUTS,
        DENSE0_OUTPUTS,
        DENSE1_INPUTS,
        DENSE1_OUTPUTS,
        OUTPUT_INPUTS,
        OUTPUT_OUTPUTS,
    )
    struct.pack_into("<8H", header, 72, 1, 2, 3, 2, 1, 2, 2, 1)
    struct.pack_into(
        "<6I",
        header,
        88,
        TRANSFORMER_SCALE,
        DENSE_WEIGHT_SCALE,
        OUTPUT_WEIGHT_SCALE,
        OUTPUT_VALUE_SCALE_CP,
        DENSE_ACTIVATION_DIVISOR,
        OUTPUT_DIVISOR,
    )
    for index, (_name, offset, width, _typecode, _count) in enumerate(TENSOR_LAYOUT):
        struct.pack_into("<II", header, 112 + index * 8, offset, width)
    struct.pack_into("<4I", header, 176, 1, 1, 1, 1)
    header[192:224] = RULE_PROFILE_SHA256
    header[224:256] = PHYSICAL_SCHEMA_SHA256
    header[256:288] = FEATURE_CONTRACT_SHA256
    header[288:320] = ARCHITECTURE_CONTRACT_SHA256
    header[320:352] = QUANTIZATION_CONTRACT_SHA256
    header[352:384] = network.provenance.dataset_manifest_sha256
    header[384:416] = network.provenance.training_config_sha256
    header[416:448] = hashlib.sha256(payload).digest()
    struct.pack_into("<I", header, 508, crc32c(bytes(header[:508])))
    output = bytes(header) + payload
    if len(output) != FILE_BYTES:
        raise ProductiveReferenceError("FILE_BYTES")
    return output


def _format_failure(code: str) -> None:
    raise ProductiveFormatError(code)


def _expect_u16(payload: bytes, offset: int, expected: int, code: str) -> None:
    if struct.unpack_from("<H", payload, offset)[0] != expected:
        _format_failure(code)


def _expect_u32(payload: bytes, offset: int, expected: int, code: str) -> None:
    if struct.unpack_from("<I", payload, offset)[0] != expected:
        _format_failure(code)


def parse_network(payload: bytes, expected: ExpectedProvenance) -> QuantizedNetwork:
    if (
        not isinstance(expected.dataset_manifest_sha256, bytes)
        or len(expected.dataset_manifest_sha256) != 32
        or expected.dataset_manifest_sha256 == bytes(32)
        or not isinstance(expected.training_config_sha256, bytes)
        or len(expected.training_config_sha256) != 32
        or expected.training_config_sha256 == bytes(32)
    ):
        _format_failure("EXPECTED_PROVENANCE")
    if len(payload) != FILE_BYTES:
        _format_failure("WRONG_SIZE")
    if payload[:16] != MAGIC:
        _format_failure("MAGIC")
    _expect_u32(payload, 16, BYTE_ORDER_MARKER, "BYTE_ORDER")
    _expect_u16(payload, 20, HEADER_BYTES, "HEADER_SIZE")
    if struct.unpack_from("<HH", payload, 22) != (VERSION_MAJOR, VERSION_MINOR):
        _format_failure("VERSION")
    _expect_u16(payload, 26, COMMITTED_FLAG, "FLAGS")
    _expect_u32(payload, 28, FILE_BYTES, "FILE_SIZE")
    for offset, value, code in (
        (32, FEATURE_DIMENSIONS, "FEATURE_DIMENSIONS"),
        (36, MAXIMUM_ACTIVE, "MAXIMUM_ACTIVE"),
        (40, TRANSFORMER_LANES, "TRANSFORMER_LANES"),
        (44, PERSPECTIVE_COUNT, "PERSPECTIVE_COUNT"),
        (48, DENSE0_INPUTS, "DENSE0_INPUTS"),
        (52, DENSE0_OUTPUTS, "DENSE0_OUTPUTS"),
        (56, DENSE1_INPUTS, "DENSE1_INPUTS"),
        (60, DENSE1_OUTPUTS, "DENSE1_OUTPUTS"),
        (64, OUTPUT_INPUTS, "OUTPUT_INPUTS"),
        (68, OUTPUT_OUTPUTS, "OUTPUT_OUTPUTS"),
    ):
        _expect_u32(payload, offset, value, code)
    for offset, value, code in (
        (72, 1, "TRANSFORMER_WEIGHT_TYPE"),
        (74, 2, "TRANSFORMER_BIAS_TYPE"),
        (76, 3, "DENSE_WEIGHT_TYPE"),
        (78, 2, "DENSE_BIAS_TYPE"),
        (80, 1, "OUTPUT_WEIGHT_TYPE"),
        (82, 2, "OUTPUT_BIAS_TYPE"),
        (84, 2, "ACCUMULATOR_TYPE"),
        (86, 1, "ACTIVATION_TYPE"),
    ):
        _expect_u16(payload, offset, value, code)
    for offset, value, code in (
        (88, TRANSFORMER_SCALE, "TRANSFORMER_SCALE"),
        (92, DENSE_WEIGHT_SCALE, "DENSE_WEIGHT_SCALE"),
        (96, OUTPUT_WEIGHT_SCALE, "OUTPUT_WEIGHT_SCALE"),
        (100, OUTPUT_VALUE_SCALE_CP, "OUTPUT_VALUE_SCALE"),
        (104, DENSE_ACTIVATION_DIVISOR, "DENSE_ACTIVATION_DIVISOR"),
        (108, OUTPUT_DIVISOR, "OUTPUT_DIVISOR"),
    ):
        _expect_u32(payload, offset, value, code)
    for index, (_name, offset, width, _typecode, _count) in enumerate(TENSOR_LAYOUT):
        if struct.unpack_from("<II", payload, 112 + index * 8) != (offset, width):
            _format_failure("TENSOR_DIRECTORY")
    for offset, value, code in (
        (176, 1, "INPUT_SEMANTICS"),
        (180, 1, "PERSPECTIVE_ORDER"),
        (184, 1, "ACTIVATION_SEMANTICS"),
        (188, 1, "OUTPUT_UNITS"),
    ):
        _expect_u32(payload, offset, value, code)
    for start, identity, code in (
        (192, RULE_PROFILE_SHA256, "RULE_PROFILE_IDENTITY"),
        (224, PHYSICAL_SCHEMA_SHA256, "PHYSICAL_SCHEMA_IDENTITY"),
        (256, FEATURE_CONTRACT_SHA256, "FEATURE_CONTRACT_IDENTITY"),
        (288, ARCHITECTURE_CONTRACT_SHA256, "ARCHITECTURE_IDENTITY"),
        (320, QUANTIZATION_CONTRACT_SHA256, "QUANTIZATION_IDENTITY"),
    ):
        if payload[start : start + 32] != identity:
            _format_failure(code)
    if payload[352:384] == bytes(32):
        _format_failure("DATASET_IDENTITY_ZERO")
    if payload[352:384] != expected.dataset_manifest_sha256:
        _format_failure("DATASET_IDENTITY")
    if payload[384:416] == bytes(32):
        _format_failure("TRAINING_CONFIG_IDENTITY_ZERO")
    if payload[384:416] != expected.training_config_sha256:
        _format_failure("TRAINING_CONFIG_IDENTITY")
    if payload[448:508] != bytes(60):
        _format_failure("RESERVED_BYTES")
    if struct.unpack_from("<I", payload, 508)[0] != crc32c(payload[:508]):
        _format_failure("HEADER_CRC32C")
    if payload[416:448] != hashlib.sha256(payload[HEADER_BYTES:]).digest():
        _format_failure("PAYLOAD_SHA256")

    tensors: list[array] = []
    for _name, offset, width, typecode, count in TENSOR_LAYOUT:
        tensor = _unpack_array(typecode, payload[offset : offset + width])
        if len(tensor) != count:
            _format_failure("TENSOR_FRAMING")
        tensors.append(tensor)
    network = QuantizedNetwork(
        transformer_weights=tensors[0],
        transformer_biases=tensors[1],
        dense0_weights=tensors[2],
        dense0_biases=tensors[3],
        dense1_weights=tensors[4],
        dense1_biases=tensors[5],
        output_weights=tensors[6],
        output_bias=tensors[7],
        provenance=expected,
    )
    try:
        _validate_static_intervals(network, ProductiveFormatError)
    except ProductiveFormatError:
        raise
    return network


def _float32(value: float) -> float:
    try:
        return struct.unpack("<f", struct.pack("<f", float(value)))[0]
    except (OverflowError, struct.error, TypeError, ValueError) as error:
        raise ProductiveExportError("FLOAT32_VALUE") from error


def _quantize_value(value: float, scale: int, lower: int, upper: int) -> int:
    widened = float(_float32(value))
    if not math.isfinite(widened):
        raise ProductiveExportError("NONFINITE")
    scaled = widened * scale
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else -math.floor(-scaled + 0.5)
    if rounded < lower or rounded > upper:
        raise ProductiveExportError("INTEGER_TYPE_RANGE")
    return int(rounded)


def quantize_network(network: FloatNetwork) -> QuantizedNetwork:
    specifications = (
        ("TRANSFORMER_WEIGHTS", network.transformer_weights, FEATURE_DIMENSIONS * TRANSFORMER_LANES, 127, INT16_MIN, INT16_MAX),
        ("TRANSFORMER_BIASES", network.transformer_biases, TRANSFORMER_LANES, 127, INT32_MIN, INT32_MAX),
        ("DENSE0_WEIGHTS", network.dense0_weights, DENSE0_OUTPUTS * DENSE0_INPUTS, 64, INT8_MIN, INT8_MAX),
        ("DENSE0_BIASES", network.dense0_biases, DENSE0_OUTPUTS, 8128, INT32_MIN, INT32_MAX),
        ("DENSE1_WEIGHTS", network.dense1_weights, DENSE1_OUTPUTS * DENSE1_INPUTS, 64, INT8_MIN, INT8_MAX),
        ("DENSE1_BIASES", network.dense1_biases, DENSE1_OUTPUTS, 8128, INT32_MIN, INT32_MAX),
        ("OUTPUT_WEIGHTS", network.output_weights, OUTPUT_INPUTS, 64, INT16_MIN, INT16_MAX),
        ("OUTPUT_BIAS", network.output_bias, 1, 8128, INT32_MIN, INT32_MAX),
    )
    output: list[array] = []
    typecodes = ("h", "i", "b", "i", "b", "i", "h", "i")
    for (name, values, count, scale, lower, upper), typecode in zip(specifications, typecodes):
        if len(values) != count:
            raise ProductiveExportError(f"{name}_SHAPE")
        output.append(array(typecode, (_quantize_value(value, scale, lower, upper) for value in values)))
    quantized = QuantizedNetwork(
        transformer_weights=output[0],
        transformer_biases=output[1],
        dense0_weights=output[2],
        dense0_biases=output[3],
        dense1_weights=output[4],
        dense1_biases=output[5],
        output_weights=output[6],
        output_bias=output[7],
        provenance=network.provenance,
    )
    _validate_shapes_and_ranges(quantized)
    return quantized


def export_network_file(path: str, network: FloatNetwork) -> bytes:
    """Validate fully, then create one final file with an atomic same-dir rename."""

    from pathlib import Path
    import os

    destination = Path(path)
    temporary = destination.with_name(destination.name + ".partial")
    if destination.exists() or temporary.exists():
        raise ProductiveExportError("OUTPUT_EXISTS")
    payload = serialize_network(quantize_network(network))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return payload


def _activation_transformer(value: int) -> int:
    return 0 if value <= 0 else 127 if value >= 127 else value


def _activation_dense(value: int) -> int:
    return 0 if value <= 0 else 127 if value >= OUTPUT_DIVISOR else value // 64


def _checked_int32(value: int, code: str) -> int:
    if value < INT32_MIN or value > INT32_MAX:
        raise ProductiveReferenceError(code)
    return value


def _transform(network: QuantizedNetwork, rows: Sequence[int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    _validate_rows(rows)
    raw = list(network.transformer_biases)
    for row in rows:
        base = row * TRANSFORMER_LANES
        for lane in range(TRANSFORMER_LANES):
            raw[lane] += network.transformer_weights[base + lane]
    checked = tuple(_checked_int32(value, "TRANSFORMER_RUNTIME_RANGE") for value in raw)
    return checked, tuple(_activation_transformer(value) for value in checked)


def _dense(
    inputs: Sequence[int],
    weights: Sequence[int],
    biases: Sequence[int],
    output_count: int,
    code: str,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    raw: list[int] = []
    for output in range(output_count):
        base = output * len(inputs)
        value = biases[output] + sum(
            inputs[index] * weights[base + index] for index in range(len(inputs))
        )
        raw.append(_checked_int32(value, code))
    checked = tuple(raw)
    return checked, tuple(_activation_dense(value) for value in checked)


def _truncate_toward_zero(numerator: int, denominator: int) -> int:
    quotient = abs(numerator) // denominator
    return -quotient if numerator < 0 else quotient


def evaluate(
    network: QuantizedNetwork,
    white_rows: Sequence[int],
    black_rows: Sequence[int],
    side_to_move: int,
) -> ProductiveTrace:
    if side_to_move not in {0, 1}:
        raise ProductiveReferenceError("SIDE_TO_MOVE")
    white_raw, white_activation = _transform(network, white_rows)
    black_raw, black_activation = _transform(network, black_rows)
    if side_to_move == 0:
        stm_raw, opponent_raw = white_raw, black_raw
        stm_activation, opponent_activation = white_activation, black_activation
    else:
        stm_raw, opponent_raw = black_raw, white_raw
        stm_activation, opponent_activation = black_activation, white_activation
    dense0_raw, dense0_activation = _dense(
        stm_activation + opponent_activation,
        network.dense0_weights,
        network.dense0_biases,
        DENSE0_OUTPUTS,
        "DENSE0_RUNTIME_RANGE",
    )
    dense1_raw, dense1_activation = _dense(
        dense0_activation,
        network.dense1_weights,
        network.dense1_biases,
        DENSE1_OUTPUTS,
        "DENSE1_RUNTIME_RANGE",
    )
    output_raw = _checked_int32(
        network.output_bias[0]
        + sum(
            dense1_activation[index] * network.output_weights[index]
            for index in range(OUTPUT_INPUTS)
        ),
        "OUTPUT_RUNTIME_RANGE",
    )
    output_cp = _truncate_toward_zero(output_raw * OUTPUT_VALUE_SCALE_CP, OUTPUT_DIVISOR)
    return ProductiveTrace(
        transformer_stm=stm_raw,
        transformer_opponent=opponent_raw,
        transformer_stm_activation=stm_activation,
        transformer_opponent_activation=opponent_activation,
        dense0=dense0_raw,
        dense0_activation=dense0_activation,
        dense1=dense1_raw,
        dense1_activation=dense1_activation,
        output_raw=output_raw,
        output_centipawns=output_cp,
    )


def synthetic_quantized_network(provenance: ExpectedProvenance) -> QuantizedNetwork:
    return QuantizedNetwork(
        transformer_weights=array(
            "h",
            (((row * 29 + lane * 17 + 11) % 15) - 7 for row in range(FEATURE_DIMENSIONS) for lane in range(TRANSFORMER_LANES)),
        ),
        transformer_biases=array("i", (((lane * 13 + 5) % 31) - 15 for lane in range(TRANSFORMER_LANES))),
        dense0_weights=array("b", (((output * 19 + lane * 7 + 3) % 11) - 5 for output in range(DENSE0_OUTPUTS) for lane in range(DENSE0_INPUTS))),
        dense0_biases=array("i", (((output * 101 + 17) % 1025) - 512 for output in range(DENSE0_OUTPUTS))),
        dense1_weights=array("b", (((output * 23 + lane * 5 + 1) % 13) - 6 for output in range(DENSE1_OUTPUTS) for lane in range(DENSE1_INPUTS))),
        dense1_biases=array("i", (((output * 79 + 9) % 513) - 256 for output in range(DENSE1_OUTPUTS))),
        output_weights=array("h", (((lane * 31 + 7) % 33) - 16 for lane in range(OUTPUT_INPUTS))),
        output_bias=array("i", [137]),
        provenance=provenance,
    )


__all__ = [
    "ARCHITECTURE_CONTRACT_SHA256",
    "FILE_BYTES",
    "FloatNetwork",
    "HEADER_BYTES",
    "MicrofitRecordError",
    "ProductiveExportError",
    "ProductiveFormatError",
    "ProductiveReferenceError",
    "ProductiveTrace",
    "QuantizedNetwork",
    "ExpectedProvenance",
    "TENSOR_LAYOUT",
    "crc32c",
    "decode_microfit_state",
    "engineering_target",
    "evaluate",
    "export_network_file",
    "feature_rows",
    "parse_network",
    "quantize_network",
    "serialize_network",
    "synthetic_quantized_network",
]
