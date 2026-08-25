#!/usr/bin/env python3
"""Verify the C++ Crazyhouse V2 record decoder and scalar feature inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import crazyhouse_physical_v1_unit as physical_goldens  # noqa: E402


codec = physical_goldens.codec

INPUT_IDENTITIES = {
    ROOT / "schemas" / "crazyhouse-physical-v1.schema.json": (
        18_729,
        "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55",
    ),
    ROOT / "tools" / "datagen" / "crazyhouse_physical_v1.py": (
        66_451,
        "04876106c165f29ab6ee511fc02a3b2790cf9030bdfb216dbe7cafc44ce54d98",
    ),
    ROOT / "tests" / "crazyhouse" / "data" / "crazyhouse-physical-v1-goldens.json": (
        8_383,
        "94cd50961d8e51478e55a82cd4e0770d418a30483b3c5d120a470f7eb2efccac",
    ),
}

PIECE_CHARS = {
    1: "P",
    2: "N",
    3: "B",
    4: "R",
    5: "Q",
    6: "K",
    9: "p",
    10: "n",
    11: "b",
    12: "r",
    13: "q",
    14: "k",
}
POCKET_CHARS = "PNBRQpnbrq"
POCKET_TYPE_BASE = (0, 34, 44, 54, 64)
POCKET_WIDTHS = (17, 5, 5, 5, 3)


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def authenticate_inputs() -> None:
    for path, (expected_bytes, expected_sha) in INPUT_IDENTITIES.items():
        payload = path.read_bytes()
        require(len(payload) == expected_bytes, f"{path.name}: byte identity drifted")
        require(sha256(payload) == expected_sha, f"{path.name}: SHA-256 identity drifted")
    manifest = json.loads(
        (ROOT / "tests" / "crazyhouse" / "data" / "crazyhouse-physical-v1-goldens.json").read_text(
            encoding="utf-8"
        )
    )
    require(manifest["chunk"]["record_count"] == 42, "golden record count drifted")
    require(len(manifest["trajectories"]) == 11, "golden trajectory count drifted")


def square_name(square: int) -> str:
    return chr(ord("a") + square % 8) + chr(ord("1") + square // 8)


def fen_from_record(record: codec.PhysicalRecord) -> str:
    ranks: list[str] = []
    for rank in range(7, -1, -1):
        tokens: list[str] = []
        empty = 0
        for file_index in range(8):
            square = rank * 8 + file_index
            code = record.board[square]
            if code == 0:
                empty += 1
                continue
            if empty:
                tokens.append(str(empty))
                empty = 0
            require(code in PIECE_CHARS, "record contains a non-renderable piece")
            token = PIECE_CHARS[code]
            if record.promoted_mask & (1 << square):
                token += "~"
            tokens.append(token)
        if empty:
            tokens.append(str(empty))
        ranks.append("".join(tokens))
    pockets = "".join(
        symbol * record.pockets[index] for index, symbol in enumerate(POCKET_CHARS)
    )
    rights = "".join(
        symbol
        for bit, symbol in enumerate("KQkq")
        if record.castling_rights & (1 << bit)
    ) or "-"
    raw_ep = (
        "-"
        if record.raw_en_passant_square == codec.NO_SQUARE
        else square_name(record.raw_en_passant_square)
    )
    side = "w" if record.side_to_move == codec.SIDE_WHITE else "b"
    return (
        f"{'/'.join(ranks)}[{pockets}] {side} {rights} {raw_ep} "
        f"{record.halfmove_clock} {record.fullmove_number}"
    )


def expected_features(record: codec.PhysicalRecord, perspective: int) -> list[int]:
    output: list[int] = []
    for square, code in enumerate(record.board):
        if code == 0:
            continue
        piece_type = code & 7
        owner = code >> 3
        oriented = square if perspective == 0 else square ^ 56
        plane = 2 * (piece_type - 1) + int(owner != perspective)
        output.append(plane * 64 + oriented)
    for piece_type in range(5):
        for relative_owner in range(2):
            absolute_owner = perspective ^ relative_owner
            count = record.pockets[absolute_owner * 5 + piece_type]
            output.append(
                768
                + POCKET_TYPE_BASE[piece_type]
                + relative_owner * POCKET_WIDTHS[piece_type]
                + count
            )
    for square in range(64):
        if record.promoted_mask & (1 << square):
            output.append(838 + (square if perspective == 0 else square ^ 56))
    require(len(output) == len(set(output)), "independent feature inventory duplicated a row")
    require(all(0 <= row < 902 for row in output), "independent feature row is out of range")
    require(len(output) <= 138, "independent active-row capacity overflow")
    return output


def terminal_record(sequence: int, suffix: int, fen: str) -> codec.PhysicalRecord:
    return codec.build_record(
        sequence=sequence,
        game_id=codec.uuid_bytes(f"51000000-0000-4000-8000-{suffix:012d}"),
        trajectory_id=codec.uuid_bytes(f"52000000-0000-4000-8000-{suffix:012d}"),
        ply=0,
        fen=fen,
        effective_en_passant_square=codec.NO_SQUARE,
        repetition_occurrences=1,
        claim_policy=codec.CLAIM_CORE_ONLY,
        terminal_reason=codec.TERMINAL_RESIGNATION,
        move=codec.MoveWire.none(),
        game_result_white=1,
        provenance_sha256=hashlib.sha256(physical_goldens.provenance_bytes()).digest(),
        previous_history_sha256=None,
        teacher_score_kind=codec.TEACHER_NONE,
        teacher_score_value=0,
        teacher_bound=codec.BOUND_NONE,
        search_nodes=0,
        search_depth=0,
        search_seldepth=0,
        move_time_ms=0,
        teacher_used_network=False,
        nonstandard_root=True,
    )


def valid_cases() -> list[tuple[str, codec.PhysicalRecord]]:
    cases = [
        (f"golden-{index:03d}", record)
        for index, record in enumerate(physical_goldens.golden_records())
    ]
    base = terminal_record(10_000, 1, "7k/8/8/8/8/8/Q7/K7[] w - - 0 1")
    pocket = terminal_record(10_001, 2, "7k/8/8/8/8/8/Q7/K7[P] w - - 0 1")
    promoted = terminal_record(10_002, 3, "7k/8/8/8/8/8/Q~7/K7[] w - - 0 1")
    symmetry_source = terminal_record(
        10_003,
        4,
        "6rk/8/8/3n4/8/2B~5/8/K7[Pq] b - - 17 42",
    )
    symmetry_reflected = codec.reflect_rank_color_swap(symmetry_source)
    cases.extend(
        (
            ("pocket-base", base),
            ("pocket-one", pocket),
            ("promoted-one", promoted),
            ("symmetry-source", symmetry_source),
            ("symmetry-reflected", symmetry_reflected),
        )
    )
    return cases


def repair_crc(payload: bytearray) -> None:
    require(len(payload) == 256, "CRC repair requires a complete record")
    struct.pack_into("<I", payload, 252, codec.crc32c(bytes(payload[:252])))


def patch_piece(payload: bytearray, square: int, code: int) -> None:
    offset = 56 + square // 2
    shift = 4 * (square & 1)
    payload[offset] = (payload[offset] & ~(15 << shift)) | (code << shift)


def repair_position_identity(payload: bytearray) -> None:
    board = codec.unpack_board(bytes(payload[56:88]))
    promoted = struct.unpack_from("<Q", payload, 88)[0]
    digest = codec.position_identity(
        board,
        payload[106],
        payload[107],
        payload[244],
        tuple(payload[96:106]),
        promoted,
    )
    payload[148:180] = digest


def mutation(
    source: bytes,
    *,
    edit: Callable[[bytearray], None],
    repair_identity: bool = False,
    repair_record_crc: bool = True,
) -> bytes:
    payload = bytearray(source)
    edit(payload)
    if repair_identity:
        repair_position_identity(payload)
    if repair_record_crc:
        repair_crc(payload)
    return bytes(payload)


def invalid_cases(source_record: codec.PhysicalRecord) -> list[tuple[str, str, bytes]]:
    source = codec.encode_record(source_record)
    cases: list[tuple[str, str, bytes]] = [("wrong-size", "WRONG_SIZE", source[:-1])]
    cases.extend(
        (
            ("magic", "MAGIC", mutation(source, edit=lambda p: p.__setitem__(0, ord("X")))),
            ("version", "VERSION", mutation(source, edit=lambda p: p.__setitem__(4, 2))),
            (
                "reserved",
                "RESERVED_BYTES",
                mutation(source, edit=lambda p: p.__setitem__(245, 1)),
            ),
            (
                "crc",
                "CRC32C",
                mutation(
                    source,
                    edit=lambda p: p.__setitem__(200, p[200] ^ 1),
                    repair_record_crc=False,
                ),
            ),
            (
                "zero-game-id",
                "ZERO_IDENTITY",
                mutation(source, edit=lambda p: p.__setitem__(slice(16, 32), bytes(16))),
            ),
            (
                "unknown-flags",
                "FLAGS",
                mutation(source, edit=lambda p: p.__setitem__(52, p[52] | 128)),
            ),
            (
                "reserved-board-code",
                "BOARD_PIECE_CODE",
                mutation(source, edit=lambda p: patch_piece(p, 0, 7)),
            ),
            (
                "missing-white-king",
                "KING_COUNT",
                mutation(source, edit=lambda p: patch_piece(p, 4, 0)),
            ),
            (
                "pawn-on-first-rank",
                "PAWN_PROMOTION_RANK",
                mutation(source, edit=lambda p: patch_piece(p, 0, 1)),
            ),
            (
                "promoted-empty-square",
                "PROMOTED_MASK",
                mutation(
                    source,
                    edit=lambda p: struct.pack_into(
                        "<Q", p, 88, struct.unpack_from("<Q", p, 88)[0] | (1 << 16)
                    ),
                ),
            ),
            (
                "pocket-overflow",
                "POCKET_BOUNDS",
                mutation(source, edit=lambda p: p.__setitem__(96, 17)),
            ),
            (
                "side-to-move",
                "SIDE_TO_MOVE",
                mutation(source, edit=lambda p: p.__setitem__(106, 2)),
            ),
            (
                "castling-without-rook",
                "CASTLING_RIGHTS",
                mutation(source, edit=lambda p: patch_piece(p, 7, 0)),
            ),
            (
                "raw-ep-rank",
                "EN_PASSANT",
                mutation(source, edit=lambda p: p.__setitem__(108, 20)),
            ),
            (
                "zero-repetition",
                "REPETITION",
                mutation(source, edit=lambda p: p.__setitem__(109, 0)),
            ),
            (
                "claim-policy",
                "CLAIM_POLICY",
                mutation(source, edit=lambda p: p.__setitem__(110, 2)),
            ),
            (
                "terminal-enum",
                "TERMINAL_REASON",
                mutation(source, edit=lambda p: p.__setitem__(111, 7)),
            ),
            (
                "zero-fullmove-number",
                "CLOCKS",
                mutation(source, edit=lambda p: struct.pack_into("<I", p, 116, 0)),
            ),
            (
                "drop-wire",
                "MOVE_WIRE",
                mutation(source, edit=lambda p: p.__setitem__(120, codec.MOVE_DROP)),
            ),
            (
                "wrong-owner-move",
                "MOVE_STATE",
                mutation(source, edit=lambda p: p.__setitem__(121, codec.parse_square("a7"))),
            ),
            (
                "result-perspective",
                "RESULT_PERSPECTIVE",
                mutation(source, edit=lambda p: p.__setitem__(125, 0)),
            ),
            (
                "teacher-kind",
                "TEACHER_FRAMING",
                mutation(source, edit=lambda p: p.__setitem__(126, codec.TEACHER_NONE)),
            ),
            (
                "zero-history-digest",
                "ZERO_DIGEST",
                mutation(source, edit=lambda p: p.__setitem__(slice(180, 212), bytes(32))),
            ),
            (
                "position-identity",
                "POSITION_IDENTITY",
                mutation(source, edit=lambda p: p.__setitem__(148, p[148] ^ 1)),
            ),
            (
                "standard-material-conservation",
                "MATERIAL_CONSERVATION",
                mutation(
                    source,
                    edit=lambda p: p.__setitem__(96, 1),
                    repair_identity=True,
                ),
            ),
        )
    )
    return cases


def parse_rows(text: str, label: str) -> list[int]:
    require(bool(text), f"{label}: empty feature list")
    try:
        rows = [int(value) for value in text.split(",")]
    except ValueError as exc:
        raise VerificationError(f"{label}: malformed feature list") from exc
    require(len(rows) == len(set(rows)), f"{label}: duplicate feature row")
    require(all(0 <= row < 902 for row in rows), f"{label}: out-of-range feature row")
    return rows


def run_fixture(
    fixture: Path,
    valid: Sequence[tuple[str, codec.PhysicalRecord]],
    invalid: Sequence[tuple[str, str, bytes]],
) -> tuple[dict[str, tuple[list[int], list[int]]], str]:
    lines = [
        "\t".join(("VALID", case_id, codec.encode_record(record).hex(), fen_from_record(record)))
        for case_id, record in valid
    ]
    lines.extend(
        "\t".join(("INVALID", case_id, error, payload.hex()))
        for case_id, error, payload in invalid
    )
    protocol = "\n".join(lines) + "\n"
    try:
        run = subprocess.run(
            [str(fixture)],
            input=protocol,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("fixture timed out") from exc
    require(run.returncode == 0, f"fixture exited {run.returncode}: {run.stderr.strip()}")
    require(not run.stderr, "passing fixture emitted stderr")

    output = run.stdout.splitlines()
    require(len(output) == len(valid) + len(invalid) + 1, "fixture output row count drifted")
    observed: dict[str, tuple[list[int], list[int]]] = {}
    records_by_id = dict(valid)
    for expected, line in zip(valid, output[: len(valid)], strict=True):
        fields = line.split("\t")
        require(len(fields) == 5 and fields[:2] == ["OK", expected[0]], f"{expected[0]}: malformed OK row")
        record = records_by_id[expected[0]]
        require(fields[2] == record.position_identity_sha256.hex(), f"{expected[0]}: identity drifted")
        white = parse_rows(fields[3], expected[0] + "/white")
        black = parse_rows(fields[4], expected[0] + "/black")
        require(white == expected_features(record, 0), f"{expected[0]}: White rows differ")
        require(black == expected_features(record, 1), f"{expected[0]}: Black rows differ")
        observed[expected[0]] = (white, black)

    invalid_output = output[len(valid) : len(valid) + len(invalid)]
    for expected, line in zip(invalid, invalid_output, strict=True):
        require(
            line == f"REJECT\t{expected[0]}\t{expected[1]}",
            f"{expected[0]}: malformed rejection row",
        )
    require(
        output[-1]
        == f"SUMMARY\tvalid={len(valid)}\tinvalid={len(invalid)}\twrong_ruleset=REJECT\tdimensions=902",
        "fixture summary drifted",
    )
    return observed, sha256(run.stdout.encode("utf-8"))


def verify_feature_distinctions(
    records: Sequence[tuple[str, codec.PhysicalRecord]],
    features: dict[str, tuple[list[int], list[int]]],
) -> None:
    by_id = dict(records)
    base = by_id["pocket-base"]
    pocket = by_id["pocket-one"]
    promoted = by_id["promoted-one"]
    require(base.board == pocket.board == promoted.board, "distinction controls changed board bytes")
    require(base.promoted_mask == pocket.promoted_mask == 0, "pocket control changed provenance")
    require(base.pockets != pocket.pockets, "pocket control did not change a pocket")
    require(base.pockets == promoted.pockets, "promoted control changed pockets")
    for perspective in range(2):
        base_rows = set(features["pocket-base"][perspective])
        pocket_rows = set(features["pocket-one"][perspective])
        promoted_rows = set(features["promoted-one"][perspective])
        require(len(base_rows ^ pocket_rows) == 2, "exact pocket count did not replace one row")
        require(len(base_rows ^ promoted_rows) == 1, "promoted provenance did not add one row")

    source = features["symmetry-source"]
    reflected = features["symmetry-reflected"]
    require(sorted(source[0]) == sorted(reflected[1]), "White-to-Black symmetry rows differ")
    require(sorted(source[1]) == sorted(reflected[0]), "Black-to-White symmetry rows differ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    require(args.fixture.is_file(), "fixture executable is missing")
    authenticate_inputs()
    valid = valid_cases()
    require(len(valid) == 47, "valid case count drifted")
    invalid = invalid_cases(valid[0][1])
    require(len(invalid) == 26, "invalid case count drifted")
    observed, protocol_sha = run_fixture(args.fixture, valid, invalid)
    verify_feature_distinctions(valid, observed)
    print(
        "PASS crazyhouse_v2_decoder_scalar "
        f"valid={len(valid)} frozen_goldens=42 perspectives={len(valid) * 2} "
        f"invalid={len(invalid)} dimensions=902 maximum_active=138 "
        f"protocol_sha256={protocol_sha} training_admissible=false g12_closed=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL crazyhouse_v2_decoder_scalar_verify: {exc}", file=sys.stderr)
        raise SystemExit(1)
