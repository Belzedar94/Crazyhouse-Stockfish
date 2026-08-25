#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


EXPECTED_NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": resolved.as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))


def authenticate(record: dict[str, Any]) -> Path:
    path = Path(record["path"]).resolve(strict=True)
    actual = file_record(path)
    require(actual["bytes"] == record["bytes"], f"byte mismatch: {path}")
    require(actual["sha256"] == record["sha256"], f"digest mismatch: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--start-receipt", type=Path, required=True)
    parser.add_argument("--end-receipt", type=Path, required=True)
    parser.add_argument("--completion", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    require(COMMIT_RE.fullmatch(args.expected_commit) is not None, "invalid expected commit")
    require(COMMIT_RE.fullmatch(args.expected_tree) is not None, "invalid expected tree")
    require(not args.output.exists(), f"refusing existing output: {args.output}")

    result_path = args.result.resolve(strict=True)
    start_path = args.start_receipt.resolve(strict=True)
    end_path = args.end_receipt.resolve(strict=True)
    completion_path = args.completion.resolve(strict=True)
    result = read_json(result_path)
    start = read_json(start_path)
    end = read_json(end_path)
    completion = read_json(completion_path)

    require(result["schema"] == "crazyhouse-openbench-clean-exports-result/v1", "result schema")
    require(start["schema"] == "crazyhouse-resource-handoff-start/v1", "start schema")
    require(end["schema"] == "crazyhouse-resource-handoff-end/v1", "end schema")
    require(
        completion["schema"] == "crazyhouse-openbench-clean-exports-completion/v1",
        "completion schema",
    )
    require(result["result"] == "PASS_TWO_CLEAN_OPENBENCH_ENGINE_EXPORTS", "producer result")
    require(result["failure"] is None, "producer preserved a failure")
    require(result["source"]["commit"] == args.expected_commit, "source commit")
    require(result["source"]["tree"] == args.expected_tree, "source tree")
    require(result["source"]["clean"] is True, "source admission was dirty")
    require(result["network"]["sha256"] == EXPECTED_NETWORK_SHA256, "network identity")
    require(start["supervisor_pid"] == result["resource_handoff"]["supervisor_pid"], "PID handoff")
    require(start["controller_pid"] == end["controller_pid"], "controller PID handoff")
    require(start["lease"] == result["lease"] == end["lease"] == completion["lease"], "lease chain")
    require(start["owner_task"] == result["owner_task"] == end["owner_task"], "owner chain")
    require(start["resources"]["build_jobs"] == 1, "build concurrency")
    require(start["authorization"]["openbench_submission"] is False, "submission boundary")
    require(start["authorization"]["publication"] is False, "publication boundary")
    require(result["resource_handoff"]["foreign_processes_mutated"] is False, "foreign mutation")
    require(not result["resource_handoff"]["owned_timeout_kills"], "formal step timed out")
    require(end["status"] == result["result"] == completion["status"], "terminal status chain")
    require(completion["controller_reported_exit_code"] == 0, "controller reported nonzero")

    require(file_record(result_path) == end["result"], "end receipt does not pin result")
    require(file_record(result_path) == completion["result"], "completion does not pin result")
    require(file_record(start_path) == result["start_receipt"], "result does not pin start")
    require(file_record(start_path) == end["start_receipt"], "end does not pin start")
    require(file_record(end_path) == completion["end_receipt"], "completion does not pin end")

    require(len(result["archives"]) == 2, "archive count")
    archive_paths = [authenticate(record) for record in result["archives"]]
    require(result["archives"][0]["sha256"] == result["archives"][1]["sha256"], "archive digest drift")
    require(result["archives"][0]["bytes"] == result["archives"][1]["bytes"], "archive size drift")
    require(archive_paths[0] != archive_paths[1], "exports reused one archive path")
    manifest_path = authenticate(result["source_manifest"])
    manifest = read_json(manifest_path)
    require(manifest["commit"] == args.expected_commit, "manifest commit")
    require(manifest["tree"] == args.expected_tree, "manifest tree")
    require(manifest["exports_equal"] is True, "source exports differ")
    require(manifest["tracked_files"] == result["source"]["tracked_files"], "source file count")
    require(len(manifest["entries"]) == manifest["tracked_files"], "manifest entry count")

    steps = result["steps"]
    require(len(steps) == 8, "formal step count")
    require(len({step["id"] for step in steps}) == len(steps), "duplicate step id")
    for step in steps:
        require(step["timed_out"] is False, f"step timed out: {step['id']}")
        require(step["exit_code"] == 0, f"step failed: {step['id']}")
        authenticate(step["stdout"])
        authenticate(step["stderr"])

    exports = result["exports"]
    require([entry["id"] for entry in exports] == ["a", "b"], "export identity/order")
    engines = [authenticate(entry["engine"]) for entry in exports]
    require(engines[0] != engines[1], "exports reused one engine path")
    require(all(entry["tracked_source_unchanged"] for entry in exports), "tracked source mutation")
    require(exports[0]["compile_units"] == exports[1]["compile_units"] >= 25, "compile inventory")

    node_rows: list[list[int]] = []
    for entry in exports:
        verification = entry["verification"]
        require(verification["schema"] == "crazyhouse-openbench-engine-verification/v1", "engine verification schema")
        require(verification["uci"]["uciok"] is True, "UCI inventory")
        require(verification["capability"]["acknowledged"] is True, "capability acknowledgement")
        require(verification["capability"]["network_identity"] == EXPECTED_NETWORK_SHA256, "runtime network")
        require(verification["negative"]["rejected"] is True, "missing override negative")
        require(verification["negative"]["fallback_observed"] is False, "network fallback")
        require(verification["bench"]["runs"] == 2, "bench run count")
        require(verification["bench"]["deterministic"] is True, "bench determinism")
        require(verification["bench"]["expected_nodes"] == 113485, "frozen bench expectation")
        require(len(set(verification["bench"]["nodes"])) == 1, "within-export node drift")
        require(verification["addenda"]["signature"]["formal_lease"] == 286, "signature lease")
        require(verification["addenda"]["target"]["profile"]["arch"] == "x86-64", "target arch")
        require(
            verification["addenda"]["target"]["profile"]["legacy_evaluator"] == "scalar",
            "target evaluator",
        )
        require(verification["claims"]["engineering_only"] is True, "engine claim class")
        require(all(value is False for key, value in verification["claims"].items() if key != "engineering_only"), "claim leak")
        node_rows.append(verification["bench"]["nodes"])
    require(node_rows[0] == node_rows[1], "cross-export node drift")
    require(result["equality"]["bench_nodes"] == node_rows[0][0], "aggregate node mismatch")
    require(result["equality"]["source_entries"] is True, "aggregate source equality")
    require(result["claims"] == {
        "engineering_only": True,
        "strength": False,
        "openbench_official": False,
        "publication": False,
        "release": False,
    }, "aggregate claim boundary")

    output = {
        "schema": "crazyhouse-openbench-clean-exports-independent-verification/v1",
        "result": "PASS_INDEPENDENT_TWO_CLEAN_OPENBENCH_ENGINE_EXPORTS",
        "producer_result": file_record(result_path),
        "start_receipt": file_record(start_path),
        "end_receipt": file_record(end_path),
        "completion": file_record(completion_path),
        "source": {"commit": args.expected_commit, "tree": args.expected_tree},
        "network_sha256": EXPECTED_NETWORK_SHA256,
        "exports": 2,
        "bench_runs": 4,
        "bench_nodes": node_rows[0][0],
        "engine_bytes_reproducible": result["equality"]["engine_bytes"],
        "foreign_processes_mutated": False,
        "claims": {
            "engineering_only": True,
            "strength": False,
            "openbench_official": False,
            "publication": False,
            "release": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as target:
        target.write((json.dumps(output, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"result": output["result"], "output": file_record(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
