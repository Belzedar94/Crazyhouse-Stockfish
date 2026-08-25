#!/usr/bin/env python3
"""Independent verifier for a Crazyhouse live-search DATAGEN G0 bundle.

Only the Python standard library is used.  The verifier imports neither the
producer, the reference physical codec, nor the formal harness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
from typing import Any, Iterable, Mapping, Sequence
import uuid


BUNDLE_HEADER_BYTES = 256
BUNDLE_FOOTER_BYTES = 128
PHYSICAL_HEADER_BYTES = 256
RECORD_BYTES = 256
PHYSICAL_FOOTER_BYTES = 128

BUNDLE_HEADER_MAGIC = b"CHBNDLV1" + bytes(8)
BUNDLE_FOOTER_MAGIC = b"CHBNDENDV1" + bytes(6)
PHYSICAL_HEADER_MAGIC = b"CHPHYSV1" + bytes(8)
RECORD_MAGIC = b"CHR1"
PHYSICAL_FOOTER_MAGIC = b"CHPHYSENDV1" + bytes(5)

RULE_PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
RULE_PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PHYSICAL_SCHEMA_SHA256 = "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
SELFPLAY_CONTRACT_SHA256 = "482fd210ed4009aaf145c34d44b18fc05f99b11969e69dd9f69d9907204c87dd"
BUNDLE_SCHEMA_SHA256 = "27138d4049e2c6b2ad75f85d05fc799442cbf9f91a6e4a1c27c546c2eb9ecf5b"
BOOK_SHA256 = "f99f8211316813924e52fb13fbb65a5bc27dcd585e2e32a86d90db0d113fd2f6"
BOOK_BYTES = 158
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
NETWORK_BYTES = 58_534_811
SELECTION_POLICY_SHA256 = "fc67430cb09eb28531889a6b8f99a02f4b033c5bd71cbef7d2e9add8a7d573c6"

CAMPAIGN_ID = "42e04e75-21bb-5e7f-8617-54e5bc72b5a3"
BASE_SEED = 8_964_207_305_086_120_581
ASSIGNED_SEED = BASE_SEED
CHUNK_INDEX = 0
EXPECTED_RECORDS = 4
EXPECTED_TRAJECTORIES = 2

POSITION_DOMAIN = b"Crazyhouse-Stockfish physical repetition identity v1\0"
HISTORY_INITIAL_DOMAIN = b"Crazyhouse-Stockfish physical history initial v1\0"
HISTORY_STEP_DOMAIN = b"Crazyhouse-Stockfish physical history step v1\0"
IDENTITY_DOMAIN = b"Crazyhouse-Stockfish selfplay deterministic identity v1\0"
CHALLENGE_DOMAIN = b"Crazyhouse-Stockfish selfplay capability challenge v1\0"
BOOK_ORDER_DOMAIN = b"Crazyhouse-Stockfish selfplay book order v1\0"

NO_SQUARE = 255
POCKET_MAX = (16, 4, 4, 4, 2, 16, 4, 4, 4, 2)
PIECE_CODES = {
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


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def sha256_file(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in output, f"duplicate JSON key {key!r}")
        output[key] = value
    return output


def reject_constant(value: str) -> None:
    raise VerificationError(f"non-finite JSON number {value!r}")


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def parse_json_bytes(payload: bytes, label: str, *, canonical: bool) -> Mapping[str, Any]:
    require(not payload.startswith(b"\xef\xbb\xbf"), f"{label}: BOM")
    require(b"\r" not in payload and b"\0" not in payload, f"{label}: CR or NUL")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), f"{label}: LF framing")
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label}: malformed JSON: {exc}") from exc
    require(isinstance(document, dict), f"{label}: root is not an object")
    if canonical:
        require(payload == canonical_json(document), f"{label}: noncanonical JSON")
    return document


def load_json(path: Path, label: str, *, canonical: bool) -> tuple[bytes, Mapping[str, Any]]:
    payload = path.read_bytes()
    return payload, parse_json_bytes(payload, label, canonical=canonical)


def lowercase_hex(value: Any, width: int, label: str) -> None:
    require(isinstance(value, str) and len(value) == width and value == value.lower(), f"{label}: width/case")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise VerificationError(f"{label}: non-hex") from exc


def safe_repo_path(value: Any, label: str) -> None:
    require(isinstance(value, str) and value, f"{label}: empty path")
    require("\\" not in value and ":" not in value and not value.startswith("/"), f"{label}: not repository-relative POSIX")
    require(all(part not in {"", ".", ".."} for part in value.split("/")), f"{label}: unsafe component")


def derive_id(kind: str, candidate_index: int) -> bytes:
    campaign = uuid.UUID(CAMPAIGN_ID).bytes
    payload = (
        IDENTITY_DOMAIN
        + kind.encode("ascii")
        + b"\0"
        + campaign
        + struct.pack("<QQ", CHUNK_INDEX, candidate_index)
    )
    output = bytearray(sha256(payload)[:16])
    output[6] = (output[6] & 0x0F) | 0x50
    output[8] = (output[8] & 0x3F) | 0x80
    return bytes(output)


def derive_challenge(producer_digest: bytes) -> str:
    payload = (
        CHALLENGE_DOMAIN
        + uuid.UUID(CAMPAIGN_ID).bytes
        + derive_id("chunk", 0)
        + struct.pack("<Q", ASSIGNED_SEED)
        + producer_digest
    )
    return sha256(payload).hex()[:32]


def parse_square(value: str) -> int:
    require(len(value) == 2 and "a" <= value[0] <= "h" and "1" <= value[1] <= "8", f"invalid square {value!r}")
    return (ord(value[1]) - ord("1")) * 8 + ord(value[0]) - ord("a")


def unpack_board(packed: bytes) -> tuple[int, ...]:
    require(len(packed) == 32, "packed board width")
    board: list[int] = []
    for byte in packed:
        board.extend((byte & 0x0F, byte >> 4))
    require(not any(piece in {7, 8, 15} for piece in board), "reserved board code")
    return tuple(board)


def parse_book(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    payload = path.read_bytes()
    require(len(payload) == BOOK_BYTES and sha256(payload).hex() == BOOK_SHA256, "frozen book identity")
    require(b"\r" not in payload and b"\0" not in payload and payload.endswith(b"\n") and not payload.endswith(b"\n\n"), "book framing")
    pattern = re.compile(
        r'^(\S+) ([wb]) (\S+) (\S+) hmvc ([0-9]+); fmvn ([0-9]+); id "([^"\\]+)";$'
    )
    roots: list[dict[str, Any]] = []
    for index, line in enumerate(payload[:-1].decode("ascii").split("\n")):
        match = pattern.fullmatch(line)
        require(match is not None, f"book row {index}: EPD framing")
        placement, side, castling, ep, halfmove, fullmove, root_id = match.groups()
        fen = f"{placement} {side} {castling} {ep} {halfmove} {fullmove}"
        roots.append({"index": index, "line": line, "id": root_id, "fen": fen})
    require([root["id"] for root in roots] == ["CHDG-G0-0001", "CHDG-G0-0002"], "book root IDs")
    ranked = sorted(
        roots,
        key=lambda root: (
            sha256(
                BOOK_ORDER_DOMAIN
                + struct.pack("<QQ", ASSIGNED_SEED, root["index"])
                + root["line"].encode("ascii")
            ),
            root["index"],
        ),
    )
    require([root["id"] for root in ranked] == ["CHDG-G0-0002", "CHDG-G0-0001"], "deterministic book order")
    return payload, ranked


def parse_fen(fen: str) -> dict[str, Any]:
    fields = fen.split()
    require(len(fields) == 6, "FEN field count")
    placement = fields[0]
    pocket_text = ""
    if "[" in placement:
        placement, suffix = placement.split("[", 1)
        require(suffix.endswith("]"), "FEN pocket framing")
        pocket_text = suffix[:-1]
    ranks = placement.split("/")
    require(len(ranks) == 8, "FEN rank count")
    board = [0] * 64
    promoted = 0
    for rank_index, rank_text in enumerate(ranks):
        file_index = 0
        index = 0
        while index < len(rank_text):
            token = rank_text[index]
            if token.isdigit():
                file_index += int(token)
            else:
                require(token in PIECE_CODES and file_index < 8, "FEN board token")
                square = (7 - rank_index) * 8 + file_index
                board[square] = PIECE_CODES[token]
                file_index += 1
                if index + 1 < len(rank_text) and rank_text[index + 1] == "~":
                    promoted |= 1 << square
                    index += 1
            index += 1
        require(file_index == 8, "FEN rank width")
    pockets = [0] * 10
    pocket_index = {"P": 0, "N": 1, "B": 2, "R": 3, "Q": 4, "p": 5, "n": 6, "b": 7, "r": 8, "q": 9}
    for token in pocket_text:
        require(token in pocket_index, "FEN pocket piece")
        pockets[pocket_index[token]] += 1
    rights = 0
    if fields[2] != "-":
        for token in fields[2]:
            require(token in "KQkq", "FEN castling token")
            rights |= {"K": 1, "Q": 2, "k": 4, "q": 8}[token]
    return {
        "board": board,
        "promoted": promoted,
        "pockets": pockets,
        "side": 0 if fields[1] == "w" else 1,
        "rights": rights,
        "raw_ep": NO_SQUARE if fields[3] == "-" else parse_square(fields[3]),
        "halfmove": int(fields[4]),
        "fullmove": int(fields[5]),
    }


def apply_expected_move(state: Mapping[str, Any], token: str) -> dict[str, Any]:
    output: dict[str, Any] = {
        key: list(value) if isinstance(value, list) else value for key, value in state.items()
    }
    side = int(output["side"])
    board = output["board"]
    pockets = output["pockets"]
    require(isinstance(board, list) and isinstance(pockets, list), "mutable expected state")
    if "@" in token:
        piece_text, target_text = token.split("@", 1)
        piece_type = {"P": 1, "N": 2, "B": 3, "R": 4, "Q": 5}[piece_text.upper()]
        target = parse_square(target_text.lower())
        require(board[target] == 0 and pockets[side * 5 + piece_type - 1] > 0, "expected drop legality")
        pockets[side * 5 + piece_type - 1] -= 1
        board[target] = piece_type | (8 if side else 0)
    else:
        source, target = parse_square(token[:2]), parse_square(token[2:4])
        require(board[source] != 0 and board[source] >> 3 == side, "expected move source")
        board[target] = board[source]
        board[source] = 0
    output["halfmove"] = int(output["halfmove"]) + 1
    if side == 1:
        output["fullmove"] = int(output["fullmove"]) + 1
    output["side"] = 1 - side
    output["raw_ep"] = NO_SQUARE
    return output


def position_digest(record: bytes) -> bytes:
    return sha256(
        POSITION_DOMAIN
        + record[56:88]
        + bytes((record[106], record[107], record[244]))
        + record[96:106]
        + record[88:96]
    )


def validate_record_basics(record: bytes, sequence: int) -> tuple[int, ...]:
    require(len(record) == RECORD_BYTES, f"record {sequence}: width")
    require(record[:4] == RECORD_MAGIC, f"record {sequence}: magic")
    require(struct.unpack_from("<HHQ", record, 4) == (1, RECORD_BYTES, sequence), f"record {sequence}: envelope")
    require(struct.unpack_from("<I", record, 252)[0] == crc32c(record[:252]), f"record {sequence}: CRC32C")
    require(record[245:252] == bytes(7), f"record {sequence}: reserved")
    board = unpack_board(record[56:88])
    require(board.count(6) == 1 and board.count(14) == 1, f"record {sequence}: kings")
    require(not any((piece & 7) == 1 and square // 8 in {0, 7} for square, piece in enumerate(board) if piece), f"record {sequence}: pawn on promotion rank")
    promoted = struct.unpack_from("<Q", record, 88)[0]
    for square, piece in enumerate(board):
        if promoted & (1 << square):
            require(piece != 0 and piece & 7 not in {1, 6}, f"record {sequence}: promoted provenance")
    pockets = tuple(record[96:106])
    require(all(value <= maximum for value, maximum in zip(pockets, POCKET_MAX)), f"record {sequence}: pocket maximum")
    side, rights, raw_ep, repetition, claim, terminal = record[106:112]
    require(side in {0, 1} and rights <= 15, f"record {sequence}: side/rights")
    require(raw_ep in {*range(64), NO_SQUARE} and repetition in range(1, 6), f"record {sequence}: EP/repetition")
    require(claim in {0, 1} and terminal in range(7), f"record {sequence}: claim/terminal")
    effective_ep = record[244]
    require(effective_ep in {raw_ep, NO_SQUARE}, f"record {sequence}: effective EP")
    require(struct.unpack_from("<I", record, 116)[0] >= 1, f"record {sequence}: fullmove")
    flags = struct.unpack_from("<I", record, 52)[0]
    require(flags & ~0x7F == 0, f"record {sequence}: unknown flags")
    require(record[124] in {0, 1, 255} and record[125] in {0, 1, 255}, f"record {sequence}: result domain")
    require(record[126] in {0, 1, 2} and record[127] in {0, 1, 2, 3}, f"record {sequence}: teacher domain")
    require(record[148:180] == position_digest(record), f"record {sequence}: position identity")
    for bit, king_square, rook_square, king_code, rook_code in (
        (1, 4, 7, 6, 4),
        (2, 4, 0, 6, 4),
        (4, 60, 63, 14, 12),
        (8, 60, 56, 14, 12),
    ):
        if rights & bit:
            require(board[king_square] == king_code and board[rook_square] == rook_code, f"record {sequence}: castling pieces")
            require(not promoted & ((1 << king_square) | (1 << rook_square)), f"record {sequence}: castling provenance")
    return board


def compare_state(record: bytes, expected: Mapping[str, Any], sequence: int) -> None:
    require(unpack_board(record[56:88]) == tuple(expected["board"]), f"record {sequence}: board")
    require(struct.unpack_from("<Q", record, 88)[0] == expected["promoted"], f"record {sequence}: promoted")
    require(tuple(record[96:106]) == tuple(expected["pockets"]), f"record {sequence}: pockets")
    require(record[106] == expected["side"] and record[107] == expected["rights"], f"record {sequence}: side/rights")
    require(record[108] == expected["raw_ep"] and record[244] == NO_SQUARE, f"record {sequence}: EP")
    require(struct.unpack_from("<II", record, 112) == (expected["halfmove"], expected["fullmove"]), f"record {sequence}: counters")


def piece_side(piece: int) -> int:
    return 1 if piece >= 9 else 0


def square_xy(square: int) -> tuple[int, int]:
    return square % 8, square // 8


def attacked(board: Sequence[int], target: int, attacker: int) -> bool:
    tx, ty = square_xy(target)
    for source, piece in enumerate(board):
        if piece == 0 or piece_side(piece) != attacker:
            continue
        piece_type = piece & 7
        sx, sy = square_xy(source)
        dx, dy = tx - sx, ty - sy
        if piece_type == 1 and dy == (1 if attacker == 0 else -1) and abs(dx) == 1:
            return True
        if piece_type == 2 and (abs(dx), abs(dy)) in {(1, 2), (2, 1)}:
            return True
        if piece_type == 6 and max(abs(dx), abs(dy)) == 1:
            return True
        diagonal = abs(dx) == abs(dy) and dx != 0
        orthogonal = (dx == 0) != (dy == 0)
        if not ((piece_type in {3, 5} and diagonal) or (piece_type in {4, 5} and orthogonal)):
            continue
        step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
        step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
        x, y = sx + step_x, sy + step_y
        clear = True
        while (x, y) != (tx, ty):
            if board[y * 8 + x] != 0:
                clear = False
                break
            x += step_x
            y += step_y
        if clear:
            return True
    return False


def in_check(board: Sequence[int], side: int) -> bool:
    king_code = 6 | (8 if side else 0)
    kings = [square for square, piece in enumerate(board) if piece == king_code]
    require(len(kings) == 1, "check replay king count")
    return attacked(board, kings[0], 1 - side)


def ray_targets(board: Sequence[int], source: int, side: int, directions: Iterable[tuple[int, int]]) -> Iterable[int]:
    sx, sy = square_xy(source)
    for dx, dy in directions:
        x, y = sx + dx, sy + dy
        while 0 <= x < 8 and 0 <= y < 8:
            target = y * 8 + x
            piece = board[target]
            if piece == 0:
                yield target
            else:
                if piece_side(piece) != side and (piece & 7) != 6:
                    yield target
                break
            x += dx
            y += dy


def pseudo_targets(board: Sequence[int], source: int, side: int) -> Iterable[int]:
    piece_type = board[source] & 7
    sx, sy = square_xy(source)
    if piece_type == 1:
        direction = 1 if side == 0 else -1
        one_y = sy + direction
        if 0 <= one_y < 8 and board[one_y * 8 + sx] == 0:
            yield one_y * 8 + sx
            start_rank = 1 if side == 0 else 6
            two_y = sy + 2 * direction
            if sy == start_rank and board[two_y * 8 + sx] == 0:
                yield two_y * 8 + sx
        for dx in (-1, 1):
            x, y = sx + dx, sy + direction
            if 0 <= x < 8 and 0 <= y < 8:
                target = y * 8 + x
                if board[target] and piece_side(board[target]) != side and (board[target] & 7) != 6:
                    yield target
    elif piece_type == 2:
        for dx, dy in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
            x, y = sx + dx, sy + dy
            if 0 <= x < 8 and 0 <= y < 8:
                target = y * 8 + x
                if board[target] == 0 or (piece_side(board[target]) != side and (board[target] & 7) != 6):
                    yield target
    elif piece_type in {3, 4, 5}:
        directions: list[tuple[int, int]] = []
        if piece_type in {3, 5}:
            directions.extend(((1, 1), (1, -1), (-1, 1), (-1, -1)))
        if piece_type in {4, 5}:
            directions.extend(((1, 0), (-1, 0), (0, 1), (0, -1)))
        yield from ray_targets(board, source, side, directions)
    elif piece_type == 6:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                x, y = sx + dx, sy + dy
                if 0 <= x < 8 and 0 <= y < 8:
                    target = y * 8 + x
                    if board[target] == 0 or (piece_side(board[target]) != side and (board[target] & 7) != 6):
                        yield target


def has_legal_evasion(board: Sequence[int], pockets: Sequence[int], side: int) -> bool:
    for source, piece in enumerate(board):
        if piece == 0 or piece_side(piece) != side:
            continue
        for target in pseudo_targets(board, source, side):
            moved = list(board)
            moved[target] = moved[source]
            moved[source] = 0
            if not in_check(moved, side):
                return True
    for piece_type in range(1, 6):
        if pockets[side * 5 + piece_type - 1] == 0:
            continue
        for target, occupied in enumerate(board):
            if occupied or (piece_type == 1 and target // 8 in {0, 7}):
                continue
            moved = list(board)
            moved[target] = piece_type | (8 if side else 0)
            if not in_check(moved, side):
                return True
    return False


def validate_capability(
    payload: bytes,
    capability: Mapping[str, Any],
    contract: Mapping[str, Any],
    producer: Path,
    pins: Mapping[str, str],
) -> None:
    producer_bytes = producer.stat().st_size
    producer_digest = sha256_file(producer).hex()
    expected_keys = {
        "artifact_bytes", "artifact_role", "artifact_sha256", "build_recipe_sha256",
        "bundle_schema_sha256", "capability_contract_sha256", "challenge", "command",
        "complete_trajectory_only", "count_unit", "max_threads", "normal_engine_exposes_command",
        "physical_schema_id", "physical_schema_sha256", "project", "registered_network_bytes",
        "registered_network_sha256", "rule_profile_id", "rule_profile_sha256", "schema",
        "search_backend", "search_score_bound", "source_commit", "source_dirty", "source_tree",
        "src_tree", "teacher_score_kinds", "teacher_score_perspective", "toolchain_sha256",
        "transaction", "variant",
    }
    require(set(capability) == expected_keys, "capability key set")
    expected = {
        "artifact_bytes": producer_bytes,
        "artifact_role": "crazyhouse-physical-datagen-selfplay-v1",
        "artifact_sha256": producer_digest,
        "build_recipe_sha256": pins["build_recipe_sha256"],
        "bundle_schema_sha256": BUNDLE_SCHEMA_SHA256,
        "capability_contract_sha256": SELFPLAY_CONTRACT_SHA256,
        "challenge": derive_challenge(bytes.fromhex(producer_digest)),
        "command": "crazyhouse_generate_physical_v1",
        "complete_trajectory_only": True,
        "count_unit": "physical-records",
        "max_threads": 1,
        "normal_engine_exposes_command": False,
        "physical_schema_id": "crazyhouse-physical-v1",
        "physical_schema_sha256": PHYSICAL_SCHEMA_SHA256,
        "project": "Crazyhouse-Stockfish",
        "registered_network_bytes": NETWORK_BYTES,
        "registered_network_sha256": NETWORK_SHA256,
        "rule_profile_id": RULE_PROFILE_ID,
        "rule_profile_sha256": RULE_PROFILE_SHA256,
        "schema": "crazyhouse-datagen-selfplay-capability-response/v1",
        "search_backend": "product-crazyhouse-search",
        "search_score_bound": "exact-only",
        "source_commit": pins["source_commit"],
        "source_dirty": False,
        "source_tree": pins["source_tree"],
        "src_tree": pins["src_tree"],
        "teacher_score_kinds": ["centipawn", "mate-plies"],
        "teacher_score_perspective": "side-to-move",
        "toolchain_sha256": pins["toolchain_sha256"],
        "transaction": "exclusive-partial-verify-atomic-rename",
        "variant": "crazyhouse",
    }
    require(capability == expected, "capability exact bindings")
    response = contract.get("response")
    require(isinstance(response, dict), "capability contract response")
    required_exact = response.get("required_exact")
    require(isinstance(required_exact, dict), "capability contract exact fields")
    for key, value in required_exact.items():
        require(capability.get(key) == value, f"capability contract field {key}")
    require(payload == canonical_json(capability), "capability canonical bytes")


def validate_provenance(
    payload: bytes,
    provenance: Mapping[str, Any],
    capability_payload: bytes,
    capability: Mapping[str, Any],
    producer: Path,
    book: Path,
    network: Path,
    pins: Mapping[str, str],
) -> None:
    expected_keys = {
        "adjudication", "campaign_id", "chunk_id", "chunk_index", "generation_settings",
        "invalid_game_policy", "network", "opening_source", "producer_artifact",
        "producer_capability", "project", "rule_profile", "schema", "seed", "source_commit",
        "source_dirty", "source_tree", "src_tree", "teacher", "toolchain", "variant",
    }
    require(set(provenance) == expected_keys and payload == canonical_json(provenance), "provenance shape/canonical bytes")
    chunk_text = str(uuid.UUID(bytes=derive_id("chunk", 0)))
    require(provenance["campaign_id"] == CAMPAIGN_ID and provenance["chunk_id"] == chunk_text and provenance["chunk_index"] == 0, "provenance campaign/chunk")
    require(provenance["seed"] == str(ASSIGNED_SEED), "provenance seed")
    require(provenance["project"] == "Crazyhouse-Stockfish" and provenance["variant"] == "crazyhouse", "provenance project/variant")
    require(provenance["schema"] == "crazyhouse-datagen-provenance/v1", "provenance schema")
    require(provenance["rule_profile"] == {"id": RULE_PROFILE_ID, "sha256": RULE_PROFILE_SHA256}, "provenance rule profile")
    require(provenance["source_commit"] == pins["source_commit"] and provenance["source_tree"] == pins["source_tree"] and provenance["src_tree"] == pins["src_tree"] and provenance["source_dirty"] is False, "provenance source")
    require(provenance["adjudication"] == {"claim_policy": "automatic-only", "fivefold_automatic": True, "insufficient_material": False, "resignation": False, "rule50": False, "threefold_claim": False}, "provenance adjudication")
    require(provenance["generation_settings"] == {
        "accepted_trajectories": 2,
        "base_seed": BASE_SEED,
        "candidate_games_examined": 2,
        "complete_trajectory_only": True,
        "depth": 1,
        "exploration": False,
        "hash_mib": 16,
        "max_candidate_games": 2,
        "max_game_ply": 4,
        "multipv": 1,
        "nodes": 0,
        "nonstandard_root_policy": "g0-fixture-only",
        "record_count": 4,
        "threads": 1,
        "training_admissible": False,
        "wall_time_encoded": False,
    }, "provenance generation settings")
    require(provenance["invalid_game_policy"] == {
        "bound_or_missing_pv": "quarantine-game",
        "complete_trajectory_oversize": "quarantine-game",
        "crash": "abort-chunk",
        "illegal_move": "quarantine-game",
        "observed_rejections": [],
        "safety_limit": "quarantine-game",
        "unreachable_exact_quota": "abort-chunk",
    }, "provenance invalid-game policy")
    network_doc = provenance["network"]
    require(network_doc == {
        "bytes": NETWORK_BYTES,
        "compatibility": "qualified-positive-and-negative-load",
        "format": "legacy-halfkav2variants-v1",
        "license": "CC0-1.0",
        "path": network_doc.get("path") if isinstance(network_doc, dict) else None,
        "sha256": NETWORK_SHA256,
        "used": True,
    }, "provenance network")
    safe_repo_path(network_doc["path"], "provenance network path")
    require(Path(network_doc["path"]).name == network.name, "provenance network basename")
    opening = provenance["opening_source"]
    require(opening == {
        "artifact": {
            "bytes": BOOK_BYTES,
            "kind": "crazyhouse-epd-physical-roots-v1",
            "license": "GPL-3.0-or-later",
            "path": "tests/crazyhouse/data/crazyhouse-selfplay-g0-openings-v1.epd",
            "sha256": BOOK_SHA256,
        },
        "engine_selected": False,
        "kind": "deterministic-authenticated-book-order",
        "match_result_selected": False,
        "selection_policy_sha256": SELECTION_POLICY_SHA256,
    }, "provenance opening source")
    require(book.name == Path(opening["artifact"]["path"]).name, "provenance book basename")
    producer_digest = sha256_file(producer).hex()
    artifact = provenance["producer_artifact"]
    require(artifact["bytes"] == producer.stat().st_size and artifact["sha256"] == producer_digest and artifact["kind"] == "crazyhouse-physical-datagen-selfplay-v1", "provenance producer artifact")
    safe_repo_path(artifact["path"], "provenance producer path")
    capability_join = provenance["producer_capability"]
    require(capability_join == {"bytes": len(capability_payload), "challenge": capability["challenge"], "schema": "crazyhouse-datagen-selfplay-capability-response/v1", "sha256": sha256(capability_payload).hex()}, "provenance capability join")
    toolchain = provenance["toolchain"]
    require(toolchain["build_recipe_sha256"] == pins["build_recipe_sha256"] and toolchain["sha256"] == pins["toolchain_sha256"], "provenance toolchain hashes")
    require(isinstance(toolchain["identity"], str) and toolchain["identity"] and "\r" not in toolchain["identity"] and "\0" not in toolchain["identity"], "provenance toolchain identity")
    settings = (
        "Crazyhouse-Stockfish selfplay search settings v1\n"
        "depth=1\n"
        "hash_mib=16\n"
        "history_reset=every-root-search\n"
        "multipv=1\n"
        "nodes=0\n"
        "tablebases=disabled\n"
        "threads=1\n"
        "tt_reset=every-root-search\n"
        "wall_time_encoded=false\n"
    ).encode("ascii")
    teacher = provenance["teacher"]
    require(teacher == {
        "artifact": {"bytes": producer.stat().st_size, "path": artifact["path"], "sha256": producer_digest},
        "bound_policy": "exact-only-for-ongoing-records",
        "evaluator_mode": "incremental-scalar",
        "kind": "legacy-network-product-search",
        "network_used": True,
        "route_backend_identity": NETWORK_SHA256,
        "score_perspective": "side-to-move",
        "search_settings_sha256": sha256(settings).hex(),
        "synthetic": False,
    }, "provenance teacher")


def verify_physical_chunk(
    payload: bytes,
    capability_payload: bytes,
    provenance_payload: bytes,
    ordered_roots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    require(len(payload) == PHYSICAL_HEADER_BYTES + EXPECTED_RECORDS * RECORD_BYTES + PHYSICAL_FOOTER_BYTES, "physical exact bytes")
    header = payload[:PHYSICAL_HEADER_BYTES]
    records_payload = payload[PHYSICAL_HEADER_BYTES:-PHYSICAL_FOOTER_BYTES]
    footer = payload[-PHYSICAL_FOOTER_BYTES:]
    require(header[:16] == PHYSICAL_HEADER_MAGIC and footer[:16] == PHYSICAL_FOOTER_MAGIC, "physical magic")
    require(struct.unpack_from("<IHHHHH", header, 16) == (0x01020304, 256, 256, 128, 1, 0), "physical header layout")
    require(struct.unpack_from("<I", header, 32)[0] == 1 and struct.unpack_from("<Q", header, 40)[0] == EXPECTED_RECORDS, "physical header commit/count")
    require(struct.unpack_from("<HHIQQ", footer, 16) == (128, 1, 1, EXPECTED_RECORDS, EXPECTED_RECORDS * RECORD_BYTES), "physical footer layout/count")
    require(header[30:32] == bytes(2) and header[36:40] == bytes(4) and header[240:252] == bytes(12) and footer[120:124] == bytes(4), "physical reserved")
    require(struct.unpack_from("<I", header, 252)[0] == crc32c(header[:252]) and struct.unpack_from("<I", footer, 124)[0] == crc32c(footer[:124]), "physical header/footer CRC32C")
    chunk_id = derive_id("chunk", 0)
    require(header[48:64] == footer[104:120] == chunk_id and header[64:80] == uuid.UUID(CAMPAIGN_ID).bytes, "physical IDs")
    require(header[80:112].hex() == RULE_PROFILE_SHA256 and header[112:144].hex() == PHYSICAL_SCHEMA_SHA256, "physical rule/schema")
    provenance_digest = sha256(provenance_payload)
    capability_digest = sha256(capability_payload)
    records_digest = sha256(records_payload)
    require(header[144:176] == provenance_digest and header[176:208] == footer[40:72] == records_digest and header[208:240] == capability_digest, "physical section bindings")
    require(footer[72:104] == sha256(header), "physical header digest")

    records = [records_payload[index:index + RECORD_BYTES] for index in range(0, len(records_payload), RECORD_BYTES)]
    require(len(records) == EXPECTED_RECORDS, "physical record count")
    expected_moves = ("Q@b7", "d8h4")
    expected_results = (1, -1)
    expected_ids = (
        (derive_id("game", 0), derive_id("trajectory", 0)),
        (derive_id("game", 1), derive_id("trajectory", 1)),
    )
    observed_moves: list[str] = []
    observed_nodes: list[int] = []
    position_ids: set[bytes] = set()
    raw_records: set[bytes] = set()
    for trajectory_index, (root, move_token, result_white, ids) in enumerate(zip(ordered_roots, expected_moves, expected_results, expected_ids)):
        root_record = records[trajectory_index * 2]
        terminal_record = records[trajectory_index * 2 + 1]
        validate_record_basics(root_record, trajectory_index * 2)
        validate_record_basics(terminal_record, trajectory_index * 2 + 1)
        game_id, trajectory_id = ids
        for ply, record in enumerate((root_record, terminal_record)):
            sequence = trajectory_index * 2 + ply
            require(record[16:32] == game_id and record[32:48] == trajectory_id, f"record {sequence}: deterministic IDs")
            require(struct.unpack_from("<I", record, 48)[0] == ply, f"record {sequence}: ply")
            require(record[109] == 1 and record[110] == 0, f"record {sequence}: fresh automatic history")
            require(record[124] == (result_white & 0xFF), f"record {sequence}: absolute result")
            stm_result = result_white if record[106] == 0 else -result_white
            require(record[125] == (stm_result & 0xFF), f"record {sequence}: side-to-move result")
            require(record[212:244] == provenance_digest, f"record {sequence}: provenance")
            previous = sha256(HISTORY_INITIAL_DOMAIN + trajectory_id + provenance_digest) if ply == 0 else root_record[180:212]
            expected_history = sha256(HISTORY_STEP_DOMAIN + previous + struct.pack("<I", ply) + record[148:180] + record[120:124])
            require(record[180:212] == expected_history, f"record {sequence}: history prefix")
            require(record[148:180] not in position_ids, f"record {sequence}: duplicate position identity")
            position_ids.add(record[148:180])
            require(record not in raw_records, f"record {sequence}: duplicate record bytes")
            raw_records.add(record)

        root_state = parse_fen(root["fen"])
        terminal_state = apply_expected_move(root_state, move_token)
        compare_state(root_record, root_state, trajectory_index * 2)
        compare_state(terminal_record, terminal_state, trajectory_index * 2 + 1)
        expected_wire = (
            bytes((5, NO_SQUARE, parse_square("b7"), 5))
            if move_token == "Q@b7"
            else bytes((1, parse_square("d8"), parse_square("h4"), 0))
        )
        require(root_record[120:124] == expected_wire and terminal_record[120:124] == bytes((0, NO_SQUARE, NO_SQUARE, 0)), f"trajectory {trajectory_index}: move wire")
        require(struct.unpack_from("<I", root_record, 52)[0] == 109 and struct.unpack_from("<I", terminal_record, 52)[0] == 66, f"trajectory {trajectory_index}: flags")
        teacher_kind, teacher_bound, teacher_value, nodes, depth, seldepth, move_ms = struct.unpack_from("<BBiQHHI", root_record, 126)
        require((teacher_kind, teacher_bound, teacher_value, depth, move_ms) == (2, 1, 1, 1, 0), f"trajectory {trajectory_index}: exact mate teacher")
        require(nodes > 0 and seldepth >= depth, f"trajectory {trajectory_index}: teacher work metadata")
        require(struct.unpack_from("<BBiQHHI", terminal_record, 126) == (0, 0, 0, 0, 0, 0, 0), f"trajectory {trajectory_index}: terminal teacher")
        require(root_record[111] == 0 and terminal_record[111] == 1, f"trajectory {trajectory_index}: checkmate terminal")
        terminal_board = unpack_board(terminal_record[56:88])
        terminal_pockets = tuple(terminal_record[96:106])
        terminal_side = terminal_record[106]
        require(in_check(terminal_board, terminal_side), f"trajectory {trajectory_index}: terminal side not in check")
        require(not has_legal_evasion(terminal_board, terminal_pockets, terminal_side), f"trajectory {trajectory_index}: legal mate evasion")
        observed_moves.append(move_token)
        observed_nodes.append(nodes)

    require(len({record[16:32] for record in records}) == 2 and len({record[32:48] for record in records}) == 2, "physical game/trajectory cardinality")
    return {
        "records": len(records),
        "trajectories": 2,
        "moves": observed_moves,
        "nodes": observed_nodes,
        "terminal_reasons": ["checkmate", "checkmate"],
        "independent_terminal_replay": True,
        "record_duplicate_count": 0,
        "position_identity_duplicate_count": 0,
    }


def split_bundle(payload: bytes) -> tuple[bytes, bytes, bytes, dict[str, Any]]:
    require(len(payload) >= BUNDLE_HEADER_BYTES + 2 + 2 + 640 + BUNDLE_FOOTER_BYTES, "bundle minimum size")
    header = payload[:BUNDLE_HEADER_BYTES]
    footer = payload[-BUNDLE_FOOTER_BYTES:]
    require(header[:16] == BUNDLE_HEADER_MAGIC and footer[:16] == BUNDLE_FOOTER_MAGIC, "bundle magic")
    require(struct.unpack_from("<IHHHHI", header, 16) == (0x01020304, 256, 128, 1, 0, 3), "bundle header layout")
    require(struct.unpack_from("<HHI", footer, 16) == (128, 1, 3), "bundle footer layout")
    require(header[224:252] == bytes(28) and footer[104:124] == bytes(20), "bundle reserved")
    require(struct.unpack_from("<I", header, 252)[0] == crc32c(header[:252]) and struct.unpack_from("<I", footer, 124)[0] == crc32c(footer[:124]), "bundle header/footer CRC32C")
    total, capability_bytes, provenance_bytes, chunk_bytes = struct.unpack_from("<QQQQ", header, 32)
    require(2 <= capability_bytes <= 65536 and 2 <= provenance_bytes <= 1048576 and chunk_bytes >= 640, "bundle section lengths")
    payload_bytes = capability_bytes + provenance_bytes + chunk_bytes
    require(total == len(payload) == BUNDLE_HEADER_BYTES + payload_bytes + BUNDLE_FOOTER_BYTES, "bundle exact total length")
    require(struct.unpack_from("<QQ", footer, 24) == (total, payload_bytes), "bundle footer lengths")
    capability_start = BUNDLE_HEADER_BYTES
    provenance_start = capability_start + capability_bytes
    chunk_start = provenance_start + provenance_bytes
    capability = payload[capability_start:provenance_start]
    provenance = payload[provenance_start:chunk_start]
    chunk = payload[chunk_start:chunk_start + chunk_bytes]
    require(len(chunk) == chunk_bytes and chunk_start + chunk_bytes == len(payload) - BUNDLE_FOOTER_BYTES, "bundle trailing bytes")
    payload_section = payload[BUNDLE_HEADER_BYTES:-BUNDLE_FOOTER_BYTES]
    capability_digest, provenance_digest, chunk_digest, payload_digest = (
        sha256(capability), sha256(provenance), sha256(chunk), sha256(payload_section)
    )
    require(header[64:96] == capability_digest and header[96:128] == provenance_digest and header[128:160] == chunk_digest, "bundle section hashes")
    require(header[160:192] == footer[40:72] == payload_digest, "bundle payload hash")
    require(header[192:224].hex() == BUNDLE_SCHEMA_SHA256, "bundle schema binding")
    require(footer[72:104] == sha256(header), "bundle header hash")
    return capability, provenance, chunk, {
        "bundle_bytes": len(payload),
        "bundle_sha256": sha256(payload).hex(),
        "capability_bytes": len(capability),
        "capability_sha256": capability_digest.hex(),
        "provenance_bytes": len(provenance),
        "provenance_sha256": provenance_digest.hex(),
        "chunk_bytes": len(chunk),
        "chunk_sha256": chunk_digest.hex(),
    }


def verify(args: argparse.Namespace) -> dict[str, Any]:
    require(crc32c(b"123456789") == 0xE3069283, "CRC32C implementation")
    paths = {
        "producer": args.producer.resolve(strict=True),
        "bundle": args.bundle.resolve(strict=True),
        "bundle_schema": args.bundle_schema.resolve(strict=True),
        "physical_schema": args.physical_schema.resolve(strict=True),
        "contract": args.contract.resolve(strict=True),
        "book": args.book.resolve(strict=True),
        "network": args.network.resolve(strict=True),
    }
    for key in ("source_commit", "source_tree", "src_tree"):
        lowercase_hex(getattr(args, key), 40, key)
    for key in ("build_recipe_sha256", "toolchain_sha256"):
        lowercase_hex(getattr(args, key), 64, key)
    pins = {key: getattr(args, key) for key in ("source_commit", "source_tree", "src_tree", "build_recipe_sha256", "toolchain_sha256")}
    physical_schema_payload, _ = load_json(paths["physical_schema"], "physical schema", canonical=False)
    bundle_schema_payload, _ = load_json(paths["bundle_schema"], "bundle schema", canonical=False)
    contract_payload, contract = load_json(paths["contract"], "self-play capability contract", canonical=False)
    require(sha256(physical_schema_payload).hex() == PHYSICAL_SCHEMA_SHA256, "physical schema identity")
    require(sha256(bundle_schema_payload).hex() == BUNDLE_SCHEMA_SHA256, "bundle schema identity")
    require(sha256(contract_payload).hex() == SELFPLAY_CONTRACT_SHA256, "self-play contract identity")
    book_payload, ordered_roots = parse_book(paths["book"])
    require(len(book_payload) == BOOK_BYTES, "book byte count")
    require(paths["network"].stat().st_size == NETWORK_BYTES and sha256_file(paths["network"]).hex() == NETWORK_SHA256, "legacy network identity")
    producer_digest = sha256_file(paths["producer"])
    require(paths["producer"].stat().st_size > 0, "producer empty")
    bundle_payload = paths["bundle"].read_bytes()
    capability_payload, provenance_payload, chunk_payload, bundle_summary = split_bundle(bundle_payload)
    capability = parse_json_bytes(capability_payload, "bundle capability", canonical=True)
    provenance = parse_json_bytes(provenance_payload, "bundle provenance", canonical=True)
    validate_capability(capability_payload, capability, contract, paths["producer"], pins)
    require(capability["challenge"] == derive_challenge(producer_digest), "capability challenge derivation")
    validate_provenance(provenance_payload, provenance, capability_payload, capability, paths["producer"], paths["book"], paths["network"], pins)
    physical_summary = verify_physical_chunk(chunk_payload, capability_payload, provenance_payload, ordered_roots)
    return {
        "schema": "crazyhouse-selfplay-datagen-g0-independent-verification/v1",
        "status": "PASS",
        "evidence_class": "E1_ENGINEERING",
        "producer_sha256": producer_digest.hex(),
        **bundle_summary,
        **physical_summary,
        "challenge": capability["challenge"],
        "campaign_id": CAMPAIGN_ID,
        "chunk_id": str(uuid.UUID(bytes=derive_id("chunk", 0))),
        "source_commit": pins["source_commit"],
        "source_tree": pins["source_tree"],
        "src_tree": pins["src_tree"],
        "reference_codec_imported": False,
        "producer_code_imported": False,
        "harness_code_imported": False,
        "training_admissible": False,
        "strength_claim": False,
        "openbench_evidence": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bundle-schema", type=Path, required=True)
    parser.add_argument("--physical-schema", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--src-tree", required=True)
    parser.add_argument("--build-recipe-sha256", required=True)
    parser.add_argument("--toolchain-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args)
        if args.output is not None:
            require(not args.output.exists(), "independent output already exists")
            args.output.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    except (OSError, KeyError, TypeError, ValueError, VerificationError) as exc:
        print(f"FAIL_CRAZYHOUSE_SELFPLAY_DATAGEN_G0_INDEPENDENT {exc}")
        return 1
    print(
        "PASS_CRAZYHOUSE_SELFPLAY_DATAGEN_G0_INDEPENDENT "
        f"records={result['records']} trajectories={result['trajectories']} "
        f"bundle_sha256={result['bundle_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
