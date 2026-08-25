#!/usr/bin/env python3
"""Qualify the frozen fail-closed Fairy-Stockfish Crazyhouse comparator adapter."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable


CREATE_NO_WINDOW = 0x08000000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def process_snapshot() -> dict[int, tuple[int, str]]:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    result: dict[int, tuple[int, str]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            result[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                str(entry.szExeFile),
            )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def descendants(root_pid: int) -> dict[int, tuple[int, str]]:
    snapshot = process_snapshot()
    selected: dict[int, tuple[int, str]] = {}
    frontier = {root_pid}
    while frontier:
        next_frontier: set[int] = set()
        for pid, item in snapshot.items():
            if pid not in selected and item[0] in frontier:
                selected[pid] = item
                next_frontier.add(pid)
        frontier = next_frontier
    return selected


def pid_exists(pid: int) -> bool:
    return pid in process_snapshot()


def terminate_exact_pid(pid: int, exit_code: int) -> None:
    handle = kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
        False,
        pid,
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess failed for PID {pid}")
    try:
        if not kernel32.TerminateProcess(handle, exit_code):
            raise OSError(ctypes.get_last_error(), f"TerminateProcess failed for PID {pid}")
    finally:
        kernel32.CloseHandle(handle)


def wait_until(predicate: Callable[[], bool], timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError(description)


class UciProcess:
    def __init__(
        self,
        command: list[str],
        artifact_dir: Path,
        name: str,
        cwd: Path,
    ) -> None:
        self.command = command
        self.artifact_dir = artifact_dir
        self.name = name
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.records: list[dict[str, object]] = []
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._sequence = 0
        self._stdin_closed = False
        self.process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
            env=os.environ.copy(),
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._threads = [
            threading.Thread(
                target=self._read_stream,
                args=("out", self.process.stdout),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_stream,
                args=("err", self.process.stderr),
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()

    def _record(self, direction: str, line: str) -> None:
        with self._lock:
            self.records.append(
                {"sequence": self._sequence, "direction": direction, "line": line}
            )
            self._sequence += 1

    def _read_stream(self, direction: str, stream: object) -> None:
        for raw in stream:  # type: ignore[union-attr]
            line = raw.rstrip("\r\n")
            self._record(direction, line)
            if direction == "out":
                with self._lock:
                    self.stdout_lines.append(line)
                self._queue.put(line)
            else:
                with self._lock:
                    self.stderr_lines.append(line)

    def output_length(self) -> int:
        with self._lock:
            return len(self.stdout_lines)

    def output_from(self, index: int) -> list[str]:
        with self._lock:
            return list(self.stdout_lines[index:])

    def send(self, line: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(
                f"{self.name}: process exited {self.process.returncode} before {line!r}"
            )
        if self._stdin_closed:
            raise RuntimeError(f"{self.name}: stdin is already closed")
        assert self.process.stdin is not None
        self._record("in", line)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def close_stdin(self) -> None:
        if self._stdin_closed:
            return
        assert self.process.stdin is not None
        self._record("control", "<stdin-eof>")
        self.process.stdin.close()
        self._stdin_closed = True

    def wait_stdout(self, predicate: Callable[[str], bool], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None and self._queue.empty():
                raise RuntimeError(
                    f"{self.name}: process exited {self.process.returncode} before output"
                )
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = self._queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if predicate(line):
                return line
        raise TimeoutError(f"{self.name}: expected stdout was not observed")

    def initialize(self, timeout: float) -> list[str]:
        start = self.output_length()
        self.send("uci")
        self.wait_stdout(lambda line: line == "uciok", timeout)
        return self.output_from(start)

    def wait_exit(self, timeout: float) -> int:
        return self.process.wait(timeout=timeout)

    def finish(
        self,
        expected_exit: int,
        *,
        send_quit: bool = True,
        timeout: float = 10.0,
    ) -> dict[str, object]:
        forced = False
        if self.process.poll() is None and send_quit:
            try:
                self.send("quit")
            except (BrokenPipeError, RuntimeError):
                pass
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            forced = True
            self.process.kill()
            self.process.wait(timeout=5.0)
        for thread in self._threads:
            thread.join(timeout=2.0)
        transcript = self.artifact_dir / f"{self.name}.jsonl"
        with transcript.open("x", encoding="utf-8", newline="\n") as output:
            for record in self.records:
                output.write(
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                )
        descendant_samples: list[dict[str, object]] = []
        cleanup_start = time.monotonic()
        remaining = descendants(self.process.pid)
        while remaining and time.monotonic() - cleanup_start < 1.0:
            descendant_samples.append(
                {
                    "elapsed_ms": round((time.monotonic() - cleanup_start) * 1000),
                    "processes": {
                        str(pid): {"parent": item[0], "image": item[1]}
                        for pid, item in remaining.items()
                    },
                }
            )
            time.sleep(0.05)
            remaining = descendants(self.process.pid)
        cleanup_elapsed_ms = round((time.monotonic() - cleanup_start) * 1000)
        return {
            "pid": self.process.pid,
            "exit_code": self.process.returncode,
            "expected_exit": expected_exit,
            "forced": forced,
            "stderr_lines": list(self.stderr_lines),
            "transcript": str(transcript),
            "transcript_bytes": transcript.stat().st_size,
            "transcript_sha256": sha256(transcript),
            "remaining_descendants": {
                str(pid): {"parent": item[0], "image": item[1]}
                for pid, item in remaining.items()
            },
            "descendant_cleanup_elapsed_ms": cleanup_elapsed_ms,
            "descendant_samples": descendant_samples,
            "pass": self.process.returncode == expected_exit
            and not forced
            and not self.stderr_lines
            and not remaining,
        }


def adapter_command(adapter: Path, engine: Path, network: Path) -> list[str]:
    return [
        str(adapter),
        "--engine",
        str(engine),
        "--network",
        str(network),
    ]


def exact_ack(profile: dict[str, str], nonce: str) -> str:
    return (
        "info string crazyhouse_capability_ack status=ok "
        f"profile={profile['id']} profile_sha256={profile['sha256']} nonce={nonce}"
    )


def configure_adapter(
    process: UciProcess,
    profile: dict[str, str],
    network: Path,
    nonce: str | None,
) -> int:
    start = process.output_length()
    process.send("setoption name UCI_Variant value crazyhouse")
    process.send(f"setoption name CrazyhouseProfile value {profile['token']}")
    process.send(f"setoption name CrazyhouseEvalFile value {network}")
    if nonce is not None:
        process.send(f"setoption name CrazyhouseCapabilityNonce value {nonce}")
    process.send("isready")
    return start


def normalize_search(lines: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for line in lines:
        if line.startswith("info string route_commit ") or line.startswith(
            "info string crazyhouse_capability_ack "
        ):
            continue
        value = re.sub(r"\s+(?:time|nps|hashfull)\s+\d+", "", line)
        value = re.sub(r"[ \t]+", " ", value).rstrip()
        normalized.append(value)
    return normalized


def run_search_case(
    *,
    mode: str,
    case: dict[str, object],
    adapter: Path,
    engine: Path,
    network: Path,
    profile: dict[str, str],
    artifact_dir: Path,
    timeout: float,
) -> dict[str, object]:
    name = f"transparency-{case['id']}-{mode}"
    if mode == "raw":
        command = [str(engine)]
        cwd = engine.parent
    else:
        command = adapter_command(adapter, engine, network)
        cwd = adapter.parent
    process = UciProcess(command, artifact_dir, name, cwd)
    try:
        process.initialize(timeout)
        process.send("setoption name UCI_Variant value crazyhouse")
        if mode == "raw":
            process.send("setoption name Use NNUE value true")
            process.send(f"setoption name EvalFile value {network}")
        else:
            nonce = hashlib.sha256(str(case["id"]).encode("ascii")).hexdigest()[:32]
            process.send(f"setoption name CrazyhouseProfile value {profile['token']}")
            process.send(f"setoption name CrazyhouseEvalFile value {network}")
            process.send(f"setoption name CrazyhouseCapabilityNonce value {nonce}")
        process.send("setoption name Threads value 1")
        process.send("setoption name Hash value 16")
        process.send("setoption name MultiPV value 1")
        process.send("setoption name Clear Hash")
        process.send("isready")
        process.wait_stdout(lambda line: line == "readyok", timeout)
        start = process.output_length()
        process.send(str(case["position"]))
        process.send(str(case["go"]))
        process.wait_stdout(lambda line: line.startswith("bestmove "), timeout)
        lines = process.output_from(start)
        normalized = normalize_search(lines)
        marker = f"info string NNUE evaluation using {network} enabled"
        checks = {
            "one_marker": lines.count(marker) == 1,
            "marker_first": bool(lines) and lines[0] == marker,
            "one_bestmove": sum(line.startswith("bestmove ") for line in lines) == 1,
            "no_fatal": not any(line.startswith("info string ERROR") for line in lines),
            "no_classical": "info string classical evaluation enabled" not in lines,
        }
    finally:
        lifecycle = process.finish(0)
    return {
        "mode": mode,
        "lines": lines,
        "normalized": normalized,
        "normalized_sha256": hashlib.sha256(
            ("\n".join(normalized) + "\n").encode("utf-8")
        ).hexdigest(),
        "checks": checks,
        "lifecycle": lifecycle,
    }


def run_perft_case(
    *,
    mode: str,
    perft: dict[str, object],
    adapter: Path,
    engine: Path,
    network: Path,
    profile: dict[str, str],
    artifact_dir: Path,
    timeout: float,
) -> dict[str, object]:
    name = f"perft-transparency-{mode}"
    if mode == "raw":
        command = [str(engine)]
        cwd = engine.parent
    else:
        command = adapter_command(adapter, engine, network)
        cwd = adapter.parent
    process = UciProcess(command, artifact_dir, name, cwd)
    try:
        process.initialize(timeout)
        process.send("setoption name UCI_Variant value crazyhouse")
        if mode == "raw":
            process.send("setoption name Use NNUE value true")
            process.send(f"setoption name EvalFile value {network}")
        else:
            process.send(f"setoption name CrazyhouseProfile value {profile['token']}")
            process.send(f"setoption name CrazyhouseEvalFile value {network}")
            process.send(
                "setoption name CrazyhouseCapabilityNonce value "
                "89abcdef0123456789abcdef01234567"
            )
        process.send("setoption name Threads value 1")
        process.send("setoption name Hash value 16")
        process.send("setoption name Clear Hash")
        process.send("isready")
        process.wait_stdout(lambda line: line == "readyok", timeout)
        start = process.output_length()
        process.send(str(perft["position"]))
        process.send(str(perft["go"]))
        terminal = str(perft["expected_terminal_line"])
        process.wait_stdout(lambda line: line == terminal, timeout)
        observed = process.output_from(start)
        terminal_index = observed.index(terminal)
        lines = observed[: terminal_index + 1]
    finally:
        lifecycle = process.finish(0)
    return {
        "mode": mode,
        "lines": lines,
        "lines_sha256": hashlib.sha256(
            ("\n".join(lines) + "\n").encode("utf-8")
        ).hexdigest(),
        "checks": {
            "terminal_once": lines.count(str(perft["expected_terminal_line"])) == 1,
            "terminal_last": bool(lines)
            and lines[-1] == str(perft["expected_terminal_line"]),
            "no_nnue_marker": not any("NNUE evaluation using" in line for line in lines),
            "no_classical": "info string classical evaluation enabled" not in lines,
            "no_fatal": not any(line.startswith("info string ERROR") for line in lines),
            "no_bestmove": not any(line.startswith("bestmove ") for line in lines),
        },
        "lifecycle": lifecycle,
    }


def main(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise RuntimeError("the frozen adapter gate is Windows-only")
    timeout = args.timeout
    if timeout <= 0:
        raise RuntimeError("timeout must be positive")

    fixture_path = args.fixture.resolve(strict=True)
    addendum_path = args.addendum.resolve(strict=True)
    coverage_addendum_path = args.coverage_addendum.resolve(strict=True)
    source_path = args.source.resolve(strict=True)
    adapter_a = args.adapter_a.resolve(strict=True)
    adapter_b = args.adapter_b.resolve(strict=True)
    engine = args.engine.resolve(strict=True)
    network = args.network.resolve(strict=True)
    corrupt = args.corrupt.resolve(strict=True)
    incompatible = args.incompatible.resolve(strict=True)
    wrong_basename = args.wrong_basename.resolve(strict=True)
    artifact_dir = args.artifact_dir.resolve()
    result_path = args.result.resolve()
    if artifact_dir.exists():
        raise RuntimeError(f"artifact directory must be fresh: {artifact_dir}")
    artifact_dir.mkdir(parents=True)
    if result_path.exists():
        raise RuntimeError(f"result path must be fresh: {result_path}")

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    addendum = json.loads(addendum_path.read_text(encoding="utf-8"))
    coverage_addendum = json.loads(
        coverage_addendum_path.read_text(encoding="utf-8")
    )
    if fixture.get("schema") != "crazyhouse-p6-fairy-comparator-adapter/v1":
        raise RuntimeError("fixture schema mismatch")
    if addendum.get("schema") != "crazyhouse-p6-fairy-comparator-adapter-addendum/v1":
        raise RuntimeError("addendum schema mismatch")
    if (
        coverage_addendum.get("schema")
        != "crazyhouse-p6-fairy-comparator-adapter-coverage-addendum/v1"
    ):
        raise RuntimeError("coverage addendum schema mismatch")

    failures: list[str] = []
    cases: list[dict[str, object]] = []

    def record(case_id: str, checks: dict[str, bool], details: dict[str, object]) -> None:
        failed = sorted(name for name, passed in checks.items() if not passed)
        failures.extend(f"{case_id}:{name}" for name in failed)
        cases.append(
            {"id": case_id, "checks": checks, "failed": failed, "details": details}
        )

    try:
        identities = {
            "fixture": {"bytes": fixture_path.stat().st_size, "sha256": sha256(fixture_path)},
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256(addendum_path),
            },
            "coverage_addendum": {
                "bytes": coverage_addendum_path.stat().st_size,
                "sha256": sha256(coverage_addendum_path),
            },
            "source": {"bytes": source_path.stat().st_size, "sha256": sha256(source_path)},
            "adapter_a": {"bytes": adapter_a.stat().st_size, "sha256": sha256(adapter_a)},
            "adapter_b": {"bytes": adapter_b.stat().st_size, "sha256": sha256(adapter_b)},
            "engine": {"bytes": engine.stat().st_size, "sha256": sha256(engine)},
            "network": {"bytes": network.stat().st_size, "sha256": sha256(network)},
            "corrupt": {"bytes": corrupt.stat().st_size, "sha256": sha256(corrupt)},
            "incompatible": {
                "bytes": incompatible.stat().st_size,
                "sha256": sha256(incompatible),
            },
            "wrong_basename": {
                "bytes": wrong_basename.stat().st_size,
                "sha256": sha256(wrong_basename),
            },
        }
        record(
            "input-identities",
            {
                "parent_fixture_hash": identities["fixture"]["sha256"]
                == addendum["parent_fixture"]["sha256"],
                "parent_fixture_bytes": identities["fixture"]["bytes"]
                == addendum["parent_fixture"]["bytes"],
                "coverage_parent_fixture_hash": identities["fixture"]["sha256"]
                == coverage_addendum["parent_fixture"]["sha256"],
                "coverage_identity_addendum_hash": identities["addendum"]["sha256"]
                == coverage_addendum["identity_correction"]["sha256"],
                "reproducible_adapter_bytes": adapter_a.read_bytes() == adapter_b.read_bytes(),
                "engine_bytes": identities["engine"]["bytes"]
                == fixture["raw_executable"]["bytes"],
                "engine_sha256": identities["engine"]["sha256"]
                == fixture["raw_executable"]["sha256"],
                "network_bytes": identities["network"]["bytes"]
                == fixture["network"]["bytes"],
                "network_sha256": identities["network"]["sha256"]
                == fixture["network"]["sha256"],
                "corrupt_fixture": identities["corrupt"]["sha256"]
                == fixture["network_negative_cases"][1]["source_fixture_sha256"],
                "incompatible_fixture": identities["incompatible"]["sha256"]
                == fixture["network_negative_cases"][2]["source_fixture_sha256"],
                "wrong_basename_bytes": identities["wrong_basename"]["sha256"]
                == fixture["network_negative_cases"][3]["fixture_sha256"],
                "wrong_basename_name": wrong_basename.name
                == fixture["network_negative_cases"][3]["fixture_basename"],
            },
            identities,
        )

        version = subprocess.run(
            [str(adapter_a), "--version"],
            cwd=str(adapter_a.parent),
            check=False,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        version_lines = version.stdout.splitlines()
        record(
            "version",
            {
                "exit_zero": version.returncode == 0,
                "stderr_empty": version.stderr == "",
                "version_exact": version_lines[:1] == ["fairy-crazyhouse-adapter 1"],
                "engine_pin": f"engine_sha256 {fixture['raw_executable']['sha256']}"
                in version_lines,
                "network_pin": f"network_sha256 {fixture['network']['sha256']}"
                in version_lines,
            },
            {"stdout": version_lines, "stderr": version.stderr},
        )

        startup_root = artifact_dir / "startup-fixtures"
        startup_root.mkdir()

        def startup_rejection(
            case_id: str,
            engine_path: Path,
            network_path: Path,
            expected_error: str,
        ) -> None:
            process = UciProcess(
                adapter_command(adapter_a, engine_path, network_path),
                artifact_dir,
                case_id,
                adapter_a.parent,
            )
            try:
                process.wait_stdout(lambda line: line == expected_error, timeout)
                child_snapshot = {
                    pid: item
                    for pid, item in descendants(process.process.pid).items()
                    if item[1].lower() == "stockfish.exe"
                }
                exit_code = process.wait_exit(timeout)
                lines = process.output_from(0)
            finally:
                lifecycle = process.finish(3, send_quit=False)
            record(
                case_id,
                {
                    "typed_error": lines.count(expected_error) == 1,
                    "exit_3": exit_code == 3,
                    "no_child_observed": not child_snapshot,
                    "lifecycle": bool(lifecycle["pass"]),
                },
                {
                    "stdout": lines,
                    "child_snapshot": child_snapshot,
                    "lifecycle": lifecycle,
                },
            )

        missing_engine = startup_root / "missing-engine" / "stockfish.exe"
        missing_engine.parent.mkdir()
        missing_network = startup_root / "missing-network" / fixture["network"]["basename"]
        missing_network.parent.mkdir()
        startup_rejection(
            "missing-engine",
            missing_engine,
            network,
            "info string ERROR startup code=fairy_adapter_engine_missing",
        )
        startup_rejection(
            "wrong-engine-identity",
            adapter_b,
            network,
            "info string ERROR startup code=fairy_adapter_engine_identity_mismatch",
        )
        startup_rejection(
            "startup-missing-network",
            engine,
            missing_network,
            "info string ERROR startup code=legacy_missing_file",
        )
        startup_rejection(
            "startup-wrong-network-basename",
            engine,
            wrong_basename,
            "info string ERROR startup code=legacy_basename_mismatch",
        )

        profile = fixture["profile"]
        positive = UciProcess(
            adapter_command(adapter_a, engine, network),
            artifact_dir,
            "positive",
            adapter_a.parent,
        )
        try:
            inventory = positive.initialize(timeout)
            option_names = [
                line.split(" type ", 1)[0][len("option name ") :].lower()
                for line in inventory
                if line.startswith("option name ") and " type " in line
            ]
            record(
                "inventory",
                {
                    **{
                        f"required:{line}": inventory.count(line) == 1
                        for line in fixture["inventory"]["required_exact"]
                    },
                    **{
                        f"raw:{line}": inventory.count(line) == 1
                        for line in fixture["inventory"]["required_raw_exact"]
                    },
                    "unique_option_names": len(option_names) == len(set(option_names)),
                    "raw_variant_hidden": not any(
                        line.startswith("option name UCI_Variant ")
                        and line != fixture["inventory"]["required_exact"][0]
                        for line in inventory
                    ),
                    "profile_hash_absent": not any(
                        line.startswith("option name CrazyhouseProfileHash ")
                        for line in inventory
                    ),
                },
                {"line_count": len(inventory), "lines": inventory},
            )
            nonce = fixture["capability_cases"][0]["nonce"]
            start = configure_adapter(positive, profile, network, nonce)
            positive.wait_stdout(lambda line: line == "readyok", timeout)
            ready_lines = positive.output_from(start)
            route_indices = [
                index
                for index, line in enumerate(ready_lines)
                if line.startswith(
                    "info string route_commit status=ok ruleset=crazyhouse "
                )
            ]
            ack = exact_ack(profile, nonce)
            ack_indices = [
                index for index, line in enumerate(ready_lines) if line == ack
            ]
            ready_indices = [
                index for index, line in enumerate(ready_lines) if line == "readyok"
            ]
            record(
                "positive-route-bound",
                {
                    "one_route": len(route_indices) == 1,
                    "honest_backend": len(route_indices) == 1
                    and "backend=fairy-external" in ready_lines[route_indices[0]],
                    "honest_evaluator": len(route_indices) == 1
                    and "evaluator=halfkav2variants" in ready_lines[route_indices[0]],
                    "network_identity": len(route_indices) == 1
                    and f"identity={fixture['network']['sha256']}"
                    in ready_lines[route_indices[0]],
                    "one_ack": len(ack_indices) == 1,
                    "one_readyok": len(ready_indices) == 1,
                    "order": bool(route_indices and ack_indices and ready_indices)
                    and route_indices[0] < ack_indices[0] < ready_indices[0],
                },
                {"stdout": ready_lines},
            )
            second_start = positive.output_length()
            positive.send("isready")
            positive.wait_stdout(lambda line: line == "readyok", timeout)
            second_lines = positive.output_from(second_start)
            record(
                "one-shot-readiness",
                {
                    "one_route": sum("route_commit status=ok" in line for line in second_lines)
                    == 1,
                    "no_ack": not any(
                        "crazyhouse_capability_ack" in line for line in second_lines
                    ),
                    "readyok": second_lines.count("readyok") == 1,
                },
                {"stdout": second_lines},
            )
        finally:
            positive_lifecycle = positive.finish(0)
        record(
            "positive-lifecycle",
            {"clean": bool(positive_lifecycle["pass"])},
            positive_lifecycle,
        )

        standard = UciProcess(
            adapter_command(adapter_a, engine, network),
            artifact_dir,
            "standard-control",
            adapter_a.parent,
        )
        try:
            standard.initialize(timeout)
            start = standard.output_length()
            standard.send("setoption name UCI_Variant value chess")
            standard.send("isready")
            standard.wait_stdout(lambda line: line == "readyok", timeout)
            standard_lines = standard.output_from(start)
        finally:
            standard_lifecycle = standard.finish(0)
        record(
            "standard-control",
            {
                "readyok": standard_lines.count("readyok") == 1,
                "no_route": not any("route_commit" in line for line in standard_lines),
                "no_ack": not any(
                    "crazyhouse_capability_ack" in line for line in standard_lines
                ),
                "no_error": not any(
                    line.startswith("info string ERROR") for line in standard_lines
                ),
                "lifecycle": bool(standard_lifecycle["pass"]),
            },
            {"stdout": standard_lines, "lifecycle": standard_lifecycle},
        )

        def rejection_case(
            case_id: str,
            commands: list[str],
            expected_line: str,
        ) -> None:
            process = UciProcess(
                adapter_command(adapter_a, engine, network),
                artifact_dir,
                case_id,
                adapter_a.parent,
            )
            try:
                process.initialize(timeout)
                start = process.output_length()
                for command in commands:
                    process.send(command)
                process.wait_stdout(lambda line: line == expected_line, timeout)
                exit_code = process.wait_exit(timeout)
                lines = process.output_from(start)
            finally:
                lifecycle = process.finish(20, send_quit=False)
            record(
                case_id,
                {
                    "typed_error": lines.count(expected_line) == 1,
                    "readyok_withheld": "readyok" not in lines,
                    "ack_withheld": not any(
                        "crazyhouse_capability_ack" in line for line in lines
                    ),
                    "exit_20": exit_code == 20,
                    "lifecycle": bool(lifecycle["pass"]),
                },
                {"stdout": lines, "lifecycle": lifecycle},
            )

        base_configuration = [
            "setoption name UCI_Variant value crazyhouse",
            f"setoption name CrazyhouseProfile value {profile['token']}",
            f"setoption name CrazyhouseEvalFile value {network}",
        ]
        rejection_case(
            "invalid-uppercase-nonce",
            base_configuration
            + [
                "setoption name CrazyhouseCapabilityNonce value 0123456789ABCDEF0123456789ABCDEF",
                "isready",
            ],
            "info string ERROR isready code=crazyhouse_capability_nonce_invalid",
        )
        rejection_case(
            "wrong-profile",
            [
                "setoption name UCI_Variant value crazyhouse",
                "setoption name CrazyhouseProfile value "
                "LICHESS_CRAZYHOUSE_2026_08_12@"
                + "0" * 64,
                f"setoption name CrazyhouseEvalFile value {network}",
                "setoption name CrazyhouseCapabilityNonce value 0123456789abcdef0123456789abcdef",
                "isready",
            ],
            "info string ERROR isready code=crazyhouse_profile_mismatch",
        )
        rejection_case(
            "use-nnue-false",
            ["setoption name Use NNUE value false"],
            "info string ERROR setoption code=fairy_adapter_nnue_disabled",
        )

        negative_root = artifact_dir / "network-negative-fixtures"
        negative_root.mkdir()
        missing_path = negative_root / "missing" / fixture["network"]["basename"]
        corrupt_path = negative_root / "corrupt" / fixture["network"]["basename"]
        corrupt_path.parent.mkdir()
        shutil.copyfile(corrupt, corrupt_path)
        incompatible_path = (
            negative_root / "incompatible" / fixture["network"]["basename"]
        )
        incompatible_path.parent.mkdir()
        shutil.copyfile(incompatible, incompatible_path)

        for case_id, path, code in [
            ("missing-approved-basename", missing_path, "legacy_missing_file"),
            (
                "corrupt-approved-basename",
                corrupt_path,
                "legacy_network_identity_mismatch",
            ),
            (
                "incompatible-approved-basename",
                incompatible_path,
                "legacy_network_identity_mismatch",
            ),
            (
                "byte-identical-wrong-basename",
                wrong_basename,
                "legacy_basename_mismatch",
            ),
        ]:
            rejection_case(
                case_id,
                [
                    "setoption name UCI_Variant value crazyhouse",
                    f"setoption name CrazyhouseProfile value {profile['token']}",
                    f"setoption name CrazyhouseEvalFile value {path}",
                    "setoption name CrazyhouseCapabilityNonce value 0123456789abcdef0123456789abcdef",
                    "isready",
                ],
                f"info string ERROR isready code={code}",
            )

        exact_rebind_path = negative_root / "exact-rebind" / fixture["network"]["basename"]
        exact_rebind_path.parent.mkdir()
        shutil.copyfile(network, exact_rebind_path)
        rebind = UciProcess(
            adapter_command(adapter_a, engine, network),
            artifact_dir,
            "exact-network-rebind",
            adapter_a.parent,
        )
        try:
            rebind.initialize(timeout)
            nonce = "fedcba9876543210fedcba9876543210"
            start = configure_adapter(rebind, profile, exact_rebind_path, nonce)
            rebind.wait_stdout(lambda line: line == "readyok", timeout)
            ready_lines = rebind.output_from(start)
            search_start = rebind.output_length()
            rebind.send("position startpos")
            rebind.send("go nodes 1024")
            rebind.wait_stdout(lambda line: line.startswith("bestmove "), timeout)
            search_lines = rebind.output_from(search_start)
        finally:
            rebind_lifecycle = rebind.finish(0)
        exact_rebind_marker = (
            f"info string NNUE evaluation using {exact_rebind_path} enabled"
        )
        record(
            "exact-network-rebind",
            {
                "route": sum("route_commit status=ok" in line for line in ready_lines)
                == 1,
                "ack": ready_lines.count(exact_ack(profile, nonce)) == 1,
                "readyok": ready_lines.count("readyok") == 1,
                "search_marker": search_lines.count(exact_rebind_marker) == 1,
                "marker_first": bool(search_lines)
                and search_lines[0] == exact_rebind_marker,
                "bestmove": sum(
                    line.startswith("bestmove ") for line in search_lines
                )
                == 1,
                "lifecycle": bool(rebind_lifecycle["pass"]),
            },
            {
                "ready_stdout": ready_lines,
                "search_stdout": search_lines,
                "lifecycle": rebind_lifecycle,
            },
        )

        transparency_rows: list[dict[str, object]] = []
        for case in fixture["fixed_node_transparency"]["cases"]:
            raw = run_search_case(
                mode="raw",
                case=case,
                adapter=adapter_a,
                engine=engine,
                network=network,
                profile=profile,
                artifact_dir=artifact_dir,
                timeout=timeout,
            )
            adapted = run_search_case(
                mode="adapted",
                case=case,
                adapter=adapter_a,
                engine=engine,
                network=network,
                profile=profile,
                artifact_dir=artifact_dir,
                timeout=timeout,
            )
            equal = raw["normalized"] == adapted["normalized"]
            row = {
                "id": case["id"],
                "equal": equal,
                "raw": raw,
                "adapted": adapted,
            }
            transparency_rows.append(row)
            record(
                f"transparency-{case['id']}",
                {
                    "raw_checks": all(raw["checks"].values()),
                    "raw_lifecycle": bool(raw["lifecycle"]["pass"]),
                    "adapted_checks": all(adapted["checks"].values()),
                    "adapted_lifecycle": bool(adapted["lifecycle"]["pass"]),
                    "normalized_equal": equal,
                },
                row,
            )

        perft_contract = coverage_addendum["perft_transparency"]
        raw_perft = run_perft_case(
            mode="raw",
            perft=perft_contract,
            adapter=adapter_a,
            engine=engine,
            network=network,
            profile=profile,
            artifact_dir=artifact_dir,
            timeout=timeout,
        )
        adapted_perft = run_perft_case(
            mode="adapted",
            perft=perft_contract,
            adapter=adapter_a,
            engine=engine,
            network=network,
            profile=profile,
            artifact_dir=artifact_dir,
            timeout=timeout,
        )
        record(
            "perft-transparency",
            {
                "raw_checks": all(raw_perft["checks"].values()),
                "raw_lifecycle": bool(raw_perft["lifecycle"]["pass"]),
                "adapted_checks": all(adapted_perft["checks"].values()),
                "adapted_lifecycle": bool(adapted_perft["lifecycle"]["pass"]),
                "ordered_output_equal": raw_perft["lines"] == adapted_perft["lines"],
            },
            {"raw": raw_perft, "adapted": adapted_perft},
        )

        eof_process = UciProcess(
            adapter_command(adapter_a, engine, network),
            artifact_dir,
            "stdin-eof",
            adapter_a.parent,
        )
        eof_process.initialize(timeout)
        eof_child_map = descendants(eof_process.process.pid)
        eof_child_pids = sorted(
            pid
            for pid, item in eof_child_map.items()
            if item[1].lower() == "stockfish.exe"
        )
        eof_process.close_stdin()
        eof_exit = eof_process.wait_exit(timeout)
        eof_lifecycle = eof_process.finish(0, send_quit=False)
        record(
            "stdin-eof",
            {
                "one_child_before": len(eof_child_pids) == 1,
                "exit_zero": eof_exit == 0,
                "lifecycle": bool(eof_lifecycle["pass"]),
                "child_absent": all(not pid_exists(pid) for pid in eof_child_pids),
            },
            {"child_pids": eof_child_pids, "lifecycle": eof_lifecycle},
        )

        killed_child = UciProcess(
            adapter_command(adapter_a, engine, network),
            artifact_dir,
            "unexpected-child-exit",
            adapter_a.parent,
        )
        try:
            killed_child.initialize(timeout)
            child_map = descendants(killed_child.process.pid)
            child_pids = sorted(
                pid
                for pid, item in child_map.items()
                if item[1].lower() == "stockfish.exe"
            )
            if len(child_pids) != 1:
                raise RuntimeError(
                    f"unexpected-child-exit: expected one child, observed {child_map}"
                )
            child_pid = child_pids[0]
            start = killed_child.output_length()
            terminate_exact_pid(child_pid, 77)
            expected_error = (
                "info string ERROR child code=fairy_adapter_child_unexpected_exit"
            )
            killed_child.wait_stdout(lambda line: line == expected_error, timeout)
            adapter_exit = killed_child.wait_exit(timeout)
            lines = killed_child.output_from(start)
            time.sleep(0.25)
            restarted = {
                pid: item
                for pid, item in descendants(killed_child.process.pid).items()
                if item[1].lower() == "stockfish.exe"
            }
        finally:
            killed_lifecycle = killed_child.finish(20, send_quit=False)
        record(
            "unexpected-child-exit",
            {
                "one_owned_child": len(child_pids) == 1,
                "typed_error": lines.count(expected_error) == 1,
                "adapter_exit_20": adapter_exit == 20,
                "no_restart": not restarted,
                "child_absent": not pid_exists(child_pid),
                "lifecycle": bool(killed_lifecycle["pass"]),
            },
            {
                "child_pid": child_pid,
                "stdout": lines,
                "restart_snapshot": restarted,
                "lifecycle": killed_lifecycle,
            },
        )

        result = {
            "schema": "crazyhouse-p6-fairy-comparator-adapter-result/v1",
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fixture": str(fixture_path),
            "addendum": str(addendum_path),
            "coverage_addendum": str(coverage_addendum_path),
            "identities": identities,
            "case_count": len(cases),
            "failures": failures,
            "cases": cases,
            "result": "PASS" if not failures else "FAIL",
            "gate_credit": not failures,
            "strength_claim": False,
            "timing_evidence": False,
            "openbench_evidence": False,
        }
    except Exception as error:
        result = {
            "schema": "crazyhouse-p6-fairy-comparator-adapter-result/v1",
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fixture": str(fixture_path),
            "addendum": str(addendum_path),
            "coverage_addendum": str(coverage_addendum_path),
            "failures": failures + [f"harness:{type(error).__name__}:{error}"],
            "cases": cases,
            "result": "FAIL_HARNESS_EXCEPTION",
            "gate_credit": False,
            "strength_claim": False,
            "timing_evidence": False,
            "openbench_evidence": False,
        }
    with result_path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps({"result": result["result"], "failures": result["failures"]}))
    return 0 if result["result"] == "PASS" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--addendum", type=Path, required=True)
    parser.add_argument("--coverage-addendum", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--adapter-a", type=Path, required=True)
    parser.add_argument("--adapter-b", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--corrupt", type=Path, required=True)
    parser.add_argument("--incompatible", type=Path, required=True)
    parser.add_argument("--wrong-basename", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main(parse_args()))
