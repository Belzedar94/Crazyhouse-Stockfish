#!/usr/bin/env python3
"""Compare the independent C++ feature extractor with admitted goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


GOLDEN_BYTES = 58_102
GOLDEN_SHA256 = "53866d1139a85ac5e982e6ffd74ce6d0c154abdc7ea46b68fe238aa4ea822eb6"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> "NoReturn":
    print(f"FAIL crazyhouse_legacy_features_verify: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_indices(text: str, case_id: str, perspective: str) -> list[int]:
    if not text:
        fail(f"{case_id} emitted no {perspective} indices")
    try:
        result = [int(value) for value in text.split(",")]
    except ValueError:
        fail(f"{case_id} emitted malformed {perspective} indices")
    if len(result) != len(set(result)):
        fail(f"{case_id} emitted duplicate {perspective} indices")
    if any(index < 0 or index >= 55_296 for index in result):
        fail(f"{case_id} emitted out-of-range {perspective} indices")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extractor", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    args = parser.parse_args()

    if not args.extractor.is_file():
        fail("extractor executable is missing")
    if (
        not args.golden.is_file()
        or args.golden.stat().st_size != GOLDEN_BYTES
        or sha256(args.golden) != GOLDEN_SHA256
    ):
        fail("golden identity mismatch")

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    cases = golden["cases"]
    payload = "".join(case["observation"]["fen"] + "\n" for case in cases)
    try:
        run = subprocess.run(
            [str(args.extractor)],
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail("extractor timed out")

    if run.returncode != 0:
        fail(f"extractor exited {run.returncode}: {run.stderr.strip()}")
    if run.stderr:
        fail("passing extractor emitted stderr")
    lines = run.stdout.splitlines()
    if len(lines) != len(cases):
        fail(f"extractor emitted {len(lines)} rows for {len(cases)} cases")

    digester = hashlib.sha256()
    for case, line in zip(cases, lines, strict=True):
        fields = line.split("\t")
        if len(fields) != 6 or fields[0] != "OK":
            fail(f"{case['id']} emitted malformed protocol")
        observation = case["observation"]
        if fields[1] != observation["fen"]:
            fail(f"{case['id']} canonical FEN mismatch")
        if int(fields[2]) != observation["board_piece_count"]:
            fail(f"{case['id']} board count mismatch")
        if int(fields[3]) != observation["correct_bucket"]:
            fail(f"{case['id']} bucket mismatch")
        white = parse_indices(fields[4], case["id"], "white")
        black = parse_indices(fields[5], case["id"], "black")
        if white != observation["active_white"]:
            fail(f"{case['id']} ordered white indices differ")
        if black != observation["active_black"]:
            fail(f"{case['id']} ordered black indices differ")
        digester.update((line + "\n").encode("utf-8"))

    print(
        "PASS crazyhouse_legacy_features "
        f"cases={len(cases)} perspectives={len(cases) * 2} "
        f"protocol_sha256={digester.hexdigest()} wrong_ruleset=PASS feature_only=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
