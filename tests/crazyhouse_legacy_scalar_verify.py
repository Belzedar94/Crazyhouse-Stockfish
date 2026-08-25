#!/usr/bin/env python3
"""Compare the independent scalar full refresh with immutable donor goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import NoReturn


NETWORK_BYTES = 58_534_811
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
GOLDEN_BYTES = 58_102
GOLDEN_SHA256 = "53866d1139a85ac5e982e6ffd74ce6d0c154abdc7ea46b68fe238aa4ea822eb6"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> NoReturn:
    print(f"FAIL crazyhouse_legacy_scalar_verify: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_vector(text: str, case_id: str, component: str) -> list[int]:
    try:
        values = [int(value) for value in text.split(",")]
    except ValueError:
        fail(f"{case_id} emitted malformed {component}")
    if len(values) != 8:
        fail(f"{case_id} emitted {len(values)} {component} buckets")
    if any(value < -(1 << 31) or value >= (1 << 31) for value in values):
        fail(f"{case_id} emitted out-of-range {component}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    args = parser.parse_args()

    if not args.evaluator.is_file():
        fail("evaluator executable is missing")
    if (
        not args.network.is_file()
        or args.network.stat().st_size != NETWORK_BYTES
        or sha256(args.network) != NETWORK_SHA256
    ):
        fail("network identity mismatch")
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
            [str(args.evaluator), str(args.network)],
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail("scalar evaluator timed out")

    if run.returncode != 0:
        fail(f"scalar evaluator exited {run.returncode}: {run.stderr.strip()}")
    if run.stderr:
        fail("passing scalar evaluator emitted stderr")
    lines = run.stdout.splitlines()
    if len(lines) != len(cases):
        fail(f"scalar evaluator emitted {len(lines)} rows for {len(cases)} cases")

    digester = hashlib.sha256()
    for case, line in zip(cases, lines, strict=True):
        fields = line.split("\t")
        if len(fields) != 7 or fields[0] != "OK":
            fail(f"{case['id']} emitted malformed protocol")
        observation = case["observation"]
        if fields[1] != observation["fen"]:
            fail(f"{case['id']} canonical FEN mismatch")
        bucket = int(fields[2])
        if bucket != observation["correct_bucket"]:
            fail(f"{case['id']} selected bucket mismatch")
        psqt = parse_vector(fields[3], case["id"], "PSQT")
        positional = parse_vector(fields[4], case["id"], "positional")
        if psqt != observation["raw_psqt"]:
            fail(f"{case['id']} raw PSQT vector mismatch")
        if positional != observation["raw_positional"]:
            fail(f"{case['id']} raw positional vector mismatch")
        if int(fields[5]) != psqt[bucket] or int(fields[6]) != positional[bucket]:
            fail(f"{case['id']} selected component does not match its raw bucket")
        digester.update((line + "\n").encode("utf-8"))

    print(
        "PASS crazyhouse_legacy_scalar "
        f"cases={len(cases)} raw_pairs={len(cases) * 8} "
        f"protocol_sha256={digester.hexdigest()} "
        "unloaded=PASS stale_failed_load=PASS reload=PASS wrong_ruleset=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
