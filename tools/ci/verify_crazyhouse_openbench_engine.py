#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.json"
DEFAULT_LIMIT_ADDENDUM = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.001.json"
)
DEFAULT_CORPUS_ADDENDUM = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.002.json"
)
DEFAULT_TARGET_ADDENDUM = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.003.json"
)
DEFAULT_HARNESS_ADDENDUM = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.004.json"
)
DEFAULT_SIGNATURE_ADDENDUM = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.005.json"
)
NODES_PATTERN = re.compile(r"Nodes searched\s*:\s*(\d+)", re.IGNORECASE)
NPS_PATTERN = re.compile(r"Nodes/second\s*:\s*(\d+)", re.IGNORECASE)
POSITION_PATTERN = re.compile(r"Position:\s*(\d+)/(\d+)\s+\((.*)\)")


class VerificationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_engine(
    command: list[str], *, stdin: str | None, timeout: int, cwd: Path
) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VerificationError(f"engine timed out after {timeout} seconds") from error
    return completed.returncode, completed.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_uci(engine: Path, contract: dict[str, Any], timeout: int) -> dict[str, Any]:
    returncode, output = run_engine(
        [str(engine)], stdin="uci\nquit\n", timeout=timeout, cwd=engine.parent
    )
    token = "embedded:crazyhouse-8ebf84784ad2.nnue"
    profile = contract["rule_profile"]
    profile_token = f"{profile['id']}@{profile['sha256']}"
    require(returncode == 0, f"UCI inventory exited with status {returncode}")
    require("uciok" in output, "UCI inventory withheld uciok")
    require(
        f"option name CrazyhouseEvalFile type string default {token}" in output,
        "embedded CrazyhouseEvalFile is not the advertised default",
    )
    require(
        "option name UCI_Variant type combo default crazyhouse var chess var crazyhouse" in output,
        "UCI_Variant inventory does not default exactly to crazyhouse",
    )
    require(
        f"option name CrazyhouseProfile type string default {profile_token}" in output,
        "CrazyhouseProfile inventory does not match the frozen contract",
    )
    return {"uciok": True, "embedded_default": token}


def verify_capability(engine: Path, contract: dict[str, Any], timeout: int) -> dict[str, Any]:
    nonce = secrets.token_hex(16)
    profile = contract["rule_profile"]
    network = contract["legacy_network"]
    profile_token = f"{profile['id']}@{profile['sha256']}"
    commands = (
        "setoption name UCI_Variant value crazyhouse\n"
        f"setoption name CrazyhouseProfile value {profile_token}\n"
        f"setoption name CrazyhouseCapabilityNonce value {nonce}\n"
        "isready\n"
        "quit\n"
    )
    returncode, output = run_engine(
        [str(engine)], stdin=commands, timeout=timeout, cwd=engine.parent
    )
    route = (
        "route_commit status=ok ruleset=crazyhouse "
        f"profile={profile['id']} profile_sha256={profile['sha256']}"
    )
    acknowledgement = (
        "crazyhouse_capability_ack status=ok "
        f"profile={profile['id']} profile_sha256={profile['sha256']} nonce={nonce}"
    )
    require(returncode == 0, f"capability handshake exited with status {returncode}")
    require(route in output, "exact Crazyhouse route marker is absent")
    require(f"identity={network['sha256']}" in output, "route identity is not the legacy network")
    require(acknowledgement in output, "exact challenged capability acknowledgement is absent")
    require("readyok" in output, "capability handshake withheld readyok")
    require("ERROR" not in output, "capability handshake emitted an error")
    return {"nonce": nonce, "acknowledged": True, "network_identity": network["sha256"]}


def verify_missing_override(engine: Path, timeout: int) -> dict[str, Any]:
    missing = "definitely-missing-crazyhouse-openbench-network.nnue"
    commands = (
        f"setoption name CrazyhouseEvalFile value {missing}\n"
        "isready\n"
        "quit\n"
    )
    returncode, output = run_engine(
        [str(engine)], stdin=commands, timeout=timeout, cwd=engine.parent
    )
    require(returncode == 0, f"missing-network control exited with status {returncode}")
    require("code=legacy_missing_file" in output, "missing override was not rejected precisely")
    require("readyok_withheld=1" in output, "missing override did not withhold readiness")
    require("route_commit status=ok" not in output, "missing override fell back to embedded bytes")
    return {"missing_override": missing, "rejected": True, "fallback_observed": False}


def verify_bench(
    engine: Path,
    contract: dict[str, Any],
    expected_fens: list[str],
    timeout: int,
    runs: int,
    expected_nodes: int | None,
) -> dict[str, Any]:
    observed_nodes: list[int] = []
    observed_nps: list[int] = []
    expected_positions = contract["benchmark"]["position_count"]
    for index in range(runs):
        returncode, output = run_engine(
            [str(engine), "bench"], stdin=None, timeout=timeout, cwd=engine.parent
        )
        require(returncode == 0, f"bench run {index + 1} exited with status {returncode}")
        require("ERROR" not in output, f"bench run {index + 1} emitted an error")
        require(
            "route_commit status=ok ruleset=crazyhouse" in output,
            f"bench run {index + 1} did not bind the Crazyhouse route",
        )
        nodes_match = NODES_PATTERN.search(output)
        nps_match = NPS_PATTERN.search(output)
        require(nodes_match is not None, f"bench run {index + 1} emitted no node signature")
        require(nps_match is not None, f"bench run {index + 1} emitted no NPS")
        positions = POSITION_PATTERN.findall(output)
        require(len(positions) == expected_positions, f"bench run {index + 1} searched wrong count")
        require(
            positions[-1][:2] == (str(expected_positions), str(expected_positions)),
            f"bench run {index + 1} did not finish the frozen corpus",
        )
        observed_fens = [position[2] for position in positions]
        require(
            observed_fens == expected_fens,
            f"bench run {index + 1} did not search the corrected frozen FEN order",
        )
        observed_nodes.append(int(nodes_match.group(1)))
        observed_nps.append(int(nps_match.group(1)))

    require(len(set(observed_nodes)) == 1, "bench node signature is not deterministic")
    if expected_nodes is not None:
        require(observed_nodes[0] == expected_nodes, "bench signature differs from expected nodes")
    return {
        "runs": runs,
        "nodes": observed_nodes,
        "nodes_per_second": observed_nps,
        "deterministic": True,
        "expected_nodes": expected_nodes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--limit-addendum", type=Path, default=DEFAULT_LIMIT_ADDENDUM)
    parser.add_argument("--corpus-addendum", type=Path, default=DEFAULT_CORPUS_ADDENDUM)
    parser.add_argument("--target-addendum", type=Path, default=DEFAULT_TARGET_ADDENDUM)
    parser.add_argument("--harness-addendum", type=Path, default=DEFAULT_HARNESS_ADDENDUM)
    parser.add_argument("--signature-addendum", type=Path, default=DEFAULT_SIGNATURE_ADDENDUM)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--expected-nodes", type=int)
    args = parser.parse_args()

    if args.runs < 2:
        raise SystemExit("--runs must be at least 2")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")

    engine = args.engine.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    limit_addendum_path = args.limit_addendum.resolve(strict=True)
    corpus_addendum_path = args.corpus_addendum.resolve(strict=True)
    target_addendum_path = args.target_addendum.resolve(strict=True)
    harness_addendum_path = args.harness_addendum.resolve(strict=True)
    signature_addendum_path = args.signature_addendum.resolve(strict=True)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    limit_addendum = json.loads(limit_addendum_path.read_text(encoding="utf-8"))
    corpus_addendum = json.loads(corpus_addendum_path.read_text(encoding="utf-8"))
    target_addendum = json.loads(target_addendum_path.read_text(encoding="utf-8"))
    harness_addendum = json.loads(harness_addendum_path.read_text(encoding="utf-8"))
    signature_addendum = json.loads(signature_addendum_path.read_text(encoding="utf-8"))
    if limit_addendum["parent"]["sha256"] != sha256_file(contract_path):
        raise SystemExit("limit addendum does not authenticate the supplied parent contract")
    if limit_addendum["parent"]["bytes"] != contract_path.stat().st_size:
        raise SystemExit("limit addendum parent byte count differs from the supplied contract")
    if corpus_addendum["parent"]["sha256"] != sha256_file(limit_addendum_path):
        raise SystemExit("corpus addendum does not authenticate the limit addendum")
    if corpus_addendum["parent"]["bytes"] != limit_addendum_path.stat().st_size:
        raise SystemExit("corpus addendum parent byte count differs from the limit addendum")
    if target_addendum["parent"]["sha256"] != sha256_file(corpus_addendum_path):
        raise SystemExit("target addendum does not authenticate the corpus addendum")
    if target_addendum["parent"]["bytes"] != corpus_addendum_path.stat().st_size:
        raise SystemExit("target addendum parent byte count differs from the corpus addendum")
    if harness_addendum["parent"]["sha256"] != sha256_file(target_addendum_path):
        raise SystemExit("harness addendum does not authenticate the target addendum")
    if harness_addendum["parent"]["bytes"] != target_addendum_path.stat().st_size:
        raise SystemExit("harness addendum parent byte count differs from the target addendum")
    if signature_addendum["parent"]["sha256"] != sha256_file(harness_addendum_path):
        raise SystemExit("signature addendum does not authenticate the harness addendum")
    if signature_addendum["parent"]["bytes"] != harness_addendum_path.stat().st_size:
        raise SystemExit("signature addendum parent byte count differs from the harness addendum")
    if limit_addendum["correction"] != {
        "single_changed_scientific_field": "benchmark.limit",
        "before": 6,
        "after": 4,
        "limit_type": "depth",
        "reason": "Remove two Crazyhouse plies after the preregistered depth-6 corpus exceeded its functional ceiling without yielding a complete signature.",
        "frozen_before_complete_depth4_signature": True,
    }:
        raise SystemExit("unexpected benchmark limit correction addendum")
    corpus_correction = corpus_addendum["correction"]
    if corpus_correction["single_changed_scientific_field"] != "benchmark.positions[10]":
        raise SystemExit("unexpected benchmark corpus correction field")
    if not corpus_correction["frozen_before_complete_revised_corpus_signature"]:
        raise SystemExit("benchmark corpus correction was not frozen before observation")
    expected_fens = [row["fen"] for row in contract["benchmark"]["positions"]]
    if expected_fens[10] != corpus_correction["before"]["fen"]:
        raise SystemExit("benchmark corpus correction does not match its frozen predecessor")
    expected_fens[10] = corpus_correction["after"]["fen"]
    target_after = target_addendum["decision"]["after"]
    if target_after != {
        "arch": "x86-64",
        "windows_comp": "mingw",
        "linux_comp": "gcc",
        "legacy_evaluator": "scalar",
        "optimized": True,
        "pgo": False,
        "recursive_target": "all",
    }:
        raise SystemExit("unexpected OpenBench worker target profile")
    if not target_addendum["decision"]["frozen_before_clean_export_bench_signature"]:
        raise SystemExit("OpenBench target profile was not frozen before clean exports")
    if harness_addendum["rejected_lease"]["gate_credit"]:
        raise SystemExit("rejected clean-export lease received gate credit")
    if harness_addendum["correction"]["frozen_before_retry_lease"] != 286:
        raise SystemExit("unexpected clean-export retry correction")
    signature_benchmark = signature_addendum["benchmark"]
    if signature_benchmark["observed_nodes"] != [113485, 113485, 113485, 113485]:
        raise SystemExit("unexpected frozen clean-export node observations")
    if signature_benchmark["expected_nodes"] != 113485:
        raise SystemExit("unexpected frozen OpenBench bench signature")
    if signature_addendum["formal_evidence"]["lease"] != 286:
        raise SystemExit("unexpected formal clean-export lease")
    expected_nodes = args.expected_nodes
    if expected_nodes is None:
        expected_nodes = signature_benchmark["expected_nodes"]

    try:
        result = {
            "schema": "crazyhouse-openbench-engine-verification/v1",
            "contract": {
                "path": str(contract_path),
                "sha256": sha256_file(contract_path),
            },
            "addenda": {
                "limit": {
                    "path": str(limit_addendum_path),
                    "sha256": sha256_file(limit_addendum_path),
                    "effective_bench_depth": limit_addendum["correction"]["after"],
                },
                "corpus": {
                    "path": str(corpus_addendum_path),
                    "sha256": sha256_file(corpus_addendum_path),
                    "replaced_position_index": 10,
                },
                "target": {
                    "path": str(target_addendum_path),
                    "sha256": sha256_file(target_addendum_path),
                    "profile": target_after,
                },
                "harness": {
                    "path": str(harness_addendum_path),
                    "sha256": sha256_file(harness_addendum_path),
                    "rejected_lease": harness_addendum["rejected_lease"]["lease"],
                },
                "signature": {
                    "path": str(signature_addendum_path),
                    "sha256": sha256_file(signature_addendum_path),
                    "expected_nodes": signature_benchmark["expected_nodes"],
                    "formal_lease": signature_addendum["formal_evidence"]["lease"],
                },
            },
            "engine": {
                "path": str(engine),
                "bytes": engine.stat().st_size,
                "sha256": sha256_file(engine),
            },
            "uci": verify_uci(engine, contract, args.timeout),
            "capability": verify_capability(engine, contract, args.timeout),
            "negative": verify_missing_override(engine, args.timeout),
            "bench": verify_bench(
                engine, contract, expected_fens, args.timeout, args.runs, expected_nodes
            ),
            "claims": {
                "engineering_only": True,
                "strength": False,
                "openbench_official": False,
                "release": False,
            },
        }
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
