#!/usr/bin/env python3
"""Portable routing replay for the official-Stockfish-derived Crazyhouse line."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import crazyhouse_uci_routing_verify as routing


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authenticate_pin(repository: Path, name: str, pin: dict) -> Path:
    path = (repository / pin["path"]).resolve()
    require(path.is_relative_to(repository), f"pin escapes repository: {name}")
    require(path.is_file(), f"pin missing: {name}")
    require(path.stat().st_size == pin["bytes"], f"pin size mismatch: {name}")
    require(sha256_file(path) == pin["sha256"], f"pin SHA-256 mismatch: {name}")
    return path


def crazyhouse_rule_route_scenario(
    engine: Path, legacy: Path, search_context: dict
) -> list[str]:
    """Replay the quick route boundary; production Crazyhouse bench has its own P10 gate."""
    proc = routing.UciProcess(engine)
    markers: list[str] = []
    try:
        for name, value in search_context["fixed_options"].items():
            routing.setoption(
                proc, name, str(value).lower() if isinstance(value, bool) else str(value)
            )
        routing.setoption(proc, "CrazyhouseEvalFile", str(legacy))
        markers += routing.wait_ready_success(proc, "crazyhouse")
        route_commits = [
            line
            for line in markers
            if "route_commit status=ok ruleset=crazyhouse" in line
        ]
        require(
            route_commits
            and all(search_context["required_route_token"] in line for line in route_commits),
            "missing engine-authored scalar route telemetry",
        )
        proc.send("position startpos")
        markers += routing.wait_perft(proc, 1, 20)
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
        markers += routing.wait_command_error(proc, "eval", "crazyhouse_eval_not_bound")
        markers += routing.wait_command_error(proc, "speedtest", "crazyhouse_speedtest_not_bound")
        markers += routing.wait_command_error(proc, "export_net", "crazyhouse_export_net_not_bound")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--legacy-network", required=True, type=Path)
    parser.add_argument("--official-network", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--expected-engine-sha256", required=True)
    parser.add_argument("--transcript-out", required=True, type=Path)
    args = parser.parse_args()

    require(
        re.fullmatch(r"[0-9a-f]{64}", args.expected_engine_sha256) is not None,
        "expected engine SHA-256 is malformed",
    )
    engine = args.engine.resolve(strict=True)
    legacy = args.legacy_network.resolve(strict=True)
    official = args.official_network.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    transcript_path = args.transcript_out.resolve(strict=False)
    require(transcript_path.parent.is_dir(), "transcript parent is missing")
    require(not transcript_path.exists(), "transcript already exists")
    require(sha256_file(engine) == args.expected_engine_sha256, "engine SHA-256 mismatch")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(
        contract["schema"] == "crazyhouse-official-dev-routing-contract/v1",
        "contract schema mismatch",
    )
    repository = contract_path.parents[2]
    pins = {
        name: authenticate_pin(repository, name, pin)
        for name, pin in contract["pins"].items()
    }
    base = json.loads(pins["routing_contract_v1"].read_text(encoding="utf-8"))
    worker_base = json.loads(pins["worker_search_contract_v1"].read_text(encoding="utf-8"))
    standard = json.loads(pins["standard_control_v2"].read_text(encoding="utf-8"))

    profile = contract["profile"]
    require(base["profile"] == profile["id"], "profile ID drift")
    require(base["profile_sha256"] == profile["sha256"], "profile SHA-256 drift")
    require(base["profile_token"] == profile["token"], "profile token drift")
    require(worker_base["profile"]["token"] == profile["token"], "worker profile drift")
    require(
        standard["official"]["commit"] == contract["source"]["official_parent"]
        and standard["official"]["tree"] == contract["source"]["official_parent_tree"],
        "official standard-control source drift",
    )

    legacy_identity = contract["networks"]["legacy"]
    official_identity = contract["networks"]["official_chess"]
    require(legacy.stat().st_size == legacy_identity["bytes"], "legacy network size mismatch")
    require(sha256_file(legacy) == legacy_identity["sha256"], "legacy network SHA-256 mismatch")
    require(official.name == official_identity["filename"], "official network filename mismatch")
    require(official.stat().st_size == official_identity["bytes"], "official network size mismatch")
    require(
        sha256_file(official) == official_identity["sha256"],
        "official network SHA-256 mismatch",
    )

    inventory = contract["option_inventory"]
    worker = contract["worker"]
    search_context = {
        "enabled": True,
        "binding": worker["binding"],
        "fixed_options": worker["fixed_options"],
        "go_command": worker["go_command"],
        "allowed_bestmoves": set(worker["allowed_bestmoves"]),
        "expected_markers": contract["replay"]["expected_markers"],
        "expected_protocol": contract["replay"]["expected_protocol_sha256"],
        "required_route_token": worker["required_route_token"],
        "expected_crazyhouse_commits": contract["replay"][
            "successful_crazyhouse_route_commits"
        ],
    }

    transcript: list[str] = []
    transcript += routing.inventory_scenario(
        engine,
        base,
        inventory["expected_eval_file_line"],
        inventory["expected_count"],
        inventory["ordered_names"],
        inventory["required_additive_lines"],
    )
    transcript += routing.initial_failure_scenario(engine)
    transcript += routing.invalid_variant_scenario(engine, legacy)
    transcript += routing.profile_failure_scenario(engine, legacy)
    transcript += crazyhouse_rule_route_scenario(engine, legacy, search_context)
    transcript += routing.crossed_routes_scenario(engine, legacy, official)
    transcript += routing.failed_replacement_scenario(engine, legacy)
    transcript += routing.chess960_scenario(engine, legacy)
    transcript += routing.position_transaction_scenario(engine, legacy)
    transcript += routing.option_persistence_scenario(engine, legacy, official)
    transcript += routing.chess_control_scenario(engine, official)

    replay = contract["replay"]
    require(len(replay["scenario_ids"]) == 11, "scenario inventory mismatch")
    require(len(transcript) == replay["expected_markers"], "marker count mismatch")
    protocol = "\n".join(transcript).encode("utf-8")
    protocol_sha256 = hashlib.sha256(protocol).hexdigest()
    require(protocol_sha256 == replay["expected_protocol_sha256"], "protocol SHA-256 mismatch")
    commits = [
        marker
        for marker in transcript
        if "route_commit status=ok ruleset=crazyhouse" in marker
    ]
    require(
        len(commits) == replay["successful_crazyhouse_route_commits"],
        "Crazyhouse route-commit count mismatch",
    )
    require(
        all(worker["required_route_token"] in marker for marker in commits),
        "required scalar route token missing",
    )
    require(
        all(
            token not in marker
            for marker in commits
            for token in worker["forbidden_route_tokens"]
        ),
        "forbidden evaluator route token observed",
    )
    require(transcript[-2] == "Nodes searched: 20", "standard perft marker drift")
    require(
        transcript[-1] == f"bestmove {replay['standard_depth_1_bestmove']}",
        "standard depth-1 bestmove drift",
    )

    rows = [
        json.dumps({"sequence": index + 1, "marker": marker}, separators=(",", ":"))
        for index, marker in enumerate(transcript)
    ]
    with transcript_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(rows) + "\n")
    print(
        "PASS crazyhouse_official_dev_routing "
        f"scenarios=11 markers={len(transcript)} protocol_sha256={protocol_sha256} "
        f"crazyhouse_search={worker['binding']} chess_control=PASS"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL crazyhouse_official_dev_routing_verify: {exc}", file=sys.stderr)
        raise SystemExit(1)
