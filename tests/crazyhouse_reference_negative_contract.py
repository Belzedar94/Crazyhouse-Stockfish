#!/usr/bin/env python3
"""Verify that reference adapters reject malformed or unsupported requests."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from crazyhouse_reference_support import ScalachessExportFailure, authenticated_scalachess_export


ROOT = Path(__file__).resolve().parent.parent
PYTHON_ADAPTER = ROOT / "tools" / "references" / "python_chess_adapter.py"
CHESSOPS_ADAPTER = ROOT / "tools" / "references" / "chessops_adapter.mjs"
SCALACHESS_ADAPTER = ROOT / "tools" / "references" / "scalachess-adapter"
SCALACHESS_ADAPTER_IGNORES = ("target", ".bsp", ".metals", ".scala-build")
PROFILE = "LICHESS_CRAZYHOUSE_2026_08_12"
FEN = "7k/8/8/8/8/8/8/K7[] w - - 0 1"


class NegativeFailure(RuntimeError):
    pass


def isolated_scalachess_adapter(temp: Path) -> Path:
    if not SCALACHESS_ADAPTER.is_dir():
        raise NegativeFailure(f"scalachess adapter source is missing: {SCALACHESS_ADAPTER}")
    destination = temp / "scalachess-adapter"
    try:
        shutil.copytree(
            SCALACHESS_ADAPTER,
            destination,
            ignore=shutil.ignore_patterns(*SCALACHESS_ADAPTER_IGNORES),
        )
    except OSError as exc:
        raise NegativeFailure(f"failed to isolate scalachess adapter source: {exc}") from exc
    required = (
        destination / "build.sbt",
        destination / "project" / "build.properties",
        destination / "src" / "main" / "scala" / "crazyhouse" / "reference" / "Main.scala",
    )
    missing = [str(path.relative_to(destination)) for path in required if not path.is_file()]
    if missing:
        raise NegativeFailure(f"isolated scalachess adapter is incomplete: {missing}")
    return destination


def prepare_evidence_dir(requested: Path | None) -> Path | None:
    if requested is None:
        return None
    evidence = requested.resolve()
    if evidence.exists():
        raise NegativeFailure(f"evidence directory already exists: {evidence}")
    if not evidence.parent.is_dir():
        raise NegativeFailure(f"evidence parent directory is missing: {evidence.parent}")
    evidence.mkdir()
    return evidence


def malformed_requests() -> list[tuple[str | None, str, dict[str, Any] | str]]:
    return [
        (None, "INVALID_JSON", "{not-json"),
        (
            "NEG-PROFILE",
            "INVALID_PROFILE",
            {
                "schema": "crazyhouse-reference-request/v1",
                "authority_profile": "UNTRUSTED_PROFILE",
                "id": "NEG-PROFILE",
                "op": "inspect",
                "fen": FEN,
            },
        ),
        (
            "NEG-FEN",
            "INVALID_FEN_OR_POSITION",
            {
                "schema": "crazyhouse-reference-request/v1",
                "authority_profile": PROFILE,
                "id": "NEG-FEN",
                "op": "inspect",
                "fen": "not-a-fen",
            },
        ),
        (
            "NEG-MOVE",
            "ILLEGAL_MOVE",
            {
                "schema": "crazyhouse-reference-request/v1",
                "authority_profile": PROFILE,
                "id": "NEG-MOVE",
                "op": "transition",
                "fen": FEN,
                "moves": ["a1a8"],
            },
        ),
        (
            "NEG-DEPTH",
            "INVALID_REQUEST",
            {
                "schema": "crazyhouse-reference-request/v1",
                "authority_profile": PROFILE,
                "id": "NEG-DEPTH",
                "op": "perft",
                "fen": FEN,
                "depth": 7,
            },
        ),
        (
            "NEG-OP",
            "UNSUPPORTED_OPERATION",
            {
                "schema": "crazyhouse-reference-request/v1",
                "authority_profile": PROFILE,
                "id": "NEG-OP",
                "op": "guess",
                "fen": FEN,
            },
        ),
    ]


def run_and_check(
    name: str,
    command: list[str],
    cwd: Path,
    output: Path,
    timeout: float,
    expected: list[tuple[str | None, str, dict[str, Any] | str]],
    evidence: Path | None,
    env: dict[str, str] | None = None,
) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NegativeFailure(f"{name}: launch failed: {exc}") from exc
    if evidence is not None:
        (evidence / f"{name}.stdout.log").write_text(
            completed.stdout, encoding="utf-8", newline="\n"
        )
        (evidence / f"{name}.stderr.log").write_text(
            completed.stderr, encoding="utf-8", newline="\n"
        )
        if output.is_file():
            shutil.copyfile(output, evidence / f"{name}.raw.jsonl")
    if completed.returncode != 1:
        diagnostics = completed.stdout + "\n" + completed.stderr
        raise NegativeFailure(
            f"{name}: expected request-rejection exit 1, got {completed.returncode}\n{diagnostics}"
        )
    if not output.is_file():
        raise NegativeFailure(f"{name}: rejection output is missing")
    responses = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    if evidence is not None:
        normalized: list[str] = []
        for response in responses:
            record = copy.deepcopy(response)
            identity = record.get("implementation")
            if isinstance(identity, dict):
                identity.pop("root", None)
            normalized.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
        (evidence / f"{name}.normalized.jsonl").write_text(
            "\n".join(normalized) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    if len(responses) != len(expected):
        raise NegativeFailure(f"{name}: expected {len(expected)} responses, got {len(responses)}")
    allowed_fen_codes = {"INVALID_FEN", "INVALID_POSITION"}
    for response, (expected_id, expected_code, _) in zip(responses, expected, strict=True):
        if response.get("ok") is not False or response.get("id") != expected_id:
            raise NegativeFailure(f"{name}: malformed rejection envelope: {response}")
        code = response.get("error", {}).get("code")
        if expected_code == "INVALID_FEN_OR_POSITION":
            if code not in allowed_fen_codes:
                raise NegativeFailure(f"{name}:{expected_id}: expected FEN/position rejection, got {code}")
        elif code != expected_code:
            raise NegativeFailure(f"{name}:{expected_id}: expected {expected_code}, got {code}: {response}")
    print(f"PASS {name} rejected {len(expected)} malformed/unsupported requests")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-executable", type=Path, required=True)
    parser.add_argument("--python-reference-root", type=Path, required=True)
    parser.add_argument("--node", default="node")
    parser.add_argument("--chessops-root", type=Path, required=True)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--sbt-launcher", type=Path)
    parser.add_argument("--scalachess-root", type=Path)
    parser.add_argument("--sbt-cache-root", type=Path)
    parser.add_argument("--git", default="git")
    parser.add_argument("--skip-scalachess", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--scala-timeout", type=float, default=600.0)
    args = parser.parse_args()

    if not args.skip_scalachess and any(
        value is None for value in (args.java, args.sbt_launcher, args.scalachess_root, args.sbt_cache_root)
    ):
        parser.error("--java, --sbt-launcher, --scalachess-root, and --sbt-cache-root are required")

    try:
        evidence = prepare_evidence_dir(args.evidence_dir)
    except NegativeFailure as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1

    expected = malformed_requests()
    failures: list[str] = []
    scala_export = None
    try:
      with tempfile.TemporaryDirectory(prefix="crazyhouse-reference-negative-") as temp_name, ExitStack() as stack:
        temp = Path(temp_name)
        requests = temp / "negative.jsonl"
        requests.write_text(
            "".join(
                item + "\n" if isinstance(item, str) else json.dumps(item, separators=(",", ":")) + "\n"
                for _, _, item in expected
            ),
            encoding="utf-8",
            newline="\n",
        )
        if evidence is not None:
            shutil.copyfile(requests, evidence / "requests.jsonl")
        commands: list[tuple[str, list[str], Path, Path, float, dict[str, str] | None]] = []
        python_output = temp / "python.jsonl"
        commands.append(
            (
                "python-chess",
                [
                    str(args.python_executable.resolve()),
                    str(PYTHON_ADAPTER),
                    "--require-root",
                    str(args.python_reference_root.resolve()),
                    "--input",
                    str(requests),
                    "--output",
                    str(python_output),
                ],
                ROOT,
                python_output,
                args.timeout,
                None,
            )
        )
        chessops_output = temp / "chessops.jsonl"
        commands.append(
            (
                "chessops",
                [
                    args.node,
                    str(CHESSOPS_ADAPTER),
                    "--require-root",
                    str(args.chessops_root.resolve()),
                    "--input",
                    str(requests),
                    "--output",
                    str(chessops_output),
                ],
                ROOT,
                chessops_output,
                args.timeout,
                None,
            )
        )
        if not args.skip_scalachess:
            scala_adapter_root = isolated_scalachess_adapter(temp)
            scala_export = stack.enter_context(
                authenticated_scalachess_export(
                    args.scalachess_root.resolve(),
                    temp / "scalachess-export-boundary",
                    args.git,
                )
            )
            print("SCALACHESS_EXPORT " + json.dumps(scala_export.evidence, sort_keys=True))
            cache = args.sbt_cache_root.resolve()
            scala_output = temp / "scalachess.jsonl"
            scala_env = os.environ.copy()
            scala_env["COURSIER_CACHE"] = str(cache / "coursier")
            commands.append(
                (
                    "scalachess",
                    [
                        str(args.java.resolve()),
                        "-Xms512m",
                        "-Xmx2048m",
                        "-Dsbt.task.cpus=2",
                        "-Dsbt.supershell=false",
                        "-Dsbt.log.noformat=true",
                        f"-Dscalachess.build.root={scala_export.build_root}",
                        f"-Dscalachess.identity.root={scala_export.identity_root}",
                        f"-Dsbt.global.base={cache / 'global'}",
                        f"-Dsbt.boot.directory={cache / 'boot'}",
                        f"-Dsbt.ivy.home={cache / 'ivy2'}",
                        "-jar",
                        str(args.sbt_launcher.resolve()),
                        f'runMain crazyhouse.reference.Main --input "{requests.as_posix()}" --output "{scala_output.as_posix()}"',
                    ],
                    scala_adapter_root,
                    scala_output,
                    args.scala_timeout,
                    scala_env,
                )
            )

        for name, command, cwd, output, timeout, env in commands:
            try:
                run_and_check(name, command, cwd, output, timeout, expected, evidence, env)
            except (NegativeFailure, json.JSONDecodeError) as exc:
                failures.append(str(exc))
                print(f"FAIL {exc}", file=sys.stderr)
    except ScalachessExportFailure as exc:
        failures.append(f"scalachess export boundary: {exc}")
        print(f"FAIL scalachess export boundary: {exc}", file=sys.stderr)

    if scala_export is not None:
        print(
            "SCALACHESS_EXPORT_CLEANUP "
            + json.dumps(
                {
                    "cleanup_verified": scala_export.cleanup_verified,
                    "identity_clean_after": scala_export.identity_clean_after,
                },
                sort_keys=True,
            )
        )

    if failures:
        print(f"FAIL {len(failures)} negative adapter contracts", file=sys.stderr)
        return 1
    mode = "three-reference" if not args.skip_scalachess else "two-reference development"
    print(f"PASS {mode} fail-closed adapter contract")
    if args.skip_scalachess:
        print("NON-GATE: --skip-scalachess cannot satisfy G4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
