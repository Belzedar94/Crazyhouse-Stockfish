#!/usr/bin/env python3
"""Authenticate the standard-chess control through the transactional UCI route."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)


def decode(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{label} is not UTF-8: {exc}") from exc


def run_engine(
    *,
    binary: Path,
    cwd: Path,
    out_dir: Path,
    label: str,
    stdin: bytes,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.time()
    creationflags = BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(binary)],
        cwd=str(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    write_new(
        out_dir / f"{label}.process.json",
        (json.dumps({"label": label, "pid": process.pid}, indent=2) + "\n").encode("utf-8"),
    )
    try:
        stdout, stderr = process.communicate(input=stdin, timeout=timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        timed_out = True

    completed = time.time()
    write_new(out_dir / f"{label}.stdout.log", stdout)
    write_new(out_dir / f"{label}.stderr.log", stderr)
    result = {
        "label": label,
        "arguments": [],
        "stdin_utf8": stdin.decode("utf-8"),
        "pid": process.pid,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "elapsed_wall_ms_observation_only": round((completed - started) * 1000),
        "stdout_bytes": len(stdout),
        "stdout_sha256": sha256_bytes(stdout),
        "stderr_bytes": len(stderr),
        "stderr_sha256": sha256_bytes(stderr),
    }
    if timed_out:
        raise RuntimeError(f"{label}: timeout after {timeout_seconds} seconds")
    if process.returncode != 0:
        raise RuntimeError(f"{label}: engine exit {process.returncode}")
    return {"result": result, "stdout": stdout, "stderr": stderr}


def parse_option_name(line: str) -> str:
    prefix = "option name "
    marker = " type "
    if not line.startswith(prefix) or marker not in line:
        raise RuntimeError(f"malformed UCI option line: {line!r}")
    return line[len(prefix) : line.index(marker)]


def session_input(
    network: Path, command: str | None, *, stage_chess960_before_command: bool = False
) -> bytes:
    network_text = str(network)
    if "\n" in network_text or "\r" in network_text:
        raise RuntimeError("network path contains a line break")
    commands = [
        "uci",
        "setoption name UCI_Variant value chess",
        f"setoption name EvalFile value {network_text}",
        "isready",
    ]
    if stage_chess960_before_command:
        commands.extend(["setoption name UCI_Chess960 value true", "isready"])
    if command is not None:
        commands.append(command)
    commands.append("quit")
    return ("\n".join(commands) + "\n").encode("utf-8")


def authenticate_route(
    stdout_lines: list[str], label: str, *, expected_commits: int, expected_readyoks: int
) -> list[str]:
    commits = [
        line
        for line in stdout_lines
        if "info string route_commit status=ok" in line
    ]
    if len(commits) != expected_commits:
        raise RuntimeError(
            f"{label}: expected {expected_commits} authenticated chess route commits, got {commits}"
        )
    if any("ruleset=chess" not in line or "backend=official-chess" not in line for line in commits):
        raise RuntimeError(f"{label}: unauthenticated route commit: {commits}")
    identities = [line.split(" identity=", 1)[1] for line in commits if " identity=" in line]
    if len(identities) != len(commits) or len(set(identities)) != 1:
        raise RuntimeError(f"{label}: route identity drifted: {commits}")
    if stdout_lines.count("readyok") != expected_readyoks:
        raise RuntimeError(f"{label}: expected exactly {expected_readyoks} readyok records")
    if any("readyok_withheld=1" in line for line in stdout_lines):
        raise RuntimeError(f"{label}: successful route reported readyok withholding")
    if any("info string ERROR " in line or "READY state=failed" in line for line in stdout_lines):
        raise RuntimeError(f"{label}: successful route reported an error")
    return commits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--expected-binary-sha256", required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_binary_sha256):
        raise RuntimeError("binary identity is not 64 lowercase hexadecimal characters")

    binary = args.binary.resolve(strict=True)
    network = args.network.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    out_dir = args.out_dir.resolve(strict=True)
    if any(out_dir.iterdir()):
        raise RuntimeError(f"runtime output directory is not empty: {out_dir}")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema") != "crazyhouse-routed-standard-control/v1":
        raise RuntimeError("contract schema mismatch")

    write_new(
        out_dir / "harness.process.json",
        (json.dumps({"pid": os.getpid(), "parent_pid": os.getppid()}, indent=2) + "\n").encode(
            "utf-8"
        ),
    )

    binary_sha256 = sha256_file(binary)
    network_sha256 = sha256_file(network)
    if binary_sha256 != args.expected_binary_sha256:
        raise RuntimeError(f"binary SHA-256 mismatch: {binary_sha256}")
    if network_sha256 != contract["route"]["official_network_sha256"]:
        raise RuntimeError(f"network SHA-256 mismatch: {network_sha256}")

    option_contract = contract["option_inventory"]
    handshake = run_engine(
        binary=binary,
        cwd=binary.parent,
        out_dir=out_dir,
        label="routed-uci-handshake",
        stdin=session_input(network, None),
        timeout_seconds=120,
    )
    handshake_stdout = decode(handshake["stdout"], "handshake stdout")
    handshake_stderr = decode(handshake["stderr"], "handshake stderr")
    handshake_lines = [line.rstrip("\r") for line in handshake_stdout.splitlines()]
    if handshake_stderr:
        raise RuntimeError("routed UCI handshake emitted stderr")
    if handshake_lines.count("uciok") != 1:
        raise RuntimeError("routed UCI handshake did not emit exactly one uciok")
    authenticate_route(
        handshake_lines, "routed UCI handshake", expected_commits=1, expected_readyoks=1
    )

    option_lines = [line for line in handshake_lines if line.startswith("option name ")]
    option_names = [parse_option_name(line) for line in option_lines]
    option_sha256 = sha256_bytes(("\n".join(option_lines) + "\n").encode("utf-8"))
    if option_names != option_contract["ordered_names"]:
        raise RuntimeError(f"ordered option names mismatch: {option_names}")
    if len(option_lines) != option_contract["routed_count"]:
        raise RuntimeError(f"routed option count mismatch: {len(option_lines)}")
    if option_sha256 != option_contract["routed_sha256"]:
        raise RuntimeError(f"routed option inventory SHA-256 mismatch: {option_sha256}")
    for inserted_line in option_contract["inserted_lines"]:
        if inserted_line not in option_lines:
            raise RuntimeError(f"missing routed option line: {inserted_line}")

    option_inventory = {
        "count": len(option_lines),
        "ordered_names": option_names,
        "ordered_lines": option_lines,
        "sha256": option_sha256,
    }
    write_new(
        out_dir / "routed-uci-option-inventory.json",
        (json.dumps(option_inventory, indent=2) + "\n").encode("utf-8"),
    )

    bench_contract = contract["bench"]
    if bench_contract["runs"] != 3:
        raise RuntimeError("the frozen contract requires exactly three bench runs")
    bench_results: list[dict[str, Any]] = []
    signatures: list[str] = []
    for index in range(1, bench_contract["runs"] + 1):
        label = f"routed-bench-{index:02d}"
        case = run_engine(
            binary=binary,
            cwd=binary.parent,
            out_dir=out_dir,
            label=label,
            stdin=session_input(network, bench_contract["command"]),
            timeout_seconds=300,
        )
        stdout_text = decode(case["stdout"], f"{label} stdout")
        stderr_text = decode(case["stderr"], f"{label} stderr")
        stdout_lines = [line.rstrip("\r") for line in stdout_text.splitlines()]
        authenticate_route(
            stdout_lines,
            label,
            expected_commits=bench_contract["route_commits_each"],
            expected_readyoks=1,
        )
        node_matches = re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", stderr_text, re.MULTILINE)
        if len(node_matches) != 1:
            raise RuntimeError(f"{label}: expected one aggregate node count, got {node_matches}")
        nodes = int(node_matches[0])
        bestmoves = [line for line in stdout_lines if line.startswith("bestmove ")]
        signature_material = {
            "command": bench_contract["command"],
            "nodes_searched": nodes,
            "bestmoves": bestmoves,
        }
        signature = sha256_bytes(
            json.dumps(signature_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if nodes != bench_contract["nodes_each"]:
            raise RuntimeError(f"{label}: node count mismatch: {nodes}")
        if len(bestmoves) != bench_contract["bestmoves_each"]:
            raise RuntimeError(f"{label}: bestmove count mismatch: {len(bestmoves)}")
        if signature != bench_contract["signature_sha256_each"]:
            raise RuntimeError(f"{label}: deterministic signature mismatch: {signature}")
        signatures.append(signature)
        bench_results.append(
            {
                **case["result"],
                "nodes_searched": nodes,
                "bestmove_count": len(bestmoves),
                "deterministic_signature_sha256": signature,
            }
        )

    if len(set(signatures)) != 1:
        raise RuntimeError(f"routed bench signatures differ: {signatures}")

    speedtest_contract = contract["speedtest_smoke"]
    speedtest = run_engine(
        binary=binary,
        cwd=binary.parent,
        out_dir=out_dir,
        label="routed-speedtest-smoke",
        stdin=session_input(
            network,
            speedtest_contract["command"],
            stage_chess960_before_command=speedtest_contract["stage_chess960_before_command"],
        ),
        timeout_seconds=120,
    )
    speedtest_stdout = decode(speedtest["stdout"], "speedtest stdout")
    speedtest_stderr = decode(speedtest["stderr"], "speedtest stderr")
    speedtest_lines = [line.rstrip("\r") for line in speedtest_stdout.splitlines()]
    speedtest_commits = authenticate_route(
        speedtest_lines,
        "routed speedtest smoke",
        expected_commits=speedtest_contract["route_commits"],
        expected_readyoks=speedtest_contract["readyoks"],
    )
    invocation = f"User invocation            : {speedtest_contract['command']}"
    if invocation not in speedtest_stderr:
        raise RuntimeError("routed speedtest did not authenticate its invocation")
    speedtest_nodes = re.findall(
        r"^Total nodes searched\s*:\s*(\d+)\s*$", speedtest_stderr, re.MULTILINE
    )
    if len(speedtest_nodes) != 1:
        raise RuntimeError(f"routed speedtest aggregate node record mismatch: {speedtest_nodes}")
    if speedtest_contract["require_positive_nodes"] and int(speedtest_nodes[0]) <= 0:
        raise RuntimeError("routed speedtest did not search a positive node count")

    manifest = {
        "schema": "crazyhouse-routed-standard-control-runtime/v1",
        "result": "PASS_ROUTED_STANDARD_CHESS_DETERMINISTIC_CONTROL",
        "scope": contract["scope"],
        "binary": {"path": str(binary), "bytes": binary.stat().st_size, "sha256": binary_sha256},
        "network": {
            "path": str(network),
            "bytes": network.stat().st_size,
            "sha256": network_sha256,
        },
        "contract": {
            "path": str(contract_path),
            "bytes": contract_path.stat().st_size,
            "sha256": sha256_file(contract_path),
        },
        "uci": {
            **handshake["result"],
            "route": {"ruleset": "chess", "backend": "official-chess"},
            "option_inventory": option_inventory,
        },
        "bench": {
            "runs": bench_results,
            "all_three_signatures_identical": True,
            "deterministic_signature_sha256": signatures[0],
            "timing_fields_excluded_from_signature": True,
        },
        "speedtest_smoke": {
            **speedtest["result"],
            "route_commit_count": len(speedtest_commits),
            "nodes_positive": int(speedtest_nodes[0]) > 0,
            "timing_evidence": False,
        },
        "strength_claim": False,
        "timing_evidence": False,
    }
    write_new(
        out_dir / "runtime-manifest.json",
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write(f"HARNESS_FAILURE: {type(exc).__name__}: {exc}\n")
        raise
