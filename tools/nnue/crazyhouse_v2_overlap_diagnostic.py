#!/usr/bin/env python3
"""Quantify cross-role contamination in a partial Crazyhouse materialization."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import struct
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
ADMISSION_PATH = ROOT / "tools/nnue/crazyhouse_v2_training_admission.py"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


admission = load_module("crazyhouse_v2_overlap_admission", ADMISSION_PATH)
codec = admission.codec


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def model_keys(record: Any, large_reference: Any) -> tuple[bytes, bytes]:
    stm = admission.feature_rows(record, record.side_to_move)
    opponent = admission.feature_rows(record, record.side_to_move ^ 1)
    legacy = admission.model_input_key(stm, opponent)
    state = large_reference.project_physical_record(record)
    stm_large = large_reference.feature_rows(state, record.side_to_move)
    opponent_large = large_reference.feature_rows(state, record.side_to_move ^ 1)
    large = admission.large_model_input_key(
        stm_large.k64,
        stm_large.g1,
        opponent_large.k64,
        opponent_large.g1,
        sum(state.pockets),
    )
    return legacy, large


def diagnose(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    database = root / "materialization-identities.sqlite3"
    require(database.is_file(), "materialization identity index is missing")
    connection = sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True)
    kinds = ("position_identity", "model_input_key", "large_model_input_key")
    overlaps: dict[str, set[bytes]] = {}
    for kind in kinds:
        overlaps[kind] = {
            bytes(row[0])
            for row in connection.execute(
                "SELECT train.key FROM identities AS train "
                "JOIN identities AS validation "
                "ON train.kind = validation.kind AND train.key = validation.key "
                "WHERE train.kind = ? AND train.role = 0 AND validation.role = 1",
                (kind,),
            )
        }
    totals = {
        kind: {
            "train_unique": int(
                connection.execute(
                    "SELECT COUNT(*) FROM identities WHERE kind = ? AND role = 0",
                    (kind,),
                ).fetchone()[0]
            ),
            "validation_unique": int(
                connection.execute(
                    "SELECT COUNT(*) FROM identities WHERE kind = ? AND role = 1",
                    (kind,),
                ).fetchone()[0]
            ),
        }
        for kind in kinds
    }
    validation_trajectories = int(
        connection.execute(
            "SELECT COUNT(*) FROM identities WHERE kind = 'trajectory_id' AND role = 1"
        ).fetchone()[0]
    )
    connection.close()

    large_reference = admission.validate_large_projection_artifacts()
    affected_rows = {kind: 0 for kind in kinds}
    affected_trajectories = {kind: set() for kind in kinds}
    affected_any: set[bytes] = set()
    records = 0
    for path in sorted((root / "chunks/validation").glob("*.chp")):
        payload = path.read_bytes()
        count = struct.unpack_from("<Q", payload, 40)[0]
        campaign_id = bytes(payload[64:80])
        require(
            len(payload) == codec.HEADER_SIZE + count * codec.RECORD_SIZE + codec.FOOTER_SIZE,
            "physical chunk framing drifted",
        )
        for index in range(count):
            start = codec.HEADER_SIZE + index * codec.RECORD_SIZE
            raw = payload[start : start + codec.RECORD_SIZE]
            record = codec.decode_record(raw)
            legacy, large = model_keys(record, large_reference)
            keys = {
                "position_identity": record.position_identity_sha256,
                "model_input_key": legacy,
                "large_model_input_key": large,
            }
            trajectory = campaign_id + record.trajectory_id
            contaminated = False
            for kind, key in keys.items():
                if key in overlaps[kind]:
                    affected_rows[kind] += 1
                    affected_trajectories[kind].add(trajectory)
                    contaminated = True
            if contaminated:
                affected_any.add(trajectory)
            records += 1
    require(records == 131_072, "validation record total drifted")
    result = {
        "schema": "crazyhouse-a0-cross-role-overlap-diagnostic/v1",
        "status": "FAIL_CROSS_ROLE_INTERSECTION",
        "validation_records": records,
        "validation_trajectories": validation_trajectories,
        "affected_validation_trajectories_any": len(affected_any),
        "kinds": {
            kind: {
                **totals[kind],
                "overlapping_unique_keys": len(overlaps[kind]),
                "affected_validation_records": affected_rows[kind],
                "affected_validation_trajectories": len(affected_trajectories[kind]),
            }
            for kind in kinds
        },
        "raw_record_intersection": 0,
        "game_id_intersection": 0,
        "trajectory_id_intersection": 0,
    }
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(diagnose(args.root), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
