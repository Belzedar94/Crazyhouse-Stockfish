#!/usr/bin/env python3
"""Fail-closed admission of physical Crazyhouse records for NNUE V2 training.

The physical 256-byte records remain canonical.  This tool authenticates a
dataset manifest and its chunk set, derives disposable V2 feature rows, keeps
raw labels untouched, and uses disk-backed exact sets for split auditing.
Fixture construction exists only for the dedicated engineering test target.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
from typing import Any, BinaryIO, Mapping, Sequence
import uuid


ROOT = Path(__file__).resolve().parents[2]
PHYSICAL_CODEC_PATH = ROOT / "tools" / "datagen" / "crazyhouse_physical_v1.py"
PRODUCTION_CODEC_PATH = ROOT / "tools" / "datagen" / "crazyhouse_production_v1.py"
PHYSICAL_SCHEMA_PATH = ROOT / "schemas" / "crazyhouse-physical-v1.schema.json"
FEATURE_CONTRACT_PATH = ROOT / "schemas" / "crazyhouse-nnue-v2-features-v1.json"
ADMISSION_CONTRACT_PATH = ROOT / "schemas" / "crazyhouse-nnue-v2-training-admission-v1.json"
FIXTURE_CAPABILITY_CONTRACT_PATH = (
    ROOT / "tests" / "crazyhouse" / "datagen-capability-v1.json"
)
PRODUCTION_CAPABILITY_CONTRACT_PATH = (
    ROOT / "tests" / "crazyhouse" / "datagen-production-capability-v1.json"
)
GOLDEN_CAPABILITY_PATH = (
    ROOT / "tests" / "crazyhouse" / "data" / "crazyhouse-physical-v1-golden-capability-response.json"
)
GOLDEN_PROVENANCE_PATH = (
    ROOT / "tests" / "crazyhouse" / "data" / "crazyhouse-physical-v1-golden-provenance.json"
)
GOLDEN_UNIT_PATH = ROOT / "tests" / "crazyhouse_physical_v1_unit.py"

RULE_PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
RULE_PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PHYSICAL_SCHEMA_SHA256 = "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
FEATURE_CONTRACT_SHA256 = "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6"
ADMISSION_CONTRACT_SHA256 = "070ce5232b790506dcfd65e4ddd76a91e16a2e1bd71a1dee198f0eb3c37517f5"
PRODUCTION_CAPABILITY_CONTRACT_SHA256 = (
    "23386f8c51307522b08fbe3bef309791c90e40022a62e073eaaaf08a9467397b"
)
OFFICIAL_OPENBENCH = "https://belzedar.duckdns.org"

MANIFEST_SCHEMA = "crazyhouse-training-dataset-manifest/v1"
RESULT_SCHEMA = "crazyhouse-nnue-v2-training-admission-result/v1"
ROW_SCHEMA = "crazyhouse-nnue-v2-physical-row/v1"
CHUNK_RECEIPT_SCHEMA = "crazyhouse-training-chunk-completion-receipt/v1"
AGGREGATE_RECEIPT_SCHEMA = "crazyhouse-training-chunk-set-receipt/v1"

SPLIT_DOMAIN = b"Crazyhouse-Stockfish physical trajectory split v1\0"
RAW_RECORD_DOMAIN = b"Crazyhouse-Stockfish physical record identity v1\0"
MODEL_INPUT_DOMAIN = b"Crazyhouse-Stockfish NNUE V2 model input identity v1\0"
CAMPAIGN_SET_DOMAIN = b"Crazyhouse-Stockfish campaign set v1\0"
CHUNK_SET_DOMAIN = b"Crazyhouse-Stockfish ordered chunk set v1\0"
RECORD_STREAM_DOMAIN = b"Crazyhouse-Stockfish ordered record stream v1\0"
TRAJECTORY_SET_DOMAIN = b"Crazyhouse-Stockfish ordered trajectory set v1\0"
IDENTITY_SET_DOMAIN = b"Crazyhouse-Stockfish ordered admission identity set v1\0"

FEATURE_DIMENSIONS = 902
MAXIMUM_ACTIVE = 138
POCKET_TYPE_BASE = (0, 34, 44, 54, 64)
POCKET_WIDTHS = (17, 5, 5, 5, 3)
IDENTITY_KINDS = (
    "raw_record_key",
    "position_identity",
    "model_input_key",
    "game_id",
    "trajectory_id",
)
ROLE_IDS = {"train": 0, "validation": 1}

MANIFEST_KEYS = {
    "schema",
    "project",
    "variant",
    "status",
    "training_admissible",
    "fixture_mode",
    "rule_profile",
    "physical_schema",
    "feature_contract",
    "partition_config",
    "roles",
    "split_audit",
    "semantic_audit",
    "aggregate_chunk_set_receipt",
    "admission_tool",
    "created_utc",
}
ROLE_KEYS = {
    "role",
    "chunks",
    "chunk_count",
    "record_count",
    "trajectory_count",
    "ordered_chunk_set_sha256",
    "ordered_record_stream_sha256",
    "ordered_trajectory_set_sha256",
}
CHUNK_KEYS = {
    "bundle",
    "provenance",
    "capability",
    "completion_receipt",
    "campaign_id",
    "chunk_id",
    "chunk_index",
    "record_count",
    "trajectory_count",
}
ARTIFACT_KEYS = {"path", "bytes", "sha256"}
PARTITION_KEYS = {
    "method",
    "domain",
    "split_seed_u64",
    "validation_threshold_u64",
    "campaign_set_sha256",
    "rule_profile_sha256",
    "physical_schema_sha256",
    "feature_contract_sha256",
    "sha256",
}
CHUNK_RECEIPT_KEYS = {
    "schema",
    "status",
    "project",
    "variant",
    "fixture_only",
    "training_admissible",
    "official_openbench_origin",
    "campaign_id",
    "chunk_id",
    "chunk_index",
    "bundle",
    "provenance",
    "capability",
    "record_count",
    "trajectory_count",
}
AGGREGATE_KEYS = {
    "schema",
    "status",
    "project",
    "variant",
    "fixture_only",
    "training_admissible",
    "official_openbench_origin",
    "campaign_ids",
    "chunk_count",
    "record_count",
    "trajectory_count",
    "roles",
    "exact_total",
}


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


codec = _load_module("crazyhouse_physical_v1", PHYSICAL_CODEC_PATH)
production_codec = _load_module("crazyhouse_production_v1", PRODUCTION_CODEC_PATH)


class AdmissionError(RuntimeError):
    """Stable fail-closed rejection."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def reject(code: str, detail: str) -> None:
    raise AdmissionError(code, detail)


def require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        reject(code, detail)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            reject("JSON_DUPLICATE_KEY", key)
        result[key] = value
    return result


def parse_strict_json(payload: bytes, label: str, *, maximum_bytes: int = 16 * 1024 * 1024) -> Mapping[str, Any]:
    require(len(payload) <= maximum_bytes, "JSON_TOO_LARGE", label)
    require(not payload.startswith(b"\xef\xbb\xbf"), "JSON_BOM", label)
    require(b"\r" not in payload, "JSON_LINE_ENDING", label)
    require(
        payload.endswith(b"\n") and not payload.endswith(b"\n\n"),
        "JSON_FINAL_LF",
        label,
    )
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except AdmissionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        reject("JSON_PARSE", f"{label}: {exc}")
    require(isinstance(document, dict), "JSON_ROOT", label)
    require(payload == canonical_json(document), "JSON_NONCANONICAL", label)
    return document


def _same_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    return all(getattr(before, field) == getattr(after, field) for field in fields)


def read_regular(
    path: Path,
    label: str,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    maximum_bytes: int | None = None,
) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        reject("ARTIFACT_MISSING", f"{label}: {exc}")
    require(not stat.S_ISLNK(metadata.st_mode), "ARTIFACT_SYMLINK", label)
    require(stat.S_ISREG(metadata.st_mode), "ARTIFACT_NONREGULAR", label)
    if expected_bytes is not None:
        require(metadata.st_size == expected_bytes, "ARTIFACT_BYTES", label)
    if maximum_bytes is not None:
        require(metadata.st_size <= maximum_bytes, "ARTIFACT_TOO_LARGE", label)
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            payload = stream.read()
            after = os.fstat(stream.fileno())
    except OSError as exc:
        reject("ARTIFACT_READ", f"{label}: {exc}")
    require(_same_snapshot(before, after), "ARTIFACT_CHANGED", label)
    if expected_bytes is not None:
        require(len(payload) == expected_bytes, "ARTIFACT_BYTES", label)
    if expected_sha256 is not None:
        require(sha256_bytes(payload) == expected_sha256, "ARTIFACT_SHA256", label)
    return payload


def validate_hex(value: Any, width: int, code: str, label: str) -> str:
    require(
        isinstance(value, str) and len(value) == width and value == value.lower(),
        code,
        label,
    )
    try:
        bytes.fromhex(value)
    except ValueError:
        reject(code, label)
    return value


def validate_u64(value: Any, code: str, label: str) -> int:
    require(type(value) is int and 0 <= value <= 0xFFFFFFFFFFFFFFFF, code, label)
    return value


def validate_uuid(value: Any, code: str, label: str) -> uuid.UUID:
    require(isinstance(value, str), code, label)
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        reject(code, label)
    require(str(parsed) == value and parsed.int != 0, code, label)
    return parsed


def validate_artifact(value: Any, label: str) -> Mapping[str, Any]:
    require(isinstance(value, dict) and set(value) == ARTIFACT_KEYS, "ARTIFACT_DESCRIPTOR", label)
    path = value["path"]
    require(isinstance(path, str) and path, "ARTIFACT_PATH", label)
    pure = PurePosixPath(path)
    require(
        not pure.is_absolute()
        and "\\" not in path
        and ":" not in path
        and ".." not in pure.parts
        and "." not in pure.parts,
        "ARTIFACT_PATH",
        label,
    )
    require(type(value["bytes"]) is int and value["bytes"] >= 0, "ARTIFACT_DESCRIPTOR", label)
    validate_hex(value["sha256"], 64, "ARTIFACT_DESCRIPTOR", f"{label}.sha256")
    return value


def artifact_path(root: Path, descriptor: Mapping[str, Any], label: str) -> Path:
    validate_artifact(descriptor, label)
    target = root.joinpath(*PurePosixPath(descriptor["path"]).parts)
    cursor = root
    for part in PurePosixPath(descriptor["path"]).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            reject("ARTIFACT_SYMLINK", label)
    return target


def read_artifact(root: Path, descriptor: Mapping[str, Any], label: str) -> bytes:
    path = artifact_path(root, descriptor, label)
    return read_regular(
        path,
        label,
        expected_bytes=descriptor["bytes"],
        expected_sha256=descriptor["sha256"],
    )


def descriptor(path: str, payload: bytes) -> dict[str, Any]:
    return {"path": path, "bytes": len(payload), "sha256": sha256_bytes(payload)}


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        reject("OUTPUT_COLLISION", str(path))


def feature_rows(record: Any, perspective: int) -> tuple[int, ...]:
    require(perspective in {0, 1}, "FEATURE_PERSPECTIVE", str(perspective))
    rows: list[int] = []
    for square, code in enumerate(record.board):
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
            count = record.pockets[absolute_owner * 5 + piece_type]
            rows.append(
                768
                + POCKET_TYPE_BASE[piece_type]
                + relative_owner * POCKET_WIDTHS[piece_type]
                + count
            )
    for square in range(64):
        if record.promoted_mask & (1 << square):
            rows.append(838 + (square if perspective == 0 else square ^ 56))
    rows.sort()
    require(len(rows) <= MAXIMUM_ACTIVE, "FEATURE_ACTIVE_OVERFLOW", str(len(rows)))
    require(len(rows) == len(set(rows)), "FEATURE_DUPLICATE_ROW", "")
    require(all(0 <= row < FEATURE_DIMENSIONS for row in rows), "FEATURE_ROW_RANGE", "")
    return tuple(rows)


def model_input_key(stm_rows: Sequence[int], opponent_rows: Sequence[int]) -> bytes:
    payload = bytearray(MODEL_INPUT_DOMAIN)
    payload.extend(bytes.fromhex(FEATURE_CONTRACT_SHA256))
    payload.extend(struct.pack("<I", len(stm_rows)))
    for row in sorted(stm_rows):
        payload.extend(struct.pack("<I", row))
    payload.extend(struct.pack("<I", len(opponent_rows)))
    for row in sorted(opponent_rows):
        payload.extend(struct.pack("<I", row))
    return hashlib.sha256(payload).digest()


def split_role(config: Mapping[str, Any], campaign_id: bytes, trajectory_id: bytes) -> str:
    value = int.from_bytes(
        hashlib.sha256(
            SPLIT_DOMAIN
            + struct.pack("<Q", config["split_seed_u64"])
            + campaign_id
            + trajectory_id
        ).digest()[:8],
        "little",
    )
    return "validation" if value < config["validation_threshold_u64"] else "train"


def campaign_set_sha256(campaigns: Sequence[bytes]) -> str:
    ordered = sorted(set(campaigns))
    digest = hashlib.sha256(CAMPAIGN_SET_DOMAIN + struct.pack("<Q", len(ordered)))
    for campaign in ordered:
        digest.update(campaign)
    return digest.hexdigest()


def ordered_chunk_set_sha256(chunks: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256(CHUNK_SET_DOMAIN + struct.pack("<Q", len(chunks)))
    for chunk in chunks:
        digest.update(bytes.fromhex(chunk["bundle"]["sha256"]))
    return digest.hexdigest()


def ordered_record_stream_sha256(records: Sequence[bytes]) -> str:
    digest = hashlib.sha256(RECORD_STREAM_DOMAIN + struct.pack("<Q", len(records)))
    for record in records:
        digest.update(record)
    return digest.hexdigest()


def ordered_trajectory_set_sha256(keys: Sequence[bytes]) -> str:
    ordered = sorted(set(keys))
    digest = hashlib.sha256(TRAJECTORY_SET_DOMAIN + struct.pack("<Q", len(ordered)))
    for key in ordered:
        digest.update(key)
    return digest.hexdigest()


def partition_digest(config: Mapping[str, Any]) -> str:
    body = {key: value for key, value in config.items() if key != "sha256"}
    return sha256_bytes(canonical_json(body))


class IdentityIndex:
    """Disk-backed exact identity sets; memory use does not scale with records."""

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute(
            "CREATE TABLE identities ("
            "kind TEXT NOT NULL, role INTEGER NOT NULL, key BLOB NOT NULL, "
            "PRIMARY KEY (kind, role, key)) WITHOUT ROWID"
        )
        self.connection.execute(
            "CREATE TABLE trajectory_roots ("
            "key BLOB PRIMARY KEY, chunk_id BLOB NOT NULL) WITHOUT ROWID"
        )
        self.observations = {
            role: {kind: 0 for kind in IDENTITY_KINDS} for role in ROLE_IDS
        }
        self.duplicates = {
            role: {kind: 0 for kind in IDENTITY_KINDS} for role in ROLE_IDS
        }

    def add(self, role: str, kind: str, key: bytes) -> bool:
        self.observations[role][kind] += 1
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO identities(kind, role, key) VALUES (?, ?, ?)",
            (kind, ROLE_IDS[role], sqlite3.Binary(key)),
        )
        inserted = cursor.rowcount == 1
        if not inserted:
            self.duplicates[role][kind] += 1
        return inserted

    def add_trajectory_root(self, key: bytes, chunk_id: bytes) -> None:
        try:
            self.connection.execute(
                "INSERT INTO trajectory_roots(key, chunk_id) VALUES (?, ?)",
                (sqlite3.Binary(key), sqlite3.Binary(chunk_id)),
            )
        except sqlite3.IntegrityError:
            reject("TRAJECTORY_SPANS_CHUNKS", key.hex())

    def finish(self) -> None:
        self.connection.commit()

    def intersection_count(self, kind: str) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM identities AS train "
                "JOIN identities AS validation ON train.kind = validation.kind "
                "AND train.key = validation.key "
                "WHERE train.kind = ? AND train.role = 0 AND validation.role = 1",
                (kind,),
            ).fetchone()[0]
        )

    def ordered_summary(self, role: str, kind: str) -> tuple[int, str]:
        role_id = ROLE_IDS[role]
        count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM identities WHERE kind = ? AND role = ?",
                (kind, role_id),
            ).fetchone()[0]
        )
        digest = hashlib.sha256(
            IDENTITY_SET_DOMAIN
            + kind.encode("ascii")
            + b"\0"
            + role.encode("ascii")
            + b"\0"
            + struct.pack("<Q", count)
        )
        cursor = self.connection.execute(
            "SELECT key FROM identities WHERE kind = ? AND role = ? ORDER BY key",
            (kind, role_id),
        )
        for (key,) in cursor:
            digest.update(bytes(key))
        return count, digest.hexdigest()

    def ordered_trajectory_set_digest(self, role: str) -> str:
        role_id = ROLE_IDS[role]
        count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM identities WHERE kind = 'trajectory_id' AND role = ?",
                (role_id,),
            ).fetchone()[0]
        )
        digest = hashlib.sha256(TRAJECTORY_SET_DOMAIN + struct.pack("<Q", count))
        cursor = self.connection.execute(
            "SELECT key FROM identities WHERE kind = 'trajectory_id' AND role = ? ORDER BY key",
            (role_id,),
        )
        for (key,) in cursor:
            digest.update(bytes(key))
        return digest.hexdigest()

    def close(self) -> None:
        self.connection.close()


def validate_partition_config(value: Any, campaigns: Sequence[bytes]) -> Mapping[str, Any]:
    require(isinstance(value, dict) and set(value) == PARTITION_KEYS, "PARTITION_CONFIG", "keys")
    require(value["method"] == "content-hash-complete-trajectory-v1", "PARTITION_METHOD", "")
    require(
        value["domain"] == SPLIT_DOMAIN.decode("ascii"),
        "PARTITION_DOMAIN",
        "",
    )
    validate_u64(value["split_seed_u64"], "PARTITION_SEED", "")
    validate_u64(value["validation_threshold_u64"], "PARTITION_THRESHOLD", "")
    require(
        value["rule_profile_sha256"] == RULE_PROFILE_SHA256,
        "PARTITION_RULE_PROFILE",
        "",
    )
    require(
        value["physical_schema_sha256"] == PHYSICAL_SCHEMA_SHA256,
        "PARTITION_PHYSICAL_SCHEMA",
        "",
    )
    require(
        value["feature_contract_sha256"] == FEATURE_CONTRACT_SHA256,
        "PARTITION_FEATURE_CONTRACT",
        "",
    )
    require(
        value["campaign_set_sha256"] == campaign_set_sha256(campaigns),
        "PARTITION_CAMPAIGN_SET",
        "",
    )
    validate_hex(value["sha256"], 64, "PARTITION_DIGEST", "")
    require(value["sha256"] == partition_digest(value), "PARTITION_DIGEST", "mismatch")
    return value


def validate_manifest_shape(document: Mapping[str, Any], mode: str) -> dict[str, Mapping[str, Any]]:
    require(set(document) == MANIFEST_KEYS, "MANIFEST_KEYS", "")
    require(document["schema"] == MANIFEST_SCHEMA, "MANIFEST_SCHEMA", "")
    require(
        document["project"] == "Crazyhouse-Stockfish" and document["variant"] == "crazyhouse",
        "MANIFEST_PROJECT_VARIANT",
        "",
    )
    fixture = mode == "fixture"
    require(document["fixture_mode"] is fixture, "MANIFEST_MODE", "")
    require(
        document["training_admissible"] is (not fixture),
        "MANIFEST_TRAINING_ADMISSIBLE",
        "",
    )
    require(
        document["status"] == ("FIXTURE_ONLY" if fixture else "READY_FOR_TRAINING"),
        "MANIFEST_STATUS",
        "",
    )
    rule = document["rule_profile"]
    require(
        rule == {"id": RULE_PROFILE_ID, "sha256": RULE_PROFILE_SHA256},
        "MANIFEST_RULE_PROFILE",
        "",
    )
    roles = document["roles"]
    require(isinstance(roles, dict) and set(roles) == set(ROLE_IDS), "MANIFEST_ROLES", "")
    result: dict[str, Mapping[str, Any]] = {}
    global_indices: list[int] = []
    chunk_ids: set[str] = set()
    chunk_paths: set[str] = set()
    campaigns: list[bytes] = []
    for role_name in ("train", "validation"):
        role = roles[role_name]
        require(isinstance(role, dict) and set(role) == ROLE_KEYS, "ROLE_KEYS", role_name)
        require(role["role"] == role_name, "ROLE_NAME", role_name)
        chunks = role["chunks"]
        require(isinstance(chunks, list) and chunks, "ROLE_CHUNKS", role_name)
        require(role["chunk_count"] == len(chunks), "ROLE_CHUNK_COUNT", role_name)
        validate_u64(role["record_count"], "ROLE_RECORD_COUNT", role_name)
        validate_u64(role["trajectory_count"], "ROLE_TRAJECTORY_COUNT", role_name)
        validate_hex(role["ordered_chunk_set_sha256"], 64, "ROLE_DIGEST", role_name)
        validate_hex(role["ordered_record_stream_sha256"], 64, "ROLE_DIGEST", role_name)
        validate_hex(role["ordered_trajectory_set_sha256"], 64, "ROLE_DIGEST", role_name)
        require(
            role["ordered_chunk_set_sha256"] == ordered_chunk_set_sha256(chunks),
            "ROLE_CHUNK_SET_DIGEST",
            role_name,
        )
        for chunk in chunks:
            require(isinstance(chunk, dict) and set(chunk) == CHUNK_KEYS, "CHUNK_KEYS", role_name)
            for artifact_name in ("bundle", "provenance", "capability", "completion_receipt"):
                validate_artifact(chunk[artifact_name], f"{role_name}.{artifact_name}")
            campaign = validate_uuid(chunk["campaign_id"], "CHUNK_CAMPAIGN_ID", role_name)
            validate_uuid(chunk["chunk_id"], "CHUNK_ID", role_name)
            require(chunk["chunk_id"] not in chunk_ids, "CHUNK_ID_DUPLICATE", chunk["chunk_id"])
            chunk_ids.add(chunk["chunk_id"])
            bundle_path = chunk["bundle"]["path"]
            require(bundle_path not in chunk_paths, "CHUNK_PATH_DUPLICATE", bundle_path)
            chunk_paths.add(bundle_path)
            index = validate_u64(chunk["chunk_index"], "CHUNK_INDEX", role_name)
            global_indices.append(index)
            campaigns.append(campaign.bytes)
            validate_u64(chunk["record_count"], "CHUNK_RECORD_COUNT", role_name)
            validate_u64(chunk["trajectory_count"], "CHUNK_TRAJECTORY_COUNT", role_name)
        result[role_name] = role
    require(global_indices == list(range(len(global_indices))), "CHUNK_ORDER", str(global_indices))
    validate_partition_config(document["partition_config"], campaigns)
    return result


def validate_static_artifacts(
    manifest_root: Path,
    document: Mapping[str, Any],
    manifest_sha256: str,
) -> tuple[bytes, bytes]:
    physical_descriptor = validate_artifact(document["physical_schema"], "physical_schema")
    feature_descriptor = validate_artifact(document["feature_contract"], "feature_contract")
    tool_descriptor = validate_artifact(document["admission_tool"], "admission_tool")
    physical = read_artifact(manifest_root, physical_descriptor, "physical_schema")
    feature = read_artifact(manifest_root, feature_descriptor, "feature_contract")
    tool_copy = read_artifact(manifest_root, tool_descriptor, "admission_tool")
    require(
        len(physical) == 18729 and sha256_bytes(physical) == PHYSICAL_SCHEMA_SHA256,
        "PHYSICAL_SCHEMA_IDENTITY",
        "",
    )
    require(
        len(feature) == 3844 and sha256_bytes(feature) == FEATURE_CONTRACT_SHA256,
        "FEATURE_CONTRACT_IDENTITY",
        "",
    )
    own_bytes = read_regular(Path(__file__), "executing admission tool")
    require(tool_copy == own_bytes, "ADMISSION_TOOL_IDENTITY", manifest_sha256)
    admission_contract = read_regular(
        ADMISSION_CONTRACT_PATH,
        "admission contract",
        expected_sha256=ADMISSION_CONTRACT_SHA256,
    )
    try:
        admission_document = json.loads(
            admission_contract.decode("utf-8"), object_pairs_hook=_strict_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        reject("ADMISSION_CONTRACT_PARSE", str(exc))
    require(
        admission_document.get("schema")
        == "crazyhouse-nnue-v2-training-admission-contract/v1",
        "ADMISSION_CONTRACT_SCHEMA",
        "",
    )
    codec.validate_schema_bytes(physical)
    return physical, feature


def validate_aggregate_receipt(
    root: Path,
    descriptor_value: Any,
    document: Mapping[str, Any],
    roles: Mapping[str, Mapping[str, Any]],
    mode: str,
) -> Mapping[str, Any]:
    artifact = validate_artifact(descriptor_value, "aggregate_chunk_set_receipt")
    payload = read_artifact(root, artifact, "aggregate_chunk_set_receipt")
    receipt = parse_strict_json(payload, "aggregate_chunk_set_receipt")
    require(set(receipt) == AGGREGATE_KEYS, "AGGREGATE_KEYS", "")
    fixture = mode == "fixture"
    require(receipt["schema"] == AGGREGATE_RECEIPT_SCHEMA, "AGGREGATE_SCHEMA", "")
    require(
        receipt["project"] == "Crazyhouse-Stockfish" and receipt["variant"] == "crazyhouse",
        "AGGREGATE_PROJECT_VARIANT",
        "",
    )
    require(receipt["fixture_only"] is fixture, "AGGREGATE_MODE", "")
    require(receipt["training_admissible"] is (not fixture), "AGGREGATE_ADMISSION", "")
    require(
        receipt["status"] == ("PASS_FIXTURE_ONLY" if fixture else "PASS_PRODUCTION"),
        "AGGREGATE_STATUS",
        "",
    )
    require(
        receipt["official_openbench_origin"] == (None if fixture else OFFICIAL_OPENBENCH),
        "AGGREGATE_ORIGIN",
        "",
    )
    require(receipt["exact_total"] is True, "AGGREGATE_EXACT_TOTAL", "")
    expected_chunks = sum(role["chunk_count"] for role in roles.values())
    expected_records = sum(role["record_count"] for role in roles.values())
    expected_trajectories = sum(role["trajectory_count"] for role in roles.values())
    require(receipt["chunk_count"] == expected_chunks, "AGGREGATE_CHUNK_COUNT", "")
    require(receipt["record_count"] == expected_records, "AGGREGATE_RECORD_COUNT", "")
    require(receipt["trajectory_count"] == expected_trajectories, "AGGREGATE_TRAJECTORY_COUNT", "")
    campaign_ids = sorted(
        {chunk["campaign_id"] for role in roles.values() for chunk in role["chunks"]}
    )
    require(receipt["campaign_ids"] == campaign_ids, "AGGREGATE_CAMPAIGNS", "")
    require(isinstance(receipt["roles"], dict) and set(receipt["roles"]) == set(ROLE_IDS), "AGGREGATE_ROLES", "")
    for role_name, role in roles.items():
        expected = {
            "chunk_ids": [chunk["chunk_id"] for chunk in role["chunks"]],
            "ordered_chunk_set_sha256": role["ordered_chunk_set_sha256"],
            "record_count": role["record_count"],
            "trajectory_count": role["trajectory_count"],
        }
        require(receipt["roles"][role_name] == expected, "AGGREGATE_ROLE_BINDING", role_name)
    return receipt


def validate_semantic_boundary(document: Mapping[str, Any], mode: str) -> None:
    audit = document["semantic_audit"]
    require(isinstance(audit, dict), "SEMANTIC_AUDIT", "")
    if mode == "fixture":
        expected = {
            "engine_backed": False,
            "every_record_scanned": False,
            "status": "FIXTURE_INDEPENDENT_VERIFIER_REQUIRED",
            "training_admissible": False,
        }
        require(audit == expected, "SEMANTIC_FIXTURE_BOUNDARY", "")
    else:
        required = {
            "status": "PASS",
            "engine_backed": True,
            "every_record_scanned": True,
            "every_trajectory_replayed": True,
            "physical_state_equals_replay": True,
            "stored_move_is_legal": True,
            "make_undo_roundtrip": True,
            "terminal_reason_and_result_reproduced": True,
            "history_prefix_and_repetition_reproduced": True,
            "teacher_bound_and_perspective_reproduced": True,
            "split_decisions_recomputed": True,
            "training_admissible": True,
        }
        require(all(audit.get(key) == value for key, value in required.items()), "SEMANTIC_AUDIT", "production")


def _read_exact(stream: BinaryIO, size: int, label: str, digest: Any) -> bytes:
    payload = stream.read(size)
    digest.update(payload)
    require(len(payload) == size, "CHUNK_TRUNCATED", label)
    return payload


def validate_production_provenance(
    provenance: Mapping[str, Any],
    capability: Mapping[str, Any],
    mode: str,
) -> None:
    settings = provenance["generation_settings"]
    if mode == "fixture":
        require(settings.get("fixture_only") is True, "FIXTURE_PROVENANCE", "fixture_only")
        require(settings.get("training_admissible") is False, "FIXTURE_PROVENANCE", "admission")
        require(capability["production_generation_authorized"] is False, "FIXTURE_CAPABILITY", "")
        return
    require(capability["production_generation_authorized"] is True, "PRODUCTION_CAPABILITY", "")
    require(
        capability["artifact_role"] == "crazyhouse-physical-datagen-production-v1",
        "PRODUCTION_CAPABILITY_ROLE",
        "",
    )
    require(settings.get("fixture_only") is False, "PRODUCTION_FIXTURE_FORBIDDEN", "")
    require(settings.get("training_admissible") is True, "PRODUCTION_ADMISSION_FLAG", "")
    teacher = provenance["teacher"]
    require(teacher["kind"] != "golden-fixture", "PRODUCTION_GOLDEN_FORBIDDEN", "")
    require(teacher["synthetic"] is False, "PRODUCTION_SYNTHETIC_FORBIDDEN", "")
    require(teacher["artifact"] is not None, "PRODUCTION_TEACHER_IDENTITY", "")
    require(provenance["opening_source"]["artifact"] is not None, "PRODUCTION_OPENING_IDENTITY", "")


def row_document(
    role: str,
    campaign_id: bytes,
    chunk_id: bytes,
    chunk_index: int,
    record_raw: bytes,
    record: Any,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    stm_rows = feature_rows(record, record.side_to_move)
    opponent_rows = feature_rows(record, record.side_to_move ^ 1)
    raw_key = hashlib.sha256(RAW_RECORD_DOMAIN + record_raw).digest()
    model_key = model_input_key(stm_rows, opponent_rows)
    identities = {
        "raw_record_key": raw_key,
        "position_identity": record.position_identity_sha256,
        "model_input_key": model_key,
        "game_id": campaign_id + record.game_id,
        "trajectory_id": campaign_id + record.trajectory_id,
    }
    teacher_kinds = ("none", "centipawn", "mate-plies")
    teacher_bounds = ("none", "exact", "lower", "upper")
    terminal_reasons = (
        "ongoing",
        "checkmate",
        "stalemate",
        "fivefold-repetition",
        "threefold-claim-proxy",
        "resignation",
        "draw-adjudication",
    )
    row = {
        "campaign_id": str(uuid.UUID(bytes=campaign_id)),
        "chunk_id": str(uuid.UUID(bytes=chunk_id)),
        "chunk_index": chunk_index,
        "game_id": str(uuid.UUID(bytes=record.game_id)),
        "game_result_white": record.game_result_white,
        "model_input_key": model_key.hex(),
        "move_time_ms": record.move_time_ms,
        "opponent_rows": list(opponent_rows),
        "ply": record.ply,
        "position_identity_sha256": record.position_identity_sha256.hex(),
        "raw_record_key": raw_key.hex(),
        "result_side_to_move": record.result_side_to_move,
        "role": role,
        "schema": ROW_SCHEMA,
        "search_depth": record.search_depth,
        "search_nodes": record.search_nodes,
        "search_seldepth": record.search_seldepth,
        "sequence": record.sequence,
        "side_to_move": "white" if record.side_to_move == 0 else "black",
        "stm_rows": list(stm_rows),
        "teacher_bound": teacher_bounds[record.teacher_bound],
        "teacher_score_kind": teacher_kinds[record.teacher_score_kind],
        "teacher_score_value": record.teacher_score_value,
        "terminal_reason": terminal_reasons[record.terminal_reason],
        "trajectory_id": str(uuid.UUID(bytes=record.trajectory_id)),
    }
    return row, identities


def validate_chunk_receipt(
    root: Path,
    entry: Mapping[str, Any],
    mode: str,
) -> Mapping[str, Any]:
    payload = read_artifact(root, entry["completion_receipt"], "chunk completion receipt")
    receipt = parse_strict_json(payload, "chunk completion receipt")
    require(set(receipt) == CHUNK_RECEIPT_KEYS, "CHUNK_RECEIPT_KEYS", entry["chunk_id"])
    fixture = mode == "fixture"
    require(receipt["schema"] == CHUNK_RECEIPT_SCHEMA, "CHUNK_RECEIPT_SCHEMA", "")
    require(receipt["status"] == ("PASS_FIXTURE_ONLY" if fixture else "PASS_PRODUCTION"), "CHUNK_RECEIPT_STATUS", "")
    require(receipt["project"] == "Crazyhouse-Stockfish" and receipt["variant"] == "crazyhouse", "CHUNK_RECEIPT_PROJECT", "")
    require(receipt["fixture_only"] is fixture, "CHUNK_RECEIPT_MODE", "")
    require(receipt["training_admissible"] is (not fixture), "CHUNK_RECEIPT_ADMISSION", "")
    require(receipt["official_openbench_origin"] == (None if fixture else OFFICIAL_OPENBENCH), "CHUNK_RECEIPT_ORIGIN", "")
    for key in ("campaign_id", "chunk_id", "chunk_index", "record_count", "trajectory_count"):
        require(receipt[key] == entry[key], "CHUNK_RECEIPT_BINDING", key)
    for key in ("bundle", "provenance", "capability"):
        require(receipt[key] == entry[key], "CHUNK_RECEIPT_BINDING", key)
    return receipt


def scan_chunk(
    *,
    root: Path,
    role: str,
    entry: Mapping[str, Any],
    config: Mapping[str, Any],
    physical_schema: bytes,
    capability_contract: bytes,
    index: IdentityIndex,
    row_stream: BinaryIO,
    row_digest: Any,
    record_stream_digest: Any,
    mode: str,
) -> tuple[int, int]:
    provenance_bytes = read_artifact(root, entry["provenance"], "chunk provenance")
    capability_bytes = read_artifact(root, entry["capability"], "chunk capability")
    try:
        provenance_preview = parse_strict_json(provenance_bytes, "chunk provenance")
        if mode == "production":
            capability = production_codec.validate_production_capability_response_bytes(
                capability_bytes,
                contract_bytes=capability_contract,
                expected_challenge=provenance_preview["producer_capability"]["challenge"],
            )
        else:
            capability = codec.validate_capability_response_bytes(
                capability_bytes,
                contract_bytes=capability_contract,
                expected_challenge=provenance_preview["producer_capability"]["challenge"],
            )
    except (KeyError, TypeError, ValueError) as exc:
        reject("CAPABILITY_OR_PROVENANCE", str(exc))
    bundle_path = artifact_path(root, entry["bundle"], "chunk bundle")
    try:
        metadata = bundle_path.lstat()
    except OSError as exc:
        reject("ARTIFACT_MISSING", f"chunk bundle: {exc}")
    require(not stat.S_ISLNK(metadata.st_mode), "ARTIFACT_SYMLINK", "chunk bundle")
    require(stat.S_ISREG(metadata.st_mode), "ARTIFACT_NONREGULAR", "chunk bundle")
    require(metadata.st_size == entry["bundle"]["bytes"], "ARTIFACT_BYTES", "chunk bundle")
    file_digest = hashlib.sha256()
    try:
        with bundle_path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            header = _read_exact(stream, codec.HEADER_SIZE, "header", file_digest)
            require(header[:16] == codec.HEADER_MAGIC, "CHUNK_HEADER_MAGIC", "")
            require(struct.unpack_from("<I", header, 252)[0] == codec.crc32c(header[:252]), "CHUNK_HEADER_CRC32C", "")
            require(
                header[30:32] == bytes(2)
                and header[36:40] == bytes(4)
                and header[240:252] == bytes(12),
                "CHUNK_HEADER_RESERVED",
                "",
            )
            layout = struct.unpack_from("<IHHHHH", header, 16)
            require(
                layout
                == (
                    codec.BYTE_ORDER_MARKER,
                    codec.HEADER_SIZE,
                    codec.RECORD_SIZE,
                    codec.FOOTER_SIZE,
                    codec.SCHEMA_MAJOR,
                    codec.SCHEMA_MINOR,
                ),
                "CHUNK_HEADER_LAYOUT",
                "",
            )
            require(struct.unpack_from("<I", header, 32)[0] == codec.COMMITTED, "CHUNK_HEADER_FLAGS", "")
            record_count = struct.unpack_from("<Q", header, 40)[0]
            require(record_count == entry["record_count"], "CHUNK_RECORD_COUNT", entry["chunk_id"])
            expected_size = codec.HEADER_SIZE + record_count * codec.RECORD_SIZE + codec.FOOTER_SIZE
            require(expected_size == metadata.st_size, "CHUNK_FRAMING", entry["chunk_id"])
            campaign_id = bytes(header[64:80])
            chunk_id = bytes(header[48:64])
            require(str(uuid.UUID(bytes=campaign_id)) == entry["campaign_id"], "CHUNK_CAMPAIGN_ID", "")
            require(str(uuid.UUID(bytes=chunk_id)) == entry["chunk_id"], "CHUNK_ID", "")
            require(header[80:112].hex() == RULE_PROFILE_SHA256, "CHUNK_RULE_PROFILE", "")
            require(header[112:144] == hashlib.sha256(physical_schema).digest(), "CHUNK_SCHEMA_IDENTITY", "")
            require(header[144:176] == hashlib.sha256(provenance_bytes).digest(), "CHUNK_PROVENANCE_IDENTITY", "")
            require(header[208:240] == hashlib.sha256(capability_bytes).digest(), "CHUNK_CAPABILITY_IDENTITY", "")
            try:
                if mode == "production":
                    provenance = production_codec.validate_production_provenance_bytes(
                        provenance_bytes,
                        chunk_id=chunk_id,
                        campaign_id=campaign_id,
                        capability=capability,
                    )
                else:
                    provenance = codec.validate_provenance_bytes(
                        provenance_bytes,
                        chunk_id=chunk_id,
                        campaign_id=campaign_id,
                    )
            except ValueError as exc:
                reject("PROVENANCE", str(exc))
            require(
                provenance["producer_capability"]["bytes"] == len(capability_bytes)
                and provenance["producer_capability"]["sha256"]
                == sha256_bytes(capability_bytes),
                "PROVENANCE_CAPABILITY_BINDING",
                "",
            )
            producer = provenance["producer_artifact"]
            require(
                producer["kind"] == capability["artifact_role"]
                and producer["bytes"] == capability["artifact_bytes"]
                and producer["sha256"] == capability["artifact_sha256"],
                "PROVENANCE_PRODUCER_BINDING",
                "",
            )
            if mode == "production":
                require(
                    provenance["source_commit"] == capability["producer_source_commit"]
                    and provenance["source_tree"] == capability["producer_source_tree"]
                    and provenance["src_tree"] == capability["producer_src_tree"]
                    and provenance["source_dirty"]
                    == capability["producer_source_dirty"],
                    "PROVENANCE_SOURCE_BINDING",
                    "",
                )
                partition = provenance["partition"]
                require(partition["role"] == role, "PROVENANCE_PARTITION_ROLE", role)
                for provenance_key, config_key in (
                    ("campaign_set_sha256", "campaign_set_sha256"),
                    ("domain", "domain"),
                    ("method", "method"),
                    ("partition_sha256", "sha256"),
                    ("split_seed_u64", "split_seed_u64"),
                    ("validation_threshold_u64", "validation_threshold_u64"),
                ):
                    require(
                        partition[provenance_key] == config[config_key],
                        "PROVENANCE_PARTITION_BINDING",
                        provenance_key,
                    )
                require(
                    provenance["generation_settings"]["record_count"]
                    == entry["record_count"]
                    and provenance["generation_settings"]["accepted_trajectories"]
                    == entry["trajectory_count"],
                    "PROVENANCE_GENERATION_COUNTS",
                    role,
                )
            else:
                require(
                    provenance["source_commit"] == capability["source_commit"]
                    and provenance["source_tree"] == capability["source_tree"]
                    and provenance["src_tree"] == capability["src_tree"]
                    and provenance["source_dirty"] == capability["source_dirty"],
                    "PROVENANCE_SOURCE_BINDING",
                    "",
                )
            require(
                provenance["toolchain"]["build_recipe_sha256"]
                == capability["build_recipe_sha256"]
                and provenance["toolchain"]["sha256"] == capability["toolchain_sha256"],
                "PROVENANCE_TOOLCHAIN_BINDING",
                "",
            )
            require(provenance["chunk_index"] == entry["chunk_index"], "PROVENANCE_CHUNK_INDEX", "")
            validate_production_provenance(provenance, capability, mode)
            payload_digest = hashlib.sha256()
            provenance_digest = hashlib.sha256(provenance_bytes).digest()
            current_trajectory: bytes | None = None
            current_game: bytes | None = None
            current_result: int | None = None
            current_claim: int | None = None
            current_augmented: bool | None = None
            current_previous: bytes | None = None
            current_occurrences: dict[bytes, int] = {}
            expected_ply = 0
            prior_terminal = True
            trajectory_count = 0
            for sequence in range(record_count):
                raw = _read_exact(stream, codec.RECORD_SIZE, f"record {sequence}", file_digest)
                payload_digest.update(raw)
                record_stream_digest.update(raw)
                try:
                    record = codec.decode_record(raw)
                except ValueError as exc:
                    reject("PHYSICAL_RECORD", f"{sequence}: {exc}")
                require(record.sequence == sequence, "RECORD_SEQUENCE", str(sequence))
                require(record.provenance_sha256 == provenance_digest, "RECORD_PROVENANCE", str(sequence))
                teacher_network = bool(record.flags & codec.FLAG_TEACHER_NETWORK)
                require(
                    not (record.flags & codec.FLAG_TEACHER_PRESENT)
                    or teacher_network == provenance["teacher"]["network_used"],
                    "RECORD_TEACHER_PROVENANCE",
                    str(sequence),
                )
                if record.trajectory_id != current_trajectory:
                    require(prior_terminal, "TRAJECTORY_INCOMPLETE", str(sequence))
                    require(record.ply == 0, "TRAJECTORY_START", str(sequence))
                    current_trajectory = record.trajectory_id
                    current_game = record.game_id
                    current_result = record.game_result_white
                    current_claim = record.claim_policy
                    current_augmented = bool(record.flags & codec.FLAG_AUGMENTED)
                    current_previous = codec.history_initial(record.trajectory_id, provenance_digest)
                    current_occurrences = {}
                    expected_ply = 0
                    prior_terminal = False
                    trajectory_count += 1
                    trajectory_key = campaign_id + record.trajectory_id
                    index.add_trajectory_root(trajectory_key, chunk_id)
                    expected_role = split_role(config, campaign_id, record.trajectory_id)
                    require(expected_role == role, "SPLIT_ROLE_MISMATCH", record.trajectory_id.hex())
                require(record.ply == expected_ply, "TRAJECTORY_PLY", str(sequence))
                require(
                    (record.game_id, record.game_result_white, record.claim_policy, bool(record.flags & codec.FLAG_AUGMENTED))
                    == (current_game, current_result, current_claim, current_augmented),
                    "TRAJECTORY_CONSTANTS",
                    str(sequence),
                )
                require(current_previous is not None, "TRAJECTORY_HISTORY", str(sequence))
                expected_history = codec.history_step(
                    current_previous,
                    record.ply,
                    record.position_identity_sha256,
                    record.move,
                )
                require(record.history_prefix_sha256 == expected_history, "TRAJECTORY_HISTORY", str(sequence))
                observed = current_occurrences.get(record.position_identity_sha256, 0) + 1
                current_occurrences[record.position_identity_sha256] = observed
                require(record.repetition_occurrences == observed, "TRAJECTORY_REPETITION", str(sequence))
                current_previous = record.history_prefix_sha256
                expected_ply += 1
                prior_terminal = bool(record.flags & codec.FLAG_TERMINAL)
                row, identities = row_document(
                    role,
                    campaign_id,
                    chunk_id,
                    entry["chunk_index"],
                    raw,
                    record,
                )
                for kind, key in identities.items():
                    inserted = index.add(role, kind, key)
                    if kind == "raw_record_key":
                        require(inserted, "WITHIN_ROLE_RAW_DUPLICATE", key.hex())
                row_bytes = canonical_json(row)
                row_stream.write(row_bytes)
                row_digest.update(row_bytes)
            require(prior_terminal, "TRAJECTORY_INCOMPLETE", entry["chunk_id"])
            footer = _read_exact(stream, codec.FOOTER_SIZE, "footer", file_digest)
            require(stream.read(1) == b"", "CHUNK_EXTENSION", entry["chunk_id"])
            after = os.fstat(stream.fileno())
    except AdmissionError:
        raise
    except OSError as exc:
        reject("CHUNK_READ", str(exc))
    require(_same_snapshot(before, after), "ARTIFACT_CHANGED", "chunk bundle")
    require(file_digest.hexdigest() == entry["bundle"]["sha256"], "ARTIFACT_SHA256", "chunk bundle")
    require(footer[:16] == codec.FOOTER_MAGIC, "CHUNK_FOOTER_MAGIC", "")
    require(struct.unpack_from("<I", footer, 124)[0] == codec.crc32c(footer[:124]), "CHUNK_FOOTER_CRC32C", "")
    require(footer[120:124] == bytes(4), "CHUNK_FOOTER_RESERVED", "")
    footer_layout = struct.unpack_from("<HHIQQ", footer, 16)
    require(
        footer_layout
        == (
            codec.FOOTER_SIZE,
            codec.SCHEMA_MAJOR,
            codec.COMMITTED,
            record_count,
            record_count * codec.RECORD_SIZE,
        ),
        "CHUNK_FOOTER_LAYOUT",
        "",
    )
    payload_sha = payload_digest.digest()
    require(header[176:208] == payload_sha and footer[40:72] == payload_sha, "CHUNK_PAYLOAD_SHA256", "")
    require(footer[72:104] == hashlib.sha256(header).digest(), "CHUNK_HEADER_SHA256", "")
    require(footer[104:120] == chunk_id, "CHUNK_FOOTER_ID", "")
    require(trajectory_count == entry["trajectory_count"], "CHUNK_TRAJECTORY_COUNT", entry["chunk_id"])
    validate_chunk_receipt(root, entry, mode)
    return record_count, trajectory_count


def validate_split_audit(
    document: Mapping[str, Any],
    index: IdentityIndex,
) -> tuple[dict[str, int], dict[str, dict[str, int]], dict[str, dict[str, dict[str, Any]]]]:
    audit = document["split_audit"]
    require(isinstance(audit, dict), "SPLIT_AUDIT", "")
    required = {
        "status",
        "intersections",
        "within_role_duplicate_maximum",
        "within_role_duplicate_observations",
    }
    require(set(audit) == required, "SPLIT_AUDIT_KEYS", "")
    require(audit["status"] == "FROZEN_EXPECTATIONS", "SPLIT_AUDIT_STATUS", "")
    require(
        isinstance(audit["intersections"], dict)
        and set(audit["intersections"]) == set(IDENTITY_KINDS),
        "SPLIT_AUDIT_INTERSECTIONS",
        "",
    )
    intersections = {kind: index.intersection_count(kind) for kind in IDENTITY_KINDS}
    require(all(value == 0 for value in audit["intersections"].values()), "SPLIT_AUDIT_DECLARATION", "")
    overlapping = [kind for kind, value in intersections.items() if value]
    require(not overlapping, "CROSS_ROLE_INTERSECTION", ",".join(overlapping))
    maxima = audit["within_role_duplicate_maximum"]
    declared = audit["within_role_duplicate_observations"]
    require(isinstance(maxima, dict) and set(maxima) == set(ROLE_IDS), "SPLIT_DUPLICATE_MAXIMUM", "")
    require(isinstance(declared, dict) and set(declared) == set(ROLE_IDS), "SPLIT_DUPLICATE_DECLARATION", "")
    for role in ROLE_IDS:
        expected_keys = {"position_identity", "model_input_key"}
        require(set(maxima[role]) == expected_keys, "SPLIT_DUPLICATE_MAXIMUM", role)
        require(set(declared[role]) == expected_keys, "SPLIT_DUPLICATE_DECLARATION", role)
        for kind in expected_keys:
            maximum = validate_u64(maxima[role][kind], "SPLIT_DUPLICATE_MAXIMUM", f"{role}.{kind}")
            observed = index.duplicates[role][kind]
            require(declared[role][kind] == observed, "SPLIT_DUPLICATE_DECLARATION", f"{role}.{kind}")
            require(observed <= maximum, "SPLIT_DUPLICATE_BOUND", f"{role}.{kind}")
    sets: dict[str, dict[str, dict[str, Any]]] = {}
    for role in ROLE_IDS:
        sets[role] = {}
        for kind in IDENTITY_KINDS:
            unique, digest = index.ordered_summary(role, kind)
            sets[role][kind] = {
                "duplicate_observations": index.duplicates[role][kind],
                "observations": index.observations[role][kind],
                "ordered_set_sha256": digest,
                "unique_keys": unique,
            }
    return intersections, index.duplicates, sets


def admit(manifest_path: Path, output: Path, mode: str) -> Mapping[str, Any]:
    require(mode in {"fixture", "production"}, "MODE", mode)
    manifest_bytes = read_regular(
        manifest_path,
        "training dataset manifest",
        maximum_bytes=16 * 1024 * 1024,
    )
    manifest = parse_strict_json(manifest_bytes, "training dataset manifest")
    manifest_root = manifest_path.resolve(strict=True).parent
    roles = validate_manifest_shape(manifest, mode)
    physical_schema, _feature_contract = validate_static_artifacts(
        manifest_root,
        manifest,
        sha256_bytes(manifest_bytes),
    )
    validate_aggregate_receipt(
        manifest_root,
        manifest["aggregate_chunk_set_receipt"],
        manifest,
        roles,
        mode,
    )
    validate_semantic_boundary(manifest, mode)
    capability_contract = read_regular(
        PRODUCTION_CAPABILITY_CONTRACT_PATH
        if mode == "production"
        else FIXTURE_CAPABILITY_CONTRACT_PATH,
        "capability contract",
        expected_sha256=(
            PRODUCTION_CAPABILITY_CONTRACT_SHA256 if mode == "production" else None
        ),
    )
    require(
        output.parent.exists() and output.parent.is_dir(),
        "OUTPUT_PARENT",
        str(output.parent),
    )
    require(not output.exists() and not output.is_symlink(), "OUTPUT_EXISTS", str(output))
    partial = output.with_name(output.name + ".partial")
    require(not partial.exists() and not partial.is_symlink(), "OUTPUT_PARTIAL_EXISTS", str(partial))
    partial.mkdir()
    identity_index: IdentityIndex | None = None
    streams: dict[str, BinaryIO] = {}
    try:
        identity_index = IdentityIndex(partial / "identity-index.sqlite3")
        role_summaries: dict[str, Any] = {}
        for role_name in ("train", "validation"):
            role = roles[role_name]
            row_path = partial / f"{role_name}.rows.jsonl"
            row_stream = row_path.open("xb")
            streams[role_name] = row_stream
            row_digest = hashlib.sha256()
            record_digest = hashlib.sha256(
                RECORD_STREAM_DOMAIN + struct.pack("<Q", role["record_count"])
            )
            records_seen = 0
            trajectories_seen = 0
            for entry in role["chunks"]:
                seen_records, seen_trajectories = scan_chunk(
                    root=manifest_root,
                    role=role_name,
                    entry=entry,
                    config=manifest["partition_config"],
                    physical_schema=physical_schema,
                    capability_contract=capability_contract,
                    index=identity_index,
                    row_stream=row_stream,
                    row_digest=row_digest,
                    record_stream_digest=record_digest,
                    mode=mode,
                )
                records_seen += seen_records
                trajectories_seen += seen_trajectories
            require(records_seen == role["record_count"], "ROLE_RECORD_COUNT", role_name)
            require(trajectories_seen == role["trajectory_count"], "ROLE_TRAJECTORY_COUNT", role_name)
            require(
                record_digest.hexdigest() == role["ordered_record_stream_sha256"],
                "ROLE_RECORD_STREAM_DIGEST",
                role_name,
            )
            row_stream.flush()
            os.fsync(row_stream.fileno())
            row_stream.close()
            del streams[role_name]
            row_bytes = row_path.stat().st_size
            role_summaries[role_name] = {
                "chunk_count": role["chunk_count"],
                "record_count": records_seen,
                "rows": {
                    "bytes": row_bytes,
                    "path": row_path.name,
                    "sha256": row_digest.hexdigest(),
                },
                "trajectory_count": trajectories_seen,
            }
        identity_index.finish()
        intersections, duplicates, sets = validate_split_audit(manifest, identity_index)
        for role_name in ROLE_IDS:
            trajectory_unique = sets[role_name]["trajectory_id"]["unique_keys"]
            require(
                trajectory_unique == roles[role_name]["trajectory_count"],
                "ROLE_TRAJECTORY_UNIQUE_COUNT",
                role_name,
            )
            require(
                identity_index.ordered_trajectory_set_digest(role_name)
                == roles[role_name]["ordered_trajectory_set_sha256"],
                "ROLE_TRAJECTORY_SET_DIGEST",
                role_name,
            )
        identity_index.close()
        identity_index = None
        (partial / "identity-index.sqlite3").unlink()
        result = {
            "admission_contract_sha256": ADMISSION_CONTRACT_SHA256,
            "claim_boundary": (
                "Fixture PASS qualifies only structural admission, raw-label transport, "
                "feature projection, partitioning and exact-set mechanics. It is not "
                "production data, training, model selection, timing, Elo, OpenBench, "
                "Fairy-Stockfish, release or monitoring evidence."
                if mode == "fixture"
                else "Production admission does not itself select or train a model and grants no strength or release credit."
            ),
            "feature_contract_sha256": FEATURE_CONTRACT_SHA256,
            "fixture_mode": mode == "fixture",
            "intersections": intersections,
            "legacy_v1_remains_default": True,
            "physical_schema_sha256": PHYSICAL_SCHEMA_SHA256,
            "roles": role_summaries,
            "schema": RESULT_SCHEMA,
            "sets": sets,
            "source_manifest_sha256": sha256_bytes(manifest_bytes),
            "status": "PASS_FIXTURE_NONADMISSIBLE" if mode == "fixture" else "PASS_PRODUCTION_ADMISSION",
            "training_admissible": mode == "production",
            "transactional_output": True,
            "within_role_duplicates": duplicates,
        }
        result_bytes = canonical_json(result)
        _write_new(partial / "admission-result.json", result_bytes)
        os.replace(partial, output)
        return result
    except Exception:
        for stream in streams.values():
            try:
                stream.close()
            except OSError:
                pass
        if identity_index is not None:
            try:
                identity_index.close()
            except sqlite3.Error:
                pass
        if partial.exists():
            shutil.rmtree(partial)
        raise


def _fixture_artifact(path: Path, relative: str, payload: bytes) -> dict[str, Any]:
    _write_new(path / relative, payload)
    return descriptor(relative.replace("\\", "/"), payload)


def _fixture_records() -> tuple[Any, list[Any]]:
    golden = _load_module("crazyhouse_training_admission_goldens", GOLDEN_UNIT_PATH)
    fixture_codec = golden.codec
    records = list(golden.golden_records())
    records[4] = replace(
        records[4],
        teacher_score_kind=fixture_codec.TEACHER_MATE_PLIES,
        teacher_score_value=3,
    )
    records[8] = replace(
        records[8],
        teacher_score_kind=fixture_codec.TEACHER_MATE_PLIES,
        teacher_score_value=-5,
    )
    for changed in (records[4], records[8]):
        fixture_codec.validate_record(changed)
    source = [item for item in records if item.trajectory_id == fixture_codec.uuid_bytes("40000000-0000-4000-8000-000000000001")]
    reflected: list[Any] = []
    previous: bytes | None = None
    new_game = fixture_codec.uuid_bytes("30000000-0000-4000-8000-000000000013")
    new_trajectory = fixture_codec.uuid_bytes("40000000-0000-4000-8000-000000000013")
    for item in source:
        transformed = fixture_codec.reflect_rank_color_swap(
            item,
            previous_history_sha256=previous,
        )
        transformed = replace(
            transformed,
            game_id=new_game,
            trajectory_id=new_trajectory,
            flags=transformed.flags | fixture_codec.FLAG_AUGMENTED,
        )
        reflected.append(transformed)
        previous = transformed.history_prefix_sha256
    records.extend(reflected)
    return fixture_codec, records


def _rebind_records(
    fixture_codec: Any,
    records: Sequence[Any],
    provenance_sha256: bytes,
) -> list[Any]:
    rebound: list[Any] = []
    previous_by_trajectory: dict[bytes, bytes] = {}
    for sequence, record in enumerate(records):
        if record.ply == 0:
            previous = fixture_codec.history_initial(record.trajectory_id, provenance_sha256)
        else:
            previous = previous_by_trajectory.get(record.trajectory_id)
            require(previous is not None, "FIXTURE_HISTORY", record.trajectory_id.hex())
        history = fixture_codec.history_step(
            previous,
            record.ply,
            record.position_identity_sha256,
            record.move,
        )
        changed = replace(
            record,
            sequence=sequence,
            provenance_sha256=provenance_sha256,
            history_prefix_sha256=history,
        )
        fixture_codec.validate_record(changed)
        previous_by_trajectory[record.trajectory_id] = history
        rebound.append(changed)
    return rebound


def _fixture_identity_stats(
    role_records: Mapping[str, Sequence[tuple[bytes, Any, bytes]]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    duplicates: dict[str, dict[str, int]] = {}
    intersections: dict[str, int] = {}
    sets: dict[str, dict[str, set[bytes]]] = {}
    for role, rows in role_records.items():
        sets[role] = {kind: set() for kind in IDENTITY_KINDS}
        duplicates[role] = {kind: 0 for kind in ("position_identity", "model_input_key")}
        for campaign, record, raw in rows:
            _, identities = row_document(role, campaign, bytes(16), 0, raw, record)
            for kind, key in identities.items():
                if key in sets[role][kind] and kind in duplicates[role]:
                    duplicates[role][kind] += 1
                sets[role][kind].add(key)
    for kind in IDENTITY_KINDS:
        intersections[kind] = len(sets["train"][kind] & sets["validation"][kind])
    return duplicates, intersections


def build_fixture(output: Path) -> Mapping[str, Any]:
    require(output.parent.exists() and output.parent.is_dir(), "OUTPUT_PARENT", str(output.parent))
    require(not output.exists() and not output.is_symlink(), "OUTPUT_EXISTS", str(output))
    partial = output.with_name(output.name + ".partial")
    require(not partial.exists() and not partial.is_symlink(), "OUTPUT_PARTIAL_EXISTS", str(partial))
    partial.mkdir()
    try:
        fixture_codec, records = _fixture_records()
        campaign = uuid.UUID("50000000-0000-4000-8000-000000000001")
        config: dict[str, Any] = {
            "campaign_set_sha256": campaign_set_sha256([campaign.bytes]),
            "domain": SPLIT_DOMAIN.decode("ascii"),
            "feature_contract_sha256": FEATURE_CONTRACT_SHA256,
            "method": "content-hash-complete-trajectory-v1",
            "physical_schema_sha256": PHYSICAL_SCHEMA_SHA256,
            "rule_profile_sha256": RULE_PROFILE_SHA256,
            "split_seed_u64": 20260824,
            "validation_threshold_u64": 1 << 63,
        }
        config["sha256"] = partition_digest(config)
        trajectories: dict[bytes, list[Any]] = {}
        order: list[bytes] = []
        for record in records:
            if record.trajectory_id not in trajectories:
                trajectories[record.trajectory_id] = []
                order.append(record.trajectory_id)
            trajectories[record.trajectory_id].append(record)
        by_role: dict[str, list[list[Any]]] = {"train": [], "validation": []}
        for trajectory_id in order:
            role = split_role(config, campaign.bytes, trajectory_id)
            by_role[role].append(trajectories[trajectory_id])
        require(all(len(value) >= 4 for value in by_role.values()), "FIXTURE_SPLIT_BALANCE", "")
        physical_bytes = read_regular(
            PHYSICAL_SCHEMA_PATH,
            "fixture physical schema",
            expected_sha256=PHYSICAL_SCHEMA_SHA256,
        )
        feature_bytes = read_regular(
            FEATURE_CONTRACT_PATH,
            "fixture feature contract",
            expected_sha256=FEATURE_CONTRACT_SHA256,
        )
        tool_bytes = read_regular(Path(__file__), "fixture admission tool")
        capability_bytes = read_regular(GOLDEN_CAPABILITY_PATH, "fixture capability")
        _fixture_artifact(partial, "physical-schema.json", physical_bytes)
        _fixture_artifact(partial, "feature-contract.json", feature_bytes)
        _fixture_artifact(partial, "admission-tool.py", tool_bytes)
        capability_descriptor = _fixture_artifact(partial, "capability.json", capability_bytes)
        base_provenance = json.loads(GOLDEN_PROVENANCE_PATH.read_text(encoding="utf-8"))
        chunks_by_role: dict[str, list[dict[str, Any]]] = {"train": [], "validation": []}
        raw_by_role: dict[str, list[bytes]] = {"train": [], "validation": []}
        trajectory_keys_by_role: dict[str, list[bytes]] = {"train": [], "validation": []}
        identity_rows: dict[str, list[tuple[bytes, Any, bytes]]] = {"train": [], "validation": []}
        chunk_index = 0
        for role in ("train", "validation"):
            groups = by_role[role]
            split_at = len(groups) // 2
            chunk_groups = (groups[:split_at], groups[split_at:])
            for grouped_trajectories in chunk_groups:
                chunk_uuid = uuid.UUID(
                    f"60000000-0000-4000-8000-{chunk_index + 1:012d}"
                )
                provenance = dict(base_provenance)
                provenance["campaign_id"] = str(campaign)
                provenance["chunk_id"] = str(chunk_uuid)
                provenance["chunk_index"] = chunk_index
                provenance["generation_settings"] = dict(provenance["generation_settings"])
                provenance["generation_settings"]["fixture_only"] = True
                provenance["generation_settings"]["training_admissible"] = False
                provenance_payload = canonical_json(provenance)
                provenance_relative = f"chunks/chunk-{chunk_index:02d}.provenance.json"
                provenance_descriptor = _fixture_artifact(
                    partial,
                    provenance_relative,
                    provenance_payload,
                )
                flattened = [record for group in grouped_trajectories for record in group]
                rebound = _rebind_records(
                    fixture_codec,
                    flattened,
                    hashlib.sha256(provenance_payload).digest(),
                )
                chunk_payload = fixture_codec.build_chunk(
                    rebound,
                    schema_bytes=physical_bytes,
                    provenance_bytes=provenance_payload,
                    producer_capability_sha256=hashlib.sha256(capability_bytes).digest(),
                    chunk_id=chunk_uuid.bytes,
                    campaign_id=campaign.bytes,
                )
                bundle_relative = f"chunks/chunk-{chunk_index:02d}.chp"
                bundle_descriptor = _fixture_artifact(partial, bundle_relative, chunk_payload)
                raw_records = [
                    chunk_payload[
                        fixture_codec.HEADER_SIZE + offset * fixture_codec.RECORD_SIZE :
                        fixture_codec.HEADER_SIZE + (offset + 1) * fixture_codec.RECORD_SIZE
                    ]
                    for offset in range(len(rebound))
                ]
                raw_by_role[role].extend(raw_records)
                for record, raw in zip(rebound, raw_records):
                    identity_rows[role].append((campaign.bytes, record, raw))
                for group in grouped_trajectories:
                    trajectory_keys_by_role[role].append(campaign.bytes + group[0].trajectory_id)
                entry_without_receipt = {
                    "bundle": bundle_descriptor,
                    "capability": capability_descriptor,
                    "campaign_id": str(campaign),
                    "chunk_id": str(chunk_uuid),
                    "chunk_index": chunk_index,
                    "provenance": provenance_descriptor,
                    "record_count": len(rebound),
                    "trajectory_count": len(grouped_trajectories),
                }
                receipt = {
                    "bundle": bundle_descriptor,
                    "campaign_id": str(campaign),
                    "capability": capability_descriptor,
                    "chunk_id": str(chunk_uuid),
                    "chunk_index": chunk_index,
                    "fixture_only": True,
                    "official_openbench_origin": None,
                    "project": "Crazyhouse-Stockfish",
                    "provenance": provenance_descriptor,
                    "record_count": len(rebound),
                    "schema": CHUNK_RECEIPT_SCHEMA,
                    "status": "PASS_FIXTURE_ONLY",
                    "training_admissible": False,
                    "trajectory_count": len(grouped_trajectories),
                    "variant": "crazyhouse",
                }
                receipt_relative = f"receipts/chunk-{chunk_index:02d}.json"
                receipt_payload = canonical_json(receipt)
                receipt_descriptor = _fixture_artifact(
                    partial,
                    receipt_relative,
                    receipt_payload,
                )
                entry = dict(entry_without_receipt)
                entry["completion_receipt"] = receipt_descriptor
                chunks_by_role[role].append(entry)
                chunk_index += 1
        role_documents: dict[str, Any] = {}
        for role in ("train", "validation"):
            role_documents[role] = {
                "chunk_count": len(chunks_by_role[role]),
                "chunks": chunks_by_role[role],
                "ordered_chunk_set_sha256": ordered_chunk_set_sha256(chunks_by_role[role]),
                "ordered_record_stream_sha256": ordered_record_stream_sha256(raw_by_role[role]),
                "ordered_trajectory_set_sha256": ordered_trajectory_set_sha256(
                    trajectory_keys_by_role[role]
                ),
                "record_count": len(raw_by_role[role]),
                "role": role,
                "trajectory_count": len(trajectory_keys_by_role[role]),
            }
        duplicate_counts, intersections = _fixture_identity_stats(identity_rows)
        require(all(value == 0 for value in intersections.values()), "FIXTURE_CROSS_ROLE_INTERSECTION", str(intersections))
        split_audit = {
            "intersections": {kind: 0 for kind in IDENTITY_KINDS},
            "status": "FROZEN_EXPECTATIONS",
            "within_role_duplicate_maximum": {
                role: {
                    "model_input_key": duplicate_counts[role]["model_input_key"] + 4,
                    "position_identity": duplicate_counts[role]["position_identity"] + 4,
                }
                for role in ROLE_IDS
            },
            "within_role_duplicate_observations": {
                role: {
                    "model_input_key": duplicate_counts[role]["model_input_key"],
                    "position_identity": duplicate_counts[role]["position_identity"],
                }
                for role in ROLE_IDS
            },
        }
        aggregate = {
            "campaign_ids": [str(campaign)],
            "chunk_count": sum(role["chunk_count"] for role in role_documents.values()),
            "exact_total": True,
            "fixture_only": True,
            "official_openbench_origin": None,
            "project": "Crazyhouse-Stockfish",
            "record_count": sum(role["record_count"] for role in role_documents.values()),
            "roles": {
                role: {
                    "chunk_ids": [chunk["chunk_id"] for chunk in role_documents[role]["chunks"]],
                    "ordered_chunk_set_sha256": role_documents[role]["ordered_chunk_set_sha256"],
                    "record_count": role_documents[role]["record_count"],
                    "trajectory_count": role_documents[role]["trajectory_count"],
                }
                for role in ROLE_IDS
            },
            "schema": AGGREGATE_RECEIPT_SCHEMA,
            "status": "PASS_FIXTURE_ONLY",
            "training_admissible": False,
            "trajectory_count": sum(
                role["trajectory_count"] for role in role_documents.values()
            ),
            "variant": "crazyhouse",
        }
        aggregate_payload = canonical_json(aggregate)
        aggregate_descriptor = _fixture_artifact(
            partial,
            "receipts/aggregate.json",
            aggregate_payload,
        )
        manifest = {
            "admission_tool": descriptor("admission-tool.py", tool_bytes),
            "aggregate_chunk_set_receipt": aggregate_descriptor,
            "created_utc": "2026-08-24T18:00:00Z",
            "feature_contract": descriptor("feature-contract.json", feature_bytes),
            "fixture_mode": True,
            "partition_config": config,
            "physical_schema": descriptor("physical-schema.json", physical_bytes),
            "project": "Crazyhouse-Stockfish",
            "roles": role_documents,
            "rule_profile": {
                "id": RULE_PROFILE_ID,
                "sha256": RULE_PROFILE_SHA256,
            },
            "schema": MANIFEST_SCHEMA,
            "semantic_audit": {
                "engine_backed": False,
                "every_record_scanned": False,
                "status": "FIXTURE_INDEPENDENT_VERIFIER_REQUIRED",
                "training_admissible": False,
            },
            "split_audit": split_audit,
            "status": "FIXTURE_ONLY",
            "training_admissible": False,
            "variant": "crazyhouse",
        }
        manifest_payload = canonical_json(manifest)
        _fixture_artifact(partial, "training-dataset-manifest.json", manifest_payload)
        os.replace(partial, output)
        return {
            "fixture_mode": True,
            "manifest_bytes": len(manifest_payload),
            "manifest_sha256": sha256_bytes(manifest_payload),
            "record_count": aggregate["record_count"],
            "schema": "crazyhouse-training-admission-fixture-build-result/v1",
            "status": "PASS_FIXTURE_BUILT",
            "training_admissible": False,
            "trajectory_count": aggregate["trajectory_count"],
        }
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise


def self_test_identity_intersections() -> Mapping[str, Any]:
    cases: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="crazyhouse-admission-identities-") as temporary:
        for ordinal, kind in enumerate(IDENTITY_KINDS):
            path = Path(temporary) / f"{ordinal}.sqlite3"
            index = IdentityIndex(path)
            try:
                key = hashlib.sha256(b"intersection-self-test\0" + kind.encode("ascii")).digest()
                require(index.add("train", kind, key), "SELF_TEST_INSERT", kind)
                require(index.add("validation", kind, key), "SELF_TEST_INSERT", kind)
                index.finish()
                observed = index.intersection_count(kind)
                require(observed == 1, "SELF_TEST_INTERSECTION", kind)
                cases[kind] = observed
            finally:
                index.close()
    return {
        "cases": cases,
        "schema": "crazyhouse-training-admission-identity-self-test/v1",
        "status": "PASS",
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    builder = subparsers.add_parser("build-fixture")
    builder.add_argument("--output", required=True, type=Path)
    subparsers.add_parser("self-test-identities")
    admission = subparsers.add_parser("admit")
    admission.add_argument("--manifest", required=True, type=Path)
    admission.add_argument("--output", required=True, type=Path)
    admission.add_argument("--mode", choices=("fixture", "production"), required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "build-fixture":
            result = build_fixture(args.output)
        elif args.command == "self-test-identities":
            result = self_test_identity_intersections()
        else:
            result = admit(args.manifest, args.output, args.mode)
    except AdmissionError as exc:
        sys.stderr.buffer.write(
            canonical_json(
                {
                    "code": exc.code,
                    "detail": exc.detail,
                    "status": "REJECTED",
                }
            )
        )
        return 2
    except (OSError, ValueError, KeyError, TypeError, OverflowError, sqlite3.Error) as exc:
        sys.stderr.buffer.write(
            canonical_json(
                {
                    "code": "FAIL_CLOSED",
                    "detail": str(exc),
                    "status": "REJECTED",
                }
            )
        )
        return 2
    sys.stdout.buffer.write(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
