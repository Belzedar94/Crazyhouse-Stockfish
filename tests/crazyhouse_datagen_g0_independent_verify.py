#!/usr/bin/env python3
"""Independent verifier for an emitted Crazyhouse physical DATAGEN G0 chunk.

This program uses only the Python standard library. It deliberately imports
neither the reference codec, its unit suite, nor producer implementation code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence
import uuid


HEADER_BYTES = 256
RECORD_BYTES = 256
FOOTER_BYTES = 128
HEADER_MAGIC = b"CHPHYSV1" + bytes(8)
RECORD_MAGIC = b"CHR1"
FOOTER_MAGIC = b"CHPHYSENDV1" + bytes(5)
RULE_SHA = bytes.fromhex("d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68")
SCHEMA_SHA = bytes.fromhex("c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55")
CONTRACT_SHA = "dc6af06c3d18fb2ff06e27e35ab691e35555ef03a5948b23cb2a198e6b89eb96"
CORPUS_SHA = "4113b930d08d6037de8667b9919f8944882d527856b860aaf92bbf1088aa0cdd"
SELECTION_SHA = "e5b39bd15c78b00ce0f6acc01da49103e71685c95f7b6fbde09334933d8bfb18"

POSITION_DOMAIN = b"Crazyhouse-Stockfish physical repetition identity v1\0"
HISTORY_INITIAL_DOMAIN = b"Crazyhouse-Stockfish physical history initial v1\0"
HISTORY_STEP_DOMAIN = b"Crazyhouse-Stockfish physical history step v1\0"

NO_SQUARE = 255
POCKET_MAX = (16, 4, 4, 4, 2, 16, 4, 4, 4, 2)


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


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


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def load_json(path: Path, *, canonical: bool) -> tuple[bytes, Mapping[str, Any]]:
    payload = path.read_bytes()
    require(not payload.startswith(b"\xef\xbb\xbf"), f"{path.name}: BOM")
    require(b"\r" not in payload, f"{path.name}: CR byte")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), f"{path.name}: LF framing")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{path.name}: malformed JSON: {exc}") from exc
    require(isinstance(document, dict), f"{path.name}: root is not an object")
    if canonical:
        require(payload == canonical_json(document), f"{path.name}: JSON is not canonical")
    return payload, document


def lowercase_hex(value: Any, width: int, label: str) -> None:
    require(isinstance(value, str) and len(value) == width and value == value.lower(), f"{label}: width/case")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise VerificationError(f"{label}: non-hex") from exc


def parse_square(value: str) -> int:
    require(len(value) == 2 and "a" <= value[0] <= "h" and "1" <= value[1] <= "8", f"invalid square {value!r}")
    return (ord(value[1]) - ord("1")) * 8 + ord(value[0]) - ord("a")


def unpack_board(packed: bytes) -> tuple[int, ...]:
    require(len(packed) == 32, "packed board width")
    board: list[int] = []
    for byte in packed:
        board.extend((byte & 0x0F, byte >> 4))
    require(not any(piece in {7, 8, 15} for piece in board), "reserved board piece")
    return tuple(board)


def parse_corpus(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    payload = path.read_bytes()
    require(sha256(payload).hex() == CORPUS_SHA, "frozen trajectory stream identity")
    require(b"\r" not in payload and payload.endswith(b"\n") and not payload.endswith(b"\n\n"), "trajectory framing")
    lines = payload[:-1].decode("ascii").split("\n")
    require(lines[0] == "CRAZYHOUSE_TRAJECTORIES_V1\t11\t42" and lines[-1] == "END\t11\t42", "trajectory envelope")
    output: list[dict[str, Any]] = []
    games: set[str] = set()
    trajectories: set[str] = set()
    for line in lines[1:-1]:
        fields = line.split("\t")
        require(len(fields) == 10 and fields[0] == "T", "trajectory row framing")
        game, trajectory = fields[1], fields[2]
        require(game not in games and trajectory not in trajectories, "duplicate frozen UUID")
        games.add(game)
        trajectories.add(trajectory)
        moves = [] if fields[8] == "-" else fields[8].split(",")
        scores: list[int | None] = [None if value == "-" else int(value) for value in fields[9].split(",")]
        require(len(scores) == len(moves) + 1 and scores[-1] is None and all(value is not None for value in scores[:-1]), "score framing")
        output.append(
            {
                "game": game,
                "trajectory": trajectory,
                "claim": int(fields[3]),
                "result": int(fields[4]),
                "terminal": int(fields[5]),
                "nonstandard": bool(int(fields[6])),
                "root_fen": fields[7],
                "moves": moves,
                "scores": scores,
            }
        )
    require(len(output) == 11 and sum(len(row["moves"]) + 1 for row in output) == 42, "frozen trajectory counts")
    return payload, output


def expected_move_wire(token: str | None) -> bytes:
    if token is None:
        return bytes((0, NO_SQUARE, NO_SQUARE, 0))
    lowered = token.lower()
    if "@" in lowered:
        pieces = {"p": 1, "n": 2, "b": 3, "r": 4, "q": 5}
        return bytes((5, NO_SQUARE, parse_square(lowered[2:]), pieces[lowered[0]]))
    source = parse_square(lowered[:2])
    target = parse_square(lowered[2:4])
    if len(lowered) == 5:
        pieces = {"n": 2, "b": 3, "r": 4, "q": 5}
        return bytes((2, source, target, pieces[lowered[4]]))
    kind = 3 if lowered == "e5d6" else 4 if lowered == "e1g1" else 1
    return bytes((kind, source, target, 0))


def raw_ep_from_fen(fen: str) -> int:
    fields = fen.split()
    require(len(fields) == 6, "root FEN field count")
    return NO_SQUARE if fields[3] == "-" else parse_square(fields[3])


def validate_capability(
    payload: bytes,
    response: Mapping[str, Any],
    contract_bytes: bytes,
    contract: Mapping[str, Any],
    producer: Path,
    challenge: str,
) -> None:
    response_contract = contract["response"]
    expected_keys = set(response_contract["required_exact"]) | set(response_contract["required_runtime_bindings"]) | {"schema"}
    require(set(response) == expected_keys, "capability key set")
    require(response["schema"] == response_contract["schema"], "capability schema")
    for key, value in response_contract["required_exact"].items():
        require(response[key] == value, f"capability exact field {key}")
    require(response["challenge"] == challenge, "capability challenge")
    require(response["capability_contract_sha256"] == sha256(contract_bytes).hex() == CONTRACT_SHA, "capability contract binding")
    require(response["artifact_role"] == "crazyhouse-physical-datagen", "capability role")
    producer_bytes = producer.read_bytes()
    require(response["artifact_bytes"] == len(producer_bytes), "capability artifact bytes")
    require(response["artifact_sha256"] == sha256(producer_bytes).hex(), "capability artifact SHA")
    require(response["source_dirty"] is False, "capability dirty source")
    for key in ("source_commit", "source_tree", "src_tree"):
        lowercase_hex(response[key], 40, f"capability {key}")
    for key in ("artifact_sha256", "build_recipe_sha256", "toolchain_sha256"):
        lowercase_hex(response[key], 64, f"capability {key}")
    require(response["supported_record_flags"] == [1, 2, 4, 8, 16, 32, 64], "capability flags")
    require(response["supported_terminal_reasons"] == list(range(7)), "capability terminals")
    require(response["supported_claim_policies"] == [0, 1], "capability claims")
    require(response["supported_move_kinds"] == list(range(6)), "capability moves")
    for key in ("crc32c", "sha256", "fsync", "atomic_rename", "partial_quarantine", "kill_retry_unique_chunk_id", "production_generation_authorized"):
        require(response[key] is True, f"capability {key}")
    require(payload == canonical_json(response), "capability canonical bytes")


def validate_provenance(
    payload: bytes,
    provenance: Mapping[str, Any],
    capability_payload: bytes,
    capability: Mapping[str, Any],
    corpus_payload: bytes,
    corpus_path: Path,
    producer: Path,
    chunk_id: bytes,
    campaign_id: bytes,
) -> None:
    expected_keys = {
        "schema", "project", "variant", "rule_profile", "source_commit", "source_tree", "src_tree", "source_dirty",
        "producer_artifact", "producer_capability", "toolchain", "teacher", "network", "opening_source", "campaign_id",
        "chunk_id", "chunk_index", "seed", "generation_settings", "adjudication", "invalid_game_policy",
    }
    require(set(provenance) == expected_keys and payload == canonical_json(provenance), "provenance shape/canonical bytes")
    require(provenance["schema"] == "crazyhouse-datagen-provenance/v1" and provenance["project"] == "Crazyhouse-Stockfish" and provenance["variant"] == "crazyhouse", "provenance identity")
    require(provenance["rule_profile"] == {"id": "LICHESS_CRAZYHOUSE_2026_08_12", "sha256": RULE_SHA.hex()}, "provenance rule profile")
    require(provenance["source_commit"] == capability["source_commit"] and provenance["source_tree"] == capability["source_tree"] and provenance["src_tree"] == capability["src_tree"] and provenance["source_dirty"] is False, "provenance source join")
    artifact = provenance["producer_artifact"]
    require(artifact["kind"] == "crazyhouse-physical-datagen" and artifact["bytes"] == producer.stat().st_size and artifact["sha256"] == sha256(producer.read_bytes()).hex(), "provenance producer artifact")
    require(isinstance(artifact["path"], str) and "\\" not in artifact["path"] and ":" not in artifact["path"] and ".." not in artifact["path"].split("/"), "provenance producer path")
    cap = provenance["producer_capability"]
    require(cap == {"bytes": len(capability_payload), "challenge": capability["challenge"], "schema": "crazyhouse-datagen-capability-response/v1", "sha256": sha256(capability_payload).hex()}, "provenance capability join")
    toolchain = provenance["toolchain"]
    require(toolchain["build_recipe_sha256"] == capability["build_recipe_sha256"] and toolchain["sha256"] == capability["toolchain_sha256"] and isinstance(toolchain["identity"], str) and toolchain["identity"], "provenance toolchain join")
    require(provenance["teacher"] == {"artifact": None, "bound_policy": "exact-only-for-ongoing-records", "kind": "golden-fixture", "network_used": False, "score_perspective": "side-to-move", "search_settings_sha256": "f6eadbf76d6c37756f4dca4a3a2b0893a9a0ec7eaf164f309f54493185ff25d6", "synthetic": True}, "provenance teacher")
    require(provenance["network"] == {"bytes": 0, "format": None, "license": None, "path": None, "sha256": None, "used": False}, "provenance unused network")
    opening = provenance["opening_source"]
    require(opening["engine_selected"] is False and opening["match_result_selected"] is False and opening["kind"] == "authority-g0-trajectories" and opening["selection_policy_sha256"] == SELECTION_SHA, "provenance opening policy")
    require(opening["artifact"]["bytes"] == len(corpus_payload) and opening["artifact"]["sha256"] == sha256(corpus_payload).hex() and opening["artifact"]["path"] == "tests/crazyhouse/data/crazyhouse-datagen-g0-trajectories-v1.tsv" and opening["artifact"]["kind"] == "physical-trajectory-stream", "provenance opening artifact")
    require(corpus_path.name == Path(opening["artifact"]["path"]).name, "provenance corpus basename")
    require(uuid.UUID(provenance["campaign_id"]).bytes == campaign_id and uuid.UUID(provenance["chunk_id"]).bytes == chunk_id, "provenance UUID/header join")
    require(provenance["generation_settings"].get("training_admissible") is False and provenance["generation_settings"].get("fixture_only") is True, "provenance training boundary")
    require(set(provenance["invalid_game_policy"].values()) == {"quarantine-game"}, "provenance invalid-game policy")


def position_digest(record: bytes) -> bytes:
    payload = (
        POSITION_DOMAIN
        + record[56:88]
        + bytes((record[106], record[107], record[244]))
        + record[96:106]
        + record[88:96]
    )
    return sha256(payload)


def validate_physical_record(record: bytes, index: int) -> tuple[int, ...]:
    require(record[:4] == RECORD_MAGIC and struct.unpack_from("<HHQ", record, 4) == (1, 256, index), f"record {index}: envelope")
    require(struct.unpack_from("<I", record, 252)[0] == crc32c(record[:252]), f"record {index}: CRC32C")
    require(record[245:252] == bytes(7), f"record {index}: reserved bytes")
    board = unpack_board(record[56:88])
    require(board.count(6) == 1 and board.count(14) == 1, f"record {index}: kings")
    require(not any(piece & 7 == 1 and square // 8 in {0, 7} for square, piece in enumerate(board) if piece), f"record {index}: pawn promotion rank")
    promoted = struct.unpack_from("<Q", record, 88)[0]
    for square in range(64):
        if promoted & (1 << square):
            require(board[square] != 0 and board[square] & 7 not in {1, 6}, f"record {index}: promoted provenance")
    pocket = tuple(record[96:106])
    require(all(value <= maximum for value, maximum in zip(pocket, POCKET_MAX)), f"record {index}: pocket maximum")
    side, rights, raw_ep, repetition, claim, terminal = record[106:112]
    require(side in {0, 1} and rights <= 15 and raw_ep in {*range(64), NO_SQUARE} and repetition >= 1 and claim in {0, 1} and terminal <= 6, f"record {index}: state enums")
    for bit, king_square, rook_square, king_code, rook_code in (
        (1, 4, 7, 6, 4), (2, 4, 0, 6, 4), (4, 60, 63, 14, 12), (8, 60, 56, 14, 12)
    ):
        if rights & bit:
            require(board[king_square] == king_code and board[rook_square] == rook_code and not promoted & ((1 << king_square) | (1 << rook_square)), f"record {index}: castling physical state")
    require(record[148:180] == position_digest(record), f"record {index}: position identity")
    return board


def captured_pocket_index(piece: int, was_promoted: bool, capturer_side: int) -> int:
    piece_type = 1 if was_promoted else piece & 7
    require(1 <= piece_type <= 5, "captured pocket type")
    return capturer_side * 5 + piece_type - 1


def verify_transition(current: bytes, following: bytes, index: int) -> None:
    board = list(unpack_board(current[56:88]))
    next_board = list(unpack_board(following[56:88]))
    pocket = list(current[96:106])
    next_pocket = list(following[96:106])
    promoted = struct.unpack_from("<Q", current, 88)[0]
    next_promoted = struct.unpack_from("<Q", following, 88)[0]
    side = current[106]
    kind, source, target, aux = current[120:124]
    require(kind != 0 and following[106] == 1 - side, f"transition {index}: move/side")
    if kind == 5:
        require(source == NO_SQUARE and 1 <= aux <= 5 and board[target] == 0, f"transition {index}: drop framing")
        require(pocket[side * 5 + aux - 1] > 0, f"transition {index}: drop pocket")
        pocket[side * 5 + aux - 1] -= 1
        board[target] = aux | (8 if side else 0)
    else:
        require(source < 64 and target < 64 and board[source] != 0 and board[source] >> 3 == side, f"transition {index}: source ownership")
        moved = board[source]
        captured_square = target
        if kind == 3:
            captured_square = target - 8 if side == 0 else target + 8
        captured = board[captured_square]
        if captured:
            pocket[captured_pocket_index(captured, bool(promoted & (1 << captured_square)), side)] += 1
            promoted &= ~(1 << captured_square)
            board[captured_square] = 0
        board[source] = 0
        moved_promoted = bool(promoted & (1 << source))
        promoted &= ~(1 << source)
        if kind == 2:
            require(moved & 7 == 1 and 2 <= aux <= 5, f"transition {index}: promotion")
            moved = aux | (8 if side else 0)
            moved_promoted = True
        board[target] = moved
        if moved_promoted:
            promoted |= 1 << target
        if kind == 4:
            require((source, target) == (4, 6) and side == 0, f"transition {index}: frozen castling")
            require(board[7] == 4 and board[5] == 0, f"transition {index}: rook pre-state")
            board[7] = 0
            board[5] = 4
    require(board == next_board, f"transition {index}: board mismatch")
    require(pocket == next_pocket, f"transition {index}: pocket mismatch")
    require(promoted == next_promoted, f"transition {index}: promoted mismatch")


def verify_chunk(
    payload: bytes,
    provenance_payload: bytes,
    capability_payload: bytes,
    trajectories: list[dict[str, Any]],
) -> dict[str, Any]:
    require(len(payload) == HEADER_BYTES + 42 * RECORD_BYTES + FOOTER_BYTES, "chunk exact bytes")
    header, records_payload, footer = payload[:HEADER_BYTES], payload[HEADER_BYTES:-FOOTER_BYTES], payload[-FOOTER_BYTES:]
    require(header[:16] == HEADER_MAGIC and footer[:16] == FOOTER_MAGIC, "chunk magic")
    require(struct.unpack_from("<IHHHHH", header, 16) == (0x01020304, 256, 256, 128, 1, 0), "header layout")
    require(struct.unpack_from("<I", header, 32)[0] == 1 and struct.unpack_from("<Q", header, 40)[0] == 42, "header commit/count")
    require(struct.unpack_from("<HHIQQ", footer, 16) == (128, 1, 1, 42, 42 * 256), "footer layout/count")
    require(struct.unpack_from("<I", header, 252)[0] == crc32c(header[:252]) and struct.unpack_from("<I", footer, 124)[0] == crc32c(footer[:124]), "header/footer CRC32C")
    require(header[30:32] == bytes(2) and header[36:40] == bytes(4) and header[240:252] == bytes(12) and footer[120:124] == bytes(4), "header/footer reserved bytes")
    require(header[48:64] == footer[104:120] and any(header[48:64]) and any(header[64:80]), "chunk/campaign IDs")
    require(header[80:112] == RULE_SHA and header[112:144] == SCHEMA_SHA, "header rule/schema")
    provenance_digest = sha256(provenance_payload)
    capability_digest = sha256(capability_payload)
    records_digest = sha256(records_payload)
    require(header[144:176] == provenance_digest and header[176:208] == records_digest and header[208:240] == capability_digest, "header digest bindings")
    require(footer[40:72] == records_digest and footer[72:104] == sha256(header), "footer digest bindings")

    expected_rows: list[dict[str, Any]] = []
    for trajectory in trajectories:
        raw_ep = raw_ep_from_fen(trajectory["root_fen"])
        for ply in range(len(trajectory["moves"]) + 1):
            token = trajectory["moves"][ply] if ply < len(trajectory["moves"]) else None
            expected_rows.append({**trajectory, "ply": ply, "token": token, "score": trajectory["scores"][ply], "raw_ep": raw_ep})
            raw_ep = NO_SQUARE
            if token is not None and "@" not in token and len(token) == 4:
                source, target = parse_square(token[:2]), parse_square(token[2:])
                if abs(target - source) == 16:
                    raw_ep = (source + target) // 2

    records = [records_payload[index : index + RECORD_BYTES] for index in range(0, len(records_payload), RECORD_BYTES)]
    require(len(records) == len(expected_rows) == 42, "record count")
    previous_by_trajectory: dict[bytes, bytes] = {}
    occurrences: dict[bytes, dict[bytes, int]] = {}
    closed: set[bytes] = set()
    active: bytes | None = None
    terminal_trajectories: set[bytes] = set()
    coverage = {"drop": False, "promotion": False, "en_passant": False, "castling": False, "promoted": False, "raw_ep": False, "effective_ep": False, "fivefold": False, "threefold": False}
    for index, (record, expected) in enumerate(zip(records, expected_rows)):
        board = validate_physical_record(record, index)
        game_id = uuid.UUID(expected["game"]).bytes
        trajectory_id = uuid.UUID(expected["trajectory"]).bytes
        require(record[16:32] == game_id and record[32:48] == trajectory_id, f"record {index}: frozen IDs")
        ply, flags = struct.unpack_from("<II", record, 48)
        require(ply == expected["ply"], f"record {index}: ply")
        terminal = expected["token"] is None
        expected_flags = (2 if terminal else 1 | 4) | (32 if ply == 0 else 0) | (64 if expected["nonstandard"] else 0)
        require(flags == expected_flags, f"record {index}: flags")
        require(record[108] == expected["raw_ep"], f"record {index}: raw EP")
        require(record[110] == expected["claim"] and record[111] == (expected["terminal"] if terminal else 0), f"record {index}: claim/terminal")
        require(record[120:124] == expected_move_wire(expected["token"]), f"record {index}: move wire")
        white_result, stm_result = struct.unpack_from("<bb", record, 124)
        require(white_result == expected["result"] and stm_result == (white_result if record[106] == 0 else -white_result), f"record {index}: result perspective")
        teacher_kind, teacher_bound, teacher_value, nodes, depth, seldepth, move_ms = struct.unpack_from("<BBiQHHI", record, 126)
        if terminal:
            require((teacher_kind, teacher_bound, teacher_value, nodes, depth, seldepth, move_ms) == (0, 0, 0, 0, 0, 0, 0), f"record {index}: terminal teacher")
        else:
            require((teacher_kind, teacher_bound, teacher_value, nodes, depth, seldepth, move_ms) == (1, 1, expected["score"], 1024, 8, 10, 5), f"record {index}: teacher")
        require(record[212:244] == provenance_digest, f"record {index}: provenance")

        if active is not None and trajectory_id != active:
            closed.add(active)
        require(trajectory_id not in closed and trajectory_id not in terminal_trajectories, f"record {index}: trajectory contiguity")
        active = trajectory_id
        if ply == 0:
            require(trajectory_id not in previous_by_trajectory, f"record {index}: repeated root")
            previous = sha256(HISTORY_INITIAL_DOMAIN + trajectory_id + provenance_digest)
            occurrences[trajectory_id] = {}
        else:
            previous = previous_by_trajectory[trajectory_id]
        position = record[148:180]
        expected_history = sha256(HISTORY_STEP_DOMAIN + previous + struct.pack("<I", ply) + position + record[120:124])
        require(record[180:212] == expected_history, f"record {index}: ordered history")
        previous_by_trajectory[trajectory_id] = expected_history
        count = occurrences[trajectory_id].get(position, 0) + 1
        occurrences[trajectory_id][position] = count
        require(record[109] == count, f"record {index}: repetition occurrence")
        if terminal:
            terminal_trajectories.add(trajectory_id)
        if not terminal:
            verify_transition(record, records[index + 1], index)
        kind = record[120]
        coverage["drop"] |= kind == 5
        coverage["promotion"] |= kind == 2
        coverage["en_passant"] |= kind == 3
        coverage["castling"] |= kind == 4
        coverage["promoted"] |= struct.unpack_from("<Q", record, 88)[0] != 0
        coverage["raw_ep"] |= record[108] != NO_SQUARE
        coverage["effective_ep"] |= record[244] != NO_SQUARE
        coverage["fivefold"] |= record[111] == 3 and record[109] == 5
        coverage["threefold"] |= record[111] == 4 and record[109] == 3 and record[110] == 1

    require(set(previous_by_trajectory) == terminal_trajectories and len(terminal_trajectories) == 11, "complete terminal trajectories")
    require(all(coverage.values()), "rare-state coverage")
    require(records[1][108] == parse_square("e3") and records[1][244] == NO_SQUARE, "raw/effective EP normalization")
    require(records[8][108] == records[8][244] == parse_square("d6"), "legal EP")
    require(records[2][96 + 6] == 1 and records[3][96 + 6] == 0, "drop pocket ownership")
    require(struct.unpack_from("<Q", records[13], 88)[0] & (1 << parse_square("a8")), "promotion provenance")
    require(records[15][96 + 5] == 1, "captured promoted unit demotes to pocket pawn")
    return {"records": len(records), "trajectories": len(terminal_trajectories), "coverage": coverage}


def verify(args: argparse.Namespace) -> dict[str, Any]:
    require(crc32c(b"123456789") == 0xE3069283, "CRC32C implementation")
    producer = args.producer.resolve(strict=True)
    schema = args.schema.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    corpus_path = args.corpus.resolve(strict=True)
    chunk_path = args.chunk.resolve(strict=True)
    capability_path = args.capability.resolve(strict=True)
    provenance_path = args.provenance.resolve(strict=True)
    schema_payload, _ = load_json(schema, canonical=False)
    contract_payload, contract = load_json(contract_path, canonical=False)
    capability_payload, capability = load_json(capability_path, canonical=True)
    provenance_payload, provenance = load_json(provenance_path, canonical=True)
    corpus_payload, trajectories = parse_corpus(corpus_path)
    require(sha256(schema_payload) == SCHEMA_SHA and sha256(contract_payload).hex() == CONTRACT_SHA, "schema/contract identities")
    lowercase_hex(args.challenge, 32, "expected challenge")
    validate_capability(capability_payload, capability, contract_payload, contract, producer, args.challenge)
    chunk_payload = chunk_path.read_bytes()
    chunk_summary = verify_chunk(chunk_payload, provenance_payload, capability_payload, trajectories)
    validate_provenance(
        provenance_payload,
        provenance,
        capability_payload,
        capability,
        corpus_payload,
        corpus_path,
        producer,
        chunk_payload[48:64],
        chunk_payload[64:80],
    )
    return {
        "schema": "crazyhouse-datagen-g0-independent-verification/v1",
        "status": "PASS",
        "evidence_class": "E1_ENGINEERING",
        "producer_sha256": sha256(producer.read_bytes()).hex(),
        "capability_sha256": sha256(capability_payload).hex(),
        "provenance_sha256": sha256(provenance_payload).hex(),
        "chunk_sha256": sha256(chunk_payload).hex(),
        "chunk_bytes": len(chunk_payload),
        "record_count": chunk_summary["records"],
        "trajectory_count": chunk_summary["trajectories"],
        "coverage": chunk_summary["coverage"],
        "reference_codec_imported": False,
        "producer_code_imported": False,
        "training_admissible": False,
        "strength_claim": False,
        "openbench_evidence": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--chunk", type=Path, required=True)
    parser.add_argument("--capability", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--challenge", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args)
        if args.output is not None:
            require(not args.output.exists(), "independent output already exists")
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except (OSError, KeyError, TypeError, ValueError, VerificationError) as exc:
        print(f"FAIL_CRAZYHOUSE_DATAGEN_G0_INDEPENDENT {exc}")
        return 1
    print(
        "PASS_CRAZYHOUSE_DATAGEN_G0_INDEPENDENT "
        f"records={result['record_count']} trajectories={result['trajectory_count']} "
        f"chunk_sha256={result['chunk_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
