#!/usr/bin/env python3
"""End-to-end verifier for the frozen transactional Crazyhouse UCI route."""

from __future__ import annotations

import argparse
import hashlib
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable


class VerificationFailure(RuntimeError):
    pass


PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PROFILE_TOKEN = f"{PROFILE_ID}@{PROFILE_SHA256}"
HISTORICAL_OFFICIAL_NETWORK_SHA256 = (
    "ab28990d4ea3d5c97f7d3918bc5dd5061609330369fe00c2d93a34d4777b5552"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class UciProcess:
    def __init__(self, executable: Path) -> None:
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.stdout_queue: queue.Queue[str] = queue.Queue()
        self.stdout_all: list[str] = []
        self.stderr_all: list[str] = []
        self.stdout_thread = threading.Thread(
            target=self._read_lines,
            args=(self.process.stdout, self.stdout_queue, self.stdout_all),
            daemon=True,
        )
        self.stderr_thread = threading.Thread(
            target=self._read_lines,
            args=(self.process.stderr, None, self.stderr_all),
            daemon=True,
        )
        self.stdout_thread.start()
        self.stderr_thread.start()

    @staticmethod
    def _read_lines(stream, target_queue: queue.Queue[str] | None, sink: list[str]) -> None:
        for raw in stream:
            line = raw.rstrip("\r\n")
            sink.append(line)
            if target_queue is not None:
                target_queue.put(line)

    def send(self, command: str) -> None:
        require(self.process.poll() is None, f"engine exited before command: {command}")
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def wait_for(self, predicate: Callable[[str], bool], description: str, timeout: float = 45.0) -> list[str]:
        deadline = time.monotonic() + timeout
        observed: list[str] = []
        while time.monotonic() < deadline:
            if self.process.poll() is not None and self.stdout_queue.empty():
                raise VerificationFailure(
                    f"engine exited {self.process.returncode} while waiting for {description}; "
                    f"stdout={observed!r} stderr={self.stderr_all!r}"
                )
            try:
                line = self.stdout_queue.get(timeout=min(0.1, max(deadline - time.monotonic(), 0.01)))
            except queue.Empty:
                continue
            observed.append(line)
            if predicate(line):
                return observed
        raise VerificationFailure(
            f"timeout waiting for {description}; stdout={observed!r} stderr={self.stderr_all!r}"
        )

    def drain(self, duration: float = 0.2) -> list[str]:
        deadline = time.monotonic() + duration
        observed: list[str] = []
        while time.monotonic() < deadline:
            try:
                observed.append(self.stdout_queue.get(timeout=0.02))
            except queue.Empty:
                pass
        return observed

    def close(self, expect_stderr_empty: bool = True) -> None:
        if self.process.poll() is None:
            self.send("quit")
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired as exc:
            self.process.kill()
            self.process.wait(timeout=5)
            raise VerificationFailure("engine did not exit after quit") from exc
        self.stdout_thread.join(timeout=2)
        self.stderr_thread.join(timeout=2)
        require(self.process.returncode == 0, f"engine exit code {self.process.returncode}")
        if expect_stderr_empty:
            require(not self.stderr_all, f"unexpected engine stderr: {self.stderr_all!r}")


def setoption(proc: UciProcess, name: str, value: str) -> None:
    proc.send(f"setoption name {name} value {value}")


def wait_ready_success(proc: UciProcess, ruleset: str) -> list[str]:
    proc.send("isready")
    lines = proc.wait_for(
        lambda line: line == "readyok" or "READY state=failed" in line,
        f"successful {ruleset} readiness",
        90,
    )
    require(lines[-1] == "readyok", f"{ruleset} did not become ready: {lines!r}")
    require(
        not any("crazyhouse_capability_ack" in line for line in lines),
        f"nonce-free routing replay emitted a Crazyhouse capability acknowledgement: {lines!r}",
    )
    commits = [
        line
        for line in lines
        if "info string route_commit status=ok" in line and f"ruleset={ruleset}" in line
    ]
    require(commits, f"{ruleset} readyok lacked authenticated route commit: {lines!r}")
    expected_profile = (
        f"profile={PROFILE_ID} profile_sha256={PROFILE_SHA256}"
        if ruleset == "crazyhouse"
        else "profile=none profile_sha256=none"
    )
    require(
        all(expected_profile in line for line in commits),
        f"{ruleset} route commit profile acknowledgement drifted: {commits!r}",
    )
    return lines


def wait_ready_failure(proc: UciProcess, code: str) -> list[str]:
    proc.send("isready")
    lines = proc.wait_for(
        lambda line: line == "readyok" or "info string READY state=failed" in line,
        f"failed readiness code={code}",
        90,
    )
    lines += proc.drain()
    require(any(f"code={code}" in line for line in lines), f"missing failure code {code}: {lines!r}")
    require("readyok" not in lines, f"failed readiness emitted readyok: {lines!r}")
    require(
        not any("crazyhouse_capability_ack" in line for line in lines),
        f"failed nonce-free readiness emitted a Crazyhouse capability acknowledgement: {lines!r}",
    )
    require(
        any("readyok_withheld=1" in line for line in lines),
        f"failed readiness lacked explicit withholding marker: {lines!r}",
    )
    return lines


def wait_position_failure(proc: UciProcess, command: str, code: str) -> list[str]:
    proc.send(command)
    return proc.wait_for(
        lambda line: "info string ERROR position" in line and f"code={code}" in line,
        f"position failure code={code}",
    )


def wait_perft(proc: UciProcess, depth: int, nodes: int) -> list[str]:
    proc.send(f"go perft {depth}")
    lines = proc.wait_for(lambda line: line.startswith("Nodes searched:"), f"perft {depth}", 60)
    require(lines[-1] == f"Nodes searched: {nodes}", f"perft mismatch: {lines[-1]!r}")
    return lines


def wait_command_error(proc: UciProcess, command: str, code: str) -> list[str]:
    proc.send(command)
    lines = proc.wait_for(
        lambda line: f"info string ERROR {command.split()[0]}" in line and f"code={code}" in line,
        f"{command} refusal code={code}",
        30,
    )
    lines += proc.drain()
    require(not any(line.startswith("bestmove ") for line in lines), f"refused command searched: {lines!r}")
    return lines


def inventory_scenario(
    engine: Path,
    contract: dict,
    expected_eval_file_line: str,
    expected_option_count: int,
    expected_ordered_names: list[str],
    required_option_lines: list[str],
) -> list[str]:
    proc = UciProcess(engine)
    try:
        proc.send("uci")
        lines = proc.wait_for(lambda line: line == "uciok", "uciok")
        option_lines = [line for line in lines if line.startswith("option name ")]
        names = []
        for line in option_lines:
            prefix, _, remainder = line.partition(" type ")
            require(remainder, f"malformed option line: {line}")
            names.append(prefix.removeprefix("option name "))
        require(names == expected_ordered_names, f"option order mismatch: {names!r}")
        require(len(option_lines) == expected_option_count, f"option count mismatch: {len(option_lines)}")
        require(contract["options"]["uci_variant_line"] in option_lines, "UCI_Variant contract mismatch")
        require(
            contract["options"]["crazyhouse_eval_file_line"] in option_lines,
            "CrazyhouseEvalFile contract mismatch",
        )
        require(
            contract["options"]["crazyhouse_profile_line"] in option_lines,
            "CrazyhouseProfile contract mismatch",
        )
        require(expected_eval_file_line in option_lines, "EvalFile default contract mismatch")
        for required_line in required_option_lines:
            require(required_line in option_lines, f"required option line missing: {required_line}")
        return ["inventory=PASS"]
    finally:
        proc.close()


def initial_failure_scenario(engine: Path) -> list[str]:
    proc = UciProcess(engine)
    try:
        lines = wait_ready_failure(proc, "crazyhouse_eval_file_empty")
        return [next(line for line in lines if "code=crazyhouse_eval_file_empty" in line)]
    finally:
        proc.close()


def invalid_variant_scenario(engine: Path, legacy: Path) -> list[str]:
    proc = UciProcess(engine)
    try:
        setoption(proc, "UCI_Variant", "atomic")
        lines = wait_ready_failure(proc, "invalid_variant")
        setoption(proc, "UCI_Variant", "crazyhouse")
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        wait_ready_success(proc, "crazyhouse")
        return [next(line for line in lines if "code=invalid_variant" in line)]
    finally:
        proc.close()


def profile_failure_scenario(engine: Path, legacy: Path) -> list[str]:
    proc = UciProcess(engine)
    markers: list[str] = []
    try:
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        markers += wait_ready_success(proc, "crazyhouse")
        proc.send("position startpos")
        markers += wait_perft(proc, 1, 20)

        setoption(proc, "CrazyhouseProfile", "<empty>")
        missing = wait_ready_failure(proc, "crazyhouse_profile_missing")
        markers.append(next(line for line in missing if "code=crazyhouse_profile_missing" in line))
        revoked = wait_position_failure(
            proc, "position startpos", "position_requires_committed_route"
        )
        markers.append(revoked[-1])

        setoption(proc, "CrazyhouseProfile", f"UNKNOWN_PROFILE@{PROFILE_SHA256}")
        unknown = wait_ready_failure(proc, "crazyhouse_profile_unknown")
        markers.append(next(line for line in unknown if "code=crazyhouse_profile_unknown" in line))

        setoption(proc, "CrazyhouseProfile", f"{PROFILE_ID}@0")
        mismatch = wait_ready_failure(proc, "crazyhouse_profile_hash_mismatch")
        markers.append(
            next(line for line in mismatch if "code=crazyhouse_profile_hash_mismatch" in line)
        )

        setoption(proc, "CrazyhouseProfile", PROFILE_TOKEN)
        markers += wait_ready_success(proc, "crazyhouse")
        return [line for line in markers if "route_commit" in line or "code=" in line]
    finally:
        proc.close()


def crazyhouse_rule_route_scenario(
    engine: Path, legacy: Path, search_context: dict
) -> list[str]:
    proc = UciProcess(engine)
    markers: list[str] = []
    try:
        if search_context["enabled"]:
            for name, value in search_context["fixed_options"].items():
                setoption(proc, name, str(value).lower() if isinstance(value, bool) else str(value))
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        markers += wait_ready_success(proc, "crazyhouse")
        required_route_token = search_context.get("required_route_token")
        if required_route_token is not None:
            route_commits = [
                line
                for line in markers
                if "route_commit status=ok ruleset=crazyhouse" in line
            ]
            telemetry_label = "SIMD" if "incremental-simd" in required_route_token else "incremental"
            require(
                route_commits
                and all(required_route_token in line for line in route_commits),
                f"missing engine-authored {telemetry_label} route telemetry",
            )
        proc.send("position startpos")
        markers += wait_perft(proc, 1, 20)
        if search_context["enabled"]:
            proc.send(search_context["go_command"])
            search = proc.wait_for(
                lambda line: line.startswith("bestmove ")
                or ("info string ERROR go" in line and "code=" in line),
                "Crazyhouse worker bestmove",
                90,
            )
            require(
                search[-1].startswith("bestmove "),
                f"Crazyhouse bounded search remained refused: {search!r}",
            )
            require(
                any(line.startswith("info ") and " depth 1 " in line for line in search),
                f"Crazyhouse search lacked a depth-1 information line: {search!r}",
            )
            bestmove = search[-1].split()[1]
            require(
                bestmove in search_context["allowed_bestmoves"],
                f"Crazyhouse search returned an illegal start move: {bestmove!r}",
            )
            markers.append(search[-1])
        else:
            markers += wait_command_error(proc, "go depth 1", "crazyhouse_search_not_bound")
        markers += wait_command_error(proc, "eval", "crazyhouse_eval_not_bound")
        markers += wait_command_error(proc, "bench", "crazyhouse_bench_not_bound")
        markers += wait_command_error(proc, "speedtest", "crazyhouse_speedtest_not_bound")
        markers += wait_command_error(proc, "export_net", "crazyhouse_export_net_not_bound")
        return [
            line
            for line in markers
            if "route_commit" in line
            or "code=" in line
            or line.startswith("Nodes")
            or line.startswith("bestmove ")
        ]
    finally:
        proc.close()


def crossed_routes_scenario(engine: Path, legacy: Path, official: Path) -> list[str]:
    markers: list[str] = []
    proc = UciProcess(engine)
    try:
        setoption(proc, "CrazyhouseEvalFile", str(official))
        lines = wait_ready_failure(proc, "legacy_oversized_file")
        markers.append(next(line for line in lines if "code=legacy_oversized_file" in line))
    finally:
        proc.close()

    proc = UciProcess(engine)
    try:
        setoption(proc, "UCI_Variant", "chess")
        setoption(proc, "EvalFile", str(legacy))
        lines = wait_ready_failure(proc, "official_eval_not_loaded")
        markers.append(next(line for line in lines if "code=official_eval_not_loaded" in line))
    finally:
        proc.close()
    return markers


def failed_replacement_scenario(engine: Path, legacy: Path) -> list[str]:
    proc = UciProcess(engine)
    markers: list[str] = []
    missing = Path(str(legacy) + ".routing-fixture-missing")
    require(not missing.exists(), f"missing fixture unexpectedly exists: {missing}")
    try:
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        wait_ready_success(proc, "crazyhouse")
        proc.send("position startpos")
        wait_perft(proc, 1, 20)

        setoption(proc, "CrazyhouseEvalFile", str(missing))
        failed = wait_ready_failure(proc, "legacy_missing_file")
        markers.append(next(line for line in failed if "code=legacy_missing_file" in line))

        proc.send("position startpos")
        wait_perft(proc, 1, 20)

        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        wait_ready_success(proc, "crazyhouse")
        proc.send("go perft 1")
        invalidated = proc.wait_for(
            lambda line: "info string ERROR go" in line and "code=position_epoch_invalid" in line,
            "perft-only position invalidation after backend recovery",
        )
        markers.append(invalidated[-1])
        proc.send("position startpos")
        wait_perft(proc, 1, 20)
        return markers
    finally:
        proc.close()


def chess960_scenario(engine: Path, legacy: Path) -> list[str]:
    proc = UciProcess(engine)
    try:
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        setoption(proc, "UCI_Chess960", "true")
        failed = wait_ready_failure(proc, "crazyhouse_chess960_rejected")
        setoption(proc, "UCI_Chess960", "false")
        wait_ready_success(proc, "crazyhouse")
        return [next(line for line in failed if "code=crazyhouse_chess960_rejected" in line)]
    finally:
        proc.close()


def position_transaction_scenario(engine: Path, legacy: Path) -> list[str]:
    proc = UciProcess(engine)
    markers: list[str] = []
    try:
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        wait_ready_success(proc, "crazyhouse")
        proc.send("position startpos")
        wait_perft(proc, 1, 20)

        malformed = wait_position_failure(proc, "position startpos e2e4", "malformed_position")
        markers.append(malformed[-1])
        proc.send("go perft 1")
        invalid = proc.wait_for(
            lambda line: "info string ERROR go" in line and "code=position_epoch_invalid" in line,
            "position epoch invalidation after malformed command",
        )
        markers.append(invalid[-1])

        proc.send("position startpos")
        wait_perft(proc, 1, 20)
        illegal = wait_position_failure(
            proc,
            "position startpos moves e2e4 e7e5 e4e5",
            "illegal_move",
        )
        markers.append(illegal[-1])
        proc.send("go perft 1")
        proc.wait_for(
            lambda line: "info string ERROR go" in line and "code=position_epoch_invalid" in line,
            "position epoch invalidation after illegal suffix",
        )
        proc.send("position startpos moves e2e4 e7e5")
        wait_perft(proc, 1, 29)
        return markers
    finally:
        proc.close()


def option_persistence_scenario(engine: Path, legacy: Path, official: Path) -> list[str]:
    proc = UciProcess(engine)
    markers: list[str] = []
    try:
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        markers += wait_ready_success(proc, "crazyhouse")
        setoption(proc, "UCI_Variant", "chess")
        setoption(proc, "EvalFile", str(official))
        markers += wait_ready_success(proc, "chess")
        setoption(proc, "CrazyhouseProfile", f"{PROFILE_ID}@0")
        markers += wait_ready_success(proc, "chess")
        proc.send("ucinewgame")
        setoption(proc, "UCI_Variant", "crazyhouse")
        failed = wait_ready_failure(proc, "crazyhouse_profile_hash_mismatch")
        markers.append(
            next(line for line in failed if "code=crazyhouse_profile_hash_mismatch" in line)
        )
        setoption(proc, "CrazyhouseProfile", PROFILE_TOKEN)
        markers += wait_ready_success(proc, "crazyhouse")
        return [line for line in markers if "route_commit status=ok" in line or "code=" in line]
    finally:
        proc.close()


def chess_control_scenario(engine: Path, official: Path) -> list[str]:
    proc = UciProcess(engine)
    markers: list[str] = []
    try:
        setoption(proc, "UCI_Variant", "chess")
        setoption(proc, "EvalFile", str(official))
        markers += wait_ready_success(proc, "chess")
        proc.send("position startpos")
        markers += wait_perft(proc, 1, 20)
        proc.send("go depth 1")
        search = proc.wait_for(lambda line: line.startswith("bestmove "), "chess bestmove", 60)
        require(search[-1] != "bestmove (none)", f"chess search returned no move: {search!r}")
        markers.append(search[-1])
        return [line for line in markers if "route_commit" in line or line.startswith("Nodes") or line.startswith("bestmove")]
    finally:
        proc.close()


def load_upstream_context(
    contract_path: Path, contract: dict, addendum_path: Path | None
) -> dict:
    if addendum_path is None:
        return {
            "expected_option_count": len(contract["options"]["ordered_names"]),
            "expected_eval_file_line": (
                "option name EvalFile type string default "
                + contract["options"]["eval_file_default"]
            ),
            "official_network": {"sha256": HISTORICAL_OFFICIAL_NETWORK_SHA256},
        }

    addendum = json.loads(addendum_path.read_text(encoding="utf-8"))
    require(
        addendum["schema"] == "crazyhouse-uci-routing-contract-addendum/v1",
        "routing addendum schema mismatch",
    )
    require(addendum["addendum"] == 1, "routing addendum number mismatch")

    base_pin = addendum["pins"]["routing_contract_v1"]
    require(contract_path.stat().st_size == base_pin["bytes"], "base contract size mismatch")
    require(sha256_file(contract_path) == base_pin["sha256"], "base contract SHA-256 mismatch")
    require(
        contract["official_source_commit"]
        == addendum["single_variable_transition"]["from"]["official_source_commit"],
        "routing addendum does not apply to this base contract",
    )

    control_pin = addendum["pins"]["upstream_standard_control_v1"]
    control_path = addendum_path.parent / Path(control_pin["path"]).name
    require(control_path.is_file(), f"upstream standard-control contract missing: {control_path}")
    require(control_path.stat().st_size == control_pin["bytes"], "standard-control size mismatch")
    require(
        sha256_file(control_path) == control_pin["sha256"],
        "standard-control SHA-256 mismatch",
    )
    control = json.loads(control_path.read_text(encoding="utf-8"))

    transition = addendum["single_variable_transition"]["to"]
    official = addendum["official_network"]
    require(control["official"]["commit"] == transition["official_source_commit"], "official commit drift")
    require(control["official"]["tree"] == transition["official_source_tree"], "official tree drift")
    require(control["network"]["filename"] == official["filename"], "official filename drift")
    require(
        official["sha256"] == transition["official_network_sha256"],
        "official network transition drift",
    )
    require(
        control["network"]["sha256_prefix"] == official["sha256"][:12],
        "official network prefix drift",
    )
    require(
        addendum["option_inventory"]["expected_eval_file_line"]
        == f"option name EvalFile type string default {official['filename']}",
        "addendum EvalFile line drift",
    )
    require(addendum["routing_scenarios"]["expected_count"] == 11, "scenario count drift")

    return {
        "expected_option_count": addendum["option_inventory"]["expected_count"],
        "expected_eval_file_line": addendum["option_inventory"]["expected_eval_file_line"],
        "official_network": official,
    }


def resolve_addendum_pin(addendum_path: Path, pin: dict) -> Path:
    relative = Path(pin["path"])
    if relative.parts and relative.parts[0] == "..":
        return (addendum_path.parent / relative).resolve()
    return (addendum_path.parents[2] / relative).resolve()


def authenticate_addendum_pin(addendum_path: Path, pin_name: str, pin: dict) -> Path:
    path = resolve_addendum_pin(addendum_path, pin)
    require(path.is_file(), f"capability pin missing ({pin_name}): {path}")
    require(path.stat().st_size == pin["bytes"], f"capability pin size mismatch: {pin_name}")
    require(sha256_file(path) == pin["sha256"], f"capability pin SHA-256 mismatch: {pin_name}")
    return path


def load_capability_context(
    contract: dict,
    upstream_addendum_path: Path | None,
    worker_addendum_path: Path | None,
    capability_addendum_path: Path | None,
    upstream_context: dict,
    worker_context: dict,
) -> dict:
    if capability_addendum_path is None:
        return {
            "enabled": False,
            "expected_option_count": upstream_context["expected_option_count"],
            "expected_ordered_names": contract["options"]["ordered_names"],
            "required_option_lines": [],
            "engine": None,
        }

    require(upstream_addendum_path is not None, "capability routing requires upstream addendum 001")
    require(worker_addendum_path is not None, "capability routing requires Worker addendum 011")
    addendum = json.loads(capability_addendum_path.read_text(encoding="utf-8"))
    require(
        addendum["schema"] == "crazyhouse-uci-routing-capability-addendum/v1",
        "capability routing addendum schema mismatch",
    )
    require(addendum["addendum"] == 12, "capability routing addendum number mismatch")

    pins = addendum["pins"]
    require(
        set(pins)
        == {
            "routing_addendum_011",
            "upstream_addendum_001",
            "multipv_addendum_007",
            "capability_fixture",
            "capability_adr",
            "expected_red_record",
            "engine_capability_end_receipt",
        },
        "capability routing pin inventory mismatch",
    )
    authenticated = {
        name: authenticate_addendum_pin(capability_addendum_path, name, pin)
        for name, pin in pins.items()
    }
    require(
        authenticated["routing_addendum_011"] == worker_addendum_path.resolve(),
        "capability routing Worker addendum path mismatch",
    )
    require(
        authenticated["upstream_addendum_001"] == upstream_addendum_path.resolve(),
        "capability routing upstream addendum path mismatch",
    )

    expected_red = json.loads(authenticated["expected_red_record"].read_text(encoding="utf-8"))
    require(expected_red["record"] == 165, "capability routing expected-red record mismatch")
    require(
        expected_red["result"] == "EXPECTED_RED_ROUTING_OPTION_INVENTORY_STALE"
        and expected_red["invocation"]["exit_code"] == 1
        and expected_red["expected_red"]["frozen_expected_count"] == 22
        and expected_red["expected_red"]["observed_count"] == 24
        and expected_red["expected_red"]["failure_before_runtime_scenarios"] is True
        and expected_red["expected_red"]["fallback_observed"] is False,
        "capability routing expected-red semantics mismatch",
    )

    multipv = json.loads(authenticated["multipv_addendum_007"].read_text(encoding="utf-8"))
    require(
        multipv["schema"] == "crazyhouse-g3-multipv-contract-addendum/v1"
        and multipv["addendum"] == 7
        and multipv["runtime_admission"]["result"]
        == "PASS_CRAZYHOUSE_MULTIPV_PROTOCOL_BOUNDARY"
        and multipv["runtime_admission"]["fallback_observed"] is False,
        "capability routing MultiPV provenance mismatch",
    )

    fixture = json.loads(authenticated["capability_fixture"].read_text(encoding="utf-8"))
    capability_line = "option name CrazyhouseCapabilityNonce type string default <empty>"
    multipv_line = "option name CrazyhouseMultiPV type spin default 0 min 0 max 2147483647"
    require(
        fixture["schema"] == "crazyhouse-engine-capability-handshake/v1"
        and fixture["profile"]["token"] == PROFILE_TOKEN
        and fixture["network"] == contract["legacy_network"]
        and capability_line in fixture["inventory"]["required_exact"]
        and fixture["strength_claim"] is False,
        "capability handshake fixture mismatch",
    )

    end_receipt = json.loads(
        authenticated["engine_capability_end_receipt"].read_text(encoding="utf-8")
    )
    source = end_receipt["source"]
    validated_source = addendum["validated_source"]
    require(
        end_receipt["schema"] == "crazyhouse-resource-handoff-end/v1"
        and end_receipt["result"] == "PASS_ENGINE_CAPABILITY_HANDSHAKE_GREEN"
        and end_receipt["strength_claim"] is False
        and source["commit"] == validated_source["behavior_commit"]
        and source["tree"] == validated_source["behavior_tree"]
        and source["src_tree"] == validated_source["product_src_tree"]
        and source["fixture_commit"] == validated_source["fixture_commit"]
        and source["official_stockfish_ancestor"]
        == validated_source["official_stockfish_ancestor"]
        and validated_source["fairy_stockfish_source_allowed"] is False
        and source["fairy_stockfish_source_allowed"] is False,
        "capability engine receipt lineage mismatch",
    )
    capability_result = end_receipt["capability_handshake"]
    require(
        capability_result["inventory"] == "PASS_24_OPTIONS_EXACT_CAPABILITY_DECLARATION"
        and capability_result["positive_route_bound"]
        == "PASS_ROUTE_COMMIT_THEN_EXACT_ACK_THEN_READYOK"
        and capability_result["one_shot_readiness"] == "PASS_NO_REPEATED_ACK"
        and capability_result["failures"] == []
        and capability_result["stderr_bytes"] == 0
        and capability_result["timeouts"] == 0
        and capability_result["crashes"] == 0,
        "capability engine receipt result mismatch",
    )

    option_inventory = addendum["option_inventory"]
    expected_names = list(contract["options"]["ordered_names"])
    expected_names.insert(expected_names.index("MultiPV") + 1, "CrazyhouseMultiPV")
    expected_names.insert(
        expected_names.index("CrazyhouseProfile") + 1, "CrazyhouseCapabilityNonce"
    )
    require(
        option_inventory["base_count"] == len(contract["options"]["ordered_names"]) == 22
        and option_inventory["expected_count"] == len(expected_names) == 24
        and option_inventory["ordered_names"] == expected_names
        and len(set(expected_names)) == len(expected_names)
        and option_inventory["added_option_lines"] == [multipv_line, capability_line],
        "capability-aware option inventory mismatch",
    )
    unchanged_lines = option_inventory["unchanged_required_lines"]
    require(
        contract["options"]["uci_variant_line"] in unchanged_lines
        and contract["options"]["crazyhouse_profile_line"] in unchanged_lines
        and contract["options"]["crazyhouse_eval_file_line"] in unchanged_lines
        and upstream_context["expected_eval_file_line"] in unchanged_lines,
        "capability addendum changed a required routing option line",
    )

    control = addendum["routing_control"]
    require(
        worker_context["enabled"] is True
        and control["scenario_count"] == 11
        and control["marker_count"] == worker_context["expected_markers"] == 32
        and control["expected_protocol_sha256"] == worker_context["expected_protocol"]
        and control["required_binding"] == worker_context["binding"]
        and control["required_route_token"] == worker_context["required_route_token"]
        and control["successful_crazyhouse_route_commits"]
        == worker_context["expected_crazyhouse_commits"]
        and control["capability_nonce_set_during_replay"] is False
        and control["capability_ack_expected_during_replay"] is False
        and control["routing_markers_changed"] is False
        and control["product_behavior_changed_by_this_addendum"] is False,
        "capability addendum changed the frozen routing control",
    )
    extension = addendum["verifier_extension"]
    require(
        extension["new_cli_argument"] == "--capability-addendum"
        and all(
            extension[key] is True
            for key in (
                "opt_in",
                "historical_invocations_remain_supported",
                "must_authenticate_every_pin",
                "must_require_exact_24_option_order",
                "must_require_both_added_option_lines",
                "must_preserve_all_legacy_scenarios",
                "must_preserve_protocol_digest",
                "must_not_set_capability_nonce",
            )
        ),
        "capability verifier-extension contract mismatch",
    )
    expected_red_contract = addendum["expected_red"]
    require(
        expected_red_contract["record"] == 165
        and expected_red_contract["required_failure"] == "option order mismatch"
        and expected_red_contract["engine_started"] is True
        and expected_red_contract["runtime_scenarios_started"] is False
        and expected_red_contract["fallback_observed"] is False
        and all(
            addendum[key] is False
            for key in (
                "timing_evidence",
                "strength_claim",
                "openbench_evidence",
                "release_claim",
            )
        ),
        "capability routing claim boundary mismatch",
    )
    return {
        "enabled": True,
        "expected_option_count": option_inventory["expected_count"],
        "expected_ordered_names": option_inventory["ordered_names"],
        "required_option_lines": option_inventory["added_option_lines"],
        "engine": end_receipt["build"]["engine"],
    }


def load_worker_search_context(
    contract_path: Path,
    upstream_addendum_path: Path | None,
    worker_addendum_path: Path | None,
) -> dict:
    if worker_addendum_path is None:
        return {"enabled": False}

    require(upstream_addendum_path is not None, "worker search requires upstream addendum 001")
    addendum = json.loads(worker_addendum_path.read_text(encoding="utf-8"))
    require(
        addendum["schema"] == "crazyhouse-worker-search-routing-addendum/v1",
        "worker-search routing addendum schema mismatch",
    )
    require(
        addendum["addendum"] in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
        "worker-search routing addendum number mismatch",
    )

    correction = None
    correction_path = None
    default_scalar_control = None
    default_scalar_control_path = None
    default_scalar_provenance = None
    default_scalar_provenance_path = None
    failed_route_provenance = None
    failed_route_provenance_path = None
    scoped_route_provenance = None
    scoped_route_provenance_path = None
    rule_only_provenance = None
    rule_only_provenance_path = None
    if addendum["addendum"] == 11:
        rule_only_provenance = addendum
        rule_only_provenance_path = worker_addendum_path
        require(
            rule_only_provenance_path.stat().st_size == 4125
            and sha256_file(rule_only_provenance_path)
            == "0e34bdd446aa0fb03a9ef6fc8efcca434709513598508d1566d2fdb0ed40e7e5",
            "rule-only routing addendum 011 identity mismatch",
        )
        prior_pin = rule_only_provenance["pins"]["routing_addendum_010"]
        prior_path = rule_only_provenance_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"scoped-route addendum 010 missing: {prior_path}")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "scoped-route addendum 010 identity mismatch",
        )
        expected_red_pin = rule_only_provenance["pins"][
            "rule_only_expected_red_record"
        ]
        expected_red_path = (
            rule_only_provenance_path.parent / expected_red_pin["path"]
        ).resolve()
        require(expected_red_path.is_file(), "rule-only expected-red record missing")
        require(
            expected_red_path.stat().st_size == expected_red_pin["bytes"]
            and sha256_file(expected_red_path) == expected_red_pin["sha256"],
            "rule-only expected-red record identity mismatch",
        )
        expected_red_record = json.loads(expected_red_path.read_text(encoding="utf-8"))
        require(
            expected_red_record["record_id"] == 148
            and expected_red_record["result"] == "PASS_EXPECTED_RED"
            and expected_red_record["observed"]["rule_only_perft_allowed"] is False
            and expected_red_record["observed"]["search_error"]
            == "legacy_simd_unavailable"
            and expected_red_record["observed"]["scalar_fallback"] is False,
            "rule-only expected-red observation mismatch",
        )
        addendum = json.loads(prior_path.read_text(encoding="utf-8"))
        require(addendum["addendum"] == 10, "rule-only provenance predecessor mismatch")
        worker_addendum_path = prior_path
    if addendum["addendum"] == 10:
        scoped_route_provenance = addendum
        scoped_route_provenance_path = worker_addendum_path
        require(
            scoped_route_provenance_path.stat().st_size == 3636
            and sha256_file(scoped_route_provenance_path)
            == "3837b517ebd9f9050230943f1654c04a91d52efcd0253aec863c6d3fa38d1c45",
            "scoped-route routing addendum 010 identity mismatch",
        )
        prior_pin = scoped_route_provenance["pins"]["routing_addendum_009"]
        prior_path = scoped_route_provenance_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"failed-route addendum 009 missing: {prior_path}")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "failed-route addendum 009 identity mismatch",
        )
        rejection_pin = scoped_route_provenance["pins"][
            "overbroad_route_rejection_record"
        ]
        rejection_path = (
            scoped_route_provenance_path.parent / rejection_pin["path"]
        ).resolve()
        require(rejection_path.is_file(), "overbroad-route rejection record missing")
        require(
            rejection_path.stat().st_size == rejection_pin["bytes"]
            and sha256_file(rejection_path) == rejection_pin["sha256"],
            "overbroad-route rejection record identity mismatch",
        )
        addendum = json.loads(prior_path.read_text(encoding="utf-8"))
        require(addendum["addendum"] == 9, "scoped-route provenance predecessor mismatch")
        worker_addendum_path = prior_path
    if addendum["addendum"] == 9:
        failed_route_provenance = addendum
        failed_route_provenance_path = worker_addendum_path
        require(
            failed_route_provenance_path.stat().st_size == 3479
            and sha256_file(failed_route_provenance_path)
            == "4f0a29327b17682ba30cd8a9bf23d83d834f739dd967b7e02e19e85e48bc41df",
            "failed-route routing addendum 009 identity mismatch",
        )
        prior_pin = failed_route_provenance["pins"]["routing_addendum_008"]
        prior_path = failed_route_provenance_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"default-scalar addendum 008 missing: {prior_path}")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "default-scalar addendum 008 identity mismatch",
        )
        expected_red_pin = failed_route_provenance["pins"][
            "failed_route_expected_red_record"
        ]
        expected_red_path = (
            failed_route_provenance_path.parent / expected_red_pin["path"]
        ).resolve()
        require(expected_red_path.is_file(), "failed-route expected-red record missing")
        require(
            expected_red_path.stat().st_size == expected_red_pin["bytes"]
            and sha256_file(expected_red_path) == expected_red_pin["sha256"],
            "failed-route expected-red record identity mismatch",
        )
        addendum = json.loads(prior_path.read_text(encoding="utf-8"))
        require(addendum["addendum"] == 8, "failed-route provenance predecessor mismatch")
        worker_addendum_path = prior_path
    if addendum["addendum"] == 8:
        default_scalar_provenance = addendum
        default_scalar_provenance_path = worker_addendum_path
        require(
            default_scalar_provenance_path.stat().st_size == 3243
            and sha256_file(default_scalar_provenance_path)
            == "e0f7cb5b9a38402c9d3447d4672b86512a8a35e4dfce625f1bf36d0227bd5ee4",
            "default-scalar routing addendum 008 identity mismatch",
        )
        control_pin = default_scalar_provenance["pins"]["routing_addendum_007"]
        control_path = (
            default_scalar_provenance_path.parent / Path(control_pin["path"]).name
        )
        require(control_path.is_file(), f"default-scalar addendum 007 missing: {control_path}")
        require(
            control_path.stat().st_size == control_pin["bytes"]
            and sha256_file(control_path) == control_pin["sha256"],
            "default-scalar addendum 007 identity mismatch",
        )
        addendum = json.loads(control_path.read_text(encoding="utf-8"))
        require(addendum["addendum"] == 7, "default-scalar provenance predecessor mismatch")
        worker_addendum_path = control_path
    if addendum["addendum"] == 7:
        default_scalar_control = addendum
        default_scalar_control_path = worker_addendum_path
        require(
            default_scalar_control_path.stat().st_size == 4094
            and sha256_file(default_scalar_control_path)
            == "7ed6af44e43b2ea8a32c50c2e2705bb39840acd9e73eb89c3da018facab7c676",
            "default-scalar routing addendum 007 identity mismatch",
        )
        correction_pin = default_scalar_control["pins"]["routing_addendum_006"]
        correction_path = (
            default_scalar_control_path.parent / Path(correction_pin["path"]).name
        )
        require(
            correction_path.is_file(),
            f"SIMD routing correction addendum 006 missing: {correction_path}",
        )
        require(
            correction_path.stat().st_size == correction_pin["bytes"]
            and sha256_file(correction_path) == correction_pin["sha256"],
            "SIMD routing correction addendum 006 identity mismatch",
        )
        addendum = json.loads(correction_path.read_text(encoding="utf-8"))
        require(addendum["addendum"] == 6, "default-scalar control predecessor mismatch")
        worker_addendum_path = correction_path
    if addendum["addendum"] == 6:
        correction = addendum
        correction_path = worker_addendum_path
        require(
            correction_path.stat().st_size == 4620
            and sha256_file(correction_path)
            == "07c85a908eae2e692490bf40b98d1dcb3e36a3b26adb82912e32331d16272c39",
            "SIMD routing correction addendum 006 identity mismatch",
        )
        prior_pin = correction["pins"]["routing_addendum_005"]
        prior_path = correction_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"SIMD routing addendum 005 missing: {prior_path}")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "SIMD routing addendum 005 identity mismatch",
        )
        addendum = json.loads(prior_path.read_text(encoding="utf-8"))
        require(addendum["addendum"] == 5, "SIMD routing correction predecessor mismatch")
        worker_addendum_path = prior_path

    base_pin = addendum["pins"]["routing_contract_v1"]
    require(contract_path.stat().st_size == base_pin["bytes"], "worker addendum base size mismatch")
    require(
        sha256_file(contract_path) == base_pin["sha256"],
        "worker addendum base SHA-256 mismatch",
    )
    upstream_pin = addendum["pins"]["routing_addendum_001"]
    require(
        upstream_addendum_path.stat().st_size == upstream_pin["bytes"],
        "upstream routing addendum size mismatch",
    )
    require(
        sha256_file(upstream_addendum_path) == upstream_pin["sha256"],
        "upstream routing addendum SHA-256 mismatch",
    )

    worker_pin = addendum["pins"]["worker_search_contract_v1"]
    worker_path = worker_addendum_path.parent / Path(worker_pin["path"]).name
    require(worker_path.is_file(), f"worker-search contract missing: {worker_path}")
    require(worker_path.stat().st_size == worker_pin["bytes"], "worker contract size mismatch")
    require(
        sha256_file(worker_path) == worker_pin["sha256"],
        "worker contract SHA-256 mismatch",
    )
    worker = json.loads(worker_path.read_text(encoding="utf-8"))
    require(worker["schema"] == "crazyhouse-worker-search-contract/v1", "worker schema mismatch")
    require(worker["profile"]["token"] == PROFILE_TOKEN, "worker profile mismatch")
    start_case = next((case for case in worker["cases"] if case["id"] == "STARTPOS_DEPTH_1"), None)
    require(start_case is not None, "worker start-position case missing")

    transition = addendum["single_variable_transition"]
    if addendum["addendum"] == 2:
        require(
            transition["from"]["crazyhouse_search"] == "DISABLED",
            "worker transition origin mismatch",
        )
        require(
            transition["to"]["crazyhouse_search"] == "BOUND_LEGACY_V1_FULL_REFRESH",
            "worker transition target mismatch",
        )
        binding = "BOUND_LEGACY_V1_FULL_REFRESH"
        expected_markers = None
        expected_protocol = None
        required_route_token = None
        expected_crazyhouse_commits = None
    elif addendum["addendum"] == 3:
        require(
            transition["from"]["crazyhouse_search"] == "BOUND_LEGACY_V1_FULL_REFRESH",
            "worker transition origin mismatch",
        )
        require(
            transition["to"]["crazyhouse_search"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR",
            "worker transition target mismatch",
        )
        prior_pin = addendum["pins"]["routing_addendum_002"]
        prior_path = worker_addendum_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"prior Worker routing addendum missing: {prior_path}")
        require(prior_path.stat().st_size == prior_pin["bytes"], "prior addendum size mismatch")
        require(sha256_file(prior_path) == prior_pin["sha256"], "prior addendum SHA-256 mismatch")

        worker_addendum_pin = addendum["pins"]["worker_search_addendum_001"]
        worker_addendum_path = worker_addendum_path.parent / Path(worker_addendum_pin["path"]).name
        require(
            worker_addendum_path.is_file(),
            f"incremental Worker addendum missing: {worker_addendum_path}",
        )
        require(
            worker_addendum_path.stat().st_size == worker_addendum_pin["bytes"],
            "incremental Worker addendum size mismatch",
        )
        require(
            sha256_file(worker_addendum_path) == worker_addendum_pin["sha256"],
            "incremental Worker addendum SHA-256 mismatch",
        )
        worker_addendum = json.loads(worker_addendum_path.read_text(encoding="utf-8"))
        require(
            worker_addendum["schema"] == "crazyhouse-worker-search-contract-addendum/v1",
            "incremental Worker addendum schema mismatch",
        )
        require(worker_addendum["addendum"] == 1, "incremental Worker addendum number mismatch")
        worker_base_pin = worker_addendum["pins"]["worker_search_contract_v1"]
        require(worker_path.stat().st_size == worker_base_pin["bytes"], "Worker addendum base size mismatch")
        require(
            sha256_file(worker_path) == worker_base_pin["sha256"],
            "Worker addendum base SHA-256 mismatch",
        )
        worker_transition = worker_addendum["single_variable_transition"]
        require(worker_transition["from"]["mode"] == "FULL_REFRESH", "evaluator origin mismatch")
        require(
            worker_transition["to"]["mode"] == "INCREMENTAL_SCALAR",
            "evaluator target mismatch",
        )
        require(
            addendum["implementation"]["commit"] == worker_addendum["implementation"]["commit"],
            "routing and Worker implementation commit mismatch",
        )
        binding = "BOUND_LEGACY_V1_INCREMENTAL_SCALAR"
        expected_markers = addendum["replayed_scenario"]["expected_marker_count"]
        expected_protocol = addendum["replayed_scenario"]["expected_protocol_sha256"]
        required_route_token = None
        expected_crazyhouse_commits = None
    elif addendum["addendum"] == 4:
        require(
            transition["from"]["crazyhouse_search"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR",
            "authenticated Worker transition origin mismatch",
        )
        require(
            transition["to"]["crazyhouse_search"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR_AUTHENTICATED",
            "authenticated Worker transition target mismatch",
        )
        prior_pin = addendum["pins"]["routing_addendum_003"]
        prior_path = worker_addendum_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"prior incremental routing addendum missing: {prior_path}")
        require(prior_path.stat().st_size == prior_pin["bytes"], "prior incremental addendum size mismatch")
        require(
            sha256_file(prior_path) == prior_pin["sha256"],
            "prior incremental addendum SHA-256 mismatch",
        )
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        require(prior["addendum"] == 3, "prior incremental addendum number mismatch")
        require(
            prior["single_variable_transition"]["to"]["crazyhouse_search"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR",
            "prior incremental binding mismatch",
        )

        worker_addendum_pin = addendum["pins"]["worker_search_addendum_002"]
        incremental_worker_path = worker_addendum_path.parent / Path(worker_addendum_pin["path"]).name
        require(
            incremental_worker_path.is_file(),
            f"authenticated incremental Worker addendum missing: {incremental_worker_path}",
        )
        require(
            incremental_worker_path.stat().st_size == worker_addendum_pin["bytes"],
            "authenticated incremental Worker addendum size mismatch",
        )
        require(
            sha256_file(incremental_worker_path) == worker_addendum_pin["sha256"],
            "authenticated incremental Worker addendum SHA-256 mismatch",
        )
        worker_addendum = json.loads(incremental_worker_path.read_text(encoding="utf-8"))
        require(worker_addendum["addendum"] == 2, "authenticated Worker addendum number mismatch")
        require(
            worker_addendum["single_variable_transition"]["to"][
                "route_commit_evaluator_field"
            ]
            == "evaluator=incremental-scalar",
            "authenticated Worker route token mismatch",
        )
        binding = "BOUND_LEGACY_V1_INCREMENTAL_SCALAR_AUTHENTICATED"
        expected_markers = addendum["replayed_scenario"]["marker_count"]
        expected_protocol = addendum["replayed_scenario"]["expected_protocol_sha256"]
        required_route_token = "evaluator=incremental-scalar"
        expected_crazyhouse_commits = addendum["replayed_scenario"][
            "crazyhouse_route_commit_markers_changed"
        ]
    else:
        require(
            worker_addendum_path.stat().st_size == 7114,
            "SIMD routing addendum 005 size mismatch",
        )
        require(
            sha256_file(worker_addendum_path)
            == "9f6738bdb9a28e731c1e71aebe56072500ed389a894e893cbab05abd18fa1c53",
            "SIMD routing addendum 005 SHA-256 mismatch",
        )
        metadata_path = (
            worker_addendum_path.parent
            / "simd-worker-routing-preregistration-metadata.addendum.001.json"
        )
        require(metadata_path.is_file(), "SIMD preregistration metadata addendum missing")
        require(
            metadata_path.stat().st_size == 1950
            and sha256_file(metadata_path)
            == "37cbe3c1e289c28257fe18461886d16d535457c73fabc5f20bf2cfcbb62e7696",
            "SIMD preregistration metadata addendum identity mismatch",
        )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        require(
            metadata["pins"]["freeze_commit"]["commit"]
            == "ec277a25b3c43908679aa4c70bff760a29def58e"
            and metadata["scientific_contract_changed"] is False
            and metadata["expected_red_changed"] is False,
            "SIMD preregistration metadata correction boundary mismatch",
        )
        require(
            transition["from"]["crazyhouse_search"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR_AUTHENTICATED",
            "SIMD routing transition origin mismatch",
        )
        target = transition["to"]
        require(
            target["crazyhouse_search"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SIMD_AUTHENTICATED",
            "SIMD routing transition target mismatch",
        )
        require(
            target["build_selector"] == "CRAZYHOUSE_LEGACY_BACKEND=simd"
            and target["route_commit_evaluator_field"] == "evaluator=incremental-simd"
            and target["simd_backend_field"] == "simd_backend=avx2",
            "SIMD routing provenance target mismatch",
        )
        require(target["fallback_allowed"] is False, "SIMD routing enabled fallback")
        require(
            target["ordinary_product_default_changed"] is False,
            "SIMD routing changed the ordinary product default",
        )
        require(target["strength_claim"] is False, "SIMD routing addendum claims strength")

        prior_pin = addendum["pins"]["routing_addendum_004"]
        prior_path = worker_addendum_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"authenticated scalar routing addendum missing: {prior_path}")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "authenticated scalar routing addendum identity mismatch",
        )
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        require(prior["addendum"] == 4, "authenticated scalar routing addendum number mismatch")
        require(
            prior["single_variable_transition"]["to"]["crazyhouse_search"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR_AUTHENTICATED",
            "authenticated scalar routing binding mismatch",
        )

        worker_addendum_pin = addendum["pins"]["worker_search_addendum_004"]
        simd_worker_path = worker_addendum_path.parent / Path(worker_addendum_pin["path"]).name
        require(simd_worker_path.is_file(), f"SIMD Worker addendum missing: {simd_worker_path}")
        require(
            simd_worker_path.stat().st_size == worker_addendum_pin["bytes"]
            and sha256_file(simd_worker_path) == worker_addendum_pin["sha256"],
            "SIMD Worker addendum identity mismatch",
        )
        simd_worker = json.loads(simd_worker_path.read_text(encoding="utf-8"))
        require(
            simd_worker["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
            and simd_worker["addendum"] == 4,
            "SIMD Worker addendum identity mismatch",
        )
        require(
            simd_worker["runtime_replay"]["required_route_token"]
            == "evaluator=incremental-simd simd_backend=avx2",
            "SIMD Worker route token mismatch",
        )

        parity_pin = addendum["pins"]["legacy_v1_simd_parity_gate_record"]
        parity_path = (worker_addendum_path.parent / parity_pin["path"]).resolve()
        require(parity_path.is_file(), f"SIMD parity gate record missing: {parity_path}")
        require(
            parity_path.stat().st_size == parity_pin["bytes"]
            and sha256_file(parity_path) == parity_pin["sha256"],
            "SIMD parity gate record identity mismatch",
        )

        replay = addendum["replayed_scenario"]
        require(replay["scenario_count"] == 11, "SIMD routing scenario count mismatch")
        require(replay["marker_count"] == 32, "SIMD routing marker count mismatch")
        require(
            replay["scalar_protocol_sha256"]
            == "aef709f821942c38b1437d6c9647cb7ca69a83642a6cf10c4843b80d2efb505f",
            "SIMD routing scalar protocol pin mismatch",
        )
        require(
            replay["expected_protocol_sha256"]
            == "0be471a4dca5e709d9ca9b30f792715f63afe7b1958106311c5d6578b345d6b7",
            "SIMD routing expected protocol mismatch",
        )
        unavailable = addendum["unavailable_simd_negative_control"]
        require(
            unavailable["required_error_code"] == "legacy_simd_unavailable"
            and unavailable["route_commit_forbidden"] is True
            and unavailable["readyok_forbidden"] is True
            and unavailable["bestmove_forbidden"] is True
            and unavailable["scalar_fallback_forbidden"] is True,
            "SIMD unavailable-backend boundary mismatch",
        )

        binding = "BOUND_LEGACY_V1_INCREMENTAL_SIMD_AUTHENTICATED"
        expected_markers = replay["marker_count"]
        expected_protocol = replay["expected_protocol_sha256"]
        required_route_token = "evaluator=incremental-simd simd_backend=avx2"
        expected_crazyhouse_commits = replay["crazyhouse_route_commit_markers_changed"]
    if correction is not None:
        diagnostic_pin = correction["pins"]["architecture_diagnostic_record"]
        diagnostic_path = (correction_path.parent / diagnostic_pin["path"]).resolve()
        require(
            diagnostic_path.is_file(),
            f"SIMD routing architecture diagnostic missing: {diagnostic_path}",
        )
        require(
            diagnostic_path.stat().st_size == diagnostic_pin["bytes"]
            and sha256_file(diagnostic_path) == diagnostic_pin["sha256"],
            "SIMD routing architecture diagnostic identity mismatch",
        )
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        require(
            diagnostic["result"] == "CLASSIFIED_CONTRACT_CORRECTION_REQUIRED"
            and diagnostic["cause"]["classification"]
            == "CONTRACT_BASELINE_TARGET_MISMATCH",
            "SIMD routing architecture diagnosis mismatch",
        )
        require(
            diagnostic["source"]["commit"] == correction["implementation"]["commit"]
            and diagnostic["source"]["tree"] == correction["implementation"]["tree"],
            "SIMD routing correction implementation identity mismatch",
        )
        require(
            correction["pins"]["official_network"]["sha256"]
            == diagnostic["network_authentication"]["official_network"]["sha256"]
            and correction["pins"]["legacy_network"]["sha256"]
            == diagnostic["network_authentication"]["legacy_network"]["sha256"],
            "SIMD routing correction network identity mismatch",
        )
        preserved = correction["preserved_original_contract"]
        require(
            preserved["addendum"] == 5
            and preserved["rewritten"] is False
            and preserved["original_expected_red_observed"] is True
            and preserved["original_expected_protocol_sha256"]
            == addendum["replayed_scenario"]["expected_protocol_sha256"],
            "SIMD routing original-contract preservation mismatch",
        )
        corrected_replay = correction["corrected_same_target_replay"]
        require(
            corrected_replay["target_arch"] == "x86-64-avx2"
            and corrected_replay["scenario_count"] == 11
            and corrected_replay["marker_count"] == 32
            and corrected_replay["default_scalar_protocol_sha256"]
            == "df375512dda60f4cab1545287197c1b407a3d4f7c0e9f4415e0f36fcb3905d35"
            and corrected_replay["expected_protocol_sha256"]
            == "9cde249775fb8e90ffa3512daaf3a33b60750823aa722ff63a2e11f4db5bef0e",
            "SIMD routing corrected protocol pin mismatch",
        )
        require(
            corrected_replay["default_scalar_official_content_hash"]
            == corrected_replay["simd_official_content_hash"]
            == "11653427611306125920",
            "SIMD routing same-target official identity mismatch",
        )
        require(
            corrected_replay["crazyhouse_route_commit_markers_changed"] == 5
            and corrected_replay["changed_sequences"] == [4, 9, 10, 25, 29]
            and corrected_replay["chess_route_markers_unchanged_within_target"] is True
            and corrected_replay["bestmoves_unchanged"] is True
            and corrected_replay["non_search_refusals_unchanged"] is True,
            "SIMD routing corrected marker boundary mismatch",
        )
        boundaries = correction["unchanged_capability_boundaries"]
        require(
            boundaries["build_selector"] == "CRAZYHOUSE_LEGACY_BACKEND=simd"
            and boundaries["required_route_token"]
            == "evaluator=incremental-simd simd_backend=avx2"
            and boundaries["fallback_allowed"] is False
            and boundaries["ordinary_product_default_changed"] is False
            and boundaries["unavailable_simd_error"] == "legacy_simd_unavailable",
            "SIMD routing corrected capability boundary mismatch",
        )
        require(
            diagnostic["controlled_comparison"]["fresh_x86_64_avx2_default_scalar"][
                "protocol_sha256"
            ]
            == corrected_replay["default_scalar_protocol_sha256"]
            and diagnostic["controlled_comparison"]["x86_64_avx2_simd"][
                "protocol_sha256"
            ]
            == corrected_replay["expected_protocol_sha256"],
            "SIMD routing corrected diagnostic replay mismatch",
        )
        expected_markers = corrected_replay["marker_count"]
        expected_protocol = corrected_replay["expected_protocol_sha256"]
        expected_crazyhouse_commits = corrected_replay[
            "crazyhouse_route_commit_markers_changed"
        ]
    if default_scalar_control is not None:
        if rule_only_provenance is not None:
            require(
                scoped_route_provenance is not None
                and rule_only_provenance["preserved_routing_control"][
                    "predecessor_addendum"
                ]
                == 10
                and rule_only_provenance["preserved_routing_control"]["rewritten"]
                is False
                and rule_only_provenance["preserved_routing_control"][
                    "expected_protocol_sha256"
                ]
                == default_scalar_control["runtime_replay"]["expected_protocol_sha256"]
                and rule_only_provenance["preserved_routing_control"][
                    "no_active_route_position_error"
                ]
                == "position_requires_committed_route"
                and rule_only_provenance["preserved_routing_control"][
                    "failed_replacement_rule_only_perft_nodes"
                ]
                == 20
                and rule_only_provenance["preserved_routing_control"][
                    "failed_replacement_search_error"
                ]
                == "stored activeError"
                and rule_only_provenance["preserved_routing_control"][
                    "routing_expectation_changed"
                ]
                is False,
                "rule-only provenance correction changed routing",
            )
            worker_pin = rule_only_provenance["pins"]["worker_search_addendum_008"]
            expected_worker_addendum = 8
        elif scoped_route_provenance is not None:
            require(
                failed_route_provenance is not None
                and scoped_route_provenance["preserved_routing_control"]["addendum"] == 9
                and scoped_route_provenance["preserved_routing_control"]["rewritten"]
                is False
                and scoped_route_provenance["preserved_routing_control"][
                    "expected_protocol_sha256"
                ]
                == default_scalar_control["runtime_replay"]["expected_protocol_sha256"]
                and scoped_route_provenance["preserved_routing_control"][
                    "no_active_route_position_error"
                ]
                == "position_requires_committed_route"
                and scoped_route_provenance["preserved_routing_control"][
                    "routing_expectation_changed"
                ]
                is False,
                "scoped-route provenance correction changed routing",
            )
            worker_pin = scoped_route_provenance["pins"]["worker_search_addendum_007"]
            expected_worker_addendum = 7
        elif failed_route_provenance is not None:
            require(
                default_scalar_provenance is not None
                and failed_route_provenance["preserved_routing_control"]["addendum"] == 8
                and failed_route_provenance["preserved_routing_control"]["rewritten"]
                is False
                and failed_route_provenance["preserved_routing_control"][
                    "expected_protocol_sha256"
                ]
                == default_scalar_control["runtime_replay"]["expected_protocol_sha256"]
                and failed_route_provenance["preserved_routing_control"][
                    "routing_expectation_changed"
                ]
                is False,
                "failed-route provenance correction changed routing",
            )
            worker_pin = failed_route_provenance["pins"]["worker_search_addendum_006"]
            expected_worker_addendum = 6
        elif default_scalar_provenance is None:
            worker_pin = default_scalar_control["pins"]["worker_search_addendum_002"]
            expected_worker_addendum = 2
        else:
            require(
                default_scalar_provenance["preserved_routing_control"]["addendum"] == 7
                and default_scalar_provenance["preserved_routing_control"]["rewritten"]
                is False
                and default_scalar_provenance["preserved_routing_control"][
                    "expected_protocol_sha256"
                ]
                == default_scalar_control["runtime_replay"]["expected_protocol_sha256"]
                and default_scalar_provenance["preserved_routing_control"][
                    "routing_expectation_changed"
                ]
                is False,
                "default-scalar provenance correction changed routing",
            )
            require(
                default_scalar_provenance["pins"]["architecture_diagnostic_record"]
                == default_scalar_control["pins"]["architecture_diagnostic_record"],
                "default-scalar provenance diagnostic chain mismatch",
            )
            worker_pin = default_scalar_provenance["pins"]["worker_search_addendum_005"]
            expected_worker_addendum = 5
        worker_path = default_scalar_control_path.parent / Path(worker_pin["path"]).name
        require(worker_path.is_file(), f"default-scalar Worker addendum missing: {worker_path}")
        require(
            worker_path.stat().st_size == worker_pin["bytes"]
            and sha256_file(worker_path) == worker_pin["sha256"],
            "default-scalar Worker addendum identity mismatch",
        )
        worker_document = json.loads(worker_path.read_text(encoding="utf-8"))
        require(
            worker_document["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
            and worker_document["addendum"] == expected_worker_addendum,
            "default-scalar Worker binding mismatch",
        )
        if expected_worker_addendum == 2:
            require(
                worker_document["single_variable_transition"]["to"][
                    "route_commit_evaluator_field"
                ]
                == "evaluator=incremental-scalar",
                "historical default-scalar Worker binding mismatch",
            )
        elif expected_worker_addendum == 5:
            current_worker = default_scalar_provenance["worker_provenance_correction"][
                "current_positive_pin"
            ]
            require(
                current_worker["addendum"] == 5
                and current_worker["source_product_commit"]
                == worker_document["source_line"]["product_implementation_commit"]
                and current_worker["source_product_tree"]
                == worker_document["source_line"]["product_implementation_tree"]
                and current_worker["required_route_token"]
                == worker_document["runtime_replay"]["required_route_token"]
                and current_worker["forbidden_route_tokens"]
                == worker_document["runtime_replay"]["forbidden_route_tokens"]
                and current_worker["required_worker_summary"]
                == worker_document["runtime_replay"]["required_worker_summary"],
                "current-source default-scalar Worker provenance mismatch",
            )
            formal = default_scalar_provenance["formal_replay"]
            require(
                formal["routing_addendum"] == 8
                and formal["worker_addendum"] == 5
                and all(
                    value is True
                    for key, value in formal.items()
                    if key not in ("routing_addendum", "worker_addendum")
                ),
                "default-scalar corrected formal requirements mismatch",
            )
        elif expected_worker_addendum == 6:
            current_worker = failed_route_provenance["worker_provenance_correction"][
                "current_positive_pin"
            ]
            worker_rebase = worker_document["source_rebase"]
            require(
                current_worker["addendum"] == 6
                and current_worker["source_descendant_commit"]
                == worker_rebase["descendant_commit"]
                and current_worker["source_descendant_tree"]
                == worker_rebase["descendant_tree"]
                and current_worker["changed_source_pin_path"]
                == worker_rebase["changed_source_pin_path"]
                and current_worker["required_route_token"]
                == worker_document["runtime_replay"]["required_route_token"]
                and current_worker["forbidden_route_tokens"]
                == worker_document["runtime_replay"]["forbidden_route_tokens"]
                and current_worker["required_worker_summary"]
                == worker_document["runtime_replay"]["required_worker_summary"],
                "failed-route current-source Worker provenance mismatch",
            )
            require(
                worker_rebase["exact_behavior_transition"]["required_error"]
                == "legacy_simd_unavailable"
                and worker_rebase["exact_behavior_transition"][
                    "position_epoch_must_remain_invalid"
                ]
                is True
                and worker_rebase["exact_behavior_transition"][
                    "routing_success_digest_changed"
                ]
                is False,
                "failed-route Worker behavior boundary mismatch",
            )
            formal = failed_route_provenance["formal_replay"]
            require(
                formal["routing_addendum"] == 9
                and formal["worker_addendum"] == 6
                and all(
                    value is True
                    for key, value in formal.items()
                    if key not in ("routing_addendum", "worker_addendum")
                ),
                "failed-route corrected formal requirements mismatch",
            )
        elif expected_worker_addendum == 7:
            current_worker = scoped_route_provenance["worker_provenance_correction"][
                "current_positive_pin"
            ]
            worker_rebase = worker_document["source_rebase"]
            require(
                current_worker["addendum"] == 7
                and current_worker["source_descendant_commit"]
                == worker_rebase["descendant_commit"]
                and current_worker["source_descendant_tree"]
                == worker_rebase["descendant_tree"]
                and current_worker["changed_source_pin_path"]
                == worker_rebase["changed_source_pin_path"]
                and current_worker["required_route_token"]
                == worker_document["runtime_replay"]["required_route_token"]
                and current_worker["forbidden_route_tokens"]
                == worker_document["runtime_replay"]["forbidden_route_tokens"]
                and current_worker["required_worker_summary"]
                == worker_document["runtime_replay"]["required_worker_summary"],
                "scoped-route current-source Worker provenance mismatch",
            )
            behavior = worker_rebase["exact_behavior_refinement"]
            require(
                behavior["preserved_no_active_route_position_error"]
                == "position_requires_committed_route"
                and behavior["active_failed_backend_position_error"] == "stored activeError"
                and behavior["active_failed_backend_search_error"] == "stored activeError"
                and behavior["required_unavailable_simd_error"]
                == "legacy_simd_unavailable"
                and behavior["routing_success_digest_changed"] is False,
                "scoped-route Worker behavior boundary mismatch",
            )
            formal = scoped_route_provenance["formal_replay"]
            require(
                formal["routing_addendum"] == 10
                and formal["worker_addendum"] == 7
                and all(
                    value is True
                    for key, value in formal.items()
                    if key not in ("routing_addendum", "worker_addendum")
                ),
                "scoped-route corrected formal requirements mismatch",
            )
        else:
            require(expected_worker_addendum == 8, "rule-only Worker addendum mismatch")
            current_worker = rule_only_provenance["worker_provenance_correction"][
                "current_positive_pin"
            ]
            worker_rebase = worker_document["source_rebase"]
            require(
                current_worker["addendum"] == 8
                and current_worker["source_descendant_commit"]
                == worker_rebase["descendant_commit"]
                and current_worker["source_descendant_tree"]
                == worker_rebase["descendant_tree"]
                and current_worker["changed_source_pin_path"]
                == worker_rebase["changed_source_pin_path"]
                and current_worker["required_route_token"]
                == worker_document["runtime_replay"]["required_route_token"]
                and current_worker["forbidden_route_tokens"]
                == worker_document["runtime_replay"]["forbidden_route_tokens"]
                and current_worker["required_worker_summary"]
                == worker_document["runtime_replay"]["required_worker_summary"],
                "rule-only current-source Worker provenance mismatch",
            )
            behavior = worker_rebase["exact_behavior_correction"]
            require(
                behavior["preserved_no_active_route_position_error"]
                == "position_requires_committed_route"
                and behavior["active_failed_backend_position_semantics"]
                == "rule-only route admitted"
                and behavior["active_failed_backend_perft_semantics"]
                == "rule-only perft admitted"
                and behavior["active_failed_backend_search_error"]
                == "stored activeError"
                and behavior["required_unavailable_simd_error"]
                == "legacy_simd_unavailable"
                and behavior["routing_success_digest_changed"] is False
                and behavior["fallback_allowed"] is False,
                "rule-only Worker behavior boundary mismatch",
            )
            expected_red = rule_only_provenance["expected_red"]
            require(
                expected_red["record"] == 148
                and expected_red["engine_started"] is True
                and expected_red["fallback_observed"] is False,
                "rule-only routing expected-red mismatch",
            )
            formal = rule_only_provenance["formal_replay"]
            require(
                formal["routing_addendum"] == 11
                and formal["worker_addendum"] == 8
                and all(
                    value is True
                    for key, value in formal.items()
                    if key not in ("routing_addendum", "worker_addendum")
                ),
                "rule-only corrected formal requirements mismatch",
            )
        require(
            default_scalar_control["pins"]["architecture_diagnostic_record"]
            == correction["pins"]["architecture_diagnostic_record"]
            and default_scalar_control["pins"]["official_network"]
            == correction["pins"]["official_network"]
            and default_scalar_control["pins"]["legacy_network"]
            == correction["pins"]["legacy_network"],
            "default-scalar control evidence chain mismatch",
        )
        control = default_scalar_control["single_variable_control"]
        ordinary = control["ordinary_default"]
        require(
            control["simd_capability"]["target_arch"] == ordinary["target_arch"]
            == "x86-64-avx2"
            and ordinary["build_selector_supplied"] is False
            and ordinary["make_default"] == "CRAZYHOUSE_LEGACY_BACKEND=scalar"
            and ordinary["route_commit_evaluator_field"] == "evaluator=incremental-scalar"
            and ordinary["simd_backend_field"] is None
            and ordinary["runtime_selector_present"] is False
            and ordinary["environment_selector_present"] is False,
            "default-scalar build-selection boundary mismatch",
        )
        scalar_replay = default_scalar_control["runtime_replay"]
        require(
            scalar_replay["scenario_count"] == 11
            and scalar_replay["marker_count"] == 32
            and scalar_replay["expected_protocol_sha256"]
            == corrected_replay["default_scalar_protocol_sha256"]
            == "df375512dda60f4cab1545287197c1b407a3d4f7c0e9f4415e0f36fcb3905d35"
            and scalar_replay["expected_official_content_hash"]
            == corrected_replay["default_scalar_official_content_hash"]
            == "11653427611306125920",
            "default-scalar protocol pin mismatch",
        )
        require(
            scalar_replay["required_route_token"] == "evaluator=incremental-scalar"
            and scalar_replay["forbidden_route_tokens"]
            == ["evaluator=incremental-simd", "simd_backend="]
            and scalar_replay["successful_crazyhouse_route_commits"] == 5
            and scalar_replay["expected_binding"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR_AUTHENTICATED",
            "default-scalar route provenance mismatch",
        )
        requirements = default_scalar_control["formal_gate_requirements"]
        require(
            all(requirements.values()),
            "default-scalar formal-gate requirement disabled",
        )
        binding = scalar_replay["expected_binding"]
        expected_markers = scalar_replay["marker_count"]
        expected_protocol = scalar_replay["expected_protocol_sha256"]
        required_route_token = scalar_replay["required_route_token"]
        expected_crazyhouse_commits = scalar_replay[
            "successful_crazyhouse_route_commits"
        ]
    refusal_contract = (
        addendum["unchanged_command_refusals"]
        if addendum["addendum"] in (2, 3, 5)
        else prior["unchanged_command_refusals"]
    )
    require(
        refusal_contract
        == {
            "eval": "crazyhouse_eval_not_bound",
            "bench": "crazyhouse_bench_not_bound",
            "speedtest": "crazyhouse_speedtest_not_bound",
            "export_net": "crazyhouse_export_net_not_bound",
        },
        "worker addendum changed a non-search command boundary",
    )
    return {
        "enabled": True,
        "binding": binding,
        "fixed_options": worker["fixed_options"],
        "go_command": start_case["go_command"],
        "allowed_bestmoves": set(start_case["allowed_bestmoves"]),
        "expected_markers": expected_markers,
        "expected_protocol": expected_protocol,
        "required_route_token": required_route_token,
        "expected_crazyhouse_commits": expected_crazyhouse_commits,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--legacy-network", required=True, type=Path)
    parser.add_argument("--official-network", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--contract-addendum", type=Path)
    parser.add_argument("--worker-search-addendum", type=Path)
    parser.add_argument("--capability-addendum", type=Path)
    parser.add_argument("--transcript-out", type=Path)
    args = parser.parse_args()

    for path in (args.engine, args.legacy_network, args.official_network, args.contract):
        require(path.is_file(), f"required file missing: {path}")
    if args.contract_addendum is not None:
        require(args.contract_addendum.is_file(), f"required file missing: {args.contract_addendum}")
    if args.worker_search_addendum is not None:
        require(
            args.worker_search_addendum.is_file(),
            f"required file missing: {args.worker_search_addendum}",
        )
    if args.capability_addendum is not None:
        require(
            args.capability_addendum.is_file(),
            f"required file missing: {args.capability_addendum}",
        )

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    require(contract["schema"] == "crazyhouse-uci-routing-contract/v1", "contract schema mismatch")
    require(contract["profile"] == PROFILE_ID, "profile ID mismatch")
    require(contract["profile_sha256"] == PROFILE_SHA256, "profile SHA-256 mismatch")
    require(contract["profile_token"] == PROFILE_TOKEN, "profile token mismatch")
    upstream_context = load_upstream_context(args.contract, contract, args.contract_addendum)
    search_context = load_worker_search_context(
        args.contract, args.contract_addendum, args.worker_search_addendum
    )
    capability_context = load_capability_context(
        contract,
        args.contract_addendum,
        args.worker_search_addendum,
        args.capability_addendum,
        upstream_context,
        search_context,
    )
    if capability_context["enabled"]:
        engine_identity = capability_context["engine"]
        require(args.engine.stat().st_size == engine_identity["bytes"], "capability engine size mismatch")
        require(sha256_file(args.engine) == engine_identity["sha256"], "capability engine SHA-256 mismatch")
    legacy_identity = contract["legacy_network"]
    require(args.legacy_network.stat().st_size == legacy_identity["bytes"], "legacy size mismatch")
    require(sha256_file(args.legacy_network) == legacy_identity["sha256"], "legacy SHA-256 mismatch")
    official_identity = upstream_context["official_network"]
    if "filename" in official_identity:
        require(args.official_network.name == official_identity["filename"], "official network name mismatch")
    if "bytes" in official_identity:
        require(args.official_network.stat().st_size == official_identity["bytes"], "official network size mismatch")
    require(sha256_file(args.official_network) == official_identity["sha256"], "official network SHA-256 mismatch")

    transcript: list[str] = []
    transcript += inventory_scenario(
        args.engine,
        contract,
        upstream_context["expected_eval_file_line"],
        capability_context["expected_option_count"],
        capability_context["expected_ordered_names"],
        capability_context["required_option_lines"],
    )
    transcript += initial_failure_scenario(args.engine)
    transcript += invalid_variant_scenario(args.engine, args.legacy_network)
    transcript += profile_failure_scenario(args.engine, args.legacy_network)
    transcript += crazyhouse_rule_route_scenario(
        args.engine, args.legacy_network, search_context
    )
    transcript += crossed_routes_scenario(args.engine, args.legacy_network, args.official_network)
    transcript += failed_replacement_scenario(args.engine, args.legacy_network)
    transcript += chess960_scenario(args.engine, args.legacy_network)
    transcript += position_transaction_scenario(args.engine, args.legacy_network)
    transcript += option_persistence_scenario(args.engine, args.legacy_network, args.official_network)
    transcript += chess_control_scenario(args.engine, args.official_network)

    protocol = "\n".join(transcript).encode("utf-8")
    protocol_sha256 = hashlib.sha256(protocol).hexdigest()
    if search_context["enabled"] and search_context["required_route_token"] is not None:
        crazyhouse_commits = [
            marker
            for marker in transcript
            if "route_commit status=ok ruleset=crazyhouse" in marker
        ]
        require(
            len(crazyhouse_commits) == search_context["expected_crazyhouse_commits"],
            "authenticated Crazyhouse route-commit count mismatch",
        )
        require(
            all(search_context["required_route_token"] in marker for marker in crazyhouse_commits),
            "missing engine-authored "
            + (
                "SIMD"
                if "incremental-simd" in search_context["required_route_token"]
                else "incremental"
            )
            + " route telemetry",
        )
    if search_context["enabled"] and search_context["expected_markers"] is not None:
        require(
            len(transcript) == search_context["expected_markers"],
            "incremental routing marker count mismatch",
        )
        require(
            protocol_sha256 == search_context["expected_protocol"],
            "incremental routing protocol SHA-256 mismatch",
        )
    if args.transcript_out is not None:
        require(args.transcript_out.parent.is_dir(), "transcript output directory missing")
        require(not args.transcript_out.exists(), "transcript output already exists")
        transcript_lines = [
            json.dumps({"sequence": index + 1, "marker": marker}, separators=(",", ":"))
            for index, marker in enumerate(transcript)
        ]
        with args.transcript_out.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(transcript_lines) + "\n")
    print(
        "PASS crazyhouse_uci_routing "
        f"scenarios=11 markers={len(transcript)} protocol_sha256={protocol_sha256} "
        f"crazyhouse_search={search_context['binding'] if search_context['enabled'] else 'DISABLED'} "
        "chess_control=PASS"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as exc:
        print(f"FAIL crazyhouse_uci_routing_verify: {exc}", file=sys.stderr)
        raise SystemExit(1)
