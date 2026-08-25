#!/usr/bin/env python3
"""Cross-check the independent trainer reference and C++ scalar probe."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import crazyhouse_physical_v1_unit as physical_goldens  # noqa: E402


def load_trainer_reference():
    path = ROOT / "tools" / "nnue" / "crazyhouse_v2_trainer_reference.py"
    spec = importlib.util.spec_from_file_location("crazyhouse_v2_trainer_reference", path)
    if spec is None or spec.loader is None:
        raise VerificationError("cannot load independent trainer reference")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trainer = load_trainer_reference()
codec = physical_goldens.codec

INPUT_IDENTITIES = {
    ROOT / "schemas" / "crazyhouse-physical-v1.schema.json": (
        18_729,
        "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55",
    ),
    ROOT / "schemas" / "crazyhouse-nnue-v2-features-v1.json": (
        3_844,
        "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6",
    ),
    ROOT / "schemas" / "crazyhouse-nnue-v2-scalar-probe-container-v1.json": (
        5_984,
        "5fe00bb91876650fb768c6b8bc80eacbb1ca2a16f631c528f803dcc8965ec7a3",
    ),
    ROOT / "tests" / "crazyhouse" / "data" / "crazyhouse-physical-v1-goldens.json": (
        8_383,
        "94cd50961d8e51478e55a82cd4e0770d418a30483b3c5d120a470f7eb2efccac",
    ),
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def authenticate_inputs() -> None:
    for path, (expected_bytes, expected_sha) in INPUT_IDENTITIES.items():
        payload = path.read_bytes()
        require(len(payload) == expected_bytes, f"{path.name}: byte identity drifted")
        require(sha256(payload) == expected_sha, f"{path.name}: SHA-256 identity drifted")
    require(
        physical_goldens.__file__ is not None,
        "physical golden generator has no source identity",
    )
    require(
        Path(trainer.__file__).resolve().parent == (ROOT / "tools" / "nnue").resolve(),
        "trainer reference resolved outside the worktree",
    )
    trainer_source = Path(trainer.__file__).read_text(encoding="utf-8")
    require(
        "crazyhouse_physical_v1" not in trainer_source
        and "crazyhouse_v2_physical" not in trainer_source,
        "trainer reference imports a forbidden production codec",
    )


def terminal_record(sequence: int, suffix: int, fen: str):
    return codec.build_record(
        sequence=sequence,
        game_id=codec.uuid_bytes(f"61000000-0000-4000-8000-{suffix:012d}"),
        trajectory_id=codec.uuid_bytes(f"62000000-0000-4000-8000-{suffix:012d}"),
        ply=0,
        fen=fen,
        effective_en_passant_square=codec.NO_SQUARE,
        repetition_occurrences=1,
        claim_policy=codec.CLAIM_CORE_ONLY,
        terminal_reason=codec.TERMINAL_RESIGNATION,
        move=codec.MoveWire.none(),
        game_result_white=1,
        provenance_sha256=hashlib.sha256(physical_goldens.provenance_bytes()).digest(),
        previous_history_sha256=None,
        teacher_score_kind=codec.TEACHER_NONE,
        teacher_score_value=0,
        teacher_bound=codec.BOUND_NONE,
        search_nodes=0,
        search_depth=0,
        search_seldepth=0,
        move_time_ms=0,
        teacher_used_network=False,
        nonstandard_root=True,
    )


def capacity_fen() -> str:
    ranks: list[str] = []
    for rank in range(7, -1, -1):
        tokens: list[str] = []
        for file_index in range(8):
            square = rank * 8 + file_index
            if square == 0:
                tokens.append("K")
            elif square == 63:
                tokens.append("k")
            else:
                token = "Q" if (square + rank) % 2 == 0 else "q"
                tokens.append(token + "~")
        ranks.append("".join(tokens))
    return "/".join(ranks) + "[] w - - 0 1"


def valid_cases() -> list[tuple[str, object]]:
    cases = [
        (f"golden-{index:03d}", record)
        for index, record in enumerate(physical_goldens.golden_records())
    ]
    base = terminal_record(20_000, 1, "7k/8/8/8/8/8/Q7/K7[] w - - 0 1")
    pocket = terminal_record(20_001, 2, "7k/8/8/8/8/8/Q7/K7[P] w - - 0 1")
    promoted = terminal_record(20_002, 3, "7k/8/8/8/8/8/Q~7/K7[] w - - 0 1")
    symmetry_source = terminal_record(
        20_003,
        4,
        "6rk/8/8/3n4/8/2B~5/8/K7[Pq] b - - 17 42",
    )
    symmetry_reflected = codec.reflect_rank_color_swap(symmetry_source)
    capacity = terminal_record(20_004, 5, capacity_fen())
    cases.extend(
        (
            ("pocket-base", base),
            ("pocket-one", pocket),
            ("promoted-one", promoted),
            ("symmetry-source", symmetry_source),
            ("symmetry-reflected", symmetry_reflected),
            ("physical-capacity", capacity),
        )
    )
    return cases


def repair_header_crc(payload: bytearray) -> None:
    require(len(payload) == trainer.FILE_BYTES, "header CRC repair requires full file")
    struct.pack_into("<I", payload, 252, trainer.crc32c(bytes(payload[:252])))


def header_mutation(
    source: bytes,
    edit: Callable[[bytearray], None],
    *,
    repair_crc: bool = True,
) -> bytes:
    payload = bytearray(source)
    edit(payload)
    if repair_crc:
        repair_header_crc(payload)
    return bytes(payload)


def network_mutations(source: bytes) -> list[tuple[str, str, bytes]]:
    def pack(offset: int, fmt: str, value: int) -> Callable[[bytearray], None]:
        return lambda payload: struct.pack_into(fmt, payload, offset, value)

    cases: list[tuple[str, str, bytes]] = [
        ("truncated-file", "WRONG_SIZE", source[:-1]),
        ("appended-byte", "WRONG_SIZE", source + b"\0"),
        ("magic", "MAGIC", header_mutation(source, lambda p: p.__setitem__(0, ord("X")))),
        ("byte-order-marker", "BYTE_ORDER", header_mutation(source, pack(16, "<I", 0x04030201))),
        ("header-size", "HEADER_SIZE", header_mutation(source, pack(20, "<H", 255))),
        ("version-major", "VERSION", header_mutation(source, pack(22, "<H", 2))),
        ("version-minor", "VERSION", header_mutation(source, pack(24, "<H", 1))),
        ("flags", "FLAGS", header_mutation(source, pack(26, "<H", 3))),
        ("file-size", "FILE_SIZE", header_mutation(source, pack(28, "<I", trainer.FILE_BYTES + 1))),
        ("feature-dimensions", "FEATURE_DIMENSIONS", header_mutation(source, pack(32, "<I", 901))),
        ("maximum-active", "MAXIMUM_ACTIVE", header_mutation(source, pack(36, "<I", 137))),
        ("output-lanes", "OUTPUT_LANES", header_mutation(source, pack(40, "<I", 16))),
        ("input-semantics", "INPUT_SEMANTICS", header_mutation(source, pack(44, "<H", 2))),
        ("weight-type", "WEIGHT_TYPE", header_mutation(source, pack(46, "<H", 2))),
        ("bias-type", "BIAS_TYPE", header_mutation(source, pack(48, "<H", 1))),
        ("accumulator-type", "ACCUMULATOR_TYPE", header_mutation(source, pack(50, "<H", 1))),
        ("weights-offset", "WEIGHTS_OFFSET", header_mutation(source, pack(52, "<I", 258))),
        ("weights-bytes", "WEIGHTS_BYTES", header_mutation(source, pack(56, "<I", 30666))),
        ("biases-offset", "BIASES_OFFSET", header_mutation(source, pack(60, "<I", 30922))),
        ("biases-bytes", "BIASES_BYTES", header_mutation(source, pack(64, "<I", 64))),
        ("payload-bytes", "PAYLOAD_BYTES", header_mutation(source, pack(68, "<I", 30735))),
        ("reserved-zero-0", "RESERVED_BYTES", header_mutation(source, lambda p: p.__setitem__(72, 1))),
        ("reserved-zero-1", "RESERVED_BYTES", header_mutation(source, lambda p: p.__setitem__(240, 1))),
        ("rule-profile-identity", "RULE_PROFILE_IDENTITY", header_mutation(source, lambda p: p.__setitem__(80, p[80] ^ 1))),
        ("physical-schema-identity", "PHYSICAL_SCHEMA_IDENTITY", header_mutation(source, lambda p: p.__setitem__(112, p[112] ^ 1))),
        ("feature-contract-identity", "FEATURE_CONTRACT_IDENTITY", header_mutation(source, lambda p: p.__setitem__(144, p[144] ^ 1))),
        ("architecture-identity", "ARCHITECTURE_IDENTITY", header_mutation(source, lambda p: p.__setitem__(176, p[176] ^ 1))),
        ("header-crc32c", "HEADER_CRC32C", header_mutation(source, lambda p: p.__setitem__(252, p[252] ^ 1), repair_crc=False)),
        ("corrupt-weight-payload", "PAYLOAD_SHA256", header_mutation(source, lambda p: p.__setitem__(trainer.WEIGHTS_OFFSET, p[trainer.WEIGHTS_OFFSET] ^ 1), repair_crc=False)),
        ("corrupt-bias-payload", "PAYLOAD_SHA256", header_mutation(source, lambda p: p.__setitem__(trainer.BIASES_OFFSET, p[trainer.BIASES_OFFSET] ^ 1), repair_crc=False)),
    ]
    return cases


def parse_int_list(text: str, expected: int | None, label: str) -> tuple[int, ...]:
    require(bool(text), f"{label}: empty integer list")
    try:
        output = tuple(int(value) for value in text.split(","))
    except ValueError as exc:
        raise VerificationError(f"{label}: malformed integer list") from exc
    if expected is not None:
        require(len(output) == expected, f"{label}: integer count drifted")
    return output


def run_positive(
    fixture: Path,
    network_path: Path,
    cases: Sequence[tuple[str, object]],
    network,
) -> tuple[str, int]:
    encoded = [(case_id, codec.encode_record(record)) for case_id, record in cases]
    protocol = "\n".join(
        "\t".join(("VALID", case_id, payload.hex())) for case_id, payload in encoded
    ) + "\n"
    try:
        run = subprocess.run(
            [str(fixture), "--network", str(network_path)],
            input=protocol,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("positive fixture timed out") from exc
    require(run.returncode == 0, f"positive fixture exited {run.returncode}: {run.stderr.strip()}")
    require(not run.stderr, "passing positive fixture emitted stderr")
    output = run.stdout.splitlines()
    require(len(output) == len(cases) + 1, "positive output row count drifted")

    maximum_observed = 0
    for (case_id, record_bytes), line in zip(encoded, output[:-1], strict=True):
        fields = line.split("\t")
        require(len(fields) == 7 and fields[:2] == ["OK", case_id], f"{case_id}: malformed OK row")
        state = trainer.decode_physical_record(record_bytes)
        require(fields[2] == state.position_identity_sha256.hex(), f"{case_id}: identity drifted")
        for perspective, row_field, lane_field in ((0, fields[3], fields[5]), (1, fields[4], fields[6])):
            expected_rows = trainer.feature_rows(state, perspective)
            expected_lanes = trainer.evaluate(network, expected_rows)
            observed_rows = parse_int_list(row_field, None, f"{case_id}/{perspective}/rows")
            observed_lanes = parse_int_list(lane_field, trainer.OUTPUT_LANES, f"{case_id}/{perspective}/lanes")
            require(observed_rows == expected_rows, f"{case_id}/{perspective}: ordered rows differ")
            require(observed_lanes == expected_lanes, f"{case_id}/{perspective}: accumulator differs")
            maximum_observed = max(maximum_observed, len(observed_rows))
    require(
        output[-1]
        == f"SUMMARY\tvalid={len(cases)}\tperspectives={len(cases) * 2}"
        f"\tlane_values={len(cases) * 2 * trainer.OUTPUT_LANES}"
        f"\tdimensions={trainer.FEATURE_DIMENSIONS}\tlanes={trainer.OUTPUT_LANES}",
        "positive fixture summary drifted",
    )
    return sha256(run.stdout.encode("utf-8")), maximum_observed


def run_negatives(
    fixture: Path,
    temporary_path: Path,
    cases: Sequence[tuple[str, str, bytes]],
) -> str:
    transcripts: list[bytes] = []
    for case_id, expected_error, payload in cases:
        try:
            trainer.parse_network(payload)
        except trainer.ProbeFormatError as exc:
            require(exc.code == expected_error, f"{case_id}: Python expected {expected_error}, got {exc.code}")
        else:
            raise VerificationError(f"{case_id}: Python accepted an adversarial network")
        temporary_path.write_bytes(payload)
        try:
            run = subprocess.run(
                [
                    str(fixture),
                    "--network",
                    str(temporary_path),
                    "--expect-network-error",
                    expected_error,
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise VerificationError(f"{case_id}: negative fixture timed out") from exc
        require(run.returncode == 0, f"{case_id}: fixture exited {run.returncode}: {run.stderr.strip()}")
        require(not run.stderr, f"{case_id}: passing negative fixture emitted stderr")
        require(
            run.stdout == f"REJECT\tnetwork\t{expected_error}\tready=false\n",
            f"{case_id}: malformed rejection transcript",
        )
        transcripts.append(case_id.encode() + b"\0" + run.stdout.encode("utf-8"))
    return sha256(b"".join(transcripts))


def verify_controls(cases: Sequence[tuple[str, object]]) -> None:
    encoded = {case_id: codec.encode_record(record) for case_id, record in cases}
    states = {case_id: trainer.decode_physical_record(payload) for case_id, payload in encoded.items()}
    base = states["pocket-base"]
    pocket = states["pocket-one"]
    promoted = states["promoted-one"]
    require(base.board == pocket.board == promoted.board, "control board bytes drifted")
    require(base.pockets != pocket.pockets, "pocket control did not change pockets")
    require(base.promoted_mask == pocket.promoted_mask == 0, "pocket control changed provenance")
    require(promoted.promoted_mask != 0, "promoted control did not change provenance")
    for perspective in (0, 1):
        base_rows = set(trainer.feature_rows(base, perspective))
        pocket_rows = set(trainer.feature_rows(pocket, perspective))
        promoted_rows = set(trainer.feature_rows(promoted, perspective))
        require(len(base_rows ^ pocket_rows) == 2, "pocket count did not replace exactly one row")
        require(len(base_rows ^ promoted_rows) == 1, "provenance did not add exactly one row")
    source = states["symmetry-source"]
    reflected = states["symmetry-reflected"]
    require(
        sorted(trainer.feature_rows(source, 0)) == sorted(trainer.feature_rows(reflected, 1)),
        "White-to-Black symmetry rows differ",
    )
    require(
        sorted(trainer.feature_rows(source, 1)) == sorted(trainer.feature_rows(reflected, 0)),
        "Black-to-White symmetry rows differ",
    )
    capacity = states["physical-capacity"]
    require(sum(code != 0 for code in capacity.board) == 64, "capacity board is not full")
    require(capacity.promoted_mask.bit_count() == 62, "capacity provenance count drifted")
    require(
        all(len(trainer.feature_rows(capacity, perspective)) == 136 for perspective in (0, 1)),
        "capacity control did not reach the physical 136-row ceiling",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    require(args.fixture.is_file(), "fixture executable is missing")
    authenticate_inputs()
    cases = valid_cases()
    require(len(cases) == 48, "valid case count drifted")
    verify_controls(cases)

    artifact = trainer.synthetic_network_bytes()
    require(len(artifact) == trainer.FILE_BYTES == 30_992, "artifact byte count drifted")
    network = trainer.parse_network(artifact)
    require(trainer.serialize_network(network) == artifact, "Python network round-trip drifted")
    negatives = network_mutations(artifact)
    require(len(negatives) == 30, "negative case count drifted")

    with tempfile.TemporaryDirectory(prefix="crazyhouse-v2-probe-") as directory:
        temporary = Path(directory)
        network_path = temporary / "synthetic.chn2p"
        network_path.write_bytes(artifact)
        protocol_sha, maximum_observed = run_positive(args.fixture, network_path, cases, network)
        rejection_sha = run_negatives(fixture=args.fixture, temporary_path=temporary / "mutation.chn2p", cases=negatives)

    require(maximum_observed == 136, "physical active-row maximum was not observed")
    print(
        "PASS crazyhouse_v2_container_scalar "
        f"artifact_bytes={len(artifact)} artifact_sha256={sha256(artifact)} "
        f"valid={len(cases)} frozen_goldens=42 perspectives={len(cases) * 2} "
        f"lane_values={len(cases) * 2 * trainer.OUTPUT_LANES} negatives={len(negatives)} "
        f"maximum_active_observed={maximum_observed} declared_capacity={trainer.MAXIMUM_ACTIVE} "
        f"protocol_sha256={protocol_sha} rejection_sha256={rejection_sha} "
        "python_roundtrip=byte-exact training_admissible=false g12_closed=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, trainer.ReferenceError) as exc:
        print(f"FAIL crazyhouse_v2_container_scalar_verify: {exc}", file=sys.stderr)
        raise SystemExit(1)
