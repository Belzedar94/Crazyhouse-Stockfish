#!/usr/bin/env python3
"""Independent verifier for the preregistered large-A0 campaign planner."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


EXPECTED_SCHEMA = "crazyhouse-p13-nnue-v2-large-a0-production-campaign-preregistration/v1"
EXPECTED_SEEDS = [617628155675752428, 521232795329550045, 4052246384809050251]


def strict_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planner", required=True, type=Path)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--campaign-sha256", required=True)
    parser.add_argument("--trainer", required=True, type=Path)
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict[str, Any]:
    payload = args.campaign.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == args.campaign_sha256
    campaign = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=strict_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    assert campaign["schema"] == EXPECTED_SCHEMA
    namespace = campaign["paired_seeds"]["namespace"]
    observed = []
    for index, entry in enumerate(campaign["paired_seeds"]["values"]):
        digest = hashlib.sha256(f"{namespace}:seed:{index}".encode("ascii")).hexdigest()
        seed = int.from_bytes(bytes.fromhex(digest)[:8], "big") & ((1 << 63) - 1)
        assert entry == {"index": index, "material_sha256": digest, "seed": seed}
        observed.append(seed)
    assert observed == EXPECTED_SEEDS
    assert campaign["paired_seeds"]["predesignated_playing_seed_index"] == 0
    command = [
        sys.executable,
        "-B",
        str(args.planner),
        "self-test",
        "--campaign",
        str(args.campaign),
        "--campaign-sha256",
        args.campaign_sha256,
        "--trainer",
        str(args.trainer),
    ]
    success = subprocess.run(command, check=False, capture_output=True, timeout=60)
    assert success.returncode == 0, success.stderr.decode("utf-8", errors="replace")
    result = json.loads(success.stdout.decode("utf-8"), object_pairs_hook=strict_pairs)
    assert result["status"] == "PASS_CAMPAIGN_SELF_TEST"
    assert result["seeds"] == EXPECTED_SEEDS
    with tempfile.TemporaryDirectory(prefix="crazyhouse-v2-campaign-negative-") as temp:
        mutated = Path(temp) / "campaign.json"
        changed = bytearray(payload)
        changed[-2] = 32 if changed[-2] != 32 else 9
        mutated.write_bytes(changed)
        rejected = subprocess.run(
            [
                sys.executable,
                "-B",
                str(args.planner),
                "self-test",
                "--campaign",
                str(mutated),
                "--campaign-sha256",
                args.campaign_sha256,
                "--trainer",
                str(args.trainer),
            ],
            check=False,
            capture_output=True,
            timeout=60,
        )
        assert rejected.returncode == 2
        error = json.loads(rejected.stderr.decode("utf-8"), object_pairs_hook=strict_pairs)
        assert error["code"] == "CAMPAIGN_SHA256"
    return {
        "campaign_sha256": args.campaign_sha256,
        "negative_cases": 1,
        "schema": "crazyhouse-nnue-v2-large-a0-campaign-independent-verification/v1",
        "seeds": observed,
        "status": "PASS_INDEPENDENT_CAMPAIGN_VERIFICATION",
        "training_started": False,
    }


def main() -> int:
    try:
        result = run(parse_args())
    except (AssertionError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {
                    "code": type(exc).__name__,
                    "schema": "crazyhouse-nnue-v2-large-a0-campaign-independent-verification/v1",
                    "status": "REJECTED",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
