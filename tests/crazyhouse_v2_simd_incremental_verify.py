#!/usr/bin/env python3
"""Authenticate and replay the frozen Crazyhouse V2 SIMD/incremental probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "nnue"
sys.path.insert(0, str(TOOLS))

import crazyhouse_v2_trainer_reference as trainer  # noqa: E402


PREREG = ROOT / "tests" / "crazyhouse" / "p12-nnue-v2-simd-incremental-probe-v1.json"
EXPECTED_ARTIFACT_SHA = "fdd55e1a6af735cf1e999af31341c249c52f444f553454606195124a34b07d12"
EXPECTED_DEPENDENCIES = {
    "tests/crazyhouse/p12-nnue-v2-container-scalar-probe-v1.json":
        "3610b737bd5396c64a88450590b77b80b48cd052b72e8622b2e02ec7fa1c93c4",
    "tests/crazyhouse/p12-nnue-v2-container-scalar-probe-v1.result.001.json":
        "722faae7e0a71da33da12131af1bd60c67b1050a93d34fd07b217239dec1248d",
    "tests/crazyhouse/legacy-incremental-cases-v1.json":
        "ba18990faaadcb4fe92b87f8396441f249cf31c1cc6bc98d8912af0a04aa841b",
    "tests/crazyhouse/reference-cases.json":
        "4a00bca20d3b149b5bbe3f4153a4a3ff5a20473126763c2d8125a4ba2d11742e",
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def authenticate() -> dict:
    contract = json.loads(PREREG.read_text(encoding="utf-8"))
    require(contract["schema"] == "crazyhouse-nnue-v2-simd-incremental-probe/v1",
            "preregistration schema drifted")
    require(contract["status"] == "FROZEN_BEFORE_IMPLEMENTATION",
            "preregistration status drifted")
    require(contract["simd_contract"]["required_backend"] == "sse2-x16-scalar-tail1",
            "SIMD backend contract drifted")
    for relative, expected in EXPECTED_DEPENDENCIES.items():
        path = ROOT / relative
        require(path.is_file(), f"bound dependency is missing: {relative}")
        require(sha256(path.read_bytes()) == expected, f"bound dependency drifted: {relative}")
    cases = contract["transition_cases"]
    require(len(cases) == contract["expected_counts"]["cases"] == 13,
            "transition case count drifted")
    require(len({case["id"] for case in cases}) == len(cases), "duplicate transition id")
    return contract


def fixture_stream(contract: dict) -> bytes:
    lines = []
    for case in contract["transition_cases"]:
        require(case["mode"] in {"walk", "null"}, f"unknown mode for {case['id']}")
        if case["mode"] == "null":
            require(not case["moves"], f"null case {case['id']} has moves")
        lines.append("\t".join((
            case["id"],
            case["mode"],
            case["fen"],
            " ".join(case["moves"]),
            case["expected_final_fen"],
        )))
    return ("\n".join(lines) + "\n").encode("utf-8")


def run_once(fixture: Path, artifact: Path, stream: bytes) -> bytes:
    completed = subprocess.run(
        [str(fixture), str(artifact)],
        input=stream,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(completed.returncode == 0,
            f"fixture exited {completed.returncode}: {completed.stderr.decode('utf-8', 'replace')}")
    require(completed.stderr == b"", "fixture wrote stderr on a passing run")
    require(completed.stdout.count(b"\n") == 1, "fixture did not emit one normalized line")
    return completed.stdout


def verify_output(output: bytes, expected: dict) -> None:
    text = output.decode("ascii").strip()
    require(text.startswith("PASS crazyhouse_v2_simd_incremental "), "PASS prefix drifted")
    required = {
        "backend": "sse2-x16-scalar-tail1",
        "cases": str(expected["cases"]),
        "moves": str(expected["real_moves"]),
        "undos": str(expected["real_undos"]),
        "nulls": str(expected["null_moves"]),
        "null_undos": str(expected["null_undos"]),
        "checkpoints": str(expected["position_checkpoints"]),
        "perspective_checkpoints": str(expected["perspective_checkpoints"]),
        "simd_transition_lanes": str(expected["simd_transition_lane_comparisons"]),
        "incremental_lanes": str(expected["incremental_lane_comparisons"]),
        "single_row_simd_lanes": str(expected["single_row_simd_lane_comparisons"]),
        "bias_simd_lanes": "34",
        "multirow_simd_lanes": "102",
        "feature_negatives": "6",
        "accumulator_negatives": "5",
        "training_admissible": "false",
        "g12_closed": "false",
    }
    tokens = dict(token.split("=", 1) for token in text.split()[2:] if "=" in token)
    for key, value in required.items():
        require(tokens.get(key) == value,
                f"output token {key} drifted: expected {value}, got {tokens.get(key)}")
    require(len(tokens.get("digest", "")) == 16, "trace digest is not 64-bit hexadecimal")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    require(args.fixture.is_file(), "fixture executable is missing")

    contract = authenticate()
    stream = fixture_stream(contract)
    artifact = trainer.synthetic_network_bytes()
    require(len(artifact) == 30_992, "synthetic artifact byte count drifted")
    require(sha256(artifact) == EXPECTED_ARTIFACT_SHA, "synthetic artifact identity drifted")

    with tempfile.TemporaryDirectory(prefix="crazyhouse-v2-simd-incremental-") as directory:
        artifact_path = Path(directory) / "synthetic.chn2p"
        artifact_path.write_bytes(artifact)
        first = run_once(args.fixture, artifact_path, stream)
        second = run_once(args.fixture, artifact_path, stream)

    require(first == second, "two-run normalized output is not byte-identical")
    verify_output(first, contract["expected_counts"])
    print(
        "PASS crazyhouse_v2_simd_incremental_verify "
        f"artifact_sha256={sha256(artifact)} stream_sha256={sha256(stream)} "
        f"protocol_sha256={sha256(first)} two_run_byte_identical=true "
        "training_admissible=false g12_closed=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, trainer.ReferenceError) as exc:
        print(f"FAIL crazyhouse_v2_simd_incremental_verify: {exc}", file=sys.stderr)
        raise SystemExit(1)
