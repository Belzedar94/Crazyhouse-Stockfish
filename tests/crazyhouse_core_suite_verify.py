#!/usr/bin/env python3
"""Run the frozen core Crazyhouse rule suite and its fail-closed controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


NORMAL_CASES = {
    "move-abi": "PASS crazyhouse_move_abi raw=65536 invalid=36546 normal=4094 promotion=16384 ep=4096 castling=4096 drop=320 structural_chess=28226 structural_crazyhouse=28546 controls=SEPARATE",
    "move-buffer": "PASS crazyhouse_move_buffer counts=303,512,1024 recursive_ownership=PASS selected_stage_boundaries=PASS fixed_overflow_control=SEPARATE overflow_control=SEPARATE",
    "move-codec": "PASS crazyhouse_move_codec canonical=320 lowercase=320 rejected=34 output_role=uppercase square=lowercase",
    "ruleset-boundary": "PASS crazyhouse_ruleset_boundary values=chess,crazyhouse parser=exact position_owner=PASS storage_mapping=PASS invalid_controls=SEPARATE",
    "state-layout": "PASS crazyhouse_state_layout pocket_bytes=10 pocket_limits=16,4,4,4,2 stateinfo_prefix=PASS chess_zero=PASS crazyhouse_zero=PASS",
    "fen": "PASS crazyhouse_fen canonical=8 rejected=24 transactional=PASS bracket=PASS slash=PASS promoted=PASS chess_unchanged=PASS",
    "zobrist": "PASS crazyhouse_zobrist ruleset_salt=PASS pocket_states=60 promoted_squares=PASS formula=PASS chess_unchanged=PASS raw_key=PASS",
    "transitions": "PASS crazyhouse_transitions captures=PASS demotion=PASS promotion=PASS ep=PASS drops=PASS castling=PASS null=PASS undo=PASS keys=PASS",
    "drop-generation": "PASS crazyhouse_drop_generation exact303=PASS all_types=PASS restrictions=PASS single_check=PASS double_check=PASS check_drop=PASS ownership=PASS duplicates=PASS chess_isolation=PASS",
    "repetition-terminal": "PASS crazyhouse_repetition_terminal horizon=PASS threefold=PASS fivefold=PASS precedence=PASS no_50=PASS no_insufficient=PASS stalemate=PASS syzygy=PASS chess_isolation=PASS",
    "search-capacity": "PASS crazyhouse_search_capacity inline=1..255 fallback=256,303,512,1024 invalid_zero_control=SEPARATE",
    "search-primitives": "PASS crazyhouse_search_primitives moved_piece=PASS prefetch=DISABLED see=ENABLED move_picker_303=PASS drop_evasions=PASS chess_isolation=PASS",
}

NEGATIVE_CASES = [
    ("move-abi", "drop-from", "--drop-from-control", "kind() != MoveKind::DROP", False),
    ("move-abi", "nondrop-piece", "--nondrop-piece-control", "is_drop()", False),
    ("move-abi", "nonpromotion", "--nonpromotion-control", "kind() == MoveKind::PROMOTION", False),
    ("move-abi", "invalid-drop-piece", "--invalid-drop-piece-control", "pt >= PAWN && pt <= QUEEN", False),
    ("move-abi", "invalid-ruleset", "--invalid-ruleset-control", "FATAL Ruleset: invalid value in Move::is_structurally_valid", False),
    ("move-buffer", "growable-overflow", "--overflow-control", "FATAL CrazyhouseMoveBuffer: capacity overflow", False),
    ("move-buffer", "fixed-overflow", "--fixed-overflow-control", "FATAL FixedMoveBuffer: capacity exceeded", False),
    ("move-codec", "format-nondrop", "--format-nondrop-control", "move.is_drop()", False),
    ("ruleset-boundary", "invalid-storage", "--invalid-storage-control", "FATAL Ruleset: invalid value in uses_growable_move_storage", False),
    ("ruleset-boundary", "invalid-position", "--invalid-position-control", "FATAL Ruleset: invalid value in Position", False),
    ("fen", "invalid-pocket-type", "--invalid-pocket-type-control", "", True),
    ("fen", "invalid-pocket-color", "--invalid-pocket-color-control", "", True),
    ("search-capacity", "invalid-zero", "--invalid-zero-control", "FATAL MoveCountReductionTable: nonpositive index", False),
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_case(binary: Path, arguments: list[str], timeout: int) -> dict[str, Any]:
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [str(binary), *arguments],
        cwd=str(binary.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate(timeout=30)
    return {
        "pid": process.pid,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": len(stdout),
        "stdout_sha256": sha256_bytes(stdout),
        "stderr_bytes": len(stderr),
        "stderr_sha256": sha256_bytes(stderr),
    }


def public_record(case: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in case.items() if key not in {"stdout", "stderr"}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin-dir", required=True, type=Path)
    parser.add_argument("--configuration", required=True, choices=("debug", "release"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    bin_dir = args.bin_dir.resolve(strict=True)
    output = args.output.resolve(strict=False)
    require(not output.exists(), f"output already exists: {output}")
    require(args.timeout > 0, "timeout must be positive")

    suffix = ".exe" if os.name == "nt" else ""
    normal_results: dict[str, Any] = {}
    binaries: dict[str, Any] = {}
    for case_id, expected in NORMAL_CASES.items():
        binary = (bin_dir / f"crazyhouse-core-{case_id}-tests{suffix}").resolve(strict=True)
        require(binary.is_file(), f"not a binary: {binary}")
        run = run_case(binary, [], args.timeout)
        require(not run["timed_out"], f"normal case timed out: {case_id}")
        require(run["exit_code"] == 0, f"normal case failed: {case_id}")
        require(not run["stderr"], f"normal case emitted stderr: {case_id}")
        actual = run["stdout"].decode("utf-8", "strict").rstrip("\r\n")
        require(actual == expected, f"normal output mismatch: {case_id}")
        normal_results[case_id] = public_record(run)
        binaries[case_id] = {
            "path": str(binary),
            "bytes": binary.stat().st_size,
            "sha256": sha256_file(binary),
        }

    negative_results: dict[str, Any] = {}
    for case_id, control_id, argument, marker, require_empty in NEGATIVE_CASES:
        if args.configuration == "release" and case_id != "fen":
            continue
        binary = (bin_dir / f"crazyhouse-core-{case_id}-tests{suffix}").resolve(strict=True)
        run = run_case(binary, [argument], args.timeout)
        label = f"{case_id}:{control_id}"
        require(not run["timed_out"], f"negative case timed out: {label}")
        require(run["exit_code"] != 0, f"negative case did not fail closed: {label}")
        if require_empty:
            require(not run["stdout"] and not run["stderr"], f"negative case emitted output: {label}")
        else:
            require(marker.encode("utf-8") in run["stderr"], f"negative marker missing: {label}")
        negative_results[label] = {
            **public_record(run),
            "argument": argument,
            "required_empty_output": require_empty,
            "expected_stderr_fragment": marker,
        }

    result = {
        "schema": "crazyhouse-core-suite-runtime/v1",
        "result": "PASS_CORE_CRAZYHOUSE_RULE_REPLAY",
        "configuration": args.configuration,
        "normal_cases": normal_results,
        "negative_cases": negative_results,
        "binaries": binaries,
        "normal_case_count": len(normal_results),
        "negative_case_count": len(negative_results),
        "timing_evidence": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_claim": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as destination:
        destination.write((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(
        "PASS crazyhouse_core_suite "
        f"configuration={args.configuration} normal={len(normal_results)} negative={len(negative_results)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL crazyhouse_core_suite_verify: {error}", file=sys.stderr)
        raise SystemExit(1)
