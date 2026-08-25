#!/usr/bin/env python3
"""Run the pinned Crazyhouse corpus through all three reference adapters."""

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
DEFAULT_CASES = ROOT / "tests" / "crazyhouse" / "reference-cases.json"
PYTHON_ADAPTER = ROOT / "tools" / "references" / "python_chess_adapter.py"
CHESSOPS_ADAPTER = ROOT / "tools" / "references" / "chessops_adapter.mjs"
SCALACHESS_ADAPTER = ROOT / "tools" / "references" / "scalachess-adapter"
SCALACHESS_ADAPTER_IGNORES = ("target", ".bsp", ".metals", ".scala-build")
EXPECTED_IDENTITIES = {
    "python-chess": ("9c24454dcea4f8a30259d811a2f10b26e911deb4", "33627273cd58c1a5a20c3132548e5df7b85ff9d6"),
    "chessops": ("736c40ced7130d453d85e7979c360b797474c9a7", "d555da3d103eef217c7a894e7a994c4f55313a42"),
    "scalachess": ("cbffc9d7e2c6f8ba33381c5403e1b4f992199626", "f5410eb2a6ddb6ef7092317533f704158c86a4fc"),
}


class ContractFailure(RuntimeError):
    pass


def isolated_scalachess_adapter(temp: Path) -> Path:
    if not SCALACHESS_ADAPTER.is_dir():
        raise ContractFailure(f"scalachess adapter source is missing: {SCALACHESS_ADAPTER}")
    destination = temp / "scalachess-adapter"
    try:
        shutil.copytree(
            SCALACHESS_ADAPTER,
            destination,
            ignore=shutil.ignore_patterns(*SCALACHESS_ADAPTER_IGNORES),
        )
    except OSError as exc:
        raise ContractFailure(f"failed to isolate scalachess adapter source: {exc}") from exc
    required = (
        destination / "build.sbt",
        destination / "project" / "build.properties",
        destination / "src" / "main" / "scala" / "crazyhouse" / "reference" / "Main.scala",
    )
    missing = [str(path.relative_to(destination)) for path in required if not path.is_file()]
    if missing:
        raise ContractFailure(f"isolated scalachess adapter is incomplete: {missing}")
    return destination


def prepare_evidence_dir(requested: Path | None) -> Path | None:
    if requested is None:
        return None
    evidence = requested.resolve()
    if evidence.exists():
        raise ContractFailure(f"evidence directory already exists: {evidence}")
    if not evidence.parent.is_dir():
        raise ContractFailure(f"evidence parent directory is missing: {evidence.parent}")
    evidence.mkdir()
    return evidence


def retain_adapter_evidence(
    evidence: Path | None,
    name: str,
    output_path: Path,
    responses: list[dict[str, Any]],
) -> None:
    if evidence is None:
        return
    shutil.copyfile(output_path, evidence / f"{name}.raw.jsonl")
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


def request_for(case: dict[str, Any], profile: str) -> dict[str, Any]:
    request = {
        "schema": "crazyhouse-reference-request/v1",
        "authority_profile": profile,
        "id": case["id"],
        "op": case["op"],
        "fen": case["fen"],
    }
    if "moves" in case:
        request["moves"] = case["moves"]
    if "depth" in case:
        request["depth"] = case["depth"]
    return request


def run_adapter(
    name: str,
    command: list[str],
    cwd: Path,
    input_path: Path,
    output_path: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
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
    except subprocess.TimeoutExpired as exc:
        raise ContractFailure(f"{name}: timed out after {timeout}s") from exc
    except OSError as exc:
        raise ContractFailure(f"{name}: launch failed: {exc}") from exc

    if completed.returncode != 0:
        diagnostics = completed.stdout + "\n" + completed.stderr
        output_diagnostics = ""
        if output_path.is_file():
            output_diagnostics = "\nJSONL output:\n" + output_path.read_text(
                encoding="utf-8", errors="replace"
            )
        raise ContractFailure(
            f"{name}: exited {completed.returncode}\n{diagnostics}{output_diagnostics}"
        )
    if not output_path.is_file():
        diagnostics = completed.stdout + "\n" + completed.stderr
        raise ContractFailure(f"{name}: produced no JSONL output\n{diagnostics}")

    responses: list[dict[str, Any]] = []
    for line_number, line in enumerate(output_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractFailure(f"{name}: invalid JSON at output line {line_number}: {exc}") from exc
        if not isinstance(response, dict):
            raise ContractFailure(f"{name}: output line {line_number} is not an object")
        responses.append(response)
    return responses


def response_map(
    expected_ids: list[str], responses: list[dict[str, Any]], expected_name: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if len(responses) != len(expected_ids):
        raise ContractFailure(f"{expected_name}: expected {len(expected_ids)} responses, got {len(responses)}")
    actual_ids = [response.get("id") for response in responses]
    if actual_ids != expected_ids:
        raise ContractFailure(f"{expected_name}: response IDs differ\nexpected={expected_ids}\nactual={actual_ids}")
    if any(response.get("ok") is not True for response in responses):
        failures = [response for response in responses if response.get("ok") is not True]
        raise ContractFailure(f"{expected_name}: adapter returned errors: {json.dumps(failures, sort_keys=True)}")

    identities = [response.get("implementation") for response in responses]
    if not all(isinstance(identity, dict) for identity in identities):
        raise ContractFailure(f"{expected_name}: missing implementation identity")
    identity = identities[0]
    if identity.get("name") != expected_name or any(item != identity for item in identities[1:]):
        raise ContractFailure(f"{expected_name}: identity changed between responses")
    commit, tree = EXPECTED_IDENTITIES[expected_name]
    if identity.get("commit") != commit or identity.get("tree") != tree:
        raise ContractFailure(f"{expected_name}: unexpected commit/tree identity")
    return dict(zip(expected_ids, responses, strict=True)), identity


def assert_subset(actual: Any, expected: Any, context: str) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ContractFailure(f"{context}: expected object, got {type(actual).__name__}")
        for key, value in expected.items():
            if key not in actual:
                raise ContractFailure(f"{context}: missing key {key!r}")
            assert_subset(actual[key], value, f"{context}.{key}")
    elif actual != expected:
        raise ContractFailure(f"{context}: expected {expected!r}, got {actual!r}")


def state_for(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    key = "root" if case["op"] == "perft" else "state"
    state = response.get(key)
    if not isinstance(state, dict):
        raise ContractFailure(f"{case['id']}: missing {key} state")
    return state


def validate_expected(
    implementation: str,
    case: dict[str, Any],
    response: dict[str, Any],
) -> None:
    expected = case.get("expected", {})
    state = state_for(case, response)
    direct_state_keys = {
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
    for key in direct_state_keys & expected.keys():
        assert_subset(state.get(key), expected[key], f"{implementation}:{case['id']}.state.{key}")

    legal_moves = state.get("legal_moves")
    if not isinstance(legal_moves, list) or legal_moves != sorted(set(legal_moves)):
        raise ContractFailure(f"{implementation}:{case['id']}: legal_moves is not a sorted unique array")
    if "legal_moves_exact" in expected and legal_moves != expected["legal_moves_exact"]:
        raise ContractFailure(
            f"{implementation}:{case['id']}: expected exact legal moves {expected['legal_moves_exact']}, got {legal_moves}"
        )
    missing = sorted(set(expected.get("legal_must_include", [])) - set(legal_moves))
    forbidden = sorted(set(expected.get("legal_must_exclude", [])) & set(legal_moves))
    if missing or forbidden:
        raise ContractFailure(f"{implementation}:{case['id']}: missing={missing}, forbidden-present={forbidden}")
    for prefix in expected.get("legal_forbidden_prefixes", []):
        prefixed = [move for move in legal_moves if move.startswith(prefix)]
        if prefixed:
            raise ContractFailure(f"{implementation}:{case['id']}: forbidden prefix {prefix!r}: {prefixed}")

    if "nodes" in expected and response.get("nodes") != expected["nodes"]:
        raise ContractFailure(
            f"{implementation}:{case['id']}: expected {expected['nodes']} nodes, got {response.get('nodes')}"
        )
    terminal_by_implementation = expected.get("terminal_by_implementation", {})
    if implementation in terminal_by_implementation:
        assert_subset(
            state.get("terminal"),
            terminal_by_implementation[implementation],
            f"{implementation}:{case['id']}.state.terminal",
        )
    state_by_implementation = expected.get("state_by_implementation", {}).get(implementation)
    if state_by_implementation is not None:
        assert_subset(
            state,
            state_by_implementation,
            f"{implementation}:{case['id']}.state",
        )
    diagnostics = expected.get("diagnostics_by_implementation", {}).get(implementation)
    if diagnostics is not None:
        assert_subset(
            state.get("native_diagnostics"),
            diagnostics,
            f"{implementation}:{case['id']}.state.native_diagnostics",
        )


def compare_case(
    case: dict[str, Any],
    maps: dict[str, dict[str, dict[str, Any]]],
    default_fields: list[str],
) -> None:
    fields = case.get("cross_fields", default_fields)
    states = {
        implementation: state_for(case, responses[case["id"]])
        for implementation, responses in maps.items()
    }
    baseline_name = "scalachess" if "scalachess" in states else sorted(states)[0]
    baseline = states[baseline_name]
    for field in fields:
        if field not in baseline:
            raise ContractFailure(f"{case['id']}: {baseline_name} lacks cross field {field}")
        for implementation, state in states.items():
            if state.get(field) != baseline[field]:
                raise ContractFailure(
                    f"{case['id']}:{field}: {implementation} differs from {baseline_name}\n"
                    f"{baseline_name}={baseline[field]!r}\n{implementation}={state.get(field)!r}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
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
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--scala-timeout", type=float, default=900.0)
    args = parser.parse_args()

    if not args.skip_scalachess:
        missing = [
            name
            for name, value in {
                "--java": args.java,
                "--sbt-launcher": args.sbt_launcher,
                "--scalachess-root": args.scalachess_root,
                "--sbt-cache-root": args.sbt_cache_root,
            }.items()
            if value is None
        ]
        if missing:
            parser.error(f"required unless --skip-scalachess: {', '.join(missing)}")

    try:
        evidence = prepare_evidence_dir(args.evidence_dir)
    except ContractFailure as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1

    corpus = json.loads(args.cases.read_text(encoding="utf-8"))
    profile = corpus["authority_profile"]
    cases = corpus["cases"]
    capabilities_id = "CH-REF-CAPABILITIES"
    requests = [
        {
            "schema": "crazyhouse-reference-request/v1",
            "authority_profile": profile,
            "id": capabilities_id,
            "op": "capabilities",
        },
        *(request_for(case, profile) for case in cases),
    ]
    expected_ids = [request["id"] for request in requests]
    failures: list[str] = []
    maps: dict[str, dict[str, dict[str, Any]]] = {}
    identities: dict[str, dict[str, Any]] = {}

    scala_export = None
    try:
      with tempfile.TemporaryDirectory(prefix="crazyhouse-reference-") as temp_name, ExitStack() as stack:
        temp = Path(temp_name)
        input_path = temp / "requests.jsonl"
        input_path.write_text(
            "".join(json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n" for request in requests),
            encoding="utf-8",
            newline="\n",
        )
        if evidence is not None:
            shutil.copyfile(input_path, evidence / "requests.jsonl")
        adapters: list[tuple[str, list[str], Path, Path, float, dict[str, str] | None]] = [
            (
                "python-chess",
                [
                    str(args.python_executable.resolve()),
                    str(PYTHON_ADAPTER),
                    "--require-root",
                    str(args.python_reference_root.resolve()),
                    "--input",
                    str(input_path),
                    "--output",
                    str(temp / "python-chess.jsonl"),
                ],
                ROOT,
                temp / "python-chess.jsonl",
                args.timeout,
                None,
            ),
            (
                "chessops",
                [
                    args.node,
                    str(CHESSOPS_ADAPTER),
                    "--require-root",
                    str(args.chessops_root.resolve()),
                    "--input",
                    str(input_path),
                    "--output",
                    str(temp / "chessops.jsonl"),
                ],
                ROOT,
                temp / "chessops.jsonl",
                args.timeout,
                None,
            ),
        ]
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
            cache_root = args.sbt_cache_root.resolve()
            scala_output = temp / "scalachess.jsonl"
            sbt_command = (
                "clean; runMain crazyhouse.reference.Main "
                f'--input "{input_path.as_posix()}" --output "{scala_output.as_posix()}"'
            )
            scala_env = os.environ.copy()
            scala_env["COURSIER_CACHE"] = str(cache_root / "coursier")
            adapters.append(
                (
                    "scalachess",
                    [
                        str(args.java.resolve()),
                        "-Xms512m",
                        "-Xmx2048m",
                        "-Dsbt.task.cpus=2",
                        "-Dsbt.coursier.parallel-downloads=4",
                        "-Dsbt.supershell=false",
                        "-Dsbt.log.noformat=true",
                        f"-Dscalachess.build.root={scala_export.build_root}",
                        f"-Dscalachess.identity.root={scala_export.identity_root}",
                        f"-Dsbt.global.base={cache_root / 'global'}",
                        f"-Dsbt.boot.directory={cache_root / 'boot'}",
                        f"-Dsbt.ivy.home={cache_root / 'ivy2'}",
                        "-jar",
                        str(args.sbt_launcher.resolve()),
                        sbt_command,
                    ],
                    scala_adapter_root,
                    scala_output,
                    args.scala_timeout,
                    scala_env,
                )
            )

        for name, command, cwd, output_path, timeout, env in adapters:
            try:
                responses = run_adapter(name, command, cwd, input_path, output_path, timeout, env)
                retain_adapter_evidence(evidence, name, output_path, responses)
                mapped, identity = response_map(expected_ids, responses, name)
                maps[name] = mapped
                identities[name] = identity
                capabilities = mapped[capabilities_id].get("capabilities", {})
                if capabilities.get("operations") != ["capabilities", "inspect", "transition", "perft"]:
                    raise ContractFailure(f"{name}: unexpected capability operations")
                print(f"PASS {name} identity and {len(cases)} cases")
            except ContractFailure as exc:
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
        print(f"FAIL {len(failures)} adapter launch/identity failures", file=sys.stderr)
        return 1

    for implementation, responses in maps.items():
        for case in cases:
            try:
                validate_expected(implementation, case, responses[case["id"]])
            except ContractFailure as exc:
                failures.append(str(exc))
                print(f"FAIL {exc}", file=sys.stderr)

    default_fields = corpus["default_cross_fields"]
    for case in cases:
        try:
            compare_case(case, maps, default_fields)
        except ContractFailure as exc:
            failures.append(str(exc))
            print(f"FAIL {exc}", file=sys.stderr)

    if failures:
        print(f"FAIL {len(failures)} of {len(cases) * (len(maps) + 1)} checks", file=sys.stderr)
        return 1
    mode = "official three-reference" if "scalachess" in maps else "development two-reference"
    print(f"PASS {mode} Crazyhouse differential corpus: {len(cases)} cases")
    if "scalachess" not in maps:
        print("NON-GATE: --skip-scalachess cannot satisfy G4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
