#!/usr/bin/env python3
"""Independent negative-load and scalar parity suite for large Crazyhouse V2."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import mmap
import re
import struct
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "tests/crazyhouse/p12-nnue-v2-large-simd-incremental-v1.json"
PREREG_SHA256 = "f8c944b7d6b519f6272ead4dff46e04dc0fdf7318d2bbc9494fefd729d6788bf"
TRANSITIONS = ROOT / "tests/crazyhouse/p12-nnue-v2-simd-incremental-probe-v1.json"
TRANSITIONS_SHA256 = "1f93f28118478e46362b4254df7e2fa366b851f698f7c1075676a973f7e80a34"
HEX16 = re.compile(r"^[0-9a-f]{16}$")


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location("crazyhouse_v2_large_container_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load large-container reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(executable: Path, network: Path, expected_error: str | None = None) -> list[str]:
    command = [str(executable), str(network)]
    if expected_error is not None:
        command.extend(("--expect-error", expected_error))
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(
            f"verifier failed for {expected_error or 'positive'}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    if result.stderr:
        raise RuntimeError(f"verifier emitted stderr for {expected_error or 'positive'}: {result.stderr!r}")
    lines = result.stdout.splitlines()
    if not lines:
        raise RuntimeError("verifier emitted no stdout")
    return lines


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def authenticate_transition_contract() -> tuple[dict[str, object], str]:
    if sha256_file(PREREG) != PREREG_SHA256:
        raise RuntimeError("large SIMD/incremental preregistration pin mismatch")
    if sha256_file(TRANSITIONS) != TRANSITIONS_SHA256:
        raise RuntimeError("transition fixture pin mismatch")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    transitions = json.loads(TRANSITIONS.read_text(encoding="utf-8"))
    if prereg.get("status") != "PREREGISTERED_BEFORE_IMPLEMENTATION":
        raise RuntimeError("large SIMD/incremental preregistration status mismatch")
    counts = prereg.get("frozen_counts")
    if not isinstance(counts, dict) or counts.get("cases") != 13:
        raise RuntimeError("large SIMD/incremental frozen counts mismatch")
    cases = transitions.get("transition_cases")
    if not isinstance(cases, list) or len(cases) != 13:
        raise RuntimeError("transition fixture case count mismatch")
    lines: list[str] = []
    for case in cases:
        if not isinstance(case, dict) or case.get("mode") not in {"walk", "null"}:
            raise RuntimeError("transition fixture case framing mismatch")
        moves = case.get("moves")
        if not isinstance(moves, list):
            raise RuntimeError("transition fixture moves are not a list")
        lines.append(
            "\t".join(
                (
                    str(case["id"]),
                    str(case["mode"]),
                    str(case["fen"]),
                    " ".join(str(move) for move in moves),
                    str(case["expected_final_fen"]),
                )
            )
        )
    return prereg, "\n".join(lines) + "\n"


def run_transition(executable: Path, network: Path, protocol: str) -> str:
    result = subprocess.run(
        [str(executable), str(network), "--transition-suite"],
        input=protocol,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"transition verifier failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    if result.stderr:
        raise RuntimeError(f"transition verifier emitted stderr: {result.stderr!r}")
    lines = result.stdout.splitlines()
    if len(lines) != 1 or not lines[0].startswith("TRANSITIONS\t"):
        raise RuntimeError(f"transition verifier framing mismatch: {lines!r}")
    return lines[0]


def verify_transition_line(line: str, prereg: dict[str, object]) -> None:
    counts = prereg["frozen_counts"]
    if not isinstance(counts, dict):
        raise RuntimeError("frozen counts are not an object")
    tokens = dict(field.split("=", 1) for field in line.split("\t")[1:] if "=" in field)
    expected = {
        "backend": "sse2-x8-int16-to-int32",
        "cases": str(counts["cases"]),
        "moves": str(counts["real_moves"]),
        "undos": str(counts["real_undos"]),
        "nulls": str(counts["null_moves"]),
        "null_undos": str(counts["null_undos"]),
        "refreshes": str(counts["refreshes"]),
        "updates": str(counts["source_target_updates"]),
        "checkpoints": str(counts["position_checkpoints"]),
        "side_to_move_evaluations": str(counts["side_to_move_evaluations"]),
        "simd_trace_values": str(counts["scalar_simd_trace_values"]),
        "incremental_trace_values": str(counts["incremental_scalar_trace_values"]),
        "operation_negatives": str(counts["operation_negatives"]),
        "evaluation_negatives": str(counts["evaluation_negatives"]),
        "training_admissible": "false",
        "g12_closed": "false",
    }
    for key, value in expected.items():
        if tokens.get(key) != value:
            raise RuntimeError(f"transition token {key}: {tokens.get(key)!r} != {value!r}")
    if HEX16.fullmatch(tokens.get("digest", "")) is None:
        raise RuntimeError("transition digest framing mismatch")


def parse_csv(text: str) -> list[int]:
    return [int(value) for value in text.split(",")]


def parse_trace(line: str) -> tuple[str, int, dict[str, object]]:
    fields = line.split("\t")
    if len(fields) != 20 or fields[0] != "TRACE":
        raise RuntimeError(f"invalid trace framing: {line[:200]!r}")
    observed: dict[str, object] = {
        "bucket": int(fields[3]),
        "k": [parse_csv(fields[4]), parse_csv(fields[7])],
        "g": [parse_csv(fields[5]), parse_csv(fields[8])],
        "perspective": [parse_csv(fields[6]), parse_csv(fields[9])],
        "dense": parse_csv(fields[10]),
        "fc0": parse_csv(fields[11]),
        "fc0_squared": parse_csv(fields[12]),
        "fc0_clipped": parse_csv(fields[13]),
        "fc1": parse_csv(fields[14]),
        "fc1_squared": parse_csv(fields[15]),
        "fc1_clipped": parse_csv(fields[16]),
        "fc2": int(fields[17]),
        "fwd": int(fields[18]),
        "output": int(fields[19]),
    }
    return fields[1], 0 if fields[2] == "white" else 1, observed


def refinalize_file(path: Path, reference) -> None:
    with path.open("r+b") as stream:
        with mmap.mmap(stream.fileno(), 0) as mapped:
            payload = memoryview(mapped)[1_024:]
            digest = hashlib.sha256(payload).digest()
            del payload
            mapped[576:608] = digest
            mapped[608:612] = b"\0" * 4
            mapped[608:612] = struct.pack("<I", reference.crc32c(mapped[:1_024]))
            mapped.flush()


def mutate_and_reject(path: Path, executable: Path, offset: int, replacement: bytes,
                      expected: str, reference, refinalize: bool = False) -> None:
    with path.open("r+b") as stream:
        stream.seek(offset)
        original = stream.read(len(replacement))
        if len(original) != len(replacement):
            raise RuntimeError(f"mutation {expected} is outside the file")
        stream.seek(offset)
        stream.write(replacement)
        stream.flush()
    if refinalize:
        refinalize_file(path, reference)
    lines = run(executable, path, expected)
    if lines != [f"REJECT\t{expected}\tobject=false"]:
        raise RuntimeError(f"unexpected rejection output for {expected}: {lines!r}")
    with path.open("r+b") as stream:
        stream.seek(offset)
        stream.write(original)
        stream.flush()
    if refinalize:
        refinalize_file(path, reference)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    args = parser.parse_args()
    reference = load_reference(args.reference.resolve())
    executable = args.executable.resolve()
    if not executable.is_file():
        raise RuntimeError("large-network verifier executable is missing")
    prereg, transition_protocol = authenticate_transition_contract()

    with tempfile.TemporaryDirectory(prefix="crazyhouse-v2-large-") as temporary:
        root = Path(temporary)
        network = root / "fixture.nnue"
        reference.write_fixture_container(network)
        if network.stat().st_size != reference.FILE_BYTES:
            raise RuntimeError("fixture container size drifted")

        positive_lines = run(executable, network)
        trace_lines = [line for line in positive_lines if line.startswith("TRACE\t")]
        summary_lines = [line for line in positive_lines if line.startswith("SUMMARY\t")]
        if len(trace_lines) != 6 or len(summary_lines) != 1:
            raise RuntimeError("positive verifier count drifted")
        with network.open("rb") as stream:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                for line in trace_lines:
                    case_id, side_to_move, observed = parse_trace(line)
                    expected = reference.evaluate_reference(mapped, case_id, side_to_move)
                    if observed != expected:
                        differing = [key for key in expected if observed[key] != expected[key]]
                        raise RuntimeError(
                            f"scalar parity failed for {case_id}/{side_to_move}: {differing}"
                        )

        rejected = 0
        expected_lines = run(executable, network, "EXPECTED_PROVENANCE")
        if expected_lines != ["REJECT\tEXPECTED_PROVENANCE\tobject=false"]:
            raise RuntimeError("expected-provenance rejection drifted")
        rejected += 1

        short = root / "short.nnue"
        short.write_bytes(b"x")
        if run(executable, short, "WRONG_SIZE") != ["REJECT\tWRONG_SIZE\tobject=false"]:
            raise RuntimeError("wrong-size rejection drifted")
        rejected += 1

        mutations = (
            (0, b"X", "MAGIC"),
            (16, b"\0", "BYTE_ORDER"),
            (20, struct.pack("<H", 1000), "HEADER_SIZE"),
            (22, struct.pack("<H", 2), "VERSION"),
            (26, struct.pack("<H", 0), "FLAGS"),
            (28, struct.pack("<I", 0), "FILE_SIZE"),
            (32, struct.pack("<I", 0), "PAYLOAD_SIZE"),
            (36, struct.pack("<H", 9), "TENSOR_COUNT"),
            (38, struct.pack("<H", 7), "LAYER_STACKS"),
            (40, struct.pack("<I", 81_663), "K_DIMENSIONS"),
            (100, struct.pack("<H", 2), "TENSOR_TYPES"),
            (116, struct.pack("<I", 254), "TRANSFORM_CONSTANTS"),
            (124, struct.pack("<I", 5), "ACTIVATION_CONSTANTS"),
            (132, struct.pack("<I", 15), "OUTPUT_CONSTANTS"),
            (156, struct.pack("<I", 2), "INPUT_SEMANTICS"),
            (160, struct.pack("<I", 2), "PERSPECTIVE_ORDER"),
            (164, struct.pack("<I", 620), "DIRECTORY_LAYOUT"),
            (224, b"\0", "RULE_PROFILE_IDENTITY"),
            (384, b"\0" * 32, "DATASET_IDENTITY_ZERO"),
            (384, b"\x77" * 32, "DATASET_IDENTITY"),
            (176, b"\x01", "RESERVED_BYTES"),
            (624, struct.pack("<H", 99), "TENSOR_DIRECTORY"),
            (608, b"\0\0\0\0", "HEADER_CRC32C"),
            (reference.FILE_BYTES - 1, b"\x01", "PAYLOAD_SHA256"),
        )
        for offset, replacement, expected in mutations:
            mutate_and_reject(network, executable, offset, replacement, expected, reference)
            rejected += 1

        for offset, value, expected in (
            (reference.FC0_BIAS_OFFSET, 40_000, "FC0_INTERVAL"),
            (reference.FC1_BIAS_OFFSET, 40_000, "FC1_INTERVAL"),
            (reference.FC2_BIAS_OFFSET, 2_147_483_647, "FC2_INTERVAL"),
        ):
            mutate_and_reject(network, executable, offset, struct.pack("<i", value), expected,
                              reference, refinalize=True)
            rejected += 1

        first_transition = run_transition(executable, network, transition_protocol)
        second_transition = run_transition(executable, network, transition_protocol)
        if first_transition != second_transition:
            raise RuntimeError("transition trace digest is not deterministic")
        verify_transition_line(first_transition, prereg)

        print(
            "PASS crazyhouse_v2_large_container"
            f" positive_cases={len(trace_lines)} negative_cases={rejected}"
            f" container_bytes={reference.FILE_BYTES} scalar_reference=independent"
            " simd_backend=sse2-x8-int16-to-int32 transition_cases=13"
            " transition_evaluations=98 simd_trace_values=420616"
            " incremental_trace_values=420616"
            " training_admissible=false g12_closed=false"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
