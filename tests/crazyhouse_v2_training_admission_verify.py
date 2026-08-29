#!/usr/bin/env python3
"""Independent full-scan and adversarial test for NNUE V2 data admission.

This verifier intentionally imports neither the physical producer codec nor
the admission loader.  The loader is exercised only as a subprocess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence
import uuid


ROOT = Path(__file__).resolve().parents[1]
RULE_SHA = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PHYSICAL_SHA = "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
FEATURE_SHA = "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6"
PRODUCTION_CAPABILITY_SHA = "23386f8c51307522b08fbe3bef309791c90e40022a62e073eaaaf08a9467397b"
PRODUCTION_NETWORK_SHA = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
PRODUCTION_BOOK_SHA = "1371e87ce3bdb875d922ad0061c96c4a123bc571daf4ae2bff24e5176287f0fa"
PRODUCTION_POLICY_SHA = "475fd0fb9a929e964ff32357031a18d33ecc2543e8681cc73068858c10db3014"

HEADER_BYTES = 256
RECORD_BYTES = 256
FOOTER_BYTES = 128
HEADER_MAGIC = b"CHPHYSV1" + bytes(8)
FOOTER_MAGIC = b"CHPHYSENDV1" + bytes(5)
BYTE_ORDER = 0x01020304
COMMITTED = 1
NO_SQUARE = 255

POSITION_DOMAIN = b"Crazyhouse-Stockfish physical repetition identity v1\0"
HISTORY_INITIAL_DOMAIN = b"Crazyhouse-Stockfish physical history initial v1\0"
HISTORY_STEP_DOMAIN = b"Crazyhouse-Stockfish physical history step v1\0"
SPLIT_DOMAIN = b"Crazyhouse-Stockfish physical trajectory split v1\0"
RAW_DOMAIN = b"Crazyhouse-Stockfish physical record identity v1\0"
MODEL_DOMAIN = b"Crazyhouse-Stockfish NNUE V2 model input identity v1\0"
CHUNK_SET_DOMAIN = b"Crazyhouse-Stockfish ordered chunk set v1\0"
RECORD_STREAM_DOMAIN = b"Crazyhouse-Stockfish ordered record stream v1\0"
TRAJECTORY_SET_DOMAIN = b"Crazyhouse-Stockfish ordered trajectory set v1\0"
IDENTITY_SET_DOMAIN = b"Crazyhouse-Stockfish ordered admission identity set v1\0"

POCKET_MAXIMUMS = (16, 4, 4, 4, 2, 16, 4, 4, 4, 2)
POCKET_TYPE_BASE = (0, 34, 44, 54, 64)
POCKET_WIDTHS = (17, 5, 5, 5, 3)
IDENTITY_KINDS = (
    "raw_record_key",
    "position_identity",
    "model_input_key",
    "game_id",
    "trajectory_id",
)


class VerificationFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in output, f"duplicate JSON key {key}")
        output[key] = value
    return output


def parse_json(payload: bytes, label: str) -> Mapping[str, Any]:
    require(not payload.startswith(b"\xef\xbb\xbf"), f"{label}: BOM")
    require(b"\r" not in payload, f"{label}: CR")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), f"{label}: LF")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(f"{label}: {exc}") from exc
    require(isinstance(document, dict), f"{label}: root")
    require(payload == canonical_json(document), f"{label}: canonical")
    return document


def read_descriptor(root: Path, value: Mapping[str, Any], label: str) -> bytes:
    require(set(value) == {"path", "bytes", "sha256"}, f"{label}: descriptor")
    path = root.joinpath(*value["path"].split("/"))
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode), f"{label}: regular")
    payload = path.read_bytes()
    require(len(payload) == value["bytes"], f"{label}: bytes")
    require(sha256(payload) == value["sha256"], f"{label}: digest")
    return payload


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def history_initial(trajectory_id: bytes, provenance_sha: bytes) -> bytes:
    return hashlib.sha256(HISTORY_INITIAL_DOMAIN + trajectory_id + provenance_sha).digest()


def history_step(previous: bytes, ply: int, position_sha: bytes, move: bytes) -> bytes:
    return hashlib.sha256(
        HISTORY_STEP_DOMAIN + previous + struct.pack("<I", ply) + position_sha + move
    ).digest()


def feature_rows(record: Mapping[str, Any], perspective: int) -> tuple[int, ...]:
    rows: list[int] = []
    board = record["board"]
    for square, code in enumerate(board):
        if not code:
            continue
        piece_type = code & 7
        owner = code >> 3
        oriented = square if perspective == 0 else square ^ 56
        plane = 2 * (piece_type - 1) + int(owner != perspective)
        rows.append(plane * 64 + oriented)
    for piece_type in range(5):
        for relative_owner in range(2):
            absolute_owner = perspective ^ relative_owner
            count = record["pockets"][absolute_owner * 5 + piece_type]
            rows.append(
                768
                + POCKET_TYPE_BASE[piece_type]
                + relative_owner * POCKET_WIDTHS[piece_type]
                + count
            )
    for square in range(64):
        if record["promoted_mask"] & (1 << square):
            rows.append(838 + (square if perspective == 0 else square ^ 56))
    rows.sort()
    require(len(rows) <= 138 and len(rows) == len(set(rows)), "independent feature rows")
    require(all(0 <= row < 902 for row in rows), "independent feature range")
    return tuple(rows)


def model_key(stm_rows: Sequence[int], opponent_rows: Sequence[int]) -> bytes:
    payload = bytearray(MODEL_DOMAIN)
    payload.extend(bytes.fromhex(FEATURE_SHA))
    payload.extend(struct.pack("<I", len(stm_rows)))
    for row in sorted(stm_rows):
        payload.extend(struct.pack("<I", row))
    payload.extend(struct.pack("<I", len(opponent_rows)))
    for row in sorted(opponent_rows):
        payload.extend(struct.pack("<I", row))
    return hashlib.sha256(payload).digest()


def split_role(config: Mapping[str, Any], campaign: bytes, trajectory: bytes) -> str:
    value = int.from_bytes(
        hashlib.sha256(
            SPLIT_DOMAIN
            + struct.pack("<Q", config["split_seed_u64"])
            + campaign
            + trajectory
        ).digest()[:8],
        "little",
    )
    return "validation" if value < config["validation_threshold_u64"] else "train"


def decode_record(raw: bytes) -> dict[str, Any]:
    require(len(raw) == RECORD_BYTES, "record width")
    require(raw[:4] == b"CHR1", "record magic")
    require(struct.unpack_from("<HH", raw, 4) == (1, RECORD_BYTES), "record version")
    require(raw[245:252] == bytes(7), "record reserved")
    require(struct.unpack_from("<I", raw, 252)[0] == crc32c(raw[:252]), "record CRC")
    sequence = struct.unpack_from("<Q", raw, 8)[0]
    ply, flags = struct.unpack_from("<II", raw, 48)
    require(flags & ~0x7F == 0 and bool(flags & 32) == (ply == 0), "record flags")
    board: list[int] = []
    for packed in raw[56:88]:
        board.extend((packed & 15, packed >> 4))
    allowed = {0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14}
    require(all(code in allowed for code in board), "record pieces")
    require(board.count(6) == 1 and board.count(14) == 1, "record kings")
    promoted = struct.unpack_from("<Q", raw, 88)[0]
    occupied = sum(1 << square for square, code in enumerate(board) if code)
    require(promoted & ~occupied == 0, "record promoted occupancy")
    pockets = tuple(raw[96:106])
    require(
        all(value <= maximum for value, maximum in zip(pockets, POCKET_MAXIMUMS)),
        "record pockets",
    )
    stm, rights, raw_ep, repetition, claim, terminal = raw[106:112]
    effective_ep = raw[244]
    require(stm in {0, 1} and rights & ~15 == 0, "record state scalar")
    require(raw_ep == NO_SQUARE or raw_ep < 64, "record raw ep")
    require(effective_ep in {NO_SQUARE, raw_ep}, "record effective ep")
    require(1 <= repetition <= 5 and claim in {0, 1} and terminal < 7, "record history scalar")
    halfmove, fullmove = struct.unpack_from("<II", raw, 112)
    require(fullmove > 0, "record fullmove")
    move = raw[120:124]
    require(move[0] < 6, "record move kind")
    (
        white_result,
        stm_result,
        teacher_kind,
        teacher_bound,
        teacher_value,
        nodes,
        depth,
        seldepth,
        move_time,
    ) = struct.unpack_from("<bbBBiQHHI", raw, 124)
    require(white_result in {-1, 0, 1}, "record white result")
    require(
        stm_result == (white_result if stm == 0 else -white_result),
        "record result perspective",
    )
    is_terminal = bool(flags & 2)
    has_teacher = bool(flags & 4)
    require(is_terminal == (terminal != 0), "record terminal flag")
    require(bool(flags & 1) == (move[0] != 0), "record move flag")
    require(is_terminal != bool(flags & 1), "record terminal/move")
    require(has_teacher == (not is_terminal), "record teacher presence")
    if has_teacher:
        require(teacher_kind in {1, 2} and teacher_bound == 1 and nodes > 0, "record teacher")
    else:
        require(
            teacher_kind == teacher_bound == teacher_value == nodes == depth == seldepth == move_time == 0,
            "record absent teacher",
        )
    position = hashlib.sha256(
        POSITION_DOMAIN
        + raw[56:88]
        + bytes((stm, rights, effective_ep))
        + bytes(pockets)
        + struct.pack("<Q", promoted)
    ).digest()
    require(raw[148:180] == position, "record position identity")
    require(all(any(raw[start : start + width]) for start, width in ((16, 16), (32, 16), (180, 32), (212, 32))), "record zero identity")
    return {
        "augmented": bool(flags & 16),
        "board": tuple(board),
        "castling_rights": rights,
        "effective_ep": effective_ep,
        "flags": flags,
        "fullmove": fullmove,
        "game_id": raw[16:32],
        "game_result_white": white_result,
        "halfmove": halfmove,
        "history": raw[180:212],
        "move": move,
        "move_time": move_time,
        "ply": ply,
        "pockets": pockets,
        "position": position,
        "promoted_mask": promoted,
        "provenance": raw[212:244],
        "raw_ep": raw_ep,
        "repetition": repetition,
        "result_stm": stm_result,
        "search_depth": depth,
        "search_nodes": nodes,
        "search_seldepth": seldepth,
        "sequence": sequence,
        "side_to_move": stm,
        "teacher_bound": teacher_bound,
        "teacher_kind": teacher_kind,
        "teacher_value": teacher_value,
        "terminal": terminal,
        "trajectory_id": raw[32:48],
    }


def expected_row(
    role: str,
    campaign: bytes,
    chunk: bytes,
    chunk_index: int,
    raw: bytes,
    record: Mapping[str, Any],
) -> tuple[bytes, dict[str, bytes]]:
    stm_rows = feature_rows(record, record["side_to_move"])
    opponent_rows = feature_rows(record, record["side_to_move"] ^ 1)
    raw_key = hashlib.sha256(RAW_DOMAIN + raw).digest()
    feature_key = model_key(stm_rows, opponent_rows)
    identities = {
        "raw_record_key": raw_key,
        "position_identity": record["position"],
        "model_input_key": feature_key,
        "game_id": campaign + record["game_id"],
        "trajectory_id": campaign + record["trajectory_id"],
    }
    teacher_kinds = ("none", "centipawn", "mate-plies")
    teacher_bounds = ("none", "exact", "lower", "upper")
    terminals = (
        "ongoing",
        "checkmate",
        "stalemate",
        "fivefold-repetition",
        "threefold-claim-proxy",
        "resignation",
        "draw-adjudication",
    )
    row = {
        "campaign_id": str(uuid.UUID(bytes=campaign)),
        "chunk_id": str(uuid.UUID(bytes=chunk)),
        "chunk_index": chunk_index,
        "game_id": str(uuid.UUID(bytes=record["game_id"])),
        "game_result_white": record["game_result_white"],
        "model_input_key": feature_key.hex(),
        "move_time_ms": record["move_time"],
        "opponent_rows": list(opponent_rows),
        "ply": record["ply"],
        "position_identity_sha256": record["position"].hex(),
        "raw_record_key": raw_key.hex(),
        "result_side_to_move": record["result_stm"],
        "role": role,
        "schema": "crazyhouse-nnue-v2-physical-row/v1",
        "search_depth": record["search_depth"],
        "search_nodes": record["search_nodes"],
        "search_seldepth": record["search_seldepth"],
        "sequence": record["sequence"],
        "side_to_move": "white" if record["side_to_move"] == 0 else "black",
        "stm_rows": list(stm_rows),
        "teacher_bound": teacher_bounds[record["teacher_bound"]],
        "teacher_score_kind": teacher_kinds[record["teacher_kind"]],
        "teacher_score_value": record["teacher_value"],
        "terminal_reason": terminals[record["terminal"]],
        "trajectory_id": str(uuid.UUID(bytes=record["trajectory_id"])),
    }
    return canonical_json(row), identities


def ordered_chunk_digest(chunks: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256(CHUNK_SET_DOMAIN + struct.pack("<Q", len(chunks)))
    for chunk in chunks:
        digest.update(bytes.fromhex(chunk["bundle"]["sha256"]))
    return digest.hexdigest()


def ordered_record_digest(records: Sequence[bytes]) -> str:
    digest = hashlib.sha256(RECORD_STREAM_DOMAIN + struct.pack("<Q", len(records)))
    for record in records:
        digest.update(record)
    return digest.hexdigest()


def ordered_trajectory_digest(keys: set[bytes]) -> str:
    digest = hashlib.sha256(TRAJECTORY_SET_DOMAIN + struct.pack("<Q", len(keys)))
    for key in sorted(keys):
        digest.update(key)
    return digest.hexdigest()


def ordered_identity_digest(role: str, kind: str, keys: set[bytes]) -> str:
    digest = hashlib.sha256(
        IDENTITY_SET_DOMAIN
        + kind.encode("ascii")
        + b"\0"
        + role.encode("ascii")
        + b"\0"
        + struct.pack("<Q", len(keys))
    )
    for key in sorted(keys):
        digest.update(key)
    return digest.hexdigest()


def independent_full_scan(
    fixture: Path,
    admitted: Path,
) -> dict[str, Any]:
    manifest_bytes = (fixture / "training-dataset-manifest.json").read_bytes()
    manifest = parse_json(manifest_bytes, "manifest")
    require(manifest["fixture_mode"] is True and manifest["training_admissible"] is False, "fixture boundary")
    require(sha256(read_descriptor(fixture, manifest["physical_schema"], "physical schema")) == PHYSICAL_SHA, "physical pin")
    require(sha256(read_descriptor(fixture, manifest["feature_contract"], "feature contract")) == FEATURE_SHA, "feature pin")
    read_descriptor(fixture, manifest["admission_tool"], "admission tool")
    aggregate = parse_json(
        read_descriptor(fixture, manifest["aggregate_chunk_set_receipt"], "aggregate"),
        "aggregate",
    )
    require(aggregate["exact_total"] is True and aggregate["training_admissible"] is False, "aggregate boundary")
    identities: dict[str, dict[str, list[bytes]]] = {
        role: {kind: [] for kind in IDENTITY_KINDS} for role in ("train", "validation")
    }
    expected_rows: dict[str, list[bytes]] = {"train": [], "validation": []}
    raw_by_role: dict[str, list[bytes]] = {"train": [], "validation": []}
    trajectories_by_role: dict[str, set[bytes]] = {"train": set(), "validation": set()}
    coverage = {
        "pockets": False,
        "drop": False,
        "promoted_provenance": False,
        "capture_of_promoted": False,
        "castling_rights": False,
        "effective_en_passant": False,
        "repetition_history": False,
        "checkmate": False,
        "draw_terminal": False,
        "white_to_move_white_win": False,
        "black_to_move_white_win": False,
        "draw_white_to_move": False,
        "draw_black_to_move": False,
        "positive_centipawn": False,
        "negative_centipawn": False,
        "positive_mate": False,
        "negative_mate": False,
        "rank_reflection_color_swap": False,
    }
    augmented_models: set[bytes] = set()
    ordinary_models: set[bytes] = set()
    total_records = 0
    total_trajectories = 0
    for role in ("train", "validation"):
        role_doc = manifest["roles"][role]
        require(role_doc["ordered_chunk_set_sha256"] == ordered_chunk_digest(role_doc["chunks"]), f"{role} chunk digest")
        for entry in role_doc["chunks"]:
            capability = read_descriptor(fixture, entry["capability"], "capability")
            provenance_bytes = read_descriptor(fixture, entry["provenance"], "provenance")
            provenance = parse_json(provenance_bytes, "provenance")
            require(provenance["variant"] == "crazyhouse", "provenance variant")
            require(provenance["generation_settings"]["fixture_only"] is True, "provenance fixture")
            require(provenance["producer_capability"]["sha256"] == sha256(capability), "capability binding")
            bundle = read_descriptor(fixture, entry["bundle"], "bundle")
            require(bundle[:16] == HEADER_MAGIC and bundle[-FOOTER_BYTES:-FOOTER_BYTES + 16] == FOOTER_MAGIC, "bundle magic")
            header = bundle[:HEADER_BYTES]
            footer = bundle[-FOOTER_BYTES:]
            require(struct.unpack_from("<I", header, 252)[0] == crc32c(header[:252]), "header CRC")
            require(struct.unpack_from("<I", footer, 124)[0] == crc32c(footer[:124]), "footer CRC")
            require(struct.unpack_from("<IHHHHH", header, 16) == (BYTE_ORDER, 256, 256, 128, 1, 0), "header layout")
            count = struct.unpack_from("<Q", header, 40)[0]
            require(len(bundle) == HEADER_BYTES + count * RECORD_BYTES + FOOTER_BYTES, "bundle framing")
            require(count == entry["record_count"], "chunk count")
            campaign = header[64:80]
            chunk = header[48:64]
            require(str(uuid.UUID(bytes=campaign)) == entry["campaign_id"], "campaign binding")
            require(str(uuid.UUID(bytes=chunk)) == entry["chunk_id"], "chunk binding")
            require(header[80:112].hex() == RULE_SHA, "chunk rule")
            require(header[112:144].hex() == PHYSICAL_SHA, "chunk schema")
            require(header[144:176] == hashlib.sha256(provenance_bytes).digest(), "chunk provenance")
            require(header[208:240] == hashlib.sha256(capability).digest(), "chunk capability")
            payload = bundle[HEADER_BYTES:-FOOTER_BYTES]
            require(header[176:208] == hashlib.sha256(payload).digest(), "payload hash")
            require(footer[40:72] == hashlib.sha256(payload).digest(), "footer payload hash")
            require(footer[72:104] == hashlib.sha256(header).digest(), "footer header hash")
            require(footer[104:120] == chunk, "footer chunk")
            current: bytes | None = None
            previous: bytes | None = None
            expected_ply = 0
            occurrences: dict[bytes, int] = {}
            prior_terminal = True
            chunk_trajectories = 0
            for sequence in range(count):
                raw = payload[sequence * RECORD_BYTES : (sequence + 1) * RECORD_BYTES]
                record = decode_record(raw)
                require(record["sequence"] == sequence, "record sequence")
                require(record["provenance"] == hashlib.sha256(provenance_bytes).digest(), "record provenance")
                if record["trajectory_id"] != current:
                    require(prior_terminal and record["ply"] == 0, "trajectory boundary")
                    current = record["trajectory_id"]
                    previous = history_initial(current, record["provenance"])
                    expected_ply = 0
                    occurrences = {}
                    prior_terminal = False
                    chunk_trajectories += 1
                    trajectory_key = campaign + current
                    require(trajectory_key not in trajectories_by_role[role], "trajectory duplicate")
                    trajectories_by_role[role].add(trajectory_key)
                    require(split_role(manifest["partition_config"], campaign, current) == role, "split role")
                require(record["ply"] == expected_ply and previous is not None, "trajectory ply")
                expected_history = history_step(
                    previous,
                    record["ply"],
                    record["position"],
                    record["move"],
                )
                require(record["history"] == expected_history, "trajectory history")
                observed = occurrences.get(record["position"], 0) + 1
                occurrences[record["position"]] = observed
                require(record["repetition"] == observed, "trajectory repetition")
                previous = record["history"]
                expected_ply += 1
                prior_terminal = bool(record["flags"] & 2)
                row, row_identities = expected_row(
                    role, campaign, chunk, entry["chunk_index"], raw, record
                )
                expected_rows[role].append(row)
                raw_by_role[role].append(raw)
                for kind, key in row_identities.items():
                    identities[role][kind].append(key)
                model = row_identities["model_input_key"]
                (augmented_models if record["augmented"] else ordinary_models).add(model)
                coverage["pockets"] |= any(record["pockets"])
                coverage["drop"] |= record["move"][0] == 5
                coverage["promoted_provenance"] |= bool(record["promoted_mask"])
                if record["move"][0] not in {0, 5}:
                    coverage["capture_of_promoted"] |= bool(
                        record["promoted_mask"] & (1 << record["move"][2])
                    )
                coverage["castling_rights"] |= bool(record["castling_rights"])
                coverage["effective_en_passant"] |= record["effective_ep"] != NO_SQUARE
                coverage["repetition_history"] |= record["repetition"] > 1
                coverage["checkmate"] |= record["terminal"] == 1
                coverage["draw_terminal"] |= record["terminal"] in {2, 3, 4, 6}
                coverage["white_to_move_white_win"] |= (
                    record["side_to_move"] == 0 and record["game_result_white"] == 1
                )
                coverage["black_to_move_white_win"] |= (
                    record["side_to_move"] == 1 and record["game_result_white"] == 1
                )
                coverage["draw_white_to_move"] |= (
                    record["side_to_move"] == 0 and record["game_result_white"] == 0
                )
                coverage["draw_black_to_move"] |= (
                    record["side_to_move"] == 1 and record["game_result_white"] == 0
                )
                coverage["positive_centipawn"] |= (
                    record["teacher_kind"] == 1 and record["teacher_value"] > 0
                )
                coverage["negative_centipawn"] |= (
                    record["teacher_kind"] == 1 and record["teacher_value"] < 0
                )
                coverage["positive_mate"] |= (
                    record["teacher_kind"] == 2 and record["teacher_value"] > 0
                )
                coverage["negative_mate"] |= (
                    record["teacher_kind"] == 2 and record["teacher_value"] < 0
                )
            require(prior_terminal, "chunk incomplete trajectory")
            require(chunk_trajectories == entry["trajectory_count"], "chunk trajectories")
            receipt = parse_json(
                read_descriptor(fixture, entry["completion_receipt"], "completion"),
                "completion",
            )
            require(receipt["bundle"] == entry["bundle"], "receipt bundle")
            require(receipt["training_admissible"] is False, "receipt boundary")
        coverage["rank_reflection_color_swap"] = bool(augmented_models & ordinary_models)
        require(len(raw_by_role[role]) == role_doc["record_count"], f"{role} records")
        require(len(trajectories_by_role[role]) == role_doc["trajectory_count"], f"{role} trajectories")
        require(ordered_record_digest(raw_by_role[role]) == role_doc["ordered_record_stream_sha256"], f"{role} record stream")
        require(ordered_trajectory_digest(trajectories_by_role[role]) == role_doc["ordered_trajectory_set_sha256"], f"{role} trajectory set")
        observed_rows = (admitted / f"{role}.rows.jsonl").read_bytes()
        require(observed_rows == b"".join(expected_rows[role]), f"{role} output rows")
        total_records += len(raw_by_role[role])
        total_trajectories += len(trajectories_by_role[role])
    require(all(coverage.values()), "coverage gaps: " + ",".join(key for key, value in coverage.items() if not value))
    intersections: dict[str, int] = {}
    duplicates: dict[str, dict[str, int]] = {"train": {}, "validation": {}}
    expected_sets: dict[str, dict[str, dict[str, Any]]] = {"train": {}, "validation": {}}
    for kind in IDENTITY_KINDS:
        train_set = set(identities["train"][kind])
        validation_set = set(identities["validation"][kind])
        intersections[kind] = len(train_set & validation_set)
        for role, role_set in (("train", train_set), ("validation", validation_set)):
            duplicate_count = len(identities[role][kind]) - len(role_set)
            duplicates[role][kind] = duplicate_count
            expected_sets[role][kind] = {
                "duplicate_observations": duplicate_count,
                "observations": len(identities[role][kind]),
                "ordered_set_sha256": ordered_identity_digest(role, kind, role_set),
                "unique_keys": len(role_set),
            }
    require(all(value == 0 for value in intersections.values()), "independent cross-role intersection")
    declared = manifest["split_audit"]["within_role_duplicate_observations"]
    for role in ("train", "validation"):
        for kind in ("position_identity", "model_input_key"):
            require(declared[role][kind] == duplicates[role][kind], f"declared duplicates {role} {kind}")
    result_bytes = (admitted / "admission-result.json").read_bytes()
    result = parse_json(result_bytes, "admission result")
    require(result["status"] == "PASS_FIXTURE_NONADMISSIBLE", "result status")
    require(result["training_admissible"] is False and result["fixture_mode"] is True, "result boundary")
    require(result["source_manifest_sha256"] == sha256(manifest_bytes), "result manifest")
    require(result["intersections"] == intersections, "result intersections")
    require(result["sets"] == expected_sets, "result exact sets")
    require(total_records >= 32 and total_trajectories >= 8, "fixture minimums")
    return {
        "coverage": coverage,
        "intersections": intersections,
        "record_count": total_records,
        "trajectory_count": total_trajectories,
    }


def python_command(loader: Path, *arguments: str) -> list[str]:
    command = [sys.executable]
    command.extend("-O" for _ in range(sys.flags.optimize))
    command.extend(["-B", str(loader), *arguments])
    return command


def run_loader(loader: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        python_command(loader, *arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
        env=environment,
    )


def run_success(loader: Path, *arguments: str) -> Mapping[str, Any]:
    completed = run_loader(loader, *arguments)
    require(completed.returncode == 0, f"loader success failed: {completed.stderr!r}")
    require(completed.stderr == b"", "loader success stderr")
    return parse_json(completed.stdout, "loader stdout")


def tree_digest(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, sha256(path.read_bytes()))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_manifest(fixture: Path) -> dict[str, Any]:
    return dict(parse_json((fixture / "training-dataset-manifest.json").read_bytes(), "manifest mutation"))


def write_manifest(fixture: Path, manifest: Mapping[str, Any]) -> None:
    (fixture / "training-dataset-manifest.json").write_bytes(canonical_json(manifest))


def pin(relative: str, payload: bytes) -> dict[str, Any]:
    return {"path": relative, "bytes": len(payload), "sha256": sha256(payload)}


def bundle_records(payload: bytes) -> list[bytes]:
    count = struct.unpack_from("<Q", payload, 40)[0]
    return [
        payload[HEADER_BYTES + index * RECORD_BYTES : HEADER_BYTES + (index + 1) * RECORD_BYTES]
        for index in range(count)
    ]


def refresh_outer_bindings(fixture: Path, manifest: dict[str, Any]) -> None:
    aggregate_path = fixture / manifest["aggregate_chunk_set_receipt"]["path"]
    aggregate = dict(parse_json(aggregate_path.read_bytes(), "aggregate mutation"))
    for role in ("train", "validation"):
        role_doc = manifest["roles"][role]
        records: list[bytes] = []
        for entry in role_doc["chunks"]:
            for artifact_name in ("bundle", "provenance", "capability"):
                relative = entry[artifact_name]["path"]
                payload = (fixture / relative).read_bytes()
                entry[artifact_name] = pin(relative, payload)
            receipt_path = fixture / entry["completion_receipt"]["path"]
            receipt = dict(parse_json(receipt_path.read_bytes(), "receipt mutation"))
            for artifact_name in ("bundle", "provenance", "capability"):
                receipt[artifact_name] = entry[artifact_name]
            receipt_payload = canonical_json(receipt)
            receipt_path.write_bytes(receipt_payload)
            entry["completion_receipt"] = pin(
                entry["completion_receipt"]["path"], receipt_payload
            )
            records.extend(bundle_records((fixture / entry["bundle"]["path"]).read_bytes()))
        role_doc["ordered_chunk_set_sha256"] = ordered_chunk_digest(role_doc["chunks"])
        role_doc["ordered_record_stream_sha256"] = ordered_record_digest(records)
        aggregate["roles"][role]["ordered_chunk_set_sha256"] = role_doc[
            "ordered_chunk_set_sha256"
        ]
    aggregate_payload = canonical_json(aggregate)
    aggregate_path.write_bytes(aggregate_payload)
    manifest["aggregate_chunk_set_receipt"] = pin(
        manifest["aggregate_chunk_set_receipt"]["path"], aggregate_payload
    )
    write_manifest(fixture, manifest)


def repair_record_and_chunk(payload: bytearray, record_index: int) -> None:
    start = HEADER_BYTES + record_index * RECORD_BYTES
    struct.pack_into("<I", payload, start + 252, crc32c(bytes(payload[start : start + 252])))
    records_end = len(payload) - FOOTER_BYTES
    payload_digest = hashlib.sha256(payload[HEADER_BYTES:records_end]).digest()
    payload[176:208] = payload_digest
    struct.pack_into("<I", payload, 252, crc32c(bytes(payload[:252])))
    footer_start = records_end
    payload[footer_start + 40 : footer_start + 72] = payload_digest
    payload[footer_start + 72 : footer_start + 104] = hashlib.sha256(
        payload[:HEADER_BYTES]
    ).digest()
    struct.pack_into(
        "<I",
        payload,
        footer_start + 124,
        crc32c(bytes(payload[footer_start : footer_start + 124])),
    )


def reseal_chunk_after_provenance(
    payload: bytearray,
    provenance_sha: bytes,
    capability_sha: bytes,
) -> None:
    count = struct.unpack_from("<Q", payload, 40)[0]
    previous_by_trajectory: dict[bytes, bytes] = {}
    for index in range(count):
        start = HEADER_BYTES + index * RECORD_BYTES
        trajectory = bytes(payload[start + 32 : start + 48])
        ply = struct.unpack_from("<I", payload, start + 48)[0]
        previous = (
            history_initial(trajectory, provenance_sha)
            if ply == 0
            else previous_by_trajectory[trajectory]
        )
        position = bytes(payload[start + 148 : start + 180])
        move = bytes(payload[start + 120 : start + 124])
        history = history_step(previous, ply, position, move)
        payload[start + 180 : start + 212] = history
        payload[start + 212 : start + 244] = provenance_sha
        struct.pack_into(
            "<I",
            payload,
            start + 252,
            crc32c(bytes(payload[start : start + 252])),
        )
        previous_by_trajectory[trajectory] = history
    records_end = len(payload) - FOOTER_BYTES
    payload_digest = hashlib.sha256(payload[HEADER_BYTES:records_end]).digest()
    payload[144:176] = provenance_sha
    payload[176:208] = payload_digest
    payload[208:240] = capability_sha
    struct.pack_into("<I", payload, 252, crc32c(bytes(payload[:252])))
    payload[records_end + 40 : records_end + 72] = payload_digest
    payload[records_end + 72 : records_end + 104] = hashlib.sha256(
        payload[:HEADER_BYTES]
    ).digest()
    struct.pack_into(
        "<I",
        payload,
        records_end + 124,
        crc32c(bytes(payload[records_end : records_end + 124])),
    )


def make_production_shape(
    fixture: Path,
    provenance_case: str,
) -> None:
    manifest = load_manifest(fixture)
    manifest["fixture_mode"] = False
    manifest["training_admissible"] = True
    manifest["status"] = "READY_FOR_TRAINING"
    manifest["semantic_audit"] = {
        "engine_backed": True,
        "every_record_scanned": True,
        "every_trajectory_replayed": True,
        "history_prefix_and_repetition_reproduced": True,
        "make_undo_roundtrip": True,
        "physical_state_equals_replay": True,
        "split_decisions_recomputed": True,
        "status": "PASS",
        "stored_move_is_legal": True,
        "teacher_bound_and_perspective_reproduced": True,
        "terminal_reason_and_result_reproduced": True,
        "training_admissible": True,
    }
    aggregate_path = fixture / manifest["aggregate_chunk_set_receipt"]["path"]
    aggregate = dict(parse_json(aggregate_path.read_bytes(), "production aggregate"))
    aggregate["fixture_only"] = False
    aggregate["training_admissible"] = True
    aggregate["status"] = "PASS_PRODUCTION"
    aggregate["official_openbench_origin"] = "https://belzedar.duckdns.org"
    aggregate_payload = canonical_json(aggregate)
    aggregate_path.write_bytes(aggregate_payload)
    manifest["aggregate_chunk_set_receipt"] = pin(
        manifest["aggregate_chunk_set_receipt"]["path"], aggregate_payload
    )
    capability_path = fixture / "capability.json"
    legacy_capability = dict(
        parse_json(capability_path.read_bytes(), "legacy fixture capability")
    )
    first_entry = manifest["roles"]["train"]["chunks"][0]
    first_provenance = parse_json(
        (fixture / first_entry["provenance"]["path"]).read_bytes(),
        "legacy fixture provenance",
    )
    capability = {
        "artifact_bytes": legacy_capability["artifact_bytes"],
        "artifact_role": "crazyhouse-physical-datagen-production-v1",
        "artifact_sha256": legacy_capability["artifact_sha256"],
        "build_recipe_sha256": legacy_capability["build_recipe_sha256"],
        "capability_contract_sha256": PRODUCTION_CAPABILITY_SHA,
        "challenge": legacy_capability["challenge"],
        "command": "crazyhouse_generate_physical_production_v1",
        "openbench_publication_protocol": 41,
        "physical_record_bytes": 256,
        "physical_schema_sha256": PHYSICAL_SHA,
        "producer_source_commit": legacy_capability["source_commit"],
        "producer_source_dirty": False,
        "producer_source_tree": legacy_capability["source_tree"],
        "producer_src_tree": legacy_capability["src_tree"],
        "production_generation_authorized": provenance_case != "unauthorized",
        "registered_network_bytes": 58_534_811,
        "registered_network_sha256": PRODUCTION_NETWORK_SHA,
        "rule_profile_id": "LICHESS_CRAZYHOUSE_2026_08_12",
        "rule_profile_sha256": RULE_SHA,
        "schema": "crazyhouse-datagen-production-capability-response/v1",
        "selection_policy_sha256": PRODUCTION_POLICY_SHA,
        "toolchain_identity": first_provenance["toolchain"]["identity"],
        "toolchain_sha256": legacy_capability["toolchain_sha256"],
        "trajectory_partition_domain": (
            "Crazyhouse-Stockfish physical trajectory split v1\\0"
            if provenance_case == "domain-printable"
            else SPLIT_DOMAIN.decode("ascii")
        ),
        "variant": "crazyhouse",
    }
    capability_payload = canonical_json(capability)
    capability_path.write_bytes(capability_payload)
    capability_sha = hashlib.sha256(capability_payload).digest()
    for role in ("train", "validation"):
        for entry in manifest["roles"][role]["chunks"]:
            provenance_path = fixture / entry["provenance"]["path"]
            provenance = dict(parse_json(provenance_path.read_bytes(), "production provenance"))
            producer = {
                "bytes": capability["artifact_bytes"],
                "kind": capability["artifact_role"],
                "path": "artifacts/crazyhouse-physical-datagen.exe",
                "sha256": capability["artifact_sha256"],
            }
            provenance.update(
                {
                    "adjudication": {
                        "claim_policy": "automatic-only",
                        "fivefold_automatic": True,
                        "insufficient_material": False,
                        "resignation": False,
                        "rule50": False,
                        "threefold_claim": False,
                    },
                    "cohort": "training-admission-production-shape-v1",
                    "external_workload_id": "local-structural-negative-only",
                    "generation_settings": {
                        "accepted_trajectories": entry["trajectory_count"],
                        "base_seed": int(provenance["seed"]),
                        "candidate_games_examined": entry["trajectory_count"],
                        "complete_trajectory_only": True,
                        "depth_cap": 64,
                        "exact_count": True,
                        "exact_quota_algorithm": "deterministic-first-reachable-exact-subset-v1",
                        "exploration_max_score_diff_internal": 256,
                        "exploration_multipv": 4,
                        "exploration_plies": 8,
                        "fixture_only": False,
                        "hash_mib": 128,
                        "max_candidate_games": max(1, entry["trajectory_count"]),
                        "max_game_ply": 512,
                        "nodes_per_position": 16384,
                        "production_generation_authorized": True,
                        "record_count": entry["record_count"],
                        "role_eligible_complete_candidates": entry["trajectory_count"],
                        "role_ineligible_candidates": 0,
                        "subset_candidates_omitted": 0,
                        "threads": 1,
                        "training_admissible": True,
                        "wall_time_encoded": False,
                    },
                    "invalid_game_policy": {
                        "bound_or_missing_pv": "quarantine-game",
                        "complete_trajectory_oversize": "quarantine-game",
                        "crash": "abort-chunk",
                        "illegal_move": "quarantine-game",
                        "observed_rejections": [],
                        "safety_limit": "quarantine-game",
                        "unreachable_exact_quota": "abort-chunk",
                    },
                    "network": {
                        "bytes": 58_534_811,
                        "compatibility": "qualified-positive-and-negative-load",
                        "format": "legacy-halfkav2variants-v1",
                        "license": "CC0-1.0",
                        "path": "artifacts/networks/crazyhouse_run15rl_e190_l03.nnue",
                        "sha256": PRODUCTION_NETWORK_SHA,
                        "used": True,
                    },
                    "official_openbench_origin": "https://belzedar.duckdns.org",
                    "openbench_assignment": {"worker_threads_capacity": 12},
                    "openbench_publication_protocol": 41,
                    "opening_source": {
                        "artifact": {
                            "bytes": 39_922,
                            "kind": "official-crazyhouse-epd-physical-roots-v1",
                            "license": "GPL-3.0-or-later",
                            "path": "openbench/books/CRAZYHOUSE_openings.epd",
                            "roots": 599,
                            "sha256": PRODUCTION_BOOK_SHA,
                        },
                        "engine_selected": False,
                        "kind": "deterministic-authenticated-book-order",
                        "match_result_selected": False,
                        "selection_policy_sha256": PRODUCTION_POLICY_SHA,
                    },
                    "partition": {
                        "campaign_set_sha256": manifest["partition_config"]["campaign_set_sha256"],
                        "domain": manifest["partition_config"]["domain"],
                        "label_free": True,
                        "method": manifest["partition_config"]["method"],
                        "partition_sha256": manifest["partition_config"]["sha256"],
                        "posthoc_rebalance": False,
                        "role": role,
                        "split_seed_u64": manifest["partition_config"]["split_seed_u64"],
                        "validation_threshold_u64": manifest["partition_config"]["validation_threshold_u64"],
                    },
                    "producer_artifact": producer,
                    "producer_capability": {
                        "bytes": len(capability_payload),
                        "challenge": capability["challenge"],
                        "schema": "crazyhouse-datagen-production-capability-response/v1",
                        "sha256": capability_sha.hex(),
                    },
                    "teacher": {
                        "artifact": {
                            "bytes": producer["bytes"],
                            "path": producer["path"],
                            "sha256": producer["sha256"],
                        },
                        "bound_policy": "selected-line-exact-only",
                        "evaluator_mode": "legacy-scalar-full-refresh",
                        "kind": "legacy-network-product-search",
                        "network_used": True,
                        "route_backend_identity": "legacy-scalar-full-refresh",
                        "score_perspective": "side-to-move",
                        "search_settings_sha256": provenance["teacher"]["search_settings_sha256"],
                        "selected_line_owns_score_and_pv": True,
                        "synthetic": False,
                    },
                }
            )
            if provenance_case == "source-dirty":
                provenance["source_dirty"] = True
            elif provenance_case == "synthetic":
                settings = provenance["generation_settings"]
                settings["candidate_games_examined"] += 1
                settings["max_candidate_games"] = settings["candidate_games_examined"]
                provenance["invalid_game_policy"]["observed_rejections"] = [
                    {
                        "candidate_index": settings["candidate_games_examined"] - 1,
                        "reason": "independent-valid-rejection-accounting-probe",
                        "root_id": "fixture-root",
                    }
                ]
                provenance["teacher"] = dict(provenance["teacher"])
                provenance["teacher"]["synthetic"] = True
            elif provenance_case == "golden":
                provenance["teacher"] = dict(provenance["teacher"])
                provenance["teacher"]["kind"] = "golden-fixture"
            provenance_payload = canonical_json(provenance)
            provenance_path.write_bytes(provenance_payload)
            bundle_path = fixture / entry["bundle"]["path"]
            bundle = bytearray(bundle_path.read_bytes())
            reseal_chunk_after_provenance(
                bundle,
                hashlib.sha256(provenance_payload).digest(),
                capability_sha,
            )
            bundle_path.write_bytes(bundle)
    refresh_outer_bindings(fixture, manifest)


def copied_fixture(base: Path, root: Path, label: str) -> Path:
    destination = root / label / "fixture"
    destination.parent.mkdir(parents=True)
    shutil.copytree(base, destination)
    return destination


def expect_failure(
    loader: Path,
    fixture: Path,
    output: Path,
    expected_code: str,
    *,
    mode: str = "fixture",
    preserve_output: bool = False,
    expected_detail: str | None = None,
) -> str:
    completed = run_loader(
        loader,
        "admit",
        "--mode",
        mode,
        "--manifest",
        str(fixture / "training-dataset-manifest.json"),
        "--output",
        str(output),
    )
    require(completed.returncode == 2 and completed.stdout == b"", f"negative {expected_code}: process")
    error = parse_json(completed.stderr, f"negative {expected_code}")
    require(error["code"] == expected_code, f"negative expected {expected_code}, got {error}")
    if expected_detail is not None:
        require(
            expected_detail in error["detail"],
            f"negative expected detail {expected_detail!r}, got {error}",
        )
    if not preserve_output:
        require(not output.exists(), f"negative {expected_code}: output leaked")
    require(not output.with_name(output.name + ".partial").exists(), f"negative {expected_code}: partial leaked")
    return expected_code


def adversarial_matrix(loader: Path, base: Path, root: Path) -> list[str]:
    passed: list[str] = []

    fixture = copied_fixture(base, root, "missing")
    manifest = load_manifest(fixture)
    (fixture / manifest["roles"]["train"]["chunks"][0]["bundle"]["path"]).unlink()
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "ARTIFACT_MISSING"))

    fixture = copied_fixture(base, root, "nonregular")
    manifest = load_manifest(fixture)
    path = fixture / manifest["roles"]["train"]["chunks"][0]["bundle"]["path"]
    path.unlink()
    path.mkdir()
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "ARTIFACT_NONREGULAR"))

    fixture = copied_fixture(base, root, "symlink")
    manifest = load_manifest(fixture)
    entry = manifest["roles"]["train"]["chunks"][0]
    path = fixture / entry["bundle"]["path"]
    target = fixture / manifest["roles"]["train"]["chunks"][1]["bundle"]["path"]
    path.unlink()
    try:
        os.symlink(target, path)
    except OSError as exc:
        raise VerificationFailure(f"symlink negative unavailable: {exc}") from exc
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "ARTIFACT_SYMLINK"))

    for label, operation, expected in (
        ("truncation", lambda data: data[:-1], "CHUNK_FRAMING"),
        ("extension", lambda data: data + b"x", "CHUNK_FRAMING"),
    ):
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        entry = manifest["roles"]["train"]["chunks"][0]
        path = fixture / entry["bundle"]["path"]
        path.write_bytes(operation(path.read_bytes()))
        refresh_outer_bindings(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    for label, offset, expected in (
        ("header-crc", 252, "CHUNK_HEADER_CRC32C"),
        ("record-crc", HEADER_BYTES + 252, "PHYSICAL_RECORD"),
        ("footer-crc", -1, "CHUNK_FOOTER_CRC32C"),
    ):
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        entry = manifest["roles"]["train"]["chunks"][0]
        path = fixture / entry["bundle"]["path"]
        data = bytearray(path.read_bytes())
        data[offset] ^= 1
        path.write_bytes(data)
        refresh_outer_bindings(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    for label, offset, expected in (
        ("wrong-rule", 80, "CHUNK_RULE_PROFILE"),
        ("wrong-schema", 112, "CHUNK_SCHEMA_IDENTITY"),
    ):
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        entry = manifest["roles"]["train"]["chunks"][0]
        path = fixture / entry["bundle"]["path"]
        data = bytearray(path.read_bytes())
        data[offset] ^= 1
        struct.pack_into("<I", data, 252, crc32c(bytes(data[:252])))
        path.write_bytes(data)
        refresh_outer_bindings(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    fixture = copied_fixture(base, root, "payload-digest")
    manifest = load_manifest(fixture)
    entry = manifest["roles"]["train"]["chunks"][0]
    path = fixture / entry["bundle"]["path"]
    data = bytearray(path.read_bytes())
    data[176] ^= 1
    struct.pack_into("<I", data, 252, crc32c(bytes(data[:252])))
    path.write_bytes(data)
    refresh_outer_bindings(fixture, manifest)
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "CHUNK_PAYLOAD_SHA256"))

    for label, field, value, expected in (
        ("manifest-variant", "variant", "standard", "MANIFEST_PROJECT_VARIANT"),
        ("manifest-rule", "rule_profile", {"id": "wrong", "sha256": RULE_SHA}, "MANIFEST_RULE_PROFILE"),
    ):
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        manifest[field] = value
        write_manifest(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    fixture = copied_fixture(base, root, "feature-contract")
    manifest = load_manifest(fixture)
    path = fixture / manifest["feature_contract"]["path"]
    path.write_bytes(path.read_bytes() + b"x")
    manifest["feature_contract"] = pin(manifest["feature_contract"]["path"], path.read_bytes())
    write_manifest(fixture, manifest)
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "FEATURE_CONTRACT_IDENTITY"))

    fixture = copied_fixture(base, root, "noncanonical-manifest")
    manifest = load_manifest(fixture)
    (fixture / "training-dataset-manifest.json").write_bytes(
        json.dumps(manifest, indent=2).encode("utf-8") + b"\n"
    )
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "JSON_NONCANONICAL"))

    fixture = copied_fixture(base, root, "duplicate-key-manifest")
    path = fixture / "training-dataset-manifest.json"
    payload = path.read_bytes()
    path.write_bytes(payload[:-2] + b',"variant":"crazyhouse"}\n')
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "JSON_DUPLICATE_KEY"))

    for label, mutation, expected in (
        (
            "missing-chunk",
            lambda manifest: manifest["roles"]["train"]["chunks"].pop(),
            "ROLE_CHUNK_COUNT",
        ),
        (
            "duplicate-chunk",
            lambda manifest: (
                manifest["roles"]["train"]["chunks"].append(
                    dict(manifest["roles"]["train"]["chunks"][0])
                ),
                manifest["roles"]["train"].__setitem__("chunk_count", 3),
            ),
            "CHUNK_ID_DUPLICATE",
        ),
        (
            "reordered-chunks",
            lambda manifest: manifest["roles"]["train"]["chunks"].reverse(),
            "ROLE_CHUNK_SET_DIGEST",
        ),
    ):
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        mutation(manifest)
        if label == "duplicate-chunk":
            manifest["roles"]["train"]["ordered_chunk_set_sha256"] = ordered_chunk_digest(
                manifest["roles"]["train"]["chunks"]
            )
        write_manifest(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    for label, field, transform, expected in (
        ("wrong-campaign", "campaign_id", lambda _: "70000000-0000-4000-8000-000000000001", "PARTITION_CAMPAIGN_SET"),
        ("wrong-chunk", "chunk_id", lambda _: "70000000-0000-4000-8000-000000000002", "AGGREGATE_ROLE_BINDING"),
        ("wrong-count", "record_count", lambda value: value + 1, "CHUNK_RECORD_COUNT"),
    ):
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        entry = manifest["roles"]["train"]["chunks"][0]
        entry[field] = transform(entry[field])
        write_manifest(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    fixture = copied_fixture(base, root, "receipt-binding")
    manifest = load_manifest(fixture)
    entry = manifest["roles"]["train"]["chunks"][0]
    path = fixture / entry["completion_receipt"]["path"]
    receipt = dict(parse_json(path.read_bytes(), "receipt mutation"))
    receipt["chunk_index"] += 1
    payload = canonical_json(receipt)
    path.write_bytes(payload)
    entry["completion_receipt"] = pin(entry["completion_receipt"]["path"], payload)
    write_manifest(fixture, manifest)
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "CHUNK_RECEIPT_BINDING"))

    for label, artifact_name, expected in (
        ("corrupt-capability", "capability", "CAPABILITY_OR_PROVENANCE"),
        ("corrupt-provenance", "provenance", "JSON_PARSE"),
    ):
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        entry = manifest["roles"]["train"]["chunks"][0]
        path = fixture / entry[artifact_name]["path"]
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 1
        path.write_bytes(data)
        refresh_outer_bindings(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    record_mutations = (
        ("wrong-result-perspective", 125, lambda data, offset: data.__setitem__(offset, 0), "PHYSICAL_RECORD"),
        ("teacher-kind", 126, lambda data, offset: data.__setitem__(offset, 9), "PHYSICAL_RECORD"),
        ("teacher-bound", 127, lambda data, offset: data.__setitem__(offset, 2), "PHYSICAL_RECORD"),
        ("trajectory-sequence", 8, lambda data, offset: struct.pack_into("<Q", data, offset, 99), "RECORD_SEQUENCE"),
        ("trajectory-history", 180, lambda data, offset: data.__setitem__(offset, data[offset] ^ 1), "TRAJECTORY_HISTORY"),
    )
    for label, relative_offset, mutation, expected in record_mutations:
        fixture = copied_fixture(base, root, label)
        manifest = load_manifest(fixture)
        entry = manifest["roles"]["train"]["chunks"][1]
        path = fixture / entry["bundle"]["path"]
        data = bytearray(path.read_bytes())
        record_index = 0
        if label in {"wrong-result-perspective", "teacher-kind", "teacher-bound"}:
            for candidate, raw in enumerate(bundle_records(data)):
                if struct.unpack_from("<I", raw, 52)[0] & 4:
                    record_index = candidate
                    break
        offset = HEADER_BYTES + record_index * RECORD_BYTES + relative_offset
        mutation(data, offset)
        repair_record_and_chunk(data, record_index)
        path.write_bytes(data)
        refresh_outer_bindings(fixture, manifest)
        passed.append(expect_failure(loader, fixture, fixture.parent / "out", expected))

    fixture = copied_fixture(base, root, "terminal-record")
    manifest = load_manifest(fixture)
    entry = manifest["roles"]["train"]["chunks"][0]
    path = fixture / entry["bundle"]["path"]
    data = bytearray(path.read_bytes())
    last = len(bundle_records(data)) - 1
    offset = HEADER_BYTES + last * RECORD_BYTES + 111
    data[offset] = 0
    repair_record_and_chunk(data, last)
    path.write_bytes(data)
    refresh_outer_bindings(fixture, manifest)
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "PHYSICAL_RECORD"))

    fixture = copied_fixture(base, root, "label-dependent-split")
    manifest = load_manifest(fixture)
    manifest["partition_config"]["label_source"] = "game_result_white"
    write_manifest(fixture, manifest)
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "PARTITION_CONFIG"))

    fixture = copied_fixture(base, root, "partition-drift")
    manifest = load_manifest(fixture)
    manifest["partition_config"]["sha256"] = "0" * 64
    write_manifest(fixture, manifest)
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "PARTITION_DIGEST"))

    fixture = copied_fixture(base, root, "split-role")
    manifest = load_manifest(fixture)
    manifest["partition_config"]["validation_threshold_u64"] = 0
    body = {
        key: value
        for key, value in manifest["partition_config"].items()
        if key != "sha256"
    }
    manifest["partition_config"]["sha256"] = sha256(canonical_json(body))
    write_manifest(fixture, manifest)
    passed.append(expect_failure(loader, fixture, fixture.parent / "out", "SPLIT_ROLE_MISMATCH"))

    for label, provenance_case, expected, detail in (
        (
            "production-domain-printable-backslash-zero",
            "domain-printable",
            "CAPABILITY_OR_PROVENANCE",
            "production capability trajectory_partition_domain drifted",
        ),
        ("production-unauthorized", "unauthorized", "CAPABILITY_OR_PROVENANCE", None),
        ("production-source-dirty", "source-dirty", "PROVENANCE", None),
        (
            "production-synthetic",
            "synthetic",
            "PROVENANCE",
            "production teacher drifted",
        ),
        ("production-golden", "golden", "PROVENANCE", None),
    ):
        fixture = copied_fixture(base, root, label)
        make_production_shape(fixture, provenance_case)
        passed.append(
            expect_failure(
                loader,
                fixture,
                fixture.parent / "out",
                expected,
                mode="production",
                expected_detail=detail,
            )
        )

    fixture = copied_fixture(base, root, "output-collision")
    output = fixture.parent / "out"
    output.mkdir()
    marker = output / "marker"
    marker.write_bytes(b"preserve")
    passed.append(
        expect_failure(
            loader,
            fixture,
            output,
            "OUTPUT_EXISTS",
            preserve_output=True,
        )
    )
    require(marker.read_bytes() == b"preserve", "output collision replaced data")

    fixture = copied_fixture(base, root, "partial-collision")
    output = fixture.parent / "out"
    partial = output.with_name(output.name + ".partial")
    partial.mkdir()
    completed = run_loader(
        loader,
        "admit",
        "--mode",
        "fixture",
        "--manifest",
        str(fixture / "training-dataset-manifest.json"),
        "--output",
        str(output),
    )
    error = parse_json(completed.stderr, "partial collision")
    require(completed.returncode == 2 and error["code"] == "OUTPUT_PARTIAL_EXISTS", "partial collision")
    require(partial.exists() and not output.exists(), "partial collision mutation")
    passed.append("OUTPUT_PARTIAL_EXISTS")

    return passed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loader", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    loader = args.loader.resolve(strict=True)
    try:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-training-admission-verify-") as temporary:
            root = Path(temporary)
            fixture_a = root / "fixture-a"
            fixture_b = root / "fixture-b"
            build_a = run_success(loader, "build-fixture", "--output", str(fixture_a))
            build_b = run_success(loader, "build-fixture", "--output", str(fixture_b))
            require(build_a == build_b, "fixture build result differs")
            require(tree_digest(fixture_a) == tree_digest(fixture_b), "two fixture exports differ")
            admitted_a = root / "admitted-a"
            admitted_b = root / "admitted-b"
            run_success(
                loader,
                "admit",
                "--mode",
                "fixture",
                "--manifest",
                str(fixture_a / "training-dataset-manifest.json"),
                "--output",
                str(admitted_a),
            )
            run_success(
                loader,
                "admit",
                "--mode",
                "fixture",
                "--manifest",
                str(fixture_b / "training-dataset-manifest.json"),
                "--output",
                str(admitted_b),
            )
            require(tree_digest(admitted_a) == tree_digest(admitted_b), "admission outputs differ")
            independent = independent_full_scan(fixture_a, admitted_a)
            identity_self_test = run_success(loader, "self-test-identities")
            require(
                identity_self_test["cases"] == {kind: 1 for kind in IDENTITY_KINDS},
                "identity intersection self-test",
            )
            negatives = adversarial_matrix(loader, fixture_a, root / "negatives")
            result = {
                "clean_fixture_exports": 2,
                "cross_profile_normalized": True,
                "fixture_mode": True,
                "identity_intersection_cases": len(identity_self_test["cases"]),
                "independent_full_scan": independent,
                "negative_cases": len(negatives),
                "negative_codes": negatives,
                "production_training_admissible": False,
                "schema": "crazyhouse-nnue-v2-training-admission-verification/v1",
                "status": "PASS",
            }
            sys.stdout.buffer.write(canonical_json(result))
            return 0
    except (OSError, ValueError, KeyError, TypeError, VerificationFailure) as exc:
        sys.stderr.write(f"training admission verification failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
