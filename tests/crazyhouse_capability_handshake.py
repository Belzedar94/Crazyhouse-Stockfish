#!/usr/bin/env python3
"""Fixture-driven UCI test for the Crazyhouse pre-game capability handshake."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable


NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class EngineProcess:
    def __init__(self, executable: Path, artifact_dir: Path, name: str) -> None:
        self.name = name
        self.artifact_dir = artifact_dir
        self.records: list[dict[str, object]] = []
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self._sequence = 0
        self._lock = threading.Lock()
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            cwd=str(artifact_dir),
            env=os.environ.copy(),
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._threads = [
            threading.Thread(target=self._read, args=("out", self.process.stdout), daemon=True),
            threading.Thread(target=self._read, args=("err", self.process.stderr), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _record(self, direction: str, line: str) -> None:
        with self._lock:
            self.records.append(
                {"sequence": self._sequence, "direction": direction, "line": line}
            )
            self._sequence += 1

    def _read(self, direction: str, stream: object) -> None:
        for raw in stream:  # type: ignore[union-attr]
            line = raw.rstrip("\r\n")
            self._record(direction, line)
            if direction == "out":
                self.stdout_lines.append(line)
                self._stdout_queue.put(line)
            else:
                self.stderr_lines.append(line)

    def send(self, line: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(f"{self.name}: process exited before command {line!r}")
        assert self.process.stdin is not None
        self._record("in", line)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def wait_stdout(self, predicate: Callable[[str], bool], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None and self._stdout_queue.empty():
                raise RuntimeError(f"{self.name}: process exited with {self.process.returncode}")
            try:
                line = self._stdout_queue.get(timeout=min(0.1, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if predicate(line):
                return line
        raise TimeoutError(f"{self.name}: expected output was not observed")

    def initialize(self, timeout: float = 10.0) -> list[str]:
        start = len(self.stdout_lines)
        self.send("uci")
        self.wait_stdout(lambda line: line == "uciok", timeout)
        return list(self.stdout_lines[start:])

    def finish(self) -> None:
        if self.process.poll() is None:
            self.send("quit")
            try:
                self.process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5.0)
        for thread in self._threads:
            thread.join(timeout=1.0)
        transcript = self.artifact_dir / f"{self.name}.jsonl"
        with transcript.open("x", encoding="utf-8", newline="\n") as output:
            for record in self.records:
                output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def configure_crazyhouse(engine: EngineProcess, profile: str, net: Path, nonce: str) -> int:
    start = len(engine.stdout_lines)
    engine.send("setoption name UCI_Variant value crazyhouse")
    engine.send(f"setoption name CrazyhouseProfile value {profile}")
    engine.send(f"setoption name CrazyhouseEvalFile value {net}")
    engine.send(f"setoption name CrazyhouseCapabilityNonce value {nonce}")
    engine.send("isready")
    return start


def exact_ack(profile: dict[str, str], nonce: str) -> str:
    return (
        "info string crazyhouse_capability_ack status=ok "
        f"profile={profile['id']} profile_sha256={profile['sha256']} nonce={nonce}"
    )


def run(args: argparse.Namespace) -> int:
    timeout_scale = args.timeout_scale
    if not math.isfinite(timeout_scale) or timeout_scale <= 0:
        raise RuntimeError("timeout scale must be a positive finite number")
    effective_timeouts = {
        "uci": 10.0 * timeout_scale,
        "ready": 20.0 * timeout_scale,
        "invalid_observation": 3.0 * timeout_scale,
    }
    engine_path = args.engine.resolve(strict=True)
    net_path = args.net.resolve(strict=True)
    fixture_path = args.fixture.resolve(strict=True)
    artifact_dir = args.artifact_dir.resolve()
    if artifact_dir.exists():
        raise RuntimeError(f"artifact directory must be fresh: {artifact_dir}")
    artifact_dir.mkdir(parents=True)

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("schema") != "crazyhouse-engine-capability-handshake/v1":
        raise RuntimeError("fixture schema mismatch")
    network = fixture["network"]
    if net_path.stat().st_size != network["bytes"] or sha256(net_path) != network["sha256"]:
        raise RuntimeError("legacy network identity mismatch")

    profile = fixture["profile"]
    failures: list[str] = []
    cases: list[dict[str, object]] = []

    def record(case_id: str, checks: dict[str, bool], details: dict[str, object]) -> None:
        failed = sorted(name for name, passed in checks.items() if not passed)
        failures.extend(f"{case_id}:{name}" for name in failed)
        cases.append({"id": case_id, "checks": checks, "failed": failed, "details": details})

    positive = EngineProcess(engine_path, artifact_dir, "positive")
    try:
        inventory = positive.initialize(effective_timeouts["uci"])
        required = fixture["inventory"]["required_exact"]
        forbidden = fixture["inventory"]["forbidden_prefixes"]
        inventory_checks = {
            **{f"required:{line}": inventory.count(line) == 1 for line in required},
            **{
                f"forbidden:{prefix}": not any(line.startswith(prefix) for line in inventory)
                for prefix in forbidden
            },
        }
        record("inventory", inventory_checks, {"line_count": len(inventory)})

        nonce = fixture["cases"][0]["nonce"]
        if not NONCE_RE.fullmatch(nonce):
            raise RuntimeError("fixture positive nonce is malformed")
        start = configure_crazyhouse(positive, profile["token"], net_path, nonce)
        positive.wait_stdout(lambda line: line == "readyok", effective_timeouts["ready"])
        lines = positive.stdout_lines[start:]
        ack = exact_ack(profile, nonce)
        route_indices = [i for i, line in enumerate(lines) if line.startswith("info string route_commit status=ok ruleset=crazyhouse ")]
        ack_indices = [i for i, line in enumerate(lines) if line == ack]
        ready_indices = [i for i, line in enumerate(lines) if line == "readyok"]
        record(
            "positive-route-bound",
            {
                "one_route_commit": len(route_indices) == 1,
                "one_exact_ack": len(ack_indices) == 1,
                "one_readyok": len(ready_indices) == 1,
                "exact_order": bool(route_indices and ack_indices and ready_indices)
                and route_indices[0] < ack_indices[0] < ready_indices[0],
            },
            {"nonce": nonce, "stdout": lines},
        )

        second_start = len(positive.stdout_lines)
        positive.send("isready")
        positive.wait_stdout(lambda line: line == "readyok", effective_timeouts["ready"])
        second_lines = positive.stdout_lines[second_start:]
        record(
            "one-shot-readiness",
            {
                "readyok": second_lines.count("readyok") == 1,
                "no_repeated_ack": not any("crazyhouse_capability_ack" in line for line in second_lines),
            },
            {"stdout": second_lines},
        )
    finally:
        positive.finish()

    invalid = EngineProcess(engine_path, artifact_dir, "invalid-uppercase")
    try:
        invalid.initialize(effective_timeouts["uci"])
        invalid_nonce = fixture["cases"][2]["nonce"]
        start = configure_crazyhouse(invalid, profile["token"], net_path, invalid_nonce)
        expected_error = fixture["cases"][2]["expected_error"]
        try:
            invalid.wait_stdout(
                lambda line: line.startswith(expected_error),
                effective_timeouts["invalid_observation"],
            )
        except TimeoutError:
            pass
        time.sleep(0.25)
        lines = invalid.stdout_lines[start:]
        record(
            "invalid-uppercase-nonce",
            {
                "typed_error": any(line.startswith(expected_error) for line in lines),
                "no_ack": not any("crazyhouse_capability_ack" in line for line in lines),
                "readyok_withheld": "readyok" not in lines,
            },
            {"stdout": lines},
        )
    finally:
        invalid.finish()

    missing = EngineProcess(engine_path, artifact_dir, "missing-network")
    try:
        missing.initialize(effective_timeouts["uci"])
        missing_path = artifact_dir / "definitely-missing" / "network.nnue"
        nonce = "fedcba9876543210fedcba9876543210"
        start = configure_crazyhouse(missing, profile["token"], missing_path, nonce)
        error_code = fixture["cases"][3]["expected_error_code"]
        missing.wait_stdout(
            lambda line: f"code={error_code}" in line, effective_timeouts["ready"]
        )
        time.sleep(0.25)
        lines = missing.stdout_lines[start:]
        record(
            "missing-network",
            {
                "typed_error": any(f"code={error_code}" in line for line in lines),
                "no_ack": not any("crazyhouse_capability_ack" in line for line in lines),
                "readyok_withheld": "readyok" not in lines,
            },
            {"stdout": lines},
        )
    finally:
        missing.finish()

    standard = EngineProcess(engine_path, artifact_dir, "standard-control")
    try:
        standard.initialize(effective_timeouts["uci"])
        start = len(standard.stdout_lines)
        standard.send("setoption name UCI_Variant value chess")
        standard.send("isready")
        standard.wait_stdout(lambda line: line == "readyok", effective_timeouts["ready"])
        lines = standard.stdout_lines[start:]
        record(
            "standard-control",
            {
                "readyok": lines.count("readyok") == 1,
                "no_capability_ack": not any("crazyhouse_capability_ack" in line for line in lines),
                "no_error": not any(line.startswith("info string ERROR ") for line in lines),
            },
            {"stdout": lines},
        )
    finally:
        standard.finish()

    summary = {
        "schema": "crazyhouse-engine-capability-handshake-result/v1",
        "engine": {"path": str(engine_path), "bytes": engine_path.stat().st_size, "sha256": sha256(engine_path)},
        "network": {"path": str(net_path), "bytes": net_path.stat().st_size, "sha256": sha256(net_path)},
        "fixture": {"path": str(fixture_path), "bytes": fixture_path.stat().st_size, "sha256": sha256(fixture_path)},
        "cases": cases,
        "failures": failures,
        "passed": not failures,
        "timeout_scale": timeout_scale,
        "effective_timeouts_seconds": effective_timeouts,
        "strength_claim": False,
        "openbench_evidence": False,
    }
    (artifact_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({"passed": not failures, "failures": failures}, sort_keys=True))
    return 0 if not failures else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--timeout-scale", type=float, default=1.0)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except Exception as error:
        print(f"ERROR capability harness: {error}", file=sys.stderr)
        raise SystemExit(2)
