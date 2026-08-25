#!/usr/bin/env python3
"""Verify the preregistered productive Crazyhouse NNUE V2 accumulator."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Sequence

import crazyhouse_v2_productive_verify as scalar_verify


ROOT = Path(__file__).resolve().parents[1]
PREREG_RELATIVE = Path("tests/crazyhouse/p12-nnue-v2-productive-incremental-v1.json")
ADDENDUM_RELATIVE = Path(
    "tests/crazyhouse/p12-nnue-v2-productive-incremental-v1.addendum.001.json"
)
TRANSITION_RELATIVE = Path("tests/crazyhouse/p12-nnue-v2-simd-incremental-probe-v1.json")
PREREG_SHA256 = "9d34b494aea9715fed462e3610380c983f5a98c18aa685c7e6eebe30c07b2973"
ADDENDUM_SHA256 = "e563ab89f602a96520e51349ff2bf6dc6b697665d511469d5d2ab9613b90b39b"
TRANSITION_SHA256 = "1f93f28118478e46362b4254df7e2fa366b851f698f7c1075676a973f7e80a34"
BACKEND = "sse2-x8-int16-to-int32"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def fixture_stream(transition: dict[str, Any]) -> bytes:
    lines: list[str] = []
    for case in transition["transition_cases"]:
        require(case["mode"] in {"walk", "null"}, f"unknown mode for {case['id']}")
        if case["mode"] == "null":
            require(not case["moves"], f"null case {case['id']} has moves")
        lines.append(
            "\t".join(
                (
                    case["id"],
                    case["mode"],
                    case["fen"],
                    " ".join(case["moves"]),
                    case["expected_final_fen"],
                )
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def authenticate(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    prereg_path = repo / PREREG_RELATIVE
    addendum_path = repo / ADDENDUM_RELATIVE
    transition_path = repo / TRANSITION_RELATIVE
    require(sha256_file(prereg_path) == PREREG_SHA256, "preregistration pin")
    require(sha256_file(addendum_path) == ADDENDUM_SHA256, "expected-red addendum pin")
    require(sha256_file(transition_path) == TRANSITION_SHA256, "transition contract pin")
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    addendum = json.loads(addendum_path.read_text(encoding="utf-8"))
    transition = json.loads(transition_path.read_text(encoding="utf-8"))
    require(
        prereg["schema"]
        == "crazyhouse-p12-nnue-v2-productive-incremental-preregistration/v1",
        "preregistration schema",
    )
    require(prereg["status"] == "PREREGISTERED_BEFORE_IMPLEMENTATION", "preregistration status")
    require(
        addendum["status"] == "EXPECTED_RED_AUTHENTICATED_IMPLEMENTATION_AUTHORIZED",
        "expected-red status",
    )
    require(addendum["implementation_transition"]["implementation_may_begin"], "implementation gate")
    expected = prereg["transition_matrix"]
    require(len(transition["transition_cases"]) == expected["cases"] == 13, "case count")
    require(
        len({case["id"] for case in transition["transition_cases"]}) == expected["cases"],
        "duplicate transition id",
    )
    require(expected["trace_values_per_evaluation"] == 2178, "trace width")
    require(expected["scalar_simd_trace_values"] == 213444, "SIMD trace contract")
    require(expected["incremental_scalar_trace_values"] == 213444, "incremental trace contract")
    require(prereg["negative_matrix"]["operation_failures"] == 10, "operation negative contract")
    require(prereg["negative_matrix"]["evaluation_failures"] == 4, "evaluation negative contract")
    for identity in (
        prereg["frozen_source"]["commit"],
        prereg["frozen_source"]["tree"],
        prereg["frozen_source"]["src_tree"],
    ):
        require(HEX40.fullmatch(identity) is not None, "frozen source identity")
    source = (repo / "src/nnue/crazyhouse_v2_productive.cpp").read_text(encoding="utf-8")
    header = (repo / "src/nnue/crazyhouse_v2_productive.h").read_text(encoding="utf-8")
    for marker in (
        "ProductiveAccumulatorV1::refresh",
        "ProductiveAccumulatorV1::update",
        "ProductiveAccumulatorV1::evaluate",
        "make_productive_membership",
        "evaluate_from_transformers",
    ):
        require(marker in source, f"productive accumulator source marker missing: {marker}")
    require("class ProductiveAccumulatorV1" in header, "productive accumulator declaration")
    return prereg, transition


def run_binary(binary: Path, arguments: Sequence[str], protocol: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(binary), *arguments],
        input=protocol,
        capture_output=True,
        timeout=240,
        env=scalar_verify.binary_environment(),
        check=False,
    )


def parse_output(payload: bytes, expected: dict[str, Any], negative: dict[str, Any]) -> dict[str, str]:
    lines = payload.decode("ascii").splitlines()
    require(len(lines) == 1, "fixture did not emit exactly one semantic line")
    line = lines[0]
    require(line.startswith("PASS crazyhouse_v2_productive_incremental "), "PASS prefix")
    tokens = dict(token.split("=", 1) for token in line.split()[2:] if "=" in token)
    required = {
        "backend": BACKEND,
        "cases": str(expected["cases"]),
        "moves": str(expected["real_moves"]),
        "undos": str(expected["real_undos"]),
        "nulls": str(expected["null_moves"]),
        "null_undos": str(expected["null_undos"]),
        "refreshes": str(expected["refreshes"]),
        "updates": str(expected["source_target_updates"]),
        "checkpoints": str(expected["position_checkpoints"]),
        "side_to_move_evaluations": str(expected["side_to_move_evaluations"]),
        "simd_trace_values": str(expected["scalar_simd_trace_values"]),
        "incremental_trace_values": str(expected["incremental_scalar_trace_values"]),
        "operation_negatives": str(negative["operation_failures"]),
        "evaluation_negatives": str(negative["evaluation_failures"]),
        "training_admissible": "false",
        "g12_closed": "false",
    }
    for key, value in required.items():
        require(tokens.get(key) == value, f"output token {key}: {tokens.get(key)} != {value}")
    require(HEX16.fullmatch(tokens.get("digest", "")) is not None, "trace digest")
    return tokens


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--src-tree", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output_dir.resolve()
    try:
        require(not output.exists(), "exclusive output directory already exists")
        require(args.binary.is_file(), "productive incremental test binary missing")
        for identity in (args.source_commit, args.source_tree, args.src_tree):
            require(HEX40.fullmatch(identity) is not None, "tested source identity")
        prereg, transition = authenticate(args.repo)
        require(
            sha256_file(args.repo / scalar_verify.MANIFEST_RELATIVE)
            == scalar_verify.MANIFEST_SHA256,
            "physical manifest pin",
        )
        require(
            sha256_file(args.repo / scalar_verify.CONFIG_RELATIVE) == scalar_verify.CONFIG_SHA256,
            "productive scalar contract pin",
        )

        output.mkdir(parents=False, exist_ok=False)
        provenance = scalar_verify.reference.ExpectedProvenance(
            bytes.fromhex(scalar_verify.MANIFEST_SHA256),
            bytes.fromhex(scalar_verify.CONFIG_SHA256),
        )
        network = scalar_verify.reference.synthetic_quantized_network(provenance)
        network_bytes = scalar_verify.reference.serialize_network(network)
        require(len(network_bytes) == 960324, "network byte count")
        network_path = output / "synthetic.nnuev2"
        network_path.write_bytes(network_bytes)
        protocol = fixture_stream(transition)
        (output / "transitions.protocol").write_bytes(protocol)
        base = [
            "--network",
            str(network_path),
            "--dataset-sha256",
            scalar_verify.MANIFEST_SHA256,
            "--training-config-sha256",
            scalar_verify.CONFIG_SHA256,
        ]
        runs = [run_binary(args.binary, base, protocol) for _ in range(2)]
        for label, completed in (("first", runs[0]), ("replay", runs[1])):
            require(completed.returncode == 0, f"{label} exit: {completed.stderr!r}")
            require(completed.stderr == b"", f"{label} stderr")
        require(runs[0].stdout == runs[1].stdout, "fixture replay bytes")
        tokens = parse_output(
            runs[0].stdout,
            prereg["transition_matrix"],
            prereg["negative_matrix"],
        )

        result = {
            "schema": "crazyhouse-p12-nnue-v2-productive-incremental-verification/v1",
            "status": "PASS_ENGINEERING_ONLY",
            "source": {
                "commit": args.source_commit,
                "tree": args.source_tree,
                "src_tree": args.src_tree,
            },
            "pins": {
                "preregistration_sha256": PREREG_SHA256,
                "expected_red_addendum_sha256": ADDENDUM_SHA256,
                "transition_contract_sha256": TRANSITION_SHA256,
                "physical_manifest_sha256": scalar_verify.MANIFEST_SHA256,
                "scalar_contract_sha256": scalar_verify.CONFIG_SHA256,
            },
            "network": {
                "bytes": len(network_bytes),
                "sha256": sha256_bytes(network_bytes),
            },
            "backend": BACKEND,
            "matrix": {
                "cases": 13,
                "moves": 17,
                "undos": 17,
                "null_moves": 1,
                "null_undos": 1,
                "refreshes": 13,
                "source_target_updates": 36,
                "position_checkpoints": 49,
                "side_to_move_evaluations": 98,
                "trace_values_per_evaluation": 2178,
                "scalar_simd_trace_values": 213444,
                "incremental_scalar_trace_values": 213444,
                "total_trace_values_compared": 426888,
                "operation_negatives": 10,
                "evaluation_negatives": 4,
            },
            "replay": {
                "byte_equal": True,
                "stdout_bytes": len(runs[0].stdout),
                "stdout_sha256": sha256_bytes(runs[0].stdout),
                "transition_stream_bytes": len(protocol),
                "transition_stream_sha256": sha256_bytes(protocol),
                "trace_digest": tokens["digest"],
            },
            "boundaries": {
                "simd_productive_proven": True,
                "incremental_productive_proven": True,
                "sanitizer_productive_proven": False,
                "training_admissible": False,
                "model_selection_credit": False,
                "strength_credit": False,
                "g12_closed": False,
                "openbench_used": False,
                "release_credit": False,
                "legacy_v1_remains_default": True,
            },
        }
        result_bytes = canonical_json(result)
        (output / "verification.json").write_bytes(result_bytes)
        print(
            "PASS crazyhouse_v2_productive_incremental_verify"
            f" result_sha256={sha256_bytes(result_bytes)}"
            " evaluations=98 trace_values=426888"
        )
        return 0
    except (
        VerificationError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"FAIL crazyhouse_v2_productive_incremental_verify: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
