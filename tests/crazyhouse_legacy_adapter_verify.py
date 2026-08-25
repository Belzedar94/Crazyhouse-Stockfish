#!/usr/bin/env python3
"""Verify explicit legacy value and Crazyhouse outer adapter semantics."""

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
VALUES = {"N": 781, "B": 825, "R": 1276, "Q": 2538}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> NoReturn:
    print(f"FAIL crazyhouse_legacy_adapter_verify: {message}", file=sys.stderr)
    raise SystemExit(1)


def trunc_div(numerator: int, denominator: int) -> int:
    quotient = abs(numerator) // denominator
    return -quotient if numerator < 0 else quotient


def board_inventory(fen: str) -> tuple[int, int, int]:
    board = fen.split()[0].split("[")[0]
    pawns = board.count("P") + board.count("p")
    white = sum(board.count(piece) * value for piece, value in VALUES.items())
    black = sum(board.count(piece.lower()) * value for piece, value in VALUES.items())
    return pawns, white, black


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    args = parser.parse_args()

    if not args.evaluator.is_file():
        fail("adapter executable is missing")
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
        fail("legacy adapter timed out")

    if run.returncode != 0:
        fail(f"legacy adapter exited {run.returncode}: {run.stderr.strip()}")
    if run.stderr:
        fail("passing legacy adapter emitted stderr")
    lines = run.stdout.splitlines()
    if len(lines) != len(cases):
        fail(f"legacy adapter emitted {len(lines)} rows for {len(cases)} cases")

    digester = hashlib.sha256()
    for case, line in zip(cases, lines, strict=True):
        fields = line.split("\t")
        if len(fields) != 15 or fields[0] != "OK":
            fail(f"{case['id']} emitted malformed protocol")
        observation = case["observation"]
        if fields[1] != observation["fen"]:
            fail(f"{case['id']} canonical FEN mismatch")
        bucket = int(fields[2])
        if bucket != observation["correct_bucket"]:
            fail(f"{case['id']} selected bucket mismatch")
        psqt, positional = int(fields[3]), int(fields[4])
        if psqt != observation["raw_psqt"][bucket] or positional != observation["raw_positional"][bucket]:
            fail(f"{case['id']} selected raw pair mismatch")

        pawns, white_npm, black_npm = board_inventory(observation["fen"])
        if [int(fields[i]) for i in (5, 6, 7)] != [pawns, white_npm, black_npm]:
            fail(f"{case['id']} frozen board inventory mismatch")
        entertainment = 7 if abs(white_npm - black_npm) <= 44 else 0
        unadjusted = trunc_div(psqt + positional, 16)
        blend = trunc_div((128 - entertainment) * psqt + (128 + entertainment) * positional, 128)
        adjusted = trunc_div(blend, 16)
        scale = 903 + 32 * pawns + trunc_div(32 * (white_npm + black_npm), 1024)
        pre_clamp = trunc_div(adjusted * scale, 1024)
        outer = max(-31507, min(31507, pre_clamp))
        expected = [
            1 if entertainment else 0,
            scale,
            observation["legacy_unadjusted"],
            observation["legacy_adjusted"],
            pre_clamp,
            observation["legacy_outer"],
            1 if pre_clamp != outer else 0,
        ]
        actual = [int(fields[i]) for i in range(8, 15)]
        if unadjusted != expected[2] or adjusted != expected[3] or outer != expected[5]:
            fail(f"{case['id']} independent formula does not reproduce admitted goldens")
        if actual != expected:
            fail(f"{case['id']} adapter output mismatch")
        digester.update((line + "\n").encode("utf-8"))

    print(
        "PASS crazyhouse_legacy_adapter "
        f"cases={len(cases)} protocol_sha256={digester.hexdigest()} "
        "golden_values=PASS frozen_material=PASS division=PASS clamp_microfixtures=PASS "
        "invalid_inventory=PASS arithmetic_overflow=PASS chess960=PASS stale_failed_load=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
