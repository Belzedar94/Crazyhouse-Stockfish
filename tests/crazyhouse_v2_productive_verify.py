#!/usr/bin/env python3
"""Independent formal verifier for the productive Crazyhouse NNUE V2 slice."""

from __future__ import annotations

import argparse
from array import array
import copy
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from typing import Any, Callable, Sequence

import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[1]
NNUE_TOOLS = ROOT / "tools" / "nnue"
sys.path.insert(0, str(NNUE_TOOLS))

import crazyhouse_v2_productive_reference as reference  # noqa: E402


MANIFEST_RELATIVE = Path("tests/crazyhouse/data/crazyhouse-physical-v1-goldens.json")
CONFIG_RELATIVE = Path("tests/crazyhouse/p12-nnue-v2-productive-scalar-trainer-v1.json")
MANIFEST_SHA256 = "94cd50961d8e51478e55a82cd4e0770d418a30483b3c5d120a470f7eb2efccac"
CONFIG_SHA256 = "d521795e18fe51ad561a13deacf217d6e9632caa1ea7071bf9fd966df00e67a5"


class VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParserNegative:
    name: str
    expected: str
    payload: bytes
    provenance: reference.ExpectedProvenance


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
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_generated(path: Path, payload: bytes) -> None:
    require(not path.exists(), f"generated path already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def load_golden_records(repo: Path) -> tuple[list[bytes], dict[str, Any]]:
    module_path = repo / "tests" / "crazyhouse_physical_v1_unit.py"
    spec = importlib.util.spec_from_file_location("crazyhouse_physical_v1_unit_for_v2", module_path)
    if spec is None or spec.loader is None:
        raise VerificationError("cannot load physical golden generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    encoded = [module.codec.encode_record(record) for record in module.golden_records()]
    manifest_path = repo / MANIFEST_RELATIVE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["expected"]["record_sha256"]
    require(len(encoded) == len(expected) == 42, "golden record count")
    require([sha256_bytes(record) for record in encoded] == expected, "golden record hashes")
    require(sha256_bytes(b"".join(encoded)) == manifest["expected"]["payload_sha256"], "golden payload hash")
    return encoded, manifest


def refresh_header_crc(payload: bytearray) -> None:
    struct.pack_into("<I", payload, 508, reference.crc32c(bytes(payload[:508])))


def refresh_payload_and_header(payload: bytearray) -> None:
    payload[416:448] = hashlib.sha256(payload[reference.HEADER_BYTES :]).digest()
    refresh_header_crc(payload)


def changed_integer(payload: bytes, offset: int, width: int) -> bytes:
    output = bytearray(payload)
    if width == 2:
        struct.pack_into("<H", output, offset, (struct.unpack_from("<H", output, offset)[0] + 1) & 0xFFFF)
    elif width == 4:
        struct.pack_into("<I", output, offset, (struct.unpack_from("<I", output, offset)[0] + 1) & 0xFFFFFFFF)
    else:
        raise AssertionError(width)
    return bytes(output)


def changed_byte(payload: bytes, offset: int) -> bytes:
    output = bytearray(payload)
    output[offset] ^= 1
    return bytes(output)


def parser_negatives(
    valid: bytes, provenance: reference.ExpectedProvenance
) -> list[ParserNegative]:
    cases = [
        ParserNegative(
            "expected-dataset-zero",
            "EXPECTED_PROVENANCE",
            valid,
            reference.ExpectedProvenance(bytes(32), provenance.training_config_sha256),
        ),
        ParserNegative(
            "expected-config-zero",
            "EXPECTED_PROVENANCE",
            valid,
            reference.ExpectedProvenance(provenance.dataset_manifest_sha256, bytes(32)),
        ),
        ParserNegative("wrong-size-short", "WRONG_SIZE", valid[:-1], provenance),
        ParserNegative("wrong-size-trailing", "WRONG_SIZE", valid + b"\0", provenance),
        ParserNegative("magic", "MAGIC", changed_byte(valid, 0), provenance),
        ParserNegative("byte-order", "BYTE_ORDER", changed_integer(valid, 16, 4), provenance),
        ParserNegative("header-size", "HEADER_SIZE", changed_integer(valid, 20, 2), provenance),
        ParserNegative("version", "VERSION", changed_integer(valid, 22, 2), provenance),
        ParserNegative("flags", "FLAGS", changed_integer(valid, 26, 2), provenance),
        ParserNegative("file-size", "FILE_SIZE", changed_integer(valid, 28, 4), provenance),
    ]
    for offset, code in (
        (32, "FEATURE_DIMENSIONS"),
        (36, "MAXIMUM_ACTIVE"),
        (40, "TRANSFORMER_LANES"),
        (44, "PERSPECTIVE_COUNT"),
        (48, "DENSE0_INPUTS"),
        (52, "DENSE0_OUTPUTS"),
        (56, "DENSE1_INPUTS"),
        (60, "DENSE1_OUTPUTS"),
        (64, "OUTPUT_INPUTS"),
        (68, "OUTPUT_OUTPUTS"),
    ):
        cases.append(ParserNegative(code.lower(), code, changed_integer(valid, offset, 4), provenance))
    for offset, code in (
        (72, "TRANSFORMER_WEIGHT_TYPE"),
        (74, "TRANSFORMER_BIAS_TYPE"),
        (76, "DENSE_WEIGHT_TYPE"),
        (78, "DENSE_BIAS_TYPE"),
        (80, "OUTPUT_WEIGHT_TYPE"),
        (82, "OUTPUT_BIAS_TYPE"),
        (84, "ACCUMULATOR_TYPE"),
        (86, "ACTIVATION_TYPE"),
    ):
        cases.append(ParserNegative(code.lower(), code, changed_integer(valid, offset, 2), provenance))
    for offset, code in (
        (88, "TRANSFORMER_SCALE"),
        (92, "DENSE_WEIGHT_SCALE"),
        (96, "OUTPUT_WEIGHT_SCALE"),
        (100, "OUTPUT_VALUE_SCALE"),
        (104, "DENSE_ACTIVATION_DIVISOR"),
        (108, "OUTPUT_DIVISOR"),
    ):
        cases.append(ParserNegative(code.lower(), code, changed_integer(valid, offset, 4), provenance))
    for index in range(8):
        cases.append(
            ParserNegative(
                f"tensor-directory-{index}",
                "TENSOR_DIRECTORY",
                changed_integer(valid, 112 + 8 * index, 4),
                provenance,
            )
        )
    for offset, code in (
        (176, "INPUT_SEMANTICS"),
        (180, "PERSPECTIVE_ORDER"),
        (184, "ACTIVATION_SEMANTICS"),
        (188, "OUTPUT_UNITS"),
    ):
        cases.append(ParserNegative(code.lower(), code, changed_integer(valid, offset, 4), provenance))
    for offset, code in (
        (192, "RULE_PROFILE_IDENTITY"),
        (224, "PHYSICAL_SCHEMA_IDENTITY"),
        (256, "FEATURE_CONTRACT_IDENTITY"),
        (288, "ARCHITECTURE_IDENTITY"),
        (320, "QUANTIZATION_IDENTITY"),
    ):
        cases.append(ParserNegative(code.lower(), code, changed_byte(valid, offset), provenance))

    dataset_zero = bytearray(valid)
    dataset_zero[352:384] = bytes(32)
    cases.append(ParserNegative("dataset-zero", "DATASET_IDENTITY_ZERO", bytes(dataset_zero), provenance))
    cases.append(ParserNegative("dataset-mismatch", "DATASET_IDENTITY", changed_byte(valid, 352), provenance))
    config_zero = bytearray(valid)
    config_zero[384:416] = bytes(32)
    cases.append(
        ParserNegative("training-config-zero", "TRAINING_CONFIG_IDENTITY_ZERO", bytes(config_zero), provenance)
    )
    cases.append(
        ParserNegative("training-config-mismatch", "TRAINING_CONFIG_IDENTITY", changed_byte(valid, 384), provenance)
    )
    cases.append(ParserNegative("reserved", "RESERVED_BYTES", changed_byte(valid, 448), provenance))
    cases.append(ParserNegative("header-crc", "HEADER_CRC32C", changed_byte(valid, 508), provenance))
    cases.append(ParserNegative("payload-sha", "PAYLOAD_SHA256", changed_byte(valid, len(valid) - 1), provenance))

    transformer = bytearray(valid)
    struct.pack_into("<i", transformer, 924160, (1 << 31) - 1)
    refresh_payload_and_header(transformer)
    cases.append(ParserNegative("transformer-interval", "TRANSFORMER_INTERVAL", bytes(transformer), provenance))
    dense0 = bytearray(valid)
    struct.pack_into("<i", dense0, 958976, (1 << 31) - 1)
    refresh_payload_and_header(dense0)
    cases.append(ParserNegative("dense0-interval", "DENSE0_INTERVAL", bytes(dense0), provenance))
    dense1 = bytearray(valid)
    struct.pack_into("<i", dense1, 960128, (1 << 31) - 1)
    refresh_payload_and_header(dense1)
    cases.append(ParserNegative("dense1-interval", "DENSE1_INTERVAL", bytes(dense1), provenance))
    output = bytearray(valid)
    struct.pack_into("<i", output, 960320, (1 << 31) - 1)
    refresh_payload_and_header(output)
    cases.append(ParserNegative("output-interval", "OUTPUT_INTERVAL", bytes(output), provenance))
    require(len(cases) >= 36, "parser negative minimum")
    return cases


def binary_environment() -> dict[str, str]:
    environment = os.environ.copy()
    prefixes = [r"C:\msys64\mingw64\bin", r"C:\msys64\usr\bin"]
    environment["PATH"] = os.pathsep.join(prefixes + [environment.get("PATH", "")])
    return environment


def run_binary_negative(
    binary: Path, path: Path, expected: str, provenance: reference.ExpectedProvenance
) -> str:
    completed = subprocess.run(
        [
            str(binary),
            "--network",
            str(path),
            "--dataset-sha256",
            provenance.dataset_manifest_sha256.hex(),
            "--training-config-sha256",
            provenance.training_config_sha256.hex(),
            "--expect-network-error",
            expected,
        ],
        input=b"",
        capture_output=True,
        timeout=60,
        env=binary_environment(),
        check=False,
    )
    require(completed.returncode == 0, f"C++ parser negative {expected}: {completed.stderr!r}")
    require(completed.stderr == b"", f"C++ parser negative {expected} stderr")
    decoded_lines = completed.stdout.decode("utf-8").splitlines()
    require(
        decoded_lines == [f"REJECT\tnetwork\t{expected}\tobject=false"],
        f"C++ parser negative {expected} output",
    )
    return sha256_bytes(completed.stdout)


def verify_parser_matrix(
    binary: Path,
    output: Path,
    valid: bytes,
    provenance: reference.ExpectedProvenance,
) -> list[dict[str, Any]]:
    negative_dir = output / "parser-negatives"
    negative_dir.mkdir()
    rows: list[dict[str, Any]] = []
    for case in parser_negatives(valid, provenance):
        path = negative_dir / f"{case.name}.nnuev2"
        write_generated(path, case.payload)
        try:
            reference.parse_network(case.payload, case.provenance)
        except reference.ProductiveFormatError as error:
            require(error.code == case.expected, f"Python {case.name}: {error.code} != {case.expected}")
        else:
            raise VerificationError(f"Python accepted parser negative {case.name}")
        stdout_sha = run_binary_negative(binary, path, case.expected, case.provenance)
        rows.append(
            {
                "case": case.name,
                "expected": case.expected,
                "bytes": len(case.payload),
                "sha256": sha256_bytes(case.payload),
                "cpp_stdout_sha256": stdout_sha,
            }
        )
    return rows


def zero_float_network(provenance: reference.ExpectedProvenance) -> reference.FloatNetwork:
    return reference.FloatNetwork(
        transformer_weights=array("f", [0.0]) * (902 * 512),
        transformer_biases=array("f", [0.0]) * 512,
        dense0_weights=array("f", [0.0]) * (32 * 1024),
        dense0_biases=array("f", [0.0]) * 32,
        dense1_weights=array("f", [0.0]) * (32 * 32),
        dense1_biases=array("f", [0.0]) * 32,
        output_weights=array("f", [0.0]) * 32,
        output_bias=array("f", [0.0]),
        provenance=provenance,
    )


def verify_exporter_negatives(
    output: Path, provenance: reference.ExpectedProvenance
) -> list[dict[str, str]]:
    base = zero_float_network(provenance)
    cases: list[tuple[str, Callable[[], reference.FloatNetwork]]] = [
        ("transformer-weight-shape", lambda: replace(base, transformer_weights=base.transformer_weights[:-1])),
        ("transformer-bias-shape", lambda: replace(base, transformer_biases=base.transformer_biases[:-1])),
        ("dense0-weight-shape", lambda: replace(base, dense0_weights=base.dense0_weights[:-1])),
        ("dense0-bias-shape", lambda: replace(base, dense0_biases=base.dense0_biases[:-1])),
        ("dense1-weight-shape", lambda: replace(base, dense1_weights=base.dense1_weights[:-1])),
        ("dense1-bias-shape", lambda: replace(base, dense1_biases=base.dense1_biases[:-1])),
        ("output-weight-shape", lambda: replace(base, output_weights=base.output_weights[:-1])),
        ("output-bias-shape", lambda: replace(base, output_bias=[])),
    ]

    def changed(field: str, value: float) -> reference.FloatNetwork:
        tensor = array("f", getattr(base, field))
        tensor[0] = value
        network = copy.copy(base)
        setattr(network, field, tensor)
        return network

    cases.extend(
        [
            ("nan", lambda: changed("output_weights", float("nan"))),
            ("infinity", lambda: changed("dense1_weights", float("inf"))),
            ("transformer-int16-overflow", lambda: changed("transformer_weights", 300.0)),
            ("dense0-int8-overflow", lambda: changed("dense0_weights", 3.0)),
            ("dense1-int8-overflow", lambda: changed("dense1_weights", -3.0)),
            ("output-int16-overflow", lambda: changed("output_weights", 600.0)),
            ("bias-int32-overflow", lambda: changed("output_bias", 1.0e9)),
        ]
    )
    require(len(cases) >= 8, "exporter negative minimum")
    rows = []
    negative_dir = output / "exporter-negatives"
    negative_dir.mkdir()
    for name, factory in cases:
        destination = negative_dir / f"{name}.nnuev2"
        try:
            reference.export_network_file(str(destination), factory())
        except reference.ProductiveExportError as error:
            require(not destination.exists(), f"{name} left final artifact")
            require(not destination.with_name(destination.name + ".partial").exists(), f"{name} left partial")
            rows.append({"case": name, "error": error.code})
        else:
            raise VerificationError(f"exporter accepted {name}")

    for name, field, expected in (
        ("transformer-static-interval", "transformer_biases", "TRANSFORMER_INTERVAL"),
        ("dense0-static-interval", "dense0_biases", "DENSE0_INTERVAL"),
        ("dense1-static-interval", "dense1_biases", "DENSE1_INTERVAL"),
        ("output-static-interval", "output_bias", "OUTPUT_INTERVAL"),
    ):
        network = copy.deepcopy(reference.synthetic_quantized_network(provenance))
        getattr(network, field)[0] = (1 << 31) - 1
        try:
            reference.serialize_network(network)
        except reference.ProductiveExportError as error:
            require(error.code == expected, f"{name} returned {error.code}")
            rows.append({"case": name, "error": error.code})
        else:
            raise VerificationError(f"exporter accepted {name}")
    return rows


def parse_csv(text: str) -> tuple[int, ...]:
    return tuple(int(value) for value in text.split(",")) if text else ()


def trace_fields(trace: reference.ProductiveTrace) -> tuple[tuple[int, ...] | int, ...]:
    return (
        trace.transformer_stm,
        trace.transformer_opponent,
        trace.transformer_stm_activation,
        trace.transformer_opponent_activation,
        trace.dense0,
        trace.dense0_activation,
        trace.dense1,
        trace.dense1_activation,
        trace.output_raw,
        trace.output_centipawns,
    )


def verify_cpp_parity(
    binary: Path,
    network_path: Path,
    network: reference.QuantizedNetwork,
    provenance: reference.ExpectedProvenance,
    records: Sequence[bytes],
) -> dict[str, Any]:
    protocol = b"".join(
        f"VALID\trecord-{index:02d}\t{record.hex()}\n".encode()
        for index, record in enumerate(records)
    )
    completed = subprocess.run(
        [
            str(binary),
            "--network",
            str(network_path),
            "--dataset-sha256",
            provenance.dataset_manifest_sha256.hex(),
            "--training-config-sha256",
            provenance.training_config_sha256.hex(),
        ],
        input=protocol,
        capture_output=True,
        timeout=180,
        env=binary_environment(),
        check=False,
    )
    require(completed.returncode == 0, f"C++ parity failed: {completed.stderr!r}")
    require(completed.stderr == b"", "C++ parity stderr")
    lines = completed.stdout.decode("utf-8").splitlines()
    require(lines[-1] == "SUMMARY\trecords=42\tevaluations=84\ttransformer_lanes=512\tcontainer_bytes=960324\ttraining_admissible=false\tg12_closed=false", "C++ parity summary")
    require(len(lines) == 85, "C++ parity line count")
    manifest = json.loads((ROOT / MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    for index, line in enumerate(lines[:-1]):
        fields = line.split("\t")
        require(len(fields) == 16 and fields[0] == "OK", "C++ parity protocol")
        record_index = index // 2
        perspective = 0 if fields[2] == "white" else 1
        require(fields[1] == f"record-{record_index:02d}", "C++ parity record id")
        state = reference.decode_microfit_state(records[record_index], manifest["expected"]["record_sha256"][record_index])
        white_rows = reference.feature_rows(state, 0)
        black_rows = reference.feature_rows(state, 1)
        require(fields[3] == state.position_identity_sha256.hex(), "C++ parity identity")
        require(parse_csv(fields[4]) == white_rows and parse_csv(fields[5]) == black_rows, "C++ feature rows")
        expected = trace_fields(reference.evaluate(network, white_rows, black_rows, perspective))
        observed: tuple[tuple[int, ...] | int, ...] = (
            parse_csv(fields[6]),
            parse_csv(fields[7]),
            parse_csv(fields[8]),
            parse_csv(fields[9]),
            parse_csv(fields[10]),
            parse_csv(fields[11]),
            parse_csv(fields[12]),
            parse_csv(fields[13]),
            int(fields[14]),
            int(fields[15]),
        )
        require(observed == expected, f"C++ layer parity record {record_index} perspective {perspective}")
    return {
        "records": 42,
        "perspectives": 84,
        "layer_values": 84 * (512 * 4 + 32 * 4 + 2),
        "stdout_bytes": len(completed.stdout),
        "stdout_sha256": sha256_bytes(completed.stdout),
    }


def trainer_environment() -> dict[str, str]:
    environment = binary_environment()
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "CUDA_VISIBLE_DEVICES": "",
        }
    )
    return environment


def trainer_base_command(
    python: Path,
    command: str,
    repo: Path,
    records_path: Path,
    output_dir: Path,
    source_commit: str,
    source_tree: str,
    src_tree: str,
) -> list[str]:
    return [
        str(python),
        str(repo / "tools/nnue/crazyhouse_v2_train.py"),
        command,
        "--engineering-microfit",
        "--records",
        str(records_path),
        "--manifest",
        str(repo / MANIFEST_RELATIVE),
        "--manifest-sha256",
        MANIFEST_SHA256,
        "--training-config",
        str(repo / CONFIG_RELATIVE),
        "--training-config-sha256",
        CONFIG_SHA256,
        "--source-commit",
        source_commit,
        "--source-tree",
        source_tree,
        "--src-tree",
        src_tree,
        "--output-dir",
        str(output_dir),
    ]


def run_trainer(command: list[str], timeout: int = 600) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        input=b"",
        capture_output=True,
        timeout=timeout,
        env=trainer_environment(),
        check=False,
    )


def recursive_equal(left: Any, right: Any, path: str = "root") -> None:
    if isinstance(left, torch.Tensor) or isinstance(right, torch.Tensor):
        require(isinstance(left, torch.Tensor) and isinstance(right, torch.Tensor), f"{path} tensor type")
        require(left.dtype == right.dtype and tuple(left.shape) == tuple(right.shape), f"{path} tensor metadata")
        require(torch.equal(left, right), f"{path} tensor values")
    elif isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        require(isinstance(left, np.ndarray) and isinstance(right, np.ndarray), f"{path} ndarray type")
        require(left.dtype == right.dtype and left.shape == right.shape and np.array_equal(left, right), f"{path} ndarray")
    elif isinstance(left, dict) and isinstance(right, dict):
        require(left.keys() == right.keys(), f"{path} dict keys")
        for key in left:
            recursive_equal(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, (list, tuple)) and isinstance(right, type(left)):
        require(len(left) == len(right), f"{path} sequence length")
        for index, (left_value, right_value) in enumerate(zip(left, right)):
            recursive_equal(left_value, right_value, f"{path}[{index}]")
    else:
        require(left == right and type(left) is type(right), f"{path} scalar")


def verify_resume_negatives(
    python: Path,
    repo: Path,
    records_path: Path,
    frozen_checkpoint: Path,
    output: Path,
    source_commit: str,
    source_tree: str,
    src_tree: str,
) -> list[dict[str, Any]]:
    original_sha256 = sha256_file(frozen_checkpoint)
    original = torch.load(frozen_checkpoint, map_location="cpu", weights_only=True)
    mutations: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
        ("source-commit", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["source"].__setitem__("commit", "0" * 40)),
        ("source-tree", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["source"].__setitem__("tree", "1" * 40)),
        ("src-tree", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["source"].__setitem__("src_tree", "2" * 40)),
        ("dataset", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["inputs"].__setitem__("manifest_sha256", "3" * 64)),
        ("config", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["inputs"].__setitem__("training_config_sha256", "4" * 64)),
        ("architecture", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["contracts"].__setitem__("architecture_sha256", "5" * 64)),
        ("environment", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["environment"].__setitem__("python", "0.0.0")),
        ("optimizer-contract", "CHECKPOINT_IDENTITY", lambda doc: doc["identity"]["trainer"]["optimizer"].__setitem__("learning_rate", 0.5)),
        ("optimizer-state", "OPTIMIZER_SPEC", lambda doc: doc["optimizer_state"]["param_groups"][0].__setitem__("lr", 0.5)),
        ("optimizer-params", "OPTIMIZER_PARAMS", lambda doc: doc["optimizer_state"]["param_groups"][0]["params"].reverse()),
        ("optimizer-moment", "OPTIMIZER_MOMENT", lambda doc: doc["optimizer_state"]["state"][0].__setitem__("exp_avg", torch.zeros(1))),
        ("rng", "TORCH_RNG", lambda doc: doc["rng"].__setitem__("torch", torch.zeros(1, dtype=torch.uint8))),
        ("cursor", "CURSOR_RELATION", lambda doc: doc["cursor"].__setitem__("global_step", doc["cursor"]["global_step"] + 1)),
        ("order-chain", "ORDER_CHAIN", lambda doc: doc.__setitem__("sample_order_chain", "0" * 64)),
        ("model", "MODEL_STATE_SHAPE", lambda doc: doc["model_state"].__setitem__("output.bias", torch.zeros(2, dtype=torch.float32))),
    ]
    require(len(mutations) >= 9, "resume negative minimum")
    rows: list[dict[str, Any]] = []
    negative_dir = output / "resume-negatives"
    negative_dir.mkdir()

    wire_checkpoint = negative_dir / "checkpoint-wire.pt"
    wire_payload = bytearray(frozen_checkpoint.read_bytes())
    wire_payload[-1] ^= 1
    write_generated(wire_checkpoint, bytes(wire_payload))
    wire_output = negative_dir / "checkpoint-wire-output"
    wire_command = trainer_base_command(
        python,
        "resume",
        repo,
        records_path,
        wire_output,
        source_commit,
        source_tree,
        src_tree,
    ) + [
        "--checkpoint",
        str(wire_checkpoint),
        "--checkpoint-sha256",
        original_sha256,
    ]
    wire_completed = run_trainer(wire_command, timeout=180)
    require(wire_completed.returncode == 2, "resume negative checkpoint-wire exit")
    require(wire_completed.stdout == b"", "resume negative checkpoint-wire stdout")
    require(
        wire_completed.stderr.decode("utf-8").splitlines()
        == ["FAIL crazyhouse_v2_train: CHECKPOINT_SHA256"],
        "resume negative checkpoint-wire error",
    )
    require(not wire_output.exists(), "resume negative checkpoint-wire created output")
    rows.append(
        {
            "case": "checkpoint-wire",
            "checkpoint_bytes": wire_checkpoint.stat().st_size,
            "checkpoint_sha256": sha256_file(wire_checkpoint),
            "expected_checkpoint_sha256": original_sha256,
            "expected_error": "CHECKPOINT_SHA256",
            "returncode": wire_completed.returncode,
            "stderr_sha256": sha256_bytes(wire_completed.stderr),
        }
    )

    for name, expected_error, mutate in mutations:
        document = copy.deepcopy(original)
        mutate(document)
        checkpoint = negative_dir / f"{name}.pt"
        torch.save(document, checkpoint)
        attempted_output = negative_dir / f"{name}-output"
        command = trainer_base_command(
            python,
            "resume",
            repo,
            records_path,
            attempted_output,
            source_commit,
            source_tree,
            src_tree,
        ) + [
            "--checkpoint",
            str(checkpoint),
            "--checkpoint-sha256",
            sha256_file(checkpoint),
        ]
        completed = run_trainer(command, timeout=180)
        require(completed.returncode == 2, f"resume negative {name} exit")
        require(completed.stdout == b"", f"resume negative {name} stdout")
        require(
            completed.stderr.decode("utf-8").splitlines()
            == [f"FAIL crazyhouse_v2_train: {expected_error}"],
            f"resume negative {name} error",
        )
        require(not attempted_output.exists(), f"resume negative {name} created output")
        rows.append(
            {
                "case": name,
                "checkpoint_bytes": checkpoint.stat().st_size,
                "checkpoint_sha256": sha256_file(checkpoint),
                "expected_error": expected_error,
                "returncode": completed.returncode,
                "stderr_sha256": sha256_bytes(completed.stderr),
            }
        )
    return rows


def verify_training_resume(
    python: Path,
    binary: Path,
    repo: Path,
    output: Path,
    records: Sequence[bytes],
    records_path: Path,
    source_commit: str,
    source_tree: str,
    src_tree: str,
) -> dict[str, Any]:
    full_dir = output / "training-full"
    resumed_dir = output / "training-resumed"
    full = run_trainer(
        trainer_base_command(
            python, "train", repo, records_path, full_dir, source_commit, source_tree, src_tree
        )
    )
    require(full.returncode == 0 and full.stderr == b"", f"full training failed: {full.stderr!r}")
    interrupted = run_trainer(
        trainer_base_command(
            python, "train", repo, records_path, resumed_dir, source_commit, source_tree, src_tree
        )
        + ["--stop-after-steps", "7"]
    )
    require(interrupted.returncode == 0 and interrupted.stderr == b"", f"interrupted training failed: {interrupted.stderr!r}")
    require(not (resumed_dir / "network.nnuev2").exists(), "interrupted run exported network")
    frozen_checkpoint = output / "checkpoint-step7.pt"
    shutil.copyfile(resumed_dir / "checkpoint.pt", frozen_checkpoint)
    frozen_checkpoint_sha256 = sha256_file(frozen_checkpoint)
    resumed = run_trainer(
        trainer_base_command(
            python, "resume", repo, records_path, resumed_dir, source_commit, source_tree, src_tree
        )
        + [
            "--checkpoint",
            str(resumed_dir / "checkpoint.pt"),
            "--checkpoint-sha256",
            frozen_checkpoint_sha256,
        ]
    )
    require(resumed.returncode == 0 and resumed.stderr == b"", f"resume failed: {resumed.stderr!r}")

    full_network = (full_dir / "network.nnuev2").read_bytes()
    resumed_network = (resumed_dir / "network.nnuev2").read_bytes()
    require(full_network == resumed_network, "full/resumed container bytes")
    require((full_dir / "metrics.jsonl").read_bytes() == (resumed_dir / "metrics.jsonl").read_bytes(), "full/resumed metrics")
    full_checkpoint = torch.load(full_dir / "checkpoint.pt", map_location="cpu", weights_only=True)
    resumed_checkpoint = torch.load(resumed_dir / "checkpoint.pt", map_location="cpu", weights_only=True)
    recursive_equal(full_checkpoint, resumed_checkpoint)

    provenance = reference.ExpectedProvenance(bytes.fromhex(MANIFEST_SHA256), bytes.fromhex(CONFIG_SHA256))
    parsed = reference.parse_network(full_network, provenance)
    require(reference.serialize_network(parsed) == full_network, "trained parse/reserialize")
    parity = verify_cpp_parity(binary, full_dir / "network.nnuev2", parsed, provenance, records)
    resume_negatives = verify_resume_negatives(
        python,
        repo,
        records_path,
        frozen_checkpoint,
        output,
        source_commit,
        source_tree,
        src_tree,
    )
    return {
        "full_stdout_sha256": sha256_bytes(full.stdout),
        "interrupted_stdout_sha256": sha256_bytes(interrupted.stdout),
        "resumed_stdout_sha256": sha256_bytes(resumed.stdout),
        "metrics_bytes": (full_dir / "metrics.jsonl").stat().st_size,
        "metrics_sha256": sha256_file(full_dir / "metrics.jsonl"),
        "checkpoint_step7_bytes": frozen_checkpoint.stat().st_size,
        "checkpoint_step7_sha256": sha256_file(frozen_checkpoint),
        "final_checkpoint_semantic_equal": True,
        "final_network_bytes": len(full_network),
        "final_network_sha256": sha256_bytes(full_network),
        "final_network_byte_equal": True,
        "parity": parity,
        "resume_negatives": resume_negatives,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--src-tree", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    output = args.output_dir.resolve()
    try:
        require(not output.exists(), "exclusive output directory already exists")
        require(args.binary.is_file(), "productive test binary missing")
        require(sha256_file(args.repo / MANIFEST_RELATIVE) == MANIFEST_SHA256, "manifest pin")
        require(sha256_file(args.repo / CONFIG_RELATIVE) == CONFIG_SHA256, "config pin")
        output.mkdir(parents=False, exist_ok=False)
        records, _manifest = load_golden_records(args.repo)
        records_path = output / "records.bin"
        write_generated(records_path, b"".join(records))
        provenance = reference.ExpectedProvenance(
            bytes.fromhex(MANIFEST_SHA256), bytes.fromhex(CONFIG_SHA256)
        )

        synthetic = reference.synthetic_quantized_network(provenance)
        synthetic_bytes = reference.serialize_network(synthetic)
        synthetic_path = output / "synthetic.nnuev2"
        write_generated(synthetic_path, synthetic_bytes)
        parsed = reference.parse_network(synthetic_bytes, provenance)
        require(reference.serialize_network(parsed) == synthetic_bytes, "synthetic parse/reserialize")

        float_positive = zero_float_network(provenance)
        positive_output_weights = array("f", float_positive.output_weights)
        positive_output_weights[0] = 0.5 / 64.0
        positive_output_weights[1] = -0.5 / 64.0
        float_positive = replace(float_positive, output_weights=positive_output_weights)
        exporter_positive_path = output / "exporter-positive.nnuev2"
        reference.export_network_file(str(exporter_positive_path), float_positive)
        exporter_positive_parsed = reference.parse_network(exporter_positive_path.read_bytes(), provenance)
        require(exporter_positive_parsed.output_weights[0] == 1, "positive half-away rounding")
        require(exporter_positive_parsed.output_weights[1] == -1, "negative half-away rounding")

        exporter_rows = verify_exporter_negatives(output, provenance)
        parser_rows = verify_parser_matrix(args.binary, output, synthetic_bytes, provenance)
        synthetic_parity = verify_cpp_parity(
            args.binary, synthetic_path, parsed, provenance, records
        )
        training = verify_training_resume(
            args.python,
            args.binary,
            args.repo,
            output,
            records,
            records_path,
            args.source_commit,
            args.source_tree,
            args.src_tree,
        )
        result = {
            "schema": "crazyhouse-nnue-v2-productive-scalar-trainer-verification/v1",
            "status": "PASS_ENGINEERING_ONLY",
            "source": {
                "commit": args.source_commit,
                "tree": args.source_tree,
                "src_tree": args.src_tree,
            },
            "records": {
                "count": len(records),
                "bytes": records_path.stat().st_size,
                "sha256": sha256_file(records_path),
                "labels_consumed_by_trainer": False,
            },
            "synthetic_container": {
                "bytes": len(synthetic_bytes),
                "sha256": sha256_bytes(synthetic_bytes),
                "parse_reserialize_byte_equal": True,
            },
            "exporter_positive": {
                "bytes": exporter_positive_path.stat().st_size,
                "sha256": sha256_file(exporter_positive_path),
                "half_away_rounding": True,
            },
            "exporter_negatives": exporter_rows,
            "parser_negatives": parser_rows,
            "synthetic_parity": synthetic_parity,
            "training_resume": training,
            "boundaries": {
                "training_admissible": False,
                "model_selection_credit": False,
                "strength_credit": False,
                "simd_productive_proven": False,
                "incremental_productive_proven": False,
                "openbench_used": False,
                "release_credit": False,
                "g12_closed": False,
            },
        }
        result_path = output / "verification.json"
        write_generated(result_path, canonical_json(result))
        print(
            "PASS crazyhouse_v2_productive_verify"
            f" parser_negatives={len(parser_rows)}"
            f" exporter_negatives={len(exporter_rows)}"
            " physical_records=42 perspectives=84"
            f" resume_negatives={len(training['resume_negatives'])}"
            f" network_sha256={training['final_network_sha256']}"
            " training_admissible=false model_selection_credit=false"
            " strength_credit=false g12_closed=false"
        )
        return 0
    except (VerificationError, reference.ProductiveReferenceError, OSError, ValueError, KeyError) as error:
        print(f"FAIL crazyhouse_v2_productive_verify: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
