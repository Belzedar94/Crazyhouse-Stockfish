#!/usr/bin/env python3
"""Fail-closed reference codec for CRAZYHOUSE_PHYSICAL_V1.

The codec stores physical Crazyhouse state and labels.  It deliberately does
not expose NNUE feature rows and is not the production DATAGEN executable.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Iterable, Mapping, Sequence
import uuid


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = REPOSITORY_ROOT / "schemas" / "crazyhouse-physical-v1.schema.json"

SCHEMA_ID = "crazyhouse-physical-v1"
SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
HEADER_SIZE = 256
RECORD_SIZE = 256
FOOTER_SIZE = 128
HEADER_MAGIC = b"CHPHYSV1" + bytes(8)
RECORD_MAGIC = b"CHR1"
FOOTER_MAGIC = b"CHPHYSENDV1" + bytes(5)
BYTE_ORDER_MARKER = 0x01020304
COMMITTED = 1

RULE_PROFILE_SHA256 = bytes.fromhex(
    "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
)
SCHEMA_SHA256 = bytes.fromhex(
    "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
)

FLAG_MOVE_PRESENT = 1 << 0
FLAG_TERMINAL = 1 << 1
FLAG_TEACHER_PRESENT = 1 << 2
FLAG_TEACHER_NETWORK = 1 << 3
FLAG_AUGMENTED = 1 << 4
FLAG_TRAJECTORY_START = 1 << 5
FLAG_NONSTANDARD_ROOT = 1 << 6
KNOWN_RECORD_FLAGS = (
    FLAG_MOVE_PRESENT
    | FLAG_TERMINAL
    | FLAG_TEACHER_PRESENT
    | FLAG_TEACHER_NETWORK
    | FLAG_AUGMENTED
    | FLAG_TRAJECTORY_START
    | FLAG_NONSTANDARD_ROOT
)

MOVE_NONE = 0
MOVE_NORMAL = 1
MOVE_PROMOTION = 2
MOVE_EN_PASSANT = 3
MOVE_CASTLING = 4
MOVE_DROP = 5

PIECE_NONE = 0
PIECE_PAWN = 1
PIECE_KNIGHT = 2
PIECE_BISHOP = 3
PIECE_ROOK = 4
PIECE_QUEEN = 5
PIECE_KING = 6

SIDE_WHITE = 0
SIDE_BLACK = 1
NO_SQUARE = 255

TERMINAL_ONGOING = 0
TERMINAL_CHECKMATE = 1
TERMINAL_STALEMATE = 2
TERMINAL_FIVEFOLD = 3
TERMINAL_THREEFOLD_PROXY = 4
TERMINAL_RESIGNATION = 5
TERMINAL_DRAW_ADJUDICATION = 6

CLAIM_CORE_ONLY = 0
CLAIM_IMMEDIATE_THREEFOLD = 1

TEACHER_NONE = 0
TEACHER_CENTIPAWN = 1
TEACHER_MATE_PLIES = 2
BOUND_NONE = 0
BOUND_EXACT = 1
BOUND_LOWER = 2
BOUND_UPPER = 3

POSITION_DOMAIN = b"Crazyhouse-Stockfish physical repetition identity v1\0"
HISTORY_INITIAL_DOMAIN = b"Crazyhouse-Stockfish physical history initial v1\0"
HISTORY_STEP_DOMAIN = b"Crazyhouse-Stockfish physical history step v1\0"

PIECE_TO_CODE = {
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
CODE_TO_PIECE = {value: key for key, value in PIECE_TO_CODE.items()}
RESERVED_PIECE_CODES = {7, 8, 15}
POCKET_INDEX = {
    "P": 0,
    "N": 1,
    "B": 2,
    "R": 3,
    "Q": 4,
    "p": 5,
    "n": 6,
    "b": 7,
    "r": 8,
    "q": 9,
}
POCKET_MAXIMUMS = (16, 4, 4, 4, 2, 16, 4, 4, 4, 2)


class FormatError(ValueError):
    """Raised when any schema, record, or chunk invariant fails."""


class DuplicateJsonKeyError(FormatError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FormatError(message)


def sha256(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def strict_json_bytes(path: Path) -> tuple[bytes, Mapping[str, Any]]:
    try:
        payload = path.resolve(strict=True).read_bytes()
    except OSError as exc:
        raise FormatError(f"cannot read JSON {path}: {exc}") from exc
    require(not payload.startswith(b"\xef\xbb\xbf"), "JSON must not contain a BOM")
    require(b"\r" not in payload, "JSON must use LF line endings")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), "JSON must end with exactly one LF")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise FormatError(f"invalid strict JSON: {exc}") from exc
    require(isinstance(document, dict), "JSON root must be an object")
    return payload, document


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


PROVENANCE_KEYS = {
    "schema",
    "project",
    "variant",
    "rule_profile",
    "source_commit",
    "source_tree",
    "src_tree",
    "source_dirty",
    "producer_artifact",
    "producer_capability",
    "toolchain",
    "teacher",
    "network",
    "opening_source",
    "campaign_id",
    "chunk_id",
    "chunk_index",
    "seed",
    "generation_settings",
    "adjudication",
    "invalid_game_policy",
}


def _hex(value: Any, width: int, label: str) -> None:
    require(isinstance(value, str) and len(value) == width and value == value.lower(), f"{label} width/case drifted")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise FormatError(f"{label} is not hexadecimal") from exc


def _relative_artifact(value: Any, label: str) -> None:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == {"kind", "path", "bytes", "sha256"}, f"{label} keys drifted")
    require(isinstance(value["kind"], str) and value["kind"], f"{label}.kind missing")
    path = value["path"]
    require(
        isinstance(path, str)
        and path
        and "\\" not in path
        and not path.startswith("/")
        and ":" not in path
        and ".." not in path.split("/"),
        f"{label}.path must be repository-relative POSIX text",
    )
    require(type(value["bytes"]) is int and value["bytes"] > 0, f"{label}.bytes invalid")
    _hex(value["sha256"], 64, f"{label}.sha256")


def validate_provenance_bytes(payload: bytes, *, chunk_id: bytes, campaign_id: bytes) -> Mapping[str, Any]:
    require(not payload.startswith(b"\xef\xbb\xbf"), "provenance must not contain a BOM")
    require(b"\r" not in payload, "provenance must use LF line endings")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), "provenance must end with exactly one LF")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise FormatError(f"invalid provenance JSON: {exc}") from exc
    require(isinstance(document, dict), "provenance root must be an object")
    require(set(document) == PROVENANCE_KEYS, "provenance keys drifted")
    require(payload == canonical_json_bytes(document), "provenance JSON is not canonical")
    require(document["schema"] == "crazyhouse-datagen-provenance/v1", "provenance schema drifted")
    require(document["project"] == "Crazyhouse-Stockfish" and document["variant"] == "crazyhouse", "provenance project/variant drifted")
    rule = document["rule_profile"]
    require(
        isinstance(rule, dict)
        and set(rule) == {"id", "sha256"}
        and rule["id"] == "LICHESS_CRAZYHOUSE_2026_08_12"
        and rule["sha256"] == RULE_PROFILE_SHA256.hex(),
        "provenance rule profile drifted",
    )
    for key in ("source_commit", "source_tree", "src_tree"):
        _hex(document[key], 40, f"provenance.{key}")
    require(document["source_dirty"] is False, "dirty source is inadmissible")
    _relative_artifact(document["producer_artifact"], "provenance.producer_artifact")
    capability = document["producer_capability"]
    require(
        isinstance(capability, dict)
        and set(capability) == {"bytes", "challenge", "schema", "sha256"},
        "provenance.producer_capability keys drifted",
    )
    require(capability["schema"] == "crazyhouse-datagen-capability-response/v1", "producer capability schema drifted")
    require(type(capability["bytes"]) is int and capability["bytes"] > 0, "producer capability byte count invalid")
    require(
        isinstance(capability["challenge"], str)
        and len(capability["challenge"]) == 32
        and capability["challenge"] == capability["challenge"].lower(),
        "producer capability challenge drifted",
    )
    _hex(capability["challenge"], 32, "provenance.producer_capability.challenge")
    _hex(capability["sha256"], 64, "provenance.producer_capability.sha256")
    toolchain = document["toolchain"]
    require(
        isinstance(toolchain, dict)
        and set(toolchain) == {"build_recipe_sha256", "identity", "sha256"}
        and isinstance(toolchain["identity"], str)
        and toolchain["identity"],
        "toolchain identity drifted",
    )
    _hex(toolchain["build_recipe_sha256"], 64, "provenance.toolchain.build_recipe_sha256")
    _hex(toolchain["sha256"], 64, "provenance.toolchain.sha256")
    teacher = document["teacher"]
    network = document["network"]
    require(
        isinstance(teacher, dict)
        and set(teacher)
        == {
            "artifact",
            "bound_policy",
            "kind",
            "network_used",
            "score_perspective",
            "search_settings_sha256",
            "synthetic",
        },
        "teacher provenance keys drifted",
    )
    require(teacher["kind"] in {"golden-fixture", "classical", "network"}, "teacher kind invalid")
    require(teacher["score_perspective"] == "side-to-move", "teacher score perspective drifted")
    require(teacher["bound_policy"] == "exact-only-for-ongoing-records", "teacher bound policy drifted")
    require(type(teacher["synthetic"]) is bool, "teacher.synthetic must be boolean")
    _hex(teacher["search_settings_sha256"], 64, "provenance.teacher.search_settings_sha256")
    if teacher["kind"] == "golden-fixture":
        require(teacher["artifact"] is None and teacher["synthetic"] is True, "golden teacher identity is dishonest")
    else:
        _relative_artifact(teacher["artifact"], "provenance.teacher.artifact")
        require(teacher["synthetic"] is False, "production teacher cannot be synthetic")
    require(
        isinstance(network, dict)
        and set(network) == {"bytes", "format", "license", "path", "sha256", "used"},
        "network provenance keys drifted",
    )
    require(type(network.get("used")) is bool, "network.used must be boolean")
    require(type(teacher.get("network_used")) is bool, "teacher.network_used must be boolean")
    require(teacher["network_used"] == network["used"], "teacher/network use mismatch")
    if network["used"]:
        require(type(network["bytes"]) is int and network["bytes"] > 0, "network byte count invalid")
        require(
            isinstance(network["path"], str)
            and network["path"]
            and "\\" not in network["path"]
            and not network["path"].startswith("/")
            and ":" not in network["path"],
            "network path must be repository-relative POSIX text",
        )
        require(isinstance(network["format"], str) and network["format"], "network format missing")
        require(isinstance(network["license"], str) and network["license"], "network license missing")
        _hex(network["sha256"], 64, "network.sha256")
    else:
        require(
            network == {"bytes": 0, "format": None, "license": None, "path": None, "sha256": None, "used": False},
            "unused network must have an explicit null identity",
        )
    if teacher.get("kind") == "classical":
        require(network["used"] is False, "classical teacher cannot claim a network")
    if teacher.get("kind") == "network":
        require(network["used"] is True, "network teacher must bind network bytes")
    opening = document["opening_source"]
    require(
        isinstance(opening, dict)
        and set(opening)
        == {"artifact", "engine_selected", "kind", "match_result_selected", "selection_policy_sha256"},
        "opening source keys drifted",
    )
    require(type(opening["engine_selected"]) is bool, "opening_source.engine_selected must be boolean")
    require(type(opening["match_result_selected"]) is bool, "opening_source.match_result_selected must be boolean")
    require(isinstance(opening["kind"], str) and opening["kind"], "opening source kind missing")
    _hex(opening["selection_policy_sha256"], 64, "provenance.opening_source.selection_policy_sha256")
    if opening["artifact"] is not None:
        _relative_artifact(opening["artifact"], "provenance.opening_source.artifact")
    require(document["campaign_id"] == str(uuid.UUID(bytes=campaign_id)), "campaign id/header mismatch")
    require(document["chunk_id"] == str(uuid.UUID(bytes=chunk_id)), "chunk id/header mismatch")
    require(type(document["chunk_index"]) is int and document["chunk_index"] >= 0, "chunk index invalid")
    require(isinstance(document["seed"], str) and document["seed"].isdigit(), "seed must be a decimal string")
    require(
        isinstance(document["generation_settings"], dict)
        and document["generation_settings"]
        and type(document["generation_settings"].get("training_admissible")) is bool,
        "generation settings missing training admission",
    )
    require(isinstance(document["adjudication"], dict) and document["adjudication"], "adjudication policy missing")
    invalid_policy = document["invalid_game_policy"]
    require(
        isinstance(invalid_policy, dict)
        and set(invalid_policy) == {"crash", "illegal_move", "safety_limit", "timeloss"}
        and all(value == "quarantine-game" for value in invalid_policy.values()),
        "invalid-game policy drifted",
    )
    return document


def validate_capability_response_bytes(
    payload: bytes,
    *,
    contract_bytes: bytes,
    expected_challenge: str,
) -> Mapping[str, Any]:
    require(not payload.startswith(b"\xef\xbb\xbf"), "capability response must not contain a BOM")
    require(b"\r" not in payload, "capability response must use LF line endings")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), "capability response must end with exactly one LF")
    try:
        response = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
        contract = json.loads(contract_bytes.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise FormatError(f"invalid capability JSON: {exc}") from exc
    require(isinstance(response, dict) and isinstance(contract, dict), "capability JSON roots must be objects")
    require(payload == canonical_json_bytes(response), "capability response JSON is not canonical")
    require(contract.get("schema") == "crazyhouse-datagen-capability-contract/v1", "capability contract schema drifted")
    response_contract = contract.get("response")
    require(isinstance(response_contract, dict), "capability response contract missing")
    required_exact = response_contract.get("required_exact")
    runtime_bindings = response_contract.get("required_runtime_bindings")
    require(isinstance(required_exact, dict) and isinstance(runtime_bindings, list), "capability field contract drifted")
    expected_keys = set(required_exact) | set(runtime_bindings) | {"schema"}
    require(set(response) == expected_keys, "capability response keys drifted")
    require(response["schema"] == response_contract.get("schema"), "capability response schema drifted")
    for key, expected in required_exact.items():
        require(response[key] == expected, f"capability response {key} drifted")
    require(
        isinstance(expected_challenge, str)
        and len(expected_challenge) == 32
        and expected_challenge == expected_challenge.lower(),
        "expected capability challenge invalid",
    )
    _hex(expected_challenge, 32, "expected capability challenge")
    require(response["challenge"] == expected_challenge, "capability challenge mismatch")
    require(
        response["capability_contract_sha256"] == sha256(contract_bytes).hex(),
        "capability contract digest mismatch",
    )
    require(response["source_dirty"] is False, "capability response reports dirty source")
    require(type(response["artifact_bytes"]) is int and response["artifact_bytes"] > 0, "capability artifact byte count invalid")
    for key in (
        "artifact_sha256",
        "build_recipe_sha256",
        "source_commit",
        "source_tree",
        "src_tree",
        "toolchain_sha256",
    ):
        _hex(response[key], 40 if key in {"source_commit", "source_tree", "src_tree"} else 64, f"capability.{key}")
    for key in (
        "crc32c",
        "sha256",
        "fsync",
        "atomic_rename",
        "partial_quarantine",
        "kill_retry_unique_chunk_id",
        "production_generation_authorized",
    ):
        require(type(response[key]) is bool, f"capability.{key} must be boolean")
    require(response["crc32c"] and response["sha256"], "capability integrity algorithms unavailable")
    require(response["supported_record_flags"] == [1, 2, 4, 8, 16, 32, 64], "capability record flags drifted")
    require(response["supported_terminal_reasons"] == list(range(7)), "capability terminal reasons drifted")
    require(response["supported_claim_policies"] == [0, 1], "capability claim policies drifted")
    require(response["supported_move_kinds"] == list(range(6)), "capability move kinds drifted")
    role = response["artifact_role"]
    if role == contract["producer_boundary"]["schema_golden_role"]:
        require(response["production_generation_authorized"] is False, "golden codec claims production authorization")
    elif role == contract["producer_boundary"]["production_role"]:
        require(response["production_generation_authorized"] is True, "production producer is not authorized")
        require(
            response["fsync"]
            and response["atomic_rename"]
            and response["partial_quarantine"]
            and response["kill_retry_unique_chunk_id"],
            "production producer lacks transactional capabilities",
        )
    else:
        raise FormatError("capability artifact role is unknown")
    return response


def _validate_layout(fields: Any, expected_size: int, label: str) -> None:
    require(isinstance(fields, list) and fields, f"{label}.fields must be a nonempty array")
    cursor = 0
    names: set[str] = set()
    for index, field in enumerate(fields):
        require(isinstance(field, dict), f"{label}.fields[{index}] must be an object")
        require(set(field) == {"name", "offset", "size", "storage"}, f"{label}.fields[{index}] keys drifted")
        name = field["name"]
        offset = field["offset"]
        size = field["size"]
        require(isinstance(name, str) and name not in names, f"duplicate {label} field {name!r}")
        require(type(offset) is int and offset == cursor, f"{label}.{name} offset must be {cursor}")
        require(type(size) is int and size > 0, f"{label}.{name} size must be positive")
        require(isinstance(field["storage"], str), f"{label}.{name} storage must be text")
        names.add(name)
        cursor += size
    require(cursor == expected_size, f"{label} covers {cursor} bytes, expected {expected_size}")


def validate_schema(document: Mapping[str, Any]) -> None:
    expected_top = {
        "schema_version",
        "schema_id",
        "status",
        "variant",
        "authority_profile",
        "scientific_boundary",
        "file_layout",
        "header",
        "record",
        "footer",
        "board_encoding",
        "pocket_encoding",
        "state_encoding",
        "move_encoding",
        "label_contract",
        "identity_contract",
        "provenance_contract",
        "symmetry_contract",
        "standard_start_reachability",
        "transaction_contract",
        "g0_requirements",
        "file_policy",
    }
    require(set(document) == expected_top, "schema top-level keys drifted")
    require(document["schema_version"] == 1, "schema_version drifted")
    require(document["schema_id"] == SCHEMA_ID, "schema_id drifted")
    require(document["status"] == "frozen-before-data", "schema status drifted")
    require(document["variant"] == "crazyhouse", "variant drifted")
    authority = document["authority_profile"]
    require(isinstance(authority, dict), "authority_profile must be an object")
    require(authority.get("id") == "LICHESS_CRAZYHOUSE_2026_08_12", "authority id drifted")
    require(authority.get("sha256") == RULE_PROFILE_SHA256.hex(), "authority digest drifted")
    layout = document["file_layout"]
    require(isinstance(layout, dict), "file_layout must be an object")
    require(layout.get("byte_order") == "little-endian", "byte order drifted")
    require(layout.get("header_size") == HEADER_SIZE, "header size drifted")
    require(layout.get("record_size") == RECORD_SIZE, "record size drifted")
    require(layout.get("footer_size") == FOOTER_SIZE, "footer size drifted")
    require(bytes.fromhex(layout.get("header_magic_hex", "")) == HEADER_MAGIC, "header magic drifted")
    require(bytes.fromhex(layout.get("record_magic_hex", "")) == RECORD_MAGIC, "record magic drifted")
    require(bytes.fromhex(layout.get("footer_magic_hex", "")) == FOOTER_MAGIC, "footer magic drifted")
    require(layout.get("byte_order_marker") == BYTE_ORDER_MARKER, "byte-order marker drifted")
    _validate_layout(document["header"].get("fields"), HEADER_SIZE, "header")
    _validate_layout(document["record"].get("fields"), RECORD_SIZE, "record")
    _validate_layout(document["footer"].get("fields"), FOOTER_SIZE, "footer")
    require(document["scientific_boundary"].get("evaluator_independent") is True, "schema is not evaluator-independent")
    require(document["scientific_boundary"].get("nnue_feature_rows_are_canonical") is False, "feature rows became canonical")
    require(document["g0_requirements"].get("opening_root_split_is_sufficient") is False, "opening split boundary drifted")
    require(document["transaction_contract"].get("append") == "forbidden", "append policy drifted")


def load_schema(path: Path = DEFAULT_SCHEMA) -> tuple[bytes, Mapping[str, Any]]:
    payload, document = strict_json_bytes(path)
    validate_schema(document)
    require(sha256(payload) == SCHEMA_SHA256, "schema byte identity drifted")
    return payload, document


def validate_schema_bytes(payload: bytes) -> Mapping[str, Any]:
    require(not payload.startswith(b"\xef\xbb\xbf"), "schema must not contain a BOM")
    require(b"\r" not in payload, "schema must use LF line endings")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), "schema must end with exactly one LF")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise FormatError(f"invalid schema JSON: {exc}") from exc
    require(isinstance(document, dict), "schema root must be an object")
    validate_schema(document)
    require(sha256(payload) == SCHEMA_SHA256, "schema byte identity drifted")
    return document


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def parse_square(value: str) -> int:
    require(len(value) == 2, f"invalid square {value!r}")
    file_index = ord(value[0]) - ord("a")
    rank_index = ord(value[1]) - ord("1")
    require(0 <= file_index < 8 and 0 <= rank_index < 8, f"invalid square {value!r}")
    return rank_index * 8 + file_index


def square_name(square: int) -> str:
    require(0 <= square < 64, f"invalid square {square}")
    return chr(ord("a") + square % 8) + chr(ord("1") + square // 8)


def pack_board(board: Sequence[int]) -> bytes:
    require(len(board) == 64, "board must contain 64 squares")
    output = bytearray(32)
    for square, code in enumerate(board):
        require(type(code) is int and 0 <= code <= 15 and code not in RESERVED_PIECE_CODES, f"invalid piece code {code} at {square_name(square)}")
        output[square // 2] |= code << (4 * (square & 1))
    return bytes(output)


def unpack_board(payload: bytes) -> tuple[int, ...]:
    require(len(payload) == 32, "packed board must be 32 bytes")
    board: list[int] = []
    for byte in payload:
        board.extend((byte & 0x0F, byte >> 4))
    require(not any(code in RESERVED_PIECE_CODES for code in board), "packed board contains a reserved piece code")
    return tuple(board)


@dataclass(frozen=True)
class FenState:
    board: tuple[int, ...]
    promoted_mask: int
    pockets: tuple[int, ...]
    side_to_move: int
    castling_rights: int
    raw_en_passant_square: int
    halfmove_clock: int
    fullmove_number: int


def parse_fen(fen: str) -> FenState:
    fields = fen.split()
    require(len(fields) == 6, "Crazyhouse FEN must contain six fields")
    board_pocket, side, castling, ep, halfmove, fullmove = fields
    opening = board_pocket.find("[")
    require(opening >= 0 and board_pocket.endswith("]"), "canonical Crazyhouse FEN requires bracket pockets")
    board_text = board_pocket[:opening]
    pocket_text = board_pocket[opening + 1 : -1]
    ranks = board_text.split("/")
    require(len(ranks) == 8, "board must contain eight ranks")
    board = [0] * 64
    promoted = 0
    for source_rank, rank_text in enumerate(ranks):
        board_rank = 7 - source_rank
        file_index = 0
        previous_square: int | None = None
        for token in rank_text:
            if token.isdigit():
                require(token != "0", "zero-length FEN run is invalid")
                file_index += int(token)
                previous_square = None
            elif token == "~":
                require(previous_square is not None, "promoted marker must follow a piece")
                code = board[previous_square]
                require(code & 7 not in {PIECE_PAWN, PIECE_KING}, "pawn or king cannot carry promoted provenance")
                require(not promoted & (1 << previous_square), "duplicate promoted marker")
                promoted |= 1 << previous_square
                previous_square = None
            else:
                require(token in PIECE_TO_CODE, f"invalid board piece {token!r}")
                require(file_index < 8, "rank overflow")
                square = board_rank * 8 + file_index
                board[square] = PIECE_TO_CODE[token]
                file_index += 1
                previous_square = square
        require(file_index == 8, "rank does not contain eight squares")
    pockets = [0] * 10
    for token in pocket_text:
        require(token in POCKET_INDEX, f"invalid pocket piece {token!r}")
        pockets[POCKET_INDEX[token]] += 1
    for observed, maximum in zip(pockets, POCKET_MAXIMUMS):
        require(observed <= maximum, "pocket count exceeds physical maximum")
    require(side in {"w", "b"}, "invalid side to move")
    rights = 0
    if castling != "-":
        require(len(set(castling)) == len(castling), "duplicate castling right")
        for token, bit in (("K", 0), ("Q", 1), ("k", 2), ("q", 3)):
            if token in castling:
                rights |= 1 << bit
        require(set(castling) <= set("KQkq"), "invalid castling right")
    raw_ep = NO_SQUARE if ep == "-" else parse_square(ep)
    try:
        halfmove_value = int(halfmove)
        fullmove_value = int(fullmove)
    except ValueError as exc:
        raise FormatError("invalid FEN clock") from exc
    require(0 <= halfmove_value <= 0xFFFFFFFF, "halfmove clock out of range")
    require(1 <= fullmove_value <= 0xFFFFFFFF, "fullmove number out of range")
    return FenState(
        board=tuple(board),
        promoted_mask=promoted,
        pockets=tuple(pockets),
        side_to_move=SIDE_WHITE if side == "w" else SIDE_BLACK,
        castling_rights=rights,
        raw_en_passant_square=raw_ep,
        halfmove_clock=halfmove_value,
        fullmove_number=fullmove_value,
    )


@dataclass(frozen=True)
class MoveWire:
    kind: int
    from_square: int
    to_square: int
    aux_piece: int

    @staticmethod
    def none() -> "MoveWire":
        return MoveWire(MOVE_NONE, NO_SQUARE, NO_SQUARE, PIECE_NONE)

    def bytes(self) -> bytes:
        return bytes((self.kind, self.from_square, self.to_square, self.aux_piece))


def parse_move(value: str | None, *, kind: str | None = None) -> MoveWire:
    if value is None:
        require(kind in {None, "none"}, "none move cannot have a kind")
        move = MoveWire.none()
        validate_move(move)
        return move
    lowered = value.lower()
    if "@" in lowered:
        require(kind in {None, "drop"}, "drop move kind mismatch")
        require(len(lowered) == 4 and lowered[1] == "@", "invalid drop wire")
        piece = {"p": 1, "n": 2, "b": 3, "r": 4, "q": 5}.get(lowered[0])
        require(piece is not None, "invalid drop piece")
        move = MoveWire(MOVE_DROP, NO_SQUARE, parse_square(lowered[2:]), piece)
        validate_move(move)
        return move
    require(len(lowered) in {4, 5}, "invalid move wire length")
    from_square = parse_square(lowered[:2])
    to_square = parse_square(lowered[2:4])
    if len(lowered) == 5:
        require(kind in {None, "promotion"}, "promotion kind mismatch")
        aux = {"n": 2, "b": 3, "r": 4, "q": 5}.get(lowered[4])
        require(aux is not None, "invalid promotion piece")
        move = MoveWire(MOVE_PROMOTION, from_square, to_square, aux)
        validate_move(move)
        return move
    move_kind = {
        None: MOVE_NORMAL,
        "normal": MOVE_NORMAL,
        "en_passant": MOVE_EN_PASSANT,
        "castling": MOVE_CASTLING,
    }.get(kind)
    require(move_kind is not None, "invalid move kind")
    move = MoveWire(move_kind, from_square, to_square, PIECE_NONE)
    validate_move(move)
    return move


def position_identity(
    board: Sequence[int],
    side_to_move: int,
    castling_rights: int,
    effective_en_passant_square: int,
    pockets: Sequence[int],
    promoted_mask: int,
) -> bytes:
    payload = (
        POSITION_DOMAIN
        + pack_board(board)
        + bytes((side_to_move, castling_rights, effective_en_passant_square))
        + bytes(pockets)
        + struct.pack("<Q", promoted_mask)
    )
    return sha256(payload)


def history_initial(trajectory_id: bytes, provenance_sha256: bytes) -> bytes:
    require(len(trajectory_id) == 16, "trajectory id must be 16 bytes")
    require(len(provenance_sha256) == 32, "provenance digest must be 32 bytes")
    return sha256(HISTORY_INITIAL_DOMAIN + trajectory_id + provenance_sha256)


def history_step(previous: bytes, ply: int, position_sha256: bytes, move: MoveWire) -> bytes:
    require(len(previous) == 32 and len(position_sha256) == 32, "history digest width drifted")
    require(0 <= ply <= 0xFFFFFFFF, "ply out of range")
    return sha256(HISTORY_STEP_DOMAIN + previous + struct.pack("<I", ply) + position_sha256 + move.bytes())


@dataclass(frozen=True)
class PhysicalRecord:
    sequence: int
    game_id: bytes
    trajectory_id: bytes
    ply: int
    flags: int
    board: tuple[int, ...]
    promoted_mask: int
    pockets: tuple[int, ...]
    side_to_move: int
    castling_rights: int
    raw_en_passant_square: int
    effective_en_passant_square: int
    repetition_occurrences: int
    claim_policy: int
    terminal_reason: int
    halfmove_clock: int
    fullmove_number: int
    move: MoveWire
    game_result_white: int
    result_side_to_move: int
    teacher_score_kind: int
    teacher_bound: int
    teacher_score_value: int
    search_nodes: int
    search_depth: int
    search_seldepth: int
    move_time_ms: int
    position_identity_sha256: bytes
    history_prefix_sha256: bytes
    provenance_sha256: bytes


def _validate_uuid_bytes(value: bytes, label: str) -> None:
    require(isinstance(value, bytes) and len(value) == 16 and any(value), f"{label} must be a nonzero 16-byte id")


def validate_record(record: PhysicalRecord, *, recompute_position: bool = True) -> None:
    require(0 <= record.sequence <= 0xFFFFFFFFFFFFFFFF, "sequence out of range")
    _validate_uuid_bytes(record.game_id, "game_id")
    _validate_uuid_bytes(record.trajectory_id, "trajectory_id")
    require(0 <= record.ply <= 0xFFFFFFFF, "ply out of range")
    require(record.flags & ~KNOWN_RECORD_FLAGS == 0, "unknown record flag")
    require(bool(record.flags & FLAG_TRAJECTORY_START) == (record.ply == 0), "trajectory-start flag/ply mismatch")
    pack_board(record.board)
    require(0 <= record.promoted_mask <= 0xFFFFFFFFFFFFFFFF, "promoted mask out of range")
    occupied = sum((1 << square) for square, code in enumerate(record.board) if code)
    forbidden_promoted = sum((1 << square) for square, code in enumerate(record.board) if code & 7 in {PIECE_PAWN, PIECE_KING})
    require(record.promoted_mask & ~occupied == 0, "promoted mask includes an empty square")
    require(record.promoted_mask & forbidden_promoted == 0, "pawn or king carries promoted provenance")
    require(
        not any(
            code and (code & 7) == PIECE_PAWN and square // 8 in {0, 7}
            for square, code in enumerate(record.board)
        ),
        "pawn occupies a promotion rank",
    )
    require(record.board.count(PIECE_KING) == 1, "record must contain exactly one white king")
    require(record.board.count(PIECE_KING ^ 8) == 1, "record must contain exactly one black king")
    require(len(record.pockets) == 10, "pockets must contain ten counts")
    for observed, maximum in zip(record.pockets, POCKET_MAXIMUMS):
        require(type(observed) is int and 0 <= observed <= maximum, "pocket count out of range")
    require(record.side_to_move in {SIDE_WHITE, SIDE_BLACK}, "invalid side to move")
    require(0 <= record.castling_rights <= 15, "invalid castling rights")
    for bit, king_square, king_code, rook_square, rook_code in (
        (0, parse_square("e1"), PIECE_KING, parse_square("h1"), PIECE_ROOK),
        (1, parse_square("e1"), PIECE_KING, parse_square("a1"), PIECE_ROOK),
        (2, parse_square("e8"), PIECE_KING ^ 8, parse_square("h8"), PIECE_ROOK ^ 8),
        (3, parse_square("e8"), PIECE_KING ^ 8, parse_square("a8"), PIECE_ROOK ^ 8),
    ):
        if record.castling_rights & (1 << bit):
            require(record.board[king_square] == king_code, "castling right has no eligible king")
            require(record.board[rook_square] == rook_code, "castling right has no eligible rook")
            require(
                not record.promoted_mask & ((1 << king_square) | (1 << rook_square)),
                "castling right uses promoted provenance",
            )
    require(record.raw_en_passant_square == NO_SQUARE or 0 <= record.raw_en_passant_square < 64, "raw en-passant square out of range")
    require(record.effective_en_passant_square in {record.raw_en_passant_square, NO_SQUARE}, "effective en-passant must equal raw or none")
    if record.raw_en_passant_square != NO_SQUARE:
        ep = record.raw_en_passant_square
        expected_rank = 5 if record.side_to_move == SIDE_WHITE else 2
        require(ep // 8 == expected_rank, "raw en-passant square has the wrong rank for side to move")
        require(record.board[ep] == PIECE_NONE, "raw en-passant target is occupied")
        previous_mover = record.side_to_move ^ 1
        pawn_push = 8 if previous_mover == SIDE_WHITE else -8
        pawn_code = PIECE_PAWN if previous_mover == SIDE_WHITE else PIECE_PAWN ^ 8
        require(record.board[ep + pawn_push] == pawn_code, "raw en-passant target has no double-pushed pawn")
        require(record.board[ep - pawn_push] == PIECE_NONE, "raw en-passant pawn origin is occupied")
        if record.effective_en_passant_square != NO_SQUARE:
            attacker_code = PIECE_PAWN if record.side_to_move == SIDE_WHITE else PIECE_PAWN ^ 8
            source_rank_delta = -1 if record.side_to_move == SIDE_WHITE else 1
            attacker_rank = expected_rank + source_rank_delta
            attacker_files = (ep % 8 - 1, ep % 8 + 1)
            require(
                any(
                    0 <= file_index < 8
                    and record.board[attacker_rank * 8 + file_index] == attacker_code
                    for file_index in attacker_files
                ),
                "effective en-passant target has no pseudo-legal capturer",
            )
    require(1 <= record.repetition_occurrences <= 5, "repetition occurrence count out of range")
    require(record.claim_policy in {CLAIM_CORE_ONLY, CLAIM_IMMEDIATE_THREEFOLD}, "unknown claim policy")
    require(TERMINAL_ONGOING <= record.terminal_reason <= TERMINAL_DRAW_ADJUDICATION, "unknown terminal reason")
    require(0 <= record.halfmove_clock <= 0xFFFFFFFF, "halfmove clock out of range")
    require(1 <= record.fullmove_number <= 0xFFFFFFFF, "fullmove number out of range")
    terminal = bool(record.flags & FLAG_TERMINAL)
    move_present = bool(record.flags & FLAG_MOVE_PRESENT)
    require(terminal == (record.terminal_reason != TERMINAL_ONGOING), "terminal flag/reason mismatch")
    require(move_present == (record.move.kind != MOVE_NONE), "move-present flag/move mismatch")
    require(terminal != move_present, "a record must be terminal without a move or ongoing with a move")
    validate_move(record.move)
    if move_present:
        if record.move.kind == MOVE_DROP:
            require(record.board[record.move.to_square] == PIECE_NONE, "drop target is occupied")
            pocket_index = record.side_to_move * 5 + record.move.aux_piece - 1
            require(record.pockets[pocket_index] > 0, "drop piece is absent from side-to-move pocket")
        else:
            moving_piece = record.board[record.move.from_square]
            require(moving_piece != PIECE_NONE, "move origin is empty")
            require((moving_piece >> 3) == record.side_to_move, "move origin belongs to the wrong side")
            target_piece = record.board[record.move.to_square]
            require(target_piece == PIECE_NONE or (target_piece >> 3) != record.side_to_move, "move captures own piece")
            moving_type = moving_piece & 7
            if record.move.kind == MOVE_PROMOTION:
                require(moving_type == PIECE_PAWN, "promotion mover is not a pawn")
                required_from_rank = 6 if record.side_to_move == SIDE_WHITE else 1
                required_to_rank = 7 if record.side_to_move == SIDE_WHITE else 0
                require(
                    record.move.from_square // 8 == required_from_rank
                    and record.move.to_square // 8 == required_to_rank,
                    "promotion ranks are invalid",
                )
            elif moving_type == PIECE_PAWN:
                require(record.move.to_square // 8 not in {0, 7}, "pawn move to promotion rank lacks promotion kind")
            if record.move.kind == MOVE_EN_PASSANT:
                require(moving_type == PIECE_PAWN, "en-passant mover is not a pawn")
                require(record.move.to_square == record.effective_en_passant_square, "en-passant move does not use effective target")
                require(target_piece == PIECE_NONE, "en-passant target is occupied")
            if record.move.kind == MOVE_CASTLING:
                expected_from = parse_square("e1") if record.side_to_move == SIDE_WHITE else parse_square("e8")
                destinations = (
                    (parse_square("g1"), 0),
                    (parse_square("c1"), 1),
                ) if record.side_to_move == SIDE_WHITE else (
                    (parse_square("g8"), 2),
                    (parse_square("c8"), 3),
                )
                destination_to_bit = dict(destinations)
                require(moving_type == PIECE_KING and record.move.from_square == expected_from, "castling mover/origin is invalid")
                require(record.move.to_square in destination_to_bit, "castling destination is invalid")
                require(record.castling_rights & (1 << destination_to_bit[record.move.to_square]), "castling move lacks its right")
    require(record.game_result_white in {-1, 0, 1}, "white result out of range")
    expected_stm = record.game_result_white if record.side_to_move == SIDE_WHITE else -record.game_result_white
    require(record.result_side_to_move == expected_stm, "side-to-move result perspective mismatch")
    teacher_present = bool(record.flags & FLAG_TEACHER_PRESENT)
    teacher_network = bool(record.flags & FLAG_TEACHER_NETWORK)
    require(not teacher_network or teacher_present, "network teacher flag requires a teacher score")
    require(teacher_present == (not terminal), "ongoing records require an exact teacher and terminal records forbid one")
    if teacher_present:
        require(record.teacher_score_kind in {TEACHER_CENTIPAWN, TEACHER_MATE_PLIES}, "invalid teacher score kind")
        require(record.teacher_bound == BOUND_EXACT, "committed teacher score must be exact")
        require(record.search_nodes > 0, "teacher search must record positive nodes")
    else:
        require(record.teacher_score_kind == TEACHER_NONE, "absent teacher has a score kind")
        require(record.teacher_bound == BOUND_NONE, "absent teacher has a bound")
        require(record.teacher_score_value == 0, "absent teacher has a value")
        require(record.search_nodes == 0 and record.search_depth == 0 and record.search_seldepth == 0 and record.move_time_ms == 0, "absent teacher has search metadata")
    require(-(1 << 31) <= record.teacher_score_value < (1 << 31), "teacher value out of range")
    require(0 <= record.search_nodes <= 0xFFFFFFFFFFFFFFFF, "search nodes out of range")
    require(0 <= record.search_depth <= 0xFFFF and 0 <= record.search_seldepth <= 0xFFFF, "search depth out of range")
    require(0 <= record.move_time_ms <= 0xFFFFFFFF, "move time out of range")
    for label, value, width in (
        ("position identity", record.position_identity_sha256, 32),
        ("history prefix", record.history_prefix_sha256, 32),
        ("provenance", record.provenance_sha256, 32),
    ):
        require(isinstance(value, bytes) and len(value) == width and any(value), f"{label} digest invalid")
    if recompute_position:
        expected_position = position_identity(
            record.board,
            record.side_to_move,
            record.castling_rights,
            record.effective_en_passant_square,
            record.pockets,
            record.promoted_mask,
        )
        require(record.position_identity_sha256 == expected_position, "position identity digest mismatch")
    if record.terminal_reason == TERMINAL_FIVEFOLD:
        require(record.repetition_occurrences == 5, "fivefold terminal requires five occurrences")
    if record.terminal_reason == TERMINAL_THREEFOLD_PROXY:
        require(record.claim_policy == CLAIM_IMMEDIATE_THREEFOLD and record.repetition_occurrences >= 3, "threefold proxy terminal mismatch")
    if record.terminal_reason == TERMINAL_CHECKMATE:
        require(record.result_side_to_move == -1, "checkmate terminal side must lose")
    if record.terminal_reason in {
        TERMINAL_STALEMATE,
        TERMINAL_FIVEFOLD,
        TERMINAL_THREEFOLD_PROXY,
        TERMINAL_DRAW_ADJUDICATION,
    }:
        require(record.result_side_to_move == 0, "draw terminal reason requires a draw result")
    if not record.flags & FLAG_NONSTANDARD_ROOT:
        promoted_count = record.promoted_mask.bit_count()
        board_type_count = lambda piece_type, exclude_promoted=False: sum(
            1
            for square, code in enumerate(record.board)
            if code and (code & 7) == piece_type and (not exclude_promoted or not record.promoted_mask & (1 << square))
        )
        require(
            board_type_count(PIECE_PAWN) + record.pockets[0] + record.pockets[5] + promoted_count == 16,
            "standard-root pawn-origin conservation failed",
        )
        for piece_type, pocket_offset, expected in (
            (PIECE_KNIGHT, 1, 4),
            (PIECE_BISHOP, 2, 4),
            (PIECE_ROOK, 3, 4),
            (PIECE_QUEEN, 4, 2),
        ):
            require(
                board_type_count(piece_type, exclude_promoted=True)
                + record.pockets[pocket_offset]
                + record.pockets[pocket_offset + 5]
                == expected,
                "standard-root non-pawn origin conservation failed",
            )


def validate_move(move: MoveWire) -> None:
    require(move.kind in {MOVE_NONE, MOVE_NORMAL, MOVE_PROMOTION, MOVE_EN_PASSANT, MOVE_CASTLING, MOVE_DROP}, "unknown move kind")
    if move.kind == MOVE_NONE:
        require((move.from_square, move.to_square, move.aux_piece) == (NO_SQUARE, NO_SQUARE, PIECE_NONE), "none move payload drifted")
    elif move.kind == MOVE_DROP:
        require(move.from_square == NO_SQUARE and 0 <= move.to_square < 64, "drop square payload invalid")
        require(move.aux_piece in {PIECE_PAWN, PIECE_KNIGHT, PIECE_BISHOP, PIECE_ROOK, PIECE_QUEEN}, "drop piece invalid")
        if move.aux_piece == PIECE_PAWN:
            require(move.to_square // 8 not in {0, 7}, "pawn drop targets a forbidden rank")
    else:
        require(0 <= move.from_square < 64 and 0 <= move.to_square < 64 and move.from_square != move.to_square, "board move squares invalid")
        if move.kind == MOVE_PROMOTION:
            require(move.aux_piece in {PIECE_KNIGHT, PIECE_BISHOP, PIECE_ROOK, PIECE_QUEEN}, "promotion piece invalid")
        else:
            require(move.aux_piece == PIECE_NONE, "nonpromotion board move has aux piece")


def build_record(
    *,
    sequence: int,
    game_id: bytes,
    trajectory_id: bytes,
    ply: int,
    fen: str,
    effective_en_passant_square: int,
    repetition_occurrences: int,
    claim_policy: int,
    terminal_reason: int,
    move: MoveWire,
    game_result_white: int,
    provenance_sha256: bytes,
    previous_history_sha256: bytes | None,
    teacher_score_kind: int = TEACHER_NONE,
    teacher_score_value: int = 0,
    teacher_bound: int = BOUND_NONE,
    search_nodes: int = 0,
    search_depth: int = 0,
    search_seldepth: int = 0,
    move_time_ms: int = 0,
    teacher_used_network: bool = False,
    augmented: bool = False,
    nonstandard_root: bool = False,
) -> PhysicalRecord:
    state = parse_fen(fen)
    flags = FLAG_TRAJECTORY_START if ply == 0 else 0
    if move.kind != MOVE_NONE:
        flags |= FLAG_MOVE_PRESENT
    if terminal_reason != TERMINAL_ONGOING:
        flags |= FLAG_TERMINAL
    if teacher_score_kind != TEACHER_NONE:
        flags |= FLAG_TEACHER_PRESENT
    if teacher_used_network:
        flags |= FLAG_TEACHER_NETWORK
    if augmented:
        flags |= FLAG_AUGMENTED
    if nonstandard_root:
        flags |= FLAG_NONSTANDARD_ROOT
    position_sha = position_identity(
        state.board,
        state.side_to_move,
        state.castling_rights,
        effective_en_passant_square,
        state.pockets,
        state.promoted_mask,
    )
    previous = previous_history_sha256
    if ply == 0:
        require(previous is None, "ply zero must not supply previous history")
        previous = history_initial(trajectory_id, provenance_sha256)
    else:
        require(previous is not None and len(previous) == 32, "nonzero ply requires previous history")
    record = PhysicalRecord(
        sequence=sequence,
        game_id=game_id,
        trajectory_id=trajectory_id,
        ply=ply,
        flags=flags,
        board=state.board,
        promoted_mask=state.promoted_mask,
        pockets=state.pockets,
        side_to_move=state.side_to_move,
        castling_rights=state.castling_rights,
        raw_en_passant_square=state.raw_en_passant_square,
        effective_en_passant_square=effective_en_passant_square,
        repetition_occurrences=repetition_occurrences,
        claim_policy=claim_policy,
        terminal_reason=terminal_reason,
        halfmove_clock=state.halfmove_clock,
        fullmove_number=state.fullmove_number,
        move=move,
        game_result_white=game_result_white,
        result_side_to_move=game_result_white if state.side_to_move == SIDE_WHITE else -game_result_white,
        teacher_score_kind=teacher_score_kind,
        teacher_bound=teacher_bound,
        teacher_score_value=teacher_score_value,
        search_nodes=search_nodes,
        search_depth=search_depth,
        search_seldepth=search_seldepth,
        move_time_ms=move_time_ms,
        position_identity_sha256=position_sha,
        history_prefix_sha256=history_step(previous, ply, position_sha, move),
        provenance_sha256=provenance_sha256,
    )
    validate_record(record)
    return record


def encode_record(record: PhysicalRecord) -> bytes:
    validate_record(record)
    output = bytearray(RECORD_SIZE)
    output[0:4] = RECORD_MAGIC
    struct.pack_into("<HHQ", output, 4, SCHEMA_MAJOR, RECORD_SIZE, record.sequence)
    output[16:32] = record.game_id
    output[32:48] = record.trajectory_id
    struct.pack_into("<II", output, 48, record.ply, record.flags)
    output[56:88] = pack_board(record.board)
    struct.pack_into("<Q", output, 88, record.promoted_mask)
    output[96:106] = bytes(record.pockets)
    output[106:112] = bytes(
        (
            record.side_to_move,
            record.castling_rights,
            record.raw_en_passant_square,
            record.repetition_occurrences,
            record.claim_policy,
            record.terminal_reason,
        )
    )
    struct.pack_into("<II", output, 112, record.halfmove_clock, record.fullmove_number)
    output[120:124] = record.move.bytes()
    struct.pack_into(
        "<bbBBiQHHI",
        output,
        124,
        record.game_result_white,
        record.result_side_to_move,
        record.teacher_score_kind,
        record.teacher_bound,
        record.teacher_score_value,
        record.search_nodes,
        record.search_depth,
        record.search_seldepth,
        record.move_time_ms,
    )
    output[148:180] = record.position_identity_sha256
    output[180:212] = record.history_prefix_sha256
    output[212:244] = record.provenance_sha256
    output[244] = record.effective_en_passant_square
    struct.pack_into("<I", output, 252, crc32c(output[:252]))
    return bytes(output)


def decode_record(payload: bytes) -> PhysicalRecord:
    require(len(payload) == RECORD_SIZE, "record size mismatch")
    require(payload[:4] == RECORD_MAGIC, "record magic mismatch")
    require(struct.unpack_from("<I", payload, 252)[0] == crc32c(payload[:252]), "record CRC32C mismatch")
    require(payload[245:252] == bytes(7), "record reserved bytes are nonzero")
    schema_major, record_size, sequence = struct.unpack_from("<HHQ", payload, 4)
    require(schema_major == SCHEMA_MAJOR and record_size == RECORD_SIZE, "record version/size mismatch")
    ply, flags = struct.unpack_from("<II", payload, 48)
    promoted = struct.unpack_from("<Q", payload, 88)[0]
    halfmove, fullmove = struct.unpack_from("<II", payload, 112)
    white_result, stm_result, teacher_kind, teacher_bound, teacher_value, nodes, depth, seldepth, move_time = struct.unpack_from(
        "<bbBBiQHHI", payload, 124
    )
    record = PhysicalRecord(
        sequence=sequence,
        game_id=bytes(payload[16:32]),
        trajectory_id=bytes(payload[32:48]),
        ply=ply,
        flags=flags,
        board=unpack_board(payload[56:88]),
        promoted_mask=promoted,
        pockets=tuple(payload[96:106]),
        side_to_move=payload[106],
        castling_rights=payload[107],
        raw_en_passant_square=payload[108],
        effective_en_passant_square=payload[244],
        repetition_occurrences=payload[109],
        claim_policy=payload[110],
        terminal_reason=payload[111],
        halfmove_clock=halfmove,
        fullmove_number=fullmove,
        move=MoveWire(*payload[120:124]),
        game_result_white=white_result,
        result_side_to_move=stm_result,
        teacher_score_kind=teacher_kind,
        teacher_bound=teacher_bound,
        teacher_score_value=teacher_value,
        search_nodes=nodes,
        search_depth=depth,
        search_seldepth=seldepth,
        move_time_ms=move_time,
        position_identity_sha256=bytes(payload[148:180]),
        history_prefix_sha256=bytes(payload[180:212]),
        provenance_sha256=bytes(payload[212:244]),
    )
    validate_record(record)
    return record


def _build_header(
    *,
    record_count: int,
    chunk_id: bytes,
    campaign_id: bytes,
    schema_sha256: bytes,
    provenance_sha256: bytes,
    payload_sha256: bytes,
    producer_capability_sha256: bytes,
) -> bytes:
    output = bytearray(HEADER_SIZE)
    output[:16] = HEADER_MAGIC
    struct.pack_into(
        "<IHHHHH",
        output,
        16,
        BYTE_ORDER_MARKER,
        HEADER_SIZE,
        RECORD_SIZE,
        FOOTER_SIZE,
        SCHEMA_MAJOR,
        SCHEMA_MINOR,
    )
    struct.pack_into("<I", output, 32, COMMITTED)
    struct.pack_into("<Q", output, 40, record_count)
    output[48:64] = chunk_id
    output[64:80] = campaign_id
    output[80:112] = RULE_PROFILE_SHA256
    output[112:144] = schema_sha256
    output[144:176] = provenance_sha256
    output[176:208] = payload_sha256
    output[208:240] = producer_capability_sha256
    struct.pack_into("<I", output, 252, crc32c(output[:252]))
    return bytes(output)


def _build_footer(*, record_count: int, payload_sha256: bytes, header_sha256: bytes, chunk_id: bytes) -> bytes:
    output = bytearray(FOOTER_SIZE)
    output[:16] = FOOTER_MAGIC
    struct.pack_into("<HHIQQ", output, 16, FOOTER_SIZE, SCHEMA_MAJOR, COMMITTED, record_count, record_count * RECORD_SIZE)
    output[40:72] = payload_sha256
    output[72:104] = header_sha256
    output[104:120] = chunk_id
    struct.pack_into("<I", output, 124, crc32c(output[:124]))
    return bytes(output)


@dataclass(frozen=True)
class Chunk:
    header: bytes
    records: tuple[PhysicalRecord, ...]
    footer: bytes
    payload_sha256: bytes
    schema_sha256: bytes
    provenance_sha256: bytes
    producer_capability_sha256: bytes
    chunk_id: bytes
    campaign_id: bytes


def _validate_trajectory_sequence(
    records: Sequence[PhysicalRecord],
    *,
    provenance_sha256: bytes,
    teacher_network_used: bool,
    decoded: bool = False,
) -> None:
    prefix = "decoded " if decoded else ""
    previous_by_trajectory: dict[bytes, bytes] = {}
    next_ply_by_trajectory: dict[bytes, int] = {}
    occurrences_by_trajectory: dict[bytes, dict[bytes, int]] = {}
    constants_by_trajectory: dict[bytes, tuple[bytes, int, int, bool]] = {}
    closed_trajectories: set[bytes] = set()
    terminal_trajectories: set[bytes] = set()
    active_trajectory: bytes | None = None
    for index, record in enumerate(records):
        require(record.sequence == index, f"{prefix}record sequence is not contiguous")
        require(record.provenance_sha256 == provenance_sha256, f"{prefix}record provenance mismatch")
        if record.flags & FLAG_TEACHER_PRESENT:
            require(
                bool(record.flags & FLAG_TEACHER_NETWORK) == teacher_network_used,
                f"{prefix}record teacher/network provenance mismatch",
            )
        if active_trajectory is not None and record.trajectory_id != active_trajectory:
            closed_trajectories.add(active_trajectory)
        require(record.trajectory_id not in closed_trajectories, f"{prefix}trajectory records are not one contiguous block")
        require(record.trajectory_id not in terminal_trajectories, f"{prefix}trajectory continues after a terminal record")
        active_trajectory = record.trajectory_id
        expected_ply = next_ply_by_trajectory.get(record.trajectory_id, 0)
        require(record.ply == expected_ply, f"{prefix}trajectory records are not contiguous from ply zero")
        previous = previous_by_trajectory.get(record.trajectory_id)
        if record.ply == 0:
            previous = history_initial(record.trajectory_id, provenance_sha256)
            require(record.repetition_occurrences == 1, f"{prefix}trajectory root contains hidden repetition history")
            constants_by_trajectory[record.trajectory_id] = (
                record.game_id,
                record.game_result_white,
                record.claim_policy,
                bool(record.flags & FLAG_AUGMENTED),
            )
            occurrences_by_trajectory[record.trajectory_id] = {}
        else:
            require(
                constants_by_trajectory[record.trajectory_id]
                == (
                    record.game_id,
                    record.game_result_white,
                    record.claim_policy,
                    bool(record.flags & FLAG_AUGMENTED),
                ),
                f"{prefix}trajectory identity/result/policy drifted",
            )
        require(previous is not None, f"{prefix}trajectory history is missing")
        expected_history = history_step(previous, record.ply, record.position_identity_sha256, record.move)
        require(record.history_prefix_sha256 == expected_history, f"{prefix}trajectory history prefix mismatch")
        occurrences = occurrences_by_trajectory[record.trajectory_id]
        observed_occurrences = occurrences.get(record.position_identity_sha256, 0) + 1
        occurrences[record.position_identity_sha256] = observed_occurrences
        require(
            record.repetition_occurrences == observed_occurrences,
            f"{prefix}repetition occurrence count does not match physical history",
        )
        previous_by_trajectory[record.trajectory_id] = record.history_prefix_sha256
        next_ply_by_trajectory[record.trajectory_id] = record.ply + 1
        if record.flags & FLAG_TERMINAL:
            terminal_trajectories.add(record.trajectory_id)
    require(
        set(next_ply_by_trajectory) == terminal_trajectories,
        f"{prefix}chunk contains a trajectory without a terminal record",
    )


def build_chunk(
    records: Sequence[PhysicalRecord],
    *,
    schema_bytes: bytes,
    provenance_bytes: bytes,
    producer_capability_sha256: bytes,
    chunk_id: bytes,
    campaign_id: bytes,
) -> bytes:
    require(records, "empty chunk is invalid")
    validate_schema_bytes(schema_bytes)
    _validate_uuid_bytes(chunk_id, "chunk_id")
    _validate_uuid_bytes(campaign_id, "campaign_id")
    require(len(producer_capability_sha256) == 32 and any(producer_capability_sha256), "producer capability digest invalid")
    provenance_digest = sha256(provenance_bytes)
    encoded: list[bytes] = []
    provenance_document = validate_provenance_bytes(
        provenance_bytes, chunk_id=chunk_id, campaign_id=campaign_id
    )
    require(
        provenance_document["producer_capability"]["sha256"]
        == producer_capability_sha256.hex(),
        "producer capability/header mismatch",
    )
    expected_network_teacher = provenance_document["teacher"]["network_used"]
    _validate_trajectory_sequence(
        records,
        provenance_sha256=provenance_digest,
        teacher_network_used=expected_network_teacher,
    )
    for record in records:
        encoded.append(encode_record(record))
    payload = b"".join(encoded)
    payload_digest = sha256(payload)
    schema_digest = sha256(schema_bytes)
    header = _build_header(
        record_count=len(records),
        chunk_id=chunk_id,
        campaign_id=campaign_id,
        schema_sha256=schema_digest,
        provenance_sha256=provenance_digest,
        payload_sha256=payload_digest,
        producer_capability_sha256=producer_capability_sha256,
    )
    footer = _build_footer(
        record_count=len(records),
        payload_sha256=payload_digest,
        header_sha256=sha256(header),
        chunk_id=chunk_id,
    )
    return header + payload + footer


def parse_chunk(payload: bytes, *, schema_bytes: bytes, provenance_bytes: bytes) -> Chunk:
    require(len(payload) >= HEADER_SIZE + RECORD_SIZE + FOOTER_SIZE, "chunk is empty or truncated")
    validate_schema_bytes(schema_bytes)
    header = payload[:HEADER_SIZE]
    footer = payload[-FOOTER_SIZE:]
    require(header[:16] == HEADER_MAGIC, "header magic mismatch")
    require(footer[:16] == FOOTER_MAGIC, "footer magic mismatch")
    require(struct.unpack_from("<I", header, 252)[0] == crc32c(header[:252]), "header CRC32C mismatch")
    require(struct.unpack_from("<I", footer, 124)[0] == crc32c(footer[:124]), "footer CRC32C mismatch")
    require(header[30:32] == bytes(2) and header[36:40] == bytes(4) and header[240:252] == bytes(12), "header reserved bytes are nonzero")
    require(footer[120:124] == bytes(4), "footer reserved bytes are nonzero")
    marker, header_size, record_size, footer_size, major, minor = struct.unpack_from("<IHHHHH", header, 16)
    require((marker, header_size, record_size, footer_size, major, minor) == (BYTE_ORDER_MARKER, HEADER_SIZE, RECORD_SIZE, FOOTER_SIZE, SCHEMA_MAJOR, SCHEMA_MINOR), "header layout/version mismatch")
    header_flags = struct.unpack_from("<I", header, 32)[0]
    record_count = struct.unpack_from("<Q", header, 40)[0]
    require(header_flags == COMMITTED, "header is not committed or has unknown flags")
    footer_size_seen, footer_major, footer_flags, footer_count, footer_payload_bytes = struct.unpack_from("<HHIQQ", footer, 16)
    require((footer_size_seen, footer_major, footer_flags) == (FOOTER_SIZE, SCHEMA_MAJOR, COMMITTED), "footer layout/version/flags mismatch")
    require(record_count == footer_count and footer_payload_bytes == record_count * RECORD_SIZE, "header/footer count mismatch")
    expected_size = HEADER_SIZE + record_count * RECORD_SIZE + FOOTER_SIZE
    require(len(payload) == expected_size, "chunk exact framing mismatch")
    records_bytes = payload[HEADER_SIZE:-FOOTER_SIZE]
    payload_digest = sha256(records_bytes)
    require(header[176:208] == payload_digest and footer[40:72] == payload_digest, "payload SHA-256 mismatch")
    require(footer[72:104] == sha256(header), "footer/header SHA-256 mismatch")
    require(header[48:64] == footer[104:120], "chunk id mismatch")
    _validate_uuid_bytes(bytes(header[48:64]), "header chunk_id")
    _validate_uuid_bytes(bytes(header[64:80]), "header campaign_id")
    schema_digest = sha256(schema_bytes)
    provenance_digest = sha256(provenance_bytes)
    provenance_document = validate_provenance_bytes(
        provenance_bytes,
        chunk_id=bytes(header[48:64]),
        campaign_id=bytes(header[64:80]),
    )
    require(
        provenance_document["producer_capability"]["sha256"]
        == bytes(header[208:240]).hex(),
        "decoded producer capability/provenance mismatch",
    )
    require(header[80:112] == RULE_PROFILE_SHA256, "rule profile digest mismatch")
    require(header[112:144] == schema_digest, "schema digest mismatch")
    require(header[144:176] == provenance_digest, "provenance digest mismatch")
    producer_digest = bytes(header[208:240])
    require(any(producer_digest), "producer capability digest is zero")
    records = tuple(
        decode_record(records_bytes[offset : offset + RECORD_SIZE])
        for offset in range(0, len(records_bytes), RECORD_SIZE)
    )
    _validate_trajectory_sequence(
        records,
        provenance_sha256=provenance_digest,
        teacher_network_used=provenance_document["teacher"]["network_used"],
        decoded=True,
    )
    return Chunk(
        header=header,
        records=records,
        footer=footer,
        payload_sha256=payload_digest,
        schema_sha256=schema_digest,
        provenance_sha256=provenance_digest,
        producer_capability_sha256=producer_digest,
        chunk_id=bytes(header[48:64]),
        campaign_id=bytes(header[64:80]),
    )


def _flip_mask(mask: int) -> int:
    output = 0
    for square in range(64):
        if mask & (1 << square):
            output |= 1 << (square ^ 56)
    return output


def reflect_rank_color_swap(record: PhysicalRecord, *, previous_history_sha256: bytes | None = None) -> PhysicalRecord:
    board = [0] * 64
    for square, code in enumerate(record.board):
        board[square ^ 56] = 0 if code == 0 else code ^ 8
    pockets = tuple(record.pockets[5:10] + record.pockets[0:5])
    raw_ep = NO_SQUARE if record.raw_en_passant_square == NO_SQUARE else record.raw_en_passant_square ^ 56
    effective_ep = NO_SQUARE if record.effective_en_passant_square == NO_SQUARE else record.effective_en_passant_square ^ 56
    rights = ((record.castling_rights & 0b0011) << 2) | ((record.castling_rights & 0b1100) >> 2)
    move = record.move
    if move.kind == MOVE_DROP:
        move = MoveWire(move.kind, NO_SQUARE, move.to_square ^ 56, move.aux_piece)
    elif move.kind != MOVE_NONE:
        move = MoveWire(move.kind, move.from_square ^ 56, move.to_square ^ 56, move.aux_piece)
    position_sha = position_identity(board, record.side_to_move ^ 1, rights, effective_ep, pockets, _flip_mask(record.promoted_mask))
    previous = previous_history_sha256
    if record.ply == 0:
        require(previous is None, "reflected ply zero must not receive previous history")
        previous = history_initial(record.trajectory_id, record.provenance_sha256)
    else:
        require(previous is not None, "reflected nonzero ply requires transformed previous history")
    reflected = replace(
        record,
        board=tuple(board),
        promoted_mask=_flip_mask(record.promoted_mask),
        pockets=pockets,
        side_to_move=record.side_to_move ^ 1,
        castling_rights=rights,
        raw_en_passant_square=raw_ep,
        effective_en_passant_square=effective_ep,
        move=move,
        game_result_white=-record.game_result_white,
        position_identity_sha256=position_sha,
        history_prefix_sha256=history_step(previous, record.ply, position_sha, move),
    )
    validate_record(reflected)
    return reflected


def uuid_bytes(text: str) -> bytes:
    return uuid.UUID(text).bytes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    payload, _ = load_schema(args.schema)
    print(f"PASS_CRAZYHOUSE_PHYSICAL_V1_SCHEMA sha256={sha256(payload).hex()} record_size={RECORD_SIZE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
