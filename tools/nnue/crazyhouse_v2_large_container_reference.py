#!/usr/bin/env python3
"""Independent container writer and scalar reference for large Crazyhouse V2 A0."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Sequence


HEADER_BYTES = 1_024
PAYLOAD_BYTES = 126_405_664
FILE_BYTES = 126_406_688
K_DIMENSIONS = 81_664
G_DIMENSIONS = 1_340
K_LANES = 768
G_LANES = 256
MAXIMUM_ACTIVE = 48
PERSPECTIVE_BYTES = 512
DENSE_INPUTS = 1_024
LAYER_STACKS = 8
FC0_OUTPUTS = 32
FC1_INPUTS = 64
FC1_OUTPUTS = 32
FC2_INPUTS = 128
K_POCKET_OFFSET = 45_056
K_PROMOTED_OFFSET = 48_896
G_POCKET_OFFSET = 768
G_PROMOTED_OFFSET = 828

K_WEIGHT_OFFSET = 1_024
K_BIAS_OFFSET = 125_436_928
G_WEIGHT_OFFSET = 125_438_464
G_BIAS_OFFSET = 126_124_544
FC0_BIAS_OFFSET = 126_125_056
FC0_WEIGHT_OFFSET = 126_126_080
FC1_BIAS_OFFSET = 126_388_224
FC1_WEIGHT_OFFSET = 126_389_248
FC2_BIAS_OFFSET = 126_405_632
FC2_WEIGHT_OFFSET = 126_405_664

MAGIC = b"CHNNUEV2LARGEA0\0"
RULE_PROFILE = bytes.fromhex(
    "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
)
PHYSICAL_SCHEMA = bytes.fromhex(
    "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
)
FEATURE_CONTRACT = hashlib.sha256(
    b"crazyhouse-v2-large-k64g1-feature-contract-v1\n"
).digest()
ARCHITECTURE = hashlib.sha256(b"CH-NNUE-V2-LARGE-K64G1-SFNNV16\n").digest()
QUANTIZATION = hashlib.sha256(
    b"crazyhouse-v2-large-sfnnv16-quantization-v1\n"
).digest()
PROVENANCE = tuple(bytes((value,)) * 32 for value in (0x11, 0x22, 0x33, 0x44, 0x55, 0x66))

TENSOR_DIRECTORY = (
    (1, 1, 2, 0, 1_024, 125_435_904, (81_664, 768, 0, 0)),
    (2, 1, 1, 0, 125_436_928, 1_536, (768, 0, 0, 0)),
    (3, 1, 2, 0, 125_438_464, 686_080, (1_340, 256, 0, 0)),
    (4, 1, 1, 0, 126_124_544, 512, (256, 0, 0, 0)),
    (5, 2, 2, 0, 126_125_056, 1_024, (8, 32, 0, 0)),
    (6, 3, 3, 0, 126_126_080, 262_144, (8, 32, 1_024, 0)),
    (7, 2, 2, 0, 126_388_224, 1_024, (8, 32, 0, 0)),
    (8, 3, 3, 0, 126_389_248, 16_384, (8, 32, 64, 0)),
    (9, 2, 1, 0, 126_405_632, 32, (8, 0, 0, 0)),
    (10, 3, 2, 0, 126_405_664, 1_024, (8, 128, 0, 0)),
)


def crc32c(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _put_i8(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<b", blob, offset, value)


def _put_i16(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<h", blob, offset, value)


def _put_i32(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<i", blob, offset, value)


def _k_weight_offset(row: int, lane: int) -> int:
    return K_WEIGHT_OFFSET + (row * K_LANES + lane) * 2


def _g_weight_offset(row: int, lane: int) -> int:
    return G_WEIGHT_OFFSET + (row * G_LANES + lane) * 2


def _fc0_weight_offset(bucket: int, output: int, input_index: int) -> int:
    return FC0_WEIGHT_OFFSET + (bucket * FC0_OUTPUTS * DENSE_INPUTS
                                + output * DENSE_INPUTS + input_index)


def _fc1_weight_offset(bucket: int, output: int, input_index: int) -> int:
    return FC1_WEIGHT_OFFSET + (bucket * FC1_OUTPUTS * FC1_INPUTS
                                + output * FC1_INPUTS + input_index)


def _fc2_weight_offset(bucket: int, input_index: int) -> int:
    return FC2_WEIGHT_OFFSET + bucket * FC2_INPUTS + input_index


def finalize(blob: bytearray) -> None:
    blob[576:608] = hashlib.sha256(memoryview(blob)[HEADER_BYTES:]).digest()
    blob[608:612] = b"\0" * 4
    struct.pack_into("<I", blob, 608, crc32c(memoryview(blob)[:HEADER_BYTES]))


def build_fixture_container() -> bytearray:
    blob = bytearray(FILE_BYTES)
    blob[:16] = MAGIC
    struct.pack_into("<IHHHHIIHH", blob, 16, 0x01020304, HEADER_BYTES, 1, 0, 1,
                     FILE_BYTES, PAYLOAD_BYTES, len(TENSOR_DIRECTORY), LAYER_STACKS)
    for index, value in enumerate(
        (K_DIMENSIONS, G_DIMENSIONS, MAXIMUM_ACTIVE, K_LANES, G_LANES, 2,
         PERSPECTIVE_BYTES, DENSE_INPUTS, FC0_OUTPUTS, FC1_INPUTS, FC1_OUTPUTS,
         FC2_INPUTS, 1)
    ):
        struct.pack_into("<I", blob, 40 + index * 4, value)
    struct.pack_into("<II", blob, 92, 4, 7)
    for index, value in enumerate((1, 1, 1, 1, 3, 2, 2, 2)):
        struct.pack_into("<H", blob, 100 + index * 2, value)
    for offset, value in (
        (116, 255), (120, 512), (124, 6), (128, 128), (132, 16),
        (136, 7), (140, 6), (144, 7), (148, 9_600), (152, 16_384),
        (156, 1), (160, 1), (164, 624), (168, 40), (172, 1),
    ):
        struct.pack_into("<I", blob, offset, value)
    for offset, digest in zip((224, 256, 288, 320, 352),
                              (RULE_PROFILE, PHYSICAL_SCHEMA, FEATURE_CONTRACT,
                               ARCHITECTURE, QUANTIZATION)):
        blob[offset:offset + 32] = digest
    for offset, digest in zip((384, 416, 448, 480, 512, 544), PROVENANCE):
        blob[offset:offset + 32] = digest
    for index, (tensor_id, tensor_type, rank, flags, offset, size, dimensions) in enumerate(
        TENSOR_DIRECTORY
    ):
        struct.pack_into("<HHHHQQIIII", blob, 624 + index * 40, tensor_id, tensor_type,
                         rank, flags, offset, size, *dimensions)

    for lane in range(K_LANES):
        value = 128 + lane % 64 if lane < K_LANES // 2 else 192 - (lane - K_LANES // 2) % 64
        _put_i16(blob, K_BIAS_OFFSET + lane * 2, value)
    for lane in range(G_LANES):
        value = 80 + lane % 48 if lane < G_LANES // 2 else 224 - (lane - G_LANES // 2) % 48
        _put_i16(blob, G_BIAS_OFFSET + lane * 2, value)

    for row, scale in ((7, 1), (13, -1)):
        for lane, value in ((0, 23 * scale), (100, -9 * scale),
                            (K_LANES // 2, -17 * scale), (K_LANES // 2 + 100, 13 * scale)):
            _put_i16(blob, _k_weight_offset(row, lane), value)
    for row, scale in ((9, 1), (17, -1)):
        for lane, value in ((0, 19 * scale), (37, -7 * scale),
                            (G_LANES // 2, -11 * scale), (G_LANES // 2 + 37, 5 * scale)):
            _put_i16(blob, _g_weight_offset(row, lane), value)
    for perspective in range(2):
        for index in range(28):
            row = K_POCKET_OFFSET + perspective * 64 + index
            _put_i16(blob, _k_weight_offset(row, 1), 1 + perspective)
            _put_i16(blob, _k_weight_offset(row, K_LANES // 2 + 1), 2 - perspective)
    for index in range(28):
        row = G_POCKET_OFFSET + index
        _put_i16(blob, _g_weight_offset(row, 2), 1)
        _put_i16(blob, _g_weight_offset(row, G_LANES // 2 + 2), -1)

    for bucket in range(LAYER_STACKS):
        for output in range(FC0_OUTPUTS):
            _put_i32(blob, FC0_BIAS_OFFSET + (bucket * FC0_OUTPUTS + output) * 4,
                     (output - 16) * 16 + bucket * 8)
            value0 = (output + bucket) % 7 - 3
            value1 = (2 * output + bucket) % 5 - 2
            _put_i8(blob, _fc0_weight_offset(bucket, output,
                                             (output * 37 + bucket * 11) % DENSE_INPUTS), value0)
            _put_i8(blob, _fc0_weight_offset(bucket, output,
                                             512 + (output * 19 + bucket * 7) % 512), value1)
        for output in range(FC1_OUTPUTS):
            _put_i32(blob, FC1_BIAS_OFFSET + (bucket * FC1_OUTPUTS + output) * 4,
                     (output - 8) * 9 - bucket * 3)
            _put_i8(blob, _fc1_weight_offset(bucket, output, output % FC1_INPUTS),
                    (output + bucket) % 5 - 2)
            _put_i8(blob, _fc1_weight_offset(bucket, output, 32 + output % 32),
                    (3 * output + bucket) % 7 - 3)
        _put_i32(blob, FC2_BIAS_OFFSET + bucket * 4, bucket * 31 - 77)
        for input_index in range(FC2_INPUTS):
            if input_index % 17 == bucket:
                _put_i8(blob, _fc2_weight_offset(bucket, input_index),
                        (input_index + bucket) % 7 - 3)

    finalize(blob)
    return blob


def write_fixture_container(path: Path) -> None:
    blob = build_fixture_container()
    with path.open("wb") as output:
        output.write(blob)


def case_features(case_id: str) -> tuple[list[list[int]], list[list[int]], int]:
    if case_id == "empty":
        return [[], []], [[], []], 0
    if case_id == "one":
        return [[7], [13]], [[9], [17]], 0
    if case_id == "bucket7":
        return (
            [[K_POCKET_OFFSET + perspective * 64 + index for index in range(28)]
             for perspective in range(2)],
            [[G_POCKET_OFFSET + index for index in range(28)] for _ in range(2)],
            28,
        )
    raise ValueError(case_id)


def _i8(blob: bytes | bytearray | memoryview, offset: int) -> int:
    return struct.unpack_from("<b", blob, offset)[0]


def _i16(blob: bytes | bytearray | memoryview, offset: int) -> int:
    return struct.unpack_from("<h", blob, offset)[0]


def _i32(blob: bytes | bytearray | memoryview, offset: int) -> int:
    return struct.unpack_from("<i", blob, offset)[0]


def _pair(values: Sequence[int]) -> list[int]:
    half = len(values) // 2
    return [max(0, min(255, values[index])) * max(0, min(255, values[index + half])) // 512
            for index in range(half)]


def _squared(value: int, shift: int) -> int:
    return min(127, value * value >> (2 * shift + 7))


def _clipped(value: int, shift: int) -> int:
    return 0 if value <= 0 else min(127, value >> shift)


def _trunc_div(value: int, divisor: int) -> int:
    return value // divisor if value >= 0 else -((-value) // divisor)


def evaluate_reference(blob: bytes | bytearray | memoryview, case_id: str,
                       side_to_move: int) -> dict[str, object]:
    k_rows, g_rows, pocket_units = case_features(case_id)
    k_accumulators: list[list[int]] = []
    g_accumulators: list[list[int]] = []
    perspectives: list[list[int]] = []
    for perspective in range(2):
        k_values = [
            _i16(blob, K_BIAS_OFFSET + lane * 2)
            + sum(_i16(blob, _k_weight_offset(row, lane)) for row in k_rows[perspective])
            for lane in range(K_LANES)
        ]
        g_values = [
            _i16(blob, G_BIAS_OFFSET + lane * 2)
            + sum(_i16(blob, _g_weight_offset(row, lane)) for row in g_rows[perspective])
            for lane in range(G_LANES)
        ]
        k_accumulators.append(k_values)
        g_accumulators.append(g_values)
        perspectives.append(_pair(k_values) + _pair(g_values))
    dense = perspectives[side_to_move] + perspectives[side_to_move ^ 1]
    bucket = min(7, pocket_units // 4)
    fc0: list[int] = []
    for output in range(FC0_OUTPUTS):
        value = _i32(blob, FC0_BIAS_OFFSET + (bucket * FC0_OUTPUTS + output) * 4)
        value += sum(dense[input_index]
                     * _i8(blob, _fc0_weight_offset(bucket, output, input_index))
                     for input_index in range(DENSE_INPUTS))
        fc0.append(value)
    fc0_squared = [_squared(value, 7) for value in fc0]
    fc0_clipped = [_clipped(value, 7) for value in fc0]
    fc1_input = fc0_squared + fc0_clipped
    fc1: list[int] = []
    for output in range(FC1_OUTPUTS):
        value = _i32(blob, FC1_BIAS_OFFSET + (bucket * FC1_OUTPUTS + output) * 4)
        value += sum(fc1_input[input_index]
                     * _i8(blob, _fc1_weight_offset(bucket, output, input_index))
                     for input_index in range(FC1_INPUTS))
        fc1.append(value)
    fc1_squared = [_squared(value, 6) for value in fc1]
    fc1_clipped = [_clipped(value, 6) for value in fc1]
    fc2_input = fc0_squared + fc0_clipped + fc1_squared + fc1_clipped
    fc2 = _i32(blob, FC2_BIAS_OFFSET + bucket * 4)
    fc2 += sum(fc2_input[input_index] * _i8(blob, _fc2_weight_offset(bucket, input_index))
               for input_index in range(FC2_INPUTS))
    fwd = fc2 + fc0[30] - fc0[31]
    output = _trunc_div(fwd * 9_600, 16_384)
    return {
        "bucket": bucket,
        "k": k_accumulators,
        "g": g_accumulators,
        "perspective": perspectives,
        "dense": dense,
        "fc0": fc0,
        "fc0_squared": fc0_squared,
        "fc0_clipped": fc0_clipped,
        "fc1": fc1,
        "fc1_squared": fc1_squared,
        "fc1_clipped": fc1_clipped,
        "fc2": fc2,
        "fwd": fwd,
        "output": output,
    }


__all__ = [
    "FILE_BYTES", "FC0_BIAS_OFFSET", "FC1_BIAS_OFFSET", "FC2_BIAS_OFFSET",
    "PROVENANCE", "build_fixture_container", "case_features", "crc32c",
    "evaluate_reference", "finalize", "write_fixture_container",
]
