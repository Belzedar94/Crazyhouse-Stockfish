#!/usr/bin/env python3
"""Differential standard-chess control for an exact official upstream merge."""

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
    *, binary: Path, out_dir: Path, label: str, stdin: bytes, timeout_seconds: int
) -> dict[str, Any]:
    started = time.time()
    creationflags = BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(binary)],
        cwd=str(binary.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    write_new(
        out_dir / f"{label}.process.json",
        (json.dumps({"label": label, "pid": process.pid}, indent=2) + "\n").encode(),
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
    observation = {
        "label": label,
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
    return {"observation": observation, "stdout": stdout, "stderr": stderr}


def option_name(line: str) -> str:
    prefix = "option name "
    marker = " type "
    if not line.startswith(prefix) or marker not in line:
        raise RuntimeError(f"malformed option line: {line!r}")
    return line[len(prefix) : line.index(marker)]


def session_input(*, role: str, network: Path, command: str | None) -> bytes:
    network_text = str(network)
    if "\r" in network_text or "\n" in network_text:
        raise RuntimeError("network path contains a line break")
    commands = ["uci"]
    if role == "product":
        commands.append("setoption name UCI_Variant value chess")
    commands.extend([f"setoption name EvalFile value {network_text}", "isready"])
    if command is not None:
        commands.append(command)
    commands.append("quit")
    return ("\n".join(commands) + "\n").encode()


def authenticate_common_protocol(
    *, role: str, label: str, stdout: bytes, expected_route_commits: int
) -> list[str]:
    lines = [line.rstrip("\r") for line in decode(stdout, f"{label} stdout").splitlines()]
    if lines.count("uciok") != 1:
        raise RuntimeError(f"{label}: expected exactly one uciok")
    if lines.count("readyok") != 1:
        raise RuntimeError(f"{label}: expected exactly one readyok")
    if any("info string ERROR " in line or "READY state=failed" in line for line in lines):
        raise RuntimeError(f"{label}: protocol error marker")
    commits = [line for line in lines if "info string route_commit status=ok" in line]
    if role == "official" and commits:
        raise RuntimeError(f"{label}: official control emitted product route markers")
    if role == "product":
        if len(commits) != expected_route_commits:
            raise RuntimeError(
                f"{label}: expected {expected_route_commits} route commits, got {len(commits)}"
            )
        if any("ruleset=chess" not in line or "backend=official-chess" not in line for line in commits):
            raise RuntimeError(f"{label}: unauthenticated standard route: {commits}")
    return lines


def bench_signature(command: str, nodes: int, bestmoves: list[str]) -> str:
    material = {"command": command, "nodes_searched": nodes, "bestmoves": bestmoves}
    return sha256_bytes(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-binary", required=True, type=Path)
    parser.add_argument("--product-binary", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--expected-official-sha256", required=True)
    parser.add_argument("--expected-product-sha256", required=True)
    args = parser.parse_args()

    for value in (args.expected_official_sha256, args.expected_product_sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise RuntimeError("binary identity is not 64 lowercase hexadecimal characters")

    official = args.official_binary.resolve(strict=True)
    product = args.product_binary.resolve(strict=True)
    network = args.network.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    out_dir = args.out_dir.resolve(strict=True)
    if any(out_dir.iterdir()):
        raise RuntimeError("output directory is not empty")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema") != "crazyhouse-upstream-sync-control/v1":
        raise RuntimeError("contract schema mismatch")

    official_sha = sha256_file(official)
    product_sha = sha256_file(product)
    network_sha = sha256_file(network)
    if official_sha != args.expected_official_sha256:
        raise RuntimeError(f"official binary SHA-256 mismatch: {official_sha}")
    if product_sha != args.expected_product_sha256:
        raise RuntimeError(f"product binary SHA-256 mismatch: {product_sha}")
    if network.name != contract["network"]["filename"]:
        raise RuntimeError(f"network filename mismatch: {network.name}")
    if not network_sha.startswith(contract["network"]["sha256_prefix"]):
        raise RuntimeError(f"network SHA-256 prefix mismatch: {network_sha}")

    write_new(
        out_dir / "harness.process.json",
        (json.dumps({"pid": os.getpid(), "parent_pid": os.getppid()}, indent=2) + "\n").encode(),
    )

    handshakes: dict[str, dict[str, Any]] = {}
    inventories: dict[str, dict[str, Any]] = {}
    for role, binary in (("official", official), ("product", product)):
        label = f"{role}-handshake"
        run = run_engine(
            binary=binary,
            out_dir=out_dir,
            label=label,
            stdin=session_input(role=role, network=network, command=None),
            timeout_seconds=contract["timeouts"]["handshake_seconds"],
        )
        if run["stderr"]:
            raise RuntimeError(f"{label}: handshake emitted stderr")
        lines = authenticate_common_protocol(
            role=role,
            label=label,
            stdout=run["stdout"],
            expected_route_commits=contract["route"]["handshake_commits"],
        )
        option_lines = [line for line in lines if line.startswith("option name ")]
        inventories[role] = {
            "count": len(option_lines),
            "names": [option_name(line) for line in option_lines],
            "lines": option_lines,
            "sha256": sha256_bytes(("\n".join(option_lines) + "\n").encode()),
        }
        handshakes[role] = run["observation"]

    official_by_name = {
        option_name(line): line for line in inventories["official"]["lines"]
    }
    product_by_name = {
        option_name(line): line for line in inventories["product"]["lines"]
    }
    extra_names = [
        name for name in inventories["product"]["names"] if name not in official_by_name
    ]
    if extra_names != contract["options"]["product_extra_names"]:
        raise RuntimeError(f"product option additions mismatch: {extra_names}")
    common_product_names = [
        name for name in inventories["product"]["names"] if name in official_by_name
    ]
    if common_product_names != inventories["official"]["names"]:
        raise RuntimeError("common option ordering differs from official")
    for name, line in official_by_name.items():
        if product_by_name.get(name) != line:
            raise RuntimeError(f"common option line differs for {name}")
    for line in contract["options"]["required_product_lines"]:
        if line not in inventories["product"]["lines"]:
            raise RuntimeError(f"required product option line missing: {line}")

    bench = contract["bench"]
    observations: dict[str, list[dict[str, Any]]] = {"official": [], "product": []}
    signatures: dict[str, list[str]] = {"official": [], "product": []}
    for role, binary in (("official", official), ("product", product)):
        for index in range(1, bench["runs_per_binary"] + 1):
            label = f"{role}-bench-{index:02d}"
            run = run_engine(
                binary=binary,
                out_dir=out_dir,
                label=label,
                stdin=session_input(role=role, network=network, command=bench["command"]),
                timeout_seconds=contract["timeouts"]["bench_seconds"],
            )
            lines = authenticate_common_protocol(
                role=role,
                label=label,
                stdout=run["stdout"],
                expected_route_commits=contract["route"]["bench_commits"],
            )
            stderr_text = decode(run["stderr"], f"{label} stderr")
            node_matches = re.findall(
                r"^Nodes searched\s*:\s*(\d+)\s*$", stderr_text, re.MULTILINE
            )
            if len(node_matches) != 1:
                raise RuntimeError(f"{label}: aggregate node record mismatch: {node_matches}")
            nodes = int(node_matches[0])
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            if len(bestmoves) != bench["bestmoves_each"]:
                raise RuntimeError(f"{label}: bestmove count {len(bestmoves)}")
            signature = bench_signature(bench["command"], nodes, bestmoves)
            signatures[role].append(signature)
            observations[role].append(
                {
                    **run["observation"],
                    "nodes_searched": nodes,
                    "bestmove_count": len(bestmoves),
                    "deterministic_signature_sha256": signature,
                }
            )

    for role in ("official", "product"):
        if len(set(signatures[role])) != 1:
            raise RuntimeError(f"{role} fresh-process bench signatures differ")
    if signatures["official"][0] != signatures["product"][0]:
        raise RuntimeError("product standard bench differs from exact official upstream")

    manifest = {
        "schema": "crazyhouse-upstream-sync-control-runtime/v1",
        "result": "PASS_EXACT_OFFICIAL_STANDARD_CONTROL",
        "contract": {
            "path": str(contract_path),
            "bytes": contract_path.stat().st_size,
            "sha256": sha256_file(contract_path),
        },
        "official": {
            "binary": {"path": str(official), "bytes": official.stat().st_size, "sha256": official_sha},
            "handshake": handshakes["official"],
            "options": inventories["official"],
            "bench": observations["official"],
        },
        "product": {
            "binary": {"path": str(product), "bytes": product.stat().st_size, "sha256": product_sha},
            "handshake": handshakes["product"],
            "options": inventories["product"],
            "bench": observations["product"],
        },
        "network": {"path": str(network), "bytes": network.stat().st_size, "sha256": network_sha},
        "relation": {
            "official_repetitions_identical": True,
            "product_repetitions_identical": True,
            "product_equals_exact_official": True,
            "deterministic_signature_sha256": signatures["official"][0],
            "timing_fields_excluded": True,
        },
        "timing_evidence": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_claim": False,
    }
    write_new(
        out_dir / "runtime-manifest.json",
        (json.dumps(manifest, indent=2) + "\n").encode(),
    )
    print(
        "PASS upstream_sync_standard_control "
        f"runs={bench['runs_per_binary']}+{bench['runs_per_binary']} "
        f"bestmoves={bench['bestmoves_each']} signature={signatures['official'][0]}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write(f"HARNESS_FAILURE: {type(exc).__name__}: {exc}\n")
        raise
