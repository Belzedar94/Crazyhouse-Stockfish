#!/usr/bin/env python3
"""Authenticated QAT trainer and exporter for Crazyhouse NNUE V2 large A0.

The production path accepts only rows emitted by the frozen large-K64/G1
admission projection.  A reduced-lane CPU fixture exercises the same sparse
optimizers, quantized arithmetic, checkpoint framing, and resume state, but is
explicitly non-admissible and cannot be exported.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
import mmap
import os
from pathlib import Path
import platform
import random
import re
import struct
import subprocess
import sys
from typing import Any, Mapping, Sequence, cast, overload

CUBLAS_WORKSPACE_CONFIG = ":4096:8"
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", CUBLAS_WORKSPACE_CONFIG)

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


ROOT = Path(__file__).resolve().parents[2]
TRAINING_CONTRACT_PATH = ROOT / "schemas" / "crazyhouse-nnue-v2-large-training-v1.json"
TRAINING_CONTRACT_SHA256 = "cae6e1d1f51f2e33e113c5e9c1007131e1b40de1cf0800543aa4829657353d68"
LARGE_REFERENCE_PATH = ROOT / "tools" / "nnue" / "crazyhouse_v2_large_reference.py"
LARGE_REFERENCE_SHA256 = "3f61002dd262e24327c8b5fb31a53b773468a3dc336d87ac7572c1418d1a2975"
LARGE_FEATURE_SCHEMA_PATH = ROOT / "schemas" / "crazyhouse-nnue-v2-large-k64g1-features-v1.json"
LARGE_FEATURE_SCHEMA_SHA256 = "837b82eb9af44829bca913a22c3702270b58cc6970e2b36c3d1fe3419945c397"

RULE_PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PHYSICAL_SCHEMA_SHA256 = "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
FEATURE_CONTRACT_SHA256 = "6e616c2e090b43daa7710ca39aaedc76b43a90db46e8f093466f45b821f44a79"
ARCHITECTURE_SHA256 = "2f5efc7cf05f3365bf5e524e636d47a6abdbadcdf5673cc0d260f1e61638341e"
QUANTIZATION_SHA256 = "262399c3d1e8f96681f485d8b2d9d6d1c8e783cd1685250317a9c7e244c9386c"
ADMISSION_CONTRACT_SHA256 = "070ce5232b790506dcfd65e4ddd76a91e16a2e1bd71a1dee198f0eb3c37517f5"

ROW_SCHEMA = "crazyhouse-nnue-v2-large-physical-row/v1"
RESULT_SCHEMA = "crazyhouse-nnue-v2-large-training-admission-result/v1"
CONFIG_SCHEMA = "crazyhouse-nnue-v2-large-training-config/v1"
CHECKPOINT_SCHEMA = "crazyhouse-nnue-v2-large-training-checkpoint/v1"
RUN_IDENTITY_SCHEMA = "crazyhouse-nnue-v2-large-training-run-identity/v1"
TRAINING_RESULT_SCHEMA = "crazyhouse-nnue-v2-large-training-result/v1"
EXPORT_RESULT_SCHEMA = "crazyhouse-nnue-v2-large-export-result/v1"

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

K_INPUTS = 81_664
G_INPUTS = 1_340
MAXIMUM_ACTIVE = 48
PRODUCTION_PARAMETER_COUNT = 63_342_088
FAST_CACHE_SCHEMA = "crazyhouse-nnue-fast-array-cache/v1"
FAST_CACHE_ENV = "CRAZYHOUSE_TRAINING_CACHE"

FEATURE_WEIGHT_SCALE = 256
FEATURE_BIAS_SCALE = 256
TRANSFORMER_MAXIMUM = 255
PAIR_DIVISOR = 512
HIDDEN_ONE = 128
FC0_WEIGHT_SCALE = 128
FC0_BIAS_SCALE = 16_384
FC1_WEIGHT_SCALE = 64
FC1_BIAS_SCALE = 8_192
FC2_WEIGHT_SCALE = 128
FC2_BIAS_SCALE = 16_384
OUTPUT_NUMERATOR = 9_600
OUTPUT_DENOMINATOR = 16_384
ENGINE_UNITS_PER_CP = 16

CHECKPOINT_MAGIC = b"CHV2LCKPT1".ljust(16, b"\0")
CHECKPOINT_HEADER_BYTES = 56
INITIAL_ORDER_CHAIN = hashlib.sha256(
    b"Crazyhouse-Stockfish NNUE V2 large sample order v1\0"
).digest()
INITIAL_METRIC_CHAIN = hashlib.sha256(
    b"Crazyhouse-Stockfish NNUE V2 large metric chain v1\0"
).digest()
INITIAL_RESUME_DOMAIN = b"Crazyhouse-Stockfish NNUE V2 large resume lineage v1\0"
MODEL_INPUT_DOMAIN = b"Crazyhouse-Stockfish NNUE V2 large K64G1 model input identity v1\0"

CONTAINER_MAGIC = b"CHNNUEV2LARGEA0\0"
HEADER_BYTES = 1_024
PAYLOAD_BYTES = 126_405_664
FILE_BYTES = 126_406_688
TENSOR_DIRECTORY = (
    (1, 1, 2, 0, 1_024, 125_435_904, (81_664, 768, 0, 0)),
    (2, 1, 1, 0, 125_436_928, 1_536, (768, 0, 0, 0)),
    (3, 1, 2, 0, 125_438_464, 686_080, (1_340, 256, 0, 0)),
    (4, 1, 1, 0, 126_124_544, 512, (256, 0, 0, 0)),
    (5, 2, 2, 0, 126_125_056, 1_024, (8, 32, 0, 0)),
    (6, 3, 3, 0, 126_126_080, 262_144, (8, 32, 1_024, 0)),
    (7, 2, 2, 0, 126_388_224, 1_024, (8, 32, 0, 0)),
    (8, 3, 3, 0, 126_389_248, 16_384, (8, 32, 64, 0)),
    (9, 2, 1, 0, 126_405_632, 32, (8, 0, 0, 0)),
    (10, 3, 2, 0, 126_405_664, 1_024, (8, 128, 0, 0)),
)

ROW_KEYS = {
    "campaign_id",
    "chunk_id",
    "chunk_index",
    "game_id",
    "game_result_white",
    "large_model_input_key",
    "move_time_ms",
    "opponent_g1_rows",
    "opponent_k64_rows",
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
    "stm_g1_rows",
    "stm_k64_rows",
    "teacher_bound",
    "teacher_score_kind",
    "teacher_score_value",
    "terminal_reason",
    "total_pocket_units",
    "trajectory_id",
}

CONFIG_KEYS = {
    "schema",
    "mode",
    "device",
    "cpu_threads",
    "score_scale_cp",
    "lambda",
    "loss_exponent",
    "batch_size",
    "epochs",
    "seed",
    "sparse_learning_rate",
    "dense_learning_rate",
    "validation_interval_steps",
    "checkpoint_interval_steps",
}


class TrainerError(RuntimeError):
    """Stable fail-closed trainer error."""


@dataclass(frozen=True)
class ModelShape:
    k_inputs: int
    k_lanes: int
    g_inputs: int
    g_lanes: int
    buckets: int
    fc0_outputs: int
    fc1_outputs: int

    @property
    def perspective_lanes(self) -> int:
        return self.k_lanes // 2 + self.g_lanes // 2

    @property
    def dense_inputs(self) -> int:
        return 2 * self.perspective_lanes

    @property
    def fc1_inputs(self) -> int:
        return 2 * self.fc0_outputs

    @property
    def fc2_inputs(self) -> int:
        return 2 * self.fc0_outputs + 2 * self.fc1_outputs

    @property
    def parameter_count(self) -> int:
        return (
            self.k_inputs * self.k_lanes
            + self.k_lanes
            + self.g_inputs * self.g_lanes
            + self.g_lanes
            + self.buckets * self.fc0_outputs * self.dense_inputs
            + self.buckets * self.fc0_outputs
            + self.buckets * self.fc1_outputs * self.fc1_inputs
            + self.buckets * self.fc1_outputs
            + self.buckets * self.fc2_inputs
            + self.buckets
        )


PRODUCTION_SHAPE = ModelShape(K_INPUTS, 768, G_INPUTS, 256, 8, 32, 32)
FIXTURE_SHAPE = ModelShape(K_INPUTS, 16, G_INPUTS, 8, 2, 32, 16)


@dataclass(frozen=True)
class SourceIdentity:
    commit: str
    tree: str
    src_tree: str


@dataclass(frozen=True)
class TrainingConfig:
    mode: str
    device: str
    cpu_threads: int
    score_scale_cp: float
    lambda_: float
    loss_exponent: float
    batch_size: int
    epochs: int
    seed: int
    sparse_learning_rate: float
    dense_learning_rate: float
    validation_interval_steps: int
    checkpoint_interval_steps: int
    sha256: str
    document: Mapping[str, Any]


@dataclass(frozen=True)
class Sample:
    stm_k_rows: tuple[int, ...]
    stm_g_rows: tuple[int, ...]
    opponent_k_rows: tuple[int, ...]
    opponent_g_rows: tuple[int, ...]
    total_pocket_units: int
    target_probability: float
    raw_record_key: str
    model_input_key: str


@dataclass(frozen=True)
class Batch:
    stm_k_rows: torch.Tensor
    stm_g_rows: torch.Tensor
    opponent_k_rows: torch.Tensor
    opponent_g_rows: torch.Tensor
    total_pocket_units: torch.Tensor
    targets: torch.Tensor

    def __len__(self) -> int:
        return int(self.targets.shape[0])


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


class RowDataset(Sequence[Sample]):
    """Authenticated rows parsed once into a reusable vectorized array cache."""

    def __init__(
        self,
        path: Path,
        descriptor: Mapping[str, Any],
        role: str,
        config: TrainingConfig,
        expected_count: int,
    ) -> None:
        _require(set(descriptor) == {"bytes", "path", "sha256"}, f"{role.upper()}_ROWS_DESCRIPTOR")
        _require(descriptor.get("path") == f"{role}.rows.jsonl", f"{role.upper()}_ROWS_PATH")
        _require(path.is_file() and not path.is_symlink(), f"{role.upper()}_ROWS")
        size = path.stat().st_size
        _require(size > 0 and size == descriptor.get("bytes"), f"{role.upper()}_ROWS_BYTES")
        _validate_hex(descriptor.get("sha256"), HEX64, f"{role.upper()}_ROWS_SHA256_FORMAT")
        _require(_is_int(expected_count) and expected_count > 0, f"{role.upper()}_ROWS_COUNT")
        self.path = path
        self.role = role
        self.config = config
        self._stream = path.open("rb")
        self._mapping = mmap.mmap(self._stream.fileno(), 0, access=mmap.ACCESS_READ)
        cache_identity = {
            "schema": FAST_CACHE_SCHEMA,
            "architecture": "large-v2",
            "role": role,
            "rows_sha256": descriptor["sha256"],
            "rows_bytes": size,
            "record_count": expected_count,
            "score_scale_cp_hex": config.score_scale_cp.hex(),
            "lambda_hex": config.lambda_.hex(),
        }
        self.cache_identity_sha256 = hashlib.sha256(_canonical_json(cache_identity)).hexdigest()
        configured_root = os.environ.get(FAST_CACHE_ENV)
        cache_root = (
            Path(configured_root)
            if configured_root
            else path.parent / ".fast-training-cache"
        )
        self.cache_path = cache_root / f"large-v2-{role}-{self.cache_identity_sha256}.npz"
        self.cache_hit = self.cache_path.is_file()
        try:
            if self.cache_hit:
                self._load_cache(expected_count)
            else:
                _require(_sha256_file(path) == descriptor["sha256"], f"{role.upper()}_ROWS_SHA256")
                self._build_cache(expected_count, size)
        except BaseException:
            self.close()
            raise

    def _load_cache(self, expected_count: int) -> None:
        try:
            with np.load(self.cache_path, allow_pickle=False) as cached:
                identity = str(cached["identity_sha256"].item())
                arrays = {name: cached[name] for name in (
                    "offsets",
                    "stm_k_rows",
                    "stm_g_rows",
                    "opponent_k_rows",
                    "opponent_g_rows",
                    "total_pocket_units",
                    "targets",
                )}
        except (OSError, ValueError, KeyError) as error:
            raise TrainerError(f"{self.role.upper()}_FAST_CACHE_LOAD") from error
        _require(identity == self.cache_identity_sha256, f"{self.role.upper()}_FAST_CACHE_IDENTITY")
        maximum_active = arrays["stm_k_rows"].shape[1]
        _require(
            0 < maximum_active <= MAXIMUM_ACTIVE,
            f"{self.role.upper()}_FAST_CACHE_MAXIMUM_ACTIVE",
        )
        expected = {
            "offsets": ((expected_count,), np.dtype("uint64")),
            "stm_k_rows": ((expected_count, maximum_active), np.dtype("int32")),
            "stm_g_rows": ((expected_count, maximum_active), np.dtype("int32")),
            "opponent_k_rows": ((expected_count, maximum_active), np.dtype("int32")),
            "opponent_g_rows": ((expected_count, maximum_active), np.dtype("int32")),
            "total_pocket_units": ((expected_count,), np.dtype("int16")),
            "targets": ((expected_count,), np.dtype("float32")),
        }
        for name, (shape, dtype) in expected.items():
            _require(arrays[name].shape == shape and arrays[name].dtype == dtype, f"{self.role.upper()}_FAST_CACHE_{name.upper()}")
            setattr(self, f"_{name}", arrays[name])

    def _build_cache(self, expected_count: int, size: int) -> None:
        offsets = np.empty(expected_count, dtype=np.uint64)
        stm_k_rows = np.full((expected_count, MAXIMUM_ACTIVE), -1, dtype=np.int32)
        stm_g_rows = np.full((expected_count, MAXIMUM_ACTIVE), -1, dtype=np.int32)
        opponent_k_rows = np.full((expected_count, MAXIMUM_ACTIVE), -1, dtype=np.int32)
        opponent_g_rows = np.full((expected_count, MAXIMUM_ACTIVE), -1, dtype=np.int32)
        total_pocket_units = np.empty(expected_count, dtype=np.int16)
        targets = np.empty(expected_count, dtype=np.float32)
        start = 0
        index = 0
        maximum_active = 0
        while start < size:
            _require(index < expected_count, f"{self.role.upper()}_ROWS_COUNT")
            end = self._mapping.find(b"\n", start)
            _require(end >= start, f"{self.role.upper()}_ROW_NEWLINE")
            try:
                document = json.loads(self._mapping[start:end])
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise TrainerError(f"{self.role.upper()}_ROW_JSON") from error
            offsets[index] = start
            for key, output in (
                ("stm_k64_rows", stm_k_rows),
                ("stm_g1_rows", stm_g_rows),
                ("opponent_k64_rows", opponent_k_rows),
                ("opponent_g1_rows", opponent_g_rows),
            ):
                rows = document[key]
                _require(isinstance(rows, list) and 0 < len(rows) <= MAXIMUM_ACTIVE, f"{self.role.upper()}_CACHE_ROWS")
                output[index, : len(rows)] = rows
                maximum_active = max(maximum_active, len(rows))
            total_pocket_units[index] = document["total_pocket_units"]
            targets[index] = _target_probability(document, self.config)
            index += 1
            start = end + 1
        _require(start == size and index == expected_count, f"{self.role.upper()}_ROWS_FRAMING")
        stm_k_rows = stm_k_rows[:, :maximum_active]
        stm_g_rows = stm_g_rows[:, :maximum_active]
        opponent_k_rows = opponent_k_rows[:, :maximum_active]
        opponent_g_rows = opponent_g_rows[:, :maximum_active]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.cache_path.with_name(self.cache_path.stem + ".partial.npz")
        _require(not partial.exists(), f"{self.role.upper()}_FAST_CACHE_PARTIAL")
        try:
            np.savez(
                partial,
                identity_sha256=np.asarray(self.cache_identity_sha256),
                offsets=offsets,
                stm_k_rows=stm_k_rows,
                stm_g_rows=stm_g_rows,
                opponent_k_rows=opponent_k_rows,
                opponent_g_rows=opponent_g_rows,
                total_pocket_units=total_pocket_units,
                targets=targets,
            )
            os.replace(partial, self.cache_path)
        except BaseException:
            try:
                partial.unlink()
            except FileNotFoundError:
                pass
            raise
        self._offsets = offsets
        self._stm_k_rows = stm_k_rows
        self._stm_g_rows = stm_g_rows
        self._opponent_k_rows = opponent_k_rows
        self._opponent_g_rows = opponent_g_rows
        self._total_pocket_units = total_pocket_units
        self._targets = targets

    @staticmethod
    def _tensor(values: np.ndarray, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
        return torch.as_tensor(np.ascontiguousarray(values), dtype=dtype, device=device)

    def batch(self, indices: Sequence[int], device: torch.device) -> Batch:
        selected = np.asarray(indices, dtype=np.int64)
        _require(selected.ndim == 1 and selected.size > 0, f"{self.role.upper()}_BATCH_INDICES")
        return Batch(
            stm_k_rows=self._tensor(self._stm_k_rows[selected], device, torch.int64),
            stm_g_rows=self._tensor(self._stm_g_rows[selected], device, torch.int64),
            opponent_k_rows=self._tensor(self._opponent_k_rows[selected], device, torch.int64),
            opponent_g_rows=self._tensor(self._opponent_g_rows[selected], device, torch.int64),
            total_pocket_units=self._tensor(self._total_pocket_units[selected], device, torch.int64),
            targets=self._tensor(self._targets[selected], device, torch.float32),
        )

    def batch_slice(self, start: int, stop: int, device: torch.device) -> Batch:
        _require(0 <= start < stop <= len(self), f"{self.role.upper()}_BATCH_SLICE")
        selected = slice(start, stop)
        return Batch(
            stm_k_rows=self._tensor(self._stm_k_rows[selected], device, torch.int64),
            stm_g_rows=self._tensor(self._stm_g_rows[selected], device, torch.int64),
            opponent_k_rows=self._tensor(self._opponent_k_rows[selected], device, torch.int64),
            opponent_g_rows=self._tensor(self._opponent_g_rows[selected], device, torch.int64),
            total_pocket_units=self._tensor(self._total_pocket_units[selected], device, torch.int64),
            targets=self._tensor(self._targets[selected], device, torch.float32),
        )

    @overload
    def __getitem__(self, index: int) -> Sample: ...

    @overload
    def __getitem__(self, index: slice) -> list[Sample]: ...

    def __getitem__(self, index: int | slice) -> Sample | list[Sample]:
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        start = int(self._offsets[index])
        end = self._mapping.find(b"\n", start)
        _require(end >= start, f"{self.role.upper()}_ROW_RUNTIME_NEWLINE")
        document = _strict_json_bytes(
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


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise TrainerError(code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _strict_json_bytes(payload: bytes, code: str) -> Any:
    try:
        text = payload.decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=lambda pairs: _pairs_without_duplicates(pairs, code),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise TrainerError(code) from error
    canonical = _canonical_json(document)
    _require(payload in {canonical, canonical.replace(b"\n", b"\r\n")}, f"{code}_CANONICAL")
    return document


def _pairs_without_duplicates(pairs: Sequence[tuple[str, Any]], code: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"{code}:duplicate:{key}")
        output[key] = value
    return output


def _read_regular(path: Path, code: str, maximum_bytes: int = 64 * 1024 * 1024) -> bytes:
    _require(path.is_file() and not path.is_symlink(), code)
    size = path.stat().st_size
    _require(0 < size <= maximum_bytes, f"{code}_SIZE")
    payload = path.read_bytes()
    _require(len(payload) == size, f"{code}_SHORT_READ")
    return payload


def _validate_hex(value: Any, pattern: re.Pattern[str], code: str) -> str:
    _require(isinstance(value, str) and pattern.fullmatch(value) is not None, code)
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or (
        isinstance(value, float) and math.isfinite(value)
    )


def _authenticate_static_contracts() -> None:
    pins = (
        (TRAINING_CONTRACT_PATH, TRAINING_CONTRACT_SHA256, "TRAINING_CONTRACT"),
        (LARGE_REFERENCE_PATH, LARGE_REFERENCE_SHA256, "LARGE_REFERENCE"),
        (LARGE_FEATURE_SCHEMA_PATH, LARGE_FEATURE_SCHEMA_SHA256, "LARGE_FEATURE_SCHEMA"),
    )
    for path, expected, code in pins:
        _require(_sha256_file(path) == expected, code)


def _load_config(path: Path, expected_sha256: str) -> TrainingConfig:
    _validate_hex(expected_sha256, HEX64, "CONFIG_SHA256_ARGUMENT")
    payload = _read_regular(path, "CONFIG", 1024 * 1024)
    _require(hashlib.sha256(payload).hexdigest() == expected_sha256, "CONFIG_SHA256")
    document = _strict_json_bytes(payload, "CONFIG_JSON")
    _require(isinstance(document, dict) and set(document) == CONFIG_KEYS, "CONFIG_KEYS")
    _require(document.get("schema") == CONFIG_SCHEMA, "CONFIG_SCHEMA")
    mode = document.get("mode")
    device = document.get("device")
    _require(mode in {"fixture", "production"}, "CONFIG_MODE")
    _require(device in {"cpu", "cuda"}, "CONFIG_DEVICE")
    _require(mode == "production" or device == "cpu", "FIXTURE_DEVICE")
    integer_fields = (
        "cpu_threads",
        "batch_size",
        "epochs",
        "seed",
        "validation_interval_steps",
        "checkpoint_interval_steps",
    )
    for key in integer_fields:
        _require(_is_int(document.get(key)), f"CONFIG_{key.upper()}_TYPE")
    _require(document["cpu_threads"] == 1, "CONFIG_CPU_THREADS")
    _require(document["batch_size"] > 0, "CONFIG_BATCH_SIZE")
    _require(document["epochs"] > 0, "CONFIG_EPOCHS")
    _require(0 <= document["seed"] < 2**63, "CONFIG_SEED")
    _require(document["validation_interval_steps"] > 0, "CONFIG_VALIDATION_INTERVAL")
    _require(document["checkpoint_interval_steps"] > 0, "CONFIG_CHECKPOINT_INTERVAL")
    for key in ("score_scale_cp", "lambda", "loss_exponent", "sparse_learning_rate", "dense_learning_rate"):
        _require(_is_number(document.get(key)), f"CONFIG_{key.upper()}_TYPE")
    _require(float(document["score_scale_cp"]) > 0.0, "CONFIG_SCORE_SCALE")
    _require(0.0 <= float(document["lambda"]) <= 1.0, "CONFIG_LAMBDA")
    _require(float(document["loss_exponent"]) > 0.0, "CONFIG_LOSS_EXPONENT")
    _require(float(document["sparse_learning_rate"]) > 0.0, "CONFIG_SPARSE_LR")
    _require(float(document["dense_learning_rate"]) > 0.0, "CONFIG_DENSE_LR")
    return TrainingConfig(
        mode=cast(str, mode),
        device=cast(str, device),
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
        sha256=expected_sha256,
        document=document,
    )


def _git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    _require(result.returncode == 0 and not result.stderr, "GIT_IDENTITY")
    return result.stdout.strip()


def _validate_source(source: SourceIdentity, production: bool) -> None:
    for value, code in (
        (source.commit, "SOURCE_COMMIT"),
        (source.tree, "SOURCE_TREE"),
        (source.src_tree, "SOURCE_SRC_TREE"),
    ):
        _validate_hex(value, HEX40, code)
    _require(_git_output("rev-parse", "HEAD") == source.commit, "SOURCE_HEAD")
    _require(_git_output("rev-parse", "HEAD^{tree}") == source.tree, "SOURCE_TREE_IDENTITY")
    _require(_git_output("rev-parse", "HEAD:src") == source.src_tree, "SOURCE_SRC_TREE_IDENTITY")
    if production:
        _require(_git_output("status", "--porcelain=v1") == "", "SOURCE_DIRTY")


def _configure_runtime(config: TrainingConfig) -> torch.device:
    torch.set_num_threads(config.cpu_threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        _require(torch.get_num_interop_threads() == 1, "TORCH_INTEROP_THREADS")
    torch.use_deterministic_algorithms(True)
    _require(torch.get_num_threads() == config.cpu_threads, "TORCH_THREADS")
    _require(torch.get_num_interop_threads() == 1, "TORCH_INTEROP_THREADS")
    if config.device == "cuda":
        _require(
            os.environ.get("CUBLAS_WORKSPACE_CONFIG") == CUBLAS_WORKSPACE_CONFIG,
            "CUBLAS_WORKSPACE_CONFIG",
        )
        _require(torch.cuda.is_available(), "CUDA_UNAVAILABLE")
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        return torch.device("cuda:0")
    return torch.device("cpu")


def _runtime_identity(device: torch.device) -> dict[str, Any]:
    cuda: dict[str, Any] | None = None
    if device.type == "cuda":
        properties = torch.cuda.get_device_properties(device)
        cuda = {
            "device_index": device.index or 0,
            "name": properties.name,
            "capability": list(properties.major_minor) if hasattr(properties, "major_minor") else [properties.major, properties.minor],
            "total_memory": properties.total_memory,
            "torch_cuda": str(torch.version.cuda),
            "cudnn": torch.backends.cudnn.version(),
        }
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "torch": str(torch.__version__),
        "numpy": str(np.__version__),
        "device": str(device),
        "cuda": cuda,
        "torch_threads": torch.get_num_threads(),
        "torch_interop_threads": torch.get_num_interop_threads(),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
    }


def _model_input_key(
    stm_k: Sequence[int],
    stm_g: Sequence[int],
    opponent_k: Sequence[int],
    opponent_g: Sequence[int],
    total_pocket_units: int,
) -> str:
    digest = hashlib.sha256()
    digest.update(MODEL_INPUT_DOMAIN)
    digest.update(bytes.fromhex(FEATURE_CONTRACT_SHA256))
    for rows in (stm_k, stm_g, opponent_k, opponent_g):
        ordered = sorted(rows)
        digest.update(struct.pack("<I", len(ordered)))
        digest.update(b"".join(struct.pack("<I", row) for row in ordered))
    digest.update(struct.pack("<I", total_pocket_units))
    return digest.hexdigest()


def _validated_rows(value: Any, dimensions: int, code: str) -> tuple[int, ...]:
    _require(isinstance(value, list), f"{code}_TYPE")
    _require(0 < len(value) <= MAXIMUM_ACTIVE, f"{code}_COUNT")
    _require(all(_is_int(row) and 0 <= row < dimensions for row in value), f"{code}_RANGE")
    _require(len(set(value)) == len(value), f"{code}_DUPLICATE")
    return tuple(sorted(value))


def _target_probability(row: Mapping[str, Any], config: TrainingConfig) -> float:
    result = row.get("result_side_to_move")
    _require(result in {-1, 0, 1}, "ROW_RESULT")
    result_target = {-1: 0.0, 0: 0.5, 1: 1.0}[cast(int, result)]
    terminal = row.get("terminal_reason")
    _require(isinstance(terminal, str) and terminal, "ROW_TERMINAL_REASON")
    kind = row.get("teacher_score_kind")
    bound = row.get("teacher_bound")
    value = row.get("teacher_score_value")
    _require(_is_int(value), "ROW_TEACHER_VALUE")
    if terminal != "ongoing":
        _require(kind == "none" and bound == "none" and value == 0, "ROW_TERMINAL_TEACHER")
        return result_target
    _require(bound == "exact", "ROW_TEACHER_BOUND")
    if kind == "centipawn":
        scaled = float(value) / config.score_scale_cp
        if scaled >= 0.0:
            teacher = 1.0 / (1.0 + math.exp(-scaled))
        else:
            exponential = math.exp(scaled)
            teacher = exponential / (1.0 + exponential)
    elif kind == "mate-plies":
        _require(value != 0, "ROW_MATE_ZERO")
        teacher = 1.0 if value > 0 else 0.0
    else:
        raise TrainerError("ROW_TEACHER_KIND")
    return teacher * config.lambda_ + result_target * (1.0 - config.lambda_)


def _validate_row(document: Any, role: str, config: TrainingConfig) -> Sample:
    _require(isinstance(document, dict) and set(document) == ROW_KEYS, "ROW_KEYS")
    _require(document.get("schema") == ROW_SCHEMA, "ROW_SCHEMA")
    _require(document.get("role") == role, "ROW_ROLE")
    for key in ("campaign_id", "chunk_id", "game_id", "trajectory_id"):
        _require(isinstance(document.get(key), str) and UUID.fullmatch(document[key]) is not None, f"ROW_{key.upper()}")
    for key in ("position_identity_sha256", "raw_record_key", "large_model_input_key"):
        _validate_hex(document.get(key), HEX64, f"ROW_{key.upper()}")
    for key in (
        "chunk_index",
        "move_time_ms",
        "ply",
        "search_depth",
        "search_nodes",
        "search_seldepth",
        "sequence",
        "total_pocket_units",
    ):
        _require(_is_int(document.get(key)) and document[key] >= 0, f"ROW_{key.upper()}")
    _require(document.get("side_to_move") in {"white", "black"}, "ROW_SIDE_TO_MOVE")
    _require(document.get("game_result_white") in {-1, 0, 1}, "ROW_GAME_RESULT_WHITE")
    expected_stm_result = (
        document["game_result_white"]
        if document["side_to_move"] == "white"
        else -document["game_result_white"]
    )
    _require(document.get("result_side_to_move") == expected_stm_result, "ROW_RESULT_PERSPECTIVE")
    stm_k = _validated_rows(document.get("stm_k64_rows"), K_INPUTS, "ROW_STM_K")
    stm_g = _validated_rows(document.get("stm_g1_rows"), G_INPUTS, "ROW_STM_G")
    opponent_k = _validated_rows(document.get("opponent_k64_rows"), K_INPUTS, "ROW_OPPONENT_K")
    opponent_g = _validated_rows(document.get("opponent_g1_rows"), G_INPUTS, "ROW_OPPONENT_G")
    total_pocket_units = document["total_pocket_units"]
    _require(total_pocket_units <= 30, "ROW_POCKET_UNITS")
    expected_key = _model_input_key(stm_k, stm_g, opponent_k, opponent_g, total_pocket_units)
    _require(document["large_model_input_key"] == expected_key, "ROW_MODEL_INPUT_KEY")
    return Sample(
        stm_k_rows=stm_k,
        stm_g_rows=stm_g,
        opponent_k_rows=opponent_k,
        opponent_g_rows=opponent_g,
        total_pocket_units=total_pocket_units,
        target_probability=_target_probability(document, config),
        raw_record_key=document["raw_record_key"],
        model_input_key=expected_key,
    )


def _load_admission(
    result_path: Path,
    expected_sha256: str,
    config: TrainingConfig,
) -> tuple[RowDataset, RowDataset, AdmissionInputs]:
    _validate_hex(expected_sha256, HEX64, "ADMISSION_SHA256_ARGUMENT")
    payload = _read_regular(result_path, "ADMISSION_RESULT", 16 * 1024 * 1024)
    _require(hashlib.sha256(payload).hexdigest() == expected_sha256, "ADMISSION_RESULT_SHA256")
    result = _strict_json_bytes(payload, "ADMISSION_RESULT_JSON")
    _require(isinstance(result, dict), "ADMISSION_RESULT_DOCUMENT")
    expected_static = {
        "schema": RESULT_SCHEMA,
        "projection": "large-k64g1-v1",
        "admission_contract_sha256": ADMISSION_CONTRACT_SHA256,
        "physical_schema_sha256": PHYSICAL_SCHEMA_SHA256,
        "large_feature_contract_sha256": FEATURE_CONTRACT_SHA256,
        "large_feature_reference_sha256": LARGE_REFERENCE_SHA256,
        "large_feature_schema_file_sha256": LARGE_FEATURE_SCHEMA_SHA256,
        "large_training_contract_sha256": TRAINING_CONTRACT_SHA256,
        "legacy_v1_remains_default": True,
        "transactional_output": True,
    }
    for key, expected in expected_static.items():
        _require(result.get(key) == expected, f"ADMISSION_{key.upper()}")
    diagnostic = result.get("status") == "PASS_PRODUCTION_DIAGNOSTIC_ADMISSION"
    if config.mode == "production":
        _require(
            result.get("status")
            in {"PASS_PRODUCTION_ADMISSION", "PASS_PRODUCTION_DIAGNOSTIC_ADMISSION"},
            "ADMISSION_PRODUCTION_STATUS",
        )
        _require(result.get("training_admissible") is True, "ADMISSION_PRODUCTION_CREDIT")
        _require(result.get("fixture_mode") is False, "ADMISSION_PRODUCTION_FIXTURE")
        if diagnostic:
            _require(result.get("diagnostic_only") is True, "ADMISSION_DIAGNOSTIC_ONLY")
            _require(result.get("release_admissible") is False, "ADMISSION_RELEASE_BOUNDARY")
            exception = result.get("diagnostic_exception")
            _require(isinstance(exception, dict), "ADMISSION_DIAGNOSTIC_EXCEPTION")
            _require(
                exception
                == {
                    "campaign_addendum_sha256": "8c9dd55c22664481ad18cb4cb8d38443ecfee81d80368ac56cd257e83005372c",
                    "frozen_intersections": {
                        "game_id": 0,
                        "large_model_input_key": 17_262,
                        "model_input_key": 17_262,
                        "position_identity": 17_127,
                        "raw_record_key": 0,
                        "trajectory_id": 0,
                    },
                    "owner_waiver_sha256": "a67fe2ec5b2058b665c20da8dc158af8e91560b4de05a64824dbbdbfe72c5e2c",
                    "validation_checkpoint_or_seed_selection": False,
                    "validation_gradients": False,
                    "validation_usage": "forward-only health telemetry",
                },
                "ADMISSION_DIAGNOSTIC_BINDING",
            )
        else:
            _require(result.get("diagnostic_only") is not True, "ADMISSION_UNDECLARED_DIAGNOSTIC")
            _require(result.get("release_admissible") is not False, "ADMISSION_RELEASE_BOUNDARY")
    else:
        _require(result.get("status") == "PASS_FIXTURE_NONADMISSIBLE", "ADMISSION_FIXTURE_STATUS")
        _require(result.get("training_admissible") is False, "ADMISSION_FIXTURE_CREDIT")
        _require(result.get("fixture_mode") is True, "ADMISSION_FIXTURE_MODE")
        _require(not diagnostic, "ADMISSION_FIXTURE_DIAGNOSTIC")
    roles = result.get("roles")
    _require(isinstance(roles, dict) and set(roles) == {"train", "validation"}, "ADMISSION_ROLES")
    intersections = result.get("intersections")
    _require(isinstance(intersections, dict), "ADMISSION_INTERSECTIONS")
    if diagnostic:
        _require(
            intersections
            == {
                "game_id": 0,
                "large_model_input_key": 17_262,
                "model_input_key": 17_262,
                "position_identity": 17_127,
                "raw_record_key": 0,
                "trajectory_id": 0,
            },
            "ADMISSION_DIAGNOSTIC_INTERSECTIONS",
        )
    else:
        _require(all(value == 0 for value in intersections.values()), "ADMISSION_INTERSECTION_NONZERO")
    sets = result.get("sets")
    _require(isinstance(sets, dict) and set(sets) == {"train", "validation"}, "ADMISSION_SETS")
    root = result_path.resolve(strict=True).parent
    loaded: dict[str, RowDataset] = {}
    try:
        for role in ("train", "validation"):
            summary = roles.get(role)
            _require(isinstance(summary, dict), f"ADMISSION_{role.upper()}_SUMMARY")
            _require(set(summary) == {"chunk_count", "record_count", "rows", "trajectory_count"}, f"ADMISSION_{role.upper()}_KEYS")
            _require(_is_int(summary.get("record_count")) and summary["record_count"] > 0, f"ADMISSION_{role.upper()}_COUNT")
            rows_path = root / f"{role}.rows.jsonl"
            samples = RowDataset(
                rows_path,
                summary["rows"],
                role,
                config,
                summary["record_count"],
            )
            loaded[role] = samples
            _require(len(samples) == summary["record_count"], f"ADMISSION_{role.upper()}_RECORD_COUNT")
    except BaseException:
        for dataset in loaded.values():
            dataset.close()
        raise
    train_summary = roles["train"]
    validation_summary = roles["validation"]
    raw_set_sha256: dict[str, str] = {}
    for role, summary in (("train", train_summary), ("validation", validation_summary)):
        role_sets = sets.get(role)
        _require(isinstance(role_sets, dict), f"ADMISSION_{role.upper()}_SETS")
        raw_set = role_sets.get("raw_record_key")
        _require(isinstance(raw_set, dict), f"ADMISSION_{role.upper()}_RAW_SET")
        _require(
            raw_set.get("observations") == summary["record_count"]
            and raw_set.get("unique_keys") == summary["record_count"]
            and raw_set.get("duplicate_observations") == 0,
            f"ADMISSION_{role.upper()}_RAW_SET_COUNTS",
        )
        raw_set_sha256[role] = _validate_hex(
            raw_set.get("ordered_set_sha256"),
            HEX64,
            f"ADMISSION_{role.upper()}_RAW_SET_SHA256",
        )
    return (
        loaded["train"],
        loaded["validation"],
        AdmissionInputs(
            result_sha256=expected_sha256,
            source_manifest_sha256=_validate_hex(result.get("source_manifest_sha256"), HEX64, "ADMISSION_SOURCE_MANIFEST"),
            train_rows_sha256=train_summary["rows"]["sha256"],
            validation_rows_sha256=validation_summary["rows"]["sha256"],
            train_raw_record_ordered_set_sha256=raw_set_sha256["train"],
            validation_raw_record_ordered_set_sha256=raw_set_sha256["validation"],
            train_rows_bytes=train_summary["rows"]["bytes"],
            validation_rows_bytes=validation_summary["rows"]["bytes"],
            train_record_count=train_summary["record_count"],
            validation_record_count=validation_summary["record_count"],
            status=result["status"],
            training_admissible=result["training_admissible"],
            diagnostic_only=diagnostic,
            release_admissible=bool(result.get("release_admissible", not diagnostic)),
        ),
    )


def _round_away(value: torch.Tensor) -> torch.Tensor:
    return torch.sign(value) * torch.floor(torch.abs(value) + 0.5)


def _ste_round_away(value: torch.Tensor) -> torch.Tensor:
    rounded = _round_away(value)
    return value + (rounded - value).detach()


def _ste_floor(value: torch.Tensor) -> torch.Tensor:
    floored = torch.floor(value)
    return value + (floored - value).detach()


def _ste_trunc(value: torch.Tensor) -> torch.Tensor:
    truncated = torch.trunc(value)
    return value + (truncated - value).detach()


class LargeQatModel(nn.Module):
    """Sparse shared transformers plus bucketed SFNNv16 integer-equivalent trunks."""

    def __init__(self, shape: ModelShape, device: torch.device, initialize: bool = True) -> None:
        super().__init__()
        _require(shape.k_lanes % 2 == 0 and shape.g_lanes % 2 == 0, "MODEL_PAIR_LANES")
        _require(shape.fc0_outputs >= 32, "MODEL_SKIP_LANES")
        self.shape = shape
        self.k = nn.Embedding(shape.k_inputs, shape.k_lanes, sparse=True, device=device, dtype=torch.float32)
        self.g = nn.Embedding(shape.g_inputs, shape.g_lanes, sparse=True, device=device, dtype=torch.float32)
        self.k_bias = nn.Parameter(torch.empty(shape.k_lanes, device=device, dtype=torch.float32))
        self.g_bias = nn.Parameter(torch.empty(shape.g_lanes, device=device, dtype=torch.float32))
        self.fc0_weight = nn.Parameter(torch.empty(shape.buckets, shape.fc0_outputs, shape.dense_inputs, device=device))
        self.fc0_bias = nn.Parameter(torch.empty(shape.buckets, shape.fc0_outputs, device=device))
        self.fc1_weight = nn.Parameter(torch.empty(shape.buckets, shape.fc1_outputs, shape.fc1_inputs, device=device))
        self.fc1_bias = nn.Parameter(torch.empty(shape.buckets, shape.fc1_outputs, device=device))
        self.fc2_weight = nn.Parameter(torch.empty(shape.buckets, shape.fc2_inputs, device=device))
        self.fc2_bias = nn.Parameter(torch.empty(shape.buckets, device=device))
        if initialize:
            self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.uniform_(self.k.weight, -4.0 / FEATURE_WEIGHT_SCALE, 4.0 / FEATURE_WEIGHT_SCALE)
        nn.init.uniform_(self.g.weight, -4.0 / FEATURE_WEIGHT_SCALE, 4.0 / FEATURE_WEIGHT_SCALE)
        nn.init.constant_(self.k_bias, 128.0 / FEATURE_BIAS_SCALE)
        nn.init.constant_(self.g_bias, 128.0 / FEATURE_BIAS_SCALE)
        nn.init.uniform_(self.fc0_weight, -2.0 / FC0_WEIGHT_SCALE, 2.0 / FC0_WEIGHT_SCALE)
        nn.init.zeros_(self.fc0_bias)
        nn.init.uniform_(self.fc1_weight, -2.0 / FC1_WEIGHT_SCALE, 2.0 / FC1_WEIGHT_SCALE)
        nn.init.zeros_(self.fc1_bias)
        nn.init.uniform_(self.fc2_weight, -2.0 / FC2_WEIGHT_SCALE, 2.0 / FC2_WEIGHT_SCALE)
        nn.init.zeros_(self.fc2_bias)

    def sparse_parameters(self) -> list[nn.Parameter]:
        return [self.k.weight, self.g.weight]

    def dense_parameters(self) -> list[nn.Parameter]:
        sparse_ids = {id(parameter) for parameter in self.sparse_parameters()}
        return [parameter for parameter in self.parameters() if id(parameter) not in sparse_ids]

    @staticmethod
    def _quantized_integer(parameter: torch.Tensor, scale: int) -> torch.Tensor:
        return _ste_round_away(parameter * scale)

    def _transform(self, rows: Sequence[int], embedding: nn.Embedding, bias: torch.Tensor) -> torch.Tensor:
        indices = torch.tensor(rows, dtype=torch.int64, device=embedding.weight.device)
        selected = F.embedding(indices, embedding.weight, sparse=True)
        values = self._quantized_integer(selected, FEATURE_WEIGHT_SCALE).sum(dim=0)
        values = values + self._quantized_integer(bias, FEATURE_BIAS_SCALE)
        return torch.clamp(values, 0.0, float(TRANSFORMER_MAXIMUM))

    def _transform_batch(
        self,
        rows: torch.Tensor,
        embedding: nn.Embedding,
        bias: torch.Tensor,
    ) -> torch.Tensor:
        mask = rows.ge(0)
        selected = F.embedding(rows.clamp_min(0), embedding.weight, sparse=True)
        values = self._quantized_integer(selected, FEATURE_WEIGHT_SCALE)
        values = values * mask.unsqueeze(-1)
        values = values.sum(dim=1)
        values = values + self._quantized_integer(bias, FEATURE_BIAS_SCALE)
        return torch.clamp(values, 0.0, float(TRANSFORMER_MAXIMUM))

    @staticmethod
    def _pair(values: torch.Tensor) -> torch.Tensor:
        half = values.shape[0] // 2
        return _ste_floor(values[:half] * values[half:] / PAIR_DIVISOR)

    @staticmethod
    def _pair_batch(values: torch.Tensor) -> torch.Tensor:
        half = values.shape[1] // 2
        return _ste_floor(values[:, :half] * values[:, half:] / PAIR_DIVISOR)

    @staticmethod
    def _squared(values: torch.Tensor, shift: int) -> torch.Tensor:
        return torch.clamp(_ste_floor(values * values / float(1 << (2 * shift + 7))), 0.0, 127.0)

    @staticmethod
    def _clipped(values: torch.Tensor, shift: int) -> torch.Tensor:
        return torch.clamp(_ste_floor(values / float(1 << shift)), 0.0, 127.0)

    def forward_sample(self, sample: Sample, trace: bool = False) -> torch.Tensor | tuple[torch.Tensor, dict[str, torch.Tensor | int]]:
        stm_k = self._transform(sample.stm_k_rows, self.k, self.k_bias)
        stm_g = self._transform(sample.stm_g_rows, self.g, self.g_bias)
        opponent_k = self._transform(sample.opponent_k_rows, self.k, self.k_bias)
        opponent_g = self._transform(sample.opponent_g_rows, self.g, self.g_bias)
        stm = torch.cat((self._pair(stm_k), self._pair(stm_g)))
        opponent = torch.cat((self._pair(opponent_k), self._pair(opponent_g)))
        dense = torch.cat((stm, opponent))
        bucket = min(self.shape.buckets - 1, sample.total_pocket_units // 4)
        fc0_weight = self._quantized_integer(self.fc0_weight[bucket], FC0_WEIGHT_SCALE)
        fc0_bias = self._quantized_integer(self.fc0_bias[bucket], FC0_BIAS_SCALE)
        fc0 = F.linear(dense, fc0_weight, fc0_bias)
        fc0_squared = self._squared(fc0, 7)
        fc0_clipped = self._clipped(fc0, 7)
        fc1_input = torch.cat((fc0_squared, fc0_clipped))
        fc1_weight = self._quantized_integer(self.fc1_weight[bucket], FC1_WEIGHT_SCALE)
        fc1_bias = self._quantized_integer(self.fc1_bias[bucket], FC1_BIAS_SCALE)
        fc1 = F.linear(fc1_input, fc1_weight, fc1_bias)
        fc1_squared = self._squared(fc1, 6)
        fc1_clipped = self._clipped(fc1, 6)
        fc2_input = torch.cat((fc0_squared, fc0_clipped, fc1_squared, fc1_clipped))
        fc2_weight = self._quantized_integer(self.fc2_weight[bucket], FC2_WEIGHT_SCALE)
        fc2_bias = self._quantized_integer(self.fc2_bias[bucket], FC2_BIAS_SCALE)
        fc2 = torch.dot(fc2_input, fc2_weight) + fc2_bias
        fwd = fc2 + fc0[30] - fc0[31]
        output_value = _ste_trunc(fwd * OUTPUT_NUMERATOR / OUTPUT_DENOMINATOR)
        centipawns = output_value / ENGINE_UNITS_PER_CP
        if not trace:
            return centipawns
        return centipawns, {
            "bucket": bucket,
            "stm_k": stm_k,
            "stm_g": stm_g,
            "opponent_k": opponent_k,
            "opponent_g": opponent_g,
            "stm": stm,
            "opponent": opponent,
            "dense": dense,
            "fc0": fc0,
            "fc0_squared": fc0_squared,
            "fc0_clipped": fc0_clipped,
            "fc1": fc1,
            "fc1_squared": fc1_squared,
            "fc1_clipped": fc1_clipped,
            "fc2": fc2,
            "fwd": fwd,
            "output_value": output_value,
        }

    def forward_batch(self, batch: Batch) -> torch.Tensor:
        stm_k = self._transform_batch(batch.stm_k_rows, self.k, self.k_bias)
        stm_g = self._transform_batch(batch.stm_g_rows, self.g, self.g_bias)
        opponent_k = self._transform_batch(
            batch.opponent_k_rows, self.k, self.k_bias
        )
        opponent_g = self._transform_batch(
            batch.opponent_g_rows, self.g, self.g_bias
        )
        stm = torch.cat((self._pair_batch(stm_k), self._pair_batch(stm_g)), dim=1)
        opponent = torch.cat(
            (self._pair_batch(opponent_k), self._pair_batch(opponent_g)), dim=1
        )
        dense = torch.cat((stm, opponent), dim=1)
        buckets = torch.clamp(
            torch.div(batch.total_pocket_units, 4, rounding_mode="floor"),
            max=self.shape.buckets - 1,
        )

        fc0_weight = self._quantized_integer(
            self.fc0_weight[buckets], FC0_WEIGHT_SCALE
        )
        fc0_bias = self._quantized_integer(self.fc0_bias[buckets], FC0_BIAS_SCALE)
        fc0 = torch.bmm(fc0_weight, dense.unsqueeze(2)).squeeze(2) + fc0_bias
        fc0_squared = self._squared(fc0, 7)
        fc0_clipped = self._clipped(fc0, 7)
        fc1_input = torch.cat((fc0_squared, fc0_clipped), dim=1)

        fc1_weight = self._quantized_integer(
            self.fc1_weight[buckets], FC1_WEIGHT_SCALE
        )
        fc1_bias = self._quantized_integer(self.fc1_bias[buckets], FC1_BIAS_SCALE)
        fc1 = torch.bmm(fc1_weight, fc1_input.unsqueeze(2)).squeeze(2) + fc1_bias
        fc1_squared = self._squared(fc1, 6)
        fc1_clipped = self._clipped(fc1, 6)
        fc2_input = torch.cat(
            (fc0_squared, fc0_clipped, fc1_squared, fc1_clipped), dim=1
        )

        fc2_weight = self._quantized_integer(
            self.fc2_weight[buckets], FC2_WEIGHT_SCALE
        )
        fc2_bias = self._quantized_integer(self.fc2_bias[buckets], FC2_BIAS_SCALE)
        fc2 = torch.sum(fc2_input * fc2_weight, dim=1) + fc2_bias
        fwd = fc2 + fc0[:, 30] - fc0[:, 31]
        output_value = _ste_trunc(fwd * OUTPUT_NUMERATOR / OUTPUT_DENOMINATOR)
        return output_value / ENGINE_UNITS_PER_CP

    def probabilities(
        self, samples: Batch | Sequence[Sample], score_scale_cp: float
    ) -> torch.Tensor:
        if isinstance(samples, Batch):
            centipawns = self.forward_batch(samples)
        else:
            centipawns = torch.stack(
                [cast(torch.Tensor, self.forward_sample(sample)) for sample in samples]
            )
        return torch.sigmoid(centipawns / score_scale_cp)


def _feistel_value(value: int, bits: int, key: bytes) -> int:
    half_bits = bits // 2
    mask = (1 << half_bits) - 1
    left = value >> half_bits
    right = value & mask
    for round_index in range(8):
        round_value = int.from_bytes(
            hashlib.sha256(key + bytes((round_index,)) + right.to_bytes((half_bits + 7) // 8, "little")).digest()[:8],
            "little",
        ) & mask
        left, right = right, left ^ round_value
    return (left << half_bits) | right


def sample_order(count: int, seed: int, epoch: int, dataset_identity: str) -> list[int]:
    _require(count > 0, "ORDER_COUNT")
    _validate_hex(dataset_identity, HEX64, "ORDER_DATASET_IDENTITY")
    bits = max(2, (count - 1).bit_length())
    if bits % 2:
        bits += 1
    domain = 1 << bits
    key = hashlib.sha256(
        b"Crazyhouse-Stockfish NNUE V2 large Feistel v1\0"
        + seed.to_bytes(8, "little")
        + epoch.to_bytes(8, "little")
        + bytes.fromhex(dataset_identity)
    ).digest()
    order: list[int] = []
    for source in range(count):
        value = _feistel_value(source, bits, key)
        attempts = 1
        while value >= count:
            value = _feistel_value(value, bits, key)
            attempts += 1
            _require(attempts <= domain + 1, "ORDER_CYCLE_WALK")
        order.append(value)
    _require(sorted(order) == list(range(count)), "ORDER_PERMUTATION")
    return order


def _order_chain(previous: bytes, epoch: int, batch: int, indices: Sequence[int]) -> bytes:
    payload = previous + struct.pack("<II", epoch, batch)
    payload += b"".join(struct.pack("<I", index) for index in indices)
    return hashlib.sha256(payload).digest()


def _metric_chain(previous: bytes, metric: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(previous + _canonical_json(metric)).digest()


def _python_rng_document() -> dict[str, Any]:
    version, state, gaussian = random.getstate()
    return {
        "version": version,
        "state": list(state),
        "gaussian_hex": None if gaussian is None else float(gaussian).hex(),
    }


def _numpy_rng_document() -> dict[str, Any]:
    state = cast(tuple[Any, Any, Any, Any, Any], np.random.get_state())
    return {
        "generator": str(state[0]),
        "state": np.asarray(state[1], dtype=np.uint32).tolist(),
        "position": int(state[2]),
        "has_gauss": int(state[3]),
        "cached_gaussian_hex": float(state[4]).hex(),
    }


def _rng_document(device: torch.device) -> dict[str, Any]:
    cuda_states = [state.cpu() for state in torch.cuda.get_rng_state_all()] if device.type == "cuda" else []
    return {
        "python": _python_rng_document(),
        "numpy": _numpy_rng_document(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": cuda_states,
    }


def _restore_rng(document: Mapping[str, Any], device: torch.device) -> None:
    _require(set(document) == {"python", "numpy", "torch_cpu", "torch_cuda"}, "CHECKPOINT_RNG_KEYS")
    python_state = document["python"]
    numpy_state = document["numpy"]
    _require(isinstance(python_state, dict) and isinstance(numpy_state, dict), "CHECKPOINT_RNG_DOCUMENT")
    gaussian_hex = python_state["gaussian_hex"]
    random.setstate((python_state["version"], tuple(python_state["state"]), None if gaussian_hex is None else float.fromhex(gaussian_hex)))
    np.random.set_state(
        (
            numpy_state["generator"],
            np.asarray(numpy_state["state"], dtype=np.uint32),
            numpy_state["position"],
            numpy_state["has_gauss"],
            float.fromhex(numpy_state["cached_gaussian_hex"]),
        )
    )
    torch.set_rng_state(document["torch_cpu"])
    cuda_states = document["torch_cuda"]
    _require(isinstance(cuda_states, list), "CHECKPOINT_CUDA_RNG")
    if device.type == "cuda":
        _require(len(cuda_states) == torch.cuda.device_count(), "CHECKPOINT_CUDA_RNG_COUNT")
        torch.cuda.set_rng_state_all(cuda_states)
    else:
        _require(cuda_states == [], "CHECKPOINT_CPU_CUDA_RNG")


def _hash_update_tensor(digest: Any, tensor: torch.Tensor) -> None:
    digest.update(b"T")
    digest.update(str(tensor.dtype).encode("ascii") + b"\0")
    digest.update(struct.pack("<I", tensor.ndim))
    for dimension in tensor.shape:
        digest.update(struct.pack("<Q", dimension))
    flattened = tensor.detach().reshape(-1)
    for offset in range(0, flattened.numel(), 1_048_576):
        chunk = flattened[offset : offset + 1_048_576].cpu().contiguous()
        digest.update(chunk.numpy().tobytes(order="C"))


def _hash_update_state(digest: Any, value: Any) -> None:
    if isinstance(value, torch.Tensor):
        _hash_update_tensor(digest, value)
    elif isinstance(value, dict):
        digest.update(b"D" + struct.pack("<Q", len(value)))
        for key in sorted(value, key=lambda item: (type(item).__name__, repr(item))):
            _hash_update_state(digest, key)
            _hash_update_state(digest, value[key])
    elif isinstance(value, (list, tuple)):
        digest.update(b"L" + struct.pack("<Q", len(value)))
        for item in value:
            _hash_update_state(digest, item)
    elif isinstance(value, bool):
        digest.update(b"B1" if value else b"B0")
    elif isinstance(value, int):
        payload = str(value).encode("ascii")
        digest.update(b"I" + struct.pack("<Q", len(payload)) + payload)
    elif isinstance(value, float):
        payload = value.hex().encode("ascii")
        digest.update(b"F" + struct.pack("<Q", len(payload)) + payload)
    elif isinstance(value, str):
        payload = value.encode("utf-8")
        digest.update(b"S" + struct.pack("<Q", len(payload)) + payload)
    elif value is None:
        digest.update(b"N")
    else:
        raise TrainerError(f"STATE_HASH_TYPE:{type(value).__name__}")


def canonical_state_sha256(value: Any) -> str:
    digest = hashlib.sha256()
    _hash_update_state(digest, value)
    return digest.hexdigest()


def _checkpoint_payload(document: Mapping[str, Any]) -> bytes:
    buffer = io.BytesIO()
    torch.save(dict(document), buffer)
    payload = buffer.getvalue()
    header = CHECKPOINT_MAGIC + struct.pack("<Q", len(payload)) + hashlib.sha256(payload).digest()
    _require(len(header) == CHECKPOINT_HEADER_BYTES, "CHECKPOINT_HEADER_WIDTH")
    return header + payload


def _write_atomic_replace(path: Path, payload: bytes) -> None:
    partial = path.with_name(path.name + ".partial")
    _require(not partial.exists(), "OUTPUT_PARTIAL_EXISTS")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
    except BaseException:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
        raise


def _save_checkpoint(path: Path, document: Mapping[str, Any]) -> str:
    payload = _checkpoint_payload(document)
    _write_atomic_replace(path, payload)
    return hashlib.sha256(payload).hexdigest()


def _load_checkpoint(path: Path, expected_sha256: str) -> dict[str, Any]:
    _validate_hex(expected_sha256, HEX64, "CHECKPOINT_SHA256_ARGUMENT")
    payload = _read_regular(path, "CHECKPOINT", 16 * 1024 * 1024 * 1024)
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


def _shape_for(config: TrainingConfig) -> ModelShape:
    return PRODUCTION_SHAPE if config.mode == "production" else FIXTURE_SHAPE


def _initialize(
    config: TrainingConfig,
    device: torch.device,
) -> tuple[LargeQatModel, torch.optim.SparseAdam, torch.optim.AdamW]:
    random.seed(config.seed)
    np.random.seed(config.seed % 2**32)
    torch.manual_seed(config.seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(config.seed)
    model = LargeQatModel(_shape_for(config), device)
    sparse = torch.optim.SparseAdam(model.sparse_parameters(), lr=config.sparse_learning_rate)
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
    shape: ModelShape,
) -> dict[str, Any]:
    return {
        "schema": RUN_IDENTITY_SCHEMA,
        "mode": config.mode,
        "training_admissible": admission.training_admissible,
        "diagnostic_only": admission.diagnostic_only,
        "release_admissible": admission.release_admissible,
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
        "trainer_code_sha256": _sha256_file(Path(__file__).resolve()),
        "training_contract_sha256": TRAINING_CONTRACT_SHA256,
        "feature_contract_sha256": FEATURE_CONTRACT_SHA256,
        "architecture_sha256": ARCHITECTURE_SHA256,
        "quantization_sha256": QUANTIZATION_SHA256,
        "runtime": dict(runtime),
        "shape": asdict(shape),
        "parameter_count": shape.parameter_count,
        "dataloader_workers": 0,
        "sparse_optimizer": "torch.optim.SparseAdam",
        "dense_optimizer": "torch.optim.AdamW-weight_decay-0-foreach-false-fused-false",
    }


def _loss(
    model: LargeQatModel,
    samples: Batch | Sequence[Sample],
    config: TrainingConfig,
    device: torch.device,
) -> torch.Tensor:
    probabilities = model.probabilities(samples, config.score_scale_cp)
    targets = (
        samples.targets
        if isinstance(samples, Batch)
        else torch.tensor(
            [sample.target_probability for sample in samples],
            dtype=torch.float32,
            device=device,
        )
    )
    return torch.mean(torch.abs(probabilities - targets).pow(config.loss_exponent))


def _validation_metric(
    model: LargeQatModel,
    samples: Sequence[Sample],
    config: TrainingConfig,
    device: torch.device,
) -> float:
    model.eval()
    total = 0.0
    with torch.no_grad():
        for offset in range(0, len(samples), config.batch_size):
            stop = min(offset + config.batch_size, len(samples))
            batch = (
                samples.batch_slice(offset, stop, device)
                if isinstance(samples, RowDataset)
                else samples[offset:stop]
            )
            total += float(_loss(model, batch, config, device).cpu()) * len(batch)
    model.train()
    return total / len(samples)


def _checkpoint_document(
    identity: Mapping[str, Any],
    model: LargeQatModel,
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
        "rng": _rng_document(device),
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


def _validate_checkpoint_document(document: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
    expected_keys = {
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
    }
    _require(set(document) == expected_keys, "CHECKPOINT_KEYS")
    _require(document.get("schema") == CHECKPOINT_SCHEMA, "CHECKPOINT_SCHEMA")
    _require(document.get("identity") == identity, "CHECKPOINT_IDENTITY")
    cursor = document.get("cursor")
    _require(isinstance(cursor, dict) and set(cursor) == {"epoch", "batch_cursor", "global_step", "current_order"}, "CHECKPOINT_CURSOR")
    for key in ("epoch", "batch_cursor", "global_step"):
        _require(_is_int(cursor[key]) and cursor[key] >= 0, f"CHECKPOINT_CURSOR_{key.upper()}")
    _require(isinstance(document.get("order_chain"), torch.Tensor) is False, "CHECKPOINT_ORDER_CHAIN_TENSOR")
    _require(isinstance(document.get("order_chain"), bytes) and len(document["order_chain"]) == 32, "CHECKPOINT_ORDER_CHAIN")
    _require(isinstance(document.get("metric_chain"), bytes) and len(document["metric_chain"]) == 32, "CHECKPOINT_METRIC_CHAIN")
    _require(isinstance(document.get("resume_lineage"), bytes) and len(document["resume_lineage"]) == 32, "CHECKPOINT_RESUME_LINEAGE")
    _require(isinstance(document.get("metrics"), list), "CHECKPOINT_METRICS")
    _require(isinstance(document.get("complete"), bool), "CHECKPOINT_COMPLETE")


def _restore_checkpoint(
    document: Mapping[str, Any],
    model: LargeQatModel,
    sparse: torch.optim.SparseAdam,
    dense: torch.optim.AdamW,
    device: torch.device,
) -> tuple[int, int, int, list[int] | None, bytes, bytes, list[Mapping[str, Any]], bytes]:
    model.load_state_dict(document["model_state"], strict=True)
    sparse.load_state_dict(document["sparse_optimizer_state"])
    dense.load_state_dict(document["dense_optimizer_state"])
    _restore_rng(document["rng"], device)
    cursor = document["cursor"]
    order = cursor["current_order"]
    _require(order is None or (isinstance(order, list) and all(_is_int(item) for item in order)), "CHECKPOINT_ORDER")
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


def _result_document(
    status: str,
    identity: Mapping[str, Any],
    checkpoint_path: Path,
    checkpoint_sha256: str,
    model: LargeQatModel,
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
        "release_admissible": identity["release_admissible"],
        "model_selection_credit": False,
        "strength_credit": False,
        "legacy_v1_remains_default": True,
        "identity_sha256": hashlib.sha256(_canonical_json(identity)).hexdigest(),
        "checkpoint": {"path": checkpoint_path.name, "sha256": checkpoint_sha256, "bytes": checkpoint_path.stat().st_size},
        "cursor": {"epoch": epoch, "batch_cursor": batch_cursor, "global_step": global_step},
        "model_state_sha256": canonical_state_sha256(model.state_dict()),
        "sparse_optimizer_state_sha256": canonical_state_sha256(sparse.state_dict()),
        "dense_optimizer_state_sha256": canonical_state_sha256(dense.state_dict()),
        "order_chain_sha256": order_chain.hex(),
        "metric_chain_sha256": metric_chain.hex(),
        "metrics_sha256": hashlib.sha256(_canonical_json(list(metrics))).hexdigest(),
        "resume_lineage_sha256": resume_lineage.hex(),
    }


def _run_training(
    train_samples: Sequence[Sample],
    validation_samples: Sequence[Sample],
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
        order_chain = INITIAL_ORDER_CHAIN
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
        resume_lineage = hashlib.sha256(previous_lineage + bytes.fromhex(prior_checkpoint_sha256)).digest()

    checkpoint_path = output / "checkpoint.chv2"
    last_validation_step = max(
        (metric["step"] for metric in metrics if metric.get("kind") == "validation"),
        default=-1,
    )
    interrupted = False
    while epoch < config.epochs:
        if current_order is None:
            current_order = sample_order(len(train_samples), config.seed, epoch, dataset_identity)
            batch_cursor = 0
        batch_count = (len(current_order) + config.batch_size - 1) // config.batch_size
        while batch_cursor < batch_count:
            start = batch_cursor * config.batch_size
            indices = current_order[start : start + config.batch_size]
            batch = (
                train_samples.batch(indices, device)
                if isinstance(train_samples, RowDataset)
                else [train_samples[index] for index in indices]
            )
            sparse.zero_grad(set_to_none=True)
            dense.zero_grad(set_to_none=True)
            loss = _loss(model, batch, config, device)
            _require(torch.isfinite(loss).item(), "TRAINING_LOSS_NONFINITE")
            loss.backward()
            _require(model.k.weight.grad is not None and model.k.weight.grad.is_sparse, "K_GRADIENT_NOT_SPARSE")
            _require(model.g.weight.grad is not None and model.g.weight.grad.is_sparse, "G_GRADIENT_NOT_SPARSE")
            sparse.step()
            dense.step()
            order_chain = _order_chain(order_chain, epoch, batch_cursor, indices)
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
            metric_chain = _metric_chain(metric_chain, metric)
            if global_step % config.validation_interval_steps == 0:
                validation_loss = _validation_metric(model, validation_samples, config, device)
                validation_metric: Mapping[str, Any] = {
                    "kind": "validation",
                    "step": global_step,
                    "samples": len(validation_samples),
                    "loss_hex": validation_loss.hex(),
                }
                metrics.append(validation_metric)
                metric_chain = _metric_chain(metric_chain, validation_metric)
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
                _save_checkpoint(checkpoint_path, document)
            if stop_after_steps is not None and global_step >= stop_after_steps:
                interrupted = True
                break
        if interrupted:
            break
        epoch += 1
        batch_cursor = 0
        current_order = None

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
        metric_chain = _metric_chain(metric_chain, validation_metric)
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
        if complete and config.mode == "production" and identity["diagnostic_only"]
        else "PASS_PRODUCTION_TRAINING_COMPLETE"
        if complete and config.mode == "production"
        else "PASS_FIXTURE_TRAINING_COMPLETE_NONADMISSIBLE"
        if complete
        else "INTERRUPTED_PRODUCTION_DIAGNOSTIC_CHECKPOINT"
        if config.mode == "production" and identity["diagnostic_only"]
        else "INTERRUPTED_PRODUCTION_CHECKPOINT"
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
    _write_atomic_replace(output / "training-result.json", _canonical_json(result))
    return result


def _prepare_output(target: Path) -> Path:
    _require(target.parent.exists() and target.parent.is_dir(), "OUTPUT_PARENT")
    _require(not target.exists() and not target.is_symlink(), "OUTPUT_EXISTS")
    partial = target.with_name(target.name + ".partial")
    _require(not partial.exists() and not partial.is_symlink(), "OUTPUT_PARTIAL_EXISTS")
    partial.mkdir()
    return partial


def _commit_output(partial: Path, target: Path) -> None:
    os.replace(partial, target)


def train_or_resume(args: argparse.Namespace, resume: bool) -> Mapping[str, Any]:
    _authenticate_static_contracts()
    config = _load_config(args.config, args.config_sha256)
    source = SourceIdentity(args.source_commit, args.source_tree, args.src_tree)
    _validate_source(source, config.mode == "production")
    device = _configure_runtime(config)
    train_samples, validation_samples, admission = _load_admission(
        args.admission_result, args.admission_result_sha256, config
    )
    try:
        shape = _shape_for(config)
        identity = _identity(source, config, admission, _runtime_identity(device), shape)
        checkpoint: Mapping[str, Any] | None = None
        prior_sha: str | None = None
        if resume:
            prior_sha = args.checkpoint_sha256
            checkpoint = _load_checkpoint(args.checkpoint, prior_sha)
        if args.stop_after_steps is not None:
            _require(args.stop_after_steps > 0, "STOP_AFTER_STEPS")
        partial = _prepare_output(args.output_dir)
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
            _commit_output(partial, args.output_dir)
            return result
        except BaseException:
            if partial.exists():
                import shutil

                shutil.rmtree(partial)
            raise
    finally:
        train_samples.close()
        validation_samples.close()


def crc32c(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _quantized_numpy(tensor: torch.Tensor, scale: float, minimum: int, maximum: int, dtype: str) -> np.ndarray:
    values = tensor.detach().cpu().numpy().astype(np.float32, copy=False)
    # QAT multiplies float32 parameters by a scalar cast to float32.  Preserve
    # that exact pre-rounding value here so the serialized integer cannot
    # diverge at a half-integer boundary.
    scaled = values * np.float32(scale)
    widened = scaled.astype(np.float64, copy=False)
    rounded = np.sign(widened) * np.floor(np.abs(widened) + 0.5)
    _require(np.isfinite(rounded).all(), "EXPORT_NONFINITE")
    _require(bool((rounded >= minimum).all() and (rounded <= maximum).all()), "EXPORT_QUANTIZATION_RANGE")
    return rounded.astype(dtype, casting="unsafe", copy=False)


def _container_header(payload_sha256: bytes, provenance: Sequence[bytes]) -> bytes:
    _require(len(payload_sha256) == 32 and len(provenance) == 6 and all(len(item) == 32 for item in provenance), "EXPORT_PROVENANCE")
    header = bytearray(HEADER_BYTES)
    header[:16] = CONTAINER_MAGIC
    struct.pack_into("<IHHHHIIHH", header, 16, 0x01020304, HEADER_BYTES, 1, 0, 1, FILE_BYTES, PAYLOAD_BYTES, 10, 8)
    for index, value in enumerate((K_INPUTS, G_INPUTS, MAXIMUM_ACTIVE, 768, 256, 2, 512, 1024, 32, 64, 32, 128, 1)):
        struct.pack_into("<I", header, 40 + index * 4, value)
    struct.pack_into("<II", header, 92, 4, 7)
    for index, value in enumerate((1, 1, 1, 1, 3, 2, 2, 2)):
        struct.pack_into("<H", header, 100 + index * 2, value)
    for offset, value in (
        (116, 255),
        (120, 512),
        (124, 6),
        (128, 128),
        (132, 16),
        (136, 7),
        (140, 6),
        (144, 7),
        (148, OUTPUT_NUMERATOR),
        (152, OUTPUT_DENOMINATOR),
        (156, 1),
        (160, 1),
        (164, 624),
        (168, 40),
        (172, 1),
    ):
        struct.pack_into("<I", header, offset, value)
    for offset, value in zip(
        (224, 256, 288, 320, 352),
        map(bytes.fromhex, (RULE_PROFILE_SHA256, PHYSICAL_SCHEMA_SHA256, FEATURE_CONTRACT_SHA256, ARCHITECTURE_SHA256, QUANTIZATION_SHA256)),
    ):
        header[offset : offset + 32] = value
    for offset, value in zip((384, 416, 448, 480, 512, 544), provenance):
        header[offset : offset + 32] = value
    header[576:608] = payload_sha256
    for index, (tensor_id, tensor_type, rank, flags, offset, size, dimensions) in enumerate(TENSOR_DIRECTORY):
        struct.pack_into("<HHHHQQIIII", header, 624 + index * 40, tensor_id, tensor_type, rank, flags, offset, size, *dimensions)
    struct.pack_into("<I", header, 608, crc32c(header))
    return bytes(header)


def _write_tensor(stream: Any, digest: Any, array: np.ndarray, expected_offset: int, expected_bytes: int) -> None:
    _require(stream.tell() == expected_offset, "EXPORT_TENSOR_OFFSET")
    payload = array.tobytes(order="C")
    _require(len(payload) == expected_bytes, "EXPORT_TENSOR_BYTES")
    stream.write(payload)
    digest.update(payload)


def _validate_dense_intervals(
    fc0_bias: np.ndarray,
    fc0_weight: np.ndarray,
    fc1_bias: np.ndarray,
    fc1_weight: np.ndarray,
    fc2_bias: np.ndarray,
    fc2_weight: np.ndarray,
) -> None:
    minimum = -(2**31)
    maximum = 2**31 - 1
    for biases, weights, code in (
        (fc0_bias, fc0_weight, "EXPORT_FC0_INTERVAL"),
        (fc1_bias, fc1_weight, "EXPORT_FC1_INTERVAL"),
    ):
        lower = biases.astype(np.int64) + 127 * np.minimum(weights.astype(np.int64), 0).sum(axis=2)
        upper = biases.astype(np.int64) + 127 * np.maximum(weights.astype(np.int64), 0).sum(axis=2)
        _require(bool((lower >= minimum).all() and (upper <= maximum).all()), code)
    lower = fc2_bias.astype(np.int64) + 127 * np.minimum(fc2_weight.astype(np.int64), 0).sum(axis=1)
    upper = fc2_bias.astype(np.int64) + 127 * np.maximum(fc2_weight.astype(np.int64), 0).sum(axis=1)
    _require(bool((lower - 65_535 >= minimum).all() and (upper + 65_535 <= maximum).all()), "EXPORT_FC2_INTERVAL")


def _reauth_export(path: Path) -> None:
    _require(path.stat().st_size == FILE_BYTES, "EXPORT_FILE_BYTES")
    with path.open("rb") as stream:
        header = stream.read(HEADER_BYTES)
        _require(len(header) == HEADER_BYTES and header[:16] == CONTAINER_MAGIC, "EXPORT_HEADER")
        header_copy = bytearray(header)
        observed_crc = struct.unpack_from("<I", header_copy, 608)[0]
        header_copy[608:612] = b"\0" * 4
        _require(crc32c(header_copy) == observed_crc, "EXPORT_HEADER_CRC32C")
        digest = hashlib.sha256()
        while block := stream.read(1024 * 1024):
            digest.update(block)
        _require(digest.digest() == header[576:608], "EXPORT_PAYLOAD_SHA256")


def export_checkpoint(args: argparse.Namespace) -> Mapping[str, Any]:
    _authenticate_static_contracts()
    document = _load_checkpoint(args.checkpoint, args.checkpoint_sha256)
    identity = document.get("identity")
    _require(isinstance(identity, dict), "EXPORT_IDENTITY")
    _require(identity.get("mode") == "production" and identity.get("training_admissible") is True, "EXPORT_FIXTURE_FORBIDDEN")
    diagnostic = identity.get("diagnostic_only") is True
    _require(
        identity.get("release_admissible") is (not diagnostic),
        "EXPORT_RELEASE_BOUNDARY",
    )
    _require(document.get("complete") is True, "EXPORT_INCOMPLETE")
    config_document = identity.get("configuration")
    _require(isinstance(config_document, dict), "EXPORT_CONFIGURATION")
    temporary_config = TrainingConfig(
        mode="production",
        device=config_document["device"],
        cpu_threads=config_document["cpu_threads"],
        score_scale_cp=float(config_document["score_scale_cp"]),
        lambda_=float(config_document["lambda"]),
        loss_exponent=float(config_document["loss_exponent"]),
        batch_size=config_document["batch_size"],
        epochs=config_document["epochs"],
        seed=config_document["seed"],
        sparse_learning_rate=float(config_document["sparse_learning_rate"]),
        dense_learning_rate=float(config_document["dense_learning_rate"]),
        validation_interval_steps=config_document["validation_interval_steps"],
        checkpoint_interval_steps=config_document["checkpoint_interval_steps"],
        sha256=identity["configuration_sha256"],
        document=config_document,
    )
    device = _configure_runtime(temporary_config)
    _require(_runtime_identity(device) == identity.get("runtime"), "EXPORT_RUNTIME_IDENTITY")
    model, sparse, dense = _initialize(temporary_config, device)
    model.load_state_dict(document["model_state"], strict=True)
    sparse.load_state_dict(document["sparse_optimizer_state"])
    dense.load_state_dict(document["dense_optimizer_state"])
    _require(not args.output.exists(), "EXPORT_OUTPUT_EXISTS")
    _require(args.output.parent.exists() and args.output.parent.is_dir(), "EXPORT_OUTPUT_PARENT")
    partial = args.output.with_name(args.output.name + ".partial")
    _require(not partial.exists(), "EXPORT_PARTIAL_EXISTS")
    provenance = (
        bytes.fromhex(identity["admission"]["source_manifest_sha256"]),
        bytes.fromhex(identity["admission"]["result_sha256"]),
        bytes.fromhex(identity["configuration_sha256"]),
        bytes.fromhex(identity["trainer_code_sha256"]),
        hashlib.sha256(_canonical_json(identity["runtime"])).digest(),
        document["resume_lineage"],
    )
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w+b", closefd=True) as stream:
            stream.write(bytes(HEADER_BYTES))
            digest = hashlib.sha256()
            k_weight = _quantized_numpy(model.k.weight, FEATURE_WEIGHT_SCALE, -32768, 32767, "<i2")
            k_bias = _quantized_numpy(model.k_bias, FEATURE_BIAS_SCALE, -32768, 32767, "<i2")
            g_weight = _quantized_numpy(model.g.weight, FEATURE_WEIGHT_SCALE, -32768, 32767, "<i2")
            g_bias = _quantized_numpy(model.g_bias, FEATURE_BIAS_SCALE, -32768, 32767, "<i2")
            fc0_bias = _quantized_numpy(model.fc0_bias, FC0_BIAS_SCALE, -(2**31), 2**31 - 1, "<i4")
            fc0_weight = _quantized_numpy(model.fc0_weight, FC0_WEIGHT_SCALE, -128, 127, "i1")
            fc1_bias = _quantized_numpy(model.fc1_bias, FC1_BIAS_SCALE, -(2**31), 2**31 - 1, "<i4")
            fc1_weight = _quantized_numpy(model.fc1_weight, FC1_WEIGHT_SCALE, -128, 127, "i1")
            fc2_bias = _quantized_numpy(model.fc2_bias, FC2_BIAS_SCALE, -(2**31), 2**31 - 1, "<i4")
            fc2_weight = _quantized_numpy(model.fc2_weight, FC2_WEIGHT_SCALE, -128, 127, "i1")
            _validate_dense_intervals(fc0_bias, fc0_weight, fc1_bias, fc1_weight, fc2_bias, fc2_weight)
            tensors = (
                (k_weight, TENSOR_DIRECTORY[0]),
                (k_bias, TENSOR_DIRECTORY[1]),
                (g_weight, TENSOR_DIRECTORY[2]),
                (g_bias, TENSOR_DIRECTORY[3]),
                (fc0_bias, TENSOR_DIRECTORY[4]),
                (fc0_weight, TENSOR_DIRECTORY[5]),
                (fc1_bias, TENSOR_DIRECTORY[6]),
                (fc1_weight, TENSOR_DIRECTORY[7]),
                (fc2_bias, TENSOR_DIRECTORY[8]),
                (fc2_weight, TENSOR_DIRECTORY[9]),
            )
            for array, directory in tensors:
                _write_tensor(stream, digest, array, directory[4], directory[5])
            _require(stream.tell() == FILE_BYTES, "EXPORT_FINAL_OFFSET")
            header = _container_header(digest.digest(), provenance)
            stream.seek(0)
            stream.write(header)
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
        "status": (
            "PASS_PRODUCTION_DIAGNOSTIC_EXPORT_REAUTHENTICATED"
            if diagnostic
            else "PASS_PRODUCTION_EXPORT_REAUTHENTICATED"
        ),
        "path": args.output.name,
        "bytes": args.output.stat().st_size,
        "sha256": _sha256_file(args.output),
        "training_admissible": True,
        "diagnostic_only": diagnostic,
        "release_admissible": not diagnostic,
        "model_selection_credit": False,
        "strength_credit": False,
        "legacy_v1_remains_default": True,
        "provenance": [item.hex() for item in provenance],
    }
    if args.receipt is not None:
        _require(not args.receipt.exists(), "EXPORT_RECEIPT_EXISTS")
        _write_atomic_replace(args.receipt, _canonical_json(result))
    return result


def meta_check() -> Mapping[str, Any]:
    _authenticate_static_contracts()
    model = LargeQatModel(PRODUCTION_SHAPE, torch.device("meta"), initialize=False)
    observed = sum(parameter.numel() for parameter in model.parameters())
    _require(observed == PRODUCTION_PARAMETER_COUNT, "META_PARAMETER_COUNT")
    _require(PRODUCTION_SHAPE.parameter_count == PRODUCTION_PARAMETER_COUNT, "META_SHAPE_PARAMETER_COUNT")
    expected_shapes = {
        "k.weight": (K_INPUTS, 768),
        "k_bias": (768,),
        "g.weight": (G_INPUTS, 256),
        "g_bias": (256,),
        "fc0_weight": (8, 32, 1024),
        "fc0_bias": (8, 32),
        "fc1_weight": (8, 32, 64),
        "fc1_bias": (8, 32),
        "fc2_weight": (8, 128),
        "fc2_bias": (8,),
    }
    observed_shapes = {name: tuple(parameter.shape) for name, parameter in model.named_parameters()}
    _require(observed_shapes == expected_shapes, "META_PARAMETER_SHAPES")
    return {
        "schema": "crazyhouse-nnue-v2-large-meta-check/v1",
        "status": "PASS",
        "parameter_count": observed,
        "production_shape": True,
        "allocated_production_storage": False,
        "training_admissible": False,
        "model_selection_credit": False,
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
        sys.stdout.buffer.write(_canonical_json(result))
        return 0
    except (TrainerError, OSError, ValueError, KeyError, TypeError, OverflowError, RuntimeError) as error:
        code = str(error) if isinstance(error, TrainerError) else "FAIL_CLOSED"
        sys.stderr.buffer.write(
            _canonical_json(
                {
                    "schema": "crazyhouse-nnue-v2-large-trainer-rejection/v1",
                    "status": "REJECTED",
                    "code": code,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
