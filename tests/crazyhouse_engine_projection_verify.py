#!/usr/bin/env python3
"""Verify the frozen Crazyhouse corpus through direct and production-UCI lanes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from crazyhouse_uci_routing_verify import (
    PROFILE_ID,
    PROFILE_SHA256,
    PROFILE_TOKEN,
    UciProcess,
    VerificationFailure,
    setoption,
    wait_command_error,
    wait_ready_success,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "tests" / "crazyhouse" / "reference-cases.json"
DEFAULT_APPLICABILITY = (
    ROOT / "tests" / "crazyhouse" / "g4-engine-projection-applicability-v1.json"
)
DEFAULT_CAPACITY = ROOT / "tests" / "crazyhouse" / "max-moves-303.json"
CASES_SHA256 = "4a00bca20d3b149b5bbe3f4153a4a3ff5a20473126763c2d8125a4ba2d11742e"
APPLICABILITY_SHA256 = "bc6cc255beada0adb7a8139441debfbb8d4d5d4e9d93c4c8cda2dbb395260c7c"
CAPACITY_SHA256 = "3c77de3377d66feecdffd37459e31a1824424b3833a39e2272b4202d1c312e38"
DIRECT_SCHEMA = "crazyhouse-engine-direct-projection/v1"
RESULT_SCHEMA = "crazyhouse-engine-projection-result/v1"

FEN_RE = re.compile(r"^Fen: (.+)$")
CHECKERS_RE = re.compile(r"^Checkers:[ \t]*([^\r\n]*)$")
ROOT_MOVE_RE = re.compile(
    r"^((?:(?:[a-h][1-8]){2}[a-z]?|[PNBRQ]@[a-h][1-8])):\s+(\d+)\s*$"
)
NODE_COUNT_RE = re.compile(r"^Nodes searched:\s+(\d+)\s*$")
SEARCH_FORMS = (
    "go depth 1",
    "go nodes 1",
    "go movetime 1",
    "go infinite",
    "go ponder wtime 1000 btime 1000",
)


class ProjectionFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProjectionFailure(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_jsonl(values: list[dict[str, Any]]) -> bytes:
    return ("".join(canonical_json(value) + "\n" for value in values)).encode("ascii")


def assert_subset(actual: Any, expected: Any, context: str) -> None:
    if isinstance(expected, dict):
        require(isinstance(actual, dict), f"{context}: expected object")
        for key, value in expected.items():
            require(key in actual, f"{context}: missing key {key!r}")
            assert_subset(actual[key], value, f"{context}.{key}")
    else:
        require(actual == expected, f"{context}: expected {expected!r}, got {actual!r}")


def selected_expected(case: dict[str, Any]) -> dict[str, Any]:
    expected = dict(case.get("expected", {}))
    for key, value in expected.get("state_by_implementation", {}).get("scalachess", {}).items():
        expected[key] = value
    terminal = expected.get("terminal_by_implementation", {}).get("scalachess")
    if terminal is not None:
        expected["terminal"] = terminal
    return expected


def validate_legal(case: dict[str, Any], legal_moves: Any, context: str) -> None:
    require(isinstance(legal_moves, list), f"{context}: legal_moves is not an array")
    require(
        legal_moves == sorted(set(legal_moves)),
        f"{context}: legal_moves is not a sorted unique set",
    )
    expected = selected_expected(case)
    if "legal_moves_exact" in expected:
        require(
            legal_moves == expected["legal_moves_exact"],
            f"{context}: exact legal move set differs",
        )
    missing = sorted(set(expected.get("legal_must_include", ())) - set(legal_moves))
    forbidden = sorted(set(expected.get("legal_must_exclude", ())) & set(legal_moves))
    require(not missing and not forbidden, f"{context}: missing={missing} forbidden={forbidden}")
    for prefix in expected.get("legal_forbidden_prefixes", ()):
        present = [move for move in legal_moves if move.startswith(prefix)]
        require(not present, f"{context}: forbidden prefix {prefix!r}: {present}")


def validate_direct_case(
    case: dict[str, Any], record: dict[str, Any], history_cases: dict[str, Any]
) -> None:
    context = f"direct:{case['id']}"
    require(record.get("schema") == DIRECT_SCHEMA, f"{context}: schema drifted")
    require(record.get("ok") is True, f"{context}: adapter failure {record!r}")
    require(record.get("profile_id") == PROFILE_ID, f"{context}: profile ID drifted")
    require(
        record.get("profile_sha256") == PROFILE_SHA256,
        f"{context}: profile hash drifted",
    )
    state = record.get("state")
    require(isinstance(state, dict), f"{context}: state missing")
    expected = selected_expected(case)
    direct_fields = {
        "canonical_fen",
        "turn",
        "castling_rights",
        "ep_square",
        "halfmove_clock",
        "fullmove_number",
        "pockets",
        "promoted_squares",
        "in_check",
        "terminal",
    }
    for key in sorted(direct_fields & expected.keys()):
        assert_subset(state.get(key), expected[key], f"{context}.state.{key}")
    validate_legal(case, state.get("legal_moves"), context)
    require(
        record.get("legal_move_count") == len(state["legal_moves"]),
        f"{context}: legal move count drifted",
    )
    if case["op"] == "perft":
        require(
            record.get("perft_nodes") == expected.get("nodes"),
            f"{context}: perft expected {expected.get('nodes')}, got {record.get('perft_nodes')}",
        )
    else:
        require(record.get("perft_nodes") is None, f"{context}: unexpected perft result")
    history = history_cases.get(case["id"], {}).get("direct")
    if history is not None:
        assert_subset(record.get("direct"), history, f"{context}.direct")


def validate_uci_case(case: dict[str, Any], record: dict[str, Any]) -> None:
    context = f"uci:{case['id']}"
    require(record.get("id") == case["id"], f"{context}: ID drifted")
    state = record.get("state")
    require(isinstance(state, dict), f"{context}: state missing")
    expected = selected_expected(case)
    for key in ("canonical_fen", "in_check"):
        if key in expected:
            assert_subset(state.get(key), expected[key], f"{context}.state.{key}")
    validate_legal(case, state.get("legal_moves"), context)
    if case["op"] == "perft":
        require(
            record.get("perft_nodes") == expected.get("nodes"),
            f"{context}: perft expected {expected.get('nodes')}, got {record.get('perft_nodes')}",
        )
    else:
        require(record.get("perft_nodes") is None, f"{context}: unexpected perft result")


def validate_shared(case: dict[str, Any], direct: dict[str, Any], uci: dict[str, Any]) -> None:
    context = f"shared:{case['id']}"
    direct_state = direct["state"]
    uci_state = uci["state"]
    for field in ("canonical_fen", "in_check", "legal_moves"):
        require(
            direct_state[field] == uci_state[field],
            f"{context}: {field} differs between participants",
        )
    require(
        direct.get("perft_nodes") == uci.get("perft_nodes"),
        f"{context}: perft nodes differ between participants",
    )


def prepare_output(path: Path) -> Path:
    resolved = path.resolve()
    require(not resolved.exists(), f"output directory already exists: {resolved}")
    require(resolved.parent.is_dir(), f"output parent is missing: {resolved.parent}")
    resolved.mkdir()
    return resolved


def direct_input(cases: list[dict[str, Any]], capacity_fen: str) -> bytes:
    lines: list[str] = []
    for case in cases:
        fields = (
            "CASE",
            str(case["id"]),
            str(case["op"]),
            str(case.get("depth", 0)),
            str(case["fen"]),
            " ".join(str(move) for move in case.get("moves", ())),
        )
        require(all("\t" not in field and "\r" not in field and "\n" not in field for field in fields),
                f"{case['id']}: direct protocol delimiter in input")
        lines.append("\t".join(fields))
    lines.append("\t".join(("CASE", "CH-G4-CAPACITY-303", "capacity", "0", capacity_fen, "")))
    lines.append("QUIT")
    return ("\n".join(lines) + "\n").encode("utf-8")


def run_direct(
    executable: Path,
    expected_sha256: str,
    cases: list[dict[str, Any]],
    capacity_fen: str,
    output: Path,
    run_name: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any], bytes]:
    require(sha256_file(executable) == expected_sha256, "direct executable identity drifted")
    request = direct_input(cases, capacity_fen)
    (output / f"{run_name}-direct.input.tsv").write_bytes(request)
    try:
        completed = subprocess.run(
            [str(executable)],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectionFailure(f"{run_name}: direct participant timed out") from exc
    (output / f"{run_name}-direct.stdout.jsonl").write_bytes(completed.stdout)
    (output / f"{run_name}-direct.stderr.log").write_bytes(completed.stderr)
    require(completed.returncode == 0, f"{run_name}: direct exit {completed.returncode}")
    require(not completed.stderr, f"{run_name}: direct participant emitted stderr")
    values: list[dict[str, Any]] = []
    for number, raw in enumerate(completed.stdout.decode("utf-8", errors="strict").splitlines(), 1):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectionFailure(f"{run_name}: direct JSON line {number}: {exc}") from exc
        require(isinstance(value, dict), f"{run_name}: direct line {number} is not an object")
        values.append(value)
    require(len(values) == len(cases) + 2, f"{run_name}: direct record count drifted")
    capabilities = values[0]
    require(capabilities.get("kind") == "capabilities", f"{run_name}: capabilities missing")
    require(capabilities.get("schema") == DIRECT_SCHEMA, f"{run_name}: direct schema drifted")
    require(capabilities.get("profile_id") == PROFILE_ID, f"{run_name}: direct profile ID drifted")
    require(
        capabilities.get("profile_sha256") == PROFILE_SHA256,
        f"{run_name}: direct profile hash drifted",
    )
    records = values[1:-1]
    capacity = values[-1]
    expected_ids = [case["id"] for case in cases]
    require([record.get("id") for record in records] == expected_ids, f"{run_name}: direct IDs drifted")
    require(capacity.get("id") == "CH-G4-CAPACITY-303", f"{run_name}: capacity ID missing")
    normalized = canonical_jsonl(values)
    (output / f"{run_name}-direct.normalized.jsonl").write_bytes(normalized)
    return records, capacity, normalized


def position_command(case: dict[str, Any]) -> str:
    command = f"position fen {case['fen']}"
    moves = [str(move) for move in case.get("moves", ())]
    if moves:
        command += " moves " + " ".join(moves)
    return command


def drain_blank(proc: UciProcess, context: str) -> None:
    trailing = proc.drain(0.04)
    require(all(not line for line in trailing), f"{context}: unexpected trailing output {trailing!r}")


def observe_display(proc: UciProcess, context: str) -> tuple[str, bool, list[str]]:
    proc.send("d")
    lines = proc.wait_for(lambda line: line.startswith("Checkers:"), f"{context} display", 20)
    require(not any("info string ERROR" in line for line in lines), f"{context}: display saw error")
    require(not any("WARNING" in line for line in lines), f"{context}: display saw warning")
    fens = [match.group(1) for line in lines if (match := FEN_RE.fullmatch(line))]
    checkers = [match.group(1) for line in lines if (match := CHECKERS_RE.fullmatch(line))]
    require(len(fens) == 1, f"{context}: expected one canonical FEN, got {fens!r}")
    require(len(checkers) == 1, f"{context}: expected one Checkers line")
    drain_blank(proc, context)
    return fens[0], bool(checkers[0].strip()), lines


def observe_perft(
    proc: UciProcess, depth: int, context: str, expect_leaf_counts: bool = False
) -> tuple[list[str], int, list[str]]:
    proc.send(f"go perft {depth}")
    lines = proc.wait_for(
        lambda line: NODE_COUNT_RE.fullmatch(line) is not None,
        f"{context} perft {depth}",
        120,
    )
    require(not any("info string ERROR" in line for line in lines), f"{context}: perft saw error")
    require(not any("WARNING" in line for line in lines), f"{context}: perft saw warning")
    require(not any(line.startswith("bestmove ") for line in lines), f"{context}: perft searched")
    roots = [(match.group(1), int(match.group(2))) for line in lines if (match := ROOT_MOVE_RE.fullmatch(line))]
    totals = [int(match.group(1)) for line in lines if (match := NODE_COUNT_RE.fullmatch(line))]
    require(len(totals) == 1, f"{context}: perft total count drifted")
    raw_moves = [move for move, _ in roots]
    require(len(raw_moves) == len(set(raw_moves)), f"{context}: root moves contain duplicates")
    moves = sorted(raw_moves)
    require(sum(count for _, count in roots) == totals[0], f"{context}: root subtotal mismatch")
    if expect_leaf_counts:
        require(all(count == 1 for _, count in roots), f"{context}: perft-1 subtotal drifted")
        require(len(moves) == totals[0], f"{context}: legal root count mismatch")
    drain_blank(proc, context)
    return moves, totals[0], lines


def verify_capacity(record: dict[str, Any], capacity: dict[str, Any], context: str) -> None:
    legal = record.get("state", {}).get("legal_moves")
    require(isinstance(legal, list), f"{context}: capacity legal set missing")
    expected = sorted(capacity["drop_moves"] + capacity["non_drop_moves"])
    require(legal == expected, f"{context}: capacity legal set differs")
    require(record.get("legal_move_count") == 303, f"{context}: capacity count is not 303")
    require(record.get("drop_move_count") == 295, f"{context}: capacity drops are not 295")
    require(record.get("non_drop_move_count") == 8, f"{context}: capacity non-drops are not 8")
    digest = sha256_bytes(("\n".join(legal) + "\n").encode("ascii"))
    require(digest == capacity["sorted_uci_lf_sha256"], f"{context}: capacity digest drifted")


def run_uci(
    executable: Path,
    expected_sha256: str,
    legacy_network: Path,
    cases: list[dict[str, Any]],
    capacity: dict[str, Any],
    output: Path,
    run_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], bytes]:
    require(sha256_file(executable) == expected_sha256, "UCI executable identity drifted")
    proc = UciProcess(executable)
    records: list[dict[str, Any]] = []
    refusal_records: list[dict[str, Any]] = []
    capacity_record: dict[str, Any] | None = None
    close_error: Exception | None = None
    try:
        proc.send("uci")
        handshake = proc.wait_for(lambda line: line == "uciok", f"{run_name} uciok", 20)
        option_lines = [line for line in handshake if line.startswith("option name ")]
        require(
            any(
                line
                == f"option name CrazyhouseProfile type string default {PROFILE_TOKEN}"
                for line in option_lines
            ),
            f"{run_name}: exact CrazyhouseProfile option missing",
        )
        require(
            any(line.startswith("option name UCI_Variant type combo") and "var crazyhouse" in line for line in option_lines),
            f"{run_name}: UCI_Variant does not advertise crazyhouse",
        )
        setoption(proc, "UCI_Variant", "crazyhouse")
        setoption(proc, "CrazyhouseProfile", PROFILE_TOKEN)
        setoption(proc, "CrazyhouseEvalFile", str(legacy_network))
        route_lines = wait_ready_success(proc, "crazyhouse")
        route_commits = [line for line in route_lines if "info string route_commit status=ok" in line]
        require(len(route_commits) == 1, f"{run_name}: route acknowledgement count drifted")
        drain_blank(proc, f"{run_name}:route")

        for case in cases:
            context = f"{run_name}:{case['id']}"
            proc.send(position_command(case))
            fen, in_check, display_lines = observe_display(proc, context)
            legal_moves, legal_nodes, perft_one_lines = observe_perft(
                proc, 1, context, expect_leaf_counts=True
            )
            perft_nodes: int | None = None
            perft_lines: list[str] = []
            if case["op"] == "perft":
                _, perft_nodes, perft_lines = observe_perft(
                    proc, int(case["depth"]), context
                )
            records.append(
                {
                    "id": case["id"],
                    "op": case["op"],
                    "state": {
                        "canonical_fen": fen,
                        "in_check": in_check,
                        "legal_moves": legal_moves,
                    },
                    "legal_move_count": legal_nodes,
                    "perft_nodes": perft_nodes,
                    "display_sha256": sha256_bytes(("\n".join(display_lines) + "\n").encode("utf-8")),
                    "perft_1_sha256": sha256_bytes(("\n".join(perft_one_lines) + "\n").encode("utf-8")),
                    "perft_sha256": (
                        sha256_bytes(("\n".join(perft_lines) + "\n").encode("utf-8"))
                        if perft_lines
                        else None
                    ),
                }
            )

        capacity_case = {
            "id": "CH-G4-CAPACITY-303",
            "op": "capacity",
            "fen": capacity["fen"],
        }
        proc.send(position_command(capacity_case))
        capacity_fen, capacity_check, capacity_display = observe_display(
            proc, f"{run_name}:capacity"
        )
        capacity_moves, capacity_nodes, capacity_perft = observe_perft(
            proc, 1, f"{run_name}:capacity", expect_leaf_counts=True
        )
        capacity_record = {
            "id": "CH-G4-CAPACITY-303",
            "op": "capacity",
            "state": {
                "canonical_fen": capacity_fen,
                "in_check": capacity_check,
                "legal_moves": capacity_moves,
            },
            "legal_move_count": capacity_nodes,
            "drop_move_count": sum("@" in move for move in capacity_moves),
            "non_drop_move_count": sum("@" not in move for move in capacity_moves),
            "perft_nodes": None,
            "display_sha256": sha256_bytes(("\n".join(capacity_display) + "\n").encode("utf-8")),
            "perft_1_sha256": sha256_bytes(("\n".join(capacity_perft) + "\n").encode("utf-8")),
        }

        proc.send("position startpos")
        baseline_moves, baseline_nodes, _ = observe_perft(
            proc, 1, f"{run_name}:refusal-baseline", expect_leaf_counts=True
        )
        require(baseline_nodes == 20, f"{run_name}: refusal baseline is not startpos")
        for command in SEARCH_FORMS:
            lines = wait_command_error(proc, command, "crazyhouse_search_not_bound")
            require(
                not any(
                    line.startswith("info ")
                    and not line.startswith("info string ")
                    for line in lines
                ),
                f"{run_name}:{command}: search info escaped refusal",
            )
            after_moves, after_nodes, after_lines = observe_perft(
                proc, 1, f"{run_name}:{command}:post-refusal", expect_leaf_counts=True
            )
            require(
                (after_moves, after_nodes) == (baseline_moves, baseline_nodes),
                f"{run_name}:{command}: refusal changed the position",
            )
            refusal_records.append(
                {
                    "command": command,
                    "error_code": "crazyhouse_search_not_bound",
                    "bestmove_seen": any(line.startswith("bestmove ") for line in lines),
                    "search_info_seen": any(
                        line.startswith("info ")
                        and not line.startswith("info string ")
                        for line in lines
                    ),
                    "post_refusal_perft_nodes": after_nodes,
                    "post_refusal_perft_sha256": sha256_bytes(
                        ("\n".join(after_lines) + "\n").encode("utf-8")
                    ),
                }
            )
    except VerificationFailure as exc:
        raise ProjectionFailure(f"{run_name}: {exc}") from exc
    finally:
        try:
            proc.close()
        except Exception as exc:  # retain streams before surfacing close failures
            close_error = exc
        (output / f"{run_name}-uci.stdout.log").write_text(
            "\n".join(proc.stdout_all) + ("\n" if proc.stdout_all else ""),
            encoding="utf-8",
            newline="\n",
        )
        (output / f"{run_name}-uci.stderr.log").write_text(
            "\n".join(proc.stderr_all) + ("\n" if proc.stderr_all else ""),
            encoding="utf-8",
            newline="\n",
        )
    if close_error is not None:
        raise ProjectionFailure(f"{run_name}: UCI close failed: {close_error}")
    require(capacity_record is not None, f"{run_name}: capacity record missing")
    normalized_values = [
        {
            "schema": "crazyhouse-engine-uci-projection/v1",
            "kind": "identity",
            "engine_sha256": expected_sha256,
            "profile_id": PROFILE_ID,
            "profile_sha256": PROFILE_SHA256,
        },
        *records,
        capacity_record,
        {"kind": "search_refusals", "records": refusal_records},
    ]
    normalized = canonical_jsonl(normalized_values)
    (output / f"{run_name}-uci.normalized.jsonl").write_bytes(normalized)
    return records, capacity_record, refusal_records, normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--legacy-network", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--applicability", type=Path, default=DEFAULT_APPLICABILITY)
    parser.add_argument("--capacity", type=Path, default=DEFAULT_CAPACITY)
    parser.add_argument("--expected-direct-sha256", required=True)
    parser.add_argument("--expected-engine-sha256", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--direct-timeout", type=float, default=240.0)
    args = parser.parse_args()

    try:
        direct = args.direct.resolve(strict=True)
        engine = args.engine.resolve(strict=True)
        legacy = args.legacy_network.resolve(strict=True)
        cases_path = args.cases.resolve(strict=True)
        applicability_path = args.applicability.resolve(strict=True)
        capacity_path = args.capacity.resolve(strict=True)
        require(args.expected_direct_sha256 == args.expected_direct_sha256.lower(),
                "expected direct hash is not lowercase")
        require(args.expected_engine_sha256 == args.expected_engine_sha256.lower(),
                "expected engine hash is not lowercase")
        require(sha256_file(cases_path) == CASES_SHA256, "corpus identity drifted")
        require(
            sha256_file(applicability_path) == APPLICABILITY_SHA256,
            "applicability identity drifted",
        )
        require(sha256_file(capacity_path) == CAPACITY_SHA256, "capacity identity drifted")
        require(sha256_file(legacy) == "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43",
                "legacy network identity drifted")
        require(sha256_file(direct) == args.expected_direct_sha256, "direct identity mismatch")
        require(sha256_file(engine) == args.expected_engine_sha256, "engine identity mismatch")

        corpus = json.loads(cases_path.read_text(encoding="utf-8"))
        applicability = json.loads(applicability_path.read_text(encoding="utf-8"))
        capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
        cases = corpus["cases"]
        ids = [case["id"] for case in cases]
        require(corpus["authority_profile"] == PROFILE_ID, "corpus profile drifted")
        require(applicability["profile_id"] == PROFILE_ID, "applicability profile drifted")
        require(applicability["profile_sha256"] == PROFILE_SHA256, "profile hash drifted")
        require(applicability["profile_token"] == PROFILE_TOKEN, "profile token drifted")
        require(len(cases) == 48 and len(set(ids)) == 48, "corpus IDs are not exactly 48 unique cases")
        require(applicability["case_coverage"] == ids, "static applicability order drifted")
        require(
            applicability["participants"]["official_base_engine_direct"]["all_48_cases_required"]
            and applicability["participants"]["official_base_engine_uci"]["all_48_cases_required"],
            "participant coverage contract drifted",
        )
        require(capacity["legal_move_count"] == 303, "capacity count contract drifted")

        output = prepare_output(args.out_dir)
        direct_runs = []
        uci_runs = []
        for index in (1, 2):
            run_name = f"run-{index}"
            d_records, d_capacity, d_normalized = run_direct(
                direct,
                args.expected_direct_sha256,
                cases,
                capacity["fen"],
                output,
                run_name,
                args.direct_timeout,
            )
            u_records, u_capacity, refusals, u_normalized = run_uci(
                engine,
                args.expected_engine_sha256,
                legacy,
                cases,
                capacity,
                output,
                run_name,
            )
            require(len(refusals) == len(SEARCH_FORMS), f"{run_name}: refusal count drifted")
            for case, direct_record, uci_record in zip(cases, d_records, u_records, strict=True):
                validate_direct_case(
                    case,
                    direct_record,
                    applicability["history_result_cases"],
                )
                validate_uci_case(case, uci_record)
                validate_shared(case, direct_record, uci_record)
            verify_capacity(d_capacity, capacity, f"{run_name}:direct")
            verify_capacity(u_capacity, capacity, f"{run_name}:uci")
            require(
                d_capacity["state"]["legal_moves"] == u_capacity["state"]["legal_moves"],
                f"{run_name}: capacity participants differ",
            )
            direct_runs.append((d_records, d_capacity, d_normalized))
            uci_runs.append((u_records, u_capacity, refusals, u_normalized))

        require(
            direct_runs[0][2] == direct_runs[1][2],
            "direct normalized runs are not byte-identical",
        )
        require(
            uci_runs[0][3] == uci_runs[1][3],
            "UCI normalized runs are not byte-identical",
        )
        manifest = {
            "schema": RESULT_SCHEMA,
            "profile_id": PROFILE_ID,
            "profile_sha256": PROFILE_SHA256,
            "corpus": {"cases": 48, "sha256": CASES_SHA256},
            "applicability_sha256": APPLICABILITY_SHA256,
            "capacity_sha256": CAPACITY_SHA256,
            "direct_sha256": args.expected_direct_sha256,
            "engine_sha256": args.expected_engine_sha256,
            "legacy_network_sha256": sha256_file(legacy),
            "direct_runs": 2,
            "uci_runs": 2,
            "search_refusal_forms_per_run": len(SEARCH_FORMS),
            "direct_normalized_sha256": sha256_bytes(direct_runs[0][2]),
            "uci_normalized_sha256": sha256_bytes(uci_runs[0][3]),
            "capacity": {
                "legal_moves": 303,
                "drops": 295,
                "non_drops": 8,
                "sorted_uci_lf_sha256": capacity["sorted_uci_lf_sha256"],
            },
            "shared_fields": ["canonical_fen", "in_check", "legal_moves", "perft_nodes"],
            "direct_only_fields": [
                "turn",
                "castling_rights",
                "ep_square",
                "halfmove_clock",
                "fullmove_number",
                "pockets",
                "promoted_squares",
                "repetition_occurrences",
                "is_draw_at_ply_1",
                "automatic_terminal_reason",
                "automatic_terminal_winner",
                "automatic_terminal_result",
            ],
            "crazyhouse_search": "DISABLED",
            "result": "PASS_DIRECT_AND_PRODUCTION_UCI_PROJECTION",
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("ascii")
        (output / "manifest.json").write_bytes(manifest_bytes)
        print(
            "PASS crazyhouse_engine_projection "
            f"cases=48 direct_runs=2 uci_runs=2 capacity=303/295/8 "
            f"search_refusals={len(SEARCH_FORMS) * 2} "
            f"direct_sha256={manifest['direct_normalized_sha256']} "
            f"uci_sha256={manifest['uci_normalized_sha256']} "
            "crazyhouse_search=DISABLED"
        )
        return 0
    except (OSError, KeyError, TypeError, ValueError, ProjectionFailure) as exc:
        print(f"FAIL crazyhouse_engine_projection: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
