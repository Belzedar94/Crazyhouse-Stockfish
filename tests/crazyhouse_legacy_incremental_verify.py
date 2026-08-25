#!/usr/bin/env python3
"""Verify the frozen legacy V1 incremental/full-refresh transition corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


SCHEMA = "crazyhouse-legacy-incremental-cases/v1"
OFFICIAL_SOURCE_COMMIT = "229f6339e537a097a79831cd06dbfdb3e623d4ac"
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
NETWORK_BYTES = 58_534_811


def fail(message: str) -> None:
    print(f"FAIL crazyhouse_legacy_incremental_verify: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_counters(case: dict[str, object]) -> tuple[int, int, int, int]:
    mode = case["mode"]
    moves = case["moves"]
    if not isinstance(moves, list) or not all(isinstance(move, str) and move for move in moves):
        if mode not in {"null"} or moves != []:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()

    for label, path in (("oracle", args.oracle), ("network", args.network), ("fixture", args.fixture)):
        if not path.is_file():
            fail(f"{label} is not a regular file: {path}")

    if args.network.stat().st_size != NETWORK_BYTES:
        fail("registered network size mismatch")
    network_sha = sha256(args.network)
    if network_sha != NETWORK_SHA256:
        fail("registered network SHA-256 mismatch")

    try:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"fixture parse failed: {exc}")
    if fixture.get("schema") != SCHEMA:
        fail("fixture schema mismatch")
    if fixture.get("official_source_commit") != OFFICIAL_SOURCE_COMMIT:
        fail("fixture official-source identity mismatch")
    if fixture.get("network") != {"bytes": NETWORK_BYTES, "sha256": NETWORK_SHA256}:
        fail("fixture network identity mismatch")

    repo_root = args.fixture.resolve().parents[2]
    authorities = fixture.get("bound_authorities")
    if not isinstance(authorities, dict):
        fail("fixture bound authorities are missing")
    for label in ("numeric_goldens", "rule_fixture"):
        record = authorities.get(label)
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            fail(f"fixture {label} authority record is malformed")
        authority_path = repo_root / str(record["path"])
        if not authority_path.is_file() or sha256(authority_path) != record["sha256"]:
            fail(f"fixture {label} authority identity mismatch")

    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("fixture cases are missing")
    ids: set[str] = set()
    lines: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            fail("fixture case is not an object")
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

    payload = ("\n".join(lines) + "\n").encode("utf-8")
    first = subprocess.run(
        [str(args.oracle), str(args.network)], input=payload, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    second = subprocess.run(
        [str(args.oracle), str(args.network)], input=payload, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    for run_id, completed in ((1, first), (2, second)):
        if completed.returncode != 0:
            fail(
                f"oracle run {run_id} exited {completed.returncode}: "
                + completed.stderr.decode("utf-8", errors="replace")
            )
        if completed.stderr:
            fail(f"oracle run {run_id} emitted stderr")
    if first.stdout != second.stdout:
        fail("two identical oracle runs produced different stdout")

    output = first.stdout.decode("utf-8", errors="strict").strip()
    expected_prefix = f"PASS crazyhouse_legacy_incremental cases={len(cases)} "
    if not output.startswith(expected_prefix) or " trace_digest=" not in output:
        fail(f"unexpected oracle output: {output}")

    fixture_sha = sha256(args.fixture)
    protocol_sha = hashlib.sha256(first.stdout).hexdigest()
    print(
        "PASS crazyhouse_legacy_incremental_verify "
        f"cases={len(cases)} fixture_sha256={fixture_sha} "
        f"network_sha256={network_sha} protocol_sha256={protocol_sha}"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
