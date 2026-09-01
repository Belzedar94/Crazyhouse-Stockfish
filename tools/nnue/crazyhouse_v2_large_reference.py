#!/usr/bin/env python3
"""Independent trainer-side rows for CH-NNUE-V2-LARGE-K64G1-SFNNV16.

The canonical input is physical Crazyhouse state.  This module deliberately
does not import engine code, a C++ binding, or the legacy 902-row feature
reference.  It validates the evaluator projection domain and enumerates both
frozen sparse domains for either relative-color perspective.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


K_BOARD_ROWS = 64 * 11 * 64
K_POCKET_OFFSET = K_BOARD_ROWS
K_POCKET_ROWS = 64 * 60
K_PROMOTED_OFFSET = K_POCKET_OFFSET + K_POCKET_ROWS
K_PROMOTED_ROWS = 64 * 8 * 64
K_DIMENSIONS = K_PROMOTED_OFFSET + K_PROMOTED_ROWS

G_BOARD_ROWS = 12 * 64
G_POCKET_OFFSET = G_BOARD_ROWS
G_POCKET_ROWS = 60
G_PROMOTED_OFFSET = G_POCKET_OFFSET + G_POCKET_ROWS
G_PROMOTED_ROWS = 8 * 64
G_DIMENSIONS = G_PROMOTED_OFFSET + G_PROMOTED_ROWS

MAXIMUM_ACTIVE_PER_DOMAIN = 48
K_TRANSFORMER_LANES = 768
G_TRANSFORMER_LANES = 256
PERSPECTIVE_OUTPUT_BYTES = 512
DENSE_INPUT_BYTES = 1024
POCKET_MAXIMUMS = (16, 4, 4, 4, 2, 16, 4, 4, 4, 2)
POCKET_PREFIXES = (0, 16, 20, 24, 28)
OTHER_ORIGIN_MAXIMUMS = (4, 4, 4, 2)
VALID_PIECE_CODES = frozenset((0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14))


class LargeFeatureError(RuntimeError):
    """A physical state or resulting sparse inventory is inadmissible."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalFeatureState:
    board: tuple[int, ...]
    pockets: tuple[int, ...]
    promoted_mask: int


@dataclass(frozen=True)
class LargeFeatureRows:
    k64: tuple[int, ...]
    g1: tuple[int, ...]


def pair_product_transform(
    k64_accumulator: Sequence[int], g1_accumulator: Sequence[int]
) -> tuple[int, ...]:
    """Apply the frozen SFNNv16 pair-product transform for one perspective."""

    if len(k64_accumulator) != K_TRANSFORMER_LANES:
        _reject("K64_ACCUMULATOR_WIDTH")
    if len(g1_accumulator) != G_TRANSFORMER_LANES:
        _reject("G1_ACCUMULATOR_WIDTH")
    if any(type(value) is not int or not -(1 << 31) <= value < (1 << 31)
           for value in (*k64_accumulator, *g1_accumulator)):
        _reject("ACCUMULATOR_VALUE")

    def transform_domain(values: Sequence[int]) -> tuple[int, ...]:
        half = len(values) // 2
        return tuple(
            min(255, max(0, values[index]))
            * min(255, max(0, values[index + half]))
            // 512
            for index in range(half)
        )

    output = transform_domain(k64_accumulator) + transform_domain(g1_accumulator)
    if len(output) != PERSPECTIVE_OUTPUT_BYTES or any(not 0 <= value <= 127 for value in output):
        _reject("PAIR_PRODUCT_RANGE")
    return output


def dense_input(
    white: Sequence[int], black: Sequence[int], side_to_move: int
) -> tuple[int, ...]:
    """Order transformed perspectives as side-to-move then opponent."""

    if side_to_move not in {0, 1}:
        _reject("SIDE_TO_MOVE")
    if len(white) != PERSPECTIVE_OUTPUT_BYTES or len(black) != PERSPECTIVE_OUTPUT_BYTES:
        _reject("PERSPECTIVE_OUTPUT_WIDTH")
    if any(type(value) is not int or not 0 <= value <= 127 for value in (*white, *black)):
        _reject("PERSPECTIVE_OUTPUT_VALUE")
    output = tuple(white) + tuple(black) if side_to_move == 0 else tuple(black) + tuple(white)
    if len(output) != DENSE_INPUT_BYTES:
        _reject("DENSE_INPUT_WIDTH")
    return output


def project_physical_record(record: object) -> PhysicalFeatureState:
    """Project an already authenticated physical-v1 record without labels."""

    try:
        board = tuple(getattr(record, "board"))
        pockets = tuple(getattr(record, "pockets"))
        promoted_mask = getattr(record, "promoted_mask")
    except (AttributeError, TypeError) as exc:
        raise LargeFeatureError("PHYSICAL_RECORD_PROJECTION") from exc
    state = PhysicalFeatureState(board=board, pockets=pockets, promoted_mask=promoted_mask)
    _validate_state(state)
    return state


def _reject(code: str) -> None:
    raise LargeFeatureError(code)


def _bounded_int(value: object, minimum: int, maximum: int, code: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _reject(code)
    return value


def _validate_state(state: PhysicalFeatureState) -> tuple[int, int]:
    if len(state.board) != 64:
        _reject("BOARD_WIDTH")
    if len(state.pockets) != 10:
        _reject("POCKET_WIDTH")
    promoted_mask = _bounded_int(state.promoted_mask, 0, (1 << 64) - 1, "PROMOTED_MASK")

    king_squares = [-1, -1]
    king_counts = [0, 0]
    occupied = 0
    forbidden_promoted = 0
    unpromoted_units = [0] * 6
    for square, raw_code in enumerate(state.board):
        code = _bounded_int(raw_code, 0, 15, "INVALID_PIECE")
        if code not in VALID_PIECE_CODES:
            _reject("INVALID_PIECE")
        if code == 0:
            continue
        occupied |= 1 << square
        piece_type = code & 7
        owner = code >> 3
        if piece_type == 6:
            king_counts[owner] += 1
            king_squares[owner] = square
        if piece_type == 1 and square // 8 in {0, 7}:
            _reject("PAWN_PROMOTION_RANK")
        if piece_type in {1, 6}:
            forbidden_promoted |= 1 << square
        if piece_type <= 5 and not promoted_mask & (1 << square):
            unpromoted_units[piece_type] += 1

    if king_counts != [1, 1]:
        _reject("INVALID_KING_STATE")
    if promoted_mask & ~occupied or promoted_mask & forbidden_promoted:
        _reject("PROMOTED_MASK")

    pockets = tuple(
        _bounded_int(value, 0, maximum, "POCKET_BOUNDS")
        for value, maximum in zip(state.pockets, POCKET_MAXIMUMS)
    )
    pawn_units = (
        unpromoted_units[1]
        + promoted_mask.bit_count()
        + pockets[0]
        + pockets[5]
    )
    if pawn_units > 16:
        _reject("PHYSICAL_UNIT_BOUNDS")
    physical_units = 2 + pawn_units
    for piece_type, maximum in zip(range(2, 6), OTHER_ORIGIN_MAXIMUMS):
        units = (
            unpromoted_units[piece_type]
            + pockets[piece_type - 1]
            + pockets[5 + piece_type - 1]
        )
        if units > maximum:
            _reject("PHYSICAL_UNIT_BOUNDS")
        physical_units += units
    if physical_units > 32:
        _reject("PHYSICAL_UNIT_BOUNDS")
    return king_squares[0], king_squares[1]


def feature_rows(state: PhysicalFeatureState, perspective: int) -> LargeFeatureRows:
    """Return K64 and G1 rows in the frozen C++ enumeration order."""

    if perspective not in {0, 1}:
        _reject("PERSPECTIVE")
    king_squares = _validate_state(state)
    king_bucket = king_squares[perspective]
    if perspective == 1:
        king_bucket ^= 56

    k64: list[int] = []
    g1: list[int] = []
    for square, code in enumerate(state.board):
        if code == 0:
            continue
        piece_type = code & 7
        relative_owner = int((code >> 3) != perspective)
        oriented = square if perspective == 0 else square ^ 56
        k_plane = 10 if piece_type == 6 else 2 * (piece_type - 1) + relative_owner
        g_plane = 2 * (piece_type - 1) + relative_owner
        k64.append((king_bucket * 11 + k_plane) * 64 + oriented)
        g1.append(g_plane * 64 + oriented)

    for piece_type, prefix in enumerate(POCKET_PREFIXES):
        for relative_owner in range(2):
            absolute_owner = perspective ^ relative_owner
            count = state.pockets[absolute_owner * 5 + piece_type]
            for slot in range(count):
                pocket_plane = relative_owner * 30 + prefix + slot
                k64.append(K_POCKET_OFFSET + king_bucket * 60 + pocket_plane)
                g1.append(G_POCKET_OFFSET + pocket_plane)

    for square, code in enumerate(state.board):
        if not state.promoted_mask & (1 << square):
            continue
        piece_type = code & 7
        relative_owner = int((code >> 3) != perspective)
        promoted_plane = relative_owner * 4 + piece_type - 2
        oriented = square if perspective == 0 else square ^ 56
        k64.append(
            K_PROMOTED_OFFSET + (king_bucket * 8 + promoted_plane) * 64 + oriented
        )
        g1.append(G_PROMOTED_OFFSET + promoted_plane * 64 + oriented)

    _validate_rows(k64, K_DIMENSIONS)
    _validate_rows(g1, G_DIMENSIONS)
    return LargeFeatureRows(k64=tuple(k64), g1=tuple(g1))


def _validate_rows(rows: Sequence[int], dimensions: int) -> None:
    if len(rows) > MAXIMUM_ACTIVE_PER_DOMAIN:
        _reject("ACTIVE_OVERFLOW")
    if len(rows) != len(set(rows)):
        _reject("DUPLICATE_INDEX")
    if any(row < 0 or row >= dimensions for row in rows):
        _reject("INDEX_OUT_OF_RANGE")


__all__ = [
    "G_DIMENSIONS",
    "G_TRANSFORMER_LANES",
    "G_POCKET_OFFSET",
    "G_PROMOTED_OFFSET",
    "K_DIMENSIONS",
    "K_TRANSFORMER_LANES",
    "K_POCKET_OFFSET",
    "K_PROMOTED_OFFSET",
    "LargeFeatureError",
    "LargeFeatureRows",
    "MAXIMUM_ACTIVE_PER_DOMAIN",
    "PERSPECTIVE_OUTPUT_BYTES",
    "PhysicalFeatureState",
    "dense_input",
    "feature_rows",
    "pair_product_transform",
    "project_physical_record",
]
