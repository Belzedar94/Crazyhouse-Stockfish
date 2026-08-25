#!/usr/bin/env python3
"""Deterministic rule-free UCI actor for the G4 missing-capability red test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--log", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sequence = 0
    with args.log.open("xb", buffering=0) as log:

        def record(direction: str, line: str) -> None:
            nonlocal sequence
            payload = json.dumps(
                {"direction": direction, "line": line, "sequence": sequence},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii") + b"\n"
            log.write(payload)
            sequence += 1

        def emit(line: str) -> None:
            record("out", line)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

        record("meta", "actor-start:" + args.name)
        while True:
            raw = sys.stdin.buffer.readline()
            if raw == b"":
                record("meta", "eof")
                break
            line = raw.rstrip(b"\r\n").decode("utf-8", errors="strict")
            record("in", line)

            if line == "uci":
                emit("id name " + args.name)
                emit("id author Crazyhouse-Stockfish")
                emit("option name UCI_Variant type combo default chess var chess var crazyhouse")
                emit("uciok")
            elif line == "isready":
                emit("readyok")
            elif line.startswith("go"):
                emit("bestmove 0000")
            elif line == "quit":
                record("meta", "quit")
                break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
