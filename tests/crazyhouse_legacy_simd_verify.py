#!/usr/bin/env python3
"""Verify the frozen legacy V1 scalar/SIMD parity contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


CONTRACT_SCHEMA = "crazyhouse-legacy-v1-simd-parity-contract/v1"
FIXTURE_SCHEMA = "crazyhouse-legacy-incremental-cases/v1"
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
NETWORK_BYTES = 58_534_811
TRACE_DIGEST = "ccc566b7c58a917a"


def fail(message: str) -> None:
    print(f"FAIL crazyhouse_legacy_simd_verify: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{label} parse failed: {exc}")
    if not isinstance(value, dict):
        fail(f"{label} root is not an object")
    return value


def expected_counters(case: dict[str, object]) -> tuple[int, int, int, int]:
    mode = case.get("mode")
    moves = case.get("moves")
    if not isinstance(moves, list) or not all(isinstance(move, str) and move for move in moves):
        if mode != "null" or moves != []:
            fail(f"{case.get('id')} has an invalid move list")
    count = len(moves)
    if mode == "walk":
        if count == 0:
            fail(f"{case.get('id')} walk has no moves")
        return count, count, 1, count
    if mode == "lazy":
        if count < 2:
            fail(f"{case.get('id')} lazy schedule is not multi-ply")
        return count, 1, count, count
    if mode == "null":
        return 0, 2, 0, 0
    if mode == "unsynchronized":
        if count != 1:
            fail(f"{case.get('id')} unsynchronized schedule must contain one move")
        return 0, 1, 0, 1
    fail(f"{case.get('id')} has unknown mode {mode!r}")


def build_payload(fixture: dict[str, object]) -> tuple[bytes, int]:
    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("fixture cases are missing")

    ids: set[str] = set()
    lines: list[str] = []
    for case_value in cases:
        if not isinstance(case_value, dict):
            fail("fixture case is not an object")
        case = case_value
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            fail(f"invalid or duplicate case id: {case_id!r}")
        ids.add(case_id)
        fen = case.get("fen")
        expected_fen = case.get("expected_final_fen")
        king_refreshes = case.get("expected_king_refreshes")
        if not isinstance(fen, str) or "\t" in fen or not isinstance(expected_fen, str):
            fail(f"{case_id} has an invalid FEN field")
        if not isinstance(king_refreshes, int) or king_refreshes < 0:
            fail(f"{case_id} has invalid king-refresh expectation")
        deltas, reuses, max_distance, move_count = expected_counters(case)
        moves = case["moves"]
        if move_count != len(moves):
            fail(f"{case_id} internal move-count contract mismatch")
        fields = [
            case_id,
            str(case["mode"]),
            fen,
            " ".join(moves),
            expected_fen,
            "1",
            str(deltas),
            str(reuses),
            str(king_refreshes),
            str(max_distance),
        ]
        if any("\t" in field or "\n" in field or "\r" in field for field in fields):
            fail(f"{case_id} contains a forbidden stream delimiter")
        lines.append("\t".join(fields))
    return ("\n".join(lines) + "\n").encode("utf-8"), len(cases)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--expected-backend", required=True)
    args = parser.parse_args()

    for label, path in (
        ("oracle", args.oracle),
        ("network", args.network),
        ("fixture", args.fixture),
        ("contract", args.contract),
    ):
        if not path.is_file():
            fail(f"{label} is not a regular file: {path}")

    contract = read_json(args.contract, "contract")
    if contract.get("schema") != CONTRACT_SCHEMA:
        fail("contract schema mismatch")
    network_record = contract.get("legacy_network")
    if not isinstance(network_record, dict) or network_record.get("bytes") != NETWORK_BYTES \
            or network_record.get("sha256") != NETWORK_SHA256:
        fail("contract network identity mismatch")
    proposed_api = contract.get("proposed_api")
    lanes = proposed_api.get("required_test_lanes") if isinstance(proposed_api, dict) else None
    if not isinstance(lanes, list) or args.expected_backend not in {
        lane.get("expected_backend") for lane in lanes if isinstance(lane, dict)
    }:
        fail("expected backend is not admitted by the contract")

    if args.network.stat().st_size != NETWORK_BYTES or sha256(args.network) != NETWORK_SHA256:
        fail("registered network identity mismatch")

    fixture = read_json(args.fixture, "fixture")
    if fixture.get("schema") != FIXTURE_SCHEMA:
        fail("fixture schema mismatch")
    frozen = contract.get("frozen_corpus")
    if not isinstance(frozen, dict) or sha256(args.fixture) != frozen.get("sha256"):
        fail("fixture identity mismatch")
    repo_root = args.fixture.resolve().parents[2]
    addendum = repo_root / str(frozen.get("addendum_path"))
    if not addendum.is_file() or sha256(addendum) != frozen.get("addendum_sha256"):
        fail("fixture addendum identity mismatch")

    payload, case_count = build_payload(fixture)
    command = [str(args.oracle), str(args.network), args.expected_backend]
    runs = [
        subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       check=False)
        for _ in range(2)
    ]
    for run_id, completed in enumerate(runs, start=1):
        if completed.returncode != 0:
            fail(
                f"oracle run {run_id} exited {completed.returncode}: "
                + completed.stderr.decode("utf-8", errors="replace")
            )
        if completed.stderr:
            fail(f"oracle run {run_id} emitted stderr")
    if runs[0].stdout != runs[1].stdout:
        fail("two identical SIMD oracle runs produced different stdout")

    output = runs[0].stdout.decode("utf-8", errors="strict").strip()
    expected = (
        f"PASS crazyhouse_legacy_simd backend={args.expected_backend} cases={case_count} "
        f"transitions=27 undos=27 nulls=1 rejections=3 trace_digest={TRACE_DIGEST}"
    )
    if output != expected:
        fail(f"unexpected oracle output: {output}")

    print(
        "PASS crazyhouse_legacy_simd_verify "
        f"backend={args.expected_backend} cases={case_count} "
        f"fixture_sha256={sha256(args.fixture)} network_sha256={NETWORK_SHA256} "
        f"protocol_sha256={hashlib.sha256(runs[0].stdout).hexdigest()}"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
