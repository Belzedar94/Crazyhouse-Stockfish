#!/usr/bin/env python3
"""Deterministic CPU trainer kernel for the Crazyhouse NNUE V2 baseline.

Only the explicitly marked engineering micro-fit is currently admitted.  It
uses authenticated physical states and synthetic position-identity targets;
it never consumes move, result, terminal, teacher, or search labels.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import sys
from typing import Any, cast, Iterable, Sequence

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from crazyhouse_v2_productive_reference import (
    ARCHITECTURE_CONTRACT_SHA256,
    ExpectedProvenance,
    FEATURE_DIMENSIONS,
    FloatNetwork,
    ProductiveExportError,
    QUANTIZATION_CONTRACT_SHA256,
    decode_microfit_state,
    engineering_target,
    export_network_file,
    feature_rows,
)


SEED = 2026082412
EPOCHS = 3
BATCH_SIZE = 7
RECORD_COUNT = 42
FULL_STEPS = 18
INTERRUPTION_STEP = 7
ORDER_SEED = SEED ^ 0x435A5632
INITIAL_ORDER_CHAIN = hashlib.sha256(
    b"Crazyhouse-Stockfish NNUE V2 engineering sample order v1\0"
).digest()

OPTIMIZER_SPEC = {
    "name": "torch.optim.AdamW",
    "learning_rate": 0.001,
    "betas": [0.9, 0.999],
    "epsilon": 1e-8,
    "weight_decay": 0.0,
    "amsgrad": False,
    "foreach": False,
    "fused": False,
    "maximize": False,
    "capturable": False,
    "differentiable": False,
}

TRAINER_SPEC = {
    "schema": "crazyhouse-nnue-v2-engineering-trainer/v1",
    "seed": SEED,
    "order_seed": ORDER_SEED,
    "epochs": EPOCHS,
    "batch_size": BATCH_SIZE,
    "record_count": RECORD_COUNT,
    "full_steps": FULL_STEPS,
    "interruption_step": INTERRUPTION_STEP,
    "loss": "mean-squared-error-on-engineering-target",
    "labels_consumed": False,
    "device": "cpu",
    "cpu_threads": 1,
    "dataloader_workers": 0,
    "optimizer": OPTIMIZER_SPEC,
}

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class TrainerError(RuntimeError):
    """Stable fail-closed trainer error."""


@dataclass(frozen=True)
class SourceIdentity:
    commit: str
    tree: str
    src_tree: str


@dataclass(frozen=True)
class TrainingInputs:
    manifest_sha256: str
    training_config_sha256: str
    records_sha256: str
    record_sha256: tuple[str, ...]


@dataclass(frozen=True)
class Sample:
    white_rows: tuple[int, ...]
    black_rows: tuple[int, ...]
    side_to_move: int
    target: float
    position_identity: str


class ProductiveFloatModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.transformer_weights = nn.Parameter(torch.empty(902, 512, dtype=torch.float32))
        self.transformer_biases = nn.Parameter(torch.empty(512, dtype=torch.float32))
        self.dense0 = nn.Linear(1024, 32, bias=True, dtype=torch.float32)
        self.dense1 = nn.Linear(32, 32, bias=True, dtype=torch.float32)
        self.output = nn.Linear(32, 1, bias=True, dtype=torch.float32)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.uniform_(self.transformer_weights, -0.02, 0.02)
        nn.init.zeros_(self.transformer_biases)
        nn.init.uniform_(self.dense0.weight, -0.03, 0.03)
        nn.init.zeros_(self.dense0.bias)
        nn.init.uniform_(self.dense1.weight, -0.12, 0.12)
        nn.init.zeros_(self.dense1.bias)
        nn.init.uniform_(self.output.weight, -0.12, 0.12)
        nn.init.zeros_(self.output.bias)

    def _transform(self, rows: Sequence[int]) -> torch.Tensor:
        indices = torch.tensor(rows, dtype=torch.int64, device=self.transformer_weights.device)
        return torch.clamp(
            torch.index_select(self.transformer_weights, 0, indices).sum(dim=0)
            + self.transformer_biases,
            0.0,
            1.0,
        )

    def forward_sample(self, sample: Sample) -> torch.Tensor:
        white = self._transform(sample.white_rows)
        black = self._transform(sample.black_rows)
        joined = torch.cat((white, black) if sample.side_to_move == 0 else (black, white))
        hidden0 = torch.clamp(F.linear(joined, self.dense0.weight, self.dense0.bias), 0.0, 1.0)
        hidden1 = torch.clamp(F.linear(hidden0, self.dense1.weight, self.dense1.bias), 0.0, 1.0)
        return F.linear(hidden1, self.output.weight, self.output.bias).squeeze(0)

    def forward_batch(self, samples: Sequence[Sample]) -> torch.Tensor:
        return torch.stack([self.forward_sample(sample) for sample in samples])


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise TrainerError(code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _load_checkpoint(path: Path, expected_sha256: str) -> dict[str, Any]:
    _validate_hex(expected_sha256, HEX64, "CHECKPOINT_SHA256_ARGUMENT")
    _require(path.is_file(), "CHECKPOINT_MISSING")
    _require(_sha256_file(path) == expected_sha256, "CHECKPOINT_SHA256")
    try:
        document = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as error:
        raise TrainerError("CHECKPOINT_LOAD") from error
    _require(isinstance(document, dict), "CHECKPOINT_DOCUMENT")
    return document


def _validate_hex(value: str, pattern: re.Pattern[str], code: str) -> None:
    _require(bool(pattern.fullmatch(value)), code)


def _canonical_json(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_new_bytes(path: Path, payload: bytes) -> None:
    _require(not path.exists(), "OUTPUT_EXISTS")
    partial = path.with_name(path.name + ".partial")
    _require(not partial.exists(), "PARTIAL_EXISTS")
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


def _replace_bytes(path: Path, payload: bytes) -> None:
    partial = path.with_name(path.name + ".partial")
    _require(not partial.exists(), "PARTIAL_EXISTS")
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


def _save_checkpoint(path: Path, document: dict[str, Any]) -> None:
    partial = path.with_name(path.name + ".partial")
    _require(not partial.exists(), "CHECKPOINT_PARTIAL_EXISTS")
    with partial.open("xb") as stream:
        torch.save(document, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def configure_runtime() -> None:
    required_environment = {
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }
    for name, value in required_environment.items():
        _require(os.environ.get(name) == value, f"ENVIRONMENT_{name}")
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        _require(torch.get_num_interop_threads() == 1, "INTEROP_THREADS")
    torch.use_deterministic_algorithms(True)
    _require(torch.get_num_threads() == 1, "TORCH_THREADS")
    _require(torch.get_num_interop_threads() == 1, "TORCH_INTEROP_THREADS")
    _require(torch.are_deterministic_algorithms_enabled(), "TORCH_DETERMINISM")


def runtime_identity() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "torch": str(torch.__version__),
        "numpy": str(np.__version__),
        "device": "cpu",
        "torch_threads": torch.get_num_threads(),
        "torch_interop_threads": torch.get_num_interop_threads(),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
        "mkl_num_threads": os.environ.get("MKL_NUM_THREADS"),
        "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
    }


def validate_source(source: SourceIdentity) -> None:
    _validate_hex(source.commit, HEX40, "SOURCE_COMMIT")
    _validate_hex(source.tree, HEX40, "SOURCE_TREE")
    _validate_hex(source.src_tree, HEX40, "SOURCE_SRC_TREE")


def load_samples(
    records_path: Path,
    manifest_path: Path,
    training_config_path: Path,
    expected_manifest_sha256: str,
    expected_config_sha256: str,
) -> tuple[list[Sample], TrainingInputs]:
    _validate_hex(expected_manifest_sha256, HEX64, "MANIFEST_SHA256_ARGUMENT")
    _validate_hex(expected_config_sha256, HEX64, "CONFIG_SHA256_ARGUMENT")
    _require(_sha256_file(manifest_path) == expected_manifest_sha256, "MANIFEST_SHA256")
    _require(_sha256_file(training_config_path) == expected_config_sha256, "CONFIG_SHA256")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require(manifest.get("schema") == "crazyhouse-physical-v1-golden-manifest/v1", "MANIFEST_SCHEMA")
    record_hashes = tuple(manifest.get("expected", {}).get("record_sha256", ()))
    _require(len(record_hashes) == RECORD_COUNT, "MANIFEST_RECORD_COUNT")
    _require(all(HEX64.fullmatch(item) for item in record_hashes), "MANIFEST_RECORD_HASH")
    payload = records_path.read_bytes()
    _require(len(payload) == RECORD_COUNT * 256, "RECORD_STREAM_SIZE")
    records_sha256 = hashlib.sha256(payload).hexdigest()
    _require(records_sha256 == manifest["expected"]["payload_sha256"], "RECORD_STREAM_SHA256")

    samples: list[Sample] = []
    for index, expected_record_hash in enumerate(record_hashes):
        record = payload[index * 256 : (index + 1) * 256]
        state = decode_microfit_state(record, expected_record_hash)
        samples.append(
            Sample(
                white_rows=feature_rows(state, 0),
                black_rows=feature_rows(state, 1),
                side_to_move=state.side_to_move,
                target=engineering_target(state.position_identity_sha256),
                position_identity=state.position_identity_sha256.hex(),
            )
        )
    _require(len({sample.position_identity for sample in samples}) >= 16, "POSITION_COVERAGE")
    inputs = TrainingInputs(
        manifest_sha256=expected_manifest_sha256,
        training_config_sha256=expected_config_sha256,
        records_sha256=records_sha256,
        record_sha256=record_hashes,
    )
    return samples, inputs


def make_optimizer(model: ProductiveFloatModel) -> torch.optim.AdamW:
    return torch.optim.AdamW(
        model.parameters(),
        lr=0.001,
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


def initialize_training() -> tuple[ProductiveFloatModel, torch.optim.AdamW, torch.Generator]:
    random.seed(SEED)
    np.random.seed(SEED % (1 << 32))
    torch.manual_seed(SEED)
    model = ProductiveFloatModel()
    optimizer = make_optimizer(model)
    order_generator = torch.Generator(device="cpu")
    order_generator.manual_seed(ORDER_SEED)
    return model, optimizer, order_generator


def _python_rng_document() -> dict[str, Any]:
    version, state, gaussian = random.getstate()
    return {
        "version": version,
        "state": list(state),
        "gaussian_hex": None if gaussian is None else float(gaussian).hex(),
    }


def _numpy_rng_document() -> dict[str, Any]:
    numpy_state = cast(tuple[Any, Any, Any, Any, Any], np.random.get_state())
    generator = str(numpy_state[0])
    state = np.asarray(numpy_state[1], dtype=np.uint32)
    position = int(numpy_state[2])
    has_gauss = int(numpy_state[3])
    cached_gaussian = float(numpy_state[4])
    return {
        "generator": generator,
        "state": state.tolist(),
        "position": position,
        "has_gauss": has_gauss,
        "cached_gaussian_hex": float(cached_gaussian).hex(),
    }


def _restore_python_rng(document: dict[str, Any]) -> None:
    gaussian_hex = document["gaussian_hex"]
    gaussian = None if gaussian_hex is None else float.fromhex(gaussian_hex)
    random.setstate((document["version"], tuple(document["state"]), gaussian))


def _restore_numpy_rng(document: dict[str, Any]) -> None:
    np.random.set_state(
        (
            document["generator"],
            np.asarray(document["state"], dtype=np.uint32),
            document["position"],
            document["has_gauss"],
            float.fromhex(document["cached_gaussian_hex"]),
        )
    )


def _identity_document(
    source: SourceIdentity, inputs: TrainingInputs, environment: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": "crazyhouse-nnue-v2-engineering-run-identity/v1",
        "training_admissible": False,
        "labels_consumed": False,
        "source": asdict(source),
        "inputs": {
            **asdict(inputs),
            "record_sha256": list(inputs.record_sha256),
        },
        "contracts": {
            "architecture_sha256": ARCHITECTURE_CONTRACT_SHA256.hex(),
            "quantization_sha256": QUANTIZATION_CONTRACT_SHA256.hex(),
        },
        "environment": environment,
        "trainer": TRAINER_SPEC,
    }


def _sample_order_chain(previous: bytes, epoch: int, batch: int, indices: Sequence[int]) -> bytes:
    payload = previous + epoch.to_bytes(4, "little") + batch.to_bytes(4, "little")
    payload += b"".join(index.to_bytes(4, "little") for index in indices)
    return hashlib.sha256(payload).digest()


def _checkpoint_document(
    identity: dict[str, Any],
    model: ProductiveFloatModel,
    optimizer: torch.optim.AdamW,
    order_generator: torch.Generator,
    epoch: int,
    batch_index: int,
    global_step: int,
    current_order: list[int] | None,
    order_chain: bytes,
    metrics_lines: list[str],
) -> dict[str, Any]:
    return {
        "schema": "crazyhouse-nnue-v2-engineering-checkpoint/v1",
        "identity": identity,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "rng": {
            "python": _python_rng_document(),
            "numpy": _numpy_rng_document(),
            "torch": torch.get_rng_state(),
            "order": order_generator.get_state(),
        },
        "cursor": {
            "epoch": epoch,
            "batch_index": batch_index,
            "global_step": global_step,
            "current_order": current_order,
        },
        "sample_order_chain": order_chain.hex(),
        "metrics_lines": list(metrics_lines),
    }


def _validate_tensor_state(state: Any) -> None:
    expected_shapes = {
        "transformer_weights": (902, 512),
        "transformer_biases": (512,),
        "dense0.weight": (32, 1024),
        "dense0.bias": (32,),
        "dense1.weight": (32, 32),
        "dense1.bias": (32,),
        "output.weight": (1, 32),
        "output.bias": (1,),
    }
    _require(isinstance(state, dict) and set(state) == set(expected_shapes), "MODEL_STATE_KEYS")
    for name, shape in expected_shapes.items():
        tensor = state[name]
        _require(isinstance(tensor, torch.Tensor), "MODEL_STATE_TENSOR")
        _require(
            tuple(tensor.shape) == shape
            and tensor.dtype == torch.float32
            and tensor.device.type == "cpu",
            "MODEL_STATE_SHAPE",
        )
        _require(bool(torch.isfinite(tensor).all()), "MODEL_STATE_NONFINITE")


def _validate_rng_document(rng: Any) -> None:
    _require(isinstance(rng, dict) and set(rng) == {"python", "numpy", "torch", "order"}, "RNG_STATE")

    python_rng = rng["python"]
    _require(
        isinstance(python_rng, dict)
        and set(python_rng) == {"version", "state", "gaussian_hex"},
        "PYTHON_RNG",
    )
    _require(python_rng["version"] == 3, "PYTHON_RNG_VERSION")
    python_state = python_rng["state"]
    _require(
        isinstance(python_state, list)
        and len(python_state) == 625
        and all(type(value) is int and 0 <= value <= 0xFFFFFFFF for value in python_state),
        "PYTHON_RNG_STATE",
    )
    gaussian_hex = python_rng["gaussian_hex"]
    _require(gaussian_hex is None or isinstance(gaussian_hex, str), "PYTHON_RNG_GAUSSIAN")
    try:
        gaussian = None if gaussian_hex is None else float.fromhex(gaussian_hex)
        _require(gaussian is None or math.isfinite(gaussian), "PYTHON_RNG_GAUSSIAN")
        probe_python = random.Random()
        probe_python.setstate((3, tuple(python_state), gaussian))
    except (TypeError, ValueError, OverflowError) as error:
        raise TrainerError("PYTHON_RNG_STATE") from error

    numpy_rng = rng["numpy"]
    _require(
        isinstance(numpy_rng, dict)
        and set(numpy_rng)
        == {"generator", "state", "position", "has_gauss", "cached_gaussian_hex"},
        "NUMPY_RNG",
    )
    _require(numpy_rng["generator"] == "MT19937", "NUMPY_RNG_GENERATOR")
    numpy_state = numpy_rng["state"]
    _require(
        isinstance(numpy_state, list)
        and len(numpy_state) == 624
        and all(type(value) is int and 0 <= value <= 0xFFFFFFFF for value in numpy_state),
        "NUMPY_RNG_STATE",
    )
    _require(
        type(numpy_rng["position"]) is int and 0 <= numpy_rng["position"] <= 624,
        "NUMPY_RNG_POSITION",
    )
    _require(numpy_rng["has_gauss"] in {0, 1}, "NUMPY_RNG_HAS_GAUSS")
    _require(isinstance(numpy_rng["cached_gaussian_hex"], str), "NUMPY_RNG_GAUSSIAN")
    try:
        cached = float.fromhex(numpy_rng["cached_gaussian_hex"])
        _require(math.isfinite(cached), "NUMPY_RNG_GAUSSIAN")
        probe_numpy = np.random.RandomState()
        probe_numpy.set_state(
            (
                "MT19937",
                np.asarray(numpy_state, dtype=np.uint32),
                numpy_rng["position"],
                numpy_rng["has_gauss"],
                cached,
            )
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise TrainerError("NUMPY_RNG_STATE") from error

    current_torch_rng = torch.get_rng_state()
    current_order_rng = torch.Generator(device="cpu").get_state()
    for name, value, expected in (
        ("TORCH_RNG", rng["torch"], current_torch_rng),
        ("ORDER_RNG", rng["order"], current_order_rng),
    ):
        _require(
            isinstance(value, torch.Tensor)
            and value.dtype == torch.uint8
            and value.device.type == "cpu"
            and tuple(value.shape) == tuple(expected.shape),
            name,
        )
        try:
            probe_torch = torch.Generator(device="cpu")
            probe_torch.set_state(value)
        except RuntimeError as error:
            raise TrainerError(name) from error


def _validate_metrics(
    metrics: Any,
    step: int,
    cursor_epoch: int,
    cursor_batch: int,
    current_order: list[int] | None,
    stored_chain: str,
) -> None:
    _require(isinstance(metrics, list) and len(metrics) == step, "METRICS_COUNT")
    chain = INITIAL_ORDER_CHAIN
    epoch_orders: dict[int, list[int]] = {}
    metric_keys = {
        "batch",
        "epoch",
        "loss_float32_hex",
        "order",
        "order_chain",
        "step",
        "training_admissible",
    }
    for index, line in enumerate(metrics):
        _require(isinstance(line, str) and line.endswith("\n"), "METRICS_LINES")
        try:
            metric = json.loads(line)
        except json.JSONDecodeError as error:
            raise TrainerError("METRICS_JSON") from error
        _require(isinstance(metric, dict) and set(metric) == metric_keys, "METRICS_FIELDS")
        _require(_canonical_json(metric).decode("utf-8") == line, "METRICS_CANONICAL")
        expected_step = index + 1
        expected_epoch = index // (RECORD_COUNT // BATCH_SIZE)
        expected_batch = index % (RECORD_COUNT // BATCH_SIZE)
        _require(metric["step"] == expected_step, "METRICS_STEP")
        _require(metric["epoch"] == expected_epoch, "METRICS_EPOCH")
        _require(metric["batch"] == expected_batch, "METRICS_BATCH")
        _require(metric["training_admissible"] is False, "METRICS_BOUNDARY")
        order = metric["order"]
        _require(
            isinstance(order, list)
            and len(order) == BATCH_SIZE
            and len(set(order)) == BATCH_SIZE
            and all(type(value) is int and 0 <= value < RECORD_COUNT for value in order),
            "METRICS_ORDER",
        )
        epoch_orders.setdefault(expected_epoch, []).extend(order)
        chain = _sample_order_chain(chain, expected_epoch, expected_batch, order)
        _require(metric["order_chain"] == chain.hex(), "METRICS_ORDER_CHAIN")
        _require(isinstance(metric["loss_float32_hex"], str), "METRICS_LOSS")
        try:
            loss = float.fromhex(metric["loss_float32_hex"])
        except ValueError as error:
            raise TrainerError("METRICS_LOSS") from error
        _require(math.isfinite(loss) and loss >= 0.0, "METRICS_LOSS")
        _require(loss.hex() == metric["loss_float32_hex"], "METRICS_LOSS_CANONICAL")

    _require(chain.hex() == stored_chain, "ORDER_CHAIN")
    for epoch, order in epoch_orders.items():
        if len(order) == RECORD_COUNT:
            _require(sorted(order) == list(range(RECORD_COUNT)), "METRICS_EPOCH_ORDER")
        else:
            _require(epoch == cursor_epoch and len(order) == cursor_batch * BATCH_SIZE, "METRICS_EPOCH_PREFIX")
    if current_order is not None:
        prefix = epoch_orders.get(cursor_epoch, [])
        _require(current_order[: len(prefix)] == prefix, "CURSOR_ORDER_PREFIX")


def _validate_checkpoint(document: Any, identity: dict[str, Any]) -> None:
    _require(isinstance(document, dict), "CHECKPOINT_DOCUMENT")
    _require(
        set(document)
        == {
            "schema",
            "identity",
            "model_state",
            "optimizer_state",
            "rng",
            "cursor",
            "sample_order_chain",
            "metrics_lines",
        },
        "CHECKPOINT_KEYS",
    )
    _require(document.get("schema") == "crazyhouse-nnue-v2-engineering-checkpoint/v1", "CHECKPOINT_SCHEMA")
    _require(document.get("identity") == identity, "CHECKPOINT_IDENTITY")
    _validate_tensor_state(document.get("model_state"))

    cursor = document.get("cursor")
    _require(isinstance(cursor, dict), "CURSOR")
    _require(set(cursor) == {"epoch", "batch_index", "global_step", "current_order"}, "CURSOR_KEYS")
    epoch = cursor["epoch"]
    batch = cursor["batch_index"]
    step = cursor["global_step"]
    order = cursor["current_order"]
    _require(type(epoch) is int and 0 <= epoch <= EPOCHS, "CURSOR_EPOCH")
    _require(type(batch) is int and 0 <= batch <= RECORD_COUNT // BATCH_SIZE, "CURSOR_BATCH")
    _require(type(step) is int and 0 <= step <= FULL_STEPS, "CURSOR_STEP")
    _require(step == min(epoch * 6 + batch, FULL_STEPS), "CURSOR_RELATION")
    if epoch == EPOCHS:
        _require(order is None and batch == 0 and step == FULL_STEPS, "CURSOR_FINAL")
    else:
        _require(isinstance(order, list), "CURSOR_ORDER")
        _require(
            len(order) == RECORD_COUNT
            and all(type(value) is int for value in order)
            and sorted(order) == list(range(RECORD_COUNT)),
            "CURSOR_ORDER_PERMUTATION",
        )

    optimizer_state = document.get("optimizer_state")
    _require(isinstance(optimizer_state, dict), "OPTIMIZER_STATE")
    _require(set(optimizer_state) == {"state", "param_groups"}, "OPTIMIZER_STATE_KEYS")
    _require(len(optimizer_state["param_groups"]) == 1, "OPTIMIZER_PARAM_GROUPS")
    group = optimizer_state["param_groups"][0]
    expected_group = {
        "lr": 0.001,
        "betas": (0.9, 0.999),
        "eps": 1e-8,
        "weight_decay": 0.0,
        "amsgrad": False,
        "foreach": False,
        "maximize": False,
        "capturable": False,
        "differentiable": False,
        "fused": False,
        "decoupled_weight_decay": True,
    }
    _require(isinstance(group, dict), "OPTIMIZER_PARAM_GROUP")
    _require(set(group) == set(expected_group) | {"params"}, "OPTIMIZER_PARAM_GROUP_KEYS")
    _require(group.get("params") == list(range(8)), "OPTIMIZER_PARAMS")
    for name, expected in expected_group.items():
        _require(group.get(name) == expected, "OPTIMIZER_SPEC")

    optimizer_shapes = {
        0: (902, 512),
        1: (512,),
        2: (32, 1024),
        3: (32,),
        4: (32, 32),
        5: (32,),
        6: (1, 32),
        7: (1,),
    }
    states = optimizer_state["state"]
    _require(isinstance(states, dict) and set(states) == set(optimizer_shapes), "OPTIMIZER_STATE_PARAMS")
    for parameter, shape in optimizer_shapes.items():
        state = states[parameter]
        _require(isinstance(state, dict) and set(state) == {"step", "exp_avg", "exp_avg_sq"}, "OPTIMIZER_MOMENT_KEYS")
        step_tensor = state["step"]
        _require(
            isinstance(step_tensor, torch.Tensor)
            and step_tensor.dtype == torch.float32
            and step_tensor.device.type == "cpu"
            and tuple(step_tensor.shape) == ()
            and bool(torch.isfinite(step_tensor))
            and float(step_tensor.item()) == float(step),
            "OPTIMIZER_STEP",
        )
        for name in ("exp_avg", "exp_avg_sq"):
            tensor = state[name]
            _require(
                isinstance(tensor, torch.Tensor)
                and tensor.dtype == torch.float32
                and tensor.device.type == "cpu"
                and tuple(tensor.shape) == shape
                and bool(torch.isfinite(tensor).all()),
                "OPTIMIZER_MOMENT",
            )
        _require(bool((state["exp_avg_sq"] >= 0).all()), "OPTIMIZER_SECOND_MOMENT")

    _validate_rng_document(document.get("rng"))
    chain = document.get("sample_order_chain")
    _require(isinstance(chain, str) and bool(HEX64.fullmatch(chain)), "ORDER_CHAIN")
    _validate_metrics(document.get("metrics_lines"), step, epoch, batch, order, chain)


def _restore_checkpoint(
    document: dict[str, Any], model: ProductiveFloatModel, optimizer: torch.optim.AdamW, order: torch.Generator
) -> tuple[int, int, int, list[int] | None, bytes, list[str]]:
    model.load_state_dict(document["model_state"], strict=True)
    optimizer.load_state_dict(document["optimizer_state"])
    _restore_python_rng(document["rng"]["python"])
    _restore_numpy_rng(document["rng"]["numpy"])
    torch.set_rng_state(document["rng"]["torch"])
    order.set_state(document["rng"]["order"])
    cursor = document["cursor"]
    return (
        cursor["epoch"],
        cursor["batch_index"],
        cursor["global_step"],
        cursor["current_order"],
        bytes.fromhex(document["sample_order_chain"]),
        list(document["metrics_lines"]),
    )


def _float_network(model: ProductiveFloatModel, provenance: ExpectedProvenance) -> FloatNetwork:
    def values(tensor: torch.Tensor) -> list[float]:
        return tensor.detach().cpu().contiguous().view(-1).tolist()

    return FloatNetwork(
        transformer_weights=values(model.transformer_weights),
        transformer_biases=values(model.transformer_biases),
        dense0_weights=values(model.dense0.weight),
        dense0_biases=values(model.dense0.bias),
        dense1_weights=values(model.dense1.weight),
        dense1_biases=values(model.dense1.bias),
        output_weights=values(model.output.weight),
        output_bias=values(model.output.bias),
        provenance=provenance,
    )


def _run_loop(
    samples: Sequence[Sample],
    output_dir: Path,
    identity: dict[str, Any],
    model: ProductiveFloatModel,
    optimizer: torch.optim.AdamW,
    order_generator: torch.Generator,
    epoch: int,
    batch_index: int,
    global_step: int,
    current_order: list[int] | None,
    order_chain: bytes,
    metrics_lines: list[str],
    stop_after_steps: int | None,
) -> str:
    checkpoint_path = output_dir / "checkpoint.pt"
    metrics_path = output_dir / "metrics.jsonl"
    while epoch < EPOCHS:
        if current_order is None:
            current_order = torch.randperm(RECORD_COUNT, generator=order_generator).tolist()
            batch_index = 0
        while batch_index < RECORD_COUNT // BATCH_SIZE:
            indices = current_order[batch_index * BATCH_SIZE : (batch_index + 1) * BATCH_SIZE]
            batch = [samples[index] for index in indices]
            targets = torch.tensor([sample.target for sample in batch], dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            predictions = model.forward_batch(batch)
            loss = F.mse_loss(predictions, targets, reduction="mean")
            _require(bool(torch.isfinite(loss)), "NONFINITE_LOSS")
            loss.backward()
            optimizer.step()

            order_chain = _sample_order_chain(order_chain, epoch, batch_index, indices)
            global_step += 1
            metric = {
                "batch": batch_index,
                "epoch": epoch,
                "loss_float32_hex": float(loss.detach().cpu().item()).hex(),
                "order": indices,
                "order_chain": order_chain.hex(),
                "step": global_step,
                "training_admissible": False,
            }
            metrics_lines.append(_canonical_json(metric).decode("utf-8"))
            batch_index += 1
            _replace_bytes(metrics_path, "".join(metrics_lines).encode("utf-8"))
            _save_checkpoint(
                checkpoint_path,
                _checkpoint_document(
                    identity,
                    model,
                    optimizer,
                    order_generator,
                    epoch,
                    batch_index,
                    global_step,
                    current_order,
                    order_chain,
                    metrics_lines,
                ),
            )
            if stop_after_steps is not None and global_step == stop_after_steps:
                return "INTERRUPTED_ENGINEERING"
        epoch += 1
        batch_index = 0
        current_order = None

    _require(global_step == FULL_STEPS, "FINAL_STEP_COUNT")
    _save_checkpoint(
        checkpoint_path,
        _checkpoint_document(
            identity,
            model,
            optimizer,
            order_generator,
            EPOCHS,
            0,
            global_step,
            None,
            order_chain,
            metrics_lines,
        ),
    )
    provenance = ExpectedProvenance(
        bytes.fromhex(identity["inputs"]["manifest_sha256"]),
        bytes.fromhex(identity["inputs"]["training_config_sha256"]),
    )
    network_path = output_dir / "network.nnuev2"
    export_network_file(str(network_path), _float_network(model, provenance))
    summary = {
        "schema": "crazyhouse-nnue-v2-engineering-training-result/v1",
        "status": "PASS_ENGINEERING_MICROFIT",
        "steps": global_step,
        "records": RECORD_COUNT,
        "metrics_sha256": _sha256_file(metrics_path),
        "checkpoint_sha256": _sha256_file(checkpoint_path),
        "network_bytes": network_path.stat().st_size,
        "network_sha256": _sha256_file(network_path),
        "sample_order_chain": order_chain.hex(),
        "labels_consumed": False,
        "training_admissible": False,
        "model_selection_credit": False,
        "strength_credit": False,
    }
    _write_new_bytes(output_dir / "result.json", _canonical_json(summary))
    return "PASS_ENGINEERING_MICROFIT"


def fresh_train(
    samples: Sequence[Sample],
    inputs: TrainingInputs,
    source: SourceIdentity,
    output_dir: Path,
    stop_after_steps: int | None,
) -> str:
    _require(not output_dir.exists(), "OUTPUT_DIRECTORY_EXISTS")
    if stop_after_steps is not None:
        _require(stop_after_steps == INTERRUPTION_STEP, "STOP_STEP")
    environment = runtime_identity()
    identity = _identity_document(source, inputs, environment)
    model, optimizer, order_generator = initialize_training()
    output_dir.mkdir(parents=False, exist_ok=False)
    _write_new_bytes(output_dir / "run.json", _canonical_json(identity))
    _write_new_bytes(output_dir / "metrics.jsonl", b"")
    return _run_loop(
        samples,
        output_dir,
        identity,
        model,
        optimizer,
        order_generator,
        0,
        0,
        0,
        None,
        INITIAL_ORDER_CHAIN,
        [],
        stop_after_steps,
    )


def resume_train(
    samples: Sequence[Sample],
    inputs: TrainingInputs,
    source: SourceIdentity,
    output_dir: Path,
    checkpoint_path: Path,
    checkpoint_sha256: str,
) -> str:
    environment = runtime_identity()
    identity = _identity_document(source, inputs, environment)
    document = _load_checkpoint(checkpoint_path, checkpoint_sha256)
    _validate_checkpoint(document, identity)

    _require(output_dir.is_dir(), "RESUME_OUTPUT_MISSING")
    _require((output_dir / "run.json").read_bytes() == _canonical_json(identity), "RESUME_RUN_IDENTITY")
    expected_metrics = "".join(document["metrics_lines"]).encode("utf-8")
    _require((output_dir / "metrics.jsonl").read_bytes() == expected_metrics, "RESUME_METRICS")
    _require(not (output_dir / "network.nnuev2").exists(), "RESUME_NETWORK_EXISTS")
    _require(not (output_dir / "result.json").exists(), "RESUME_RESULT_EXISTS")

    model, optimizer, order_generator = initialize_training()
    epoch, batch, step, order, chain, metrics = _restore_checkpoint(
        document, model, optimizer, order_generator
    )
    return _run_loop(
        samples,
        output_dir,
        identity,
        model,
        optimizer,
        order_generator,
        epoch,
        batch,
        step,
        order,
        chain,
        metrics,
        None,
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("train", "resume"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--engineering-microfit", action="store_true")
        subparser.add_argument("--records", type=Path, required=True)
        subparser.add_argument("--manifest", type=Path, required=True)
        subparser.add_argument("--manifest-sha256", required=True)
        subparser.add_argument("--training-config", type=Path, required=True)
        subparser.add_argument("--training-config-sha256", required=True)
        subparser.add_argument("--source-commit", required=True)
        subparser.add_argument("--source-tree", required=True)
        subparser.add_argument("--src-tree", required=True)
        subparser.add_argument("--output-dir", type=Path, required=True)
    subparsers.choices["train"].add_argument("--stop-after-steps", type=int)
    subparsers.choices["resume"].add_argument("--checkpoint", type=Path, required=True)
    subparsers.choices["resume"].add_argument("--checkpoint-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _require(args.engineering_microfit, "ENGINEERING_MICROFIT_MARKER_REQUIRED")
        configure_runtime()
        source = SourceIdentity(args.source_commit, args.source_tree, args.src_tree)
        validate_source(source)
        samples, inputs = load_samples(
            args.records,
            args.manifest,
            args.training_config,
            args.manifest_sha256,
            args.training_config_sha256,
        )
        if args.command == "train":
            status = fresh_train(samples, inputs, source, args.output_dir, args.stop_after_steps)
        else:
            status = resume_train(
                samples,
                inputs,
                source,
                args.output_dir,
                args.checkpoint,
                args.checkpoint_sha256,
            )
        print(
            json.dumps(
                {
                    "schema": "crazyhouse-nnue-v2-engineering-trainer-status/v1",
                    "status": status,
                    "training_admissible": False,
                    "model_selection_credit": False,
                    "strength_credit": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (TrainerError, ProductiveExportError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL crazyhouse_v2_train: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
