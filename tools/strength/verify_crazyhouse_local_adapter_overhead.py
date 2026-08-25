#!/usr/bin/env python3
"""Independently verify a formal Crazyhouse comparator-adapter overhead run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Iterable


ROUTE_PREFIX = "info string route_commit status=ok ruleset=crazyhouse "
ACK_PREFIX = "info string crazyhouse_capability_ack status=ok "
VOLATILE = re.compile(r"\s+(?:time|nps|hashfull)\s+\d+")
NODES = re.compile(r"(?:^|\s)nodes\s+(\d+)(?:\s|$)")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    require(resolved.is_file(), f"not a file: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def authenticate(path: Path, pin: dict[str, object], label: str) -> dict[str, object]:
    observed = identity(path)
    require(observed["bytes"] == pin["bytes"], f"{label} bytes drifted")
    require(observed["sha256"] == pin["sha256"], f"{label} SHA-256 drifted")
    return observed


def pinned_path(pin: dict[str, object], repository: Path) -> Path:
    path = Path(pin["path"])
    return path if path.is_absolute() else repository / path


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root is not an object: {path}")
    return value


def write_fresh(path: Path, value: object) -> None:
    require(not path.exists(), f"refusing to replace output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_utc(value: object, label: str) -> datetime:
    require(isinstance(value, str) and bool(value), f"{label} is not an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{label} is not an ISO timestamp") from exc
    require(parsed.tzinfo is not None, f"{label} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def percentile(values: list[float], probability: float) -> float:
    require(bool(values), "empty percentile sample")
    return sorted(values)[max(0, math.ceil(probability * len(values)) - 1)]


def mean_ucb99(values: list[float]) -> float:
    require(bool(values), "empty confidence-bound sample")
    mean = statistics.fmean(values)
    if len(values) == 1:
        return mean
    return mean + NormalDist().inv_cdf(0.99) * statistics.stdev(values) / math.sqrt(len(values))


def ratio_ucb99(adapter: list[float], raw: list[float]) -> float:
    require(len(adapter) == len(raw) > 0, "ratio samples are not paired")
    return math.exp(mean_ucb99([math.log(a / r) for a, r in zip(adapter, raw)]))


def normalize(lines: Iterable[str]) -> list[str]:
    result = []
    for line in lines:
        if line.startswith(ROUTE_PREFIX) or line.startswith(ACK_PREFIX):
            continue
        result.append(re.sub(r"[ \t]+", " ", VOLATILE.sub("", line)).rstrip())
    return result


def select_sample(rows: list[dict[str, object]], contract: dict[str, object]) -> list[dict[str, object]]:
    rule = contract["sample"]
    seed = str(rule["seed_utf8"]).encode("utf-8")
    count = int(rule["rows_per_depth"])
    selected: list[dict[str, object]] = []
    for depth in range(int(rule["depth_min"]), int(rule["depth_max"]) + 1):
        candidates = [row for row in rows if int(row["target_depth"]) == depth]
        candidates.sort(
            key=lambda row: hashlib.sha256(
                seed + b"\0" + str(row["id"]).encode("ascii")
            ).digest()
        )
        require(len(candidates) >= count, f"depth {depth} is undersized")
        selected.extend(candidates[:count])
    selected.sort(key=lambda row: int(row["accepted_index"]))
    projection = [
        [
            int(row["accepted_index"]),
            str(row["id"]),
            int(row["target_depth"]),
            str(row["canonical_fen"]),
        ]
        for row in selected
    ]
    require(hashlib.sha256(canonical(projection)).hexdigest() == rule["selection_sha256"], "sample digest drifted")
    require([int(row["accepted_index"]) for row in selected] == rule["accepted_indices"], "sample indices drifted")
    return selected


def validate_host(
    host: dict[str, object],
    contract: dict[str, object],
    repository: Path,
    effective_time: datetime,
) -> dict[str, object]:
    pin = contract["host_precondition"]["attestation_producer"]
    producer = authenticate(repository / pin["path"], pin, "host attestation producer")
    require(host.get("schema") == "crazyhouse-host-timing-attestation/v1", "host schema drifted")
    require(host.get("result") == "PASS_HOST_TIMING_CLEAN", "host did not pass")
    require(host.get("dry_run") is False, "dry-run host receipt is inadmissible")
    require(host.get("producer") == pin, "host producer drifted")
    require(host.get("foreign_processes_mutated") is False, "host mutated foreign processes")
    require(host.get("command_lines_recorded") is False, "host recorded command lines")
    require(float(host.get("maximum_cpu_percent")) <= 5.0, "host CPU bound is too loose")
    require(int(host.get("requested_sample_seconds")) >= 60, "host sample is too short")
    for key in ("process_snapshot_before", "process_snapshot_after"):
        snapshot = host.get(key)
        require(isinstance(snapshot, dict), f"host {key} is missing")
        require(snapshot.get("foreign") == [], f"host {key} contains foreign work")
        require(snapshot.get("crazyhouse") == [], f"host {key} contains Crazyhouse work")
    summary = host.get("cpu_summary")
    require(isinstance(summary, dict), "host CPU summary is missing")
    require(int(summary.get("count")) >= 60, "host CPU summary is too short")
    require(summary.get("every_sample_strictly_below_limit") is True, "host CPU bound failed")
    require(float(summary.get("maximum")) < float(host["maximum_cpu_percent"]), "host CPU maximum failed")
    require(host["host"].get("priority_or_affinity_changed") is False, "host settings were changed")
    captured = parse_utc(host.get("captured_utc"), "host captured_utc")
    valid_until = parse_utc(host.get("valid_until_utc"), "host valid_until_utc")
    require(captured <= effective_time <= valid_until, "host receipt was stale at run start")
    return {
        "producer": producer,
        "captured_utc": host["captured_utc"],
        "valid_until_utc": host["valid_until_utc"],
        "effective_time_utc": effective_time.isoformat().replace("+00:00", "Z"),
    }


def transcript_records(path: Path, pin: dict[str, object], result_dir: Path) -> list[dict[str, object]]:
    resolved = path.resolve(strict=True)
    require(resolved.is_relative_to(result_dir.resolve()), "transcript escaped result namespace")
    authenticate(resolved, pin, "block transcript")
    rows = [json.loads(line) for line in resolved.read_text(encoding="utf-8").splitlines()]
    require(bool(rows), "empty transcript")
    for index, row in enumerate(rows):
        require(row.get("sequence") == index, "transcript sequence drifted")
        require(row.get("direction") in {"in", "out", "err"}, "transcript direction drifted")
        require(isinstance(row.get("line"), str), "transcript line is not text")
    require(not [row for row in rows if row["direction"] == "err"], "transcript contains stderr")
    return rows


def extract_searches(records: list[dict[str, object]]) -> list[dict[str, object]]:
    current_fen: str | None = None
    current: dict[str, object] | None = None
    searches: list[dict[str, object]] = []
    for record in records:
        direction = str(record["direction"])
        line = str(record["line"])
        if direction == "in" and line.startswith("position fen "):
            require(current is None, "position changed during a search")
            current_fen = line[len("position fen ") :]
        elif direction == "in" and line.startswith("go nodes "):
            require(current is None and current_fen is not None, "go without a fresh position")
            current = {
                "nodes": int(line[len("go nodes ") :]),
                "fen": current_fen,
                "output": [],
            }
        elif direction == "out" and current is not None:
            current["output"].append(line)
            if line.startswith("bestmove "):
                searches.append(current)
                current = None
                current_fen = None
    require(current is None, "unterminated search in transcript")
    return searches


def validate_block(
    block: dict[str, object],
    *,
    index: int,
    role: str,
    contract: dict[str, object],
    sample: list[dict[str, object]],
    result_dir: Path,
) -> dict[str, object]:
    overhead = contract["adapter_overhead"]
    warmup = contract["warmup"]
    require(block.get("phase") == "adapter-overhead", "block phase drifted")
    require(block.get("block_index") == index, "block index drifted")
    require(block.get("role") == role, "block role drifted")
    require(block.get("nodes") == overhead["nodes"], "block nodes drifted")
    require(block.get("warmup_nodes") == warmup["nodes"], "warm-up nodes drifted")
    lifecycle = block.get("lifecycle")
    require(isinstance(lifecycle, dict), "block lifecycle is missing")
    require(lifecycle.get("pass") is True, "block lifecycle did not pass")
    require(lifecycle.get("exit_code") == 0, "block engine exit drifted")
    require(lifecycle.get("forced") is False, "block engine was forced")
    require(lifecycle.get("stderr_lines") == [], "block stderr is not empty")
    transcript_pin = {
        "bytes": lifecycle["transcript_bytes"],
        "sha256": lifecycle["transcript_sha256"],
    }
    records = transcript_records(Path(lifecycle["transcript"]), transcript_pin, result_dir)
    searches = extract_searches(records)
    warmup_searches = [item for item in searches if item["nodes"] == warmup["nodes"]]
    measured = [item for item in searches if item["nodes"] == overhead["nodes"]]
    require(len(warmup_searches) == int(warmup["rows"]), "warm-up search count drifted")
    require(len(measured) == len(sample), "measured search count drifted")
    require(not [item for item in searches if item["nodes"] not in {warmup["nodes"], overhead["nodes"]}], "unexpected node limit")
    stored = block.get("searches")
    require(isinstance(stored, list) and len(stored) == len(sample), "stored search count drifted")
    verified = []
    for position, (row, segment, item) in enumerate(zip(sample, measured, stored)):
        require(segment["fen"] == row["canonical_fen"], f"search {position} FEN drifted")
        require(item.get("accepted_index") == row["accepted_index"], f"search {position} index drifted")
        require(item.get("id") == row["id"], f"search {position} id drifted")
        require(item.get("target_depth") == row["target_depth"], f"search {position} depth drifted")
        require(int(item.get("elapsed_ns")) > 0, f"search {position} elapsed time invalid")
        require(float(item.get("elapsed_ms")) == int(item["elapsed_ns"]) / 1_000_000.0, f"search {position} elapsed units drifted")
        output = list(segment["output"])
        bestmoves = [line for line in output if line.startswith("bestmove ")]
        require(len(bestmoves) == 1 and item.get("bestmove") == bestmoves[0], f"search {position} bestmove drifted")
        reported = [int(match.group(1)) for line in output for match in [NODES.search(line)] if match]
        require(reported and max(reported) == item.get("reported_nodes") and max(reported) >= overhead["nodes"], f"search {position} nodes drifted")
        normalized_sha = hashlib.sha256(("\n".join(normalize(output)) + "\n").encode("utf-8")).hexdigest()
        require(item.get("normalized_sha256") == normalized_sha, f"search {position} normalized transcript drifted")
        verified.append({"id": row["id"], "elapsed_ms": float(item["elapsed_ms"]), "normalized_sha256": normalized_sha})
    configured = block.get("configured")
    require(isinstance(configured, dict), "block configuration is missing")
    ready = configured.get("ready_lines")
    require(isinstance(ready, list), "ready transcript is missing")
    if role == "adapter":
        require(len([line for line in ready if str(line).startswith(ROUTE_PREFIX)]) == 1, "adapter route count drifted")
        require(len([line for line in ready if str(line).startswith(ACK_PREFIX)]) == 1, "adapter ack count drifted")
    else:
        require(not [line for line in ready if str(line).startswith(ROUTE_PREFIX)], "raw Fairy emitted product route")
    return {"index": index, "role": role, "searches": verified, "transcript": identity(Path(lifecycle["transcript"]))}


def recompute(blocks: list[dict[str, object]], contract: dict[str, object]) -> dict[str, object]:
    require(len(blocks) % 2 == 0, "block count is not pair-aligned")
    raw_ms: list[float] = []
    adapter_ms: list[float] = []
    mismatches = []
    for pair_index in range(0, len(blocks), 2):
        pair = blocks[pair_index : pair_index + 2]
        require({item["role"] for item in pair} == {"raw_fairy", "adapter"}, "paired roles drifted")
        by_role = {item["role"]: item for item in pair}
        raw = by_role["raw_fairy"]["searches"]
        adapted = by_role["adapter"]["searches"]
        require(len(raw) == len(adapted), "paired search count drifted")
        for raw_item, adapted_item in zip(raw, adapted):
            require(raw_item["id"] == adapted_item["id"], "paired root order drifted")
            raw_ms.append(float(raw_item["elapsed_ms"]))
            adapter_ms.append(float(adapted_item["elapsed_ms"]))
            if raw_item["normalized_sha256"] != adapted_item["normalized_sha256"]:
                mismatches.append({"pair": pair_index // 2, "id": raw_item["id"], "raw": raw_item["normalized_sha256"], "adapter": adapted_item["normalized_sha256"]})
    deltas = [a - r for a, r in zip(adapter_ms, raw_ms)]
    limits = contract["adapter_overhead"]["pass_limits"]
    metrics = {
        "paired_searches": len(deltas),
        "mean_delta_ms": statistics.fmean(deltas),
        "median_delta_ms": statistics.median(deltas),
        "p95_delta_ms_nearest_rank": percentile(deltas, 0.95),
        "mean_delta_ms_ucb99": mean_ucb99(deltas),
        "geometric_mean_ratio_ucb99": ratio_ucb99(adapter_ms, raw_ms),
        "raw_median_ms": statistics.median(raw_ms),
        "adapter_median_ms": statistics.median(adapter_ms),
        "transcript_mismatches": mismatches,
    }
    checks = {
        "exact_pair_count": len(deltas) == int(contract["adapter_overhead"]["expected_paired_searches"]),
        "transcript_identity": not mismatches,
        "median_delta": metrics["median_delta_ms"] <= float(limits["median_delta_ms_max"]),
        "p95_delta": metrics["p95_delta_ms_nearest_rank"] <= float(limits["p95_delta_ms_max"]),
        "mean_ucb99": metrics["mean_delta_ms_ucb99"] <= float(limits["mean_delta_ms_ucb99_max"]),
        "ratio_ucb99": metrics["geometric_mean_ratio_ucb99"] <= float(limits["geometric_mean_ratio_ucb99_max"]),
    }
    return {"metrics": metrics, "checks": checks, "pass": all(checks.values())}


def verify(contract_path: Path, result_path: Path) -> dict[str, object]:
    contract_path = contract_path.resolve(strict=True)
    result_path = result_path.resolve(strict=True)
    repository = contract_path.parents[2]
    contract = load_json(contract_path)
    result = load_json(result_path)
    require(contract.get("schema") == "crazyhouse-local-adapter-overhead/v1", "contract schema drifted")
    require(result.get("schema") == "crazyhouse-local-adapter-overhead-result/v1", "result schema drifted")
    verifier_identity = authenticate(Path(__file__), contract["verification"]["implementation"], "independent verifier")
    implementation = authenticate(repository / contract["implementation"]["path"], contract["implementation"], "measurement implementation")
    python = authenticate(Path(sys.executable), contract["verification"]["python"], "verification Python")
    contract_identity = identity(contract_path)
    require(result.get("contract", {}).get("bytes") == contract_identity["bytes"], "result contract bytes drifted")
    require(result.get("contract", {}).get("sha256") == contract_identity["sha256"], "result contract hash drifted")
    require(result.get("implementation") == implementation, "result implementation identity drifted")
    require(result.get("python") == python, "result Python identity drifted")
    authenticated_inputs = {}
    for role, pin in contract["inputs"].items():
        authenticated_inputs[role] = authenticate(pinned_path(pin, repository), pin, role)
        require(result["inputs"][role] == authenticated_inputs[role], f"result {role} identity drifted")
    host_path = Path(result["host_attestation"]["path"])
    host_identity = authenticate(host_path, result["host_attestation"], "timing host attestation")
    host_validation = validate_host(load_json(host_path), contract, repository, parse_utc(result.get("started_utc"), "result started_utc"))
    require(result.get("host_attestation_validation") == host_validation, "host validation drifted")
    corpus_path = pinned_path(contract["inputs"]["corpus"], repository)
    rows = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines()]
    sample = select_sample(rows, contract)
    require(result.get("sample", {}).get("rows") == len(sample), "result sample count drifted")
    require(result.get("sample", {}).get("ids") == [row["id"] for row in sample], "result sample IDs drifted")
    raw_blocks = result.get("blocks", {}).get("adapter_overhead")
    expected_roles = contract["adapter_overhead"]["block_order"]
    require(isinstance(raw_blocks, list) and len(raw_blocks) == len(expected_roles), "result block count drifted")
    verified_blocks = [
        validate_block(block, index=index, role=str(expected_roles[index]), contract=contract, sample=sample, result_dir=result_path.parent)
        for index, block in enumerate(raw_blocks)
    ]
    recomputed = recompute(verified_blocks, contract)
    require(result.get("adapter_overhead") == recomputed, "reported overhead analysis drifted")
    require(recomputed["pass"] is True, "recomputed overhead gate failed")
    require(result.get("result") == "PASS_ADAPTER_OVERHEAD", "formal result did not pass")
    require(result.get("error") is None, "formal result contains an error")
    require(result.get("time_controls_derived") is False, "formal result derived a time control")
    require(result.get("scientific_boundary", {}).get("game_results_consumed") is False, "formal result consumed game data")
    return {
        "schema": "crazyhouse-local-adapter-overhead-independent-verification/v1",
        "created_utc": utc_now(),
        "result": "PASS_ADAPTER_OVERHEAD_INDEPENDENTLY_VERIFIED",
        "contract": contract_identity,
        "formal_result": identity(result_path),
        "verifier": verifier_identity,
        "measurement_implementation": implementation,
        "python": python,
        "inputs": authenticated_inputs,
        "host_attestation": host_identity,
        "host_validation": host_validation,
        "sample_rows": len(sample),
        "blocks": len(verified_blocks),
        "measured_searches": sum(len(block["searches"]) for block in verified_blocks),
        "recomputed": recomputed,
        "scientific_boundary": {
            "fixed_node_timing_only": True,
            "game_results_consumed": False,
            "strength_claim": False,
            "openbench_claim": False,
            "release_claim": False,
        },
    }


def main(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    try:
        verified = verify(args.contract, args.result)
        write_fresh(output, verified)
        print(verified["result"])
        return 0
    except BaseException as exc:
        write_fresh(
            output,
            {
                "schema": "crazyhouse-local-adapter-overhead-independent-verification-invalid/v1",
                "created_utc": utc_now(),
                "result": "INVALID_ADAPTER_OVERHEAD_VERIFICATION",
                "error": f"{type(exc).__name__}: {exc}",
                "strength_claim": False,
                "openbench_claim": False,
            },
        )
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(main(parser.parse_args()))
