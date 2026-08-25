#!/usr/bin/env python3
"""Verify the preregistered productive Crazyhouse NNUE V2 SSE2 backend."""

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
PREREG_RELATIVE = Path("tests/crazyhouse/p12-nnue-v2-productive-simd-v1.json")
ADDENDUM_RELATIVE = Path("tests/crazyhouse/p12-nnue-v2-productive-simd-v1.addendum.001.json")
PREREG_SHA256 = "eadefcd84b1e873600433e08e55723e296177edb6f2de72dc94d156d0b825020"
ADDENDUM_SHA256 = "3696b3f2f4e1e4231db281d087dd7f54b8007b17f9c95ea3423de5cbbd0274b7"
BACKEND = "sse2-x8-int16-to-int32"
HEX40 = re.compile(r"^[0-9a-f]{40}$")


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


def run_binary(binary: Path, arguments: Sequence[str], protocol: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(binary), *arguments],
        input=protocol,
        capture_output=True,
        timeout=240,
        env=scalar_verify.binary_environment(),
        check=False,
    )


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
        require(args.binary.is_file(), "productive test binary missing")
        require(HEX40.fullmatch(args.source_commit) is not None, "source commit identity")
        require(HEX40.fullmatch(args.source_tree) is not None, "source tree identity")
        require(HEX40.fullmatch(args.src_tree) is not None, "src tree identity")
        require(sha256_file(args.repo / PREREG_RELATIVE) == PREREG_SHA256, "preregistration pin")
        require(sha256_file(args.repo / ADDENDUM_RELATIVE) == ADDENDUM_SHA256, "addendum pin")
        require(
            sha256_file(args.repo / scalar_verify.MANIFEST_RELATIVE)
            == scalar_verify.MANIFEST_SHA256,
            "physical manifest pin",
        )
        require(
            sha256_file(args.repo / scalar_verify.CONFIG_RELATIVE) == scalar_verify.CONFIG_SHA256,
            "productive scalar contract pin",
        )
        source = (args.repo / "src/nnue/crazyhouse_v2_productive.cpp").read_text(encoding="utf-8")
        for marker in (
            "ProductiveNetworkV1::evaluate_simd",
            "_mm_loadu_si128",
            "_mm_unpacklo_epi16",
            "_mm_unpackhi_epi16",
            "_mm_add_epi32",
        ):
            require(marker in source, f"productive SSE2 source marker missing: {marker}")

        output.mkdir(parents=False, exist_ok=False)
        records, _manifest = scalar_verify.load_golden_records(args.repo)
        require(len(records) == 42, "physical golden count")
        provenance = scalar_verify.reference.ExpectedProvenance(
            bytes.fromhex(scalar_verify.MANIFEST_SHA256),
            bytes.fromhex(scalar_verify.CONFIG_SHA256),
        )
        network = scalar_verify.reference.synthetic_quantized_network(provenance)
        network_bytes = scalar_verify.reference.serialize_network(network)
        require(len(network_bytes) == 960324, "network byte count")
        network_path = output / "synthetic.nnuev2"
        network_path.write_bytes(network_bytes)
        protocol = b"".join(
            f"VALID\trecord-{index:02d}\t{record.hex()}\n".encode("ascii")
            for index, record in enumerate(records)
        )
        (output / "physical.protocol").write_bytes(protocol)
        base = [
            "--network",
            str(network_path),
            "--dataset-sha256",
            scalar_verify.MANIFEST_SHA256,
            "--training-config-sha256",
            scalar_verify.CONFIG_SHA256,
        ]

        selftest_runs = [run_binary(args.binary, [*base, "--simd-selftest"], b"") for _ in range(2)]
        for completed in selftest_runs:
            require(completed.returncode == 0, f"SIMD selftest exit: {completed.stderr!r}")
            require(completed.stderr == b"", "SIMD selftest stderr")
        require(selftest_runs[0].stdout == selftest_runs[1].stdout, "SIMD selftest replay bytes")
        expected_selftest_line = (
            f"SIMD_SELFTEST\tbackend={BACKEND}\tnegative_cases=6\tbias_evaluations=2"
            "\tsingle_row_evaluations=3608\tmaximum_active_evaluations=2"
            "\ttotal_evaluations=3612\ttrace_values=7866936"
        )
        require(
            selftest_runs[0].stdout.decode("ascii").splitlines() == [expected_selftest_line],
            "SIMD selftest protocol",
        )

        scalar_runs = [run_binary(args.binary, base, protocol) for _ in range(2)]
        simd_runs = [run_binary(args.binary, [*base, "--backend", "simd"], protocol) for _ in range(2)]
        for label, completed in (("scalar", scalar_runs[0]), ("scalar-replay", scalar_runs[1]),
                                 ("simd", simd_runs[0]), ("simd-replay", simd_runs[1])):
            require(completed.returncode == 0, f"{label} exit: {completed.stderr!r}")
            require(completed.stderr == b"", f"{label} stderr")
        require(scalar_runs[0].stdout == scalar_runs[1].stdout, "scalar replay bytes")
        require(simd_runs[0].stdout == simd_runs[1].stdout, "SIMD replay bytes")
        scalar_lines = scalar_runs[0].stdout.decode("utf-8").splitlines()
        simd_lines = simd_runs[0].stdout.decode("utf-8").splitlines()
        require(len(scalar_lines) == 85 and len(simd_lines) == 85, "physical protocol line count")
        require(scalar_lines[:-1] == simd_lines[:-1], "physical scalar/SIMD trace bytes")
        require(
            scalar_lines[-1]
            == "SUMMARY\trecords=42\tevaluations=84\ttransformer_lanes=512"
               "\tcontainer_bytes=960324\ttraining_admissible=false\tg12_closed=false",
            "scalar summary regression",
        )
        require(
            simd_lines[-1]
            == "SUMMARY\trecords=42\tevaluations=84\ttransformer_lanes=512"
               "\tcontainer_bytes=960324\ttraining_admissible=false\tg12_closed=false"
               f"\tbackend={BACKEND}\tscalar_simd_evaluations=84\ttrace_values=182952",
            "SIMD physical summary",
        )

        result = {
            "schema": "crazyhouse-p12-nnue-v2-productive-simd-verification/v1",
            "status": "PASS_ENGINEERING_ONLY",
            "source": {
                "commit": args.source_commit,
                "tree": args.source_tree,
                "src_tree": args.src_tree,
            },
            "pins": {
                "preregistration_sha256": PREREG_SHA256,
                "addendum_sha256": ADDENDUM_SHA256,
                "physical_manifest_sha256": scalar_verify.MANIFEST_SHA256,
                "scalar_contract_sha256": scalar_verify.CONFIG_SHA256,
            },
            "network": {
                "bytes": len(network_bytes),
                "sha256": sha256_bytes(network_bytes),
            },
            "backend": BACKEND,
            "negative_error_cases": 6,
            "bias_evaluations": 2,
            "single_row_evaluations": 3608,
            "maximum_active_evaluations": 2,
            "physical_golden_evaluations": 84,
            "total_scalar_simd_evaluations": 3696,
            "trace_values_per_evaluation": 2178,
            "total_trace_values_compared": 8049888,
            "replay": {
                "selftest_byte_equal": True,
                "scalar_byte_equal": True,
                "simd_byte_equal": True,
                "selftest_stdout_sha256": sha256_bytes(selftest_runs[0].stdout),
                "scalar_stdout_sha256": sha256_bytes(scalar_runs[0].stdout),
                "simd_stdout_sha256": sha256_bytes(simd_runs[0].stdout),
            },
            "boundaries": {
                "incremental_productive_proven": False,
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
            "PASS crazyhouse_v2_productive_simd_verify"
            f" result_sha256={sha256_bytes(result_bytes)} evaluations=3696"
            " trace_values=8049888"
        )
        return 0
    except (VerificationError, OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL crazyhouse_v2_productive_simd_verify: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
