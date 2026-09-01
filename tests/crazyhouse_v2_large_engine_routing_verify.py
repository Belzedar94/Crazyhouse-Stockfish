#!/usr/bin/env python3
"""End-to-end fail-closed verifier for the opt-in large Crazyhouse V2 route."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from crazyhouse_uci_routing_verify import UciProcess, VerificationFailure, require, setoption


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "tests/crazyhouse/p12-nnue-v2-large-engine-routing-v1.json"
PREREG_SHA256 = "791cdde30e6fcb6b1ae05124deb1e498824851db353e6775a5a444f15d59a654"
TRANSITIONS = ROOT / "tests/crazyhouse/p12-nnue-v2-simd-incremental-probe-v1.json"
TRANSITIONS_SHA256 = "1f93f28118478e46362b4254df7e2fa366b851f698f7c1075676a973f7e80a34"
NETWORK_SHA256 = "e305c386080c3d802deb23fad322ee04689d360d9b04526f7e5608e9fc055311"
LEGACY_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
PROVENANCE = ":".join(f"{value:02x}" * 32 for value in (0x11, 0x22, 0x33, 0x44, 0x55, 0x66))
ROUTE_TOKEN = (
    f"backend=large-v2-a0 identity={NETWORK_SHA256} "
    "evaluator=large-v2-a0-incremental transformer_update=scalar-delta "
    "parity_simd_backend=sse2-x8-int16-to-int32"
)
NODES = re.compile(r"\bnodes (\d+)\b")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location("crazyhouse_v2_large_reference", path)
    if spec is None or spec.loader is None:
        raise VerificationFailure("cannot import the large-V2 reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_uci(engine: Path) -> tuple[UciProcess, list[str]]:
    proc = UciProcess(engine)
    proc.send("uci")
    lines = proc.wait_for(lambda line: line == "uciok", "large-V2 uciok")
    required = {
        "option name CrazyhouseEvaluator type combo default legacy-v1 var legacy-v1 var large-v2-a0",
        "option name CrazyhouseEvalFile type string default <empty>",
        "option name CrazyhouseEvalSHA256 type string default <empty>",
        "option name CrazyhouseEvalProvenance type string default <empty>",
    }
    require(required.issubset(set(lines)), f"large-V2 option inventory drifted: {lines!r}")
    return proc, lines


def configure_large(proc: UciProcess, network: Path | str, sha256: str, provenance: str,
                    evaluator: str = "large-v2-a0") -> None:
    setoption(proc, "Threads", "1")
    setoption(proc, "Hash", "16")
    setoption(proc, "UCI_Variant", "crazyhouse")
    setoption(proc, "CrazyhouseEvaluator", evaluator)
    setoption(proc, "CrazyhouseEvalFile", str(network))
    setoption(proc, "CrazyhouseEvalSHA256", sha256)
    setoption(proc, "CrazyhouseEvalProvenance", provenance)


def ready_success(proc: UciProcess) -> list[str]:
    proc.send("isready")
    lines = proc.wait_for(
        lambda line: line == "readyok" or "READY state=failed" in line,
        "large-V2 readyok",
        180,
    )
    require(lines[-1] == "readyok", f"large-V2 route did not commit: {lines!r}")
    commits = [line for line in lines if "route_commit status=ok ruleset=crazyhouse" in line]
    require(len(commits) == 1, f"large-V2 route commit count drifted: {commits!r}")
    require(ROUTE_TOKEN in commits[0], f"large-V2 route truth drifted: {commits[0]!r}")
    return lines


def ready_failure(proc: UciProcess, code: str) -> list[str]:
    proc.send("isready")
    lines = proc.wait_for(
        lambda line: line == "readyok" or "READY state=failed" in line,
        f"large-V2 failure {code}",
        180,
    )
    lines += proc.drain()
    require("readyok" not in lines, f"{code} emitted readyok: {lines!r}")
    require(any(f"code={code}" in line for line in lines), f"{code} was not typed: {lines!r}")
    require(any("readyok_withheld=1" in line for line in lines),
            f"{code} lacked readyok withholding: {lines!r}")
    require(not any("route_commit status=ok" in line for line in lines),
            f"{code} committed a backend: {lines!r}")
    proc.send("go nodes 1")
    refusal = proc.wait_for(
        lambda line: "info string ERROR go" in line and f"code={code}" in line,
        f"large-V2 search refusal {code}",
    )
    refusal += proc.drain()
    require(not any(line.startswith("bestmove ") for line in refusal),
            f"{code} admitted search: {refusal!r}")
    return lines + refusal


def run_failure(engine: Path, network: Path | str, sha256: str, provenance: str, code: str,
                evaluator: str = "large-v2-a0") -> None:
    proc, _ = open_uci(engine)
    try:
        configure_large(proc, network, sha256, provenance, evaluator)
        ready_failure(proc, code)
    finally:
        proc.close()


def run_go(proc: UciProcess, nodes: int, position: str = "position startpos") -> tuple[str, int]:
    proc.send(position)
    proc.send(f"go nodes {nodes}")
    lines = proc.wait_for(lambda line: line.startswith("bestmove "), f"go nodes {nodes}", 120)
    info_nodes = [int(match.group(1)) for line in lines if (match := NODES.search(line))]
    require(info_nodes and max(info_nodes) >= nodes,
            f"large-V2 search did not reach its node limit: {lines!r}")
    require(not any("ERROR" in line for line in lines), f"large-V2 search emitted error: {lines!r}")
    return lines[-1], max(info_nodes)


def wait_for_stderr(proc: UciProcess, predicate, description: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(predicate(line) for line in proc.stderr_all):
            return
        time.sleep(0.01)
    raise VerificationFailure(f"timeout waiting for {description}: {proc.stderr_all!r}")


def positive_run(engine: Path, network: Path, legacy_network: Path | None,
                 transition_fens: list[str], label: str) -> tuple[str, tuple[str, ...], str]:
    proc, _ = open_uci(engine)
    stage = "configure-large"
    try:
        configure_large(proc, network, NETWORK_SHA256, PROVENANCE)
        stage = "commit-large"
        ready_success(proc)
        stage = "first-search"
        first_bestmove, _ = run_go(proc, 512)

        stage = "repeated-search"
        proc.send("setoption name Clear Hash")
        second_bestmove, _ = run_go(proc, 512)
        require(first_bestmove == second_bestmove,
                f"large-V2 repeated search is not deterministic: {first_bestmove!r} != {second_bestmove!r}")

        stage = "special-state-searches"
        special_bestmoves = tuple(
            run_go(proc, 128, f"position fen {fen}")[0] for fen in transition_fens
        )

        stage = "bench"
        proc.send("bench 16 1 256 current nodes")
        proc.send("isready")
        bench_lines = proc.wait_for(lambda line: line == "readyok", "large-V2 bench completion", 180)
        require(any(line.startswith("bestmove ") for line in bench_lines),
                f"large-V2 bench did not search: {bench_lines!r}")
        wait_for_stderr(proc, lambda line: "Nodes searched  :" in line,
                        "large-V2 bench summary")

        legacy_commit = "not-requested"
        if legacy_network is not None:
            stage = "commit-legacy-restore"
            setoption(proc, "CrazyhouseEvaluator", "legacy-v1")
            setoption(proc, "CrazyhouseEvalFile", str(legacy_network))
            setoption(proc, "CrazyhouseEvalSHA256", "")
            setoption(proc, "CrazyhouseEvalProvenance", "")
            proc.send("isready")
            lines = proc.wait_for(
                lambda line: line == "readyok" or "READY state=failed" in line,
                "legacy route after large-V2",
                180,
            )
            require(lines[-1] == "readyok", f"legacy restore failed: {lines!r}")
            commits = [line for line in lines if "route_commit status=ok ruleset=crazyhouse" in line]
            require(len(commits) == 1 and f"backend=legacy-v1 identity={LEGACY_SHA256}" in commits[0],
                    f"legacy restore identity drifted: {commits!r}")
            require("evaluator=incremental-" in commits[0],
                    f"legacy restore evaluator truth is missing: {commits[0]!r}")
            legacy_commit = commits[0]
            stage = "legacy-restore-search"
            run_go(proc, 128)
        stage = "complete"
        return first_bestmove, special_bestmoves, legacy_commit
    finally:
        print(f"TRACE crazyhouse_v2_large_engine_routing process={label} stage={stage} closing", flush=True)
        try:
            proc.close(expect_stderr_empty=False)
        except VerificationFailure as exc:
            raise VerificationFailure(f"process={label} stage={stage}; {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--legacy-network", type=Path)
    args = parser.parse_args()

    engine = args.engine.resolve()
    reference_path = args.reference.resolve()
    legacy_network = args.legacy_network.resolve() if args.legacy_network else None
    require(engine.is_file(), "large-V2 routed engine is missing")
    require(reference_path.is_file(), "large-V2 reference is missing")
    require(sha256_file(PREREG) == PREREG_SHA256, "large-V2 routing preregistration pin mismatch")
    require(sha256_file(TRANSITIONS) == TRANSITIONS_SHA256,
            "large-V2 transition fixture pin mismatch")
    transition_contract = json.loads(TRANSITIONS.read_text(encoding="utf-8"))
    transition_cases = transition_contract.get("transition_cases")
    require(isinstance(transition_cases, list) and len(transition_cases) == 13,
            "large-V2 transition case count drifted")
    transition_fens: list[str] = []
    for case in transition_cases:
        require(isinstance(case, dict) and isinstance(case.get("fen"), str),
                "large-V2 transition FEN framing drifted")
        transition_fens.append(case["fen"])
    if legacy_network is not None:
        require(legacy_network.is_file(), "legacy restore network is missing")
        require(sha256_file(legacy_network) == LEGACY_SHA256, "legacy restore network identity mismatch")

    reference = load_reference(reference_path)
    with tempfile.TemporaryDirectory(prefix="crazyhouse-v2-engine-route-") as temporary:
        root = Path(temporary)
        network = root / "fixture.nnue"
        reference.write_fixture_container(network)
        require(network.stat().st_size == reference.FILE_BYTES, "large-V2 fixture size drifted")
        require(sha256_file(network) == NETWORK_SHA256, "large-V2 fixture SHA-256 drifted")

        first = positive_run(engine, network, legacy_network, transition_fens, "positive-1")
        second = positive_run(engine, network, legacy_network, transition_fens, "positive-2")
        require(first == second, f"large-V2 fresh-process result is not deterministic: {first!r} != {second!r}")

        missing = root / "missing.nnue"
        short = root / "short.nnue"
        short.write_bytes(b"x")
        corrupt = root / "corrupt.nnue"
        shutil.copyfile(network, corrupt)
        with corrupt.open("r+b") as stream:
            stream.seek(-1, 2)
            original = stream.read(1)
            require(len(original) == 1, "large-V2 corrupt fixture is empty")
            stream.seek(-1, 2)
            stream.write(bytes((original[0] ^ 1,)))
        corrupt_sha = sha256_file(corrupt)
        wrong_provenance = "77" * 32 + PROVENANCE[64:]

        run_failure(engine, network, NETWORK_SHA256, PROVENANCE,
                    "crazyhouse_evaluator_unknown", "unknown-v9")
        run_failure(engine, "", NETWORK_SHA256, PROVENANCE, "large_eval_file_empty")
        run_failure(engine, network, "invalid", PROVENANCE, "large_sha256_invalid")
        run_failure(engine, network, NETWORK_SHA256, "invalid", "large_provenance_invalid")
        run_failure(engine, missing, NETWORK_SHA256, PROVENANCE, "large_missing_file")
        run_failure(engine, root, NETWORK_SHA256, PROVENANCE, "large_not_regular_file")
        run_failure(engine, short, NETWORK_SHA256, PROVENANCE, "large_wrong_file_size")
        run_failure(engine, network, "00" * 32, PROVENANCE, "large_sha256_mismatch")
        run_failure(engine, network, NETWORK_SHA256, wrong_provenance, "large_container_rejected")
        run_failure(engine, corrupt, corrupt_sha, PROVENANCE, "large_container_rejected")

    print(
        "PASS crazyhouse_v2_large_engine_routing"
        " positive_processes=2 repeated_searches=4 bench_runs=2"
        " special_state_searches=26 transition_roots=13"
        " negative_routes=10 readyok_withheld=10 search_refused=10"
        f" legacy_restore={'true' if legacy_network is not None else 'not-requested'}"
        " model_selected=false strength_evidence=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationFailure, RuntimeError) as exc:
        raise SystemExit(f"FAIL crazyhouse_v2_large_engine_routing: {exc}") from exc
