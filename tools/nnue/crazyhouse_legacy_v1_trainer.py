#!/usr/bin/env python3
"""Authenticated diagnostic trainer and exact exporter for Crazyhouse legacy V1.

This is the same-data control arm for the paired A0 architecture experiment.
It accepts the frozen diagnostic admission only, uses the V2 trainer's frozen
configuration, labels, optimizers and sample-order implementation, and emits a
strict legacy ``.nnue`` artifact.  Neither loss nor this diagnostic export can
select a model or grant release credit.
"""

from __future__ import annotations

import argparse
from array import array
from dataclasses import asdict, dataclass
import hashlib
import io
import mmap
import os
from pathlib import Path
import random
import re
import struct
import sys
from typing import Any, Mapping, Sequence, cast, overload

CUBLAS_WORKSPACE_CONFIG = ":4096:8"
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", CUBLAS_WORKSPACE_CONFIG)

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import crazyhouse_v2_large_trainer as shared

if CUBLAS_WORKSPACE_CONFIG != shared.CUBLAS_WORKSPACE_CONFIG:
    raise RuntimeError("CUBLAS_WORKSPACE_CONFIG_DRIFT")


ROOT = Path(__file__).resolve().parents[2]
TRAINING_CONTRACT_PATH = (
    ROOT / "schemas" / "crazyhouse-nnue-legacy-v1-diagnostic-training-v1.json"
)
TRAINING_CONTRACT_SHA256 = (
    "f679b50152aff593b2d7fb1af70de5750309680fefbd0f615a47a63bb696831e"
)
SHARED_TRAINER_PATH = ROOT / "tools" / "nnue" / "crazyhouse_v2_large_trainer.py"
SHARED_TRAINER_SHA256 = (
    "de9fb58bc6aea2214000bd71d2e1e3478a946635a3e33c8da7524b9b60009cc0"
)
PHYSICAL_FEATURE_PATH = ROOT / "schemas" / "crazyhouse-nnue-v2-features-v1.json"
PHYSICAL_FEATURE_SHA256 = (
    "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6"
)
PHYSICAL_SCHEMA_SHA256 = (
    "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
)
ADMISSION_CONTRACT_SHA256 = (
    "070ce5232b790506dcfd65e4ddd76a91e16a2e1bd71a1dee198f0eb3c37517f5"
)
DIAGNOSTIC_ADDENDUM_SHA256 = (
    "8c9dd55c22664481ad18cb4cb8d38443ecfee81d80368ac56cd257e83005372c"
)
DIAGNOSTIC_WAIVER_SHA256 = (
    "a67fe2ec5b2058b665c20da8dc158af8e91560b4de05a64824dbbdbfe72c5e2c"
)
TRAIN_RAW_SET_SHA256 = (
    "fb0a63adfa3edf69e917b9749e8f3679882a95fd7fa36975b5303b0dd56a5569"
)
VALIDATION_RAW_SET_SHA256 = (
    "ef440670aba5e74d1e62b75f6c1349db8bc632c0148889750afe0666356d9ca4"
)

ROW_SCHEMA = "crazyhouse-nnue-v2-physical-row/v1"
ADMISSION_RESULT_SCHEMA = "crazyhouse-nnue-v2-training-admission-result/v1"
RUN_IDENTITY_SCHEMA = "crazyhouse-nnue-legacy-v1-training-run-identity/v1"
CHECKPOINT_SCHEMA = "crazyhouse-nnue-legacy-v1-training-checkpoint/v1"
TRAINING_RESULT_SCHEMA = "crazyhouse-nnue-legacy-v1-training-result/v1"
EXPORT_RESULT_SCHEMA = "crazyhouse-nnue-legacy-v1-export-result/v1"

HEX64 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

PHYSICAL_DIMENSIONS = 902
PHYSICAL_BOARD_ROWS = 768
PHYSICAL_POCKET_OFFSET = 768
PHYSICAL_PROMOTED_OFFSET = 838
PHYSICAL_MAXIMUM_ACTIVE = 138
LEGACY_FEATURE_ROWS = 55_296
LEGACY_KING_STRIDE = 864
LEGACY_BOARD_ROWS = 704
LEGACY_POCKET_SLOTS = 16
LEGACY_BUCKETS = 8
LEGACY_TRANSFORMER_LANES = 512
LEGACY_PSQT_BUCKETS = 8
PRODUCTION_PARAMETER_COUNT = 28_890_248

FT_SCALE = 127
PSQT_SCALE = 9_600
DENSE_WEIGHT_SCALE = 64
DENSE_BIAS_SCALE = 8_128
OUTPUT_WEIGHT_SCALE = 9_600 / 127
OUTPUT_BIAS_SCALE = 9_600

FILE_VERSION = 0x7AF32F20
NETWORK_HASH = 0x3C103E72
TRANSFORMER_HASH = 0x5F2348B8
ARCHITECTURE_HASH = 0x633376CA
DESCRIPTION = (
    "Network trained with the https://github.com/glinscott/nnue-pytorch trainer."
)
FILE_BYTES = 58_534_811

CHECKPOINT_MAGIC = b"CHLEGCKPT1".ljust(16, b"\0")
CHECKPOINT_HEADER_BYTES = 56
INITIAL_METRIC_CHAIN = hashlib.sha256(
    b"Crazyhouse-Stockfish legacy V1 diagnostic metric chain v1\0"
).digest()
INITIAL_RESUME_DOMAIN = (
    b"Crazyhouse-Stockfish legacy V1 diagnostic resume lineage v1\0"
)
MODEL_INPUT_DOMAIN = b"Crazyhouse-Stockfish NNUE V2 model input identity v1\0"

ROW_KEYS = {
    "campaign_id",
    "chunk_id",
    "chunk_index",
    "game_id",
    "game_result_white",
    "model_input_key",
    "move_time_ms",
    "opponent_rows",
    "ply",
    "position_identity_sha256",
    "raw_record_key",
    "result_side_to_move",
    "role",
    "schema",
    "search_depth",
    "search_nodes",
    "search_seldepth",
    "sequence",
    "side_to_move",
    "stm_rows",
    "teacher_bound",
    "teacher_score_kind",
    "teacher_score_value",
    "terminal_reason",
    "trajectory_id",
}

POCKET_BANDS = (
    (0, 17),
    (34, 5),
    (44, 5),
    (54, 5),
    (64, 3),
)
NONPAWN_VALUES = (781, 825, 1276, 2538)

TrainerError = shared.TrainerError
TrainingConfig = shared.TrainingConfig
SourceIdentity = shared.SourceIdentity


@dataclass(frozen=True)
class LegacyShape:
    feature_rows: int
    transformer_lanes: int
    buckets: int
    fc0_outputs: int
    fc1_outputs: int

    @property
    def dense_inputs(self) -> int:
        return 2 * self.transformer_lanes

    @property
    def parameter_count(self) -> int:
        return (
            self.feature_rows * (self.transformer_lanes + LEGACY_PSQT_BUCKETS)
            + self.transformer_lanes
            + self.buckets * self.fc0_outputs * self.dense_inputs
            + self.buckets * self.fc0_outputs
            + self.buckets * self.fc1_outputs * self.fc0_outputs
            + self.buckets * self.fc1_outputs
            + self.buckets * self.fc1_outputs
            + self.buckets
        )


PRODUCTION_SHAPE = LegacyShape(
    LEGACY_FEATURE_ROWS, LEGACY_TRANSFORMER_LANES, 8, 16, 32
)
FIXTURE_SHAPE = LegacyShape(LEGACY_FEATURE_ROWS, 16, 2, 4, 4)


@dataclass(frozen=True)
class LegacySample:
    stm_rows: tuple[int, ...]
    opponent_rows: tuple[int, ...]
    board_piece_count: int
    layer_bucket: int
    board_pawns: int
    own_nonpawns: tuple[int, int, int, int]
    opponent_nonpawns: tuple[int, int, int, int]
    target_probability: float
    raw_record_key: str
    physical_model_input_key: str


@dataclass(frozen=True)
class AdmissionInputs:
    result_sha256: str
    source_manifest_sha256: str
    train_rows_sha256: str
    validation_rows_sha256: str
    train_raw_record_ordered_set_sha256: str
    validation_raw_record_ordered_set_sha256: str
    train_rows_bytes: int
    validation_rows_bytes: int
    train_record_count: int
    validation_record_count: int
    status: str
    training_admissible: bool
    diagnostic_only: bool
    release_admissible: bool


def _require(condition: bool, code: str) -> None:
    shared._require(condition, code)


def _authenticate_static_contracts() -> None:
    for path, expected, code in (
        (TRAINING_CONTRACT_PATH, TRAINING_CONTRACT_SHA256, "TRAINING_CONTRACT"),
        (SHARED_TRAINER_PATH, SHARED_TRAINER_SHA256, "SHARED_TRAINER"),
        (PHYSICAL_FEATURE_PATH, PHYSICAL_FEATURE_SHA256, "PHYSICAL_FEATURE_CONTRACT"),
    ):
        _require(shared._sha256_file(path) == expected, code)


def _physical_model_input_key(
    stm_rows: Sequence[int], opponent_rows: Sequence[int]
) -> str:
    digest = hashlib.sha256()
    digest.update(MODEL_INPUT_DOMAIN)
    digest.update(bytes.fromhex(PHYSICAL_FEATURE_SHA256))
    for rows in (stm_rows, opponent_rows):
        ordered = sorted(rows)
        digest.update(struct.pack("<I", len(ordered)))
        digest.update(b"".join(struct.pack("<I", row) for row in ordered))
    return digest.hexdigest()


def _validated_physical_rows(value: Any, code: str) -> tuple[int, ...]:
    _require(isinstance(value, list), f"{code}_TYPE")
    _require(0 < len(value) <= PHYSICAL_MAXIMUM_ACTIVE, f"{code}_COUNT")
    _require(
        all(shared._is_int(row) and 0 <= row < PHYSICAL_DIMENSIONS for row in value),
        f"{code}_RANGE",
    )
    _require(len(set(value)) == len(value), f"{code}_DUPLICATE")
    return tuple(sorted(value))


def _project_legacy_rows(
    physical_rows: Sequence[int], code: str
) -> tuple[tuple[int, ...], int, int, tuple[int, int, int, int], tuple[int, int, int, int]]:
    board = [row for row in physical_rows if row < PHYSICAL_BOARD_ROWS]
    _require(2 <= len(board) <= 32, f"{code}_BOARD_COUNT")
    own_king = [row % 64 for row in board if row // 64 == 10]
    opponent_king = [row % 64 for row in board if row // 64 == 11]
    _require(len(own_king) == 1 and len(opponent_king) == 1, f"{code}_KINGS")
    king_base = own_king[0] * LEGACY_KING_STRIDE
    legacy = [
        king_base + min(row // 64, 10) * 64 + row % 64
        for row in board
    ]

    pocket_rows = [
        row - PHYSICAL_POCKET_OFFSET
        for row in physical_rows
        if PHYSICAL_POCKET_OFFSET <= row < PHYSICAL_PROMOTED_OFFSET
    ]
    counts: dict[tuple[int, int], int] = {}
    for raw in pocket_rows:
        matched = False
        for piece_type, (base, width) in enumerate(POCKET_BANDS):
            if base <= raw < base + 2 * width:
                owner = (raw - base) // width
                count = (raw - base) % width
                _require((piece_type, owner) not in counts, f"{code}_POCKET_DUPLICATE")
                counts[(piece_type, owner)] = count
                matched = True
                break
        _require(matched, f"{code}_POCKET_ROW")
    _require(len(counts) == 10, f"{code}_POCKET_BANDS")
    for piece_type in range(5):
        for owner in range(2):
            count = counts[(piece_type, owner)]
            band = 2 * piece_type + owner
            legacy.extend(
                king_base + LEGACY_BOARD_ROWS + band * LEGACY_POCKET_SLOTS + slot
                for slot in range(count)
            )

    _require(len(legacy) <= 128, f"{code}_ACTIVE_COUNT")
    _require(len(legacy) == len(set(legacy)), f"{code}_LEGACY_DUPLICATE")
    _require(all(0 <= row < LEGACY_FEATURE_ROWS for row in legacy), f"{code}_LEGACY_RANGE")
    planes = [row // 64 for row in board]
    board_pawns = sum(plane in (0, 1) for plane in planes)
    own_nonpawns = cast(
        tuple[int, int, int, int],
        tuple(planes.count(2 * piece_type) for piece_type in range(1, 5)),
    )
    opponent_nonpawns = cast(
        tuple[int, int, int, int],
        tuple(planes.count(2 * piece_type + 1) for piece_type in range(1, 5)),
    )
    return tuple(sorted(legacy)), len(board), board_pawns, own_nonpawns, opponent_nonpawns


def _validate_row(document: Any, role: str, config: TrainingConfig) -> LegacySample:
    _require(isinstance(document, dict) and set(document) == ROW_KEYS, "ROW_KEYS")
    _require(document.get("schema") == ROW_SCHEMA and document.get("role") == role, "ROW_SCHEMA_ROLE")
    for key in ("campaign_id", "chunk_id", "game_id", "trajectory_id"):
        _require(isinstance(document.get(key), str) and UUID.fullmatch(document[key]), f"ROW_{key.upper()}")
    for key in ("position_identity_sha256", "raw_record_key", "model_input_key"):
        shared._validate_hex(document.get(key), HEX64, f"ROW_{key.upper()}")
    for key in (
        "chunk_index",
        "move_time_ms",
        "ply",
        "search_depth",
        "search_nodes",
        "search_seldepth",
        "sequence",
    ):
        _require(shared._is_int(document.get(key)) and document[key] >= 0, f"ROW_{key.upper()}")
    _require(document.get("side_to_move") in {"white", "black"}, "ROW_SIDE_TO_MOVE")
    _require(document.get("game_result_white") in {-1, 0, 1}, "ROW_GAME_RESULT")
    stm_physical = _validated_physical_rows(document.get("stm_rows"), "ROW_STM")
    opponent_physical = _validated_physical_rows(document.get("opponent_rows"), "ROW_OPPONENT")
    expected_key = _physical_model_input_key(stm_physical, opponent_physical)
    _require(document.get("model_input_key") == expected_key, "ROW_MODEL_INPUT_KEY")
    stm_rows, board_count, board_pawns, own_nonpawns, opponent_nonpawns = (
        _project_legacy_rows(stm_physical, "ROW_STM")
    )
    opponent_rows, opponent_board_count, _, _, _ = _project_legacy_rows(
        opponent_physical, "ROW_OPPONENT"
    )
    _require(board_count == opponent_board_count, "ROW_PERSPECTIVE_BOARD_COUNT")
    layer_bucket = min(7, (board_count - 1) * 8 // 32)
    return LegacySample(
        stm_rows=stm_rows,
        opponent_rows=opponent_rows,
        board_piece_count=board_count,
        layer_bucket=layer_bucket,
        board_pawns=board_pawns,
        own_nonpawns=own_nonpawns,
        opponent_nonpawns=opponent_nonpawns,
        target_probability=shared._target_probability(document, config),
        raw_record_key=document["raw_record_key"],
        physical_model_input_key=expected_key,
    )


class RowDataset(Sequence[LegacySample]):
    """Authenticated random-access JSONL with projection on each access."""

    def __init__(
        self,
        path: Path,
        descriptor: Mapping[str, Any],
        role: str,
        config: TrainingConfig,
    ) -> None:
        _require(set(descriptor) == {"bytes", "path", "sha256"}, f"{role.upper()}_ROWS_DESCRIPTOR")
        _require(descriptor.get("path") == f"{role}.rows.jsonl", f"{role.upper()}_ROWS_PATH")
        _require(path.is_file() and not path.is_symlink(), f"{role.upper()}_ROWS")
        size = path.stat().st_size
        _require(size > 0 and size == descriptor.get("bytes"), f"{role.upper()}_ROWS_BYTES")
        shared._validate_hex(descriptor.get("sha256"), HEX64, f"{role.upper()}_ROWS_SHA256_FORMAT")
        _require(shared._sha256_file(path) == descriptor["sha256"], f"{role.upper()}_ROWS_SHA256")
        self.path = path
        self.role = role
        self.config = config
        self._stream = path.open("rb")
        self._mapping = mmap.mmap(self._stream.fileno(), 0, access=mmap.ACCESS_READ)
        self._offsets = array("Q")
        raw_keys: set[bytes] = set()
        try:
            start = 0
            while start < size:
                end = self._mapping.find(b"\n", start)
                _require(end >= start, f"{role.upper()}_ROW_NEWLINE")
                document = shared._strict_json_bytes(
                    self._mapping[start : end + 1], f"{role.upper()}_ROW_JSON"
                )
                sample = _validate_row(document, role, config)
                raw_key = bytes.fromhex(sample.raw_record_key)
                _require(raw_key not in raw_keys, f"{role.upper()}_RAW_RECORD_DUPLICATE")
                raw_keys.add(raw_key)
                self._offsets.append(start)
                start = end + 1
            _require(start == size and len(self._offsets) > 0, f"{role.upper()}_ROWS_FRAMING")
        except BaseException:
            self.close()
            raise

    @overload
    def __getitem__(self, index: int) -> LegacySample: ...

    @overload
    def __getitem__(self, index: slice) -> list[LegacySample]: ...

    def __getitem__(self, index: int | slice) -> LegacySample | list[LegacySample]:
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        start = self._offsets[index]
        end = self._mapping.find(b"\n", start)
        _require(end >= start, f"{self.role.upper()}_ROW_RUNTIME_NEWLINE")
        document = shared._strict_json_bytes(
            self._mapping[start : end + 1], f"{self.role.upper()}_ROW_RUNTIME_JSON"
        )
        return _validate_row(document, self.role, self.config)

    def __len__(self) -> int:
        return len(self._offsets)

    def close(self) -> None:
        mapping = getattr(self, "_mapping", None)
        if mapping is not None:
            mapping.close()
            self._mapping = None
        stream = getattr(self, "_stream", None)
        if stream is not None:
            stream.close()
            self._stream = None


def _diagnostic_binding(result: Mapping[str, Any]) -> None:
    _require(result.get("diagnostic_only") is True, "ADMISSION_DIAGNOSTIC_ONLY")
    _require(result.get("release_admissible") is False, "ADMISSION_RELEASE_BOUNDARY")
    _require(
        result.get("diagnostic_exception")
        == {
            "campaign_addendum_sha256": DIAGNOSTIC_ADDENDUM_SHA256,
            "frozen_intersections": {
                "game_id": 0,
                "large_model_input_key": 17_262,
                "model_input_key": 17_262,
                "position_identity": 17_127,
                "raw_record_key": 0,
                "trajectory_id": 0,
            },
            "owner_waiver_sha256": DIAGNOSTIC_WAIVER_SHA256,
            "validation_checkpoint_or_seed_selection": False,
            "validation_gradients": False,
            "validation_usage": "forward-only health telemetry",
        },
        "ADMISSION_DIAGNOSTIC_BINDING",
    )
    _require(
        result.get("intersections")
        == {
            "game_id": 0,
            "model_input_key": 17_262,
            "position_identity": 17_127,
            "raw_record_key": 0,
            "trajectory_id": 0,
        },
        "ADMISSION_DIAGNOSTIC_INTERSECTIONS",
    )


def _load_admission(
    result_path: Path,
    expected_sha256: str,
    config: TrainingConfig,
) -> tuple[RowDataset, RowDataset, AdmissionInputs]:
    shared._validate_hex(expected_sha256, HEX64, "ADMISSION_SHA256_ARGUMENT")
    payload = shared._read_regular(result_path, "ADMISSION_RESULT", 16 * 1024 * 1024)
    _require(hashlib.sha256(payload).hexdigest() == expected_sha256, "ADMISSION_RESULT_SHA256")
    result = shared._strict_json_bytes(payload, "ADMISSION_RESULT_JSON")
    _require(isinstance(result, dict), "ADMISSION_RESULT_DOCUMENT")
    for key, expected in {
        "schema": ADMISSION_RESULT_SCHEMA,
        "admission_contract_sha256": ADMISSION_CONTRACT_SHA256,
        "physical_schema_sha256": PHYSICAL_SCHEMA_SHA256,
        "feature_contract_sha256": PHYSICAL_FEATURE_SHA256,
        "legacy_v1_remains_default": True,
        "transactional_output": True,
    }.items():
        _require(result.get(key) == expected, f"ADMISSION_{key.upper()}")
    diagnostic = result.get("status") == "PASS_PRODUCTION_DIAGNOSTIC_ADMISSION"
    if config.mode == "production":
        _require(diagnostic, "ADMISSION_PRODUCTION_DIAGNOSTIC_STATUS")
        _require(result.get("training_admissible") is True, "ADMISSION_PRODUCTION_CREDIT")
        _require(result.get("fixture_mode") is False, "ADMISSION_PRODUCTION_FIXTURE")
        _diagnostic_binding(result)
    else:
        _require(result.get("status") == "PASS_FIXTURE_NONADMISSIBLE", "ADMISSION_FIXTURE_STATUS")
        _require(result.get("training_admissible") is False, "ADMISSION_FIXTURE_CREDIT")
        _require(result.get("fixture_mode") is True, "ADMISSION_FIXTURE_MODE")
        intersections = result.get("intersections")
        _require(isinstance(intersections, dict) and all(value == 0 for value in intersections.values()), "ADMISSION_FIXTURE_INTERSECTIONS")

    roles = result.get("roles")
    sets = result.get("sets")
    _require(isinstance(roles, dict) and set(roles) == {"train", "validation"}, "ADMISSION_ROLES")
    _require(isinstance(sets, dict) and set(sets) == {"train", "validation"}, "ADMISSION_SETS")
    root = result_path.resolve(strict=True).parent
    loaded: dict[str, RowDataset] = {}
    raw_sets: dict[str, str] = {}
    try:
        for role in ("train", "validation"):
            summary = roles.get(role)
            _require(isinstance(summary, dict), f"ADMISSION_{role.upper()}_SUMMARY")
            _require(
                set(summary) == {"chunk_count", "record_count", "rows", "trajectory_count"},
                f"ADMISSION_{role.upper()}_KEYS",
            )
            _require(shared._is_int(summary.get("record_count")) and summary["record_count"] > 0, f"ADMISSION_{role.upper()}_COUNT")
            dataset = RowDataset(root / f"{role}.rows.jsonl", summary["rows"], role, config)
            loaded[role] = dataset
            _require(len(dataset) == summary["record_count"], f"ADMISSION_{role.upper()}_RECORD_COUNT")
            raw = sets.get(role, {}).get("raw_record_key")
            _require(isinstance(raw, dict), f"ADMISSION_{role.upper()}_RAW_SET")
            _require(
                raw.get("observations") == summary["record_count"]
                and raw.get("unique_keys") == summary["record_count"]
                and raw.get("duplicate_observations") == 0,
                f"ADMISSION_{role.upper()}_RAW_SET_COUNTS",
            )
            raw_sets[role] = shared._validate_hex(
                raw.get("ordered_set_sha256"), HEX64, f"ADMISSION_{role.upper()}_RAW_SET_SHA256"
            )
        if diagnostic:
            _require(raw_sets["train"] == TRAIN_RAW_SET_SHA256, "ADMISSION_TRAIN_RAW_SET_PIN")
            _require(raw_sets["validation"] == VALIDATION_RAW_SET_SHA256, "ADMISSION_VALIDATION_RAW_SET_PIN")
    except BaseException:
        for dataset in loaded.values():
            dataset.close()
        raise
    train_summary = roles["train"]
    validation_summary = roles["validation"]
    return (
        loaded["train"],
        loaded["validation"],
        AdmissionInputs(
            result_sha256=expected_sha256,
            source_manifest_sha256=shared._validate_hex(
                result.get("source_manifest_sha256"), HEX64, "ADMISSION_SOURCE_MANIFEST"
            ),
            train_rows_sha256=train_summary["rows"]["sha256"],
            validation_rows_sha256=validation_summary["rows"]["sha256"],
            train_raw_record_ordered_set_sha256=raw_sets["train"],
            validation_raw_record_ordered_set_sha256=raw_sets["validation"],
            train_rows_bytes=train_summary["rows"]["bytes"],
            validation_rows_bytes=validation_summary["rows"]["bytes"],
            train_record_count=train_summary["record_count"],
            validation_record_count=validation_summary["record_count"],
            status=result["status"],
            training_admissible=result["training_admissible"],
            diagnostic_only=diagnostic,
            release_admissible=False,
        ),
    )


def _ste_wrap_signed(value: torch.Tensor, bits: int) -> torch.Tensor:
    minimum = float(-(1 << (bits - 1)))
    modulus = float(1 << bits)
    # A float32 subtraction of INT32_MIN destroys low bits even when ``value``
    # is a small in-range integer.  Compute only the detached forward wrapping
    # value in float64, then keep the straight-through gradient on ``value``.
    widened = value.to(dtype=torch.float64)
    wrapped = (torch.remainder(widened - minimum, modulus) + minimum).to(
        dtype=value.dtype
    )
    return value + (wrapped - value).detach()


class LegacyQatModel(nn.Module):
    """Exact legacy topology with integer-equivalent fake quantization."""

    def __init__(
        self, shape: LegacyShape, device: torch.device, initialize: bool = True
    ) -> None:
        super().__init__()
        self.shape = shape
        self.feature = nn.Embedding(
            shape.feature_rows,
            shape.transformer_lanes + LEGACY_PSQT_BUCKETS,
            sparse=True,
            device=device,
            dtype=torch.float32,
        )
        self.transformer_bias = nn.Parameter(
            torch.empty(shape.transformer_lanes, device=device, dtype=torch.float32)
        )
        self.fc0_weight = nn.Parameter(
            torch.empty(shape.buckets, shape.fc0_outputs, shape.dense_inputs, device=device)
        )
        self.fc0_bias = nn.Parameter(
            torch.empty(shape.buckets, shape.fc0_outputs, device=device)
        )
        self.fc1_weight = nn.Parameter(
            torch.empty(shape.buckets, shape.fc1_outputs, shape.fc0_outputs, device=device)
        )
        self.fc1_bias = nn.Parameter(
            torch.empty(shape.buckets, shape.fc1_outputs, device=device)
        )
        self.fc2_weight = nn.Parameter(
            torch.empty(shape.buckets, shape.fc1_outputs, device=device)
        )
        self.fc2_bias = nn.Parameter(torch.empty(shape.buckets, device=device))
        if initialize:
            self.reset_parameters()

    def reset_parameters(self) -> None:
        with torch.no_grad():
            nn.init.uniform_(
                self.feature.weight[:, : self.shape.transformer_lanes],
                -4.0 / FT_SCALE,
                4.0 / FT_SCALE,
            )
            self.feature.weight[:, self.shape.transformer_lanes :].zero_()
            self.transformer_bias.fill_(64.0 / FT_SCALE)
        nn.init.uniform_(self.fc0_weight, -2.0 / DENSE_WEIGHT_SCALE, 2.0 / DENSE_WEIGHT_SCALE)
        nn.init.zeros_(self.fc0_bias)
        nn.init.uniform_(self.fc1_weight, -2.0 / DENSE_WEIGHT_SCALE, 2.0 / DENSE_WEIGHT_SCALE)
        nn.init.zeros_(self.fc1_bias)
        nn.init.uniform_(self.fc2_weight, -2.0 / OUTPUT_WEIGHT_SCALE, 2.0 / OUTPUT_WEIGHT_SCALE)
        nn.init.zeros_(self.fc2_bias)

    def sparse_parameters(self) -> list[nn.Parameter]:
        return [self.feature.weight]

    def dense_parameters(self) -> list[nn.Parameter]:
        return [parameter for name, parameter in self.named_parameters() if name != "feature.weight"]

    @staticmethod
    def _quantized(parameter: torch.Tensor, scale: float) -> torch.Tensor:
        return shared._ste_round_away(parameter * scale)

    def _transform(
        self, rows: Sequence[int]
    ) -> tuple[torch.Tensor, torch.Tensor]:
        indices = torch.tensor(rows, dtype=torch.int64, device=self.feature.weight.device)
        selected = F.embedding(indices, self.feature.weight, sparse=True)
        transformer = self._quantized(
            selected[:, : self.shape.transformer_lanes], FT_SCALE
        ).sum(dim=0)
        transformer = transformer + self._quantized(self.transformer_bias, FT_SCALE)
        transformer = torch.clamp(_ste_wrap_signed(transformer, 16), 0.0, 127.0)
        psqt = self._quantized(
            selected[:, self.shape.transformer_lanes :], PSQT_SCALE
        ).sum(dim=0)
        return transformer, _ste_wrap_signed(psqt, 32)

    @staticmethod
    def _dense(
        inputs: torch.Tensor,
        weight: torch.Tensor,
        bias: torch.Tensor,
        weight_scale: float,
        bias_scale: float,
    ) -> torch.Tensor:
        output = F.linear(
            inputs,
            shared._ste_round_away(weight * weight_scale),
            shared._ste_round_away(bias * bias_scale),
        )
        return _ste_wrap_signed(output, 32)

    @staticmethod
    def _activation(value: torch.Tensor) -> torch.Tensor:
        return torch.clamp(shared._ste_trunc(value / 64.0), 0.0, 127.0)

    def forward_sample(
        self, sample: LegacySample, trace: bool = False
    ) -> torch.Tensor | tuple[torch.Tensor, dict[str, torch.Tensor | int]]:
        stm, stm_psqt = self._transform(sample.stm_rows)
        opponent, opponent_psqt = self._transform(sample.opponent_rows)
        bucket = min(self.shape.buckets - 1, sample.layer_bucket)
        raw_psqt = shared._ste_trunc(
            _ste_wrap_signed(stm_psqt[bucket] - opponent_psqt[bucket], 32) / 2.0
        )
        fc0 = self._dense(
            torch.cat((stm, opponent)),
            self.fc0_weight[bucket],
            self.fc0_bias[bucket],
            DENSE_WEIGHT_SCALE,
            DENSE_BIAS_SCALE,
        )
        hidden0 = self._activation(fc0)
        fc1 = self._dense(
            hidden0,
            self.fc1_weight[bucket],
            self.fc1_bias[bucket],
            DENSE_WEIGHT_SCALE,
            DENSE_BIAS_SCALE,
        )
        hidden1 = self._activation(fc1)
        positional = torch.dot(
            hidden1, self._quantized(self.fc2_weight[bucket], OUTPUT_WEIGHT_SCALE)
        ) + self._quantized(self.fc2_bias[bucket], OUTPUT_BIAS_SCALE)
        positional = _ste_wrap_signed(positional, 32)

        own_material = sum(
            count * value for count, value in zip(sample.own_nonpawns, NONPAWN_VALUES)
        )
        opponent_material = sum(
            count * value
            for count, value in zip(sample.opponent_nonpawns, NONPAWN_VALUES)
        )
        entertainment = 7 if abs(own_material - opponent_material) <= 44 else 0
        numerator = (128 - entertainment) * raw_psqt + (128 + entertainment) * positional
        adjusted = shared._ste_trunc(shared._ste_trunc(numerator / 128.0) / 16.0)
        scale = 903 + 32 * sample.board_pawns + (32 * (own_material + opponent_material)) // 1024
        outer_pre_clamp = shared._ste_trunc(adjusted * float(scale) / 1024.0)
        outer = torch.clamp(outer_pre_clamp, -31_507.0, 31_507.0)
        if not trace:
            return outer
        return outer, {
            "bucket": bucket,
            "stm": stm,
            "opponent": opponent,
            "stm_psqt": stm_psqt,
            "opponent_psqt": opponent_psqt,
            "raw_psqt": raw_psqt,
            "fc0": fc0,
            "hidden0": hidden0,
            "fc1": fc1,
            "hidden1": hidden1,
            "positional": positional,
            "entertainment": entertainment,
            "adjusted": adjusted,
            "scale": scale,
            "outer_pre_clamp": outer_pre_clamp,
            "outer": outer,
        }

    def probabilities(
        self, samples: Sequence[LegacySample], score_scale_cp: float
    ) -> torch.Tensor:
        values = torch.stack(
            [cast(torch.Tensor, self.forward_sample(sample)) for sample in samples]
        )
        return torch.sigmoid(values / score_scale_cp)


def _shape_for(config: TrainingConfig) -> LegacyShape:
    return PRODUCTION_SHAPE if config.mode == "production" else FIXTURE_SHAPE


def _initialize(
    config: TrainingConfig, device: torch.device
) -> tuple[LegacyQatModel, torch.optim.SparseAdam, torch.optim.AdamW]:
    random.seed(config.seed)
    np.random.seed(config.seed % 2**32)
    torch.manual_seed(config.seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(config.seed)
    model = LegacyQatModel(_shape_for(config), device)
    sparse = torch.optim.SparseAdam(
        model.sparse_parameters(), lr=config.sparse_learning_rate
    )
    dense = torch.optim.AdamW(
        model.dense_parameters(),
        lr=config.dense_learning_rate,
        betas=(0.9, 0.999),
        eps=1e-8,
        weight_decay=0.0,
        amsgrad=False,
        foreach=False,
        maximize=False,
        capturable=False,
        differentiable=False,
        fused=False,
    )
    return model, sparse, dense


def _identity(
    source: SourceIdentity,
    config: TrainingConfig,
    admission: AdmissionInputs,
    runtime: Mapping[str, Any],
    shape: LegacyShape,
) -> dict[str, Any]:
    return {
        "schema": RUN_IDENTITY_SCHEMA,
        "mode": config.mode,
        "training_admissible": admission.training_admissible,
        "diagnostic_only": admission.diagnostic_only,
        "release_admissible": False,
        "validation_policy": {
            "usage": "forward-only health telemetry",
            "gradients": False,
            "early_stopping": False,
            "checkpoint_or_seed_selection": False,
        },
        "source": asdict(source),
        "admission": asdict(admission),
        "configuration_sha256": config.sha256,
        "configuration": dict(config.document),
        "trainer_code_sha256": shared._sha256_file(Path(__file__).resolve()),
        "shared_trainer_sha256": SHARED_TRAINER_SHA256,
        "training_contract_sha256": TRAINING_CONTRACT_SHA256,
        "physical_feature_contract_sha256": PHYSICAL_FEATURE_SHA256,
        "runtime": dict(runtime),
        "shape": asdict(shape),
        "parameter_count": shape.parameter_count,
        "dataloader_workers": 0,
        "sparse_optimizer": "torch.optim.SparseAdam",
        "dense_optimizer": "torch.optim.AdamW-weight_decay-0-foreach-false-fused-false",
        "registered_legacy_weights_imported": False,
    }


def _loss(
    model: LegacyQatModel,
    samples: Sequence[LegacySample],
    config: TrainingConfig,
    device: torch.device,
) -> torch.Tensor:
    probabilities = model.probabilities(samples, config.score_scale_cp)
    targets = torch.tensor(
        [sample.target_probability for sample in samples],
        dtype=torch.float32,
        device=device,
    )
    return torch.mean(torch.abs(probabilities - targets).pow(config.loss_exponent))


def _validation_metric(
    model: LegacyQatModel,
    samples: Sequence[LegacySample],
    config: TrainingConfig,
    device: torch.device,
) -> float:
    model.eval()
    total = 0.0
    with torch.no_grad():
        for offset in range(0, len(samples), config.batch_size):
            batch = samples[offset : offset + config.batch_size]
            total += float(_loss(model, batch, config, device).cpu()) * len(batch)
    model.train()
    return total / len(samples)


def _checkpoint_payload(document: Mapping[str, Any]) -> bytes:
    buffer = io.BytesIO()
    torch.save(dict(document), buffer)
    body = buffer.getvalue()
    header = CHECKPOINT_MAGIC + struct.pack("<Q", len(body)) + hashlib.sha256(body).digest()
    _require(len(header) == CHECKPOINT_HEADER_BYTES, "CHECKPOINT_HEADER_WIDTH")
    return header + body


def _save_checkpoint(path: Path, document: Mapping[str, Any]) -> str:
    payload = _checkpoint_payload(document)
    shared._write_atomic_replace(path, payload)
    return hashlib.sha256(payload).hexdigest()


def _load_checkpoint(path: Path, expected_sha256: str) -> dict[str, Any]:
    shared._validate_hex(expected_sha256, HEX64, "CHECKPOINT_SHA256_ARGUMENT")
    payload = shared._read_regular(path, "CHECKPOINT", 4 * 1024 * 1024 * 1024)
    _require(hashlib.sha256(payload).hexdigest() == expected_sha256, "CHECKPOINT_SHA256")
    _require(len(payload) >= CHECKPOINT_HEADER_BYTES, "CHECKPOINT_TRUNCATED")
    _require(payload[:16] == CHECKPOINT_MAGIC, "CHECKPOINT_MAGIC")
    body_bytes = struct.unpack_from("<Q", payload, 16)[0]
    _require(len(payload) == CHECKPOINT_HEADER_BYTES + body_bytes, "CHECKPOINT_FRAMING")
    body = payload[CHECKPOINT_HEADER_BYTES:]
    _require(hashlib.sha256(body).digest() == payload[24:56], "CHECKPOINT_PAYLOAD_SHA256")
    try:
        document = torch.load(io.BytesIO(body), map_location="cpu", weights_only=True)
    except Exception as error:
        raise TrainerError("CHECKPOINT_LOAD") from error
    _require(isinstance(document, dict), "CHECKPOINT_DOCUMENT")
    return document


def _checkpoint_document(
    identity: Mapping[str, Any],
    model: LegacyQatModel,
    sparse: torch.optim.SparseAdam,
    dense: torch.optim.AdamW,
    device: torch.device,
    epoch: int,
    batch_cursor: int,
    global_step: int,
    current_order: Sequence[int] | None,
    order_chain: bytes,
    metric_chain: bytes,
    metrics: Sequence[Mapping[str, Any]],
    resume_lineage: bytes,
    complete: bool,
) -> dict[str, Any]:
    return {
        "schema": CHECKPOINT_SCHEMA,
        "identity": dict(identity),
        "model_state": model.state_dict(),
        "sparse_optimizer_state": sparse.state_dict(),
        "dense_optimizer_state": dense.state_dict(),
        "rng": shared._rng_document(device),
        "cursor": {
            "epoch": epoch,
            "batch_cursor": batch_cursor,
            "global_step": global_step,
            "current_order": None if current_order is None else list(current_order),
        },
        "order_chain": order_chain,
        "metric_chain": metric_chain,
        "metrics": [dict(metric) for metric in metrics],
        "resume_lineage": resume_lineage,
        "complete": complete,
    }


def _validate_checkpoint_document(
    document: Mapping[str, Any], identity: Mapping[str, Any]
) -> None:
    _require(
        set(document)
        == {
            "schema",
            "identity",
            "model_state",
            "sparse_optimizer_state",
            "dense_optimizer_state",
            "rng",
            "cursor",
            "order_chain",
            "metric_chain",
            "metrics",
            "resume_lineage",
            "complete",
        },
        "CHECKPOINT_KEYS",
    )
    _require(document.get("schema") == CHECKPOINT_SCHEMA, "CHECKPOINT_SCHEMA")
    _require(document.get("identity") == identity, "CHECKPOINT_IDENTITY")
    cursor = document.get("cursor")
    _require(
        isinstance(cursor, dict)
        and set(cursor) == {"epoch", "batch_cursor", "global_step", "current_order"},
        "CHECKPOINT_CURSOR",
    )
    for key in ("epoch", "batch_cursor", "global_step"):
        _require(shared._is_int(cursor[key]) and cursor[key] >= 0, f"CHECKPOINT_CURSOR_{key.upper()}")
    for key in ("order_chain", "metric_chain", "resume_lineage"):
        _require(isinstance(document.get(key), bytes) and len(document[key]) == 32, f"CHECKPOINT_{key.upper()}")
    _require(isinstance(document.get("metrics"), list), "CHECKPOINT_METRICS")
    _require(isinstance(document.get("complete"), bool), "CHECKPOINT_COMPLETE")


def _restore_checkpoint(
    document: Mapping[str, Any],
    model: LegacyQatModel,
    sparse: torch.optim.SparseAdam,
    dense: torch.optim.AdamW,
    device: torch.device,
) -> tuple[int, int, int, list[int] | None, bytes, bytes, list[Mapping[str, Any]], bytes]:
    model.load_state_dict(document["model_state"], strict=True)
    sparse.load_state_dict(document["sparse_optimizer_state"])
    dense.load_state_dict(document["dense_optimizer_state"])
    shared._restore_rng(document["rng"], device)
    cursor = document["cursor"]
    order = cursor["current_order"]
    _require(order is None or (isinstance(order, list) and all(shared._is_int(item) for item in order)), "CHECKPOINT_ORDER")
    return (
        cursor["epoch"],
        cursor["batch_cursor"],
        cursor["global_step"],
        order,
        document["order_chain"],
        document["metric_chain"],
        list(document["metrics"]),
        document["resume_lineage"],
    )


def _emit_event(kind: str, **fields: Any) -> None:
    event = {
        "schema": "crazyhouse-nnue-diagnostic-training-event/v1",
        "architecture": "legacy-v1",
        "kind": kind,
        **fields,
    }
    sys.stderr.buffer.write(shared._canonical_json(event))
    sys.stderr.buffer.flush()


def _result_document(
    status: str,
    identity: Mapping[str, Any],
    checkpoint_path: Path,
    checkpoint_sha256: str,
    model: LegacyQatModel,
    sparse: torch.optim.SparseAdam,
    dense: torch.optim.AdamW,
    epoch: int,
    batch_cursor: int,
    global_step: int,
    order_chain: bytes,
    metric_chain: bytes,
    metrics: Sequence[Mapping[str, Any]],
    resume_lineage: bytes,
) -> dict[str, Any]:
    return {
        "schema": TRAINING_RESULT_SCHEMA,
        "status": status,
        "mode": identity["mode"],
        "training_admissible": identity["training_admissible"],
        "diagnostic_only": identity["diagnostic_only"],
        "release_admissible": False,
        "model_selection_credit": False,
        "strength_credit": False,
        "registered_legacy_remains_default": True,
        "identity_sha256": hashlib.sha256(shared._canonical_json(identity)).hexdigest(),
        "checkpoint": {
            "path": checkpoint_path.name,
            "sha256": checkpoint_sha256,
            "bytes": checkpoint_path.stat().st_size,
        },
        "cursor": {"epoch": epoch, "batch_cursor": batch_cursor, "global_step": global_step},
        "model_state_sha256": shared.canonical_state_sha256(model.state_dict()),
        "sparse_optimizer_state_sha256": shared.canonical_state_sha256(sparse.state_dict()),
        "dense_optimizer_state_sha256": shared.canonical_state_sha256(dense.state_dict()),
        "order_chain_sha256": order_chain.hex(),
        "metric_chain_sha256": metric_chain.hex(),
        "metrics_sha256": hashlib.sha256(shared._canonical_json(list(metrics))).hexdigest(),
        "resume_lineage_sha256": resume_lineage.hex(),
    }


def _run_training(
    train_samples: Sequence[LegacySample],
    validation_samples: Sequence[LegacySample],
    config: TrainingConfig,
    identity: Mapping[str, Any],
    device: torch.device,
    output: Path,
    stop_after_steps: int | None,
    checkpoint: Mapping[str, Any] | None,
    prior_checkpoint_sha256: str | None,
) -> dict[str, Any]:
    model, sparse, dense = _initialize(config, device)
    dataset_identity = cast(
        str, identity["admission"]["train_raw_record_ordered_set_sha256"]
    )
    if checkpoint is None:
        epoch = batch_cursor = global_step = 0
        current_order: list[int] | None = None
        order_chain = shared.INITIAL_ORDER_CHAIN
        metric_chain = INITIAL_METRIC_CHAIN
        metrics: list[Mapping[str, Any]] = []
        resume_lineage = hashlib.sha256(
            INITIAL_RESUME_DOMAIN
            + bytes.fromhex(identity["admission"]["result_sha256"])
            + bytes.fromhex(identity["configuration_sha256"])
            + bytes.fromhex(identity["source"]["commit"])
        ).digest()
    else:
        _validate_checkpoint_document(checkpoint, identity)
        _require(checkpoint.get("complete") is False, "CHECKPOINT_ALREADY_COMPLETE")
        (
            epoch,
            batch_cursor,
            global_step,
            current_order,
            order_chain,
            metric_chain,
            metrics,
            previous_lineage,
        ) = _restore_checkpoint(checkpoint, model, sparse, dense, device)
        _require(prior_checkpoint_sha256 is not None, "RESUME_CHECKPOINT_SHA256")
        resume_lineage = hashlib.sha256(
            previous_lineage + bytes.fromhex(prior_checkpoint_sha256)
        ).digest()

    checkpoint_path = output / "checkpoint.chleg"
    last_validation_step = max(
        (metric["step"] for metric in metrics if metric.get("kind") == "validation"),
        default=-1,
    )
    interrupted = False
    while epoch < config.epochs:
        if current_order is None:
            current_order = shared.sample_order(
                len(train_samples), config.seed, epoch, dataset_identity
            )
            batch_cursor = 0
        batch_count = (len(current_order) + config.batch_size - 1) // config.batch_size
        while batch_cursor < batch_count:
            start = batch_cursor * config.batch_size
            indices = current_order[start : start + config.batch_size]
            batch = [train_samples[index] for index in indices]
            sparse.zero_grad(set_to_none=True)
            dense.zero_grad(set_to_none=True)
            loss = _loss(model, batch, config, device)
            _require(torch.isfinite(loss).item(), "TRAINING_LOSS_NONFINITE")
            loss.backward()
            _require(
                model.feature.weight.grad is not None
                and model.feature.weight.grad.is_sparse,
                "FEATURE_GRADIENT_NOT_SPARSE",
            )
            sparse.step()
            dense.step()
            order_chain = shared._order_chain(order_chain, epoch, batch_cursor, indices)
            global_step += 1
            batch_cursor += 1
            metric: Mapping[str, Any] = {
                "kind": "train",
                "step": global_step,
                "epoch": epoch,
                "batch": batch_cursor - 1,
                "samples": len(batch),
                "loss_hex": float(loss.detach().cpu()).hex(),
            }
            metrics.append(metric)
            metric_chain = shared._metric_chain(metric_chain, metric)
            if global_step % config.validation_interval_steps == 0:
                validation_loss = _validation_metric(
                    model, validation_samples, config, device
                )
                validation_metric: Mapping[str, Any] = {
                    "kind": "validation",
                    "step": global_step,
                    "samples": len(validation_samples),
                    "loss_hex": validation_loss.hex(),
                }
                metrics.append(validation_metric)
                metric_chain = shared._metric_chain(metric_chain, validation_metric)
                last_validation_step = global_step
            if global_step % config.checkpoint_interval_steps == 0:
                document = _checkpoint_document(
                    identity,
                    model,
                    sparse,
                    dense,
                    device,
                    epoch,
                    batch_cursor,
                    global_step,
                    current_order,
                    order_chain,
                    metric_chain,
                    metrics,
                    resume_lineage,
                    False,
                )
                digest = _save_checkpoint(checkpoint_path, document)
                _emit_event(
                    "checkpoint",
                    seed=config.seed,
                    epoch=epoch + 1,
                    step=global_step,
                    sha256=digest,
                )
            if stop_after_steps is not None and global_step >= stop_after_steps:
                interrupted = True
                break
        if interrupted:
            break
        epoch += 1
        batch_cursor = 0
        current_order = None
        _emit_event("epoch_complete", seed=config.seed, epoch=epoch, step=global_step)

    complete = not interrupted and epoch == config.epochs
    if complete and last_validation_step != global_step:
        validation_loss = _validation_metric(model, validation_samples, config, device)
        validation_metric = {
            "kind": "validation",
            "step": global_step,
            "samples": len(validation_samples),
            "loss_hex": validation_loss.hex(),
        }
        metrics.append(validation_metric)
        metric_chain = shared._metric_chain(metric_chain, validation_metric)
    document = _checkpoint_document(
        identity,
        model,
        sparse,
        dense,
        device,
        epoch,
        batch_cursor,
        global_step,
        current_order,
        order_chain,
        metric_chain,
        metrics,
        resume_lineage,
        complete,
    )
    checkpoint_sha256 = _save_checkpoint(checkpoint_path, document)
    status = (
        "PASS_PRODUCTION_DIAGNOSTIC_TRAINING_COMPLETE"
        if complete and config.mode == "production"
        else "PASS_FIXTURE_TRAINING_COMPLETE_NONADMISSIBLE"
        if complete
        else "INTERRUPTED_PRODUCTION_DIAGNOSTIC_CHECKPOINT"
        if config.mode == "production"
        else "INTERRUPTED_FIXTURE_CHECKPOINT_NONADMISSIBLE"
    )
    result = _result_document(
        status,
        identity,
        checkpoint_path,
        checkpoint_sha256,
        model,
        sparse,
        dense,
        epoch,
        batch_cursor,
        global_step,
        order_chain,
        metric_chain,
        metrics,
        resume_lineage,
    )
    shared._write_atomic_replace(
        output / "training-result.json", shared._canonical_json(result)
    )
    _emit_event(
        "terminal" if complete else "interrupted",
        seed=config.seed,
        epoch=epoch,
        step=global_step,
        checkpoint_sha256=checkpoint_sha256,
    )
    return result


def train_or_resume(args: argparse.Namespace, resume: bool) -> Mapping[str, Any]:
    _authenticate_static_contracts()
    config = shared._load_config(args.config, args.config_sha256)
    source = SourceIdentity(args.source_commit, args.source_tree, args.src_tree)
    shared._validate_source(source, config.mode == "production")
    device = shared._configure_runtime(config)
    train_samples, validation_samples, admission = _load_admission(
        args.admission_result, args.admission_result_sha256, config
    )
    try:
        shape = _shape_for(config)
        identity = _identity(
            source, config, admission, shared._runtime_identity(device), shape
        )
        checkpoint: Mapping[str, Any] | None = None
        prior_sha: str | None = None
        if resume:
            prior_sha = args.checkpoint_sha256
            checkpoint = _load_checkpoint(args.checkpoint, prior_sha)
        if args.stop_after_steps is not None:
            _require(args.stop_after_steps > 0, "STOP_AFTER_STEPS")
        partial = shared._prepare_output(args.output_dir)
        _emit_event("start", seed=config.seed, epochs=config.epochs, batch_size=config.batch_size)
        try:
            result = _run_training(
                train_samples,
                validation_samples,
                config,
                identity,
                device,
                partial,
                args.stop_after_steps,
                checkpoint,
                prior_sha,
            )
            shared._commit_output(partial, args.output_dir)
            return result
        except BaseException:
            if partial.exists():
                import shutil

                shutil.rmtree(partial)
            raise
    finally:
        train_samples.close()
        validation_samples.close()


def _quantized_numpy(
    tensor: torch.Tensor,
    scale: float,
    minimum: int,
    maximum: int,
    dtype: str,
) -> np.ndarray:
    return shared._quantized_numpy(tensor, scale, minimum, maximum, dtype)


def _write_array(stream: Any, array_value: np.ndarray) -> None:
    stream.write(array_value.tobytes(order="C"))


def _reauth_export(path: Path) -> None:
    _require(path.is_file() and not path.is_symlink(), "EXPORT_FILE")
    _require(path.stat().st_size == FILE_BYTES, "EXPORT_FILE_BYTES")
    with path.open("rb") as stream:
        def read_exact(size: int, code: str) -> bytes:
            payload = stream.read(size)
            _require(len(payload) == size, code)
            return payload

        version, network_hash, description_bytes = struct.unpack(
            "<III", read_exact(12, "EXPORT_HEADER")
        )
        _require(version == FILE_VERSION, "EXPORT_VERSION")
        _require(network_hash == NETWORK_HASH, "EXPORT_NETWORK_HASH")
        _require(description_bytes == len(DESCRIPTION), "EXPORT_DESCRIPTION_BYTES")
        _require(
            read_exact(description_bytes, "EXPORT_DESCRIPTION").decode("ascii")
            == DESCRIPTION,
            "EXPORT_DESCRIPTION",
        )
        _require(
            struct.unpack("<I", read_exact(4, "EXPORT_TRANSFORMER_HASH"))[0]
            == TRANSFORMER_HASH,
            "EXPORT_TRANSFORMER_HASH",
        )
        read_exact(LEGACY_TRANSFORMER_LANES * 2, "EXPORT_TRANSFORMER_BIAS")
        read_exact(LEGACY_FEATURE_ROWS * LEGACY_TRANSFORMER_LANES * 2, "EXPORT_TRANSFORMER_WEIGHT")
        read_exact(LEGACY_FEATURE_ROWS * LEGACY_PSQT_BUCKETS * 4, "EXPORT_PSQT_WEIGHT")
        for _ in range(LEGACY_BUCKETS):
            _require(
                struct.unpack("<I", read_exact(4, "EXPORT_ARCHITECTURE_HASH"))[0]
                == ARCHITECTURE_HASH,
                "EXPORT_ARCHITECTURE_HASH",
            )
            read_exact(16 * 4 + 16 * 1024, "EXPORT_FC0")
            read_exact(32 * 4 + 32 * 32, "EXPORT_FC1")
            read_exact(4 + 32, "EXPORT_FC2")
        _require(stream.read(1) == b"", "EXPORT_TRAILING_BYTES")


def _temporary_config(identity: Mapping[str, Any]) -> TrainingConfig:
    document = identity.get("configuration")
    _require(isinstance(document, dict), "EXPORT_CONFIGURATION")
    return TrainingConfig(
        mode="production",
        device=document["device"],
        cpu_threads=document["cpu_threads"],
        score_scale_cp=float(document["score_scale_cp"]),
        lambda_=float(document["lambda"]),
        loss_exponent=float(document["loss_exponent"]),
        batch_size=document["batch_size"],
        epochs=document["epochs"],
        seed=document["seed"],
        sparse_learning_rate=float(document["sparse_learning_rate"]),
        dense_learning_rate=float(document["dense_learning_rate"]),
        validation_interval_steps=document["validation_interval_steps"],
        checkpoint_interval_steps=document["checkpoint_interval_steps"],
        sha256=identity["configuration_sha256"],
        document=document,
    )


def export_checkpoint(args: argparse.Namespace) -> Mapping[str, Any]:
    _authenticate_static_contracts()
    document = _load_checkpoint(args.checkpoint, args.checkpoint_sha256)
    identity = document.get("identity")
    _require(isinstance(identity, dict), "EXPORT_IDENTITY")
    _require(identity.get("mode") == "production", "EXPORT_FIXTURE_FORBIDDEN")
    _require(
        identity.get("training_admissible") is True
        and identity.get("diagnostic_only") is True
        and identity.get("release_admissible") is False,
        "EXPORT_DIAGNOSTIC_BOUNDARY",
    )
    _require(document.get("complete") is True, "EXPORT_INCOMPLETE")
    config = _temporary_config(identity)
    device = shared._configure_runtime(config)
    _require(shared._runtime_identity(device) == identity.get("runtime"), "EXPORT_RUNTIME_IDENTITY")
    random.seed(config.seed)
    np.random.seed(config.seed % 2**32)
    torch.manual_seed(config.seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(config.seed)
    model = LegacyQatModel(PRODUCTION_SHAPE, device)
    model.load_state_dict(document["model_state"], strict=True)

    _require(not args.output.exists(), "EXPORT_OUTPUT_EXISTS")
    _require(args.output.parent.exists() and args.output.parent.is_dir(), "EXPORT_OUTPUT_PARENT")
    partial = args.output.with_name(args.output.name + ".partial")
    _require(not partial.exists(), "EXPORT_PARTIAL_EXISTS")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(struct.pack("<III", FILE_VERSION, NETWORK_HASH, len(DESCRIPTION)))
            stream.write(DESCRIPTION.encode("ascii"))
            stream.write(struct.pack("<I", TRANSFORMER_HASH))
            _write_array(
                stream,
                _quantized_numpy(
                    model.transformer_bias, FT_SCALE, -32768, 32767, "<i2"
                ),
            )
            _write_array(
                stream,
                _quantized_numpy(
                    model.feature.weight[:, :LEGACY_TRANSFORMER_LANES],
                    FT_SCALE,
                    -32768,
                    32767,
                    "<i2",
                ),
            )
            _write_array(
                stream,
                _quantized_numpy(
                    model.feature.weight[:, LEGACY_TRANSFORMER_LANES:],
                    PSQT_SCALE,
                    -(2**31),
                    2**31 - 1,
                    "<i4",
                ),
            )
            fc0_bias = _quantized_numpy(
                model.fc0_bias, DENSE_BIAS_SCALE, -(2**31), 2**31 - 1, "<i4"
            )
            fc0_weight = _quantized_numpy(
                model.fc0_weight, DENSE_WEIGHT_SCALE, -128, 127, "i1"
            )
            fc1_bias = _quantized_numpy(
                model.fc1_bias, DENSE_BIAS_SCALE, -(2**31), 2**31 - 1, "<i4"
            )
            fc1_active = _quantized_numpy(
                model.fc1_weight, DENSE_WEIGHT_SCALE, -128, 127, "i1"
            )
            fc1_weight = np.zeros((8, 32, 32), dtype="i1")
            fc1_weight[:, :, :16] = fc1_active
            fc2_bias = _quantized_numpy(
                model.fc2_bias, OUTPUT_BIAS_SCALE, -(2**31), 2**31 - 1, "<i4"
            )
            fc2_weight = _quantized_numpy(
                model.fc2_weight, OUTPUT_WEIGHT_SCALE, -128, 127, "i1"
            )
            for bucket in range(8):
                stream.write(struct.pack("<I", ARCHITECTURE_HASH))
                for values in (
                    fc0_bias[bucket],
                    fc0_weight[bucket],
                    fc1_bias[bucket],
                    fc1_weight[bucket],
                    fc2_bias[bucket : bucket + 1],
                    fc2_weight[bucket],
                ):
                    _write_array(stream, values)
            _require(stream.tell() == FILE_BYTES, "EXPORT_FINAL_OFFSET")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, args.output)
    except BaseException:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
        raise
    _reauth_export(args.output)
    result = {
        "schema": EXPORT_RESULT_SCHEMA,
        "status": "PASS_PRODUCTION_DIAGNOSTIC_EXPORT_REAUTHENTICATED",
        "path": args.output.name,
        "bytes": args.output.stat().st_size,
        "sha256": shared._sha256_file(args.output),
        "checkpoint_sha256": args.checkpoint_sha256,
        "identity_sha256": hashlib.sha256(shared._canonical_json(identity)).hexdigest(),
        "training_admissible": True,
        "diagnostic_only": True,
        "release_admissible": False,
        "model_selection_credit": False,
        "strength_credit": False,
        "registered_legacy_remains_default": True,
    }
    if args.receipt is not None:
        _require(not args.receipt.exists(), "EXPORT_RECEIPT_EXISTS")
        shared._write_atomic_replace(args.receipt, shared._canonical_json(result))
    return result


def meta_check() -> Mapping[str, Any]:
    _authenticate_static_contracts()
    model = LegacyQatModel(PRODUCTION_SHAPE, torch.device("meta"), initialize=False)
    observed = sum(parameter.numel() for parameter in model.parameters())
    _require(observed == PRODUCTION_PARAMETER_COUNT, "META_PARAMETER_COUNT")
    _require(PRODUCTION_SHAPE.parameter_count == observed, "META_SHAPE_PARAMETER_COUNT")
    expected = {
        "feature.weight": (LEGACY_FEATURE_ROWS, LEGACY_TRANSFORMER_LANES + 8),
        "transformer_bias": (LEGACY_TRANSFORMER_LANES,),
        "fc0_weight": (8, 16, 1024),
        "fc0_bias": (8, 16),
        "fc1_weight": (8, 32, 16),
        "fc1_bias": (8, 32),
        "fc2_weight": (8, 32),
        "fc2_bias": (8,),
    }
    observed_shapes = {
        name: tuple(parameter.shape) for name, parameter in model.named_parameters()
    }
    _require(observed_shapes == expected, "META_PARAMETER_SHAPES")
    return {
        "schema": "crazyhouse-nnue-legacy-v1-meta-check/v1",
        "status": "PASS",
        "parameter_count": observed,
        "file_bytes": FILE_BYTES,
        "fresh_initialization": True,
        "registered_legacy_weights_imported": False,
        "training_admissible": False,
        "diagnostic_only": True,
        "release_admissible": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--admission-result", type=Path, required=True)
    common.add_argument("--admission-result-sha256", required=True)
    common.add_argument("--config", type=Path, required=True)
    common.add_argument("--config-sha256", required=True)
    common.add_argument("--source-commit", required=True)
    common.add_argument("--source-tree", required=True)
    common.add_argument("--src-tree", required=True)
    common.add_argument("--output-dir", type=Path, required=True)
    common.add_argument("--stop-after-steps", type=int)
    subparsers.add_parser("train", parents=[common])
    resume = subparsers.add_parser("resume", parents=[common])
    resume.add_argument("--checkpoint", type=Path, required=True)
    resume.add_argument("--checkpoint-sha256", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--checkpoint", type=Path, required=True)
    export.add_argument("--checkpoint-sha256", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--receipt", type=Path)
    subparsers.add_parser("meta-check")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "meta-check":
            result = meta_check()
        elif args.command == "export":
            result = export_checkpoint(args)
        else:
            result = train_or_resume(args, args.command == "resume")
        sys.stdout.buffer.write(shared._canonical_json(result))
        return 0
    except (
        TrainerError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        RuntimeError,
    ) as error:
        code = str(error) if isinstance(error, TrainerError) else "FAIL_CLOSED"
        sys.stderr.buffer.write(
            shared._canonical_json(
                {
                    "schema": "crazyhouse-nnue-legacy-v1-trainer-rejection/v1",
                    "status": "REJECTED",
                    "code": code,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
