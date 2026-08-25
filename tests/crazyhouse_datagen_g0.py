#!/usr/bin/env python3
"""Formal local G0 harness for the separate Crazyhouse physical DATAGEN producer.

The harness never opens a network connection and never invokes OpenBench.  It
uses fresh, caller-owned output paths, preserves every negative-case artifact,
and kills only the exact child PID it creates for the crash/retry control.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import uuid


ROOT = Path(__file__).resolve().parents[1]
CODEC_PATH = ROOT / "tools" / "datagen" / "crazyhouse_physical_v1.py"
GOLDEN_UNIT_PATH = ROOT / "tests" / "crazyhouse_physical_v1_unit.py"

SELECTION_POLICY_SHA256 = "e5b39bd15c78b00ce0f6acc01da49103e71685c95f7b6fbde09334933d8bfb18"
EXPECTED_CORPUS_SHA256 = "4113b930d08d6037de8667b9919f8944882d527856b860aaf92bbf1088aa0cdd"
EXPECTED_SCHEMA_SHA256 = "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
EXPECTED_CONTRACT_SHA256 = "dc6af06c3d18fb2ff06e27e35ab691e35555ef03a5948b23cb2a198e6b89eb96"
EXPECTED_NORMAL_ENGINE_SHA256 = "aef7a64760c9f4f23cb15b4402130dd6a51c0843a3f8cb00af76e90bb813004b"
EXPECTED_NORMAL_ENGINE_BYTES = 103_068_074
EXPECTED_TRAJECTORIES = 11
EXPECTED_RECORDS = 42
EXPECTED_CHUNK_BYTES = 11_136
CAMPAIGN_ID = "50000000-0000-4000-8000-000000000002"
INPUT_REPO_PATH = "tests/crazyhouse/data/crazyhouse-datagen-g0-trajectories-v1.tsv"
ARTIFACT_REPO_PATH = "artifacts/crazyhouse-stockfish-datagen.exe"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module spec: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


codec = load_module("crazyhouse_physical_v1_g0_codec", CODEC_PATH)
golden_unit = load_module("crazyhouse_physical_v1_g0_goldens", GOLDEN_UNIT_PATH)


class G0Error(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise G0Error(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def pretty_json(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def deterministic_uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.UUID("70000000-0000-4000-8000-000000000001"), label))


def deterministic_challenge(label: str) -> str:
    return hashlib.sha256(("Crazyhouse DATAGEN G0 challenge v1\0" + label).encode("utf-8")).hexdigest()[:32]


def canonical_slug(value: str) -> str:
    output = "".join(character if character.isalnum() else "-" for character in value.lower())
    while "--" in output:
        output = output.replace("--", "-")
    return output.strip("-")


@dataclass(frozen=True)
class Pins:
    source_commit: str
    source_tree: str
    src_tree: str
    build_recipe_sha256: str
    toolchain_sha256: str


@dataclass(frozen=True)
class Captured:
    label: str
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    started_utc: str
    finished_utc: str
    elapsed_seconds: float


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.artifact_dir = args.artifact_dir.resolve()
        self.logs_dir = self.artifact_dir / "logs"
        self.inputs_dir = self.artifact_dir / "mutated-inputs"
        self.chunks_dir = self.artifact_dir / "chunks"
        self.matrix: list[dict[str, Any]] = []
        self.started_utc = utc_now()
        self.pins = Pins(
            source_commit=args.source_commit,
            source_tree=args.source_tree,
            src_tree=args.src_tree,
            build_recipe_sha256=args.build_recipe_sha256,
            toolchain_sha256=args.toolchain_sha256,
        )
        require(not self.artifact_dir.exists(), "formal artifact directory already exists")
        self.logs_dir.mkdir(parents=True)
        self.inputs_dir.mkdir()
        self.chunks_dir.mkdir()

        self.producer = args.producer.resolve(strict=True)
        self.dirty_producer = args.dirty_producer.resolve(strict=True)
        self.normal_engine = args.normal_engine.resolve(strict=True)
        self.schema = args.schema.resolve(strict=True)
        self.contract = args.contract.resolve(strict=True)
        self.corpus = args.corpus.resolve(strict=True)
        self.independent_verifier = args.independent_verifier.resolve(strict=True)
        self.schema_bytes = self.schema.read_bytes()
        self.contract_bytes = self.contract.read_bytes()
        self.corpus_bytes = self.corpus.read_bytes()

    def pass_row(self, name: str, **evidence: Any) -> None:
        self.matrix.append({"name": name, "status": "PASS", "evidence": evidence})

    def capture(
        self,
        label: str,
        argv: Sequence[str | Path],
        *,
        env_delta: Mapping[str, str] | None = None,
        timeout: float = 120.0,
    ) -> Captured:
        command = tuple(str(value) for value in argv)
        environment = os.environ.copy()
        if env_delta:
            environment.update(env_delta)
        started = utc_now()
        before = time.monotonic()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                env=environment,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            self.write_log(label, stdout, stderr)
            raise G0Error(f"{label}: timeout after {timeout} seconds") from exc
        elapsed = time.monotonic() - before
        captured = Captured(
            label=label,
            argv=command,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            started_utc=started,
            finished_utc=utc_now(),
            elapsed_seconds=elapsed,
        )
        self.write_log(label, captured.stdout, captured.stderr)
        return captured

    def write_log(self, label: str, stdout: bytes, stderr: bytes) -> None:
        slug = canonical_slug(label)
        write_new(self.logs_dir / f"{slug}.stdout.bin", stdout)
        write_new(self.logs_dir / f"{slug}.stderr.bin", stderr)

    def require_producer_failure(self, captured: Captured, label: str) -> None:
        require(captured.returncode != 0, f"{label}: producer unexpectedly succeeded")
        require(captured.stdout == b"", f"{label}: producer emitted stdout on rejection")
        require(
            captured.stderr.startswith(b"ERROR crazyhouse-datagen-v1: ")
            and captured.stderr.endswith(b"\n")
            and b"\r" not in captured.stderr,
            f"{label}: noncanonical producer diagnostic",
        )

    def generation_argv(
        self,
        producer: Path,
        *,
        label: str,
        output: Path,
        input_path: Path | None = None,
        schema_path: Path | None = None,
        contract_path: Path | None = None,
        challenge: str | None = None,
        chunk_id: str | None = None,
        expected_trajectories: int = EXPECTED_TRAJECTORIES,
        expected_records: int = EXPECTED_RECORDS,
        pause_ms: int | None = None,
    ) -> tuple[list[str], str, str]:
        actual_challenge = challenge or deterministic_challenge(label)
        actual_chunk_id = chunk_id or deterministic_uuid(label)
        argv = [
            str(producer),
            "--generate-trajectories-v1",
            "--artifact-repo-path",
            ARTIFACT_REPO_PATH,
            "--campaign-id",
            CAMPAIGN_ID,
            "--challenge",
            actual_challenge,
            "--chunk-id",
            actual_chunk_id,
            "--chunk-index",
            "0",
            "--contract",
            str(contract_path or self.contract),
            "--expected-records",
            str(expected_records),
            "--expected-trajectories",
            str(expected_trajectories),
            "--input",
            str(input_path or self.corpus),
            "--input-repo-path",
            INPUT_REPO_PATH,
            "--opening-kind",
            "authority-g0-trajectories",
            "--output",
            str(output),
            "--schema",
            str(schema_path or self.schema),
            "--seed",
            "2026082401",
            "--selection-policy-sha256",
            SELECTION_POLICY_SHA256,
        ]
        if pause_ms is not None:
            argv.extend(("--test-pause-after-partial-ms", str(pause_ms)))
        return argv, actual_challenge, actual_chunk_id

    def output_namespace(self, output: Path, chunk_id: str) -> tuple[Path, ...]:
        suffix = f".partial.{chunk_id}"
        return (
            output,
            Path(str(output) + ".capability.json"),
            Path(str(output) + ".provenance.json"),
            Path(str(output) + suffix),
            Path(str(output) + ".capability.json" + suffix),
            Path(str(output) + ".provenance.json" + suffix),
        )

    def require_namespace_absent(self, output: Path, chunk_id: str, label: str) -> None:
        observed = [str(path) for path in self.output_namespace(output, chunk_id) if path.exists()]
        require(not observed, f"{label}: rejected generation created output: {observed}")

    def authenticate_capability(self, payload: bytes, producer: Path, challenge: str) -> Mapping[str, Any]:
        response = codec.validate_capability_response_bytes(
            payload,
            contract_bytes=self.contract_bytes,
            expected_challenge=challenge,
        )
        producer_bytes = producer.read_bytes()
        require(response["artifact_bytes"] == len(producer_bytes), "capability artifact byte count/self mismatch")
        require(response["artifact_sha256"] == sha256_bytes(producer_bytes), "capability artifact self-hash mismatch")
        require(response["artifact_role"] == "crazyhouse-physical-datagen", "capability producer role mismatch")
        require(response["source_commit"] == self.pins.source_commit, "capability source commit mismatch")
        require(response["source_tree"] == self.pins.source_tree, "capability source tree mismatch")
        require(response["src_tree"] == self.pins.src_tree, "capability src tree mismatch")
        require(response["build_recipe_sha256"] == self.pins.build_recipe_sha256, "capability build recipe mismatch")
        require(response["toolchain_sha256"] == self.pins.toolchain_sha256, "capability toolchain mismatch")
        require(response["source_dirty"] is False, "capability source is dirty")
        return response

    def expect_capability_rejection(self, label: str, payload: bytes, challenge: str) -> None:
        try:
            self.authenticate_capability(payload, self.producer, challenge)
        except (KeyError, TypeError, ValueError, codec.FormatError, G0Error):
            self.pass_row(label, verifier="fail-closed")
            return
        raise G0Error(f"{label}: malformed or mismatched capability was admitted")

    def mutate_response(self, response: Mapping[str, Any], key: str, value: Any) -> bytes:
        mutated = dict(response)
        mutated[key] = value
        return canonical_json(mutated)

    def check_static_inputs(self) -> None:
        for label, payload, expected in (
            ("physical schema", self.schema_bytes, EXPECTED_SCHEMA_SHA256),
            ("capability contract", self.contract_bytes, EXPECTED_CONTRACT_SHA256),
            ("trajectory stream", self.corpus_bytes, EXPECTED_CORPUS_SHA256),
        ):
            require(sha256_bytes(payload) == expected, f"{label} identity drifted")
            require(b"\r" not in payload and payload.endswith(b"\n"), f"{label} LF framing drifted")
        require(self.normal_engine.stat().st_size == EXPECTED_NORMAL_ENGINE_BYTES, "normal engine byte count drifted")
        require(sha256_file(self.normal_engine) == EXPECTED_NORMAL_ENGINE_SHA256, "normal engine SHA-256 drifted")
        for label, value, width in (
            ("source commit", self.pins.source_commit, 40),
            ("source tree", self.pins.source_tree, 40),
            ("src tree", self.pins.src_tree, 40),
            ("build recipe", self.pins.build_recipe_sha256, 64),
            ("toolchain", self.pins.toolchain_sha256, 64),
        ):
            require(len(value) == width and value == value.lower(), f"{label} pin width/case")
            bytes.fromhex(value)
        require(self.producer != self.normal_engine and self.producer != self.dirty_producer, "artifact boundary collapsed")
        self.pass_row(
            "frozen-input-identities",
            schema_sha256=EXPECTED_SCHEMA_SHA256,
            contract_sha256=EXPECTED_CONTRACT_SHA256,
            corpus_sha256=EXPECTED_CORPUS_SHA256,
            trajectories=EXPECTED_TRAJECTORIES,
            records=EXPECTED_RECORDS,
        )
        self.pass_row(
            "normal-engine-authenticated-negative-control",
            bytes=EXPECTED_NORMAL_ENGINE_BYTES,
            sha256=EXPECTED_NORMAL_ENGINE_SHA256,
        )

    def capability_matrix(self) -> tuple[bytes, Mapping[str, Any], str]:
        challenge = deterministic_challenge("capability-positive")
        positive = self.capture(
            "capability-positive",
            (self.producer, "--datagen-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(positive.returncode == 0 and positive.stderr == b"", "positive capability process failed")
        response = self.authenticate_capability(positive.stdout, self.producer, challenge)
        require(positive.stdout.count(b"\n") == 1 and b"\r" not in positive.stdout, "capability framing is not one LF line")
        self.pass_row(
            "capability-positive",
            challenge=challenge,
            response_sha256=sha256_bytes(positive.stdout),
            producer_sha256=response["artifact_sha256"],
        )

        invocation_negatives = (
            ("capability-missing-challenge", (self.producer, "--datagen-capabilities-v1")),
            (
                "capability-uppercase-challenge",
                (self.producer, "--datagen-capabilities-v1", "--challenge", challenge.upper()),
            ),
            (
                "capability-wrong-challenge-width",
                (self.producer, "--datagen-capabilities-v1", "--challenge", challenge[:-1]),
            ),
            (
                "capability-unknown-argument",
                (self.producer, "--datagen-capabilities-v1", "--challenge", challenge, "--unknown", "1"),
            ),
        )
        for label, argv in invocation_negatives:
            rejected = self.capture(label, argv, timeout=10)
            self.require_producer_failure(rejected, label)
            self.pass_row(label, returncode=rejected.returncode, stdout_bytes=0, output_created=False)

        normal = self.capture(
            "normal-engine-capability-negative",
            (self.normal_engine, "--datagen-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(not normal.stderr, "normal engine capability negative wrote stderr")
        try:
            self.authenticate_capability(normal.stdout, self.normal_engine, challenge)
        except (KeyError, TypeError, ValueError, codec.FormatError, G0Error):
            pass
        else:
            raise G0Error("normal UCI engine was admitted as DATAGEN producer")
        self.pass_row(
            "normal-engine-capability-negative",
            returncode=normal.returncode,
            stdout_sha256=sha256_bytes(normal.stdout),
            advertised_datagen_capability=False,
        )

        dirty = self.capture(
            "dirty-producer-capability",
            (self.dirty_producer, "--datagen-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(dirty.returncode == 0 and dirty.stderr == b"", "dirty control capability invocation failed")
        try:
            dirty_document = json.loads(dirty.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise G0Error(f"dirty capability is malformed: {exc}") from exc
        require(dirty.stdout == canonical_json(dirty_document), "dirty capability is not canonical")
        require(set(dirty_document) == set(response), "dirty capability key set drifted")
        allowed_differences = {"artifact_bytes", "artifact_sha256", "source_dirty"}
        require(
            all(dirty_document[key] == response[key] for key in response if key not in allowed_differences),
            "dirty capability differs beyond artifact identity/source_dirty",
        )
        require(dirty_document["source_dirty"] is True, "dirty producer does not report dirty source")
        require(dirty_document["artifact_bytes"] == self.dirty_producer.stat().st_size, "dirty producer byte binding")
        require(dirty_document["artifact_sha256"] == sha256_file(self.dirty_producer), "dirty producer self hash")
        self.pass_row(
            "dirty-producer-identity-reports-dirty",
            bytes=dirty_document["artifact_bytes"],
            sha256=dirty_document["artifact_sha256"],
            source_dirty=dirty_document["source_dirty"],
        )
        self.expect_capability_rejection("dirty-producer-verifier-negative", dirty.stdout, challenge)

        response_mutations: tuple[tuple[str, str, Any], ...] = (
            ("capability-producer-role-mismatch", "artifact_role", "wrong-role"),
            ("capability-artifact-self-hash-mismatch", "artifact_sha256", "00" * 32),
            ("capability-source-mismatch", "source_commit", "00" * 20),
            ("capability-toolchain-mismatch", "toolchain_sha256", "00" * 32),
            ("capability-rule-profile-mismatch", "rule_profile_sha256", "00" * 32),
            ("capability-schema-id-mismatch", "physical_schema_id", "wrong-schema"),
            ("capability-schema-hash-mismatch", "physical_schema_sha256", "00" * 32),
            ("capability-record-size-mismatch", "record_bytes", 255),
            ("capability-byte-order-mismatch", "byte_order", "big-endian"),
            ("capability-feature-row-source-rejected", "canonical_source", "nnue-feature-rows"),
            ("capability-production-unauthorized", "production_generation_authorized", False),
        )
        for label, key, value in response_mutations:
            self.expect_capability_rejection(label, self.mutate_response(response, key, value), challenge)

        extra_key = dict(response)
        extra_key["unexpected"] = True
        self.expect_capability_rejection("capability-extra-key", canonical_json(extra_key), challenge)
        self.expect_capability_rejection("capability-response-on-stderr", b"", challenge)
        self.expect_capability_rejection("capability-extra-stdout-line", positive.stdout + b"{}\n", challenge)
        self.expect_capability_rejection(
            "capability-noncanonical-json",
            (json.dumps(response, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            challenge,
        )
        duplicate = b'{"artifact_bytes":1,' + positive.stdout[1:]
        self.expect_capability_rejection("capability-duplicate-json-key", duplicate, challenge)
        self.expect_capability_rejection(
            "capability-stale-response",
            positive.stdout,
            deterministic_challenge("different-capability-request"),
        )
        return positive.stdout, response, challenge

    def parse_generation_result(self, captured: Captured, output: Path, challenge: str, chunk_id: str) -> dict[str, Any]:
        require(captured.returncode == 0 and captured.stderr == b"", f"{captured.label}: generation process failed")
        require(captured.stdout.count(b"\n") == 1 and b"\r" not in captured.stdout, f"{captured.label}: result framing")
        try:
            result = json.loads(captured.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise G0Error(f"{captured.label}: malformed generation result: {exc}") from exc
        require(captured.stdout == canonical_json(result), f"{captured.label}: generation result is not canonical")
        require(
            set(result)
            == {
                "artifact_sha256",
                "capability_sha256",
                "chunk_bytes",
                "chunk_id",
                "chunk_sha256",
                "output",
                "provenance_sha256",
                "records",
                "schema",
                "status",
                "trajectories",
            },
            f"{captured.label}: generation result keys",
        )
        require(result["schema"] == "crazyhouse-datagen-generation-result/v1" and result["status"] == "committed", f"{captured.label}: result status")
        require(result["artifact_sha256"] == sha256_file(self.producer), f"{captured.label}: result artifact")
        require(result["chunk_id"] == chunk_id and result["output"] == output.name, f"{captured.label}: result output identity")
        require(result["records"] == EXPECTED_RECORDS and result["trajectories"] == EXPECTED_TRAJECTORIES, f"{captured.label}: result counts")
        require(result["chunk_bytes"] == EXPECTED_CHUNK_BYTES, f"{captured.label}: chunk byte count")
        capability_path = Path(str(output) + ".capability.json")
        provenance_path = Path(str(output) + ".provenance.json")
        require(output.is_file() and capability_path.is_file() and provenance_path.is_file(), f"{captured.label}: committed artifact missing")
        require(result["chunk_sha256"] == sha256_file(output), f"{captured.label}: chunk result digest")
        require(result["capability_sha256"] == sha256_file(capability_path), f"{captured.label}: capability result digest")
        require(result["provenance_sha256"] == sha256_file(provenance_path), f"{captured.label}: provenance result digest")
        partials = self.output_namespace(output, chunk_id)[3:]
        require(not any(path.exists() for path in partials), f"{captured.label}: committed output retained partial")
        sidecar = capability_path.read_bytes()
        self.authenticate_capability(sidecar, self.producer, challenge)
        return result

    def validate_chunk(self, output: Path, challenge: str) -> dict[str, Any]:
        chunk_bytes = output.read_bytes()
        capability_path = Path(str(output) + ".capability.json")
        provenance_path = Path(str(output) + ".provenance.json")
        capability_bytes = capability_path.read_bytes()
        provenance_bytes = provenance_path.read_bytes()
        response = self.authenticate_capability(capability_bytes, self.producer, challenge)
        parsed = codec.parse_chunk(
            chunk_bytes,
            schema_bytes=self.schema_bytes,
            provenance_bytes=provenance_bytes,
        )
        require(len(parsed.records) == EXPECTED_RECORDS, "decoded record count")
        require(len({record.trajectory_id for record in parsed.records}) == EXPECTED_TRAJECTORIES, "decoded trajectory count")
        require(parsed.producer_capability_sha256.hex() == sha256_bytes(capability_bytes), "chunk/capability binding")
        require(parsed.provenance_sha256.hex() == sha256_bytes(provenance_bytes), "chunk/provenance binding")
        provenance = codec.validate_provenance_bytes(
            provenance_bytes,
            chunk_id=parsed.chunk_id,
            campaign_id=parsed.campaign_id,
        )
        require(provenance["source_commit"] == self.pins.source_commit, "provenance source commit")
        require(provenance["source_tree"] == self.pins.source_tree, "provenance source tree")
        require(provenance["src_tree"] == self.pins.src_tree, "provenance src tree")
        require(provenance["source_dirty"] is False, "provenance source dirty")
        require(provenance["producer_artifact"]["sha256"] == response["artifact_sha256"], "provenance producer binding")
        require(provenance["producer_capability"]["challenge"] == challenge, "provenance challenge binding")
        require(provenance["generation_settings"]["training_admissible"] is False, "G0 chunk marked training admissible")
        require(provenance["generation_settings"]["fixture_only"] is True, "G0 chunk not marked fixture-only")
        require(provenance["network"]["used"] is False and provenance["teacher"]["network_used"] is False, "G0 fixture claims a network")

        golden_records = golden_unit.golden_records()
        require(len(golden_records) == len(parsed.records), "G8 golden record count drifted")
        raw_records = [
            chunk_bytes[codec.HEADER_SIZE + index * codec.RECORD_SIZE : codec.HEADER_SIZE + (index + 1) * codec.RECORD_SIZE]
            for index in range(EXPECTED_RECORDS)
        ]
        for index, (raw, decoded, golden) in enumerate(zip(raw_records, parsed.records, golden_records)):
            require(codec.encode_record(decoded) == raw, f"record {index}: reference round-trip")
            golden_raw = golden_unit.codec.encode_record(golden)
            require(
                raw[:180] == golden_raw[:180] and raw[244:252] == golden_raw[244:252],
                f"record {index}: physical fields differ from frozen G8 golden",
            )
        return {
            "chunk_sha256": sha256_bytes(chunk_bytes),
            "chunk_bytes": len(chunk_bytes),
            "capability_sha256": sha256_bytes(capability_bytes),
            "provenance_sha256": sha256_bytes(provenance_bytes),
            "records": len(parsed.records),
            "trajectories": len({record.trajectory_id for record in parsed.records}),
            "round_trip_records": len(raw_records),
            "physical_golden_matches": len(raw_records),
        }

    def generate_primary(self) -> tuple[Path, str, str, dict[str, Any]]:
        label = "primary-generation"
        output = self.chunks_dir / "g0-primary.chp1"
        argv, challenge, chunk_id = self.generation_argv(self.producer, label=label, output=output)
        captured = self.capture(label, argv)
        result = self.parse_generation_result(captured, output, challenge, chunk_id)

        matching = self.capture(
            "primary-capability-requery",
            (self.producer, "--datagen-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(matching.returncode == 0 and matching.stderr == b"", "primary capability requery failed")
        require(matching.stdout == Path(str(output) + ".capability.json").read_bytes(), "generation/standalone capability bytes differ")
        summary = self.validate_chunk(output, challenge)
        self.pass_row(
            "primary-generation-and-reference-codec",
            **summary,
            generation_result_sha256=sha256_bytes(captured.stdout),
            generation_capability_same_artifact_bytes=True,
            make_undo_guard_executed_by_authenticated_producer=True,
        )

        independent_output = self.artifact_dir / "independent-verification.json"
        independent = self.capture(
            "primary-independent-verifier",
            (
                sys.executable,
                self.independent_verifier,
                "--producer",
                self.producer,
                "--schema",
                self.schema,
                "--contract",
                self.contract,
                "--corpus",
                self.corpus,
                "--chunk",
                output,
                "--capability",
                Path(str(output) + ".capability.json"),
                "--provenance",
                Path(str(output) + ".provenance.json"),
                "--challenge",
                challenge,
                "--output",
                independent_output,
            ),
        )
        require(independent.returncode == 0 and independent.stderr == b"", "independent verifier failed")
        require(independent.stdout.startswith(b"PASS_CRAZYHOUSE_DATAGEN_G0_INDEPENDENT "), "independent verifier did not report PASS")
        independent_result = json.loads(independent_output.read_text(encoding="utf-8"))
        require(independent_result["status"] == "PASS", "independent result status")
        require(independent_result["chunk_sha256"] == summary["chunk_sha256"], "independent chunk digest")
        require(independent_result["producer_sha256"] == sha256_file(self.producer), "independent producer digest")
        self.pass_row(
            "independent-verifier",
            result_sha256=sha256_file(independent_output),
            reference_codec_imported=independent_result["reference_codec_imported"],
            producer_code_imported=independent_result["producer_code_imported"],
            records=independent_result["record_count"],
            trajectories=independent_result["trajectory_count"],
        )
        return output, challenge, chunk_id, result

    def mutation_file(self, label: str, payload: bytes, suffix: str) -> Path:
        path = self.inputs_dir / f"{canonical_slug(label)}{suffix}"
        write_new(path, payload)
        return path

    def mutated_corpus(self, label: str, mutator: Callable[[list[list[str]]], None]) -> Path:
        lines = self.corpus_bytes.decode("ascii").splitlines()
        rows = [line.split("\t") for line in lines]
        mutator(rows)
        payload = ("\n".join("\t".join(row) for row in rows) + "\n").encode("ascii")
        return self.mutation_file(label, payload, ".tsv")

    def rejected_generation(
        self,
        label: str,
        *,
        input_path: Path | None = None,
        schema_path: Path | None = None,
        contract_path: Path | None = None,
        expected_records: int = EXPECTED_RECORDS,
        producer: Path | None = None,
    ) -> None:
        output = self.chunks_dir / f"{canonical_slug(label)}.chp1"
        argv, _, chunk_id = self.generation_argv(
            producer or self.producer,
            label=label,
            output=output,
            input_path=input_path,
            schema_path=schema_path,
            contract_path=contract_path,
            expected_records=expected_records,
        )
        captured = self.capture(label, argv)
        self.require_producer_failure(captured, label)
        self.require_namespace_absent(output, chunk_id, label)
        self.pass_row(label, returncode=captured.returncode, output_created=False)

    def generation_negative_matrix(self) -> None:
        wrong_schema = self.mutation_file("wrong-schema", self.schema_bytes + b" ", ".json")
        wrong_contract = self.mutation_file("wrong-contract", self.contract_bytes + b" ", ".json")
        malformed = self.corpus_bytes.replace(
            b"CRAZYHOUSE_TRAJECTORIES_V1\t11\t42",
            b"CRAZYHOUSE_TRAJECTORIES_V1\t11",
            1,
        )
        malformed_path = self.mutation_file("malformed-tsv-framing", malformed, ".tsv")

        def duplicate_game(rows: list[list[str]]) -> None:
            rows[2][1] = rows[1][1]

        def duplicate_trajectory(rows: list[list[str]]) -> None:
            rows[2][2] = rows[1][2]

        def illegal_move(rows: list[list[str]]) -> None:
            rows[1][8] = "e2e5"

        def terminal_disagreement(rows: list[list[str]]) -> None:
            rows[1][5] = "1"

        duplicate_game_path = self.mutated_corpus("duplicate-game-id", duplicate_game)
        duplicate_trajectory_path = self.mutated_corpus("duplicate-trajectory-id", duplicate_trajectory)
        illegal_move_path = self.mutated_corpus("illegal-move", illegal_move)
        terminal_disagreement_path = self.mutated_corpus("terminal-disagreement", terminal_disagreement)

        self.rejected_generation("generation-wrong-schema-bytes", schema_path=wrong_schema)
        self.rejected_generation("generation-wrong-contract-bytes", contract_path=wrong_contract)
        self.rejected_generation("generation-malformed-tsv-framing", input_path=malformed_path)
        self.rejected_generation("generation-duplicate-game-id", input_path=duplicate_game_path)
        self.rejected_generation("generation-duplicate-trajectory-id", input_path=duplicate_trajectory_path)
        self.rejected_generation("generation-illegal-move", input_path=illegal_move_path)
        self.rejected_generation("generation-wrong-expected-count", expected_records=EXPECTED_RECORDS + 1)
        self.rejected_generation("generation-terminal-disagreement", input_path=terminal_disagreement_path)
        self.rejected_generation("generation-dirty-source", producer=self.dirty_producer)

        normal_output = self.chunks_dir / "normal-engine-generation-negative.chp1"
        normal_argv, _, normal_chunk_id = self.generation_argv(
            self.normal_engine,
            label="normal-engine-generation-negative",
            output=normal_output,
        )
        normal = self.capture("normal-engine-generation-negative", normal_argv, timeout=20)
        self.require_namespace_absent(normal_output, normal_chunk_id, "normal-engine-generation-negative")
        require(normal.stdout or normal.stderr or normal.returncode != 0, "normal engine generation negative produced no diagnostic")
        require(
            b"crazyhouse-datagen-generation-result/v1" not in normal.stdout
            and b"crazyhouse-datagen-capability-response/v1" not in normal.stdout,
            "normal engine emitted a DATAGEN protocol response",
        )
        self.pass_row(
            "normal-engine-generation-negative",
            returncode=normal.returncode,
            output_created=False,
            datagen_result_emitted=False,
            datagen_capability_emitted=False,
        )

        preexisting_output = self.chunks_dir / "preexisting-final.chp1"
        marker = b"G0_PREEXISTING_FINAL_MUST_SURVIVE\n"
        write_new(preexisting_output, marker)
        preexisting_argv, _, preexisting_chunk_id = self.generation_argv(
            self.producer,
            label="preexisting-final",
            output=preexisting_output,
        )
        preexisting = self.capture("generation-preexisting-final", preexisting_argv)
        self.require_producer_failure(preexisting, "generation-preexisting-final")
        require(preexisting_output.read_bytes() == marker, "preexisting final was modified")
        require(
            not any(path.exists() for path in self.output_namespace(preexisting_output, preexisting_chunk_id)[1:]),
            "preexisting final rejection created sidecars/partials",
        )
        self.pass_row("generation-preexisting-final", marker_sha256=sha256_bytes(marker), preserved=True)

        partial_output = self.chunks_dir / "preexisting-partial.chp1"
        partial_argv, _, partial_chunk_id = self.generation_argv(
            self.producer,
            label="preexisting-partial",
            output=partial_output,
        )
        partial_path = self.output_namespace(partial_output, partial_chunk_id)[3]
        partial_marker = b"G0_PREEXISTING_PARTIAL_MUST_SURVIVE\n"
        write_new(partial_path, partial_marker)
        partial = self.capture("generation-preexisting-partial", partial_argv)
        self.require_producer_failure(partial, "generation-preexisting-partial")
        require(partial_path.read_bytes() == partial_marker, "preexisting partial was modified")
        require(
            not any(path.exists() for path in self.output_namespace(partial_output, partial_chunk_id)[:3]),
            "preexisting partial rejection created finals",
        )
        self.pass_row("generation-preexisting-partial", marker_sha256=sha256_bytes(partial_marker), preserved=True)

    def rerun_primary(self, output: Path, challenge: str, chunk_id: str) -> None:
        paths = self.output_namespace(output, chunk_id)[:3]
        before = {str(path): (path.stat().st_size, sha256_file(path)) for path in paths}
        argv, _, _ = self.generation_argv(
            self.producer,
            label="primary-generation",
            output=output,
            challenge=challenge,
            chunk_id=chunk_id,
        )
        rerun = self.capture("primary-rerun-rejected", argv)
        self.require_producer_failure(rerun, "primary-rerun-rejected")
        after = {str(path): (path.stat().st_size, sha256_file(path)) for path in paths}
        require(before == after, "primary rerun changed committed identities")
        require(not any(path.exists() for path in self.output_namespace(output, chunk_id)[3:]), "primary rerun created partials")
        self.pass_row("primary-rerun-rejected", committed_identities_unchanged=True, artifact_count=len(paths))

    def expect_codec_rejection(
        self,
        label: str,
        payload: bytes,
        provenance: bytes,
    ) -> None:
        try:
            codec.parse_chunk(payload, schema_bytes=self.schema_bytes, provenance_bytes=provenance)
        except (KeyError, TypeError, ValueError, codec.FormatError):
            self.pass_row(label, verifier="reference-codec-fail-closed")
            return
        raise G0Error(f"{label}: malformed chunk was admitted")

    def artifact_negative_matrix(self, output: Path, challenge: str) -> None:
        chunk = output.read_bytes()
        capability = Path(str(output) + ".capability.json").read_bytes()
        provenance = Path(str(output) + ".provenance.json").read_bytes()

        corrupt = bytearray(chunk)
        corrupt[codec.HEADER_SIZE + 60] ^= 1
        record = bytes(corrupt[codec.HEADER_SIZE : codec.HEADER_SIZE + codec.RECORD_SIZE])
        try:
            codec.decode_record(record)
        except (ValueError, codec.FormatError):
            pass
        else:
            raise G0Error("corrupt record CRC32C was admitted")
        self.expect_codec_rejection("chunk-corrupt-record-crc32c", bytes(corrupt), provenance)
        self.expect_codec_rejection("chunk-truncated", chunk[:-1], provenance)
        self.expect_codec_rejection("chunk-appended", chunk + b"x", provenance)

        header_mismatch = bytearray(chunk)
        header_mismatch[208] ^= 1
        struct.pack_into("<I", header_mismatch, 252, codec.crc32c(header_mismatch[:252]))
        footer_offset = len(header_mismatch) - codec.FOOTER_SIZE
        header_mismatch[footer_offset + 72 : footer_offset + 104] = hashlib.sha256(header_mismatch[: codec.HEADER_SIZE]).digest()
        struct.pack_into(
            "<I",
            header_mismatch,
            footer_offset + 124,
            codec.crc32c(header_mismatch[footer_offset : footer_offset + 124]),
        )
        self.expect_codec_rejection("chunk-capability-header-mismatch", bytes(header_mismatch), provenance)

        provenance_document = json.loads(provenance.decode("utf-8"))
        provenance_document["producer_capability"]["sha256"] = "00" * 32
        wrong_provenance = canonical_json(provenance_document)
        self.expect_codec_rejection("chunk-provenance-header-mismatch", chunk, wrong_provenance)

        other_challenge = deterministic_challenge("other-valid-sidecar")
        other = self.capture(
            "other-valid-capability-sidecar",
            (self.producer, "--datagen-capabilities-v1", "--challenge", other_challenge),
            timeout=10,
        )
        require(other.returncode == 0 and not other.stderr, "other valid capability failed")
        self.authenticate_capability(other.stdout, self.producer, other_challenge)
        require(sha256_bytes(other.stdout) != chunk[208:240].hex(), "different challenge did not change capability digest")
        try:
            require(sha256_bytes(other.stdout) == chunk[208:240].hex(), "capability sidecar/header mismatch")
        except G0Error:
            self.pass_row("capability-sidecar-header-mismatch", verifier="explicit-join-fail-closed")
        else:
            raise G0Error("mismatched valid capability sidecar was admitted")
        require(sha256_bytes(capability) == chunk[208:240].hex(), "primary capability/header positive join drifted")

    def kill_retry_matrix(self) -> None:
        label = "kill-after-partials"
        output = self.chunks_dir / "kill-after-partials.chp1"
        argv, challenge, chunk_id = self.generation_argv(
            self.producer,
            label=label,
            output=output,
            pause_ms=30_000,
        )
        command = tuple(str(value) for value in argv)
        environment = os.environ.copy()
        environment["CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        started_utc = utc_now()
        before = time.monotonic()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            creationflags=creationflags,
        )
        owned_pid = process.pid
        partials = self.output_namespace(output, chunk_id)[3:]
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if all(path.is_file() for path in partials):
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.write_log(label, stdout, stderr)
                raise G0Error(f"fault-injection child {owned_pid} exited before all partials appeared")
            time.sleep(0.05)
        else:
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
            self.write_log(label, stdout, stderr)
            raise G0Error(f"fault-injection child {owned_pid} did not create all partials")

        partial_before = {
            str(path): {"bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in partials
        }
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        elapsed = time.monotonic() - before
        self.write_log(label, stdout, stderr)
        require(process.returncode != 0, "killed producer returned success")
        require(not stdout and not stderr, "producer emitted output before injected pause")
        require(not any(path.exists() for path in self.output_namespace(output, chunk_id)[:3]), "killed producer published finals")
        partial_after = {
            str(path): {"bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in partials
        }
        require(partial_after == partial_before, "killed partial identities changed")
        self.pass_row(
            "killed-partials-retained",
            owned_pid=owned_pid,
            child_started_utc=started_utc,
            child_killed_utc=utc_now(),
            elapsed_seconds=round(elapsed, 6),
            returncode=process.returncode,
            partials=partial_after,
            finals_published=False,
            foreign_process_mutation=False,
        )

        same = self.capture(
            "same-chunk-retry-rejected",
            argv,
            env_delta={"CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION": "1"},
        )
        self.require_producer_failure(same, "same-chunk-retry-rejected")
        require(
            {
                str(path): {"bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in partials
            }
            == partial_before,
            "same-ID retry changed quarantine partials",
        )
        require(not any(path.exists() for path in self.output_namespace(output, chunk_id)[:3]), "same-ID retry published finals")
        self.pass_row("same-chunk-retry-rejected", quarantined_partials_unchanged=True, output_created=False)

        retry_label = "fresh-chunk-retry-success"
        retry_output = self.chunks_dir / "fresh-chunk-retry-success.chp1"
        retry_argv, retry_challenge, retry_chunk_id = self.generation_argv(
            self.producer,
            label=retry_label,
            output=retry_output,
        )
        retry = self.capture(retry_label, retry_argv)
        self.parse_generation_result(retry, retry_output, retry_challenge, retry_chunk_id)
        retry_summary = self.validate_chunk(retry_output, retry_challenge)
        require(all(path.is_file() for path in partials), "fresh retry removed quarantined partials")
        self.pass_row(
            "fresh-chunk-retry-success",
            **retry_summary,
            quarantined_partials_preserved=True,
            old_chunk_id=chunk_id,
            new_chunk_id=retry_chunk_id,
            old_challenge=challenge,
            new_challenge=retry_challenge,
        )

    def artifact_inventory(self) -> list[dict[str, Any]]:
        inventory: list[dict[str, Any]] = []
        for path in sorted(item for item in self.artifact_dir.rglob("*") if item.is_file()):
            inventory.append(
                {
                    "path": path.relative_to(self.artifact_dir).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        return inventory

    def run(self) -> dict[str, Any]:
        self.check_static_inputs()
        self.capability_matrix()
        primary_output, primary_challenge, primary_chunk_id, primary_result = self.generate_primary()
        self.generation_negative_matrix()
        self.rerun_primary(primary_output, primary_challenge, primary_chunk_id)
        self.artifact_negative_matrix(primary_output, primary_challenge)
        self.kill_retry_matrix()
        require(all(row["status"] == "PASS" for row in self.matrix), "matrix contains a non-PASS row")
        return {
            "schema": "crazyhouse-datagen-local-g0-result/v1",
            "created_utc": utc_now(),
            "started_utc": self.started_utc,
            "status": "PASS",
            "project": "Crazyhouse-Stockfish",
            "phase": "P9",
            "gate": "G9",
            "evidence_class": "E1_ENGINEERING",
            "owner_task": "019ff608-f6fe-7792-b0c9-fa6d8be8e6d8",
            "producer": {
                "path": str(self.producer),
                "bytes": self.producer.stat().st_size,
                "sha256": sha256_file(self.producer),
                "source_commit": self.pins.source_commit,
                "source_tree": self.pins.source_tree,
                "src_tree": self.pins.src_tree,
                "build_recipe_sha256": self.pins.build_recipe_sha256,
                "toolchain_sha256": self.pins.toolchain_sha256,
            },
            "dirty_control": {
                "path": str(self.dirty_producer),
                "bytes": self.dirty_producer.stat().st_size,
                "sha256": sha256_file(self.dirty_producer),
            },
            "normal_engine_negative_control": {
                "path": str(self.normal_engine),
                "bytes": self.normal_engine.stat().st_size,
                "sha256": sha256_file(self.normal_engine),
            },
            "primary_generation": primary_result,
            "matrix": self.matrix,
            "matrix_passed": len(self.matrix),
            "matrix_failed": 0,
            "artifact_inventory_before_result": self.artifact_inventory(),
            "resource_envelope": {
                "threads": 1,
                "network_connections": 0,
                "gpu": False,
                "foreign_process_mutation": False,
                "owned_child_kill_only": True,
                "timing_sensitive": False,
            },
            "claim_boundary": {
                "fixture_only": True,
                "training_admissible": False,
                "strength_claim": False,
                "openbench_evidence": False,
                "release_evidence": False,
            },
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--dirty-producer", type=Path, required=True)
    parser.add_argument("--normal-engine", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--independent-verifier", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--src-tree", required=True)
    parser.add_argument("--build-recipe-sha256", required=True)
    parser.add_argument("--toolchain-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    harness: Harness | None = None
    try:
        harness = Harness(args)
        result = harness.run()
        write_new(harness.artifact_dir / "g0-result.canonical.json", canonical_json(result))
        write_new(harness.artifact_dir / "g0-result.pretty.json", pretty_json(result))
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError, codec.FormatError, G0Error) as exc:
        if harness is not None and harness.artifact_dir.is_dir():
            failure = {
                "schema": "crazyhouse-datagen-local-g0-failure/v1",
                "created_utc": utc_now(),
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "completed_rows": harness.matrix,
                "claim_boundary": {
                    "gate_closed": False,
                    "training_admissible": False,
                    "strength_claim": False,
                    "openbench_evidence": False,
                },
            }
            failure_path = harness.artifact_dir / "g0-failure.json"
            if not failure_path.exists():
                write_new(failure_path, pretty_json(failure))
        print(f"FAIL_CRAZYHOUSE_DATAGEN_G0 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS_CRAZYHOUSE_DATAGEN_G0 "
        f"rows={result['matrix_passed']} records={result['primary_generation']['records']} "
        f"chunk_sha256={result['primary_generation']['chunk_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
