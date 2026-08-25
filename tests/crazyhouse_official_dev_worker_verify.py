#!/usr/bin/env python3
"""Portable bounded Worker replay for the official-derived Crazyhouse source."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import crazyhouse_official_dev_routing_verify as current_routing
import crazyhouse_worker_search_verify as worker_verify


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--legacy-network", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--expected-engine-sha256", required=True)
    parser.add_argument("--transcript-out", required=True, type=Path)
    args = parser.parse_args()

    require(
        re.fullmatch(r"[0-9a-f]{64}", args.expected_engine_sha256) is not None,
        "expected engine SHA-256 is malformed",
    )
    engine = args.engine.resolve(strict=True)
    legacy = args.legacy_network.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    transcript_path = args.transcript_out.resolve(strict=False)
    require(transcript_path.parent.is_dir(), "transcript parent is missing")
    require(not transcript_path.exists(), "transcript already exists")
    require(
        current_routing.sha256_file(engine) == args.expected_engine_sha256,
        "engine SHA-256 mismatch",
    )

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(
        contract["schema"] == "crazyhouse-official-dev-routing-contract/v1",
        "contract schema mismatch",
    )
    repository = contract_path.parents[2]
    worker_contract_path = current_routing.authenticate_pin(
        repository,
        "worker_search_contract_v1",
        contract["pins"]["worker_search_contract_v1"],
    )
    worker_contract = json.loads(worker_contract_path.read_text(encoding="utf-8"))
    require(
        worker_contract["profile"]["token"] == contract["profile"]["token"],
        "Worker profile drift",
    )
    identity = contract["networks"]["legacy"]
    require(legacy.stat().st_size == identity["bytes"], "legacy network size mismatch")
    require(
        current_routing.sha256_file(legacy) == identity["sha256"],
        "legacy network SHA-256 mismatch",
    )

    route = contract["worker"]
    evaluator_context = {
        "mode": "incremental-scalar",
        "required_route_token": route["required_route_token"],
        "forbidden_route_tokens": route["forbidden_route_tokens"],
        "telemetry_label": "incremental scalar",
    }
    engine_before = current_routing.sha256_file(engine)
    legacy_before = current_routing.sha256_file(legacy)
    observations: list[dict] = []
    for case in worker_contract["cases"]:
        runs = [
            worker_verify.run_once(engine, legacy, case, evaluator_context)
            for _ in range(case["repetitions"])
        ]
        if case["require_same_bestmove"]:
            require(
                len({run["bestmove"] for run in runs}) == 1,
                f"{case['id']}: repeated bestmove drift",
            )
        observations.append({"id": case["id"], "runs": runs})

    require(current_routing.sha256_file(engine) == engine_before, "engine changed during replay")
    require(current_routing.sha256_file(legacy) == legacy_before, "legacy network changed during replay")
    payload = {
        "schema": "crazyhouse-official-dev-worker-replay/v1",
        "engine": {
            "bytes": engine.stat().st_size,
            "sha256": engine_before,
        },
        "legacy_network": {
            "bytes": legacy.stat().st_size,
            "sha256": legacy_before,
        },
        "routing_contract": {
            "bytes": contract_path.stat().st_size,
            "sha256": current_routing.sha256_file(contract_path),
        },
        "worker_contract": {
            "bytes": worker_contract_path.stat().st_size,
            "sha256": current_routing.sha256_file(worker_contract_path),
        },
        "evaluator": "incremental-scalar",
        "route_token": route["required_route_token"],
        "observations": observations,
        "artifact_identities_stable": True,
        "result": "PASS_OFFICIAL_DEV_WORKER_SEARCH",
        "timing_evidence": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_claim": False,
    }
    with transcript_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
    print(
        "PASS crazyhouse_official_dev_worker cases=2 runs=4 "
        "backend=legacy-v1 evaluator=incremental-scalar route_telemetry=PASS "
        "strength_claim=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL crazyhouse_official_dev_worker_verify: {exc}", file=sys.stderr)
        raise SystemExit(1)
