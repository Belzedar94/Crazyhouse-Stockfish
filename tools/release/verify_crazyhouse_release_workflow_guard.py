#!/usr/bin/env python3
"""Verify that the inherited release workflow is an exact fail-closed G15 guard."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


class GuardError(RuntimeError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GuardError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardError(message)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def regular_unlinked_file(path: Path, label: str) -> Path:
    require(not path.is_symlink(), f"{label} must not be a symbolic link")
    resolved = path.resolve(strict=True)
    require(resolved.is_file(), f"{label} is not a regular file")
    require(os.stat(resolved, follow_symlinks=False).st_nlink == 1, f"{label} must have one link")
    return resolved


def load_contract(path: Path) -> dict[str, Any]:
    resolved = regular_unlinked_file(path, "contract")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GuardError(f"contract is not strict UTF-8 JSON: {error}") from error
    require(isinstance(value, dict), "contract root must be an object")
    require(value.get("schema") == "crazyhouse-release-workflow-guard/v1", "contract schema")
    require(value.get("project") == "Crazyhouse-Stockfish", "contract project")
    require(value.get("phase") == "P15", "contract phase")
    return value


def canonical_payload(contract: dict[str, Any]) -> bytes:
    guard = contract.get("canonical_guard")
    require(isinstance(guard, dict), "canonical_guard")
    lines = guard.get("lines")
    require(isinstance(lines, list) and lines and all(isinstance(line, str) for line in lines), "canonical lines")
    require(all("\r" not in line and "\n" not in line for line in lines), "canonical line framing")
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    require(len(payload) == guard.get("bytes"), "canonical byte count")
    require(sha256(payload) == guard.get("sha256"), "canonical SHA-256")
    forbidden = contract.get("forbidden_fragments")
    require(isinstance(forbidden, list) and forbidden and all(isinstance(item, str) for item in forbidden), "forbidden fragments")
    text = payload.decode("utf-8")
    require(not [item for item in forbidden if item in text], "canonical guard contains a forbidden fragment")
    return payload


def verify(contract_path: Path, workflow_path: Path) -> dict[str, Any]:
    contract = load_contract(contract_path)
    expected = canonical_payload(contract)
    workflow = regular_unlinked_file(workflow_path, "workflow")
    observed = workflow.read_bytes()
    actual_sha256 = sha256(observed)
    forbidden = [item for item in contract["forbidden_fragments"] if item.encode("utf-8") in observed]
    if observed != expected:
        inherited = contract.get("inherited_expected_red", {})
        status = (
            "EXPECTED_RED_RELEASE_WORKFLOW_NOT_GUARDED"
            if len(observed) == inherited.get("bytes") and actual_sha256 == inherited.get("sha256")
            else "FAIL_RELEASE_WORKFLOW_NOT_GUARDED"
        )
        raise GuardError(
            json.dumps(
                {
                    "actual_bytes": len(observed),
                    "actual_sha256": actual_sha256,
                    "expected_bytes": len(expected),
                    "expected_sha256": sha256(expected),
                    "forbidden_fragments_present": forbidden,
                    "status": status,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    require(not forbidden, "guard contains forbidden fragments")
    return {
        "actual_bytes": len(observed),
        "actual_sha256": actual_sha256,
        "forbidden_fragments_present": [],
        "github_writes": 0,
        "openbench_calls": 0,
        "status": "PASS_RELEASE_WORKFLOW_GUARD",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = verify(args.contract, args.workflow)
    except (GuardError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
