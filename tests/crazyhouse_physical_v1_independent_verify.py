#!/usr/bin/env python3
"""Independent byte-level verifier for the Crazyhouse physical V1 goldens.

This program intentionally does not import the reference codec or its unit
tests.  It reconstructs the frozen records and chunk with Python's standard
library, then authenticates every checked-in input and expected digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "crazyhouse-physical-v1.schema.json"
CONTRACT_PATH = ROOT / "tests" / "crazyhouse" / "datagen-capability-v1.json"
DATA_ROOT = ROOT / "tests" / "crazyhouse" / "data"
RESPONSE_PATH = DATA_ROOT / "crazyhouse-physical-v1-golden-capability-response.json"
PROVENANCE_PATH = DATA_ROOT / "crazyhouse-physical-v1-golden-provenance.json"
MANIFEST_PATH = DATA_ROOT / "crazyhouse-physical-v1-goldens.json"

HEADER_SIZE = 256
RECORD_SIZE = 256
FOOTER_SIZE = 128
HEADER_MAGIC = b"CHPHYSV1" + bytes(8)
RECORD_MAGIC = b"CHR1"
FOOTER_MAGIC = b"CHPHYSENDV1" + bytes(5)
RULE_PROFILE_SHA256 = bytes.fromhex("d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68")
SCHEMA_SHA256 = bytes.fromhex("c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55")
CAMPAIGN_ID = uuid.UUID("10000000-0000-4000-8000-000000000001").bytes
CHUNK_ID = uuid.UUID("20000000-0000-4000-8000-000000000001").bytes
NO_SQUARE = 255

MOVE_NONE = 0
MOVE_NORMAL = 1
MOVE_PROMOTION = 2
MOVE_EN_PASSANT = 3
MOVE_CASTLING = 4
MOVE_DROP = 5

TERMINAL_ONGOING = 0
TERMINAL_CHECKMATE = 1
TERMINAL_STALEMATE = 2
TERMINAL_FIVEFOLD = 3
TERMINAL_THREEFOLD = 4
TERMINAL_RESIGNATION = 5

FLAG_MOVE = 1
FLAG_TERMINAL = 2
FLAG_TEACHER = 4
FLAG_TEACHER_NETWORK = 8
FLAG_AUGMENTED = 16
FLAG_START = 32
FLAG_NONSTANDARD = 64

POSITION_DOMAIN = b"Crazyhouse-Stockfish physical repetition identity v1\0"
HISTORY_INITIAL_DOMAIN = b"Crazyhouse-Stockfish physical history initial v1\0"
HISTORY_STEP_DOMAIN = b"Crazyhouse-Stockfish physical history step v1\0"

PIECES = {
    "P": 1,
    "N": 2,
    "B": 3,
    "R": 4,
    "Q": 5,
    "K": 6,
    "p": 9,
    "n": 10,
    "b": 11,
    "r": 12,
    "q": 13,
    "k": 14,
}
POCKETS = {"P": 0, "N": 1, "B": 2, "R": 3, "Q": 4, "p": 5, "n": 6, "b": 7, "r": 8, "q": 9}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in output, f"duplicate JSON key {key!r}")
        output[key] = value
    return output


def load_json(path: Path, *, canonical: bool = False) -> tuple[bytes, Mapping[str, Any]]:
    payload = path.read_bytes()
    require(not payload.startswith(b"\xef\xbb\xbf"), f"{path.name}: BOM")
    require(b"\r" not in payload, f"{path.name}: CR line ending")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), f"{path.name}: terminal LF")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=duplicate_safe_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{path.name}: invalid JSON: {exc}") from exc
    require(isinstance(document, dict), f"{path.name}: root is not an object")
    if canonical:
        expected = (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        require(payload == expected, f"{path.name}: bytes are not canonical JSON")
    return payload, document


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def square(text: str) -> int:
    require(len(text) == 2 and "a" <= text[0] <= "h" and "1" <= text[1] <= "8", f"bad square {text}")
    return (ord(text[1]) - ord("1")) * 8 + ord(text[0]) - ord("a")


def parse_fen(fen: str) -> dict[str, Any]:
    fields = fen.split()
    require(len(fields) == 6, "golden FEN does not have six fields")
    board_and_pocket, side, castling, raw_ep, halfmove, fullmove = fields
    pocket_start = board_and_pocket.find("[")
    require(pocket_start >= 0 and board_and_pocket.endswith("]"), "golden FEN pocket framing")
    board_text = board_and_pocket[:pocket_start]
    pocket_text = board_and_pocket[pocket_start + 1 : -1]
    board = [0] * 64
    promoted = 0
    ranks = board_text.split("/")
    require(len(ranks) == 8, "golden FEN rank count")
    for source_rank, rank_text in enumerate(ranks):
        file_index = 0
        previous: int | None = None
        for token in rank_text:
            if token.isdigit():
                file_index += int(token)
                previous = None
            elif token == "~":
                require(previous is not None, "orphan promoted marker")
                promoted |= 1 << previous
                previous = None
            else:
                require(token in PIECES and file_index < 8, "golden FEN piece")
                previous = (7 - source_rank) * 8 + file_index
                board[previous] = PIECES[token]
                file_index += 1
        require(file_index == 8, "golden FEN rank width")
    pocket_counts = [0] * 10
    for token in pocket_text:
        require(token in POCKETS, "golden FEN pocket piece")
        pocket_counts[POCKETS[token]] += 1
    rights = 0
    for token, bit in (("K", 0), ("Q", 1), ("k", 2), ("q", 3)):
        if token in castling:
            rights |= 1 << bit
    return {
        "board": tuple(board),
        "promoted": promoted,
        "pockets": tuple(pocket_counts),
        "side": 0 if side == "w" else 1,
        "rights": rights,
        "raw_ep": NO_SQUARE if raw_ep == "-" else square(raw_ep),
        "halfmove": int(halfmove),
        "fullmove": int(fullmove),
    }


def pack_board(board: Sequence[int]) -> bytes:
    require(len(board) == 64, "board width")
    output = bytearray(32)
    for index, piece in enumerate(board):
        require(0 <= piece <= 15 and piece not in {7, 8, 15}, "reserved piece code")
        output[index // 2] |= piece << (4 * (index & 1))
    return bytes(output)


def unpack_board(payload: bytes) -> tuple[int, ...]:
    values: list[int] = []
    for byte in payload:
        values.extend((byte & 15, byte >> 4))
    return tuple(values)


def move_wire(notation: str | None, kind: str | None = None) -> tuple[int, int, int, int]:
    if notation is None:
        return MOVE_NONE, NO_SQUARE, NO_SQUARE, 0
    lowered = notation.lower()
    if "@" in lowered:
        piece = {"p": 1, "n": 2, "b": 3, "r": 4, "q": 5}[lowered[0]]
        return MOVE_DROP, NO_SQUARE, square(lowered[2:]), piece
    source = square(lowered[:2])
    target = square(lowered[2:4])
    if len(lowered) == 5:
        return MOVE_PROMOTION, source, target, {"n": 2, "b": 3, "r": 4, "q": 5}[lowered[4]]
    move_kind = {None: MOVE_NORMAL, "normal": MOVE_NORMAL, "en_passant": MOVE_EN_PASSANT, "castling": MOVE_CASTLING}[kind]
    return move_kind, source, target, 0


def position_digest(state: Mapping[str, Any], effective_ep: int) -> bytes:
    return sha256(
        POSITION_DOMAIN
        + pack_board(state["board"])
        + bytes((state["side"], state["rights"], effective_ep))
        + bytes(state["pockets"])
        + struct.pack("<Q", state["promoted"])
    )


def history_initial(trajectory_id: bytes, provenance_digest: bytes) -> bytes:
    return sha256(HISTORY_INITIAL_DOMAIN + trajectory_id + provenance_digest)


def history_step(previous: bytes, ply: int, position: bytes, move: tuple[int, int, int, int]) -> bytes:
    return sha256(HISTORY_STEP_DOMAIN + previous + struct.pack("<I", ply) + position + bytes(move))


def encode_record(
    *,
    sequence: int,
    suffix: int,
    ply: int,
    fen: str,
    move: tuple[int, int, int, int],
    white_result: int,
    previous_history: bytes | None,
    provenance_digest: bytes,
    terminal_reason: int = TERMINAL_ONGOING,
    repetition: int = 1,
    claim_policy: int = 0,
    nonstandard: bool = True,
    effective_ep: int = NO_SQUARE,
    teacher_score: int = 0,
) -> tuple[bytes, bytes, bytes, bytes]:
    state = parse_fen(fen)
    game_id = uuid.UUID(f"30000000-0000-4000-8000-{suffix:012d}").bytes
    trajectory_id = uuid.UUID(f"40000000-0000-4000-8000-{suffix:012d}").bytes
    terminal = terminal_reason != TERMINAL_ONGOING
    flags = (FLAG_START if ply == 0 else 0) | (FLAG_TERMINAL if terminal else FLAG_MOVE | FLAG_TEACHER)
    if nonstandard:
        flags |= FLAG_NONSTANDARD
    position = position_digest(state, effective_ep)
    predecessor = history_initial(trajectory_id, provenance_digest) if ply == 0 else previous_history
    require(predecessor is not None, "missing independent history predecessor")
    history = history_step(predecessor, ply, position, move)
    side_result = white_result if state["side"] == 0 else -white_result
    output = bytearray(RECORD_SIZE)
    output[:4] = RECORD_MAGIC
    struct.pack_into("<HHQ", output, 4, 1, RECORD_SIZE, sequence)
    output[16:32] = game_id
    output[32:48] = trajectory_id
    struct.pack_into("<II", output, 48, ply, flags)
    output[56:88] = pack_board(state["board"])
    struct.pack_into("<Q", output, 88, state["promoted"])
    output[96:106] = bytes(state["pockets"])
    output[106:112] = bytes((state["side"], state["rights"], state["raw_ep"], repetition, claim_policy, terminal_reason))
    struct.pack_into("<II", output, 112, state["halfmove"], state["fullmove"])
    output[120:124] = bytes(move)
    struct.pack_into(
        "<bbBBiQHHI",
        output,
        124,
        white_result,
        side_result,
        0 if terminal else 1,
        0 if terminal else 1,
        0 if terminal else teacher_score,
        0 if terminal else 1024,
        0 if terminal else 8,
        0 if terminal else 10,
        0 if terminal else 5,
    )
    output[148:180] = position
    output[180:212] = history
    output[212:244] = provenance_digest
    output[244] = effective_ep
    struct.pack_into("<I", output, 252, crc32c(output[:252]))
    return bytes(output), history, position, trajectory_id


def golden_records(provenance_digest: bytes) -> list[bytes]:
    records: list[bytes] = []
    history_by_suffix: dict[int, bytes] = {}

    def add(
        suffix: int,
        ply: int,
        fen: str,
        move: tuple[int, int, int, int],
        *,
        white_result: int,
        terminal_reason: int = TERMINAL_ONGOING,
        repetition: int = 1,
        claim_policy: int = 0,
        nonstandard: bool = True,
        effective_ep: int = NO_SQUARE,
        teacher_score: int = 0,
    ) -> None:
        encoded, history, _, _ = encode_record(
            sequence=len(records),
            suffix=suffix,
            ply=ply,
            fen=fen,
            move=move,
            white_result=white_result,
            previous_history=history_by_suffix.get(suffix),
            provenance_digest=provenance_digest,
            terminal_reason=terminal_reason,
            repetition=repetition,
            claim_policy=claim_policy,
            nonstandard=nonstandard,
            effective_ep=effective_ep,
            teacher_score=teacher_score,
        )
        history_by_suffix[suffix] = history
        records.append(encoded)

    add(1, 0, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1", move_wire("e2e4"), white_result=1, nonstandard=False, teacher_score=24)
    add(1, 1, "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR[] b KQkq e3 0 1", move_wire(None), white_result=1, terminal_reason=TERMINAL_RESIGNATION, nonstandard=False)
    add(2, 0, "k7/2Q5/2K5/8/8/8/8/8[n] b - - 0 1", move_wire("N@a1"), white_result=1, teacher_score=-24)
    add(2, 1, "k7/2Q5/2K5/8/8/8/8/n7[] w - - 1 2", move_wire(None), white_result=1, terminal_reason=TERMINAL_RESIGNATION)
    add(3, 0, "7k/8/8/8/8/8/Q~7/K7[] w - - 0 1", move_wire("a2b2"), white_result=1, teacher_score=24)
    add(3, 1, "7k/8/8/8/8/8/1Q~6/K7[] b - - 1 1", move_wire(None), white_result=1, terminal_reason=TERMINAL_RESIGNATION)
    add(4, 0, "k7/1Q6/2K5/8/8/8/8/8[] b - - 0 1", move_wire(None), white_result=1, terminal_reason=TERMINAL_CHECKMATE)
    add(5, 0, "k7/2Q5/2K5/8/8/8/8/8[] b - - 0 1", move_wire(None), white_result=0, terminal_reason=TERMINAL_STALEMATE)
    add(6, 0, "4k3/8/8/3pP3/8/8/8/4K3[] w - d6 0 2", move_wire("e5d6", "en_passant"), white_result=1, effective_ep=square("d6"), teacher_score=24)
    add(6, 1, "4k3/8/3P4/8/8/8/8/4K3[P] b - - 0 2", move_wire(None), white_result=1, terminal_reason=TERMINAL_RESIGNATION)
    add(7, 0, "r3k2r/8/8/8/8/8/8/R3K2R[] w KQkq - 7 12", move_wire("e1g1", "castling"), white_result=1, teacher_score=24)
    add(7, 1, "r3k2r/8/8/8/8/8/8/R4RK1[] b kq - 8 12", move_wire(None), white_result=1, terminal_reason=TERMINAL_RESIGNATION)
    add(8, 0, "7k/P7/8/8/8/8/8/4K3[] w - - 0 12", move_wire("a7a8n"), white_result=1, teacher_score=24)
    add(8, 1, "N~6k/8/8/8/8/8/8/4K3[] b - - 0 12", move_wire(None), white_result=1, terminal_reason=TERMINAL_RESIGNATION)
    add(9, 0, "r6k/8/8/8/8/8/Q~7/K7[] b - - 0 1", move_wire("a8a2"), white_result=-1, teacher_score=24)
    add(9, 1, "7k/8/8/8/8/8/r7/K7[p] w - - 0 2", move_wire(None), white_result=-1, terminal_reason=TERMINAL_RESIGNATION)

    cycle_fens = (
        "7k/8/8/8/8/8/8/K7[] w - - {halfmove} {fullmove}",
        "7k/8/8/8/8/8/8/1K6[] b - - {halfmove} {fullmove}",
        "6k1/8/8/8/8/8/8/1K6[] w - - {halfmove} {fullmove}",
        "6k1/8/8/8/8/8/8/K7[] b - - {halfmove} {fullmove}",
    )
    cycle_moves = (move_wire("a1b1"), move_wire("h8g8"), move_wire("b1a1"), move_wire("g8h8"))

    def repetition(suffix: int, cycles: int, terminal_reason: int, claim_policy: int) -> None:
        last = cycles * 4
        for ply in range(last + 1):
            index = ply % 4
            terminal = ply == last
            add(
                suffix,
                ply,
                cycle_fens[index].format(halfmove=ply, fullmove=ply // 2 + 1),
                move_wire(None) if terminal else cycle_moves[index],
                white_result=0,
                terminal_reason=terminal_reason if terminal else TERMINAL_ONGOING,
                repetition=ply // 4 + 1,
                claim_policy=claim_policy,
            )

    repetition(10, 4, TERMINAL_FIVEFOLD, 0)
    repetition(11, 2, TERMINAL_THREEFOLD, 1)
    return records


def build_chunk(records: Sequence[bytes], schema: bytes, provenance: bytes, capability: bytes) -> bytes:
    payload = b"".join(records)
    payload_digest = sha256(payload)
    header = bytearray(HEADER_SIZE)
    header[:16] = HEADER_MAGIC
    struct.pack_into("<IHHHHH", header, 16, 0x01020304, HEADER_SIZE, RECORD_SIZE, FOOTER_SIZE, 1, 0)
    struct.pack_into("<I", header, 32, 1)
    struct.pack_into("<Q", header, 40, len(records))
    header[48:64] = CHUNK_ID
    header[64:80] = CAMPAIGN_ID
    header[80:112] = RULE_PROFILE_SHA256
    header[112:144] = sha256(schema)
    header[144:176] = sha256(provenance)
    header[176:208] = payload_digest
    header[208:240] = sha256(capability)
    struct.pack_into("<I", header, 252, crc32c(header[:252]))
    footer = bytearray(FOOTER_SIZE)
    footer[:16] = FOOTER_MAGIC
    struct.pack_into("<HHIQQ", footer, 16, FOOTER_SIZE, 1, 1, len(records), len(payload))
    footer[40:72] = payload_digest
    footer[72:104] = sha256(header)
    footer[104:120] = CHUNK_ID
    struct.pack_into("<I", footer, 124, crc32c(footer[:124]))
    return bytes(header) + payload + bytes(footer)


def authenticate_pin(pin: Mapping[str, Any]) -> bytes:
    path = ROOT / pin["path"]
    payload = path.read_bytes()
    require(len(payload) == pin["bytes"], f"{pin['path']}: byte count")
    require(sha256(payload).hex() == pin["sha256"], f"{pin['path']}: SHA-256")
    return payload


def verify_schema(schema: bytes, document: Mapping[str, Any]) -> None:
    require(sha256(schema) == SCHEMA_SHA256, "schema digest")
    require(document["schema_id"] == "crazyhouse-physical-v1", "schema id")
    require(document["status"] == "frozen-before-data", "schema status")
    require(document["authority_profile"]["sha256"] == RULE_PROFILE_SHA256.hex(), "schema rule profile")
    for section, size in (("header", HEADER_SIZE), ("record", RECORD_SIZE), ("footer", FOOTER_SIZE)):
        cursor = 0
        names: set[str] = set()
        for field in document[section]["fields"]:
            require(field["offset"] == cursor and field["name"] not in names, f"{section} layout")
            names.add(field["name"])
            cursor += field["size"]
        require(cursor == size, f"{section} size")
    require(document["scientific_boundary"]["evaluator_independent"] is True, "schema evaluator independence")
    require(document["scientific_boundary"]["nnue_feature_rows_are_canonical"] is False, "schema feature-row boundary")
    require(document["identity_contract"]["trajectory_root_history"].startswith("fresh:"), "schema hidden-history boundary")
    require(document["identity_contract"]["trajectory_must_be_complete_and_end_once_with_terminal_record"] is True, "schema complete-trajectory boundary")
    require(document["identity_contract"]["trajectory_may_span_chunks"] is False, "schema cross-chunk trajectory boundary")


def verify_capability(contract_bytes: bytes, contract: Mapping[str, Any], response_bytes: bytes, response: Mapping[str, Any]) -> None:
    require(contract["schema"] == "crazyhouse-datagen-capability-contract/v1", "capability contract schema")
    response_contract = contract["response"]
    expected_keys = set(response_contract["required_exact"]) | set(response_contract["required_runtime_bindings"]) | {"schema"}
    require(set(response) == expected_keys, "capability response key set")
    for key, value in response_contract["required_exact"].items():
        require(response[key] == value, f"capability exact field {key}")
    require(response["schema"] == response_contract["schema"], "capability response schema")
    require(response["challenge"] == "0123456789abcdef0123456789abcdef", "capability challenge")
    require(response["capability_contract_sha256"] == sha256(contract_bytes).hex(), "capability contract binding")
    require(response["artifact_role"] == contract["producer_boundary"]["schema_golden_role"], "golden artifact role")
    require(response["production_generation_authorized"] is False, "golden production admission")
    require(not any(response[key] for key in ("fsync", "atomic_rename", "partial_quarantine", "kill_retry_unique_chunk_id")), "golden transaction claim")
    require(response["crc32c"] and response["sha256"], "capability hashes")
    require(response["supported_record_flags"] == [1, 2, 4, 8, 16, 32, 64], "capability flags")
    require(response["supported_terminal_reasons"] == list(range(7)), "capability terminal reasons")
    require(response["supported_move_kinds"] == list(range(6)), "capability move kinds")
    require(response_bytes == (json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n").encode(), "capability canonical bytes")


def verify_records(records: Sequence[bytes], provenance_digest: bytes, manifest: Mapping[str, Any]) -> dict[str, Any]:
    previous: dict[bytes, bytes] = {}
    occurrences: dict[bytes, dict[bytes, int]] = {}
    next_ply: dict[bytes, int] = {}
    terminal_trajectories: set[bytes] = set()
    labels: dict[int, tuple[int, int, int | None]] = {}
    for sequence, record in enumerate(records):
        require(len(record) == RECORD_SIZE and record[:4] == RECORD_MAGIC, "record framing")
        require(struct.unpack_from("<I", record, 252)[0] == crc32c(record[:252]), f"record {sequence} CRC32C")
        require(record[245:252] == bytes(7), f"record {sequence} reserved bytes")
        major, size, seen_sequence = struct.unpack_from("<HHQ", record, 4)
        require((major, size, seen_sequence) == (1, RECORD_SIZE, sequence), f"record {sequence} identity")
        trajectory = record[32:48]
        require(trajectory not in terminal_trajectories, f"record {sequence} continues a terminal trajectory")
        ply, flags = struct.unpack_from("<II", record, 48)
        require(ply == next_ply.get(trajectory, 0), f"record {sequence} ply continuity")
        next_ply[trajectory] = ply + 1
        board = unpack_board(record[56:88])
        promoted = struct.unpack_from("<Q", record, 88)[0]
        pockets = tuple(record[96:106])
        side, rights = record[106], record[107]
        repetition = record[109]
        move = tuple(record[120:124])
        effective_ep = record[244]
        expected_position = sha256(POSITION_DOMAIN + pack_board(board) + bytes((side, rights, effective_ep)) + bytes(pockets) + struct.pack("<Q", promoted))
        require(record[148:180] == expected_position, f"record {sequence} position identity")
        predecessor = previous.get(trajectory)
        if ply == 0:
            require(flags & FLAG_START and repetition == 1, f"record {sequence} root history")
            predecessor = history_initial(trajectory, provenance_digest)
            occurrences[trajectory] = {}
        require(predecessor is not None, f"record {sequence} missing history")
        expected_history = history_step(predecessor, ply, expected_position, move)
        require(record[180:212] == expected_history, f"record {sequence} history digest")
        previous[trajectory] = expected_history
        count = occurrences[trajectory].get(expected_position, 0) + 1
        occurrences[trajectory][expected_position] = count
        require(repetition == count, f"record {sequence} repetition count")
        require(record[212:244] == provenance_digest, f"record {sequence} provenance")
        white_result, stm_result, teacher_kind = struct.unpack_from("<bbB", record, 124)
        require(stm_result == (white_result if side == 0 else -white_result), f"record {sequence} label perspective")
        terminal = bool(flags & FLAG_TERMINAL)
        require(terminal == (record[111] != TERMINAL_ONGOING), f"record {sequence} terminal flag")
        require(bool(flags & FLAG_MOVE) == (move[0] != MOVE_NONE), f"record {sequence} move flag")
        require(bool(flags & FLAG_TEACHER) == (not terminal), f"record {sequence} teacher flag")
        require(not flags & FLAG_TEACHER_NETWORK, f"record {sequence} network-teacher flag")
        require(teacher_kind == (0 if terminal else 1), f"record {sequence} teacher kind")
        teacher_score = None if terminal else struct.unpack_from("<i", record, 128)[0]
        labels[sequence] = white_result, stm_result, teacher_score
        if terminal:
            terminal_trajectories.add(trajectory)
    require(set(previous) == terminal_trajectories, "chunk contains an incomplete trajectory")
    for golden in manifest["label_goldens"]:
        require(
            labels[golden["sequence"]]
            == (golden["game_result_white"], golden["result_side_to_move"], golden["teacher_score"]),
            f"label golden {golden['sequence']}",
        )
    require(records[1][108] == square("e3") and records[1][244] == NO_SQUARE, "raw/effective EP normalization golden")
    require(records[8][108] == square("d6") and records[8][244] == square("d6"), "legal EP golden")
    require(struct.unpack_from("<Q", records[4], 88)[0] & (1 << square("a2")), "promoted marker golden")
    require(records[32][111] == TERMINAL_FIVEFOLD and records[32][109] == 5, "fivefold golden")
    require(records[41][111] == TERMINAL_THREEFOLD and records[41][109] == 3 and records[41][110] == 1, "threefold golden")
    return {"trajectories": len(previous), "records": len(records), "labels": len(manifest["label_goldens"])}


def verify() -> dict[str, Any]:
    require(crc32c(b"123456789") == 0xE3069283, "CRC32C implementation")
    _, manifest = load_json(MANIFEST_PATH)
    authenticated = {name: authenticate_pin(pin) for name, pin in manifest["inputs"].items()}
    schema_bytes, schema = load_json(SCHEMA_PATH)
    contract_bytes, contract = load_json(CONTRACT_PATH)
    response_bytes, response = load_json(RESPONSE_PATH, canonical=True)
    provenance_bytes, provenance = load_json(PROVENANCE_PATH, canonical=True)
    require(authenticated["physical_schema"] == schema_bytes, "manifest/schema bytes")
    require(authenticated["capability_contract"] == contract_bytes, "manifest/contract bytes")
    require(authenticated["golden_capability_response"] == response_bytes, "manifest/response bytes")
    require(authenticated["golden_provenance"] == provenance_bytes, "manifest/provenance bytes")
    verify_schema(schema_bytes, schema)
    verify_capability(contract_bytes, contract, response_bytes, response)
    require(provenance["producer_artifact"]["sha256"] == sha256(authenticated["reference_codec"]).hex(), "provenance producer digest")
    require(provenance["producer_artifact"]["bytes"] == len(authenticated["reference_codec"]), "provenance producer bytes")
    require(provenance["producer_capability"]["sha256"] == sha256(response_bytes).hex(), "provenance capability digest")
    require(provenance["producer_capability"]["bytes"] == len(response_bytes), "provenance capability bytes")
    require(provenance["source_dirty"] is False, "provenance dirty source")
    require(provenance["teacher"]["synthetic"] is True and provenance["generation_settings"]["training_admissible"] is False, "golden training boundary")
    require(provenance["network"] == {"bytes": 0, "format": None, "license": None, "path": None, "sha256": None, "used": False}, "golden network identity")
    provenance_digest = sha256(provenance_bytes)
    records = golden_records(provenance_digest)
    expected = manifest["expected"]
    observed_record_hashes = [sha256(record).hex() for record in records]
    require(observed_record_hashes == expected["record_sha256"], "golden record SHA-256 list")
    record_summary = verify_records(records, provenance_digest, manifest)
    chunk = build_chunk(records, schema_bytes, provenance_bytes, response_bytes)
    require(len(chunk) == manifest["chunk"]["total_bytes"], "chunk byte count")
    require(sha256(chunk[:HEADER_SIZE]).hex() == expected["header_sha256"], "header SHA-256")
    require(sha256(chunk[HEADER_SIZE:-FOOTER_SIZE]).hex() == expected["payload_sha256"], "payload SHA-256")
    require(sha256(chunk[-FOOTER_SIZE:]).hex() == expected["footer_sha256"], "footer SHA-256")
    require(sha256(chunk).hex() == expected["chunk_sha256"], "chunk SHA-256")
    require(chunk[:16] == HEADER_MAGIC and chunk[-FOOTER_SIZE : -FOOTER_SIZE + 16] == FOOTER_MAGIC, "chunk magic")
    require(struct.unpack_from("<Q", chunk, 40)[0] == len(records), "header record count")
    require(struct.unpack_from("<Q", chunk, len(chunk) - FOOTER_SIZE + 24)[0] == len(records), "footer record count")
    require(struct.unpack_from("<I", chunk, 252)[0] == crc32c(chunk[:252]), "header CRC32C")
    require(struct.unpack_from("<I", chunk, len(chunk) - 4)[0] == crc32c(chunk[-FOOTER_SIZE:-4]), "footer CRC32C")
    for offset, crc_offset, span in ((0, 252, 252), (HEADER_SIZE, HEADER_SIZE + 252, 252), (len(chunk) - FOOTER_SIZE, len(chunk) - 4, 124)):
        damaged = bytearray(chunk)
        damaged[offset] ^= 1
        stored = struct.unpack_from("<I", damaged, crc_offset)[0]
        require(stored != crc32c(damaged[offset : offset + span]), "corruption negative control")
    return {
        "schema": "crazyhouse-physical-v1-independent-verification/v1",
        "status": "PASS",
        "evidence_class": "E1_ENGINEERING",
        "schema_sha256": sha256(schema_bytes).hex(),
        "capability_contract_sha256": sha256(contract_bytes).hex(),
        "capability_response_sha256": sha256(response_bytes).hex(),
        "provenance_sha256": provenance_digest.hex(),
        "golden_manifest_sha256": sha256(MANIFEST_PATH.read_bytes()).hex(),
        "chunk_sha256": sha256(chunk).hex(),
        "chunk_bytes": len(chunk),
        "record_count": len(records),
        "trajectory_count": record_summary["trajectories"],
        "label_golden_count": record_summary["labels"],
        "reference_codec_imported": False,
        "production_data_generated": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_claim": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify()
    except (OSError, KeyError, TypeError, ValueError, VerificationError) as exc:
        print(f"FAIL_CRAZYHOUSE_PHYSICAL_V1_INDEPENDENT {exc}")
        return 1
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(
        "PASS_CRAZYHOUSE_PHYSICAL_V1_INDEPENDENT "
        f"records={result['record_count']} trajectories={result['trajectory_count']} "
        f"chunk_sha256={result['chunk_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
