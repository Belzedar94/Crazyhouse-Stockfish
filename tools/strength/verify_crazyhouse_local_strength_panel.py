#!/usr/bin/env python3
"""Independently verify a completed Crazyhouse local LOS ladder."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Iterable


CANDIDATE = "candidate"
COMPARATOR = "fairy-adapted"
PROFILE = (
    "LICHESS_CRAZYHOUSE_2026_08_12@"
    "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
)
FINISHED = re.compile(
    r"^Finished game\s+(\d+)\s+\((.+) vs (.+)\):\s+"
    r"(1-0|0-1|1/2-1/2)\s+\{(.*)\}\s*$"
)
NONCE_SET = re.compile(
    r"^(?:\d+\s+)?>(?:candidate|fairy-adapted)\(\d+\): setoption name "
    r"CrazyhouseCapabilityNonce value ([0-9a-f]{32})\r?$",
    re.MULTILINE,
)
NONCE_ACK = re.compile(
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
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
    producer_pin = contract["host_precondition"]["attestation_producer"]
    producer = authenticate(
        repository_root / producer_pin["path"],
        producer_pin,
        "host attestation producer",
    )
    require(host.get("schema") == expected_schema, "host attestation schema drifted")
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
    require(host["host"].get("priority_or_affinity_changed") is False, "host priority or affinity changed")
    captured = parse_utc(host.get("captured_utc"), "host captured_utc")
    valid_until = parse_utc(host.get("valid_until_utc"), "host valid_until_utc")
    require(captured <= effective_time <= valid_until, "host attestation was not valid at run start")
    return {
        "producer": producer,
        "captured_utc": host["captured_utc"],
        "valid_until_utc": host["valid_until_utc"],
        "effective_time_utc": effective_time.isoformat().replace("+00:00", "Z"),
    }


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def write_json_fresh(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def logistic_elo(score: float) -> float:
    score = min(max(score, 1e-3), 1.0 - 1e-3)
    return -400.0 * math.log10(1.0 / score - 1.0)


def old_erf(value: float) -> float:
    coefficient = 8.0 * (math.pi - 3.0) / (3.0 * math.pi * (4.0 - math.pi))
    squared = value * value
    exponent = -squared * (4.0 / math.pi + coefficient * squared) / (
        1.0 + coefficient * squared
    )
    return math.copysign(math.sqrt(1.0 - math.exp(exponent)), value)


def historical_stats(wld: list[int]) -> dict[str, object]:
    require(len(wld) == 3 and all(item >= 0 for item in wld), "invalid WLD")
    total = sum(wld)
    if total == 0:
        return {"available": False, "total": 0, "display_los_percent": None}
    win, loss, draw = (float(item) / total for item in wld)
    mean = win + draw / 2.0
    standard_error = math.sqrt(
        win * (1.0 - mean) ** 2
        + loss * mean**2
        + draw * (0.5 - mean) ** 2
    ) / math.sqrt(total)
    if not math.isfinite(standard_error) or standard_error <= 0.0:
        return {
            "available": False,
            "total": total,
            "mean_score": mean,
            "standard_error": standard_error,
            "display_los_percent": None,
        }
    los = 0.5 * (
        1.0 + old_erf(((mean - 0.5) / standard_error) / math.sqrt(2.0))
    )
    elo = logistic_elo(mean)
    display = f"{100.0 * los:.1f}"
    return {
        "available": True,
        "total": total,
        "mean_score": mean,
        "standard_error": standard_error,
        "elo": elo,
        "los": los,
        "los_percent": 100.0 * los,
        "display_los_percent": display,
        "display_line": f"ELO: {elo:.2f} LOS: {display}%",
    }


def openbench_stats(penta: list[int]) -> dict[str, object]:
    count = sum(penta)
    if count == 0:
        return {
            "lower_elo": 0.0,
            "elo": 0.0,
            "upper_elo": 0.0,
            "ci95_minus": 0.0,
            "ci95_plus": 0.0,
            "los": 0.5,
            "los_percent": 50.0,
        }
    mean = sum((index / 4.0) * value for index, value in enumerate(penta)) / count
    variance = sum(
        ((index / 4.0) - mean) ** 2 * value for index, value in enumerate(penta)
    ) / count
    error = math.sqrt(variance) / math.sqrt(count)
    if error == 0.0:
        low_score = high_score = mean
        los = 0.5 if mean == 0.5 else float(mean > 0.5)
    else:
        low_score = mean + NormalDist().inv_cdf(0.025) * error
        high_score = mean + NormalDist().inv_cdf(0.975) * error
        los = NormalDist().cdf((mean - 0.5) / error)
    low = logistic_elo(low_score)
    middle = logistic_elo(mean)
    high = logistic_elo(high_score)
    return {
        "lower_elo": low,
        "elo": middle,
        "upper_elo": high,
        "ci95_minus": middle - low,
        "ci95_plus": high - middle,
        "los": los,
        "los_percent": 100.0 * los,
    }


def deeply_equal(left: object, right: object, *, path: str = "root") -> None:
    if isinstance(left, float) or isinstance(right, float):
        require(
            isinstance(left, (int, float)) and isinstance(right, (int, float)),
            f"numeric type drift at {path}",
        )
        require(
            math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12),
            f"numeric drift at {path}: {left!r} != {right!r}",
        )
        return
    require(type(left) is type(right), f"type drift at {path}")
    if isinstance(left, dict):
        require(set(left) == set(right), f"key drift at {path}")
        for key in left:
            deeply_equal(left[key], right[key], path=f"{path}.{key}")
    elif isinstance(left, list):
        require(len(left) == len(right), f"length drift at {path}")
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            deeply_equal(left_item, right_item, path=f"{path}[{index}]")
    else:
        require(left == right, f"value drift at {path}: {left!r} != {right!r}")


def score_for_candidate(white: str, black: str, result: str) -> float:
    require({white, black} == {CANDIDATE, COMPARATOR}, "unexpected participants")
    if result == "1/2-1/2":
        return 0.5
    winner = white if result == "1-0" else black
    return 1.0 if winner == CANDIDATE else 0.0


def parse_log(path: Path, expected_games: int) -> list[dict[str, object]]:
    games: dict[int, dict[str, object]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = FINISHED.match(raw.strip())
        if match is None:
            continue
        number = int(match.group(1))
        require(1 <= number <= expected_games, f"log game out of range: {number}")
        require(number not in games, f"duplicate log game: {number}")
        white, black, result, reason = match.group(2, 3, 4, 5)
        bad = [term for term in BAD_REASON_TERMS if term in reason.lower()]
        require(not bad, f"game {number} has invalid reason {reason!r}: {bad}")
        games[number] = {
            "local_game": number,
            "white": white,
            "black": black,
            "result": result,
            "reason": reason,
            "candidate_points": score_for_candidate(white, black, result),
        }
    require(len(games) == expected_games, "log game count drifted")
    ordered = [games[index] for index in range(1, expected_games + 1)]
    for index in range(0, expected_games, 2):
        first, second = ordered[index : index + 2]
        require(
            first["white"] == second["black"]
            and first["black"] == second["white"],
            f"log pair {index // 2 + 1} did not swap colours",
        )
    return ordered


def import_reference(reference_root: Path, contract: dict[str, object]):
    root = reference_root.resolve(strict=True)
    module_path = root / "chess" / "__init__.py"
    authenticate(module_path, contract["reference_runtime"]["module"], "python-chess module")
    sys.path.insert(0, str(root))
    try:
        chess = importlib.import_module("chess")
        pgn = importlib.import_module("chess.pgn")
        variant = importlib.import_module("chess.variant")
    finally:
        sys.path.pop(0)
    require(Path(chess.__file__).resolve() == module_path.resolve(), "wrong python-chess imported")
    require(chess.__version__ == contract["reference_runtime"]["version"], "python-chess version drifted")
    require(variant.CrazyhouseBoard is not None, "Crazyhouse reference unavailable")
    return chess, pgn, variant


def load_opening_reference(path: Path, expected_rows: int) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    require(len(rows) == expected_rows, "opening trajectory row count drifted")
    for index, row in enumerate(rows):
        require(row["accepted_index"] == index, "opening trajectory order drifted")
        require(row["schema"] == "crazyhouse-local-gate-opening-trajectory/v2", "opening schema drifted")
    return rows


def verify_pgn(
    *,
    path: Path,
    log_games: list[dict[str, object]],
    openings: list[dict[str, object]],
    opening_start: int,
    expected_tc: str,
    chess,
    pgn,
    variant,
) -> dict[str, object]:
    parsed = []
    with path.open("r", encoding="utf-8") as handle:
        while True:
            game = pgn.read_game(handle)
            if game is None:
                break
            require(not game.errors, f"PGN parser errors: {game.errors}")
            parsed.append(game)
    require(len(parsed) == len(log_games), "PGN game count drifted")
    expected: dict[tuple[str, str, str, str], int] = {}
    for local_index, logged in enumerate(log_games, start=1):
        opening = openings[opening_start - 1 + (local_index - 1) // 2]
        key = (
            str(opening["canonical_fen"]),
            str(logged["white"]),
            str(logged["black"]),
            str(logged["result"]),
        )
        expected[key] = expected.get(key, 0) + 1
    terminal_reasons: dict[str, int] = {}
    move_count = 0
    for pgn_index, game in enumerate(parsed, start=1):
        headers = game.headers
        require(headers.get("Variant") == "crazyhouse", "PGN variant drifted")
        require(headers.get("SetUp") == "1", "PGN SetUp drifted")
        key = (
            str(headers.get("FEN")),
            str(headers.get("White")),
            str(headers.get("Black")),
            str(headers.get("Result")),
        )
        require(expected.get(key, 0) > 0, f"PGN game {pgn_index} does not match log/opening plan")
        expected[key] -= 1
        require(headers.get("TimeControl") == expected_tc, "PGN time control drifted")
        termination = headers.get("Termination", "")
        require(
            termination.casefold() not in {"adjudication", "time forfeit", "unterminated"},
            f"invalid PGN termination: {termination!r}",
        )
        board = game.board()
        require(isinstance(board, variant.CrazyhouseBoard), "PGN did not instantiate CrazyhouseBoard")
        for move in game.mainline_moves():
            require(move in board.legal_moves, f"illegal PGN move {move.uci()} in game {pgn_index}")
            board.push(move)
            move_count += 1
        require(board.is_game_over(claim_draw=True), f"PGN game {pgn_index} is not terminal")
        require(
            board.result(claim_draw=True) == headers.get("Result"),
            f"reference terminal result drift in game {pgn_index}",
        )
        outcome = board.outcome(claim_draw=True)
        reason = outcome.termination.name if outcome is not None else "UNKNOWN"
        terminal_reasons[reason] = terminal_reasons.get(reason, 0) + 1
    require(not any(expected.values()), "PGN omitted an expected log/opening game")
    return {
        "games": len(parsed),
        "moves": move_count,
        "terminal_reasons": dict(sorted(terminal_reasons.items())),
    }


def expected_command(
    *,
    contract: dict[str, object],
    batch_dir: Path,
    tc: str,
    games: int,
    opening_start: int,
    seed: int,
    event: str,
    debug: bool,
) -> list[str]:
    inputs = contract["inputs"]
    settings = contract["engine_settings"]
    candidate = Path(inputs["candidate"]["path"]).resolve()
    adapter = Path(inputs["adapter"]["path"]).resolve()
    raw = Path(inputs["raw_fairy"]["path"]).resolve()
    network = Path(inputs["network"]["path"]).resolve()
    referee = Path(inputs["referee"]["path"]).resolve()
    book = Path(inputs["book"]["path"]).resolve()

    def engine(role: str) -> list[str]:
        if role == "candidate":
            values = [
                f"cmd={candidate}",
                f"dir={candidate.parent}",
                f"name={CANDIDATE}",
                "proto=uci",
            ]
        else:
            values = [
                f"cmd={adapter}",
                f"dir={adapter.parent}",
                f"name={COMPARATOR}",
                "proto=uci",
                "arg=--engine",
                f"arg={raw}",
                "arg=--network",
                f"arg={network}",
            ]
        values.extend(
            [
                f"tc={tc}",
                f"timemargin={settings['time_margin_ms']}",
                "restart=off",
                f"stderr={batch_dir / (role + '.stderr.log')}",
                "option.UCI_Variant=crazyhouse",
                f"option.CrazyhouseProfile={PROFILE}",
                f"option.CrazyhouseEvalFile={network}",
                f"option.Threads={settings['threads']}",
                f"option.Hash={settings['hash_mib']}",
                "option.MultiPV=1",
                "option.Ponder=false",
                f"option.Move Overhead={settings['move_overhead_ms']}",
                "option.UCI_ShowWDL=false",
                "option.SyzygyProbeLimit=0",
            ]
        )
        return values

    command = [
        str(referee),
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
        *engine("candidate"),
        "-engine",
        *engine("comparator"),
        "-openings",
        f"file={book}",
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


def aggregate_snapshot(games: list[dict[str, object]]) -> dict[str, object]:
    require(len(games) % 2 == 0, "aggregate is not pair-aligned")
    wld = [0, 0, 0]
    for item in games:
        points = item["candidate_points"]
        wld[0 if points == 1.0 else 1 if points == 0.0 else 2] += 1
    penta = [0, 0, 0, 0, 0]
    for index in range(0, len(games), 2):
        penta[int(round(2.0 * (games[index]["candidate_points"] + games[index + 1]["candidate_points"])))] += 1
    projection = [
        [
            item["global_game"],
            item["batch_index"],
            item["local_game"],
            item["opening_index"],
            item["white"],
            item["black"],
            item["result"],
            item["reason"],
        ]
        for item in games
    ]
    return {
        "games": len(games),
        "pairs": len(games) // 2,
        "wld_candidate_pov": wld,
        "pentanomial_candidate_pov": penta,
        "statistics_historical_wld_method": historical_stats(wld),
        "statistics_openbench_pentanomial_method": openbench_stats(penta),
        "defects": [],
        "game_projection_sha256": hashlib.sha256(canonical_bytes(projection)).hexdigest(),
    }


def decision(snapshot: dict[str, object], execution: dict[str, object]) -> dict[str, object]:
    games = int(snapshot["games"])
    display = snapshot["statistics_historical_wld_method"]["display_los_percent"]
    eligible = games >= int(execution["minimum_games_before_los_decision"])
    endpoint = display if eligible and display in {"0.0", "100.0"} else None
    return {
        "eligible": eligible,
        "display_los_percent": display,
        "endpoint": endpoint,
        "continue": endpoint is None and games < int(execution["maximum_games_per_rung"]),
        "rung_pass": endpoint == "100.0",
        "rung_reject": endpoint == "0.0",
        "cap_reached_without_endpoint": endpoint is None
        and games == int(execution["maximum_games_per_rung"]),
    }


def verify_canary(panel_dir: Path, contract: dict[str, object]) -> dict[str, object]:
    canary = load_json(panel_dir / "canary" / "result.json")
    require(canary["schema"] == "crazyhouse-local-strength-canary-result/v2", "canary schema drifted")
    require(canary["result"] == "PASS_PLUMBING_ONLY", "canary did not pass")
    require(canary["score_interpretation"] is False, "canary score was interpreted")
    batch_dir = panel_dir / "canary" / "batch-0000"
    authenticate(batch_dir / "cutechess.log", canary["outputs"]["log"], "canary log")
    authenticate(batch_dir / "games.pgn", canary["outputs"]["pgn"], "canary PGN")
    for stderr_name in ("candidate.stderr.log", "comparator.stderr.log"):
        stderr_path = batch_dir / stderr_name
        authenticate(stderr_path, canary["stderr"][stderr_name], f"canary {stderr_name}")
        require(stderr_path.stat().st_size == 0, f"nonempty canary {stderr_name}")
    text = (batch_dir / "cutechess.log").read_text(encoding="utf-8")
    logged_games = parse_log(batch_dir / "cutechess.log", 2)
    deeply_equal(logged_games, canary["games"], path="canary.games")
    sets = NONCE_SET.findall(text)
    acknowledgements = NONCE_ACK.findall(text)
    checks = {
        "four_unique_nonce_sets": len(sets) == 4 and len(set(sets)) == 4,
        "nonce_acknowledgements_match": sorted(sets) == sorted(acknowledgements),
        "candidate_route": "backend=legacy-v1" in text
        and "evaluator=incremental-scalar" in text,
        "comparator_route": "backend=fairy-external" in text
        and "evaluator=halfkav2variants" in text,
        "network_identity": text.count(
            f"identity={contract['inputs']['network']['sha256']}"
        )
        >= 4,
        "variant_commands": text.count("setoption name UCI_Variant value crazyhouse") >= 4,
        "profile_commands": text.count(f"setoption name CrazyhouseProfile value {PROFILE}") >= 4,
        "no_error": "info string ERROR" not in text,
    }
    require(all(checks.values()), f"independent canary routing checks failed: {checks}")
    return checks


def verify_rung(
    *,
    panel_dir: Path,
    rung: dict[str, object],
    rung_index: int,
    result_pin: dict[str, object],
    contract: dict[str, object],
    openings: list[dict[str, object]],
    chess,
    pgn,
    variant,
) -> tuple[dict[str, object], dict[str, object]]:
    rung_id = str(rung["id"])
    result_path = panel_dir / rung_id / "result.json"
    authenticate(result_path, result_pin, f"{rung_id} result")
    recorded = load_json(result_path)
    require(recorded["schema"] == "crazyhouse-local-strength-rung-result/v2", "rung schema drifted")
    require(recorded["rung_index"] == rung_index, "rung index drifted")
    require(recorded["rung"] == rung, "rung identity drifted")
    aggregate: list[dict[str, object]] = []
    pgn_totals = {"games": 0, "moves": 0, "terminal_reasons": {}}
    previous_endpoint = None
    for batch_index, pin in enumerate(recorded["batch_results"]):
        require(previous_endpoint is None, "runner continued after a LOS endpoint")
        batch_dir = panel_dir / rung_id / f"batch-{batch_index:04d}"
        receipt_path = batch_dir / "result.json"
        authenticate(receipt_path, pin, f"{rung_id} batch {batch_index} receipt")
        receipt = load_json(receipt_path)
        require(receipt["schema"] == "crazyhouse-local-strength-batch-result/v2", "batch schema drifted")
        require(receipt["batch_index"] == batch_index, "batch index drifted")
        expected_games = (
            int(contract["execution"]["initial_batch_games"])
            if batch_index == 0
            else int(contract["execution"]["continuation_batch_games"])
        )
        remaining = int(contract["execution"]["maximum_games_per_rung"]) - len(aggregate)
        expected_games = min(expected_games, remaining)
        require(receipt["games"] == expected_games, "batch game count drifted")
        expected_start = len(aggregate) // 2 + 1
        require(receipt["opening_start"] == expected_start, "opening start drifted")
        require(receipt["opening_end"] == expected_start + expected_games // 2 - 1, "opening end drifted")
        expected_seed = int(rung["srand"]) + batch_index
        require(receipt["seed"] == expected_seed, "batch seed drifted")
        expected = expected_command(
            contract=contract,
            batch_dir=batch_dir.resolve(),
            tc=str(rung["cutechess_tc"]),
            games=expected_games,
            opening_start=expected_start,
            seed=expected_seed,
            event=f"Crazyhouse local LOS gate {rung_id} batch {batch_index}",
            debug=False,
        )
        require(receipt["command"] == expected, "batch command drifted")
        log_path = batch_dir / "cutechess.log"
        pgn_path = batch_dir / "games.pgn"
        authenticate(log_path, receipt["outputs"]["log"], "batch log")
        authenticate(pgn_path, receipt["outputs"]["pgn"], "batch PGN")
        for stderr_name in ("candidate.stderr.log", "comparator.stderr.log"):
            stderr_path = batch_dir / stderr_name
            authenticate(stderr_path, receipt["stderr"][stderr_name], stderr_name)
            require(stderr_path.stat().st_size == 0, f"nonempty {stderr_name}")
        require(receipt["valid"] is True, "batch was not valid")
        require(not receipt["batch_defects"], "batch contains recorded defects")
        local_games = parse_log(log_path, expected_games)
        pgn_summary = verify_pgn(
            path=pgn_path,
            log_games=local_games,
            openings=openings,
            opening_start=expected_start,
            expected_tc=str(rung["cutechess_tc"]),
            chess=chess,
            pgn=pgn,
            variant=variant,
        )
        pgn_totals["games"] += pgn_summary["games"]
        pgn_totals["moves"] += pgn_summary["moves"]
        for reason, count in pgn_summary["terminal_reasons"].items():
            pgn_totals["terminal_reasons"][reason] = (
                pgn_totals["terminal_reasons"].get(reason, 0) + count
            )
        global_start = len(aggregate) + 1
        for offset, item in enumerate(local_games):
            aggregate.append(
                {
                    "global_game": global_start + offset,
                    "batch_index": batch_index,
                    "local_game": item["local_game"],
                    "opening_index": expected_start + offset // 2,
                    "white": item["white"],
                    "black": item["black"],
                    "result": item["result"],
                    "reason": item["reason"],
                    "candidate_points": item["candidate_points"],
                }
            )
        snapshot = aggregate_snapshot(aggregate)
        observed_decision = decision(snapshot, contract["execution"])
        deeply_equal(snapshot, receipt["cumulative"], path="batch.cumulative")
        deeply_equal(observed_decision, receipt["decision"], path="batch.decision")
        previous_endpoint = observed_decision["endpoint"]
    final_snapshot = aggregate_snapshot(aggregate)
    final_decision = decision(final_snapshot, contract["execution"])
    deeply_equal(final_snapshot, recorded["snapshot"], path="rung.snapshot")
    deeply_equal(final_decision, recorded["decision"], path="rung.decision")
    expected_status = (
        "PASS_LOS_100_0"
        if final_decision["rung_pass"]
        else "REJECT_LOS_0_0"
        if final_decision["rung_reject"]
        else "NO_ENDPOINT_AT_FROZEN_CAP"
    )
    require(recorded["result"] == expected_status, "rung classification drifted")
    return recorded, {
        "rung": rung_id,
        "result": expected_status,
        "snapshot": final_snapshot,
        "pgn": pgn_totals,
    }


def verify(args: argparse.Namespace) -> dict[str, object]:
    contract_path = args.contract.resolve(strict=True)
    authorization_path = args.authorization.resolve(strict=True)
    overhead_path = args.overhead_result.resolve(strict=True)
    overhead_verification_path = args.overhead_verification.resolve(strict=True)
    panel_dir = args.panel_dir.resolve(strict=True)
    contract = load_json(contract_path)
    authorization = load_json(authorization_path)
    overhead = load_json(overhead_path)
    overhead_verification = load_json(overhead_verification_path)
    require(contract["schema"] == "crazyhouse-local-strength-panel/v2", "contract schema drifted")
    require(
        authorization["schema"] == "crazyhouse-local-strength-panel-authorization/v1",
        "authorization schema drifted",
    )
    require(overhead["schema"] == "crazyhouse-local-adapter-overhead-result/v1", "overhead schema drifted")
    require(overhead["result"] == "PASS_ADAPTER_OVERHEAD", "overhead did not pass")
    require(
        overhead_verification["schema"]
        == "crazyhouse-local-adapter-overhead-independent-verification/v1",
        "overhead verification schema drifted",
    )
    require(
        overhead_verification["result"]
        == "PASS_ADAPTER_OVERHEAD_INDEPENDENTLY_VERIFIED",
        "overhead was not independently verified",
    )
    require(overhead.get("error") is None, "overhead result contains an error")
    require(overhead.get("time_controls_derived") is False, "overhead result derived a TC")
    authenticate(contract_path, authorization["parent_contract"], "authorized contract")
    authenticate(overhead_path, authorization["adapter_overhead_result"], "authorized overhead")
    authenticate(
        overhead_verification_path,
        authorization["adapter_overhead_verification"],
        "authorized overhead verification",
    )
    require(authorization["authorized_time_controls"] == contract["time_controls"], "TC authorization drifted")
    verifier = Path(__file__).resolve()
    authenticate(verifier, contract["verification"]["implementation"], "verifier implementation")
    authenticate(Path(sys.executable), contract["runtime"]["python"], "verification Python")
    authenticated_inputs = {}
    for role, pin in contract["inputs"].items():
        if "path" in pin and "bytes" in pin and "sha256" in pin:
            authenticated_inputs[role] = authenticate(Path(pin["path"]), pin, role)
    trajectories_path = Path(contract["opening_reference"]["path"])
    authenticate(trajectories_path, contract["opening_reference"], "opening trajectories")
    repository_root = contract_path.parents[2]
    runner_path = repository_root / contract["implementation"]["path"]
    runner_identity = authenticate(
        runner_path, contract["implementation"], "panel runner implementation"
    )
    unit_path = repository_root / contract["verification"]["unit_suite"]["path"]
    unit_identity = authenticate(
        unit_path,
        contract["verification"]["unit_suite"],
        "panel unit suite",
    )
    freshness_path = repository_root / contract["source_boundary"]["freshness_record"]["path"]
    freshness_identity = authenticate(
        freshness_path,
        contract["source_boundary"]["freshness_record"],
        "source freshness record",
    )
    overhead_contract_pin = contract["adapter_overhead_precondition"]["contract"]
    overhead_contract_path = repository_root / overhead_contract_pin["path"]
    overhead_contract_identity = authenticate(
        overhead_contract_path,
        overhead_contract_pin,
        "adapter overhead contract",
    )
    overhead_contract = load_json(overhead_contract_path)
    require(
        overhead_verification["contract"]["bytes"]
        == overhead_contract_identity["bytes"]
        and overhead_verification["contract"]["sha256"]
        == overhead_contract_identity["sha256"],
        "overhead verification contract identity drifted",
    )
    require(
        overhead_verification["formal_result"]["bytes"] == overhead_path.stat().st_size
        and overhead_verification["formal_result"]["sha256"]
        == sha256_file(overhead_path),
        "overhead verification formal-result identity drifted",
    )
    require(
        overhead_verification.get("recomputed") == overhead.get("adapter_overhead"),
        "overhead independent recomputation drifted",
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
        "overhead host validation drifted",
    )
    require(
        overhead["contract"]["bytes"] == overhead_contract_identity["bytes"]
        and overhead["contract"]["sha256"] == overhead_contract_identity["sha256"],
        "overhead result contract identity drifted",
    )
    require(overhead["adapter_overhead"]["pass"] is True, "internal overhead decision failed")
    require(
        overhead["sample"]["rows"] == overhead_contract["sample"]["rows"]
        and overhead["sample"]["selection_sha256"]
        == overhead_contract["sample"]["selection_sha256"],
        "overhead sample drifted",
    )
    require(
        len(overhead["blocks"]["adapter_overhead"])
        == len(overhead_contract["adapter_overhead"]["block_order"]),
        "overhead block count drifted",
    )
    for role in ("candidate", "adapter", "raw_fairy", "network"):
        require(
            overhead["inputs"][role]["bytes"] == contract["inputs"][role]["bytes"]
            and overhead["inputs"][role]["sha256"]
            == contract["inputs"][role]["sha256"],
            f"overhead {role} identity drifted",
        )
    require(
        overhead["inputs"]["corpus"]["bytes"] == contract["opening_reference"]["bytes"]
        and overhead["inputs"]["corpus"]["sha256"]
        == contract["opening_reference"]["sha256"],
        "overhead corpus identity drifted",
    )
    manifest_path = Path(contract["opening_reference"]["manifest"]["path"])
    if not manifest_path.is_absolute():
        manifest_path = repository_root / manifest_path
    authenticate(
        manifest_path,
        contract["opening_reference"]["manifest"],
        "opening corpus manifest",
    )
    method_identities = {}
    for name, pin in contract["method_references"].items():
        if isinstance(pin, dict) and {"path", "bytes", "sha256"} <= set(pin):
            method_identities[name] = authenticate(Path(pin["path"]), pin, name)
    openings = load_opening_reference(
        trajectories_path, int(contract["opening_reference"]["rows"])
    )
    chess, pgn, variant = import_reference(args.reference_root, contract)
    require(not (panel_dir / "INVALID.json").exists(), "panel contains INVALID.json")
    completion = load_json(panel_dir / "completion.json")
    manifest = load_json(panel_dir / "manifest.json")
    panel = load_json(panel_dir / "panel-result.json")
    require(completion["schema"] == "crazyhouse-local-strength-panel-completion/v2", "completion schema drifted")
    require(manifest["schema"] == "crazyhouse-local-strength-panel-runtime/v2", "manifest schema drifted")
    require(panel["schema"] == "crazyhouse-local-strength-panel-result/v2", "panel schema drifted")
    authenticate(panel_dir / "manifest.json", completion["manifest"], "runtime manifest")
    authenticate(panel_dir / "canary" / "result.json", completion["canary_result"], "canary result")
    authenticate(panel_dir / "panel-result.json", completion["panel_result"], "panel result")
    require(completion["result"] == panel["result"], "completion/panel result drifted")
    require(panel["openbench_authorized"] is False, "runner prematurely authorized OpenBench")
    require(panel["independent_verification_pending"] is True, "runner bypassed verification")
    require(manifest["time_controls"] == contract["time_controls"], "manifest TC ladder drifted")
    require(manifest["execution"] == contract["execution"], "manifest execution drifted")
    require(manifest["stopping_rule"] == contract["stopping_rule"], "manifest stopping rule drifted")
    deeply_equal(manifest["identities"]["contract"], identity(contract_path), path="manifest.contract")
    deeply_equal(
        manifest["identities"]["authorization"],
        identity(authorization_path),
        path="manifest.authorization",
    )
    deeply_equal(
        manifest["identities"]["adapter_overhead_result"],
        identity(overhead_path),
        path="manifest.adapter_overhead_result",
    )
    deeply_equal(
        manifest["identities"]["adapter_overhead_verification"],
        identity(overhead_verification_path),
        path="manifest.adapter_overhead_verification",
    )
    deeply_equal(
        manifest["identities"]["adapter_overhead_host_attestation"],
        timing_host_identity,
        path="manifest.adapter_overhead_host_attestation",
    )
    deeply_equal(
        manifest["identities"]["runner"],
        runner_identity,
        path="manifest.runner",
    )
    for role, observed in authenticated_inputs.items():
        deeply_equal(
            manifest["identities"]["assets"][role],
            observed,
            path=f"manifest.assets.{role}",
        )
    host_path = Path(manifest["host_attestation"]["path"])
    authenticate(host_path, manifest["host_attestation"], "panel host attestation")
    host = load_json(host_path)
    host_validation = validate_host_attestation(
        host,
        contract,
        repository_root,
        expected_schema="crazyhouse-host-strength-attestation/v1",
        expected_result="PASS_HOST_STRENGTH_READY",
        effective_time=parse_utc(manifest["started_utc"], "manifest started_utc"),
    )
    deeply_equal(
        manifest["host_attestation_validation"],
        host_validation,
        path="manifest.host_attestation_validation",
    )
    expected_canary_command = expected_command(
        contract=contract,
        batch_dir=(panel_dir / "canary" / "batch-0000").resolve(),
        tc=str(contract["time_controls"][0]["cutechess_tc"]),
        games=2,
        opening_start=1,
        seed=int(contract["canary"]["srand"]),
        event="Crazyhouse local LOS gate plumbing canary",
        debug=True,
    )
    require(manifest["canary_command"] == expected_canary_command, "canary command drifted")
    canary_checks = verify_canary(panel_dir, contract)
    verified_rungs = []
    recorded_rungs = []
    for index, pin in enumerate(panel["rung_results"]):
        require(index < len(contract["time_controls"]), "extra rung result")
        recorded, summary = verify_rung(
            panel_dir=panel_dir,
            rung=contract["time_controls"][index],
            rung_index=index,
            result_pin=pin,
            contract=contract,
            openings=openings,
            chess=chess,
            pgn=pgn,
            variant=variant,
        )
        recorded_rungs.append(recorded)
        verified_rungs.append(summary)
        if recorded["result"] != "PASS_LOS_100_0":
            require(index == len(panel["rung_results"]) - 1, "ladder continued after non-pass")
    all_completed = len(verified_rungs) == len(contract["time_controls"])
    all_pass = all(item["result"] == "PASS_LOS_100_0" for item in verified_rungs)
    if all_completed and all_pass:
        expected_classification = "PASS_LOCAL_SAME_NETWORK_FSF_GATE"
    elif verified_rungs and verified_rungs[-1]["result"] == "REJECT_LOS_0_0":
        expected_classification = "VALID_LOCAL_GATE_DID_NOT_BEAT_FSF"
    else:
        expected_classification = "VALID_LOCAL_GATE_INCONCLUSIVE_AT_CAP"
    require(panel["result"] == expected_classification, "panel classification drifted")
    require(panel["infrastructure_valid"] is True, "panel infrastructure was not valid")
    require(panel["all_rungs_completed"] == all_completed, "all-rungs flag drifted")
    require(panel["strength_gate_pass"] == (expected_classification == "PASS_LOCAL_SAME_NETWORK_FSF_GATE"), "strength flag drifted")
    return {
        "schema": "crazyhouse-local-strength-panel-independent-verification/v1",
        "created_utc": utc_now(),
        "evidence_class": "S3_STRENGTH",
        "result": (
            "VERIFIED_PASS_LOCAL_SAME_NETWORK_FSF_GATE"
            if expected_classification == "PASS_LOCAL_SAME_NETWORK_FSF_GATE"
            else "VERIFIED_VALID_LOCAL_NONPASS"
        ),
        "panel_classification": expected_classification,
        "strength_gate_pass": expected_classification == "PASS_LOCAL_SAME_NETWORK_FSF_GATE",
        "openbench_local_prerequisite_pass": expected_classification
        == "PASS_LOCAL_SAME_NETWORK_FSF_GATE",
        "inputs": authenticated_inputs,
        "method_references": method_identities,
        "source_freshness": freshness_identity,
        "unit_suite": unit_identity,
        "contract": identity(contract_path),
        "authorization": identity(authorization_path),
        "adapter_overhead_result": identity(overhead_path),
        "adapter_overhead_verification": identity(overhead_verification_path),
        "adapter_overhead_host_attestation": timing_host_identity,
        "adapter_overhead_contract": overhead_contract_identity,
        "panel_completion": identity(panel_dir / "completion.json"),
        "canary_checks": canary_checks,
        "rungs": verified_rungs,
        "scientific_boundary": {
            "same_network_local_strength_only": True,
            "openbench_result": False,
            "release_claim": False,
            "monitoring_claim": False,
        },
    }


def main(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    try:
        result = verify(args)
        write_json_fresh(output, result)
        print(result["result"])
        return 0
    except BaseException as exc:
        invalid = {
            "schema": "crazyhouse-local-strength-panel-independent-verification-invalid/v1",
            "created_utc": utc_now(),
            "result": "INVALID_VERIFICATION",
            "error": f"{type(exc).__name__}: {exc}",
            "strength_claim": False,
            "openbench_local_prerequisite_pass": False,
        }
        write_json_fresh(output, invalid)
        print(invalid["error"], file=sys.stderr)
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--overhead-result", type=Path, required=True)
    parser.add_argument("--overhead-verification", type=Path, required=True)
    parser.add_argument("--panel-dir", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
