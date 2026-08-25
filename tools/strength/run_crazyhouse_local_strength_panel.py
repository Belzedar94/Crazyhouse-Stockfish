#!/usr/bin/env python3
"""Run the preregistered three-rung Crazyhouse local LOS gate.

This is a same-network comparison between the Stockfish-dev-derived product
candidate and a qualified Fairy-Stockfish comparator adapter. Each rung is
evaluated only after complete colour-swapped batches. The first eligible
decision is after 50 games; subsequent batches continue until the historical
one-decimal WLD LOS display reaches 0.0% or 100.0%, or the frozen safety cap is
reached. The runner never interprets the two-game plumbing canary as strength.
"""

from __future__ import annotations

import argparse
import ctypes
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Iterable

if os.name == "nt":
    from ctypes import wintypes


CANDIDATE_NAME = "candidate"
COMPARATOR_NAME = "fairy-adapted"
PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PROFILE_TOKEN = f"{PROFILE_ID}@{PROFILE_SHA256}"
FINISHED_RE = re.compile(
    r"^Finished game\s+(\d+)\s+\((.+) vs (.+)\):\s+"
    r"(1-0|0-1|1/2-1/2)\s+\{(.*)\}\s*$"
)
NONCE_SET_RE = re.compile(
    r"^(?:\d+\s+)?>(?:candidate|fairy-adapted)\(\d+\): setoption name "
    r"CrazyhouseCapabilityNonce value ([0-9a-f]{32})\r?$",
    re.MULTILINE,
)
NONCE_ACK_RE = re.compile(
    r"^(?:\d+\s+)?<(?:candidate|fairy-adapted)\(\d+\): info string "
    r"crazyhouse_capability_ack status=ok .* nonce=([0-9a-f]{32})\r?$",
    re.MULTILINE,
)
BAD_REASON_TERMS = (
    "abandon",
    "adjudication",
    "crash",
    "disconnect",
    "failed",
    "illegal",
    "maximal game length",
    "on time",
    "stall",
    "terminated",
    "timed out",
)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


if os.name == "nt":
    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]


    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]


    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class ProcessJob:
    """Own one Windows process tree and kill only that tree if the handle closes."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.handle: int | None = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        handle = kernel32.CreateJobObjectW(None, None)
        require(bool(handle), f"CreateJobObjectW failed: {ctypes.get_last_error()}")
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise RuntimeError(f"SetInformationJobObject failed: {error}")
        if not kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise RuntimeError(f"AssignProcessToJobObject failed: {error}")
        self.handle = int(handle)

    def close(self) -> None:
        if self.handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.CloseHandle(wintypes.HANDLE(self.handle)):
            raise RuntimeError(f"CloseHandle(job) failed: {ctypes.get_last_error()}")
        self.handle = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def write_json_fresh(path: Path, payload: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def logistic_elo(score: float) -> float:
    score = min(max(score, 1e-3), 1.0 - 1e-3)
    return -400.0 * math.log10(1.0 / score - 1.0)


def openbench_statistics(results: list[int]) -> dict[str, object]:
    """Reproduce OpenBench's normal-approximation statistics over penta bins."""

    count = sum(results)
    if not count:
        return {
            "lower_elo": 0.0,
            "elo": 0.0,
            "upper_elo": 0.0,
            "ci95_minus": 0.0,
            "ci95_plus": 0.0,
            "los": 0.5,
            "los_percent": 50.0,
        }
    divisor = len(results) - 1
    mean = sum((index / divisor) * value for index, value in enumerate(results)) / count
    variance = sum(
        ((index / divisor) - mean) ** 2 * value
        for index, value in enumerate(results)
    ) / count
    standard_error = math.sqrt(variance) / math.sqrt(count)
    if standard_error == 0.0:
        lower_score = upper_score = mean
        los = 0.5 if mean == 0.5 else float(mean > 0.5)
    else:
        lower_score = mean + NormalDist().inv_cdf(0.025) * standard_error
        upper_score = mean + NormalDist().inv_cdf(0.975) * standard_error
        los = NormalDist().cdf((mean - 0.5) / standard_error)
    lower = logistic_elo(lower_score)
    middle = logistic_elo(mean)
    upper = logistic_elo(upper_score)
    return {
        "lower_elo": lower,
        "elo": middle,
        "upper_elo": upper,
        "ci95_minus": middle - lower,
        "ci95_plus": upper - middle,
        "los": los,
        "los_percent": 100.0 * los,
    }


def historical_erf(value: float) -> float:
    """The approximation used by the frozen local variant match methodology."""

    coefficient = 8.0 * (math.pi - 3.0) / (3.0 * math.pi * (4.0 - math.pi))
    squared = value * value
    exponent = -squared * (4.0 / math.pi + coefficient * squared) / (
        1.0 + coefficient * squared
    )
    return math.copysign(math.sqrt(1.0 - math.exp(exponent)), value)


def historical_phi(quantile: float) -> float:
    return 0.5 * (1.0 + historical_erf(quantile / math.sqrt(2.0)))


def historical_wld_statistics(wld: list[int]) -> dict[str, object]:
    """Compute the frozen runner's WLD LOS and exact one-decimal display.

    A zero-variance score is deliberately unavailable, matching the historical
    runner's caught division-by-zero path rather than inventing an endpoint.
    """

    require(len(wld) == 3 and all(value >= 0 for value in wld), "invalid WLD")
    total = sum(wld)
    if total == 0:
        return {"available": False, "total": 0, "display_los_percent": None}
    win = float(wld[0]) / total
    loss = float(wld[1]) / total
    draw = float(wld[2]) / total
    mean = win + draw / 2.0
    stdev = math.sqrt(
        win * (1.0 - mean) ** 2
        + loss * mean**2
        + draw * (0.5 - mean) ** 2
    ) / math.sqrt(total)
    if not math.isfinite(stdev) or stdev <= 0.0:
        return {
            "available": False,
            "total": total,
            "mean_score": mean,
            "standard_error": stdev,
            "display_los_percent": None,
        }
    los = historical_phi((mean - 0.5) / stdev)
    elo = logistic_elo(mean)
    display = f"{100.0 * los:.1f}"
    return {
        "available": True,
        "total": total,
        "mean_score": mean,
        "standard_error": stdev,
        "elo": elo,
        "los": los,
        "los_percent": 100.0 * los,
        "display_los_percent": display,
        "display_line": f"ELO: {elo:.2f} LOS: {display}%",
    }


def candidate_score(white: str, black: str, result: str) -> float:
    if result == "1/2-1/2":
        return 0.5
    winner = white if result == "1-0" else black
    if winner == CANDIDATE_NAME:
        return 1.0
    if winner == COMPARATOR_NAME:
        return 0.0
    raise RuntimeError(f"unknown winner: {winner}")


@dataclass(frozen=True)
class GameRecord:
    local_game: int
    white: str
    black: str
    result: str
    reason: str
    candidate_points: float


@dataclass(frozen=True)
class AggregateGameRecord:
    global_game: int
    batch_index: int
    local_game: int
    opening_index: int
    white: str
    black: str
    result: str
    reason: str
    candidate_points: float


class BatchTracker:
    def __init__(self, expected_games: int) -> None:
        require(
            expected_games > 0 and expected_games % 2 == 0,
            "batch games must be positive and even",
        )
        self.expected_games = expected_games
        self.games: dict[int, GameRecord] = {}
        self.defects: list[dict[str, object]] = []

    def consume(self, line: str) -> bool:
        match = FINISHED_RE.match(line.strip())
        if not match:
            return False
        game_no = int(match.group(1))
        white, black, result, reason = match.group(2, 3, 4, 5)
        require(1 <= game_no <= self.expected_games, f"game number out of range: {game_no}")
        require(game_no not in self.games, f"duplicate game number: {game_no}")
        require(
            {white, black} == {CANDIDATE_NAME, COMPARATOR_NAME},
            f"unexpected participants in game {game_no}: {white} vs {black}",
        )
        record = GameRecord(
            local_game=game_no,
            white=white,
            black=black,
            result=result,
            reason=reason,
            candidate_points=candidate_score(white, black, result),
        )
        self.games[game_no] = record
        matched_terms = [term for term in BAD_REASON_TERMS if term in reason.lower()]
        if matched_terms:
            self.defects.append(
                {"game": game_no, "reason": reason, "matched_terms": matched_terms}
            )
        return True

    def require_complete(self) -> list[GameRecord]:
        missing = sorted(set(range(1, self.expected_games + 1)) - set(self.games))
        require(not missing, f"missing games: {missing[:20]}")
        ordered = [self.games[index] for index in range(1, self.expected_games + 1)]
        for first_index in range(0, len(ordered), 2):
            first, second = ordered[first_index : first_index + 2]
            require(
                first.white == second.black and first.black == second.white,
                f"pair {first_index // 2 + 1} did not swap colours",
            )
        return ordered


class RungAccumulator:
    def __init__(self) -> None:
        self.games: list[AggregateGameRecord] = []
        self.defects: list[dict[str, object]] = []

    def add_batch(
        self,
        *,
        batch_index: int,
        opening_start: int,
        records: Iterable[GameRecord],
        defects: list[dict[str, object]],
    ) -> None:
        batch_records = list(records)
        require(len(self.games) % 2 == 0, "aggregate lost pair alignment")
        global_start = len(self.games) + 1
        for offset, record in enumerate(batch_records):
            self.games.append(
                AggregateGameRecord(
                    global_game=global_start + offset,
                    batch_index=batch_index,
                    local_game=record.local_game,
                    opening_index=opening_start + offset // 2,
                    white=record.white,
                    black=record.black,
                    result=record.result,
                    reason=record.reason,
                    candidate_points=record.candidate_points,
                )
            )
        for defect in defects:
            item = dict(defect)
            item["batch_index"] = batch_index
            item["global_game"] = global_start + int(defect["game"]) - 1
            self.defects.append(item)

    def snapshot(self) -> dict[str, object]:
        wld = [0, 0, 0]
        for record in self.games:
            bucket = (
                0
                if record.candidate_points == 1.0
                else 1
                if record.candidate_points == 0.0
                else 2
            )
            wld[bucket] += 1
        penta = [0, 0, 0, 0, 0]
        for first_index in range(0, len(self.games), 2):
            pair = self.games[first_index : first_index + 2]
            require(len(pair) == 2, "aggregate contains an incomplete pair")
            bucket = int(round(2.0 * sum(item.candidate_points for item in pair)))
            penta[bucket] += 1
        projection = [
            [
                item.global_game,
                item.batch_index,
                item.local_game,
                item.opening_index,
                item.white,
                item.black,
                item.result,
                item.reason,
            ]
            for item in self.games
        ]
        return {
            "games": len(self.games),
            "pairs": len(self.games) // 2,
            "wld_candidate_pov": wld,
            "pentanomial_candidate_pov": penta,
            "statistics_historical_wld_method": historical_wld_statistics(wld),
            "statistics_openbench_pentanomial_method": openbench_statistics(penta),
            "defects": list(self.defects),
            "game_projection_sha256": hashlib.sha256(
                canonical_json_bytes(projection)
            ).hexdigest(),
        }


def authenticate(path: Path, pin: dict[str, object], role: str) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    observed = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    require(observed["bytes"] == pin["bytes"], f"{role} byte count drifted")
    require(observed["sha256"] == pin["sha256"], f"{role} SHA-256 drifted")
    return observed


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
    expected_schema: str,
    expected_result: str,
    effective_time: datetime,
) -> dict[str, object]:
    precondition = contract["host_precondition"]
    producer_pin = precondition["attestation_producer"]
    producer_path = repository_root / producer_pin["path"]
    producer = authenticate(producer_path, producer_pin, "host attestation producer")
    require(host.get("schema") == expected_schema, "host attestation schema mismatch")
    require(host.get("result") == expected_result, "host attestation did not pass")
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


def engine_tokens(
    *,
    role: str,
    paths: dict[str, Path],
    tc: str,
    contract: dict[str, object],
    stderr_path: Path,
) -> list[str]:
    settings = contract["engine_settings"]
    if role == "candidate":
        tokens = [
            f"cmd={paths['candidate']}",
            f"dir={paths['candidate'].parent}",
            f"name={CANDIDATE_NAME}",
            "proto=uci",
        ]
    elif role == "comparator":
        tokens = [
            f"cmd={paths['adapter']}",
            f"dir={paths['adapter'].parent}",
            f"name={COMPARATOR_NAME}",
            "proto=uci",
            "arg=--engine",
            f"arg={paths['raw_fairy']}",
            "arg=--network",
            f"arg={paths['network']}",
        ]
    else:
        raise RuntimeError(f"unknown engine role: {role}")
    tokens.extend(
        [
            f"tc={tc}",
            f"timemargin={settings['time_margin_ms']}",
            "restart=off",
            f"stderr={stderr_path}",
            "option.UCI_Variant=crazyhouse",
            f"option.CrazyhouseProfile={PROFILE_TOKEN}",
            f"option.CrazyhouseEvalFile={paths['network']}",
            f"option.Threads={settings['threads']}",
            f"option.Hash={settings['hash_mib']}",
            "option.MultiPV=1",
            "option.Ponder=false",
            f"option.Move Overhead={settings['move_overhead_ms']}",
            "option.UCI_ShowWDL=false",
            "option.SyzygyProbeLimit=0",
        ]
    )
    return tokens


def build_command(
    *,
    paths: dict[str, Path],
    tc: str,
    contract: dict[str, object],
    batch_dir: Path,
    games: int,
    opening_start: int,
    seed: int,
    event: str,
    debug: bool,
) -> list[str]:
    require(games > 0 and games % 2 == 0, "games must preserve complete pairs")
    command = [
        str(paths["referee"]),
        "-repeat",
        "-variant",
        "crazyhouse",
        "-concurrency",
        str(contract["execution"]["concurrency"]),
        "-games",
        str(games),
        "-maxmoves",
        str(contract["adjudication"]["safety_max_fullmoves"]),
        "-ratinginterval",
        str(contract["reporting"]["rating_interval_games"]),
        "-outcomeinterval",
        str(contract["reporting"]["outcome_interval_games"]),
        "-engine",
        *engine_tokens(
            role="candidate",
            paths=paths,
            tc=tc,
            contract=contract,
            stderr_path=batch_dir / "candidate.stderr.log",
        ),
        "-engine",
        *engine_tokens(
            role="comparator",
            paths=paths,
            tc=tc,
            contract=contract,
            stderr_path=batch_dir / "comparator.stderr.log",
        ),
        "-openings",
        f"file={paths['book']}",
        "format=epd",
        "order=sequential",
        f"start={opening_start}",
        "policy=default",
        "-srand",
        str(seed),
        "-pgnout",
        str(batch_dir / "games.pgn"),
        "-event",
        event,
        "-site",
        "local",
    ]
    if debug:
        command.append("-debug")
    return command


def runtime_environment(contract: dict[str, object]) -> dict[str, str]:
    environment = dict(os.environ)
    prepend = [
        str(Path(item).resolve(strict=True))
        for item in contract["runtime"]["path_prepend"]
    ]
    if prepend:
        environment["PATH"] = os.pathsep.join(
            prepend + [environment.get("PATH", "")]
        )
    for name, value in contract["runtime"]["environment"].items():
        environment[str(name)] = str(value)
    return environment


def terminate_exact_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10.0)


def run_batch(
    *,
    command: list[str],
    batch_dir: Path,
    expected_games: int,
    environment: dict[str, str],
    no_progress_timeout_seconds: float,
    batch_timeout_seconds: float,
    active: list[subprocess.Popen[str]],
) -> tuple[int, BatchTracker, str | None]:
    batch_dir.mkdir(parents=True, exist_ok=False)
    tracker = BatchTracker(expected_games)
    log_path = batch_dir / "cutechess.log"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=CREATE_NO_WINDOW,
        env=environment,
    )
    try:
        job = ProcessJob(process)
    except BaseException:
        terminate_exact_process(process)
        raise
    active.append(process)
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        try:
            for raw in process.stdout:
                lines.put(raw.rstrip("\r\n"))
        finally:
            lines.put(None)

    reader = threading.Thread(
        target=pump, name=f"cutechess-reader-{process.pid}", daemon=True
    )
    reader.start()
    parse_error: str | None = None
    started = time.monotonic()
    last_game = started
    stream_ended = False
    try:
        with log_path.open("x", encoding="utf-8", newline="\n") as log:
            while not stream_ended:
                try:
                    line = lines.get(timeout=1.0)
                except queue.Empty:
                    now = time.monotonic()
                    if now - started >= batch_timeout_seconds:
                        parse_error = f"batch timeout after {now - started:.1f}s"
                        terminate_exact_process(process)
                        break
                    if now - last_game >= no_progress_timeout_seconds:
                        parse_error = f"no completed game for {now - last_game:.1f}s"
                        terminate_exact_process(process)
                        break
                    continue
                if line is None:
                    stream_ended = True
                    continue
                log.write(line + "\n")
                log.flush()
                try:
                    if tracker.consume(line):
                        last_game = time.monotonic()
                except BaseException as exc:
                    parse_error = f"{type(exc).__name__}: {exc}"
                    terminate_exact_process(process)
                    break
        if process.poll() is None:
            return_code = process.wait(timeout=30.0)
        else:
            return_code = int(process.returncode)
    except BaseException:
        terminate_exact_process(process)
        raise
    finally:
        if process in active:
            active.remove(process)
        reader.join(timeout=5.0)
        job.close()
    return return_code, tracker, parse_error


def check_stderr(batch_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in ("candidate.stderr.log", "comparator.stderr.log"):
        path = batch_dir / name
        require(path.is_file(), f"missing engine stderr file: {path}")
        result[name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "empty": path.stat().st_size == 0,
        }
    return result


def output_identity(path: Path) -> dict[str, object]:
    require(path.is_file(), f"missing output: {path}")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def verify_canary_log(
    log_path: Path, tracker: BatchTracker, network_sha256: str
) -> dict[str, object]:
    text = log_path.read_text(encoding="utf-8")
    nonce_sets = NONCE_SET_RE.findall(text)
    nonce_acks = NONCE_ACK_RE.findall(text)
    lines = text.splitlines()
    candidate_routes = [
        line
        for line in lines
        if "backend=legacy-v1" in line and "evaluator=incremental-scalar" in line
    ]
    comparator_routes = [
        line
        for line in lines
        if "backend=fairy-external" in line and "evaluator=halfkav2variants" in line
    ]
    checks = {
        "complete_pair": len(tracker.games) == 2,
        "no_defects": not tracker.defects,
        "four_unique_nonce_sets": len(nonce_sets) == 4 and len(set(nonce_sets)) == 4,
        "acknowledgements_match": sorted(nonce_sets) == sorted(nonce_acks),
        "candidate_route": len(candidate_routes) >= 2,
        "comparator_route": len(comparator_routes) >= 2,
        "network_identity_in_routes": all(
            f"identity={network_sha256}" in line
            for line in candidate_routes + comparator_routes
        ),
        "variant_commands": text.count(
            "setoption name UCI_Variant value crazyhouse"
        )
        >= 4,
        "profile_commands": text.count(
            f"setoption name CrazyhouseProfile value {PROFILE_TOKEN}"
        )
        >= 4,
        "adapted_nnue_markers": sum(
            "NNUE evaluation using" in line and "enabled" in line for line in lines
        )
        >= 2,
        "no_error_record": "info string ERROR" not in text,
    }
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "nonce_sets": nonce_sets,
        "nonce_acks": nonce_acks,
    }


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def load_inputs(
    args: argparse.Namespace,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, Path],
    dict[str, object],
]:
    contract_path = args.contract.resolve(strict=True)
    authorization_path = args.authorization.resolve(strict=True)
    overhead_path = args.overhead_result.resolve(strict=True)
    overhead_verification_path = args.overhead_verification.resolve(strict=True)
    contract = load_json(contract_path)
    authorization = load_json(authorization_path)
    overhead = load_json(overhead_path)
    overhead_verification = load_json(overhead_verification_path)
    require(
        contract.get("schema") == "crazyhouse-local-strength-panel/v2",
        "panel contract schema mismatch",
    )
    require(
        authorization.get("schema")
        == "crazyhouse-local-strength-panel-authorization/v1",
        "panel authorization schema mismatch",
    )
    require(
        overhead.get("schema") == "crazyhouse-local-adapter-overhead-result/v1",
        "adapter overhead result schema mismatch",
    )
    require(
        overhead.get("result") == "PASS_ADAPTER_OVERHEAD",
        "adapter overhead gate did not pass",
    )
    require(
        overhead_verification.get("schema")
        == "crazyhouse-local-adapter-overhead-independent-verification/v1",
        "adapter overhead verification schema mismatch",
    )
    require(
        overhead_verification.get("result")
        == "PASS_ADAPTER_OVERHEAD_INDEPENDENTLY_VERIFIED",
        "adapter overhead was not independently verified",
    )
    require(overhead.get("error") is None, "adapter overhead result contains an error")
    require(
        overhead.get("time_controls_derived") is False,
        "adapter gate attempted to derive time controls",
    )
    contract_identity = {
        "path": str(contract_path),
        "bytes": contract_path.stat().st_size,
        "sha256": sha256_file(contract_path),
    }
    require(
        contract_identity["bytes"] == authorization["parent_contract"]["bytes"],
        "authorization parent bytes drifted",
    )
    require(
        contract_identity["sha256"]
        == authorization["parent_contract"]["sha256"],
        "authorization parent hash drifted",
    )
    overhead_identity = {
        "path": str(overhead_path),
        "bytes": overhead_path.stat().st_size,
        "sha256": sha256_file(overhead_path),
    }
    require(
        overhead_identity["bytes"]
        == authorization["adapter_overhead_result"]["bytes"],
        "authorized overhead bytes drifted",
    )
    require(
        overhead_identity["sha256"]
        == authorization["adapter_overhead_result"]["sha256"],
        "authorized overhead hash drifted",
    )
    overhead_verification_identity = {
        "path": str(overhead_verification_path),
        "bytes": overhead_verification_path.stat().st_size,
        "sha256": sha256_file(overhead_verification_path),
    }
    require(
        overhead_verification_identity["bytes"]
        == authorization["adapter_overhead_verification"]["bytes"],
        "authorized overhead verification bytes drifted",
    )
    require(
        overhead_verification_identity["sha256"]
        == authorization["adapter_overhead_verification"]["sha256"],
        "authorized overhead verification hash drifted",
    )
    require(
        overhead_verification["formal_result"]["bytes"] == overhead_identity["bytes"]
        and overhead_verification["formal_result"]["sha256"]
        == overhead_identity["sha256"],
        "independent verification used a different overhead result",
    )
    require(
        authorization.get("authorized_time_controls") == contract.get("time_controls"),
        "authorized time-control ladder drifted",
    )
    repository_root = contract_path.parents[2]
    overhead_contract_pin = contract["adapter_overhead_precondition"]["contract"]
    overhead_contract_path = repository_root / overhead_contract_pin["path"]
    overhead_contract_identity = authenticate(
        overhead_contract_path, overhead_contract_pin, "adapter overhead contract"
    )
    require(
        overhead["contract"]["bytes"] == overhead_contract_identity["bytes"]
        and overhead["contract"]["sha256"] == overhead_contract_identity["sha256"],
        "adapter result used the wrong overhead contract",
    )
    overhead_contract = load_json(overhead_contract_path)
    require(
        overhead_verification["contract"]["bytes"]
        == overhead_contract_identity["bytes"]
        and overhead_verification["contract"]["sha256"]
        == overhead_contract_identity["sha256"],
        "independent verification used a different overhead contract",
    )
    require(
        overhead_verification.get("recomputed") == overhead.get("adapter_overhead"),
        "independent overhead recomputation drifted",
    )
    timing_host_path = Path(overhead["host_attestation"]["path"])
    timing_host_identity = authenticate(
        timing_host_path,
        overhead["host_attestation"],
        "adapter overhead host attestation",
    )
    timing_host_validation = validate_host_attestation(
        load_json(timing_host_path),
        overhead_contract,
        repository_root,
        expected_schema="crazyhouse-host-timing-attestation/v1",
        expected_result="PASS_HOST_TIMING_CLEAN",
        effective_time=parse_utc(overhead.get("started_utc"), "overhead started_utc"),
    )
    require(
        overhead.get("host_attestation_validation") == timing_host_validation,
        "adapter overhead host validation drifted",
    )
    require(
        overhead["adapter_overhead"]["pass"] is True,
        "adapter result's internal overhead decision did not pass",
    )
    require(
        overhead["sample"]["rows"] == overhead_contract["sample"]["rows"]
        and overhead["sample"]["selection_sha256"]
        == overhead_contract["sample"]["selection_sha256"],
        "adapter result sample drifted",
    )
    require(
        len(overhead["blocks"]["adapter_overhead"])
        == len(overhead_contract["adapter_overhead"]["block_order"]),
        "adapter result block count drifted",
    )
    for role in ("candidate", "adapter", "raw_fairy", "network"):
        require(
            overhead["inputs"][role]["bytes"] == contract["inputs"][role]["bytes"]
            and overhead["inputs"][role]["sha256"]
            == contract["inputs"][role]["sha256"],
            f"adapter result {role} identity drifted",
        )
    require(
        overhead["inputs"]["corpus"]["bytes"] == contract["opening_reference"]["bytes"]
        and overhead["inputs"]["corpus"]["sha256"]
        == contract["opening_reference"]["sha256"],
        "adapter result corpus identity drifted",
    )
    paths = {
        "candidate": args.candidate.resolve(strict=True),
        "adapter": args.adapter.resolve(strict=True),
        "raw_fairy": args.raw_fairy.resolve(strict=True),
        "network": args.network.resolve(strict=True),
        "referee": args.referee.resolve(strict=True),
        "book": args.book.resolve(strict=True),
    }
    authenticated = {
        role: authenticate(path, contract["inputs"][role], role)
        for role, path in paths.items()
    }
    runner = Path(__file__).resolve()
    require(
        runner.stat().st_size == contract["implementation"]["bytes"],
        "runner byte count drifted",
    )
    require(
        sha256_file(runner) == contract["implementation"]["sha256"],
        "runner SHA-256 drifted",
    )
    identities = {
        "contract": contract_identity,
        "authorization": {
            "path": str(authorization_path),
            "bytes": authorization_path.stat().st_size,
            "sha256": sha256_file(authorization_path),
        },
        "adapter_overhead_result": overhead_identity,
        "adapter_overhead_verification": overhead_verification_identity,
        "adapter_overhead_contract": overhead_contract_identity,
        "adapter_overhead_host_attestation": timing_host_identity,
        "assets": authenticated,
        "runner": {
            "path": str(runner),
            "bytes": runner.stat().st_size,
            "sha256": sha256_file(runner),
        },
    }
    return contract, authorization, overhead, paths, identities


def rung_batch_size(total_games: int, execution: dict[str, object]) -> int:
    maximum = int(execution["maximum_games_per_rung"])
    require(total_games < maximum, "rung is already at its cap")
    requested = (
        int(execution["initial_batch_games"])
        if total_games == 0
        else int(execution["continuation_batch_games"])
    )
    result = min(requested, maximum - total_games)
    require(result > 0 and result % 2 == 0, "batch plan broke pair alignment")
    return result


def endpoint_decision(
    snapshot: dict[str, object], execution: dict[str, object]
) -> dict[str, object]:
    total = int(snapshot["games"])
    stats = snapshot["statistics_historical_wld_method"]
    display = stats.get("display_los_percent")
    eligible = total >= int(execution["minimum_games_before_los_decision"])
    endpoint = display if eligible and display in {"0.0", "100.0"} else None
    return {
        "eligible": eligible,
        "display_los_percent": display,
        "endpoint": endpoint,
        "continue": endpoint is None
        and total < int(execution["maximum_games_per_rung"]),
        "rung_pass": endpoint == "100.0",
        "rung_reject": endpoint == "0.0",
        "cap_reached_without_endpoint": endpoint is None
        and total == int(execution["maximum_games_per_rung"]),
    }


def main(args: argparse.Namespace) -> int:
    contract, authorization, overhead, paths, identities = load_inputs(args)
    output_dir = args.output_dir.resolve()
    dry_commands: dict[str, object] = {}
    for rung in contract["time_controls"]:
        batch_dir = output_dir / str(rung["id"]) / "batch-0000"
        dry_commands[str(rung["id"])] = build_command(
            paths=paths,
            tc=str(rung["cutechess_tc"]),
            contract=contract,
            batch_dir=batch_dir,
            games=int(contract["execution"]["initial_batch_games"]),
            opening_start=1,
            seed=int(rung["srand"]),
            event=f"Crazyhouse local LOS gate {rung['id']} batch 0",
            debug=False,
        )
    canary_dir = output_dir / "canary" / "batch-0000"
    canary_command = build_command(
        paths=paths,
        tc=str(contract["time_controls"][0]["cutechess_tc"]),
        contract=contract,
        batch_dir=canary_dir,
        games=2,
        opening_start=1,
        seed=int(contract["canary"]["srand"]),
        event="Crazyhouse local LOS gate plumbing canary",
        debug=True,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "schema": "crazyhouse-local-strength-panel-dry-run/v2",
                    "identities": identities,
                    "authorized_time_controls": authorization[
                        "authorized_time_controls"
                    ],
                    "adapter_overhead_result": overhead["result"],
                    "canary_command": canary_command,
                    "first_batch_commands": dry_commands,
                    "stopping_rule": contract["stopping_rule"],
                    "game_results_consumed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    require(
        args.host_attestation is not None,
        "formal panel requires a fresh host attestation",
    )
    host_path = args.host_attestation.resolve(strict=True)
    host = load_json(host_path)
    started = datetime.now(timezone.utc)
    host_validation = validate_host_attestation(
        host,
        contract,
        repository_root,
        expected_schema="crazyhouse-host-strength-attestation/v1",
        expected_result="PASS_HOST_STRENGTH_READY",
        effective_time=started,
    )
    require(not output_dir.exists(), f"output directory must be fresh: {output_dir}")
    output_dir.mkdir(parents=True)
    started_utc = started.isoformat().replace("+00:00", "Z")
    host_identity = {
        "path": str(host_path),
        "bytes": host_path.stat().st_size,
        "sha256": sha256_file(host_path),
    }
    manifest = {
        "schema": "crazyhouse-local-strength-panel-runtime/v2",
        "started_utc": started_utc,
        "evidence_class": "S3_STRENGTH",
        "identities": identities,
        "host_attestation": host_identity,
        "host_attestation_validation": host_validation,
        "time_controls": contract["time_controls"],
        "execution": contract["execution"],
        "stopping_rule": contract["stopping_rule"],
        "canary_command": canary_command,
        "first_batch_commands": dry_commands,
        "no_optional_extension": True,
        "no_score_adjudication": True,
        "no_recovery_or_restart": True,
        "python": sys.version,
    }
    write_json_fresh(output_dir / "manifest.json", manifest)
    environment = runtime_environment(contract)
    active: list[subprocess.Popen[str]] = []
    try:
        canary_code, canary_tracker, canary_error = run_batch(
            command=canary_command,
            batch_dir=canary_dir,
            expected_games=2,
            environment=environment,
            no_progress_timeout_seconds=float(
                contract["runtime"]["no_progress_timeout_seconds"]
            ),
            batch_timeout_seconds=float(contract["runtime"]["batch_timeout_seconds"]),
            active=active,
        )
        require(canary_error is None, f"canary failed: {canary_error}")
        require(canary_code == 0, f"canary referee exited {canary_code}")
        canary_records = canary_tracker.require_complete()
        canary_stderr = check_stderr(canary_dir)
        require(
            all(item["empty"] for item in canary_stderr.values()),
            "canary engine stderr was not empty",
        )
        canary_routing = verify_canary_log(
            canary_dir / "cutechess.log",
            canary_tracker,
            str(contract["inputs"]["network"]["sha256"]),
        )
        require(canary_routing["pass"], "canary routing proof failed")
        canary_result = {
            "schema": "crazyhouse-local-strength-canary-result/v2",
            "result": "PASS_PLUMBING_ONLY",
            "score_interpretation": False,
            "games": [asdict(item) for item in canary_records],
            "routing": canary_routing,
            "stderr": canary_stderr,
            "outputs": {
                "log": output_identity(canary_dir / "cutechess.log"),
                "pgn": output_identity(canary_dir / "games.pgn"),
            },
        }
        write_json_fresh(output_dir / "canary" / "result.json", canary_result)

        rung_results: list[dict[str, object]] = []
        infrastructure_valid = True
        ladder_pass = True
        stop_reason: str | None = None
        for rung_index, rung in enumerate(contract["time_controls"]):
            rung_id = str(rung["id"])
            accumulator = RungAccumulator()
            batch_receipts: list[dict[str, object]] = []
            batch_index = 0
            decision: dict[str, object] | None = None
            while True:
                total_before = len(accumulator.games)
                games = rung_batch_size(total_before, contract["execution"])
                opening_start = total_before // 2 + 1
                batch_dir = output_dir / rung_id / f"batch-{batch_index:04d}"
                seed = int(rung["srand"]) + batch_index
                command = build_command(
                    paths=paths,
                    tc=str(rung["cutechess_tc"]),
                    contract=contract,
                    batch_dir=batch_dir,
                    games=games,
                    opening_start=opening_start,
                    seed=seed,
                    event=f"Crazyhouse local LOS gate {rung_id} batch {batch_index}",
                    debug=False,
                )
                code, tracker, parse_error = run_batch(
                    command=command,
                    batch_dir=batch_dir,
                    expected_games=games,
                    environment=environment,
                    no_progress_timeout_seconds=float(
                        contract["runtime"]["no_progress_timeout_seconds"]
                    ),
                    batch_timeout_seconds=float(
                        contract["runtime"]["batch_timeout_seconds"]
                    ),
                    active=active,
                )
                require(
                    parse_error is None,
                    f"{rung_id} batch {batch_index} failed: {parse_error}",
                )
                require(
                    code == 0,
                    f"{rung_id} batch {batch_index} referee exited {code}",
                )
                records = tracker.require_complete()
                stderr = check_stderr(batch_dir)
                batch_valid = not tracker.defects and all(
                    item["empty"] for item in stderr.values()
                )
                accumulator.add_batch(
                    batch_index=batch_index,
                    opening_start=opening_start,
                    records=records,
                    defects=tracker.defects,
                )
                snapshot = accumulator.snapshot()
                decision = endpoint_decision(snapshot, contract["execution"])
                receipt = {
                    "schema": "crazyhouse-local-strength-batch-result/v2",
                    "rung": rung,
                    "batch_index": batch_index,
                    "games": games,
                    "opening_start": opening_start,
                    "opening_end": opening_start + games // 2 - 1,
                    "seed": seed,
                    "command": command,
                    "valid": batch_valid,
                    "batch_defects": tracker.defects,
                    "stderr": stderr,
                    "cumulative": snapshot,
                    "decision": decision,
                    "outputs": {
                        "log": output_identity(batch_dir / "cutechess.log"),
                        "pgn": output_identity(batch_dir / "games.pgn"),
                    },
                }
                receipt_path = batch_dir / "result.json"
                write_json_fresh(receipt_path, receipt)
                batch_receipts.append(output_identity(receipt_path))
                if not batch_valid:
                    infrastructure_valid = False
                    ladder_pass = False
                    stop_reason = f"INVALID_{rung_id}_BATCH_{batch_index}"
                    break
                if not decision["continue"]:
                    break
                batch_index += 1

            require(decision is not None, f"{rung_id} produced no decision")
            snapshot = accumulator.snapshot()
            if infrastructure_valid:
                if decision["rung_pass"]:
                    rung_status = "PASS_LOS_100_0"
                elif decision["rung_reject"]:
                    rung_status = "REJECT_LOS_0_0"
                    ladder_pass = False
                    stop_reason = f"LOS_0_0_AT_{rung_id}"
                else:
                    rung_status = "NO_ENDPOINT_AT_FROZEN_CAP"
                    ladder_pass = False
                    stop_reason = f"NO_ENDPOINT_AT_CAP_{rung_id}"
            else:
                rung_status = "INVALID_INFRASTRUCTURE"
            rung_result = {
                "schema": "crazyhouse-local-strength-rung-result/v2",
                "created_utc": utc_now(),
                "rung_index": rung_index,
                "rung": rung,
                "result": rung_status,
                "snapshot": snapshot,
                "decision": decision,
                "batch_results": batch_receipts,
            }
            rung_result_path = output_dir / rung_id / "result.json"
            write_json_fresh(rung_result_path, rung_result)
            rung_results.append(output_identity(rung_result_path))
            if rung_status != "PASS_LOS_100_0":
                break

        all_rungs_completed = len(rung_results) == len(contract["time_controls"])
        if not infrastructure_valid:
            classification = "INVALID_LOCAL_STRENGTH_GATE"
        elif ladder_pass and all_rungs_completed:
            classification = "PASS_LOCAL_SAME_NETWORK_FSF_GATE"
        elif stop_reason and stop_reason.startswith("LOS_0_0"):
            classification = "VALID_LOCAL_GATE_DID_NOT_BEAT_FSF"
        else:
            classification = "VALID_LOCAL_GATE_INCONCLUSIVE_AT_CAP"
        panel_result = {
            "schema": "crazyhouse-local-strength-panel-result/v2",
            "created_utc": utc_now(),
            "evidence_class": "S3_STRENGTH",
            "result": classification,
            "infrastructure_valid": infrastructure_valid,
            "strength_gate_pass": classification
            == "PASS_LOCAL_SAME_NETWORK_FSF_GATE",
            "all_rungs_completed": all_rungs_completed,
            "stop_reason": stop_reason,
            "rung_results": rung_results,
            "thresholds": {
                "minimum_games": contract["execution"][
                    "minimum_games_before_los_decision"
                ],
                "displayed_los_endpoints_percent": ["0.0", "100.0"],
                "pass_endpoint_every_rung": "100.0",
            },
            "openbench_authorized": False,
            "independent_verification_pending": True,
            "release_claim": False,
        }
        panel_result_path = output_dir / "panel-result.json"
        write_json_fresh(panel_result_path, panel_result)
        completion = {
            "schema": "crazyhouse-local-strength-panel-completion/v2",
            "created_utc": utc_now(),
            "started_utc": started_utc,
            "result": classification,
            "manifest": output_identity(output_dir / "manifest.json"),
            "canary_result": output_identity(output_dir / "canary" / "result.json"),
            "panel_result": output_identity(panel_result_path),
            "independent_verification_pending": True,
            "openbench_authorized": False,
        }
        write_json_fresh(output_dir / "completion.json", completion)
        print(classification)
        return 0 if infrastructure_valid else 1
    except BaseException as exc:
        for process in list(active):
            terminate_exact_process(process)
        invalid = {
            "schema": "crazyhouse-local-strength-panel-invalid/v2",
            "created_utc": utc_now(),
            "started_utc": started_utc,
            "error": f"{type(exc).__name__}: {exc}",
            "openbench_authorized": False,
            "strength_claim": False,
        }
        write_json_fresh(output_dir / "INVALID.json", invalid)
        print(invalid["error"], file=sys.stderr)
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--overhead-result", type=Path, required=True)
    parser.add_argument("--overhead-verification", type=Path, required=True)
    parser.add_argument("--host-attestation", type=Path)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--raw-fairy", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--referee", type=Path, required=True)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
