#!/usr/bin/env python3
"""Materialize the frozen same-data legacy-V1 versus large-V2 A0 campaign."""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
BASE_CAMPAIGN = ROOT / "tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.json"
BASE_CAMPAIGN_SHA256 = "1636986a62d08d73e295f354659cfe5bdcccba0ea4133ea08793c9d02df567ce"
ADDENDUM_2 = ROOT / "tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.addendum.002.json"
ADDENDUM_2_SHA256 = "8c9dd55c22664481ad18cb4cb8d38443ecfee81d80368ac56cd257e83005372c"
ADDENDUM_3 = ROOT / "tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.addendum.003.json"
ADDENDUM_3_SHA256 = "a170bd0c3e6919c9fa3c536707c6ca3542ad9168b33853fa80e6d1414e2a8353"
LEGACY_TRAINER = ROOT / "tools/nnue/crazyhouse_legacy_v1_trainer.py"
V2_TRAINER = ROOT / "tools/nnue/crazyhouse_v2_large_trainer.py"
CONFIG_SCHEMA = "crazyhouse-nnue-v2-large-training-config/v1"
MANIFEST_SCHEMA = "crazyhouse-a0-paired-diagnostic-training-materialization/v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CampaignError(RuntimeError):
    """Stable fail-closed paired-campaign error."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CampaignError(code)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def pairs_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"JSON_DUPLICATE_KEY:{key}")
        result[key] = value
    return result


def strict_json(payload: bytes, code: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CampaignError(code) from error


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def read_regular(path: Path, code: str, maximum_bytes: int) -> bytes:
    require(path.is_file() and not path.is_symlink(), code)
    size = path.stat().st_size
    require(0 < size <= maximum_bytes, f"{code}_SIZE")
    return path.read_bytes()


def validate_hex(value: Any, pattern: re.Pattern[str], code: str) -> str:
    require(isinstance(value, str) and pattern.fullmatch(value) is not None, code)
    return value


def load_pinned_json(path: Path, digest: str, code: str) -> tuple[Mapping[str, Any], bytes]:
    validate_hex(digest, HEX64, f"{code}_SHA256_ARGUMENT")
    payload = read_regular(path, code, 2 * 1024 * 1024)
    require(sha256_bytes(payload) == digest, f"{code}_SHA256")
    document = strict_json(payload, f"{code}_JSON")
    require(isinstance(document, dict), f"{code}_DOCUMENT")
    return document, payload


def descriptor(path: Path, relative_to: Path | None = None) -> Mapping[str, Any]:
    payload = path.read_bytes()
    rendered = path.name if relative_to is None else path.relative_to(relative_to).as_posix()
    return {"bytes": len(payload), "path": rendered, "sha256": sha256_bytes(payload)}


def validate_static_chain(addendum_path: Path, addendum_sha256: str) -> tuple[Mapping[str, Any], bytes, Mapping[str, Any]]:
    base, base_payload = load_pinned_json(BASE_CAMPAIGN, BASE_CAMPAIGN_SHA256, "BASE_CAMPAIGN")
    addendum_2, _ = load_pinned_json(ADDENDUM_2, ADDENDUM_2_SHA256, "ADDENDUM_2")
    addendum, addendum_payload = load_pinned_json(addendum_path, addendum_sha256, "ADDENDUM_CURRENT")
    require(base.get("schema") == "crazyhouse-p13-nnue-v2-large-a0-production-campaign-preregistration/v1", "BASE_SCHEMA")
    require(addendum_2.get("addendum") == 2 and addendum_2.get("status") == "AUTHORIZED_DIAGNOSTIC_OVERLAP_EXCEPTION", "ADDENDUM_2_STATUS")
    number = addendum.get("addendum")
    require(number in (3, 4), "ADDENDUM_NUMBER")
    prefix = f"ADDENDUM_{number}"
    require(addendum.get("schema") == "crazyhouse-p13-nnue-v2-large-a0-production-campaign-addendum/v1", f"{prefix}_SCHEMA")
    if number == 3:
        require(addendum.get("status") == "FROZEN_DIAGNOSTIC_PAIRED_TRAINING_IMPLEMENTATION", "ADDENDUM_3_STATUS")
        predecessor = {
            "path": "tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.addendum.002.json",
            "sha256": ADDENDUM_2_SHA256,
        }
    else:
        prior, _ = load_pinned_json(ADDENDUM_3, ADDENDUM_3_SHA256, "ADDENDUM_3")
        require(prior.get("addendum") == 3 and prior.get("status") == "FROZEN_DIAGNOSTIC_PAIRED_TRAINING_IMPLEMENTATION", "ADDENDUM_3_STATUS")
        require(addendum.get("status") == "AUTHORIZED_DETERMINISTIC_CUDA_RUNTIME_REPAIR", "ADDENDUM_4_STATUS")
        predecessor = {
            "path": "tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.addendum.003.json",
            "sha256": ADDENDUM_3_SHA256,
        }
    require(addendum.get("predecessor") == predecessor, f"{prefix}_PREDECESSOR")
    tooling = addendum.get("tooling")
    require(isinstance(tooling, dict), f"{prefix}_TOOLING")
    expected = {
        "planner": Path(__file__).resolve(),
        "legacy_trainer": LEGACY_TRAINER,
        "v2_trainer": V2_TRAINER,
    }
    for key, path in expected.items():
        entry = tooling.get(key)
        require(isinstance(entry, dict), f"{prefix}_{key.upper()}")
        require(entry.get("path") == path.relative_to(ROOT).as_posix(), f"{prefix}_{key.upper()}_PATH")
        require(entry.get("sha256") == sha256_file(path), f"{prefix}_{key.upper()}_SHA256")
    require(addendum.get("release_admissible") is False, f"{prefix}_RELEASE")
    require(addendum.get("strength_testing_started") is False, f"{prefix}_STRENGTH")
    require(addendum.get("registered_legacy_remains_default") is True, f"{prefix}_DEFAULT")
    return addendum, addendum_payload, {"document": base, "payload": base_payload}


def seed_material(namespace: str, index: int) -> tuple[str, int]:
    digest = hashlib.sha256(f"{namespace}:seed:{index}".encode("ascii")).hexdigest()
    return digest, int.from_bytes(bytes.fromhex(digest)[:8], "big") & ((1 << 63) - 1)


def validate_seeds(base: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    block = base.get("paired_seeds")
    require(isinstance(block, dict), "SEEDS")
    namespace = block.get("namespace")
    values = block.get("values")
    require(isinstance(namespace, str) and namespace.isascii(), "SEED_NAMESPACE")
    require(isinstance(values, list) and len(values) == 3, "SEED_VALUES")
    require(block.get("predesignated_playing_seed_index") == 0, "PLAYING_SEED_INDEX")
    for index, entry in enumerate(values):
        digest, seed = seed_material(namespace, index)
        require(entry == {"index": index, "material_sha256": digest, "seed": seed}, "SEED_ENTRY")
    return values


def derive_score_scale(scores: Sequence[int], ln3_decimal: str) -> tuple[int, int, int]:
    require(scores and all(isinstance(value, int) and not isinstance(value, bool) for value in scores), "SCORE_INPUTS")
    ordered = sorted(abs(value) for value in scores)
    rank = (3 * len(ordered) + 3) // 4
    q75 = ordered[rank - 1]
    require(q75 > 0, "SCORE_Q75_ZERO")
    scale = int((Decimal(q75) / Decimal(ln3_decimal)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return q75, rank, scale


def materialization_intervals(records: int, batch_size: int) -> tuple[int, int, int]:
    require(records > 0 and batch_size > 0, "INTERVAL_INPUT")
    batches = (records + batch_size - 1) // batch_size
    return batches, (batches + 3) // 4, batches


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, check=False, capture_output=True, text=True, timeout=30
    )
    require(result.returncode == 0 and not result.stderr, "GIT_IDENTITY")
    return result.stdout.strip()


def validate_source(base: Mapping[str, Any]) -> Mapping[str, str]:
    frozen = base.get("frozen_source")
    require(isinstance(frozen, dict), "FROZEN_SOURCE")
    require(git_output("status", "--porcelain=v1") == "", "SOURCE_DIRTY")
    head = validate_hex(git_output("rev-parse", "HEAD"), HEX40, "SOURCE_HEAD")
    tree = validate_hex(git_output("rev-parse", "HEAD^{tree}"), HEX40, "SOURCE_TREE")
    src_tree = validate_hex(git_output("rev-parse", "HEAD:src"), HEX40, "SOURCE_SRC_TREE")
    for ancestor, code in (
        (frozen.get("commit"), "FROZEN_BASE_ANCESTRY"),
        (frozen.get("official_stockfish_ancestor"), "OFFICIAL_ANCESTRY"),
    ):
        validate_hex(ancestor, HEX40, code)
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, head],
            cwd=ROOT,
            check=False,
            capture_output=True,
            timeout=30,
        )
        require(result.returncode == 0, code)
    return {"commit": head, "tree": tree, "src_tree": src_tree}


def import_module(path: Path, name: str, expected_sha256: str) -> tuple[ModuleType, bytes]:
    payload = read_regular(path, name.upper(), 4 * 1024 * 1024)
    require(sha256_bytes(payload) == expected_sha256, f"{name.upper()}_SHA256")
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        require(spec is not None and spec.loader is not None, f"{name.upper()}_IMPORT")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module, payload
    finally:
        sys.path.pop(0)


def placeholder_config(module: ModuleType, recipe: Mapping[str, Any]) -> Any:
    document = {
        "batch_size": recipe["batch_size"],
        "checkpoint_interval_steps": 1,
        "cpu_threads": recipe["cpu_threads"],
        "dense_learning_rate": recipe["dense_learning_rate"],
        "device": recipe["device"],
        "epochs": recipe["epochs"],
        "lambda": recipe["lambda"],
        "loss_exponent": recipe["loss_exponent"],
        "mode": recipe["mode"],
        "schema": CONFIG_SCHEMA,
        "score_scale_cp": 100.0,
        "seed": 0,
        "sparse_learning_rate": recipe["sparse_learning_rate"],
        "validation_interval_steps": 1,
    }
    payload = canonical_json(document)
    return module.TrainingConfig(
        mode="production",
        device=document["device"],
        cpu_threads=document["cpu_threads"],
        score_scale_cp=100.0,
        lambda_=float(document["lambda"]),
        loss_exponent=float(document["loss_exponent"]),
        batch_size=document["batch_size"],
        epochs=document["epochs"],
        seed=0,
        sparse_learning_rate=float(document["sparse_learning_rate"]),
        dense_learning_rate=float(document["dense_learning_rate"]),
        validation_interval_steps=1,
        checkpoint_interval_steps=1,
        sha256=sha256_bytes(payload),
        document=document,
    )


def bound_call(module: ModuleType, function: Any, *arguments: Any) -> Any:
    try:
        return function(*arguments)
    except module.TrainerError as error:
        raise CampaignError(f"BOUND_TRAINER:{error}") from error


LABEL_KEYS = (
    "raw_record_key",
    "terminal_reason",
    "teacher_score_kind",
    "teacher_bound",
    "teacher_score_value",
    "result_side_to_move",
    "game_result_white",
    "side_to_move",
)


def label_stream(path: Path, module: ModuleType, expected_records: int, collect_scores: bool) -> tuple[str, list[int]]:
    digest = hashlib.sha256(b"Crazyhouse-Stockfish paired A0 label stream v1\0")
    scores: list[int] = []
    records = 0
    with path.open("rb") as stream:
        for line in stream:
            records += 1
            document = module._strict_json_bytes(line, "PAIRED_LABEL_ROW_JSON")
            require(all(key in document for key in LABEL_KEYS), "PAIRED_LABEL_KEYS")
            digest.update(canonical_json({key: document[key] for key in LABEL_KEYS}))
            if collect_scores and (
                document["terminal_reason"] == "ongoing"
                and document["teacher_score_kind"] == "centipawn"
                and document["teacher_bound"] == "exact"
            ):
                value = document["teacher_score_value"]
                require(isinstance(value, int) and not isinstance(value, bool), "SCORE_TYPE")
                scores.append(value)
    require(records == expected_records, "LABEL_RECORD_COUNT")
    return digest.hexdigest(), scores


def write_exclusive(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def self_test(addendum_path: Path, addendum_sha256: str) -> Mapping[str, Any]:
    addendum, _, base_wrapper = validate_static_chain(addendum_path, addendum_sha256)
    base = base_wrapper["document"]
    seeds = validate_seeds(base)
    q75, rank, scale = derive_score_scale(
        [100, -200, 300, -400], base["score_scale_cp_derivation"]["ln3_decimal"]
    )
    require((q75, rank, scale) == (300, 3, 273), "SCORE_SELF_TEST")
    require(materialization_intervals(1_048_577, 512) == (2049, 513, 2049), "INTERVAL_SELF_TEST")
    return {
        "schema": "crazyhouse-a0-paired-diagnostic-campaign-self-test/v1",
        "status": "PASS",
        "addendum_sha256": addendum_sha256,
        "seeds": [entry["seed"] for entry in seeds],
        "score_scale_cp": scale,
        "diagnostic_only": addendum["release_admissible"] is False,
        "training_started": False,
    }


def materialize(args: argparse.Namespace) -> Mapping[str, Any]:
    addendum, addendum_payload, base_wrapper = validate_static_chain(args.addendum, args.addendum_sha256)
    base = base_wrapper["document"]
    base_payload = base_wrapper["payload"]
    seeds = validate_seeds(base)
    recipe = base.get("frozen_recipe")
    require(isinstance(recipe, dict), "RECIPE")
    tooling = addendum["tooling"]
    legacy_module, legacy_payload = import_module(
        LEGACY_TRAINER, "crazyhouse_a0_paired_legacy_trainer", tooling["legacy_trainer"]["sha256"]
    )
    v2_module, v2_payload = import_module(
        V2_TRAINER, "crazyhouse_a0_paired_v2_trainer", tooling["v2_trainer"]["sha256"]
    )
    source = validate_source(base)
    for value, code in (
        (args.legacy_admission_sha256, "LEGACY_ADMISSION_SHA256_ARGUMENT"),
        (args.v2_admission_sha256, "V2_ADMISSION_SHA256_ARGUMENT"),
    ):
        validate_hex(value, HEX64, code)
    admission_pins = addendum.get("admissions")
    require(isinstance(admission_pins, dict), "ADDENDUM_ADMISSIONS")
    require(admission_pins["legacy_v1"]["sha256"] == args.legacy_admission_sha256, "LEGACY_ADMISSION_PIN")
    require(admission_pins["large_v2"]["sha256"] == args.v2_admission_sha256, "V2_ADMISSION_PIN")
    legacy_config = placeholder_config(legacy_module, recipe)
    v2_config = placeholder_config(v2_module, recipe)
    legacy_train, legacy_validation, legacy_inputs = bound_call(
        legacy_module,
        legacy_module._load_admission,
        args.legacy_admission,
        args.legacy_admission_sha256,
        legacy_config,
    )
    v2_train = v2_validation = None
    try:
        v2_train, v2_validation, v2_inputs = bound_call(
            v2_module,
            v2_module._load_admission,
            args.v2_admission,
            args.v2_admission_sha256,
            v2_config,
        )
        require(legacy_inputs.train_record_count == v2_inputs.train_record_count == 1_048_576, "TRAIN_COUNT")
        require(legacy_inputs.validation_record_count == v2_inputs.validation_record_count == 131_072, "VALIDATION_COUNT")
        require(legacy_inputs.train_raw_record_ordered_set_sha256 == v2_inputs.train_raw_record_ordered_set_sha256, "TRAIN_RAW_SET")
        require(legacy_inputs.validation_raw_record_ordered_set_sha256 == v2_inputs.validation_raw_record_ordered_set_sha256, "VALIDATION_RAW_SET")
        require(legacy_inputs.source_manifest_sha256 == v2_inputs.source_manifest_sha256, "SOURCE_MANIFEST")
        legacy_root = args.legacy_admission.resolve(strict=True).parent
        v2_root = args.v2_admission.resolve(strict=True).parent
        legacy_train_labels, scores = label_stream(
            legacy_root / "train.rows.jsonl", legacy_module.shared, legacy_inputs.train_record_count, True
        )
        v2_train_labels, _ = label_stream(
            v2_root / "train.rows.jsonl", v2_module, v2_inputs.train_record_count, False
        )
        legacy_validation_labels, _ = label_stream(
            legacy_root / "validation.rows.jsonl", legacy_module.shared, legacy_inputs.validation_record_count, False
        )
        v2_validation_labels, _ = label_stream(
            v2_root / "validation.rows.jsonl", v2_module, v2_inputs.validation_record_count, False
        )
        require(legacy_train_labels == v2_train_labels, "TRAIN_LABEL_STREAM")
        require(legacy_validation_labels == v2_validation_labels, "VALIDATION_LABEL_STREAM")
        minimum = base["dataset_admission"]["minimum_record_counts"]["train_ongoing_exact_centipawn"]
        require(len(scores) >= minimum, "SCORE_SAMPLE_MINIMUM")
        derivation = base["score_scale_cp_derivation"]
        q75, rank, score_scale = derive_score_scale(scores, derivation["ln3_decimal"])
        interval = derivation["allowed_derived_interval_inclusive"]
        require(interval[0] <= score_scale <= interval[1], "SCORE_INTERVAL")
        batches, validation_interval, checkpoint_interval = materialization_intervals(
            legacy_inputs.train_record_count, recipe["batch_size"]
        )
        parent = args.output.resolve(strict=False).parent
        require(parent.is_dir() and not parent.is_symlink(), "OUTPUT_PARENT")
        require(not args.output.exists() and not args.output.is_symlink(), "OUTPUT_EXISTS")
        partial = args.output.with_name(args.output.name + ".partial")
        require(not partial.exists() and not partial.is_symlink(), "OUTPUT_PARTIAL_EXISTS")
        partial.mkdir()
        configurations: list[Mapping[str, Any]] = []
        try:
            for seed in seeds:
                document = {
                    "batch_size": recipe["batch_size"],
                    "checkpoint_interval_steps": checkpoint_interval,
                    "cpu_threads": recipe["cpu_threads"],
                    "dense_learning_rate": recipe["dense_learning_rate"],
                    "device": recipe["device"],
                    "epochs": recipe["epochs"],
                    "lambda": recipe["lambda"],
                    "loss_exponent": recipe["loss_exponent"],
                    "mode": recipe["mode"],
                    "schema": CONFIG_SCHEMA,
                    "score_scale_cp": score_scale,
                    "seed": seed["seed"],
                    "sparse_learning_rate": recipe["sparse_learning_rate"],
                    "validation_interval_steps": validation_interval,
                }
                payload = canonical_json(document)
                path = partial / f"training-config-seed-{seed['index']}.json"
                write_exclusive(path, payload)
                bound_call(v2_module, v2_module._load_config, path, sha256_bytes(payload))
                bound_call(legacy_module.shared, legacy_module.shared._load_config, path, sha256_bytes(payload))
                configurations.append({
                    "index": seed["index"],
                    "material_sha256": seed["material_sha256"],
                    "seed": seed["seed"],
                    "training_config": descriptor(path),
                })
            planner_payload = Path(__file__).read_bytes()
            manifest = {
                "schema": MANIFEST_SCHEMA,
                "status": "PASS_DIAGNOSTIC_PAIRED_CONFIG_MATERIALIZATION",
                "evidence_class": "M2_MODEL_SELECTION_DIAGNOSTIC",
                "diagnostic_only": True,
                "release_admissible": False,
                "strength_testing_started": False,
                "registered_legacy_remains_default": True,
                "source": source,
                "campaign": {"bytes": len(base_payload), "sha256": BASE_CAMPAIGN_SHA256},
                "addendum": {"bytes": len(addendum_payload), "sha256": args.addendum_sha256},
                "common_sample_identity": {
                    "source_manifest_sha256": legacy_inputs.source_manifest_sha256,
                    "train_raw_record_ordered_set_sha256": legacy_inputs.train_raw_record_ordered_set_sha256,
                    "validation_raw_record_ordered_set_sha256": legacy_inputs.validation_raw_record_ordered_set_sha256,
                    "train_label_stream_sha256": legacy_train_labels,
                    "validation_label_stream_sha256": legacy_validation_labels,
                },
                "arms": {
                    "legacy_v1": {
                        "trainer_sha256": sha256_bytes(legacy_payload),
                        "training_contract_sha256": legacy_module.TRAINING_CONTRACT_SHA256,
                        "admission_result_sha256": args.legacy_admission_sha256,
                        "train_rows_sha256": legacy_inputs.train_rows_sha256,
                        "validation_rows_sha256": legacy_inputs.validation_rows_sha256,
                        "parameter_count": legacy_module.PRODUCTION_PARAMETER_COUNT,
                    },
                    "large_v2": {
                        "trainer_sha256": sha256_bytes(v2_payload),
                        "training_contract_sha256": v2_module.TRAINING_CONTRACT_SHA256,
                        "admission_result_sha256": args.v2_admission_sha256,
                        "train_rows_sha256": v2_inputs.train_rows_sha256,
                        "validation_rows_sha256": v2_inputs.validation_rows_sha256,
                        "parameter_count": v2_module.PRODUCTION_PARAMETER_COUNT,
                    },
                },
                "configurations": configurations,
                "predesignated_playing_seed_index": 0,
                "recipe": {
                    "batch_size": recipe["batch_size"],
                    "epochs": recipe["epochs"],
                    "cpu_threads": recipe["cpu_threads"],
                    "device": recipe["device"],
                    "sparse_learning_rate": recipe["sparse_learning_rate"],
                    "dense_learning_rate": recipe["dense_learning_rate"],
                    "lambda": recipe["lambda"],
                    "loss_exponent": recipe["loss_exponent"],
                    "train_batches_per_epoch": batches,
                    "validation_interval_steps": validation_interval,
                    "checkpoint_interval_steps": checkpoint_interval,
                },
                "score_scale_cp": {
                    "value": score_scale,
                    "eligible_train_rows": len(scores),
                    "q75_abs_cp": q75,
                    "nearest_rank_one_based": rank,
                    "source_role": "train",
                    "validation_rows_used": False,
                },
                "selection_boundary": {
                    "loss_selects_nothing": True,
                    "all_six_trainings_required": True,
                    "release_before_registered_best_legacy_win": "forbidden",
                },
                "tooling": {
                    "planner_sha256": sha256_bytes(planner_payload),
                    "legacy_trainer_sha256": sha256_bytes(legacy_payload),
                    "v2_trainer_sha256": sha256_bytes(v2_payload),
                },
                "training_started": False,
            }
            manifest_payload = canonical_json(manifest)
            write_exclusive(partial / "paired-campaign-materialization.json", manifest_payload)
            os.replace(partial, args.output)
        except BaseException:
            if partial.exists():
                import shutil

                shutil.rmtree(partial)
            raise
        return {
            "schema": "crazyhouse-a0-paired-diagnostic-materialization-result/v1",
            "status": "PASS_DIAGNOSTIC_PAIRED_CONFIG_MATERIALIZATION",
            "configurations": 3,
            "arms": 2,
            "training_processes": 6,
            "score_scale_cp": score_scale,
            "manifest_sha256": sha256_bytes(manifest_payload),
            "diagnostic_only": True,
            "release_admissible": False,
            "training_started": False,
        }
    finally:
        legacy_train.close()
        legacy_validation.close()
        if v2_train is not None:
            v2_train.close()
        if v2_validation is not None:
            v2_validation.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("self-test", "materialize"):
        command = subparsers.add_parser(name)
        command.add_argument("--addendum", type=Path, default=ADDENDUM_3)
        command.add_argument("--addendum-sha256", required=True)
        if name == "materialize":
            command.add_argument("--legacy-admission", type=Path, required=True)
            command.add_argument("--legacy-admission-sha256", required=True)
            command.add_argument("--v2-admission", type=Path, required=True)
            command.add_argument("--v2-admission-sha256", required=True)
            command.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = (
            self_test(args.addendum, args.addendum_sha256)
            if args.command == "self-test"
            else materialize(args)
        )
    except CampaignError as error:
        sys.stderr.buffer.write(canonical_json({
            "schema": "crazyhouse-a0-paired-diagnostic-campaign-rejection/v1",
            "status": "REJECTED",
            "code": str(error),
        }))
        return 2
    except Exception as error:
        sys.stderr.buffer.write(canonical_json({
            "schema": "crazyhouse-a0-paired-diagnostic-campaign-rejection/v1",
            "status": "REJECTED",
            "code": f"UNEXPECTED_{type(error).__name__.upper()}",
        }))
        return 3
    sys.stdout.buffer.write(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
