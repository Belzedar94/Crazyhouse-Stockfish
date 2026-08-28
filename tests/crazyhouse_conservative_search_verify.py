#!/usr/bin/env python3
"""Verify the frozen conservative Crazyhouse search-policy boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


PROFILE_TOKEN = (
    "LICHESS_CRAZYHOUSE_2026_08_12@"
    "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
)
LEGACY_BYTES = 58_534_811
LEGACY_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UciProcess:
    def __init__(self, engine: Path, timeout: float, runtime_path_prefix: str | None) -> None:
        env = os.environ.copy()
        if runtime_path_prefix:
            env["PATH"] = runtime_path_prefix + os.pathsep + env.get("PATH", "")
        self.timeout = timeout
        self.commands: list[str] = []
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.proc = subprocess.Popen(
            [str(engine)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        threading.Thread(target=self._reader, args=(self.proc.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=self._reader, args=(self.proc.stderr, "stderr"), daemon=True).start()

    def _reader(self, stream, name: str) -> None:
        for raw in stream:
            line = raw.rstrip("\r\n")
            if name == "stdout":
                self.stdout.append(line)
            else:
                self.stderr.append(line)
            self.events.put((name, line))

    def send(self, command: str) -> int:
        require(self.proc.poll() is None, f"engine exited before command {command!r}")
        assert self.proc.stdin is not None
        mark = len(self.stdout)
        self.commands.append(command)
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return mark

    def wait_after(
        self, mark: int, predicate: Callable[[list[str]], bool], description: str
    ) -> list[str]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            lines = self.stdout[mark:]
            if predicate(lines):
                return lines
            if self.proc.poll() is not None:
                raise VerificationError(
                    f"engine exited {self.proc.returncode} while waiting for {description}; "
                    f"stderr={self.stderr!r}"
                )
            try:
                self.events.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                pass
        raise VerificationError(f"timeout waiting for {description}; output={self.stdout[mark:]!r}")

    def ready(self) -> list[str]:
        mark = self.send("isready")
        return self.wait_after(mark, lambda lines: "readyok" in lines, "readyok")

    def search(self, command: str) -> list[str]:
        mark = self.send(command)
        return self.wait_after(
            mark, lambda lines: any(line.startswith("bestmove ") for line in lines), "bestmove"
        )

    def close(self) -> None:
        if self.proc.poll() is None:
            self.send("quit")
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                self.proc.kill()
                self.proc.wait(timeout=10)
                raise VerificationError("engine did not exit after quit") from exc


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def inspect_source(source_root: Path) -> dict:
    search_path = source_root / "src" / "search.cpp"
    movepick_path = source_root / "src" / "movepick.cpp"
    position_path = source_root / "src" / "position.cpp"
    for path in (search_path, movepick_path, position_path):
        require(path.is_file(), f"missing source file: {path}")

    search = search_path.read_text(encoding="utf-8")
    movepick = movepick_path.read_text(encoding="utf-8")
    position = position_path.read_text(encoding="utf-8")
    s = squash(search)
    m = squash(movepick)
    p = squash(position)

    require(
        "if (activeRuleset == Ruleset::CRAZYHOUSE) return true;" not in p,
        "Crazyhouse SEE bypass was restored",
    )
    require(
        "if (m.type_of() != NORMAL && !(activeRuleset == Ruleset::CRAZYHOUSE && m.is_drop())) return VALUE_ZERO >= threshold;"
        in p,
        "Crazyhouse drop SEE admission drifted",
    )
    require(
        "case PROBCUT : return select([&](const ExtMove& move) { return pos.see_ge(move, threshold); });"
        in m,
        "ProbCut positive-polarity caller drifted",
    )
    require(
        search.count("!pos.see_ge(move") == 4,
        "expected exactly four negative-polarity SEE pruning callers",
    )
    require(
        "if (pos.ruleset() == Ruleset::CRAZYHOUSE) return false;" in s,
        "Crazyhouse shuffling bypass drifted",
    )

    old_decode_calls = search.count(
        "value_from_tt(ttData.value, ss->ply, pos.rule50_count())"
    )
    old_high_counter = "if (pos.rule50_count() < 96)" in s
    old_null = "if (cutNode && ss->staticEval >= beta" in s
    old_probcut = "probCutBeta = beta + 241 - 64 * improving; if (depth >= 3" in s
    old_shallow = "if (!rootNode && pos.non_pawn_material(us) && !is_loss(bestValue))" in s

    gaps: list[str] = []
    if old_decode_calls == 2:
        gaps.append("rule50_tt_value_decode")
    if old_high_counter:
        gaps.append("rule50_tt_cutoff_suppression")
    if old_probcut:
        gaps.append("see_probcut_positive_polarity")
    if old_null:
        gaps.append("null_move_board_material_gate")
    if old_shallow:
        gaps.append("shallow_board_material_pruning")

    target = {
        "orthodox_policy_instances": s.count(
            "const bool orthodoxSearch = pos.ruleset() == Ruleset::CHESS;"
        ),
        "tt_decode_declaration": (
            "Value value_from_tt(Value v, int ply, bool useRule50, int r50c);" in s
        ),
        "tt_decode_definition": (
            "Value value_from_tt(Value v, int ply, bool useRule50, int r50c)" in s
        ),
        "tt_decode_policy_calls": search.count(
            "value_from_tt(ttData.value, ss->ply, orthodoxSearch, pos.rule50_count())"
        ),
        "rule50_downgrade_guards": search.count("if (useRule50 &&"),
        "high_counter_guard": "if (!orthodoxSearch || pos.rule50_count() < 96)" in s,
        "null_move_guard": "if (orthodoxSearch && cutNode && ss->staticEval >= beta" in s,
        "probcut_guard": (
            "probCutBeta = beta + 241 - 64 * improving; if (orthodoxSearch && depth >= 3" in s
        ),
        "shallow_guard": (
            "if ((orthodoxSearch || pos.ruleset() == Ruleset::CRAZYHOUSE) && !rootNode && pos.non_pawn_material(us) && !is_loss(bestValue))"
            in s
        ),
    }
    target["complete"] = (
        target["orthodox_policy_instances"] == 2
        and target["tt_decode_declaration"]
        and target["tt_decode_definition"]
        and target["tt_decode_policy_calls"] == 2
        and target["rule50_downgrade_guards"] == 4
        and target["high_counter_guard"]
        and target["null_move_guard"]
        and target["probcut_guard"]
        and target["shallow_guard"]
    )

    return {
        "files": {
            "src/search.cpp": {
                "bytes": search_path.stat().st_size,
                "sha256": sha256_file(search_path),
            },
            "src/movepick.cpp": {
                "bytes": movepick_path.stat().st_size,
                "sha256": sha256_file(movepick_path),
            },
            "src/position.cpp": {
                "bytes": position_path.stat().st_size,
                "sha256": sha256_file(position_path),
            },
        },
        "old_markers": {
            "tt_decode_calls": old_decode_calls,
            "high_counter": old_high_counter,
            "null_move": old_null,
            "probcut": old_probcut,
            "shallow": old_shallow,
        },
        "gaps": gaps,
        "target": target,
    }


def bestmove(lines: list[str]) -> str:
    rows = [line for line in lines if line.startswith("bestmove ")]
    require(len(rows) == 1, f"expected one bestmove row, got {rows!r}")
    return rows[0].split()[1]


def require_score(lines: list[str], token: str) -> None:
    require(
        any(f" score {token} " in f" {line} " for line in lines if line.startswith("info ")),
        f"missing score {token!r}: {lines!r}",
    )


def configure(proc: UciProcess, legacy_network: Path) -> list[str]:
    mark = proc.send("uci")
    handshake = proc.wait_after(mark, lambda lines: "uciok" in lines, "uciok")
    require(
        any(line.startswith("option name UCI_Variant ") for line in handshake),
        "UCI_Variant option missing",
    )
    proc.send("setoption name Threads value 1")
    proc.send("setoption name Hash value 16")
    proc.send("setoption name MultiPV value 1")
    proc.send("setoption name UCI_Chess960 value false")
    proc.send("setoption name UCI_Variant value crazyhouse")
    proc.send(f"setoption name CrazyhouseProfile value {PROFILE_TOKEN}")
    proc.send(f"setoption name CrazyhouseEvalFile value {legacy_network}")
    ready = proc.ready()
    route = [line for line in ready if "route_commit status=ok ruleset=crazyhouse" in line]
    require(len(route) == 1, f"expected one Crazyhouse route commit: {ready!r}")
    require("backend=legacy-v1" in route[0], f"wrong backend route: {route[0]}")
    require(LEGACY_SHA256 in route[0], f"legacy identity absent from route: {route[0]}")
    return route


def run_case(proc: UciProcess, case: dict) -> dict:
    proc.send("setoption name Clear Hash")
    proc.send(f"position fen {case['fen']}")
    lines = proc.search(case["go"])
    observed_bestmove = bestmove(lines)
    if "bestmove" in case:
        require(
            observed_bestmove == case["bestmove"],
            f"{case['id']} bestmove {observed_bestmove!r} != {case['bestmove']!r}",
        )
    else:
        require(
            observed_bestmove in case["bestmove_set"],
            f"{case['id']} bestmove {observed_bestmove!r} outside frozen set",
        )
    if "score" in case:
        require_score(lines, case["score"])
    require(
        any(line.startswith("info depth") for line in lines),
        f"{case['id']} emitted no depth information",
    )
    return {
        "id": case["id"],
        "bestmove": observed_bestmove,
        "info_rows": sum(line.startswith("info ") for line in lines),
        "stdout_sha256": hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest(),
    }


def run_runtime(engine: Path, legacy_network: Path, timeout: float, runtime_path_prefix: str | None) -> dict:
    cases = [
        {"id": "root_checkmate", "fen": "k7/1Q6/2K5/8/8/8/8/8[] b - - 0 1", "go": "go depth 2", "bestmove": "(none)", "score": "mate 0"},
        {"id": "root_stalemate", "fen": "k7/2Q5/2K5/8/8/8/8/8[] b - - 0 1", "go": "go depth 2", "bestmove": "(none)", "score": "cp 0"},
        {"id": "pocket_prevents_stalemate", "fen": "k7/2Q5/2K5/8/8/8/8/8[n] b - - 0 1", "go": "go depth 2 searchmoves N@a1", "bestmove": "N@a1"},
        {"id": "forced_drop_evasion", "fen": "4r2k/8/8/8/8/8/8/4K3[N] w - - 0 1", "go": "go depth 2 searchmoves N@e2", "bestmove": "N@e2"},
        {"id": "no_fifty_move_result", "fen": "7k/8/8/8/8/8/8/K7[] w - - 100 1", "go": "go depth 2", "bestmove_set": ["a1a2", "a1b1", "a1b2"]},
        {"id": "promoted_capture_demotes_to_pawn", "fen": "7k/8/8/8/8/8/Q~6r/K7[] b - - 0 1", "go": "go depth 2 searchmoves h2a2", "bestmove": "h2a2"},
        {"id": "en_passant_capture", "fen": "7k/8/8/3pP3/8/8/8/K7[] w - d6 0 2", "go": "go depth 2 searchmoves e5d6", "bestmove": "e5d6"},
        {"id": "promotion_provenance", "fen": "7k/P7/8/8/8/8/8/K7[] w - - 0 1", "go": "go depth 2 searchmoves a7a8q", "bestmove": "a7a8q"},
        {"id": "castling", "fen": "r3k2r/8/8/8/8/8/8/R3K2R[] w KQkq - 0 1", "go": "go depth 2 searchmoves e1g1", "bestmove": "e1g1"},
    ]
    proc = UciProcess(engine, timeout, runtime_path_prefix)
    try:
        route = configure(proc, legacy_network)
        observations = [run_case(proc, case) for case in cases]

        proc.send("setoption name Clear Hash")
        mate_fen = "k7/2Q5/2K5/8/8/8/8/8[] w - - 100 1"
        mate_runs = []
        for _ in range(2):
            proc.send(f"position fen {mate_fen}")
            lines = proc.search("go depth 3")
            require(bestmove(lines) == "c7b7", "high-halfmove mate bestmove mismatch")
            require_score(lines, "mate 1")
            mate_runs.append(
                {
                    "bestmove": "c7b7",
                    "score": "mate 1",
                    "stdout_sha256": hashlib.sha256(
                        ("\n".join(lines) + "\n").encode()
                    ).hexdigest(),
                }
            )
        observations.append(
            {"id": "high_halfmove_tt_mate_reuse", "runs": mate_runs, "hash_clears": 1}
        )
        proc.close()
        require(proc.proc.returncode == 0, f"engine exit code {proc.proc.returncode}")
        require(not proc.stderr, f"engine stderr was not empty: {proc.stderr!r}")
        return {
            "status": "PASS_SPECIAL_STATE_SEARCH_MATRIX",
            "route": route[0],
            "cases": observations,
            "case_count": len(observations),
            "stderr_bytes": 0,
            "exit_code": proc.proc.returncode,
            "commands_sha256": hashlib.sha256(
                ("\n".join(proc.commands) + "\n").encode()
            ).hexdigest(),
            "stdout_sha256": hashlib.sha256(
                ("\n".join(proc.stdout) + "\n").encode()
            ).hexdigest(),
        }
    finally:
        if proc.proc.poll() is None:
            proc.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("expected-red", "green"), required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--legacy-network", type=Path, required=True)
    parser.add_argument("--runtime-path-prefix")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    for path in (args.source_root, args.engine, args.legacy_network):
        require(path.exists(), f"missing required path: {path}")
    args.source_root = args.source_root.resolve(strict=True)
    args.engine = args.engine.resolve(strict=True)
    args.legacy_network = args.legacy_network.resolve(strict=True)
    require(args.engine.is_file(), "engine is not a file")
    require(args.legacy_network.is_file(), "legacy network is not a file")
    require(args.legacy_network.stat().st_size == LEGACY_BYTES, "legacy network size mismatch")
    require(sha256_file(args.legacy_network) == LEGACY_SHA256, "legacy network digest mismatch")

    source = inspect_source(args.source_root)
    runtime = run_runtime(
        args.engine, args.legacy_network, args.timeout, args.runtime_path_prefix
    )
    expected_gaps = [
        "rule50_tt_value_decode",
        "rule50_tt_cutoff_suppression",
        "see_probcut_positive_polarity",
        "null_move_board_material_gate",
        "shallow_board_material_pruning",
    ]

    if args.mode == "expected-red":
        require(source["gaps"] == expected_gaps, f"unexpected mapped gaps: {source['gaps']!r}")
        require(not source["target"]["complete"], "target policy unexpectedly already complete")
        result = "PASS_EXPECTED_RED_CONSERVATIVE_SEARCH_POLICY_GAPS"
    else:
        require(not source["gaps"], f"old policy gaps remain: {source['gaps']!r}")
        require(source["target"]["complete"], f"target policy incomplete: {source['target']!r}")
        result = "PASS_CONSERVATIVE_SEARCH_POLICY_GREEN"

    observation = {
        "schema": "crazyhouse-conservative-search-observation/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "result": result,
        "source": source,
        "engine": {
            "path": str(args.engine),
            "bytes": args.engine.stat().st_size,
            "sha256": sha256_file(args.engine),
        },
        "legacy_network": {
            "path": str(args.legacy_network),
            "bytes": LEGACY_BYTES,
            "sha256": LEGACY_SHA256,
        },
        "runtime": runtime,
        "timing_evidence": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_claim": False,
    }
    payload = json.dumps(observation, indent=2, sort_keys=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8", newline="\n")
    print(
        f"PASS crazyhouse_conservative_search mode={args.mode} result={result} "
        f"runtime_cases={runtime['case_count']} static_gaps={len(source['gaps'])} "
        "strength_claim=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL crazyhouse_conservative_search {exc}", file=sys.stderr)
        raise SystemExit(1)
