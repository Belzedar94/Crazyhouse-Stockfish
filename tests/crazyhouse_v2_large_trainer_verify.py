#!/usr/bin/env python3
"""Independent fixture, resume, negative, and QAT parity suite for the large trainer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "tests" / "crazyhouse" / "p12-nnue-v2-large-production-trainer-v1.json"
PREREG_SHA256 = "377c478dbb1b76e04d92ca23e1fd800a3b88b1aabea0339bc302f64080a1819d"
CONFIG = ROOT / "tests" / "crazyhouse" / "nnue-v2-large-trainer-fixture-config-v1.json"
CONFIG_SHA256 = "33ccbf6e4163a34ed764932c698e46d54b70f36ebcdbb5e3caf35bd4e7640f0c"
CHECKPOINT_MAGIC = b"CHV2LCKPT1".ljust(16, b"\0")
CHECKPOINT_HEADER_BYTES = 56
ORDER_INITIAL = hashlib.sha256(
    b"Crazyhouse-Stockfish NNUE V2 large sample order v1\0"
).digest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or result.stderr:
        raise RuntimeError(f"git identity failed: {result.stderr!r}")
    return result.stdout.strip()


def run(command: Sequence[str], expected_code: str | None = None, timeout: int = 240) -> Mapping[str, Any]:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    if expected_code is None:
        if result.returncode != 0 or result.stderr:
            raise RuntimeError(
                f"command failed: code={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"non-JSON stdout: {result.stdout!r}") from error
        if canonical_json(document).decode("utf-8") != result.stdout:
            raise RuntimeError("stdout is not canonical JSON")
        return document
    if result.returncode != 2 or result.stdout:
        raise RuntimeError(
            f"negative framing failed for {expected_code}: code={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    try:
        document = json.loads(result.stderr)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"negative stderr is not JSON: {result.stderr!r}") from error
    if document.get("status") != "REJECTED" or document.get("code") != expected_code:
        raise RuntimeError(f"negative {expected_code} produced {document!r}")
    return document


def load_trainer(path: Path):
    spec = importlib.util.spec_from_file_location("crazyhouse_v2_large_trainer_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load trainer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_checkpoint(path: Path) -> Mapping[str, Any]:
    payload = path.read_bytes()
    if len(payload) < CHECKPOINT_HEADER_BYTES or payload[:16] != CHECKPOINT_MAGIC:
        raise RuntimeError("checkpoint independent framing")
    body_bytes = struct.unpack_from("<Q", payload, 16)[0]
    if len(payload) != CHECKPOINT_HEADER_BYTES + body_bytes:
        raise RuntimeError("checkpoint independent length")
    body = payload[CHECKPOINT_HEADER_BYTES:]
    if hashlib.sha256(body).digest() != payload[24:56]:
        raise RuntimeError("checkpoint independent digest")
    document = torch.load(io.BytesIO(body), map_location="cpu", weights_only=True)
    if not isinstance(document, dict):
        raise RuntimeError("checkpoint independent document")
    return document


def round_away(values: np.ndarray, scale: int) -> np.ndarray:
    scaled = values.astype(np.float64, copy=False) * scale
    return (np.sign(scaled) * np.floor(np.abs(scaled) + 0.5)).astype(np.int64)


def pair(values: np.ndarray) -> np.ndarray:
    half = values.size // 2
    return values[:half] * values[half:] // 512


def squared(values: np.ndarray, shift: int) -> np.ndarray:
    return np.minimum(127, values * values >> (2 * shift + 7))


def clipped(values: np.ndarray, shift: int) -> np.ndarray:
    return np.where(values <= 0, 0, np.minimum(127, values >> shift))


def trunc_div(value: int, divisor: int) -> int:
    return value // divisor if value >= 0 else -((-value) // divisor)


def independent_trace(state: Mapping[str, torch.Tensor], row: Mapping[str, Any], buckets: int) -> Mapping[str, Any]:
    k_weights = round_away(state["k.weight"].numpy(), 256)
    g_weights = round_away(state["g.weight"].numpy(), 256)
    k_bias = round_away(state["k_bias"].numpy(), 256)
    g_bias = round_away(state["g_bias"].numpy(), 256)

    def transform(rows: Sequence[int], weights: np.ndarray, bias: np.ndarray) -> np.ndarray:
        return np.clip(bias + weights[np.asarray(sorted(rows), dtype=np.int64)].sum(axis=0), 0, 255)

    stm_k = transform(row["stm_k64_rows"], k_weights, k_bias)
    stm_g = transform(row["stm_g1_rows"], g_weights, g_bias)
    opponent_k = transform(row["opponent_k64_rows"], k_weights, k_bias)
    opponent_g = transform(row["opponent_g1_rows"], g_weights, g_bias)
    stm = np.concatenate((pair(stm_k), pair(stm_g)))
    opponent = np.concatenate((pair(opponent_k), pair(opponent_g)))
    dense = np.concatenate((stm, opponent))
    bucket = min(buckets - 1, row["total_pocket_units"] // 4)

    fc0_weight = round_away(state["fc0_weight"][bucket].numpy(), 128)
    fc0_bias = round_away(state["fc0_bias"][bucket].numpy(), 16_384)
    fc0 = fc0_bias + fc0_weight @ dense
    fc0_squared = squared(fc0, 7)
    fc0_clipped = clipped(fc0, 7)
    fc1_input = np.concatenate((fc0_squared, fc0_clipped))
    fc1_weight = round_away(state["fc1_weight"][bucket].numpy(), 64)
    fc1_bias = round_away(state["fc1_bias"][bucket].numpy(), 8_192)
    fc1 = fc1_bias + fc1_weight @ fc1_input
    fc1_squared = squared(fc1, 6)
    fc1_clipped = clipped(fc1, 6)
    fc2_input = np.concatenate((fc0_squared, fc0_clipped, fc1_squared, fc1_clipped))
    fc2_weight = round_away(state["fc2_weight"][bucket].numpy(), 128)
    fc2_bias = int(round_away(state["fc2_bias"][bucket : bucket + 1].numpy(), 16_384)[0])
    fc2 = int(fc2_bias + fc2_weight @ fc2_input)
    fwd = fc2 + int(fc0[30]) - int(fc0[31])
    output_value = trunc_div(fwd * 9_600, 16_384)
    return {
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


def compare_trace(trainer: Any, checkpoint: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    model = trainer.LargeQatModel(trainer.FIXTURE_SHAPE, torch.device("cpu"))
    model.load_state_dict(checkpoint["model_state"], strict=True)
    sample = trainer.Sample(
        stm_k_rows=tuple(sorted(row["stm_k64_rows"])),
        stm_g_rows=tuple(sorted(row["stm_g1_rows"])),
        opponent_k_rows=tuple(sorted(row["opponent_k64_rows"])),
        opponent_g_rows=tuple(sorted(row["opponent_g1_rows"])),
        total_pocket_units=row["total_pocket_units"],
        target_probability=0.5,
        raw_record_key=row["raw_record_key"],
        model_input_key=row["large_model_input_key"],
    )
    with torch.no_grad():
        _centipawns, observed = model.forward_sample(sample, trace=True)
    expected = independent_trace(checkpoint["model_state"], row, trainer.FIXTURE_SHAPE.buckets)
    for key, expected_value in expected.items():
        observed_value = observed[key]
        if isinstance(observed_value, torch.Tensor):
            array = observed_value.detach().cpu().numpy()
            if np.isscalar(expected_value):
                if int(array) != int(expected_value):
                    raise RuntimeError(f"QAT trace mismatch at {key}")
            elif not np.array_equal(array.astype(np.int64), expected_value):
                raise RuntimeError(f"QAT trace mismatch at {key}")
        elif observed_value != expected_value:
            raise RuntimeError(f"QAT trace mismatch at {key}")


def feistel(value: int, bits: int, key: bytes) -> int:
    half_bits = bits // 2
    mask = (1 << half_bits) - 1
    left, right = value >> half_bits, value & mask
    for round_index in range(8):
        round_value = int.from_bytes(
            hashlib.sha256(key + bytes((round_index,)) + right.to_bytes((half_bits + 7) // 8, "little")).digest()[:8],
            "little",
        ) & mask
        left, right = right, left ^ round_value
    return (left << half_bits) | right


def independent_order(count: int, seed: int, epoch: int, dataset_identity: str) -> list[int]:
    bits = max(2, (count - 1).bit_length())
    if bits % 2:
        bits += 1
    key = hashlib.sha256(
        b"Crazyhouse-Stockfish NNUE V2 large Feistel v1\0"
        + seed.to_bytes(8, "little")
        + epoch.to_bytes(8, "little")
        + bytes.fromhex(dataset_identity)
    ).digest()
    output: list[int] = []
    for source in range(count):
        value = feistel(source, bits, key)
        while value >= count:
            value = feistel(value, bits, key)
        output.append(value)
    if sorted(output) != list(range(count)):
        raise RuntimeError("independent order is not a permutation")
    return output


def independent_order_chain(count: int, epochs: int, batch_size: int, seed: int, dataset_identity: str) -> str:
    chain = ORDER_INITIAL
    for epoch in range(epochs):
        order = independent_order(count, seed, epoch, dataset_identity)
        for batch, start in enumerate(range(0, count, batch_size)):
            indices = order[start : start + batch_size]
            payload = chain + struct.pack("<II", epoch, batch)
            payload += b"".join(struct.pack("<I", index) for index in indices)
            chain = hashlib.sha256(payload).digest()
    return chain.hex()


def trainer_common(
    python: Path,
    trainer_path: Path,
    admission: Path,
    source: Mapping[str, str],
) -> list[str]:
    return [
        str(python),
        "-B",
        str(trainer_path),
        "--admission-result",
        str(admission),
        "--admission-result-sha256",
        sha256_file(admission),
        "--config",
        str(CONFIG),
        "--config-sha256",
        CONFIG_SHA256,
        "--source-commit",
        source["commit"],
        "--source-tree",
        source["tree"],
        "--src-tree",
        source["src_tree"],
    ]


def mutate_model_key(admitted: Path, target: Path) -> Path:
    shutil.copytree(admitted, target)
    rows_path = target / "train.rows.jsonl"
    lines = rows_path.read_bytes().splitlines()
    row = json.loads(lines[0])
    row["large_model_input_key"] = "00" * 32
    lines[0] = canonical_json(row).rstrip(b"\n")
    rows_payload = b"\n".join(lines) + b"\n"
    rows_path.write_bytes(rows_payload)
    result_path = target / "admission-result.json"
    result = json.loads(result_path.read_bytes())
    result["roles"]["train"]["rows"]["bytes"] = len(rows_payload)
    result["roles"]["train"]["rows"]["sha256"] = hashlib.sha256(rows_payload).hexdigest()
    result_path.write_bytes(canonical_json(result))
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trainer", type=Path, required=True)
    parser.add_argument("--admission-loader", type=Path, required=True)
    args = parser.parse_args()
    trainer_path = args.trainer.resolve()
    admission_loader = args.admission_loader.resolve()
    python = Path(sys.executable).resolve()
    if sha256_file(PREREG) != PREREG_SHA256:
        raise RuntimeError("trainer preregistration pin mismatch")
    if sha256_file(CONFIG) != CONFIG_SHA256:
        raise RuntimeError("fixture configuration pin mismatch")
    trainer = load_trainer(trainer_path)
    meta = run([str(python), "-B", str(trainer_path), "meta-check"])
    if meta.get("parameter_count") != 63_342_088 or meta.get("allocated_production_storage") is not False:
        raise RuntimeError(f"meta check drifted: {meta!r}")
    container_reference = load_module(
        ROOT / "tools" / "nnue" / "crazyhouse_v2_large_container_reference.py",
        "crazyhouse_v2_large_container_reference_for_trainer",
    )
    reference_blob = container_reference.build_fixture_container()
    expected_header = bytes(reference_blob[: container_reference.HEADER_BYTES])
    observed_header = trainer._container_header(
        bytes(reference_blob[576:608]), container_reference.PROVENANCE
    )
    if observed_header != expected_header:
        raise RuntimeError("production exporter header parity failed")
    del reference_blob
    zeros0b = np.zeros((8, 32), dtype=np.int32)
    zeros0w = np.zeros((8, 32, 1024), dtype=np.int8)
    zeros1b = np.zeros((8, 32), dtype=np.int32)
    zeros1w = np.zeros((8, 32, 64), dtype=np.int8)
    zeros2b = np.zeros((8,), dtype=np.int32)
    zeros2w = np.zeros((8, 128), dtype=np.int8)
    trainer._validate_dense_intervals(zeros0b, zeros0w, zeros1b, zeros1w, zeros2b, zeros2w)
    overflow_bias = zeros0b.copy()
    overflow_weight = zeros0w.copy()
    overflow_bias[0, 0] = 2**31 - 1
    overflow_weight[0, 0, 0] = 1
    try:
        trainer._validate_dense_intervals(
            overflow_bias, overflow_weight, zeros1b, zeros1w, zeros2b, zeros2w
        )
    except trainer.TrainerError as error:
        if str(error) != "EXPORT_FC0_INTERVAL":
            raise RuntimeError(f"wrong dense interval rejection: {error}") from error
    else:
        raise RuntimeError("dense interval overflow was accepted")

    source = {
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "src_tree": git("rev-parse", "HEAD:src"),
    }
    with tempfile.TemporaryDirectory(prefix="crazyhouse-v2-large-trainer-") as temporary:
        root = Path(temporary)
        fixture = root / "fixture"
        admitted = root / "admitted"
        built = run([str(python), "-B", str(admission_loader), "build-fixture", "--output", str(fixture)])
        if built.get("record_count") != 44 or built.get("training_admissible") is not False:
            raise RuntimeError("fixture build drifted")
        admission = run(
            [
                str(python),
                "-B",
                str(admission_loader),
                "admit",
                "--manifest",
                str(fixture / "training-dataset-manifest.json"),
                "--output",
                str(admitted),
                "--mode",
                "fixture",
                "--projection",
                "large-k64g1-v1",
            ]
        )
        if admission.get("status") != "PASS_FIXTURE_NONADMISSIBLE" or admission.get("projection") != "large-k64g1-v1":
            raise RuntimeError("large admission drifted")
        admission_path = admitted / "admission-result.json"
        common = trainer_common(python, trainer_path, admission_path, source)

        uninterrupted = root / "uninterrupted"
        complete = run([*common[:3], "train", *common[3:], "--output-dir", str(uninterrupted)])
        if complete.get("status") != "PASS_FIXTURE_TRAINING_COMPLETE_NONADMISSIBLE":
            raise RuntimeError("uninterrupted fixture status drifted")

        interrupted = root / "interrupted"
        partial = run(
            [*common[:3], "train", *common[3:], "--output-dir", str(interrupted), "--stop-after-steps", "3"]
        )
        if partial.get("status") != "INTERRUPTED_FIXTURE_CHECKPOINT_NONADMISSIBLE":
            raise RuntimeError("interrupted fixture status drifted")
        resumed = root / "resumed"
        interrupted_checkpoint = interrupted / "checkpoint.chv2"
        resumed_result = run(
            [
                *common[:3],
                "resume",
                *common[3:],
                "--output-dir",
                str(resumed),
                "--checkpoint",
                str(interrupted_checkpoint),
                "--checkpoint-sha256",
                sha256_file(interrupted_checkpoint),
            ]
        )
        for field in (
            "model_state_sha256",
            "sparse_optimizer_state_sha256",
            "dense_optimizer_state_sha256",
            "order_chain_sha256",
            "metric_chain_sha256",
            "metrics_sha256",
        ):
            if complete[field] != resumed_result[field]:
                raise RuntimeError(f"resume equality failed at {field}")
        if complete["resume_lineage_sha256"] == resumed_result["resume_lineage_sha256"]:
            raise RuntimeError("resume lineage did not record the interruption")

        checkpoint = load_checkpoint(uninterrupted / "checkpoint.chv2")
        first_row = json.loads((admitted / "train.rows.jsonl").read_text(encoding="utf-8").splitlines()[0])
        compare_trace(trainer, checkpoint, first_row)
        expected_chain = independent_order_chain(
            admission["roles"]["train"]["record_count"],
            2,
            7,
            2026082901,
            admission["roles"]["train"]["rows"]["sha256"],
        )
        if complete["order_chain_sha256"] != expected_chain:
            raise RuntimeError("independent order chain mismatch")

        run(
            [
                str(python),
                "-B",
                str(trainer_path),
                "export",
                "--checkpoint",
                str(uninterrupted / "checkpoint.chv2"),
                "--checkpoint-sha256",
                sha256_file(uninterrupted / "checkpoint.chv2"),
                "--output",
                str(root / "forbidden.nnue"),
            ],
            "EXPORT_FIXTURE_FORBIDDEN",
        )

        trailing = root / "trailing.chv2"
        write_new(trailing, interrupted_checkpoint.read_bytes() + b"x")
        run(
            [
                *common[:3],
                "resume",
                *common[3:],
                "--output-dir",
                str(root / "trailing-output"),
                "--checkpoint",
                str(trailing),
                "--checkpoint-sha256",
                sha256_file(trailing),
            ],
            "CHECKPOINT_FRAMING",
        )

        mutated_admission = mutate_model_key(admitted, root / "mutated-admitted")
        mutated_common = trainer_common(python, trainer_path, mutated_admission, source)
        run(
            [*mutated_common[:3], "train", *mutated_common[3:], "--output-dir", str(root / "mutated-output")],
            "ROW_MODEL_INPUT_KEY",
        )

        wrong_hash = common.copy()
        index = wrong_hash.index("--admission-result-sha256") + 1
        wrong_hash[index] = "00" * 32
        run(
            [*wrong_hash[:3], "train", *wrong_hash[3:], "--output-dir", str(root / "wrong-hash-output")],
            "ADMISSION_RESULT_SHA256",
        )

    print(
        json.dumps(
            {
                "schema": "crazyhouse-nnue-v2-large-trainer-verification/v1",
                "status": "PASS",
                "fixture_records": 44,
                "training_steps": 10,
                "resume_equalities": 6,
                "qat_trace_fields": 17,
                "negative_cases": 4,
                "export_header_parity": True,
                "dense_interval_negative": True,
                "production_parameter_count": 63_342_088,
                "training_admissible": False,
                "model_selection_credit": False,
                "strength_credit": False,
                "legacy_v1_remains_default": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
