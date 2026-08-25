#!/usr/bin/env python3
"""Fixture-first Crazyhouse MultiPV protocol verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ORTHODOX_OPTION = "option name MultiPV type spin default 1 min 1 max 256"
CRAZYHOUSE_OPTION = (
    "option name CrazyhouseMultiPV type spin default 0 min 0 max 2147483647"
)
INVALID_SETOPTION = (
    "info string ERROR setoption code=crazyhouse_multipv_invalid "
    "option=CrazyhouseMultiPV"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, object]:
    require(path.is_file(), f"missing file: {path}")
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class UciProcess:
    def __init__(self, engine: Path, timeout: float, runtime_path_prefix: str) -> None:
        env = os.environ.copy()
        if runtime_path_prefix:
            env["PATH"] = runtime_path_prefix + os.pathsep + env.get("PATH", "")
        self.timeout = timeout
        self.commands: list[str] = []
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.proc = subprocess.Popen(
            [str(engine)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        require(self.proc.stdin is not None, "engine stdin unavailable")
        require(self.proc.stdout is not None, "engine stdout unavailable")
        require(self.proc.stderr is not None, "engine stderr unavailable")
        self._threads = [
            threading.Thread(target=self._reader, args=("stdout", self.proc.stdout), daemon=True),
            threading.Thread(target=self._reader, args=("stderr", self.proc.stderr), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _reader(self, stream: str, handle: object) -> None:
        for raw in handle:  # type: ignore[union-attr]
            line = raw.rstrip("\r\n")
            if stream == "stdout":
                self.stdout.append(line)
            else:
                self.stderr.append(line)
            self.events.put((stream, line))

    def mark(self) -> int:
        return len(self.stdout)

    def send(self, command: str) -> int:
        require(self.proc.poll() is None, f"engine exited before command: {command}")
        mark = self.mark()
        self.commands.append(command)
        assert self.proc.stdin is not None
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return mark

    def wait_after(
        self,
        mark: int,
        predicate: Callable[[list[str]], bool],
        description: str,
    ) -> list[str]:
        deadline = time.monotonic() + self.timeout
        while True:
            current = self.stdout[mark:]
            if predicate(current):
                return list(current)
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"engine exited {self.proc.returncode} waiting for {description}; "
                    f"stdout={current!r}; stderr={self.stderr!r}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timeout waiting for {description}; stdout={current!r}; stderr={self.stderr!r}"
                )
            try:
                self.events.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                pass

    def uci(self) -> list[str]:
        mark = self.send("uci")
        return self.wait_after(mark, lambda lines: "uciok" in lines, "uciok")

    def ready(self) -> list[str]:
        mark = self.send("isready")
        return self.wait_after(mark, lambda lines: "readyok" in lines, "readyok")

    def search(self, command: str) -> list[str]:
        mark = self.send(command)
        return self.wait_after(
            mark, lambda lines: any(line.startswith("bestmove ") for line in lines), "bestmove"
        )

    def blocked_search(self, command: str, error_code: str) -> list[str]:
        mark = self.send(command)
        self.send("isready")
        lines = self.wait_after(mark, lambda rows: "readyok" in rows, "post-refusal readyok")
        require(
            any(
                line.startswith("info string ERROR go ") and f"code={error_code}" in line
                for line in lines
            ),
            f"missing blocked-search error {error_code}: {lines!r}",
        )
        require(
            not any(line.startswith("bestmove ") for line in lines),
            f"blocked search emitted bestmove: {lines!r}",
        )
        return lines

    def perft(self, depth: int, expected_nodes: int) -> list[str]:
        mark = self.send(f"go perft {depth}")
        marker = f"Nodes searched: {expected_nodes}"
        return self.wait_after(mark, lambda lines: marker in lines, marker)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.send("quit")
                self.proc.wait(timeout=self.timeout)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=10)
                raise
        for thread in self._threads:
            thread.join(timeout=1)
        require(self.proc.returncode == 0, f"engine exit code {self.proc.returncode}")
        require(not self.stderr, f"unexpected engine stderr: {self.stderr!r}")


def option_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("option name ")]


def depth_one_rows(lines: list[str]) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    pattern = re.compile(r"^info depth 1 .*\bmultipv (\d+)\b.*\bpv (\S+)(?:\s|$)")
    for line in lines:
        match = pattern.match(line)
        if match:
            rows.append((int(match.group(1)), match.group(2)))
    return rows


def require_pvs(lines: list[str], count: int, expected_moves: set[str] | None = None) -> dict[str, object]:
    rows = depth_one_rows(lines)
    require(len(rows) == count, f"expected {count} depth-one PV rows, got {len(rows)}")
    indices = [index for index, _ in rows]
    moves = [move for _, move in rows]
    require(indices == list(range(1, count + 1)), f"MultiPV indices mismatch: {indices!r}")
    require(len(set(moves)) == count, "duplicate root move in MultiPV output")
    if expected_moves is not None:
        require(set(moves) == expected_moves, "MultiPV root move set differs from frozen fixture")
    payload = "\n".join(sorted(moves)) + "\n"
    return {
        "count": count,
        "indices_complete": True,
        "moves_unique": True,
        "sorted_uci_lf_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def configure_crazyhouse(proc: UciProcess, legacy_network: Path) -> list[str]:
    proc.send(f"setoption name CrazyhouseEvalFile value {legacy_network}")
    lines = proc.ready()
    require(
        any("route_commit status=ok ruleset=crazyhouse" in line for line in lines),
        f"Crazyhouse route commit missing: {lines!r}",
    )
    return lines


def run_expected_red(
    engine: Path,
    legacy_network: Path,
    fixture: dict[str, object],
    timeout: float,
    runtime_path_prefix: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    proc = UciProcess(engine, timeout, runtime_path_prefix)
    observations: list[dict[str, object]] = []
    sessions: list[dict[str, object]] = []
    try:
        inventory = proc.uci()
        options = option_lines(inventory)
        require(options.count(ORTHODOX_OPTION) == 1, "orthodox MultiPV inventory drift")
        require(CRAZYHOUSE_OPTION not in options, "expected-red binary already advertises CrazyhouseMultiPV")
        observations.append({"id": "extended_option_absent", "observed": True})

        configure_crazyhouse(proc, legacy_network)
        mark = proc.send("setoption name CrazyhouseMultiPV value 303")
        missing = proc.ready()
        missing_lines = proc.stdout[mark:]
        require("No such option: CrazyhouseMultiPV" in missing_lines, "missing expected unknown-option error")
        require("readyok" in missing, "engine did not remain responsive after unknown option")
        observations.append({"id": "extended_option_unknown", "observed": True})

        proc.send("setoption name MultiPV value 303")
        proc.send(f"position fen {fixture['fen']}")
        search_lines = proc.search("go depth 1")
        rows = require_pvs(search_lines, 1)
        observations.append(
            {
                "id": "orthodox_range_silently_retained_one",
                "observed": True,
                "known_root_count": fixture["legal_move_count"],
                "output": rows,
            }
        )
    finally:
        proc.close()
        sessions.append(
            {
                "commands": proc.commands,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.proc.returncode,
            }
        )
    return observations, sessions


def fresh_process(
    engine: Path, timeout: float, runtime_path_prefix: str
) -> UciProcess:
    proc = UciProcess(engine, timeout, runtime_path_prefix)
    proc.uci()
    return proc


def run_green(
    engine: Path,
    legacy_network: Path,
    official_network: Path,
    fixture: dict[str, object],
    timeout: float,
    runtime_path_prefix: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    cases: list[dict[str, object]] = []
    sessions: list[dict[str, object]] = []

    def finish(proc: UciProcess) -> None:
        proc.close()
        sessions.append(
            {
                "commands": proc.commands,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.proc.returncode,
            }
        )

    proc = fresh_process(engine, timeout, runtime_path_prefix)
    try:
        options = option_lines(proc.stdout)
        require(options.count(ORTHODOX_OPTION) == 1, "orthodox MultiPV inventory drift")
        require(options.count(CRAZYHOUSE_OPTION) == 1, "CrazyhouseMultiPV inventory mismatch")
        configure_crazyhouse(proc, legacy_network)
        proc.send("setoption name CrazyhouseMultiPV value 303")
        proc.send(f"position fen {fixture['fen']}")
        expected_moves = set(fixture["drop_moves"]) | set(fixture["non_drop_moves"])
        output = require_pvs(proc.search("go depth 1"), 303, expected_moves)
        require(
            output["sorted_uci_lf_sha256"] == fixture["sorted_uci_lf_sha256"],
            "303-root digest mismatch",
        )
        cases.append({"id": "inventory_and_known_303", "status": "PASS", "output": output})
    finally:
        finish(proc)

    proc = fresh_process(engine, timeout, runtime_path_prefix)
    try:
        configure_crazyhouse(proc, legacy_network)
        proc.send("setoption name MultiPV value 3")
        proc.send("setoption name CrazyhouseMultiPV value 0")
        proc.send("position startpos")
        inherited = require_pvs(proc.search("go depth 1"), 3)
        proc.send("setoption name CrazyhouseMultiPV value 1000")
        proc.send("position startpos")
        clamped = require_pvs(proc.search("go depth 1"), 20)
        proc.send(f"position fen {fixture['fen']}")
        subset = require_pvs(proc.search("go depth 1 searchmoves e2d1 e2d2"), 2, {"e2d1", "e2d2"})
        cases.append(
            {
                "id": "inheritance_and_dynamic_clamps",
                "status": "PASS",
                "inherited": inherited,
                "startpos": clamped,
                "searchmoves": subset,
            }
        )
    finally:
        finish(proc)

    proc = fresh_process(engine, timeout, runtime_path_prefix)
    try:
        configure_crazyhouse(proc, legacy_network)
        proc.send("setoption name CrazyhouseMultiPV value 303")
        proc.send("setoption name UCI_Variant value chess")
        proc.send(f"setoption name EvalFile value {official_network}")
        proc.ready()
        proc.send("setoption name MultiPV value 2")
        proc.send("position startpos")
        chess = require_pvs(proc.search("go depth 1"), 2)
        proc.send("setoption name UCI_Variant value crazyhouse")
        proc.ready()
        proc.send("position startpos")
        crazyhouse = require_pvs(proc.search("go depth 1"), 20)
        cases.append(
            {
                "id": "variant_persistence_and_chess_isolation",
                "status": "PASS",
                "chess": chess,
                "crazyhouse": crazyhouse,
            }
        )
    finally:
        finish(proc)

    proc = fresh_process(engine, timeout, runtime_path_prefix)
    try:
        configure_crazyhouse(proc, legacy_network)
        proc.send("position startpos")
        invalid_values = ["abc", "-1", "2147483648", "999999999999999999999999"]
        for value in invalid_values:
            mark = proc.send(f"setoption name CrazyhouseMultiPV value {value}")
            immediate = proc.ready()
            assignment_lines = proc.stdout[mark:]
            require(INVALID_SETOPTION in assignment_lines, f"missing invalid telemetry for {value!r}")
            require("readyok" in immediate, f"isready blocked after invalid value {value!r}")
            proc.perft(1, 20)
            proc.blocked_search("go depth 1", "crazyhouse_multipv_invalid")
            proc.send("setoption name CrazyhouseMultiPV value 2")
            recovered = require_pvs(proc.search("go depth 1"), 2)
            require(recovered["count"] == 2, "valid assignment did not recover search")
        mark = proc.send("setoption name CrazyhouseMultiPV value")
        proc.ready()
        require(INVALID_SETOPTION in proc.stdout[mark:], "missing invalid telemetry for empty value")
        proc.blocked_search("go depth 1", "crazyhouse_multipv_invalid")

        proc.send("setoption name UCI_Variant value chess")
        proc.send(f"setoption name EvalFile value {official_network}")
        proc.ready()
        proc.send("setoption name MultiPV value 2")
        proc.send("position startpos")
        chess_while_invalid = require_pvs(proc.search("go depth 1"), 2)

        proc.send("setoption name UCI_Variant value crazyhouse")
        proc.ready()
        proc.send("position startpos")
        proc.blocked_search("go depth 1", "crazyhouse_multipv_invalid")

        proc.send("setoption name CrazyhouseMultiPV value 0")
        recovered_from_inherit = require_pvs(proc.search("go depth 1"), 2)
        cases.append(
            {
                "id": "invalid_sticky_boundary_and_recovery",
                "status": "PASS",
                "invalid_values": invalid_values + ["<empty>"],
                "perft_nodes_each": 20,
                "chess_while_invalid": chess_while_invalid,
                "sticky_after_return_to_crazyhouse": True,
                "recovered_from_zero_inheritance": recovered_from_inherit,
            }
        )
    finally:
        finish(proc)

    return cases, sessions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("expected-red", "green"), required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--legacy-network", type=Path, required=True)
    parser.add_argument("--official-network", type=Path)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--expected-engine-sha256", required=True)
    parser.add_argument("--transcript-out", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--runtime-path-prefix", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require(args.timeout_seconds > 0, "timeout must be positive")
    require(not args.transcript_out.exists(), f"refusing to overwrite transcript: {args.transcript_out}")

    engine_id = identity(args.engine)
    legacy_id = identity(args.legacy_network)
    fixture_id = identity(args.fixture)
    contract_id = identity(args.contract)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))

    require(engine_id["sha256"] == args.expected_engine_sha256.lower(), "engine SHA256 mismatch")
    require(
        legacy_id["sha256"] == contract["pins"]["legacy_network"]["sha256"],
        "legacy network SHA256 mismatch",
    )
    require(
        fixture_id["sha256"] == contract["pins"]["known_303_fixture"]["sha256"],
        "fixture SHA256 mismatch",
    )
    require(fixture["legal_move_count"] == 303, "fixture legal-move count mismatch")
    require(
        "303 is the global maximum" in fixture["claims_not_made"],
        "fixture must disclaim a global maximum",
    )

    started = utc_now()
    if args.mode == "expected-red":
        require(
            engine_id["sha256"] == contract["expected_red"]["engine_sha256"],
            "expected-red binary does not match preregistration",
        )
        evidence, sessions = run_expected_red(
            args.engine,
            args.legacy_network,
            fixture,
            args.timeout_seconds,
            args.runtime_path_prefix,
        )
        result = "PASS_EXPECTED_RED_PROTOCOL_RANGE_GAP"
    else:
        require(args.official_network is not None, "green mode requires --official-network")
        official_id = identity(args.official_network)
        require(
            official_id["sha256"] == contract["pins"]["official_network"]["sha256"],
            "official network SHA256 mismatch",
        )
        evidence, sessions = run_green(
            args.engine,
            args.legacy_network,
            args.official_network,
            fixture,
            args.timeout_seconds,
            args.runtime_path_prefix,
        )
        result = "PASS_CRAZYHOUSE_MULTIPV_PROTOCOL_BOUNDARY"

    payload = {
        "schema": "crazyhouse-multipv-verification/v1",
        "mode": args.mode,
        "started_utc": started,
        "completed_utc": utc_now(),
        "engine": engine_id,
        "legacy_network": legacy_id,
        "fixture": fixture_id,
        "contract": contract_id,
        "official_network": identity(args.official_network) if args.official_network else None,
        "evidence": evidence,
        "sessions": sessions,
        "result": result,
        "timing_evidence": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_claim": False,
    }
    args.transcript_out.parent.mkdir(parents=True, exist_ok=True)
    with args.transcript_out.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"{result} sessions={len(sessions)} evidence={len(evidence)} strength_claim=false")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL crazyhouse_multipv_verify: {exc}", file=sys.stderr)
        raise SystemExit(1)
