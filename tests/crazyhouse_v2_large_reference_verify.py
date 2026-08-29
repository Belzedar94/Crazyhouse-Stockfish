#!/usr/bin/env python3
"""Verify the independent trainer-side large Crazyhouse feature rows."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "nnue"))

import crazyhouse_v2_large_reference as reference  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def kings_only() -> reference.PhysicalFeatureState:
    board = [0] * 64
    board[4] = 6
    board[60] = 14
    return reference.PhysicalFeatureState(tuple(board), (0,) * 10, 0)


def replace_board(
    state: reference.PhysicalFeatureState, replacements: dict[int, int]
) -> reference.PhysicalFeatureState:
    board = list(state.board)
    for square, code in replacements.items():
        board[square] = code
    return replace(state, board=tuple(board))


def expect_error(state: reference.PhysicalFeatureState, code: str) -> None:
    try:
        reference.feature_rows(state, 0)
    except reference.LargeFeatureError as exc:
        require(exc.code == code, f"expected {code}, got {exc.code}")
    else:
        raise RuntimeError(f"expected {code}")


def verify_dimensions_and_start() -> None:
    require(reference.K_DIMENSIONS == 81_664, "K64 dimensions")
    require(reference.G_DIMENSIONS == 1_340, "G1 dimensions")
    require(reference.K_POCKET_OFFSET == 45_056, "K64 pocket offset")
    require(reference.K_PROMOTED_OFFSET == 48_896, "K64 promoted offset")
    require(reference.G_POCKET_OFFSET == 768, "G1 pocket offset")
    require(reference.G_PROMOTED_OFFSET == 828, "G1 promoted offset")

    board = [0] * 64
    board[:8] = [4, 2, 3, 5, 6, 3, 2, 4]
    board[8:16] = [1] * 8
    board[48:56] = [9] * 8
    board[56:64] = [12, 10, 11, 13, 14, 11, 10, 12]
    state = reference.PhysicalFeatureState(tuple(board), (0,) * 10, 0)
    white = reference.feature_rows(state, 0)
    black = reference.feature_rows(state, 1)
    for rows in (white, black):
        require(len(rows.k64) == 32 and len(rows.g1) == 32, "start active count")
        require(3460 in rows.k64 and 3516 in rows.k64, "start K64 king rows")
        require(644 in rows.g1 and 764 in rows.g1, "start G1 king rows")
    require(set(white.k64) == set(black.k64), "start K64 relative-color symmetry")
    require(set(white.g1) == set(black.g1), "start G1 relative-color symmetry")

    class AuthenticatedRecord:
        board = state.board
        pockets = state.pockets
        promoted_mask = state.promoted_mask

    require(
        reference.project_physical_record(AuthenticatedRecord()) == state,
        "authenticated physical-record projection",
    )


def verify_asymmetric_goldens() -> None:
    state = replace_board(kings_only(), {4: 0, 10: 6, 60: 0, 63: 14, 27: 5})
    state = replace(state, pockets=(1, 1, 0, 0, 0, 0, 0, 0, 0, 1), promoted_mask=1 << 27)
    white = reference.feature_rows(state, 0)
    black = reference.feature_rows(state, 1)
    require(len(white.k64) == len(white.g1) == 7, "asymmetric white active count")
    require(len(black.k64) == len(black.g1) == 7, "asymmetric black active count")
    require(
        {7690, 7743, 7579, 45656, 45672, 45714, 54235} == set(white.k64),
        "asymmetric white K64 goldens",
    )
    require(
        {650, 767, 539, 768, 784, 826, 1047} == set(white.g1),
        "asymmetric white G1 goldens",
    )
    require(
        {5575, 5618, 5539, 45504, 45506, 45522, 52963} == set(black.k64),
        "asymmetric black K64 goldens",
    )
    require(
        {647, 754, 611, 796, 798, 814, 1311} == set(black.g1),
        "asymmetric black G1 goldens",
    )


def rank_reflect_color_swap(
    state: reference.PhysicalFeatureState,
) -> reference.PhysicalFeatureState:
    board = [0] * 64
    promoted_mask = 0
    for square, code in enumerate(state.board):
        if code:
            board[square ^ 56] = code ^ 8
        if state.promoted_mask & (1 << square):
            promoted_mask |= 1 << (square ^ 56)
    pockets = [0] * 10
    for owner in range(2):
        for piece_type in range(5):
            pockets[(owner ^ 1) * 5 + piece_type] = state.pockets[owner * 5 + piece_type]
    return reference.PhysicalFeatureState(tuple(board), tuple(pockets), promoted_mask)


def maximum_active_state() -> reference.PhysicalFeatureState:
    state = kings_only()
    board = list(state.board)
    promoted_mask = 0
    for index in range(16):
        board[8 + index] = 10 if index % 2 else 2
        promoted_mask |= 1 << (8 + index)
    return reference.PhysicalFeatureState(
        tuple(board), (0, 4, 4, 4, 2, 0, 0, 0, 0, 0), promoted_mask
    )


def verify_capacity_and_symmetry() -> None:
    maximum = maximum_active_state()
    white = reference.feature_rows(maximum, 0)
    black = reference.feature_rows(maximum, 1)
    require(len(white.k64) == len(white.g1) == 48, "maximum white capacity")
    require(len(black.k64) == len(black.g1) == 48, "maximum black capacity")
    transformed = rank_reflect_color_swap(maximum)
    transformed_white = reference.feature_rows(transformed, 0)
    transformed_black = reference.feature_rows(transformed, 1)
    require(set(white.k64) == set(transformed_black.k64), "K64 symmetry")
    require(set(white.g1) == set(transformed_black.g1), "G1 symmetry")
    require(set(black.k64) == set(transformed_white.k64), "reverse K64 symmetry")
    require(set(black.g1) == set(transformed_white.g1), "reverse G1 symmetry")


def verify_negative_controls() -> None:
    expect_error(replace_board(kings_only(), {0: 7}), "INVALID_PIECE")
    expect_error(replace_board(kings_only(), {60: 0}), "INVALID_KING_STATE")
    expect_error(replace_board(kings_only(), {0: 1}), "PAWN_PROMOTION_RANK")
    expect_error(replace(kings_only(), pockets=(17,) + (0,) * 9), "POCKET_BOUNDS")
    expect_error(replace(kings_only(), promoted_mask=1 << 1), "PROMOTED_MASK")
    expect_error(replace(kings_only(), promoted_mask=1 << 4), "PROMOTED_MASK")

    board = list(kings_only().board)
    promoted_mask = 0
    for index in range(17):
        board[8 + index] = 2 if index % 2 == 0 else 10
        promoted_mask |= 1 << (8 + index)
    expect_error(
        reference.PhysicalFeatureState(tuple(board), (0,) * 10, promoted_mask),
        "PHYSICAL_UNIT_BOUNDS",
    )

    for piece_code, maximum in zip((2, 3, 4, 5), (4, 4, 4, 2)):
        board = list(kings_only().board)
        for index in range(maximum + 1):
            board[8 + index] = piece_code
        expect_error(
            reference.PhysicalFeatureState(tuple(board), (0,) * 10, 0),
            "PHYSICAL_UNIT_BOUNDS",
        )

    try:
        reference.feature_rows(kings_only(), 2)
    except reference.LargeFeatureError as exc:
        require(exc.code == "PERSPECTIVE", "perspective error code")
    else:
        raise RuntimeError("invalid perspective accepted")


def verify_pair_product_and_ordering() -> None:
    k64 = [0] * reference.K_TRANSFORMER_LANES
    g1 = [0] * reference.G_TRANSFORMER_LANES
    k64[0], k64[384] = 255, 255
    k64[1], k64[385] = 256, 512
    k64[2], k64[386] = -1, 255
    k64[3], k64[387] = 128, 128
    g1[0], g1[128] = 255, 254
    transformed = reference.pair_product_transform(k64, g1)
    require(len(transformed) == 512, "pair-product output width")
    require(transformed[:4] == (127, 127, 0, 32), "K64 pair-product goldens")
    require(transformed[384] == 126, "G1 pair-product golden")
    require(max(transformed) == 127 and min(transformed) == 0, "pair-product range")

    white = tuple(range(128)) * 4
    black = tuple(reversed(range(128))) * 4
    require(reference.dense_input(white, black, 0) == white + black, "white STM ordering")
    require(reference.dense_input(white, black, 1) == black + white, "black STM ordering")


def main() -> None:
    verify_dimensions_and_start()
    verify_asymmetric_goldens()
    verify_capacity_and_symmetry()
    verify_negative_controls()
    verify_pair_product_and_ordering()
    print(
        "PASS crazyhouse_v2_large_reference"
        f" K={reference.K_DIMENSIONS} G={reference.G_DIMENSIONS}"
        f" max_domain={reference.MAXIMUM_ACTIVE_PER_DOMAIN}"
    )


if __name__ == "__main__":
    main()
