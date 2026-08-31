#!/usr/bin/env python3
"""Independent projection, QAT, resume, and boundary suite for the legacy trainer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
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
CONFIG = ROOT / "tests" / "crazyhouse" / "nnue-v2-large-trainer-fixture-config-v1.json"
CONFIG_SHA256 = "33ccbf6e4163a34ed764932c698e46d54b70f36ebcdbb5e3caf35bd4e7640f0c"
CONTRACT = ROOT / "schemas" / "crazyhouse-nnue-legacy-v1-diagnostic-training-v1.json"
CONTRACT_SHA256 = "f679b50152aff593b2d7fb1af70de5750309680fefbd0f615a47a63bb696831e"
CHECKPOINT_MAGIC = b"CHLEGCKPT1".ljust(16, b"\0")
CHECKPOINT_HEADER_BYTES = 56
ORDER_INITIAL = hashlib.sha256(
    b"Crazyhouse-Stockfish NNUE V2 large sample order v1\0"
).digest()
NONPAWN_VALUES = (781, 825, 1276, 2538)
POCKET_BANDS = ((0, 17), (34, 5), (44, 5), (54, 5), (64, 3))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


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


def parse_stderr_events(stderr: str) -> list[Mapping[str, Any]]:
    events: list[Mapping[str, Any]] = []
    for line in stderr.splitlines():
        try:
            document = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"non-JSON stderr event: {line!r}") from error
        if not isinstance(document, dict):
            raise RuntimeError(f"stderr event is not an object: {document!r}")
        events.append(document)
    return events


def run(
    command: Sequence[str], expected_code: str | None = None, timeout: int = 240
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    events = parse_stderr_events(result.stderr)
    if expected_code is None:
        if result.returncode != 0:
            raise RuntimeError(
                f"command failed: code={result.returncode} stdout={result.stdout!r} events={events!r}"
            )
        if any(event.get("status") == "REJECTED" for event in events):
            raise RuntimeError(f"successful command emitted rejection: {events!r}")
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"non-JSON stdout: {result.stdout!r}") from error
        if canonical_json(document).decode("utf-8") != result.stdout:
            raise RuntimeError("stdout is not canonical JSON")
        return document, events
    if result.returncode != 2 or result.stdout:
        raise RuntimeError(
            f"negative framing failed for {expected_code}: code={result.returncode} "
            f"stdout={result.stdout!r} events={events!r}"
        )
    if not events:
        raise RuntimeError(f"negative {expected_code} emitted no rejection")
    rejection = events[-1]
    if rejection.get("status") != "REJECTED" or rejection.get("code") != expected_code:
        raise RuntimeError(f"negative {expected_code} produced {events!r}")
    return rejection, events[:-1]


def load_trainer(path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("crazyhouse_legacy_v1_trainer_under_test", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load legacy trainer")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


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


def round_away(values: np.ndarray, scale: float) -> np.ndarray:
    scaled = values.astype(np.float32, copy=False) * np.float32(scale)
    widened = scaled.astype(np.float64, copy=False)
    return (np.sign(widened) * np.floor(np.abs(widened) + 0.5)).astype(np.int64)


def wrap(values: np.ndarray | int, bits: int) -> np.ndarray | int:
    modulus = 1 << bits
    half = 1 << (bits - 1)
    if isinstance(values, np.ndarray):
        return (values + half) % modulus - half
    return (values + half) % modulus - half


def trunc_div(value: int, divisor: int) -> int:
    return value // divisor if value >= 0 else -((-value) // divisor)


def independent_projection(
    physical_rows: Sequence[int],
) -> tuple[tuple[int, ...], int, int, tuple[int, ...], tuple[int, ...]]:
    board = [row for row in physical_rows if row < 768]
    own_kings = [row % 64 for row in board if row // 64 == 10]
    opponent_kings = [row % 64 for row in board if row // 64 == 11]
    if len(own_kings) != 1 or len(opponent_kings) != 1:
        raise RuntimeError("fixture king projection is invalid")
    king_base = own_kings[0] * 864
    legacy = [king_base + min(row // 64, 10) * 64 + row % 64 for row in board]
    counts: dict[tuple[int, int], int] = {}
    for row in physical_rows:
        if not 768 <= row < 838:
            continue
        raw = row - 768
        for piece_type, (base, width) in enumerate(POCKET_BANDS):
            if base <= raw < base + 2 * width:
                owner = (raw - base) // width
                counts[(piece_type, owner)] = (raw - base) % width
                break
        else:
            raise RuntimeError("fixture pocket projection is invalid")
    if len(counts) != 10:
        raise RuntimeError("fixture pocket bands are incomplete")
    for piece_type in range(5):
        for owner in range(2):
            band = 2 * piece_type + owner
            legacy.extend(
                king_base + 704 + band * 16 + slot
                for slot in range(counts[(piece_type, owner)])
            )
    planes = [row // 64 for row in board]
    own_nonpawns = tuple(planes.count(2 * piece_type) for piece_type in range(1, 5))
    opponent_nonpawns = tuple(planes.count(2 * piece_type + 1) for piece_type in range(1, 5))
    return (
        tuple(sorted(legacy)),
        len(board),
        sum(plane in (0, 1) for plane in planes),
        own_nonpawns,
        opponent_nonpawns,
    )


def independent_trace(
    state: Mapping[str, torch.Tensor], sample: Any, lanes: int, buckets: int
) -> Mapping[str, Any]:
    feature = state["feature.weight"].numpy()
    transformer_weight = round_away(feature[:, :lanes], 127)
    psqt_weight = round_away(feature[:, lanes:], 9_600)
    transformer_bias = round_away(state["transformer_bias"].numpy(), 127)

    def transform(rows: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
        selected = np.asarray(rows, dtype=np.int64)
        transformer = transformer_bias + transformer_weight[selected].sum(axis=0)
        transformer = np.clip(wrap(transformer, 16), 0, 127).astype(np.int64)
        psqt = wrap(psqt_weight[selected].sum(axis=0), 32)
        return transformer, np.asarray(psqt, dtype=np.int64)

    stm, stm_psqt = transform(sample.stm_rows)
    opponent, opponent_psqt = transform(sample.opponent_rows)
    bucket = min(buckets - 1, sample.layer_bucket)
    raw_psqt = trunc_div(int(wrap(int(stm_psqt[bucket]) - int(opponent_psqt[bucket]), 32)), 2)
    dense = np.concatenate((stm, opponent))
    fc0_weight = round_away(state["fc0_weight"][bucket].numpy(), 64)
    fc0_bias = round_away(state["fc0_bias"][bucket].numpy(), 8_128)
    fc0 = np.asarray(wrap(fc0_bias + fc0_weight @ dense, 32), dtype=np.int64)
    hidden0 = np.clip(np.where(fc0 > 0, fc0 // 64, 0), 0, 127).astype(np.int64)
    fc1_weight = round_away(state["fc1_weight"][bucket].numpy(), 64)
    fc1_bias = round_away(state["fc1_bias"][bucket].numpy(), 8_128)
    fc1 = np.asarray(wrap(fc1_bias + fc1_weight @ hidden0, 32), dtype=np.int64)
    hidden1 = np.clip(np.where(fc1 > 0, fc1 // 64, 0), 0, 127).astype(np.int64)
    fc2_weight = round_away(state["fc2_weight"][bucket].numpy(), 9_600 / 127)
    fc2_bias = int(round_away(state["fc2_bias"][bucket : bucket + 1].numpy(), 9_600)[0])
    positional = int(wrap(int(fc2_bias + fc2_weight @ hidden1), 32))
    own_material = sum(count * value for count, value in zip(sample.own_nonpawns, NONPAWN_VALUES))
    opponent_material = sum(
        count * value for count, value in zip(sample.opponent_nonpawns, NONPAWN_VALUES)
    )
    entertainment = 7 if abs(own_material - opponent_material) <= 44 else 0
    numerator = (128 - entertainment) * raw_psqt + (128 + entertainment) * positional
    adjusted = trunc_div(trunc_div(numerator, 128), 16)
    scale = 903 + 32 * sample.board_pawns + (32 * (own_material + opponent_material)) // 1024
    outer_pre_clamp = trunc_div(adjusted * scale, 1024)
    outer = min(31_507, max(-31_507, outer_pre_clamp))
    return {
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


def compare_trace(trainer: Any, checkpoint: Mapping[str, Any], sample: Any) -> None:
    model = trainer.LegacyQatModel(trainer.FIXTURE_SHAPE, torch.device("cpu"))
    model.load_state_dict(checkpoint["model_state"], strict=True)
    with torch.no_grad():
        _value, observed = model.forward_sample(sample, trace=True)
    expected = independent_trace(
        checkpoint["model_state"], sample, trainer.FIXTURE_SHAPE.transformer_lanes, trainer.FIXTURE_SHAPE.buckets
    )
    for key, expected_value in expected.items():
        observed_value = observed[key]
        if isinstance(observed_value, torch.Tensor):
            array = observed_value.detach().cpu().numpy()
            if np.isscalar(expected_value):
                if int(array) != int(expected_value):
                    raise RuntimeError(
                        f"legacy QAT trace mismatch at {key}: observed={int(array)} "
                        f"expected={int(expected_value)}"
                    )
            elif not np.array_equal(array.astype(np.int64), expected_value):
                detail = ""
                if key == "fc0":
                    bucket = min(trainer.FIXTURE_SHAPE.buckets - 1, sample.layer_bucket)
                    model_bias = trainer.shared._ste_round_away(
                        model.fc0_bias[bucket] * trainer.DENSE_BIAS_SCALE
                    ).detach().cpu().numpy().astype(np.int64)
                    state_bias = round_away(
                        checkpoint["model_state"]["fc0_bias"][bucket].numpy(),
                        trainer.DENSE_BIAS_SCALE,
                    )
                    model_weight = trainer.shared._ste_round_away(
                        model.fc0_weight[bucket] * trainer.DENSE_WEIGHT_SCALE
                    ).detach().cpu().numpy().astype(np.int64)
                    state_weight = round_away(
                        checkpoint["model_state"]["fc0_weight"][bucket].numpy(),
                        trainer.DENSE_WEIGHT_SCALE,
                    )
                    dense = np.concatenate(
                        (
                            observed["stm"].detach().cpu().numpy().astype(np.int64),
                            observed["opponent"].detach().cpu().numpy().astype(np.int64),
                        )
                    )
                    detail = (
                        f" model_bias={model_bias.tolist()} "
                        f"state_bias={state_bias.tolist()} "
                        f"weight_equal={np.array_equal(model_weight, state_weight)} "
                        f"model_integer_dot={(model_weight @ dense + model_bias).tolist()}"
                    )
                raise RuntimeError(
                    f"legacy QAT trace mismatch at {key}: "
                    f"observed={array.astype(np.int64).tolist()} "
                    f"expected={np.asarray(expected_value).tolist()}{detail}"
                )
        elif int(observed_value) != int(expected_value):
            raise RuntimeError(f"legacy QAT trace mismatch at {key}")


def feistel(value: int, bits: int, key: bytes) -> int:
    half_bits = bits // 2
    mask = (1 << half_bits) - 1
    left, right = value >> half_bits, value & mask
    for round_index in range(8):
        round_value = int.from_bytes(
            hashlib.sha256(
                key + bytes((round_index,)) + right.to_bytes((half_bits + 7) // 8, "little")
            ).digest()[:8],
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


def independent_order_chain(
    count: int, epochs: int, batch_size: int, seed: int, dataset_identity: str
) -> str:
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
    python: Path, trainer_path: Path, admission: Path, source: Mapping[str, str]
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
    row["model_input_key"] = "00" * 32
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
    if sha256_file(CONFIG) != CONFIG_SHA256:
        raise RuntimeError("fixture configuration pin mismatch")
    if sha256_file(CONTRACT) != CONTRACT_SHA256:
        raise RuntimeError("legacy training contract pin mismatch")
    trainer = load_trainer(trainer_path)
    meta, meta_events = run([str(python), "-B", str(trainer_path), "meta-check"])
    if meta_events:
        raise RuntimeError(f"meta-check emitted events: {meta_events!r}")
    if meta.get("parameter_count") != 28_890_248 or meta.get("file_bytes") != 58_534_811:
        raise RuntimeError(f"legacy meta-check drifted: {meta!r}")

    source = {
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "src_tree": git("rev-parse", "HEAD:src"),
    }
    with tempfile.TemporaryDirectory(prefix="crazyhouse-legacy-v1-trainer-") as temporary:
        root = Path(temporary)
        fixture = root / "fixture"
        admitted = root / "admitted"
        built, _ = run(
            [str(python), "-B", str(admission_loader), "build-fixture", "--output", str(fixture)]
        )
        if built.get("record_count") != 44 or built.get("training_admissible") is not False:
            raise RuntimeError("fixture build drifted")
        admission, _ = run(
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
                "legacy-v1",
            ]
        )
        if admission.get("status") != "PASS_FIXTURE_NONADMISSIBLE" or "projection" in admission:
            raise RuntimeError("legacy admission drifted")
        admission_path = admitted / "admission-result.json"
        common = trainer_common(python, trainer_path, admission_path, source)

        uninterrupted = root / "uninterrupted"
        complete, complete_events = run(
            [*common[:3], "train", *common[3:], "--output-dir", str(uninterrupted)]
        )
        if complete.get("status") != "PASS_FIXTURE_TRAINING_COMPLETE_NONADMISSIBLE":
            raise RuntimeError("uninterrupted legacy fixture status drifted")
        kinds = [event.get("kind") for event in complete_events]
        if kinds[0] != "start" or kinds[-1] != "terminal" or kinds.count("epoch_complete") != 2:
            raise RuntimeError(f"legacy training events drifted: {complete_events!r}")

        interrupted = root / "interrupted"
        partial, partial_events = run(
            [
                *common[:3],
                "train",
                *common[3:],
                "--output-dir",
                str(interrupted),
                "--stop-after-steps",
                "3",
            ]
        )
        if partial.get("status") != "INTERRUPTED_FIXTURE_CHECKPOINT_NONADMISSIBLE":
            raise RuntimeError("interrupted legacy fixture status drifted")
        if partial_events[-1].get("kind") != "interrupted":
            raise RuntimeError("interrupted legacy event missing")
        resumed = root / "resumed"
        interrupted_checkpoint = interrupted / "checkpoint.chleg"
        resumed_result, resumed_events = run(
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
        if resumed_events[-1].get("kind") != "terminal":
            raise RuntimeError("resumed legacy terminal event missing")
        for field in (
            "model_state_sha256",
            "sparse_optimizer_state_sha256",
            "dense_optimizer_state_sha256",
            "order_chain_sha256",
            "metric_chain_sha256",
            "metrics_sha256",
        ):
            if complete[field] != resumed_result[field]:
                raise RuntimeError(f"legacy resume equality failed at {field}")
        if complete["resume_lineage_sha256"] == resumed_result["resume_lineage_sha256"]:
            raise RuntimeError("legacy resume lineage did not record the interruption")

        checkpoint = load_checkpoint(uninterrupted / "checkpoint.chleg")
        runtime = checkpoint["identity"]["runtime"]
        if runtime.get("cublas_workspace_config") != trainer.CUBLAS_WORKSPACE_CONFIG:
            raise RuntimeError("legacy CuBLAS deterministic workspace identity drifted")
        first_row = json.loads(
            (admitted / "train.rows.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        config = trainer.shared._load_config(CONFIG, CONFIG_SHA256)
        sample = trainer._validate_row(first_row, "train", config)
        projected = independent_projection(first_row["stm_rows"])
        if (
            sample.stm_rows,
            sample.board_piece_count,
            sample.board_pawns,
            sample.own_nonpawns,
            sample.opponent_nonpawns,
        ) != projected:
            raise RuntimeError("independent physical-to-legacy projection mismatch")
        expected_opponent = independent_projection(first_row["opponent_rows"])[0]
        if sample.opponent_rows != expected_opponent:
            raise RuntimeError("independent opponent projection mismatch")
        compare_trace(trainer, checkpoint, sample)
        dataset = trainer.RowDataset(
            admitted / "train.rows.jsonl",
            admission["roles"]["train"]["rows"],
            "train",
            config,
            admission["roles"]["train"]["record_count"],
        )
        try:
            model = trainer.LegacyQatModel(trainer.FIXTURE_SHAPE, torch.device("cpu"))
            model.load_state_dict(checkpoint["model_state"], strict=True)
            samples = [dataset[index] for index in range(4)]
            scalar = model.probabilities(samples, config.score_scale_cp)
            vectorized = model.probabilities(
                dataset.batch(range(4), torch.device("cpu")),
                config.score_scale_cp,
            )
            torch.testing.assert_close(vectorized, scalar, rtol=0.0, atol=0.0)
        finally:
            dataset.close()
        expected_chain = independent_order_chain(
            admission["roles"]["train"]["record_count"],
            2,
            7,
            2026082901,
            admission["sets"]["train"]["raw_record_key"]["ordered_set_sha256"],
        )
        if complete["order_chain_sha256"] != expected_chain:
            raise RuntimeError("independent legacy order-chain mismatch")

        run(
            [
                str(python),
                "-B",
                str(trainer_path),
                "export",
                "--checkpoint",
                str(uninterrupted / "checkpoint.chleg"),
                "--checkpoint-sha256",
                sha256_file(uninterrupted / "checkpoint.chleg"),
                "--output",
                str(root / "forbidden.nnue"),
            ],
            "EXPORT_FIXTURE_FORBIDDEN",
        )

        trailing = root / "trailing.chleg"
        trailing.write_bytes(interrupted_checkpoint.read_bytes() + b"x")
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
                "schema": "crazyhouse-nnue-legacy-v1-trainer-verification/v1",
                "status": "PASS",
                "fixture_records": 44,
                "training_steps": 10,
                "resume_equalities": 6,
                "projection_perspectives": 2,
                "qat_trace_fields": 16,
                "negative_cases": 3,
                "vectorized_scalar_parity": True,
                "cublas_deterministic_workspace_pinned": True,
                "production_parameter_count": 28_890_248,
                "production_file_bytes": 58_534_811,
                "training_admissible": False,
                "diagnostic_only": True,
                "release_admissible": False,
                "registered_legacy_remains_default": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
