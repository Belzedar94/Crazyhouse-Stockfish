#!/usr/bin/env python3
"""Materialize the preregistered Crazyhouse NNUE V2 large A0 campaign."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_PATH = ROOT / "tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.json"
CAMPAIGN_SHA256 = "1636986a62d08d73e295f354659cfe5bdcccba0ea4133ea08793c9d02df567ce"
CAMPAIGN_SCHEMA = "crazyhouse-p13-nnue-v2-large-a0-production-campaign-preregistration/v1"
MATERIALIZATION_SCHEMA = "crazyhouse-nnue-v2-large-a0-campaign-materialization/v1"
CONFIG_SCHEMA = "crazyhouse-nnue-v2-large-training-config/v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CampaignError(RuntimeError):
    """Stable fail-closed campaign materialization error."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CampaignError(code)


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def pairs_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CampaignError(f"JSON_DUPLICATE_KEY:{key}")
        result[key] = value
    return result


def strict_json(payload: bytes, code: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CampaignError(code) from exc


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_regular(path: Path, code: str, maximum_bytes: int) -> bytes:
    require(path.is_file() and not path.is_symlink(), code)
    size = path.stat().st_size
    require(0 < size <= maximum_bytes, f"{code}_SIZE")
    return path.read_bytes()


def validate_hex(value: Any, pattern: re.Pattern[str], code: str) -> str:
    require(isinstance(value, str) and pattern.fullmatch(value) is not None, code)
    return value


def load_campaign(path: Path, expected_sha256: str) -> tuple[Mapping[str, Any], bytes]:
    validate_hex(expected_sha256, HEX64, "CAMPAIGN_SHA256_ARGUMENT")
    require(expected_sha256 == CAMPAIGN_SHA256, "CAMPAIGN_SHA256_NOT_PREREGISTERED")
    payload = read_regular(path, "CAMPAIGN", 1024 * 1024)
    require(sha256_bytes(payload) == expected_sha256, "CAMPAIGN_SHA256")
    document = strict_json(payload, "CAMPAIGN_JSON")
    require(isinstance(document, dict), "CAMPAIGN_DOCUMENT")
    require(document.get("schema") == CAMPAIGN_SCHEMA, "CAMPAIGN_SCHEMA")
    require(document.get("status") == "PREREGISTERED_DATASET_PENDING", "CAMPAIGN_STATUS")
    require(document.get("variant") == "crazyhouse", "CAMPAIGN_VARIANT")
    return document, payload


def seed_material(namespace: str, index: int) -> tuple[str, int]:
    require(isinstance(namespace, str) and namespace.isascii() and namespace, "SEED_NAMESPACE")
    require(isinstance(index, int) and not isinstance(index, bool) and index >= 0, "SEED_INDEX")
    payload = f"{namespace}:seed:{index}".encode("ascii")
    digest = hashlib.sha256(payload).hexdigest()
    seed = int.from_bytes(bytes.fromhex(digest)[:8], "big") & ((1 << 63) - 1)
    return digest, seed


def validate_seeds(campaign: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    block = campaign.get("paired_seeds")
    require(isinstance(block, dict), "PAIRED_SEEDS")
    namespace = block.get("namespace")
    values = block.get("values")
    require(isinstance(namespace, str), "SEED_NAMESPACE")
    require(isinstance(values, list) and len(values) == 3, "SEED_VALUES")
    require(block.get("predesignated_playing_seed_index") == 0, "PLAYING_SEED_INDEX")
    observed: list[Mapping[str, Any]] = []
    for expected_index, entry in enumerate(values):
        require(isinstance(entry, dict), "SEED_ENTRY")
        require(set(entry) == {"index", "material_sha256", "seed"}, "SEED_ENTRY_KEYS")
        require(entry["index"] == expected_index, "SEED_ENTRY_INDEX")
        digest, seed = seed_material(namespace, expected_index)
        require(entry["material_sha256"] == digest, "SEED_MATERIAL_SHA256")
        require(entry["seed"] == seed, "SEED_VALUE")
        observed.append(entry)
    return observed


def derive_score_scale(scores: Sequence[int], ln3_decimal: str) -> tuple[int, int, int]:
    require(len(scores) > 0, "SCORE_SCALE_EMPTY")
    require(all(isinstance(value, int) and not isinstance(value, bool) for value in scores), "SCORE_TYPE")
    ordered = sorted(abs(value) for value in scores)
    rank_one_based = (3 * len(ordered) + 3) // 4
    q75 = ordered[rank_one_based - 1]
    require(q75 > 0, "SCORE_Q75_ZERO")
    scale = int((Decimal(q75) / Decimal(ln3_decimal)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return q75, rank_one_based, scale


def materialization_intervals(train_records: int, batch_size: int) -> tuple[int, int, int]:
    require(train_records > 0 and batch_size > 0, "INTERVAL_INPUT")
    batches = (train_records + batch_size - 1) // batch_size
    validation = (batches + 3) // 4
    return batches, validation, batches


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    require(result.returncode == 0 and not result.stderr, "GIT_IDENTITY")
    return result.stdout.strip()


def validate_source(campaign: Mapping[str, Any]) -> Mapping[str, str]:
    source = campaign.get("frozen_source")
    require(isinstance(source, dict), "FROZEN_SOURCE")
    base = validate_hex(source.get("commit"), HEX40, "FROZEN_COMMIT")
    official = validate_hex(source.get("official_stockfish_ancestor"), HEX40, "OFFICIAL_COMMIT")
    require(git_output("status", "--porcelain=v1") == "", "SOURCE_DIRTY")
    head = validate_hex(git_output("rev-parse", "HEAD"), HEX40, "SOURCE_HEAD")
    tree = validate_hex(git_output("rev-parse", "HEAD^{tree}"), HEX40, "SOURCE_TREE")
    src_tree = validate_hex(git_output("rev-parse", "HEAD:src"), HEX40, "SOURCE_SRC_TREE")
    for ancestor, code in ((base, "FROZEN_BASE_ANCESTRY"), (official, "OFFICIAL_ANCESTRY")):
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, head],
            cwd=ROOT,
            check=False,
            capture_output=True,
            timeout=30,
        )
        require(result.returncode == 0, code)
    return {"commit": head, "tree": tree, "src_tree": src_tree}


def import_trainer(path: Path, expected_sha256: str) -> tuple[ModuleType, bytes]:
    payload = read_regular(path, "TRAINER", 2 * 1024 * 1024)
    require(sha256_bytes(payload) == expected_sha256, "TRAINER_SHA256")
    spec = importlib.util.spec_from_file_location("crazyhouse_v2_large_campaign_bound_trainer", path)
    require(spec is not None and spec.loader is not None, "TRAINER_IMPORT_SPEC")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, payload


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


def collect_score_scale_inputs(
    train_rows: Path,
    module: ModuleType,
    expected_records: int,
) -> list[int]:
    scores: list[int] = []
    records = 0
    with train_rows.open("rb") as stream:
        for line in stream:
            records += 1
            document = module._strict_json_bytes(line, "CAMPAIGN_TRAIN_ROW_JSON")
            if (
                document.get("terminal_reason") == "ongoing"
                and document.get("teacher_score_kind") == "centipawn"
                and document.get("teacher_bound") == "exact"
            ):
                value = document.get("teacher_score_value")
                require(isinstance(value, int) and not isinstance(value, bool), "SCORE_VALUE_TYPE")
                scores.append(value)
    require(records == expected_records, "TRAIN_RECORD_COUNT")
    return scores


def write_exclusive(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def descriptor(path: Path) -> Mapping[str, Any]:
    payload = path.read_bytes()
    return {"bytes": len(payload), "path": path.name, "sha256": sha256_bytes(payload)}


def materialize(
    campaign_path: Path,
    campaign_sha256: str,
    trainer_path: Path,
    admission_path: Path,
    admission_sha256: str,
    output: Path,
) -> Mapping[str, Any]:
    campaign, campaign_payload = load_campaign(campaign_path, campaign_sha256)
    seeds = validate_seeds(campaign)
    recipe = campaign.get("frozen_recipe")
    require(isinstance(recipe, dict), "RECIPE")
    expected_trainer = campaign.get("bound_implementation", {}).get("trainer", {}).get("sha256")
    validate_hex(expected_trainer, HEX64, "BOUND_TRAINER_SHA256")
    module, trainer_payload = import_trainer(trainer_path, expected_trainer)
    source = validate_source(campaign)
    config = placeholder_config(module, recipe)
    validate_hex(admission_sha256, HEX64, "ADMISSION_SHA256_ARGUMENT")
    train, validation, inputs = module._load_admission(admission_path, admission_sha256, config)
    try:
        minimums = campaign.get("dataset_admission", {}).get("minimum_record_counts")
        require(isinstance(minimums, dict), "DATASET_MINIMUMS")
        require(inputs.train_record_count >= minimums["train"], "TRAIN_MINIMUM")
        require(inputs.validation_record_count >= minimums["validation"], "VALIDATION_MINIMUM")
        scores = collect_score_scale_inputs(
            admission_path.resolve(strict=True).parent / "train.rows.jsonl",
            module,
            inputs.train_record_count,
        )
        require(len(scores) >= minimums["train_ongoing_exact_centipawn"], "SCORE_SAMPLE_MINIMUM")
        derivation = campaign.get("score_scale_cp_derivation")
        require(isinstance(derivation, dict), "SCORE_DERIVATION")
        q75, rank, score_scale = derive_score_scale(scores, derivation["ln3_decimal"])
        interval = derivation.get("allowed_derived_interval_inclusive")
        require(isinstance(interval, list) and len(interval) == 2, "SCORE_INTERVAL")
        require(interval[0] <= score_scale <= interval[1], "SCORE_SCALE_INTERVAL")
        batches, validation_interval, checkpoint_interval = materialization_intervals(
            inputs.train_record_count, recipe["batch_size"]
        )
        parent = output.resolve(strict=False).parent
        require(parent.is_dir() and not parent.is_symlink(), "OUTPUT_PARENT")
        require(not output.exists() and not output.is_symlink(), "OUTPUT_EXISTS")
        partial = output.with_name(output.name + ".partial")
        require(not partial.exists() and not partial.is_symlink(), "OUTPUT_PARTIAL_EXISTS")
        partial.mkdir()
        configurations: list[Mapping[str, Any]] = []
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
            module._load_config(path, sha256_bytes(payload))
            configurations.append(
                {
                    "index": seed["index"],
                    "material_sha256": seed["material_sha256"],
                    "seed": seed["seed"],
                    "training_config": descriptor(path),
                }
            )
        planner_payload = Path(__file__).read_bytes()
        manifest = {
            "admission": {
                "result_sha256": admission_sha256,
                "source_manifest_sha256": inputs.source_manifest_sha256,
                "status": inputs.status,
                "train_record_count": inputs.train_record_count,
                "train_rows_bytes": inputs.train_rows_bytes,
                "train_rows_sha256": inputs.train_rows_sha256,
                "training_admissible": inputs.training_admissible,
                "validation_record_count": inputs.validation_record_count,
                "validation_rows_bytes": inputs.validation_rows_bytes,
                "validation_rows_sha256": inputs.validation_rows_sha256,
            },
            "architecture_id": campaign["architecture"]["id"],
            "campaign": {"bytes": len(campaign_payload), "sha256": campaign_sha256},
            "configurations": configurations,
            "evidence_class": "M2_MODEL_SELECTION",
            "predesignated_playing_seed_index": 0,
            "recipe": {
                "batch_size": recipe["batch_size"],
                "checkpoint_interval_steps": checkpoint_interval,
                "dense_learning_rate": recipe["dense_learning_rate"],
                "epochs": recipe["epochs"],
                "lambda": recipe["lambda"],
                "loss_exponent": recipe["loss_exponent"],
                "sparse_learning_rate": recipe["sparse_learning_rate"],
                "train_batches_per_epoch": batches,
                "validation_interval_steps": validation_interval,
            },
            "runtime_boundary": {
                "cuda_available": bool(module.torch.cuda.is_available()),
                "numpy": module.np.__version__,
                "python": sys.version.split()[0],
                "torch": module.torch.__version__,
            },
            "schema": MATERIALIZATION_SCHEMA,
            "score_scale_cp": {
                "eligible_train_rows": len(scores),
                "nearest_rank_one_based": rank,
                "q75_abs_cp": q75,
                "value": score_scale,
                "validation_rows_used": False,
            },
            "source": source,
            "status": "PASS_PRODUCTION_CONFIG_MATERIALIZATION",
            "tooling": {
                "planner_sha256": sha256_bytes(planner_payload),
                "trainer_sha256": sha256_bytes(trainer_payload),
            },
            "training_started": False,
        }
        manifest_payload = canonical_json(manifest)
        write_exclusive(partial / "campaign-materialization.json", manifest_payload)
        os.replace(partial, output)
        return {
            "configurations": len(configurations),
            "manifest_bytes": len(manifest_payload),
            "manifest_sha256": sha256_bytes(manifest_payload),
            "schema": "crazyhouse-nnue-v2-large-a0-campaign-materialization-result/v1",
            "score_scale_cp": score_scale,
            "status": "PASS_PRODUCTION_CONFIG_MATERIALIZATION",
            "training_started": False,
        }
    finally:
        train.close()
        validation.close()


def self_test(campaign_path: Path, campaign_sha256: str, trainer_path: Path) -> Mapping[str, Any]:
    campaign, _ = load_campaign(campaign_path, campaign_sha256)
    seeds = validate_seeds(campaign)
    expected_trainer = campaign["bound_implementation"]["trainer"]["sha256"]
    trainer_payload = read_regular(trainer_path, "TRAINER", 2 * 1024 * 1024)
    require(sha256_bytes(trainer_payload) == expected_trainer, "TRAINER_SHA256")
    q75, rank, scale = derive_score_scale([100, -200, 300, -400], campaign["score_scale_cp_derivation"]["ln3_decimal"])
    require((q75, rank, scale) == (300, 3, 273), "SCORE_SCALE_SELF_TEST")
    require(materialization_intervals(1048577, 512) == (2049, 513, 2049), "INTERVAL_SELF_TEST")
    try:
        derive_score_scale([0, 0, 0, 0], campaign["score_scale_cp_derivation"]["ln3_decimal"])
    except CampaignError as exc:
        require(str(exc) == "SCORE_Q75_ZERO", "SCORE_NEGATIVE_SELF_TEST")
    else:
        raise CampaignError("SCORE_NEGATIVE_SELF_TEST")
    return {
        "negative_cases": 1,
        "schema": "crazyhouse-nnue-v2-large-a0-campaign-self-test/v1",
        "score_scale_cp": scale,
        "seeds": [entry["seed"] for entry in seeds],
        "status": "PASS_CAMPAIGN_SELF_TEST",
        "training_started": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("self-test", "materialize"):
        command = subparsers.add_parser(name)
        command.add_argument("--campaign", type=Path, default=CAMPAIGN_PATH)
        command.add_argument("--campaign-sha256", required=True)
        command.add_argument(
            "--trainer", type=Path, default=ROOT / "tools/nnue/crazyhouse_v2_large_trainer.py"
        )
        if name == "materialize":
            command.add_argument("--admission-result", required=True, type=Path)
            command.add_argument("--admission-sha256", required=True)
            command.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "self-test":
            result = self_test(args.campaign, args.campaign_sha256, args.trainer)
        else:
            result = materialize(
                args.campaign,
                args.campaign_sha256,
                args.trainer,
                args.admission_result,
                args.admission_sha256,
                args.output,
            )
    except CampaignError as exc:
        sys.stderr.buffer.write(
            canonical_json(
                {
                    "code": str(exc),
                    "schema": "crazyhouse-nnue-v2-large-a0-campaign-error/v1",
                    "status": "REJECTED",
                }
            )
        )
        return 2
    except Exception as exc:
        sys.stderr.buffer.write(
            canonical_json(
                {
                    "code": f"UNEXPECTED_{type(exc).__name__.upper()}",
                    "schema": "crazyhouse-nnue-v2-large-a0-campaign-error/v1",
                    "status": "REJECTED",
                }
            )
        )
        return 3
    sys.stdout.buffer.write(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
