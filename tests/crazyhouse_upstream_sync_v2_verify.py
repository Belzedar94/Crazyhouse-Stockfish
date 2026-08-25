#!/usr/bin/env python3
"""Replay the exact standard-chess control against pinned official Stockfish."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import crazyhouse_routed_standard_control as routed


EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root is not an object: {path}")
    return value


def file_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    require(resolved.is_file(), f"not a file: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": routed.sha256_file(resolved),
    }


def write_json_new(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as output:
        output.write(payload)


def option_lines(text: str) -> list[str]:
    return [line.rstrip("\r") for line in text.splitlines() if line.startswith("option name ")]


def option_name(line: str) -> str:
    match = re.match(r"^option name (.+?) type ", line)
    require(match is not None, f"malformed UCI option line: {line}")
    return match.group(1)


def handshake_input(network: Path, *, product: bool) -> bytes:
    lines = ["uci"]
    if product:
        lines.append("setoption name UCI_Variant value chess")
    lines.extend(
        [
            f"setoption name EvalFile value {network}",
            "isready",
            "quit",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def bench_input(network: Path, command: str, *, product: bool) -> bytes:
    lines = ["uci"]
    if product:
        lines.append("setoption name UCI_Variant value chess")
    lines.extend(
        [
            f"setoption name EvalFile value {network}",
            "isready",
            command,
            "quit",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def authenticate_handshake(
    *,
    binary: Path,
    network: Path,
    out_dir: Path,
    label: str,
    product: bool,
    timeout_seconds: int,
) -> tuple[dict[str, Any], list[str]]:
    case = routed.run_engine(
        binary=binary,
        cwd=binary.parent,
        out_dir=out_dir,
        label=label,
        stdin=handshake_input(network, product=product),
        timeout_seconds=timeout_seconds,
    )
    stdout = routed.decode(case["stdout"], f"{label} stdout")
    stderr = routed.decode(case["stderr"], f"{label} stderr")
    require(case["result"]["exit_code"] == 0, f"{label}: nonzero exit")
    require(not case["result"]["timed_out"], f"{label}: timeout")
    require(not stderr, f"{label}: stderr was not empty")
    lines = [line.rstrip("\r") for line in stdout.splitlines()]
    require(lines.count("uciok") == 1, f"{label}: uciok count mismatch")
    require(lines.count("readyok") == 1, f"{label}: readyok count mismatch")
    route_lines = [line for line in lines if line.startswith("info string route_commit ")]
    if product:
        require(len(route_lines) == 1, f"{label}: product route count mismatch")
        require(
            "ruleset=chess" in route_lines[0] and "backend=official-chess" in route_lines[0],
            f"{label}: product route mismatch",
        )
    else:
        require(not route_lines, f"{label}: official binary emitted a product route marker")
    return case["result"], option_lines(stdout)


def deterministic_signature(command: str, nodes: int, bestmoves: list[str]) -> str:
    material = {
        "command": command,
        "nodes_searched": nodes,
        "bestmoves": bestmoves,
    }
    return routed.sha256_bytes(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def run_bench(
    *,
    binary: Path,
    network: Path,
    out_dir: Path,
    role: str,
    product: bool,
    command: str,
    runs: int,
    bestmoves_each: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index in range(1, runs + 1):
        label = f"{role}-bench-{index:02d}"
        case = routed.run_engine(
            binary=binary,
            cwd=binary.parent,
            out_dir=out_dir,
            label=label,
            stdin=bench_input(network, command, product=product),
            timeout_seconds=timeout_seconds,
        )
        stdout = routed.decode(case["stdout"], f"{label} stdout")
        stderr = routed.decode(case["stderr"], f"{label} stderr")
        require(case["result"]["exit_code"] == 0, f"{label}: nonzero exit")
        require(not case["result"]["timed_out"], f"{label}: timeout")
        lines = [line.rstrip("\r") for line in stdout.splitlines()]
        require(lines.count("uciok") == 1, f"{label}: uciok count mismatch")
        require(lines.count("readyok") == 1, f"{label}: readyok count mismatch")
        route_lines = [line for line in lines if line.startswith("info string route_commit ")]
        if product:
            require(len(route_lines) == 3, f"{label}: product route count mismatch")
            require(
                all("ruleset=chess" in line and "backend=official-chess" in line for line in route_lines),
                f"{label}: product route mismatch",
            )
        else:
            require(not route_lines, f"{label}: official binary emitted a product route marker")
        node_matches = re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", stderr, re.MULTILINE)
        require(len(node_matches) == 1, f"{label}: aggregate node count mismatch")
        nodes = int(node_matches[0])
        bestmoves = [line for line in lines if line.startswith("bestmove ")]
        require(len(bestmoves) == bestmoves_each, f"{label}: bestmove count mismatch")
        observations.append(
            {
                **case["result"],
                "nodes_searched": nodes,
                "bestmove_count": len(bestmoves),
                "deterministic_signature_sha256": deterministic_signature(
                    command, nodes, bestmoves
                ),
            }
        )
    return observations


def inventory(lines: list[str]) -> dict[str, Any]:
    return {
        "count": len(lines),
        "names": [option_name(line) for line in lines],
        "lines": lines,
        "sha256": routed.sha256_bytes(("\n".join(lines) + "\n").encode("utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official", required=True, type=Path)
    parser.add_argument("--product", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--expected-product-sha256", required=True)
    args = parser.parse_args()

    require(
        re.fullmatch(r"[0-9a-f]{64}", args.expected_product_sha256) is not None,
        "expected product SHA-256 is malformed",
    )
    official = args.official.resolve(strict=True)
    product = args.product.resolve(strict=True)
    network = args.network.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    calibration_path = args.calibration.resolve(strict=True)
    out_dir = args.out_dir.resolve(strict=False)
    require(not out_dir.exists(), f"output directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)

    contract = load_json(contract_path)
    calibration = load_json(calibration_path)
    require(contract.get("schema") == "crazyhouse-upstream-sync-control/v1", "contract schema mismatch")
    require(contract.get("contract_revision") == 2, "contract revision mismatch")
    require(
        calibration.get("schema") == "crazyhouse-upstream-sync-control-calibration/v1",
        "calibration schema mismatch",
    )
    contract_pin = calibration["pins"]["standard_control_v2"]
    require(contract_path.stat().st_size == contract_pin["bytes"], "contract size mismatch")
    require(routed.sha256_file(contract_path) == contract_pin["sha256"], "contract SHA-256 mismatch")

    official_record = file_record(official)
    product_record = file_record(product)
    network_record = file_record(network)
    official_pin = calibration["official_calibration"]["binary"]
    network_pin = calibration["official_calibration"]["network"]
    require(official_record["bytes"] == official_pin["bytes"], "official binary size mismatch")
    require(official_record["sha256"] == official_pin["sha256"], "official binary SHA-256 mismatch")
    require(product_record["sha256"] == args.expected_product_sha256, "product binary SHA-256 mismatch")
    require(network_record["bytes"] == network_pin["bytes"], "official network size mismatch")
    require(network_record["sha256"] == network_pin["sha256"], "official network SHA-256 mismatch")

    timeout_handshake = int(contract["timeouts"]["handshake_seconds"])
    timeout_bench = int(contract["timeouts"]["bench_seconds"])
    official_handshake, official_lines = authenticate_handshake(
        binary=official,
        network=network,
        out_dir=out_dir,
        label="official-handshake",
        product=False,
        timeout_seconds=timeout_handshake,
    )
    product_handshake, product_lines = authenticate_handshake(
        binary=product,
        network=network,
        out_dir=out_dir,
        label="product-handshake",
        product=True,
        timeout_seconds=timeout_handshake,
    )

    official_inventory = inventory(official_lines)
    product_inventory = inventory(product_lines)
    extra_names = set(contract["options"]["product_extra_names"])
    common_product_lines = [
        line for line in product_lines if option_name(line) not in extra_names
    ]
    require(common_product_lines == official_lines, "common UCI option lines or order drifted")
    for required in contract["options"]["required_product_lines"]:
        require(required in product_lines, f"missing product UCI option: {required}")

    bench = contract["bench"]
    command = bench["command"]
    runs = int(bench["runs_per_binary"])
    bestmoves_each = int(bench["bestmoves_each"])
    official_bench = run_bench(
        binary=official,
        network=network,
        out_dir=out_dir,
        role="official",
        product=False,
        command=command,
        runs=runs,
        bestmoves_each=bestmoves_each,
        timeout_seconds=timeout_bench,
    )
    product_bench = run_bench(
        binary=product,
        network=network,
        out_dir=out_dir,
        role="product",
        product=True,
        command=command,
        runs=runs,
        bestmoves_each=bestmoves_each,
        timeout_seconds=timeout_bench,
    )
    official_signatures = [entry["deterministic_signature_sha256"] for entry in official_bench]
    product_signatures = [entry["deterministic_signature_sha256"] for entry in product_bench]
    required_signature = calibration["future_replay"]["required_official_signature_sha256"]
    require(len(set(official_signatures)) == 1, "official repetitions are nondeterministic")
    require(len(set(product_signatures)) == 1, "product repetitions are nondeterministic")
    require(official_signatures[0] == required_signature, "official signature drifted")
    require(product_signatures[0] == required_signature, "product signature differs from official")

    manifest = {
        "schema": "crazyhouse-upstream-sync-control-runtime/v1",
        "result": "PASS_EXACT_OFFICIAL_STANDARD_CONTROL",
        "contract": file_record(contract_path),
        "calibration": file_record(calibration_path),
        "official": {
            "binary": official_record,
            "handshake": official_handshake,
            "options": official_inventory,
            "bench": official_bench,
        },
        "product": {
            "binary": product_record,
            "handshake": product_handshake,
            "options": product_inventory,
            "bench": product_bench,
        },
        "network": network_record,
        "relation": {
            "official_repetitions_identical": True,
            "product_repetitions_identical": True,
            "product_equals_exact_official": True,
            "deterministic_signature_sha256": required_signature,
            "timing_fields_excluded": True,
        },
        "timing_evidence": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_claim": False,
    }
    manifest_path = out_dir / "runtime-manifest.json"
    write_json_new(manifest_path, manifest)
    print(
        "PASS crazyhouse_upstream_sync_v2 "
        f"runs={runs}+{runs} bestmoves_each={bestmoves_each} signature={required_signature}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
