#!/usr/bin/env python3
"""Measure the qualified comparator adapter's fixed-work relay overhead.

This program is deliberately result-blind.  It performs only fixed-node UCI
searches on a frozen, rules-generated Crazyhouse sample.  No games are played,
and no score, Elo, or match result is consumed.  Time controls are owner-fixed
elsewhere and are never derived from this measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Iterable


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PROFILE_TOKEN = f"{PROFILE_ID}@{PROFILE_SHA256}"
ROUTE_PREFIX = "info string route_commit status=ok ruleset=crazyhouse "
ACK_PREFIX = "info string crazyhouse_capability_ack status=ok "
INFO_VOLATILE_RE = re.compile(r"\s+(?:time|nps|hashfull)\s+\d+")
INFO_NODES_RE = re.compile(r"(?:^|\s)nodes\s+(\d+)(?:\s|$)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_json_fresh(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_utc(value: object, label: str) -> datetime:
    require(isinstance(value, str) and bool(value), f"{label} is not an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{label} is not an ISO timestamp") from exc
    require(parsed.tzinfo is not None, f"{label} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_host_attestation(
    host: dict[str, object],
    contract: dict[str, object],
    repository_root: Path,
    *,
    effective_time: datetime,
) -> dict[str, object]:
    precondition = contract["host_precondition"]  # type: ignore[index]
    producer_pin = precondition["attestation_producer"]
    producer_path = repository_root / producer_pin["path"]
    producer = authenticate_file(producer_path, producer_pin, "host attestation producer")
    require(host.get("schema") == "crazyhouse-host-timing-attestation/v1", "host attestation schema mismatch")
    require(host.get("result") == "PASS_HOST_TIMING_CLEAN", "host is not timing-clean")
    require(host.get("dry_run") is False, "dry-run host attestation is inadmissible")
    require(host.get("foreign_processes_mutated") is False, "host attestation mutated foreign processes")
    require(host.get("command_lines_recorded") is False, "host attestation recorded command lines")
    require(host.get("producer") == producer_pin, "host attestation producer identity drifted")
    require(host.get("maximum_cpu_percent") <= 5.0, "host CPU limit is too permissive")
    require(host.get("requested_sample_seconds") >= 60, "host CPU sample is too short")
    for label in ("process_snapshot_before", "process_snapshot_after"):
        snapshot = host.get(label)
        require(isinstance(snapshot, dict), f"host {label} is missing")
        require(snapshot.get("foreign") == [], f"host {label} contains foreign workloads")
        require(snapshot.get("crazyhouse") == [], f"host {label} contains Crazyhouse workloads")
    summary = host.get("cpu_summary")
    require(isinstance(summary, dict), "host CPU summary is missing")
    require(summary.get("count") >= 60, "host CPU summary is too short")
    require(summary.get("every_sample_strictly_below_limit") is True, "host CPU sample exceeded its limit")
    require(summary.get("maximum") < host["maximum_cpu_percent"], "host CPU maximum is not below its limit")
    host_shape = host.get("host")
    require(isinstance(host_shape, dict), "host shape is missing")
    require(host_shape.get("priority_or_affinity_changed") is False, "host attestation changed priority or affinity")
    captured = parse_utc(host.get("captured_utc"), "host captured_utc")
    valid_until = parse_utc(host.get("valid_until_utc"), "host valid_until_utc")
    require(captured <= effective_time <= valid_until, "host attestation is not valid at run start")
    return {
        "producer": producer,
        "captured_utc": host["captured_utc"],
        "valid_until_utc": host["valid_until_utc"],
        "effective_time_utc": effective_time.isoformat().replace("+00:00", "Z"),
    }


def percentile_nearest_rank(values: list[float], probability: float) -> float:
    require(bool(values), "cannot compute a percentile of an empty sample")
    require(0.0 < probability <= 1.0, "percentile probability is out of range")
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def one_sided_mean_ucb99(values: list[float]) -> float:
    require(bool(values), "cannot compute a confidence bound of an empty sample")
    mean = statistics.fmean(values)
    if len(values) == 1:
        return mean
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return mean + NormalDist().inv_cdf(0.99) * standard_error


def log_ratio_ucb99(adapted_ms: list[float], raw_ms: list[float]) -> float:
    require(len(adapted_ms) == len(raw_ms) > 0, "ratio samples must be paired")
    logs = [math.log(adapted / raw) for adapted, raw in zip(adapted_ms, raw_ms)]
    return math.exp(one_sided_mean_ucb99(logs))


def normalize_search(lines: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for line in lines:
        if line.startswith(ROUTE_PREFIX) or line.startswith(ACK_PREFIX):
            continue
        value = INFO_VOLATILE_RE.sub("", line)
        normalized.append(re.sub(r"[ \t]+", " ", value).rstrip())
    return normalized


@dataclass(frozen=True)
class SampleRow:
    accepted_index: int
    identifier: str
    target_depth: int
    fen: str


class UciSession:
    def __init__(self, command: list[str], cwd: Path, transcript: Path, timeout: float):
        self.command = command
        self.cwd = cwd
        self.transcript = transcript
        self.timeout = timeout
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.records: list[dict[str, object]] = []
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._sequence = 0
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
            raise RuntimeError(f"process exited before command {line!r}")
        assert self.process.stdin is not None
        self._record("in", line)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def wait_stdout(self, predicate: Callable[[str], bool]) -> str:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None and self._queue.empty():
                raise RuntimeError(
                    f"process exited {self.process.returncode} before expected output"
                )
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = self._queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if predicate(line):
                return line
        raise TimeoutError("expected UCI output was not observed")

    def initialize(self) -> list[str]:
        start = self.output_length()
        self.send("uci")
        self.wait_stdout(lambda line: line == "uciok")
        return self.output_from(start)

    def finish(self) -> dict[str, object]:
        forced = False
        if self.process.poll() is None:
            try:
                self.send("quit")
            except (BrokenPipeError, RuntimeError):
                pass
        try:
            self.process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            forced = True
            self.process.kill()
            self.process.wait(timeout=5.0)
        for thread in self._threads:
            thread.join(timeout=2.0)
        if self.transcript.exists():
            raise FileExistsError(f"transcript already exists: {self.transcript}")
        with self.transcript.open("x", encoding="utf-8", newline="\n") as handle:
            for record in self.records:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        return {
            "pid": self.process.pid,
            "exit_code": self.process.returncode,
            "forced": forced,
            "stderr_lines": list(self.stderr_lines),
            "transcript": str(self.transcript),
            "transcript_bytes": self.transcript.stat().st_size,
            "transcript_sha256": sha256_file(self.transcript),
            "pass": self.process.returncode == 0 and not forced and not self.stderr_lines,
        }


def select_sample(rows: list[dict[str, object]], contract: dict[str, object]) -> list[SampleRow]:
    selection = contract["sample"]  # type: ignore[index]
    seed = str(selection["seed_utf8"]).encode("utf-8")  # type: ignore[index]
    per_depth = int(selection["rows_per_depth"])  # type: ignore[index]
    selected: list[dict[str, object]] = []
    for depth in range(int(selection["depth_min"]), int(selection["depth_max"]) + 1):  # type: ignore[index]
        group = [row for row in rows if int(row["target_depth"]) == depth]
        group.sort(
            key=lambda row: hashlib.sha256(
                seed + b"\0" + str(row["id"]).encode("ascii")
            ).digest()
        )
        require(len(group) >= per_depth, f"depth {depth} has too few rows")
        selected.extend(group[:per_depth])
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
    observed_digest = hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
    require(
        observed_digest == selection["selection_sha256"],  # type: ignore[index]
        "timing sample selection digest drifted",
    )
    require(
        [int(row["accepted_index"]) for row in selected] == selection["accepted_indices"],  # type: ignore[index]
        "timing sample accepted-index list drifted",
    )
    return [
        SampleRow(
            accepted_index=int(row["accepted_index"]),
            identifier=str(row["id"]),
            target_depth=int(row["target_depth"]),
            fen=str(row["canonical_fen"]),
        )
        for row in selected
    ]


def authenticate_file(path: Path, pin: dict[str, object], label: str) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    observed = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    require(observed["bytes"] == pin["bytes"], f"{label} byte count drifted")
    require(observed["sha256"] == pin["sha256"], f"{label} SHA-256 drifted")
    return observed


def configure_session(
    session: UciSession,
    role: str,
    network: Path,
    contract: dict[str, object],
    nonce: str,
) -> dict[str, object]:
    inventory = session.initialize()
    session.send("setoption name UCI_Variant value crazyhouse")
    if role == "raw_fairy":
        session.send("setoption name Use NNUE value true")
        session.send(f"setoption name EvalFile value {network}")
    else:
        session.send(f"setoption name CrazyhouseProfile value {PROFILE_TOKEN}")
        session.send(f"setoption name CrazyhouseEvalFile value {network}")
        session.send(f"setoption name CrazyhouseCapabilityNonce value {nonce}")
    settings = contract["uci_settings"]  # type: ignore[index]
    session.send(f"setoption name Threads value {settings['threads']}")  # type: ignore[index]
    session.send(f"setoption name Hash value {settings['hash_mib']}")  # type: ignore[index]
    session.send("setoption name MultiPV value 1")
    session.send(f"setoption name Move Overhead value {settings['move_overhead_ms']}")  # type: ignore[index]
    session.send("setoption name Ponder value false")
    start = session.output_length()
    session.send("isready")
    session.wait_stdout(lambda line: line == "readyok")
    ready_lines = session.output_from(start)
    if role == "raw_fairy":
        require(not any(line.startswith(ROUTE_PREFIX) for line in ready_lines), "raw Fairy emitted product route")
    else:
        expected_backend = "legacy-v1" if role == "candidate" else "fairy-external"
        expected_evaluator = "incremental-scalar" if role == "candidate" else "halfkav2variants"
        routes = [line for line in ready_lines if line.startswith(ROUTE_PREFIX)]
        require(len(routes) == 1, f"{role} readiness did not emit exactly one route")
        require(f"backend={expected_backend}" in routes[0], f"{role} backend drifted")
        require(f"evaluator={expected_evaluator}" in routes[0], f"{role} evaluator drifted")
        require(f"identity={contract['network']['sha256']}" in routes[0], f"{role} network identity drifted")  # type: ignore[index]
        acknowledgements = [line for line in ready_lines if line.startswith(ACK_PREFIX)]
        require(len(acknowledgements) == 1 and f"nonce={nonce}" in acknowledgements[0], f"{role} nonce acknowledgement drifted")
    require(not any(line.startswith("info string ERROR") for line in ready_lines), f"{role} readiness error")
    return {"inventory": inventory, "ready_lines": ready_lines}


def run_search(
    session: UciSession,
    role: str,
    row: SampleRow,
    nodes: int,
    network: Path,
) -> dict[str, object]:
    session.send("ucinewgame")
    session.send("setoption name Clear Hash")
    session.send("isready")
    session.wait_stdout(lambda line: line == "readyok")
    session.send(f"position fen {row.fen}")
    start_index = session.output_length()
    start_ns = time.perf_counter_ns()
    session.send(f"go nodes {nodes}")
    session.wait_stdout(lambda line: line.startswith("bestmove "))
    elapsed_ns = time.perf_counter_ns() - start_ns
    lines = session.output_from(start_index)
    bestmoves = [line for line in lines if line.startswith("bestmove ")]
    require(len(bestmoves) == 1, f"{role}/{row.identifier}: bestmove count drifted")
    require(not any(line.startswith("info string ERROR") for line in lines), f"{role}/{row.identifier}: engine error")
    require("info string classical evaluation enabled" not in lines, f"{role}/{row.identifier}: classical fallback")
    marker = f"info string NNUE evaluation using {network} enabled"
    if role in {"raw_fairy", "adapter"}:
        require(lines.count(marker) == 1, f"{role}/{row.identifier}: NNUE marker count drifted")
    reported_nodes = [
        int(match.group(1))
        for line in lines
        for match in [INFO_NODES_RE.search(line)]
        if match
    ]
    require(reported_nodes and max(reported_nodes) >= nodes, f"{role}/{row.identifier}: node limit not reached")
    normalized = normalize_search(lines)
    return {
        "accepted_index": row.accepted_index,
        "id": row.identifier,
        "target_depth": row.target_depth,
        "elapsed_ns": elapsed_ns,
        "elapsed_ms": elapsed_ns / 1_000_000.0,
        "reported_nodes": max(reported_nodes),
        "bestmove": bestmoves[0],
        "normalized_sha256": hashlib.sha256(("\n".join(normalized) + "\n").encode("utf-8")).hexdigest(),
    }


def command_for_role(role: str, paths: dict[str, Path]) -> tuple[list[str], Path]:
    if role == "raw_fairy":
        return [str(paths["raw_fairy"])], paths["raw_fairy"].parent
    if role == "adapter":
        return [
            str(paths["adapter"]),
            "--engine",
            str(paths["raw_fairy"]),
            "--network",
            str(paths["network"]),
        ], paths["adapter"].parent
    if role == "candidate":
        return [str(paths["candidate"])], paths["candidate"].parent
    raise RuntimeError(f"unknown timing role: {role}")


def run_block(
    *,
    phase: str,
    block_index: int,
    role: str,
    nodes: int,
    warmup_nodes: int,
    rows: list[SampleRow],
    warmup_rows: list[SampleRow],
    paths: dict[str, Path],
    contract: dict[str, object],
    output_dir: Path,
    timeout: float,
) -> dict[str, object]:
    command, cwd = command_for_role(role, paths)
    transcript = output_dir / "transcripts" / f"{phase}-{block_index:02d}-{role}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    nonce = hashlib.sha256(f"{phase}:{block_index}:{role}".encode("ascii")).hexdigest()[:32]
    session = UciSession(command, cwd, transcript, timeout)
    lifecycle: dict[str, object] | None = None
    try:
        configured = configure_session(session, role, paths["network"], contract, nonce)
        for row in warmup_rows:
            run_search(session, role, row, warmup_nodes, paths["network"])
        searches = [
            run_search(session, role, row, nodes, paths["network"]) for row in rows
        ]
    finally:
        lifecycle = session.finish()
    require(bool(lifecycle["pass"]), f"{phase}/{block_index}/{role}: lifecycle failed")
    return {
        "phase": phase,
        "block_index": block_index,
        "role": role,
        "nodes": nodes,
        "warmup_nodes": warmup_nodes,
        "configured": configured,
        "searches": searches,
        "lifecycle": lifecycle,
    }


def pair_blocks(blocks: list[dict[str, object]], left: str, right: str) -> list[tuple[dict[str, object], dict[str, object]]]:
    require(len(blocks) % 2 == 0, "paired block count must be even")
    pairs: list[tuple[dict[str, object], dict[str, object]]] = []
    for index in range(0, len(blocks), 2):
        first, second = blocks[index], blocks[index + 1]
        roles = {str(first["role"]), str(second["role"])}
        require(roles == {left, right}, f"block pair {index // 2} role drift")
        by_role = {str(first["role"]): first, str(second["role"]): second}
        pairs.append((by_role[left], by_role[right]))
    return pairs


def analyze_overhead(blocks: list[dict[str, object]], contract: dict[str, object]) -> dict[str, object]:
    paired = pair_blocks(blocks, "raw_fairy", "adapter")
    raw_ms: list[float] = []
    adapted_ms: list[float] = []
    transcript_mismatches: list[dict[str, object]] = []
    for pair_index, (raw_block, adapter_block) in enumerate(paired):
        raw_searches = raw_block["searches"]  # type: ignore[index]
        adapter_searches = adapter_block["searches"]  # type: ignore[index]
        require(len(raw_searches) == len(adapter_searches), "paired search count drift")
        for raw, adapted in zip(raw_searches, adapter_searches):
            require(raw["id"] == adapted["id"], "paired root order drift")
            raw_ms.append(float(raw["elapsed_ms"]))
            adapted_ms.append(float(adapted["elapsed_ms"]))
            if raw["normalized_sha256"] != adapted["normalized_sha256"]:
                transcript_mismatches.append(
                    {
                        "pair": pair_index,
                        "id": raw["id"],
                        "raw": raw["normalized_sha256"],
                        "adapter": adapted["normalized_sha256"],
                    }
                )
    deltas = [adapted - raw for adapted, raw in zip(adapted_ms, raw_ms)]
    limits = contract["adapter_overhead"]["pass_limits"]  # type: ignore[index]
    metrics = {
        "paired_searches": len(deltas),
        "mean_delta_ms": statistics.fmean(deltas),
        "median_delta_ms": statistics.median(deltas),
        "p95_delta_ms_nearest_rank": percentile_nearest_rank(deltas, 0.95),
        "mean_delta_ms_ucb99": one_sided_mean_ucb99(deltas),
        "geometric_mean_ratio_ucb99": log_ratio_ucb99(adapted_ms, raw_ms),
        "raw_median_ms": statistics.median(raw_ms),
        "adapter_median_ms": statistics.median(adapted_ms),
        "transcript_mismatches": transcript_mismatches,
    }
    checks = {
        "exact_pair_count": len(deltas) == int(contract["adapter_overhead"]["expected_paired_searches"]),  # type: ignore[index]
        "transcript_identity": not transcript_mismatches,
        "median_delta": metrics["median_delta_ms"] <= float(limits["median_delta_ms_max"]),
        "p95_delta": metrics["p95_delta_ms_nearest_rank"] <= float(limits["p95_delta_ms_max"]),
        "mean_ucb99": metrics["mean_delta_ms_ucb99"] <= float(limits["mean_delta_ms_ucb99_max"]),
        "ratio_ucb99": metrics["geometric_mean_ratio_ucb99"] <= float(limits["geometric_mean_ratio_ucb99_max"]),
    }
    return {"metrics": metrics, "checks": checks, "pass": all(checks.values())}


def load_contract(path: Path) -> dict[str, object]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    require(
        contract.get("schema") == "crazyhouse-local-adapter-overhead/v1",
        "contract schema mismatch",
    )
    return contract


def main(args: argparse.Namespace) -> int:
    if os.name != "nt" and not args.allow_non_windows_test:
        raise RuntimeError("the frozen timing lane is Windows-only")
    contract_path = args.contract.resolve(strict=True)
    contract = load_contract(contract_path)
    implementation = authenticate_file(
        Path(__file__), contract["implementation"], "adapter overhead implementation"
    )
    python_runtime = authenticate_file(
        Path(sys.executable), contract["runtime"]["python"], "adapter overhead Python"
    )
    require(
        sys.version_info[:3] == (3, 12, 0),
        "adapter overhead Python version drifted",
    )
    paths = {
        "candidate": args.candidate.resolve(strict=True),
        "adapter": args.adapter.resolve(strict=True),
        "raw_fairy": args.raw_fairy.resolve(strict=True),
        "network": args.network.resolve(strict=True),
        "corpus": args.corpus.resolve(strict=True),
    }
    authenticated = {
        role: authenticate_file(path, contract["inputs"][role], role)  # type: ignore[index]
        for role, path in paths.items()
    }
    rows = [json.loads(line) for line in paths["corpus"].read_text(encoding="utf-8").splitlines()]
    sample = select_sample(rows, contract)
    dry_plan = {
        "schema": "crazyhouse-local-adapter-overhead-dry-run/v1",
        "contract": {
            "path": str(contract_path),
            "bytes": contract_path.stat().st_size,
            "sha256": sha256_file(contract_path),
        },
        "implementation": implementation,
        "python": python_runtime,
        "authenticated": authenticated,
        "sample_rows": len(sample),
        "sample_ids": [row.identifier for row in sample],
        "overhead_block_order": contract["adapter_overhead"]["block_order"],  # type: ignore[index]
        "time_controls_derived": False,
        "game_results_consumed": False,
    }
    if args.dry_run:
        print(json.dumps(dry_plan, indent=2, sort_keys=True))
        return 0

    require(args.host_attestation is not None, "formal timing requires a host attestation")
    host_attestation_path = args.host_attestation.resolve(strict=True)
    host_attestation = json.loads(host_attestation_path.read_text(encoding="utf-8"))
    started = datetime.now(timezone.utc)
    repository_root = contract_path.parents[2]
    host_validation = validate_host_attestation(
        host_attestation,
        contract,
        repository_root,
        effective_time=started,
    )
    output_dir = args.output_dir.resolve()
    require(not output_dir.exists(), f"output directory must be fresh: {output_dir}")
    output_dir.mkdir(parents=True)
    started_utc = started.isoformat().replace("+00:00", "Z")
    warmup_count = int(contract["warmup"]["rows"])  # type: ignore[index]
    warmup_rows = sample[:warmup_count]
    timeout = float(contract["runtime"]["search_timeout_seconds"])  # type: ignore[index]
    blocks: dict[str, list[dict[str, object]]] = {"adapter_overhead": []}
    try:
        overhead = contract["adapter_overhead"]  # type: ignore[index]
        for index, role in enumerate(overhead["block_order"]):
            blocks["adapter_overhead"].append(
                run_block(
                    phase="adapter-overhead",
                    block_index=index,
                    role=str(role),
                    nodes=int(overhead["nodes"]),
                    warmup_nodes=int(contract["warmup"]["nodes"]),  # type: ignore[index]
                    rows=sample,
                    warmup_rows=warmup_rows,
                    paths=paths,
                    contract=contract,
                    output_dir=output_dir,
                    timeout=timeout,
                )
            )
        overhead_result = analyze_overhead(blocks["adapter_overhead"], contract)
        require(overhead_result["pass"], "adapter overhead gate failed")
        status = "PASS_ADAPTER_OVERHEAD"
        exit_code = 0
        error = None
    except BaseException as exc:
        overhead_result = (
            analyze_overhead(blocks["adapter_overhead"], contract)
            if len(blocks["adapter_overhead"]) == len(contract["adapter_overhead"]["block_order"])  # type: ignore[index]
            else None
        )
        status = "REJECTED_ADAPTER_OVERHEAD"
        exit_code = 1
        error = f"{type(exc).__name__}: {exc}"
    result = {
        "schema": "crazyhouse-local-adapter-overhead-result/v1",
        "created_utc": utc_now(),
        "started_utc": started_utc,
        "evidence_class": "E1_ENGINEERING_TIMING",
        "contract": dry_plan["contract"],
        "implementation": implementation,
        "python": python_runtime,
        "inputs": authenticated,
        "host_attestation": {
            "path": str(host_attestation_path),
            "bytes": host_attestation_path.stat().st_size,
            "sha256": sha256_file(host_attestation_path),
        },
        "host_attestation_validation": host_validation,
        "sample": {
            "rows": len(sample),
            "ids": [row.identifier for row in sample],
            "selection_sha256": contract["sample"]["selection_sha256"],  # type: ignore[index]
        },
        "adapter_overhead": overhead_result,
        "time_controls_derived": False,
        "blocks": blocks,
        "result": status,
        "error": error,
        "scientific_boundary": {
            "fixed_node_timing_only": True,
            "game_results_consumed": False,
            "elo_claim": False,
            "strength_claim": False,
            "openbench_claim": False,
            "release_claim": False,
        },
    }
    write_json_fresh(output_dir / "result.json", result)
    print(status)
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--raw-fairy", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--host-attestation", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-non-windows-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
