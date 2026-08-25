#!/usr/bin/env python3
"""Formal local G0 harness for Crazyhouse live-search physical DATAGEN.

The harness uses no network connection and performs no OpenBench action.  It
owns every child and output namespace it creates; the kill control terminates
only the exact producer PID returned by its own ``Popen`` call.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import uuid


PHYSICAL_SCHEMA_SHA256 = "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
BUNDLE_SCHEMA_SHA256 = "27138d4049e2c6b2ad75f85d05fc799442cbf9f91a6e4a1c27c546c2eb9ecf5b"
SELFPLAY_CONTRACT_SHA256 = "482fd210ed4009aaf145c34d44b18fc05f99b11969e69dd9f69d9907204c87dd"
LEGACY_CONTRACT_SHA256 = "dc6af06c3d18fb2ff06e27e35ab691e35555ef03a5948b23cb2a198e6b89eb96"
BOOK_SHA256 = "f99f8211316813924e52fb13fbb65a5bc27dcd585e2e32a86d90db0d113fd2f6"
BOOK_BYTES = 158
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
NETWORK_BYTES = 58_534_811
SELECTION_POLICY_SHA256 = "fc67430cb09eb28531889a6b8f99a02f4b033c5bd71cbef7d2e9add8a7d573c6"
CAMPAIGN_ID = "42e04e75-21bb-5e7f-8617-54e5bc72b5a3"
BASE_SEED = 8_964_207_305_086_120_581
EXPECTED_RECORDS = 4
EXPECTED_TRAJECTORIES = 2
EXPECTED_BUNDLE_HEADER_BYTES = 256
EXPECTED_CAPABILITY_BYTES = 1683
EXPECTED_PROVENANCE_BYTES = 3370
EXPECTED_PHYSICAL_CHUNK_BYTES = 1408
EXPECTED_BUNDLE_FOOTER_BYTES = 128
EXPECTED_BUNDLE_BYTES = (
    EXPECTED_BUNDLE_HEADER_BYTES
    + EXPECTED_CAPABILITY_BYTES
    + EXPECTED_PROVENANCE_BYTES
    + EXPECTED_PHYSICAL_CHUNK_BYTES
    + EXPECTED_BUNDLE_FOOTER_BYTES
)
EXPECTED_BENCH_NODES = 113485

ARTIFACT_REPO_PATH = "artifacts/crazyhouse-stockfish-datagen.exe"
BOOK_REPO_PATH = "tests/crazyhouse/data/crazyhouse-selfplay-g0-openings-v1.epd"
NETWORK_REPO_PATH = "artifacts/networks/crazyhouse_run15rl_e190_l03.nnue"
IDENTITY_DOMAIN = b"Crazyhouse-Stockfish selfplay deterministic identity v1\0"


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


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def pretty_json(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def canonical_slug(value: str) -> str:
    output = "".join(character if character.isalnum() else "-" for character in value.lower())
    while "--" in output:
        output = output.replace("--", "-")
    return output.strip("-")


def parse_canonical_json(payload: bytes, label: str) -> Mapping[str, Any]:
    require(not payload.startswith(b"\xef\xbb\xbf"), f"{label}: BOM")
    require(b"\r" not in payload and b"\0" not in payload, f"{label}: CR or NUL")
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"), f"{label}: LF framing")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise G0Error(f"{label}: malformed JSON: {exc}") from exc
    require(isinstance(document, dict) and payload == canonical_json(document), f"{label}: noncanonical JSON")
    return document


def derive_id(kind: str, seed: int, candidate_index: int = 0) -> bytes:
    chunk_index = seed - BASE_SEED
    require(chunk_index >= 0, "assigned seed precedes base seed")
    payload = (
        IDENTITY_DOMAIN
        + kind.encode("ascii")
        + b"\0"
        + uuid.UUID(CAMPAIGN_ID).bytes
        + struct.pack("<QQ", chunk_index, candidate_index)
    )
    output = bytearray(hashlib.sha256(payload).digest()[:16])
    output[6] = (output[6] & 0x0F) | 0x50
    output[8] = (output[8] & 0x3F) | 0x80
    return bytes(output)


def chunk_text(seed: int) -> str:
    return str(uuid.UUID(bytes=derive_id("chunk", seed)))


def quote_token(token: str) -> str:
    require("\n" not in token and "\r" not in token and "\0" not in token and '"' not in token, "unsafe rendered token")
    return f'"{token}"' if any(character.isspace() for character in token) else token


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
        self.outputs_dir = self.artifact_dir / "outputs"
        self.mutated_dir = self.artifact_dir / "mutated-inputs"
        self.verifier_dir = self.artifact_dir / "independent"
        self.matrix: list[dict[str, Any]] = []
        self.started_utc = utc_now()
        self.pins = Pins(
            args.source_commit,
            args.source_tree,
            args.src_tree,
            args.build_recipe_sha256,
            args.toolchain_sha256,
        )
        require(not self.artifact_dir.exists(), "formal artifact directory already exists")
        self.logs_dir.mkdir(parents=True)
        self.outputs_dir.mkdir()
        self.mutated_dir.mkdir()
        self.verifier_dir.mkdir()

        self.producer = args.producer.resolve(strict=True)
        self.dirty_producer = args.dirty_producer.resolve(strict=True)
        self.normal_engine = args.normal_engine.resolve(strict=True)
        self.g9_normal_engine = args.g9_normal_engine.resolve(strict=True)
        self.physical_schema = args.physical_schema.resolve(strict=True)
        self.bundle_schema = args.bundle_schema.resolve(strict=True)
        self.selfplay_contract = args.selfplay_contract.resolve(strict=True)
        self.legacy_contract = args.legacy_contract.resolve(strict=True)
        self.book = args.book.resolve(strict=True)
        self.network = args.network.resolve(strict=True)
        self.legacy_corpus = args.legacy_corpus.resolve(strict=True)
        self.independent_verifier = args.independent_verifier.resolve(strict=True)
        self.legacy_independent_verifier = args.legacy_independent_verifier.resolve(strict=True)
        self.legacy_harness = args.legacy_harness.resolve(strict=True)
        self.normal_engine_verifier = args.normal_engine_verifier.resolve(strict=True)

    def pass_row(self, name: str, **evidence: Any) -> None:
        self.matrix.append({"name": name, "status": "PASS", "evidence": evidence})

    def write_log(self, label: str, stdout: bytes, stderr: bytes) -> None:
        slug = canonical_slug(label)
        write_new(self.logs_dir / f"{slug}.stdout.bin", stdout)
        write_new(self.logs_dir / f"{slug}.stderr.bin", stderr)

    def capture(
        self,
        label: str,
        argv: Sequence[str | Path],
        *,
        stdin: bytes | None = None,
        env_delta: Mapping[str, str] | None = None,
        timeout: float = 180.0,
        cwd: Path | None = None,
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
                input=stdin,
                stdin=subprocess.DEVNULL if stdin is None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                cwd=cwd,
                env=environment,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            self.write_log(label, exc.stdout or b"", exc.stderr or b"")
            raise G0Error(f"{label}: timeout after {timeout} seconds") from exc
        captured = Captured(
            label,
            command,
            process.returncode,
            process.stdout,
            process.stderr,
            started,
            utc_now(),
            time.monotonic() - before,
        )
        self.write_log(label, captured.stdout, captured.stderr)
        return captured

    def command_tokens(
        self,
        producer: Path,
        output: Path,
        *,
        seed: int = BASE_SEED,
        overrides: Mapping[str, str] | None = None,
        extras: Sequence[str] = (),
    ) -> list[str]:
        values = {
            "--artifact-repo-path": ARTIFACT_REPO_PATH,
            "--base-seed": str(BASE_SEED),
            "--book": str(self.book),
            "--book-repo-path": BOOK_REPO_PATH,
            "--book-sha256": BOOK_SHA256,
            "--campaign-id": CAMPAIGN_ID,
            "--count": str(EXPECTED_RECORDS),
            "--depth": "1",
            "--hash-mb": "16",
            "--max-candidate-games": "2",
            "--max-game-ply": "4",
            "--network": str(self.network),
            "--network-repo-path": NETWORK_REPO_PATH,
            "--network-sha256": NETWORK_SHA256,
            "--nodes": "0",
            "--output": str(output),
            "--producer-sha256": sha256_file(producer),
            "--seed": str(seed),
            "--selection-policy-sha256": SELECTION_POLICY_SHA256,
            "--threads": "1",
        }
        if overrides:
            values.update(overrides)
        tokens = ["crazyhouse_generate_physical_v1"]
        for key, value in values.items():
            tokens.extend((key, value))
        tokens.extend(extras)
        return tokens

    def render(self, tokens: Sequence[str], newline: str = "\n") -> bytes:
        require(newline in {"\n", "\r\n"}, "unsupported rendered newline")
        line = " ".join(quote_token(token) for token in tokens)
        return (line + newline + "quit" + newline).encode("utf-8")

    def partial_path(self, output: Path, seed: int = BASE_SEED) -> Path:
        return Path(str(output) + ".partial." + chunk_text(seed))

    def require_namespace_absent(self, output: Path, label: str, seed: int = BASE_SEED) -> None:
        observed = [str(path) for path in (output, self.partial_path(output, seed)) if path.exists()]
        require(not observed, f"{label}: rejected generation created output {observed}")

    def require_producer_failure(self, captured: Captured, label: str) -> None:
        require(captured.returncode != 0, f"{label}: producer unexpectedly succeeded")
        require(captured.stdout == b"", f"{label}: producer emitted stdout")
        require(
            captured.stderr.startswith(b"ERROR crazyhouse-datagen-v1: ")
            and captured.stderr.endswith(b"\n")
            and b"\r" not in captured.stderr,
            f"{label}: noncanonical producer diagnostic",
        )

    def split_bundle(self, payload: bytes) -> tuple[bytes, bytes, bytes]:
        require(
            len(payload) >= EXPECTED_BUNDLE_HEADER_BYTES + EXPECTED_BUNDLE_FOOTER_BYTES,
            "bundle too short",
        )
        capability_bytes, provenance_bytes, chunk_bytes = struct.unpack_from("<QQQ", payload, 40)
        start = EXPECTED_BUNDLE_HEADER_BYTES
        capability = payload[start:start + capability_bytes]
        start += capability_bytes
        provenance = payload[start:start + provenance_bytes]
        start += provenance_bytes
        chunk = payload[start:start + chunk_bytes]
        require(
            start + chunk_bytes + EXPECTED_BUNDLE_FOOTER_BYTES == len(payload),
            "bundle split length",
        )
        return capability, provenance, chunk

    def authenticate_capability(
        self,
        payload: bytes,
        producer: Path,
        challenge: str,
        *,
        source_dirty: bool,
    ) -> Mapping[str, Any]:
        response = parse_canonical_json(payload, "self-play capability")
        require(response["schema"] == "crazyhouse-datagen-selfplay-capability-response/v1", "self-play capability schema")
        require(response["artifact_role"] == "crazyhouse-physical-datagen-selfplay-v1", "self-play capability role")
        require(response["artifact_bytes"] == producer.stat().st_size and response["artifact_sha256"] == sha256_file(producer), "self-play capability artifact")
        require(response["challenge"] == challenge, "self-play capability challenge")
        require(response["source_commit"] == self.pins.source_commit and response["source_tree"] == self.pins.source_tree and response["src_tree"] == self.pins.src_tree, "self-play capability source")
        require(response["source_dirty"] is source_dirty, "self-play capability dirty flag")
        require(response["build_recipe_sha256"] == self.pins.build_recipe_sha256 and response["toolchain_sha256"] == self.pins.toolchain_sha256, "self-play capability build/toolchain")
        contract = json.loads(self.selfplay_contract.read_text(encoding="utf-8"))
        for key, value in contract["response"]["required_exact"].items():
            require(response.get(key) == value, f"self-play capability exact field {key}")
        require(response["bundle_schema_sha256"] == BUNDLE_SCHEMA_SHA256, "self-play capability bundle schema")
        return response

    def static_admission(self) -> None:
        for label, path, expected in (
            ("physical schema", self.physical_schema, PHYSICAL_SCHEMA_SHA256),
            ("bundle schema", self.bundle_schema, BUNDLE_SCHEMA_SHA256),
            ("self-play contract", self.selfplay_contract, SELFPLAY_CONTRACT_SHA256),
            ("legacy contract", self.legacy_contract, LEGACY_CONTRACT_SHA256),
            ("book", self.book, BOOK_SHA256),
        ):
            require(sha256_file(path) == expected, f"{label} identity drifted")
        require(self.book.stat().st_size == BOOK_BYTES, "book byte count")
        require(self.network.stat().st_size == NETWORK_BYTES and sha256_file(self.network) == NETWORK_SHA256, "legacy network identity")
        for label, value, width in (
            ("source commit", self.pins.source_commit, 40),
            ("source tree", self.pins.source_tree, 40),
            ("src tree", self.pins.src_tree, 40),
            ("build recipe", self.pins.build_recipe_sha256, 64),
            ("toolchain", self.pins.toolchain_sha256, 64),
        ):
            require(len(value) == width and value == value.lower(), f"{label} width/case")
            bytes.fromhex(value)
        require(len({self.producer, self.dirty_producer, self.normal_engine}) == 3, "artifact boundary collapsed")
        require(self.producer.stat().st_size > 0 and self.dirty_producer.stat().st_size > 0 and self.normal_engine.stat().st_size > 0, "empty executable")
        self.pass_row(
            "frozen-input-identities",
            physical_schema_sha256=PHYSICAL_SCHEMA_SHA256,
            bundle_schema_sha256=BUNDLE_SCHEMA_SHA256,
            selfplay_contract_sha256=SELFPLAY_CONTRACT_SHA256,
            book_sha256=BOOK_SHA256,
            network_sha256=NETWORK_SHA256,
        )
        self.pass_row(
            "separate-artifact-boundary",
            producer_sha256=sha256_file(self.producer),
            dirty_producer_sha256=sha256_file(self.dirty_producer),
            normal_engine_sha256=sha256_file(self.normal_engine),
        )

    def capability_matrix(self) -> None:
        challenge = hashlib.sha256(b"Crazyhouse P11 formal standalone capability\0").hexdigest()[:32]
        positive = self.capture(
            "selfplay-capability-positive",
            (self.producer, "--datagen-selfplay-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(positive.returncode == 0 and positive.stderr == b"", "positive self-play capability process")
        response = self.authenticate_capability(positive.stdout, self.producer, challenge, source_dirty=False)
        self.pass_row("selfplay-capability-positive", challenge=challenge, response_sha256=sha256_bytes(positive.stdout))

        legacy = self.capture(
            "legacy-capability-substitution-negative",
            (self.producer, "--datagen-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(legacy.returncode == 0 and legacy.stderr == b"", "legacy capability invocation")
        legacy_document = parse_canonical_json(legacy.stdout, "legacy capability")
        require(legacy_document["schema"] == "crazyhouse-datagen-capability-response/v1", "legacy capability schema")
        require(legacy_document["schema"] != response["schema"] and "search_backend" not in legacy_document, "legacy capability admitted as self-play")
        self.pass_row("legacy-capability-substitution-negative", legacy_schema=legacy_document["schema"], admitted=False)

        for label, argv in (
            ("capability-missing-challenge", (self.producer, "--datagen-selfplay-capabilities-v1")),
            ("capability-uppercase-challenge", (self.producer, "--datagen-selfplay-capabilities-v1", "--challenge", challenge.upper())),
            ("capability-short-challenge", (self.producer, "--datagen-selfplay-capabilities-v1", "--challenge", challenge[:-1])),
            ("capability-extra-argument", (self.producer, "--datagen-selfplay-capabilities-v1", "--challenge", challenge, "extra")),
        ):
            captured = self.capture(label, argv, timeout=10)
            self.require_producer_failure(captured, label)
            self.pass_row(label, returncode=captured.returncode)

        dirty = self.capture(
            "dirty-selfplay-capability",
            (self.dirty_producer, "--datagen-selfplay-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(dirty.returncode == 0 and dirty.stderr == b"", "dirty self-play capability process")
        dirty_response = self.authenticate_capability(dirty.stdout, self.dirty_producer, challenge, source_dirty=True)
        allowed = {"artifact_bytes", "artifact_sha256", "source_dirty"}
        require(all(dirty_response[key] == response[key] for key in response if key not in allowed), "dirty capability differs beyond identity/dirty flag")
        self.pass_row("dirty-selfplay-capability", source_dirty=True, sha256=dirty_response["artifact_sha256"])

    def parse_generation_result(self, captured: Captured, output: Path, seed: int) -> Mapping[str, Any]:
        require(captured.returncode == 0 and captured.stderr == b"", f"{captured.label}: generation process")
        result = parse_canonical_json(captured.stdout, f"{captured.label} result")
        require(set(result) == {
            "artifact_sha256", "bundle_bytes", "bundle_sha256", "capability_sha256",
            "chunk_id", "chunk_sha256", "output", "provenance_sha256", "records",
            "schema", "status", "trajectories",
        }, f"{captured.label}: result key set")
        require(result["schema"] == "crazyhouse-datagen-selfplay-result/v1" and result["status"] == "committed", f"{captured.label}: result status")
        require(result["artifact_sha256"] == sha256_file(self.producer), f"{captured.label}: producer binding")
        require(result["output"] == output.name and result["chunk_id"] == chunk_text(seed), f"{captured.label}: output/chunk identity")
        require(result["records"] == EXPECTED_RECORDS and result["trajectories"] == EXPECTED_TRAJECTORIES, f"{captured.label}: counts")
        require(output.is_file() and not self.partial_path(output, seed).exists(), f"{captured.label}: transaction final/partial")
        payload = output.read_bytes()
        capability, provenance, chunk = self.split_bundle(payload)
        require(result["bundle_bytes"] == len(payload) and result["bundle_sha256"] == sha256_bytes(payload), f"{captured.label}: bundle result")
        require(result["capability_sha256"] == sha256_bytes(capability), f"{captured.label}: capability result")
        require(result["provenance_sha256"] == sha256_bytes(provenance), f"{captured.label}: provenance result")
        require(result["chunk_sha256"] == sha256_bytes(chunk), f"{captured.label}: chunk result")
        return result

    def verifier_command(self, bundle: Path, output: Path | None = None) -> list[str | Path]:
        argv: list[str | Path] = [
            sys.executable,
            self.independent_verifier,
            "--producer", self.producer,
            "--bundle", bundle,
            "--bundle-schema", self.bundle_schema,
            "--physical-schema", self.physical_schema,
            "--contract", self.selfplay_contract,
            "--book", self.book,
            "--network", self.network,
            "--source-commit", self.pins.source_commit,
            "--source-tree", self.pins.source_tree,
            "--src-tree", self.pins.src_tree,
            "--build-recipe-sha256", self.pins.build_recipe_sha256,
            "--toolchain-sha256", self.pins.toolchain_sha256,
        ]
        if output is not None:
            argv.extend(("--output", output))
        return argv

    def run_independent_positive(self, label: str, bundle: Path) -> Mapping[str, Any]:
        result_path = self.verifier_dir / f"{canonical_slug(label)}.json"
        captured = self.capture(label, self.verifier_command(bundle, result_path), timeout=120)
        require(captured.returncode == 0 and captured.stderr == b"", f"{label}: independent verifier process")
        require(captured.stdout.startswith(b"PASS_CRAZYHOUSE_SELFPLAY_DATAGEN_G0_INDEPENDENT "), f"{label}: independent PASS marker")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        require(result["status"] == "PASS" and result["bundle_sha256"] == sha256_file(bundle), f"{label}: independent result")
        return result

    def positive_generation_matrix(self) -> tuple[Path, Mapping[str, Any]]:
        outputs = (
            ("positive-lf", self.outputs_dir / "positive LF bundle.bin", "\n"),
            ("positive-crlf", self.outputs_dir / "positive CRLF bundle.bin", "\r\n"),
        )
        bundles: list[bytes] = []
        results: list[Mapping[str, Any]] = []
        independent_results: list[Mapping[str, Any]] = []
        for label, output, newline in outputs:
            tokens = self.command_tokens(self.producer, output)
            captured = self.capture(label, (self.producer,), stdin=self.render(tokens, newline), timeout=180)
            result = self.parse_generation_result(captured, output, BASE_SEED)
            independent = self.run_independent_positive(f"{label}-independent", output)
            bundles.append(output.read_bytes())
            results.append(result)
            independent_results.append(independent)
            self.pass_row(
                label,
                ingress="LF" if newline == "\n" else "CRLF",
                bundle_bytes=len(bundles[-1]),
                bundle_sha256=sha256_bytes(bundles[-1]),
                independent_terminal_replay=independent["independent_terminal_replay"],
                moves=independent["moves"],
                nodes=independent["nodes"],
            )
        require(bundles[0] == bundles[1], "LF and CRLF bundles are not byte-identical")
        expected_sections = {
            "bundle_bytes": EXPECTED_BUNDLE_BYTES,
            "capability_bytes": EXPECTED_CAPABILITY_BYTES,
            "provenance_bytes": EXPECTED_PROVENANCE_BYTES,
            "chunk_bytes": EXPECTED_PHYSICAL_CHUNK_BYTES,
        }
        for (label, _, _), independent in zip(outputs, independent_results, strict=True):
            observed_sections = {key: independent[key] for key in expected_sections}
            require(observed_sections == expected_sections, f"{label}: formal section byte counts drifted")
        require(len(bundles[0]) == EXPECTED_BUNDLE_BYTES, "positive bundle byte count drifted")
        self.pass_row(
            "lf-crlf-byte-determinism",
            bundle_sha256=sha256_bytes(bundles[0]),
            bytes=len(bundles[0]),
            sections={
                "header": EXPECTED_BUNDLE_HEADER_BYTES,
                "capability": EXPECTED_CAPABILITY_BYTES,
                "provenance": EXPECTED_PROVENANCE_BYTES,
                "physical_chunk": EXPECTED_PHYSICAL_CHUNK_BYTES,
                "footer": EXPECTED_BUNDLE_FOOTER_BYTES,
            },
        )

        capability, _, _ = self.split_bundle(bundles[0])
        capability_document = parse_canonical_json(capability, "embedded positive capability")
        challenge = capability_document["challenge"]
        standalone = self.capture(
            "embedded-capability-requery",
            (self.producer, "--datagen-selfplay-capabilities-v1", "--challenge", challenge),
            timeout=10,
        )
        require(standalone.returncode == 0 and standalone.stderr == b"" and standalone.stdout == capability, "embedded/standalone capability bytes")
        self.authenticate_capability(capability, self.producer, challenge, source_dirty=False)
        self.pass_row("embedded-capability-requery", challenge=challenge, capability_sha256=sha256_bytes(capability))
        return outputs[0][1], results[0]

    def rejected_generation(
        self,
        label: str,
        *,
        producer: Path | None = None,
        overrides: Mapping[str, str] | None = None,
        extras: Sequence[str] = (),
        stdin_builder: Callable[[bytes], bytes] | None = None,
        env_delta: Mapping[str, str] | None = None,
        expected_stderr: bytes | None = None,
    ) -> Captured:
        actual_producer = producer or self.producer
        output = self.outputs_dir / f"{canonical_slug(label)}.bin"
        tokens = self.command_tokens(actual_producer, output, overrides=overrides, extras=extras)
        rendered = self.render(tokens)
        if stdin_builder:
            rendered = stdin_builder(rendered)
        captured = self.capture(label, (actual_producer,), stdin=rendered, env_delta=env_delta, timeout=180)
        self.require_producer_failure(captured, label)
        self.require_namespace_absent(output, label)
        if expected_stderr is not None:
            require(expected_stderr in captured.stderr, f"{label}: expected rejection reason absent")
        self.pass_row(label, returncode=captured.returncode, output_created=False, diagnostic_sha256=sha256_bytes(captured.stderr))
        return captured

    def write_corrupt_network(self) -> Path:
        destination = self.mutated_dir / "same-size-incompatible-network.nnue"
        with self.network.open("rb") as source, destination.open("xb") as target:
            first = True
            for block in iter(lambda: source.read(1024 * 1024), b""):
                if first:
                    mutated = bytearray(block)
                    mutated[0] ^= 0xFF
                    block = bytes(mutated)
                    first = False
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        require(destination.stat().st_size == NETWORK_BYTES and sha256_file(destination) != NETWORK_SHA256, "corrupt network fixture")
        return destination

    def identity_and_search_negative_matrix(self) -> None:
        missing_network = self.mutated_dir / "missing-network.nnue"
        short_network = self.mutated_dir / "wrong-size-network.nnue"
        write_new(short_network, b"not-a-network\n")
        corrupt_network = self.write_corrupt_network()
        missing_book = self.mutated_dir / "missing-book.epd"
        corrupt_book_bytes = bytearray(self.book.read_bytes())
        corrupt_book_bytes[0] ^= 1
        corrupt_book = self.mutated_dir / "corrupt-book.epd"
        write_new(corrupt_book, bytes(corrupt_book_bytes))

        self.rejected_generation("missing-network", overrides={"--network": str(missing_network)})
        self.rejected_generation("wrong-size-network", overrides={"--network": str(short_network)})
        self.rejected_generation("same-size-corrupt-incompatible-network", overrides={"--network": str(corrupt_network)})
        self.rejected_generation("wrong-network-hash-argument", overrides={"--network-sha256": "00" * 32})
        self.rejected_generation("missing-book", overrides={"--book": str(missing_book)})
        self.rejected_generation("corrupt-book", overrides={"--book": str(corrupt_book)})
        self.rejected_generation("wrong-book-hash-argument", overrides={"--book-sha256": "00" * 32})
        self.rejected_generation("producer-self-hash-mismatch", overrides={"--producer-sha256": "00" * 32})
        self.rejected_generation("threads-two", overrides={"--threads": "2"})
        self.rejected_generation(
            "dirty-source-generation",
            producer=self.dirty_producer,
            expected_stderr=b"dirty source build is not admitted",
        )
        self.rejected_generation(
            "unreachable-exact-quota-three",
            overrides={"--count": "3"},
            expected_stderr=b"complete trajectory does not fit the remaining record quota",
        )
        self.rejected_generation(
            "unreachable-exact-quota-five",
            overrides={"--count": "5"},
            expected_stderr=b"candidate budget could not produce the exact complete-trajectory record quota",
        )
        for fault, reason in (
            ("missing-pv", b"teacher search did not return one exact principal variation"),
            ("illegal-pv", b"teacher principal move is absent or illegal"),
            ("safety-limit", b"nonterminal safety limit reached"),
        ):
            self.rejected_generation(
                f"injected-{fault}",
                extras=("--test-candidate-fault", fault),
                env_delta={"CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION": "1"},
                expected_stderr=reason,
            )
        self.rejected_generation(
            "fault-control-without-environment",
            extras=("--test-candidate-fault", "missing-pv"),
            expected_stderr=b"fault injection was not explicitly enabled",
        )
        self.rejected_generation(
            "fault-control-unknown-value",
            extras=("--test-candidate-fault", "unknown"),
            env_delta={"CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION": "1"},
            expected_stderr=b"unknown self-play candidate fault injection",
        )

    def stdin_negative_matrix(self) -> None:
        output = self.outputs_dir / "stdin-negative-template.bin"
        rendered = self.render(self.command_tokens(self.producer, output))
        command_line = rendered.split(b"\n", 1)[0]
        cases: tuple[tuple[str, bytes], ...] = (
            ("stdin-empty", b""),
            ("stdin-missing-command", b"\nquit\n"),
            ("stdin-missing-quit", command_line + b"\n"),
            ("stdin-extra-command", command_line + b"\nuci\nquit\n"),
            ("stdin-bom", b"\xef\xbb\xbf" + rendered),
            ("stdin-nul", command_line + b"\0\nquit\n"),
            ("stdin-mixed-newlines", command_line + b"\r\nquit\n"),
            ("stdin-bare-cr", command_line + b"\rquit\r"),
            ("stdin-malformed-quote", b'crazyhouse_generate_physical_v1 "unterminated\nquit\n'),
        )
        for label, payload in cases:
            case_output = self.outputs_dir / f"{label}.bin"
            payload = payload.replace(str(output).encode("utf-8"), str(case_output).encode("utf-8"))
            captured = self.capture(label, (self.producer,), stdin=payload, timeout=30)
            self.require_producer_failure(captured, label)
            self.require_namespace_absent(case_output, label)
            self.pass_row(label, returncode=captured.returncode, output_created=False)

        self.rejected_generation("stdin-unknown-option", extras=("--unknown-option", "1"))
        self.rejected_generation("stdin-duplicate-option", extras=("--count", "4"))
        self.rejected_generation(
            "stdin-missing-required-option",
            stdin_builder=lambda payload: payload.replace(b" --threads 1", b"", 1),
        )

    def preexisting_output_matrix(self) -> None:
        final = self.outputs_dir / "preexisting-final.bin"
        marker = b"P11_PREEXISTING_FINAL_MUST_SURVIVE\n"
        write_new(final, marker)
        captured = self.capture(
            "preexisting-final",
            (self.producer,),
            stdin=self.render(self.command_tokens(self.producer, final)),
            timeout=180,
        )
        self.require_producer_failure(captured, "preexisting-final")
        require(final.read_bytes() == marker and not self.partial_path(final).exists(), "preexisting final preservation")
        self.pass_row("preexisting-final", preserved=True, marker_sha256=sha256_bytes(marker))

        output = self.outputs_dir / "preexisting-partial.bin"
        partial = self.partial_path(output)
        partial_marker = b"P11_PREEXISTING_PARTIAL_MUST_SURVIVE\n"
        write_new(partial, partial_marker)
        captured = self.capture(
            "preexisting-partial",
            (self.producer,),
            stdin=self.render(self.command_tokens(self.producer, output)),
            timeout=180,
        )
        self.require_producer_failure(captured, "preexisting-partial")
        require(partial.read_bytes() == partial_marker and not output.exists(), "preexisting partial preservation")
        self.pass_row("preexisting-partial", preserved=True, marker_sha256=sha256_bytes(partial_marker))

    def mutation_file(self, label: str, payload: bytes) -> Path:
        path = self.mutated_dir / f"{canonical_slug(label)}.bin"
        write_new(path, payload)
        return path

    def rebuild_bundle(self, capability: bytes, provenance: bytes, chunk: bytes) -> bytes:
        payload = capability + provenance + chunk
        total = 256 + len(payload) + 128
        header = bytearray(256)
        header[:8] = b"CHBNDLV1"
        struct.pack_into("<IHHHHIQQQQ", header, 16, 0x01020304, 256, 128, 1, 0, 3, total, len(capability), len(provenance), len(chunk))
        header[64:96] = hashlib.sha256(capability).digest()
        header[96:128] = hashlib.sha256(provenance).digest()
        header[128:160] = hashlib.sha256(chunk).digest()
        payload_digest = hashlib.sha256(payload).digest()
        header[160:192] = payload_digest
        header[192:224] = bytes.fromhex(BUNDLE_SCHEMA_SHA256)
        struct.pack_into("<I", header, 252, crc32c(header[:252]))
        footer = bytearray(128)
        footer[:10] = b"CHBNDENDV1"
        struct.pack_into("<HHIQQ", footer, 16, 128, 1, 3, total, len(payload))
        footer[40:72] = payload_digest
        footer[72:104] = hashlib.sha256(header).digest()
        struct.pack_into("<I", footer, 124, crc32c(footer[:124]))
        return bytes(header) + payload + bytes(footer)

    def refresh_physical(self, payload: bytes) -> bytes:
        output = bytearray(payload)
        header = output[:256]
        records = output[256:-128]
        footer = output[-128:]
        records_digest = hashlib.sha256(records).digest()
        header[176:208] = records_digest
        struct.pack_into("<I", header, 252, crc32c(header[:252]))
        footer[40:72] = records_digest
        footer[72:104] = hashlib.sha256(header).digest()
        struct.pack_into("<I", footer, 124, crc32c(footer[:124]))
        return bytes(header) + bytes(records) + bytes(footer)

    def expect_independent_rejection(self, label: str, payload: bytes) -> None:
        bundle = self.mutation_file(label, payload)
        captured = self.capture(label, self.verifier_command(bundle), timeout=120)
        require(captured.returncode != 0 and captured.stderr == b"", f"{label}: independent verifier did not reject cleanly")
        require(captured.stdout.startswith(b"FAIL_CRAZYHOUSE_SELFPLAY_DATAGEN_G0_INDEPENDENT "), f"{label}: independent FAIL marker")
        self.pass_row(label, verifier="independent-fail-closed", mutated_sha256=sha256_bytes(payload))

    def artifact_mutation_matrix(self, primary: Path) -> None:
        bundle = primary.read_bytes()
        capability, provenance, chunk = self.split_bundle(bundle)
        simple: list[tuple[str, bytes]] = []
        for label, index in (
            ("bundle-header-corrupt", 20),
            ("bundle-capability-corrupt", 256),
            ("bundle-provenance-corrupt", 256 + len(capability)),
            ("physical-header-corrupt", 256 + len(capability) + len(provenance) + 20),
            ("physical-record-corrupt", 256 + len(capability) + len(provenance) + 256 + 60),
            ("physical-footer-corrupt", len(bundle) - 128 - 20),
            ("bundle-footer-corrupt", len(bundle) - 20),
        ):
            mutated = bytearray(bundle)
            mutated[index] ^= 1
            simple.append((label, bytes(mutated)))
        simple.extend((
            ("bundle-truncated", bundle[:-1]),
            ("bundle-appended", bundle + b"x"),
        ))
        for label, payload in simple:
            self.expect_independent_rejection(label, payload)

        capability_document = dict(parse_canonical_json(capability, "mutation capability"))
        capability_document["challenge"] = "00" * 16
        self.expect_independent_rejection(
            "semantic-capability-challenge-mismatch",
            self.rebuild_bundle(canonical_json(capability_document), provenance, chunk),
        )

        provenance_document = dict(parse_canonical_json(provenance, "mutation provenance"))
        provenance_document["network"] = dict(provenance_document["network"])
        provenance_document["network"]["used"] = False
        self.expect_independent_rejection(
            "semantic-provenance-network-mismatch",
            self.rebuild_bundle(capability, canonical_json(provenance_document), chunk),
        )

        teacher_chunk = bytearray(chunk)
        record_offset = 256
        teacher_chunk[record_offset + 127] = 2
        record = teacher_chunk[record_offset:record_offset + 256]
        struct.pack_into("<I", record, 252, crc32c(record[:252]))
        teacher_chunk[record_offset:record_offset + 256] = record
        teacher_chunk = bytearray(self.refresh_physical(bytes(teacher_chunk)))
        self.expect_independent_rejection(
            "semantic-teacher-bound-mismatch",
            self.rebuild_bundle(capability, provenance, bytes(teacher_chunk)),
        )

        binding_chunk = bytearray(chunk)
        binding_chunk[208] ^= 1
        binding_chunk = bytearray(self.refresh_physical(bytes(binding_chunk)))
        self.expect_independent_rejection(
            "semantic-nested-capability-binding-mismatch",
            self.rebuild_bundle(capability, provenance, bytes(binding_chunk)),
        )

    def kill_retry_matrix(self) -> None:
        label = "kill-after-bundle-partial"
        output = self.outputs_dir / "kill-after-bundle-partial.bin"
        rendered = self.render(
            self.command_tokens(
                self.producer,
                output,
                extras=("--test-pause-after-partial-ms", "30000"),
            )
        )
        environment = os.environ.copy()
        environment["CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            (str(self.producer),),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            creationflags=creationflags,
        )
        owned_pid = process.pid
        require(process.stdin is not None and process.stdout is not None and process.stderr is not None, "kill child pipes")
        process.stdin.write(rendered)
        process.stdin.flush()
        process.stdin.close()
        partial = self.partial_path(output)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if partial.is_file():
                break
            if process.poll() is not None:
                stdout, stderr = process.stdout.read(), process.stderr.read()
                self.write_log(label, stdout, stderr)
                raise G0Error(f"kill child {owned_pid} exited before partial creation")
            time.sleep(0.05)
        else:
            process.kill()
            process.wait(timeout=10)
            stdout, stderr = process.stdout.read(), process.stderr.read()
            self.write_log(label, stdout, stderr)
            raise G0Error(f"kill child {owned_pid} did not create partial")
        partial_before = {"bytes": partial.stat().st_size, "sha256": sha256_file(partial)}
        process.kill()
        returncode = process.wait(timeout=10)
        stdout, stderr = process.stdout.read(), process.stderr.read()
        self.write_log(label, stdout, stderr)
        require(returncode != 0 and stdout == b"" and stderr == b"", "killed child process result")
        require(not output.exists() and partial.is_file(), "killed child transaction state")
        require({"bytes": partial.stat().st_size, "sha256": sha256_file(partial)} == partial_before, "killed partial drift")
        self.pass_row(
            "killed-bundle-partial-retained",
            owned_pid=owned_pid,
            returncode=returncode,
            partial=partial_before,
            final_published=False,
            foreign_process_mutation=False,
        )

        same = self.capture(
            "same-chunk-retry-rejected",
            (self.producer,),
            stdin=rendered,
            env_delta={"CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION": "1"},
            timeout=180,
        )
        self.require_producer_failure(same, "same-chunk-retry-rejected")
        require(not output.exists() and partial.is_file() and sha256_file(partial) == partial_before["sha256"], "same chunk retry preservation")
        self.pass_row("same-chunk-retry-rejected", chunk_id=chunk_text(BASE_SEED), quarantined_partial_preserved=True)

        fresh_seed = BASE_SEED + 1
        fresh_output = self.outputs_dir / "fresh-chunk-retry.bin"
        fresh = self.capture(
            "fresh-chunk-retry",
            (self.producer,),
            stdin=self.render(self.command_tokens(self.producer, fresh_output, seed=fresh_seed)),
            timeout=180,
        )
        fresh_result = self.parse_generation_result(fresh, fresh_output, fresh_seed)
        require(partial.is_file() and sha256_file(partial) == partial_before["sha256"], "fresh retry removed quarantine")
        require(fresh_result["chunk_id"] != chunk_text(BASE_SEED), "fresh retry reused chunk identity")
        self.pass_row(
            "fresh-chunk-retry",
            old_chunk_id=chunk_text(BASE_SEED),
            new_chunk_id=fresh_result["chunk_id"],
            quarantined_partial_preserved=True,
            bundle_sha256=fresh_result["bundle_sha256"],
        )

    def normal_engine_matrix(self) -> Mapping[str, Any]:
        output = self.outputs_dir / "normal-engine-generator-negative.bin"
        normal_generation = self.capture(
            "normal-engine-generator-negative",
            (self.normal_engine,),
            stdin=self.render(self.command_tokens(self.normal_engine, output)),
            timeout=30,
        )
        self.require_namespace_absent(output, "normal-engine-generator-negative")
        require(b"crazyhouse-datagen-selfplay-result/v1" not in normal_generation.stdout and b"crazyhouse-datagen-selfplay-capability-response/v1" not in normal_generation.stdout, "normal engine emitted DATAGEN protocol")
        self.pass_row(
            "normal-engine-generator-negative",
            returncode=normal_generation.returncode,
            datagen_result=False,
            output_created=False,
        )

        challenge = "0123456789abcdef0123456789abcdef"
        normal_capability = self.capture(
            "normal-engine-capability-negative",
            (self.normal_engine, "--datagen-selfplay-capabilities-v1", "--challenge", challenge),
            stdin=b"quit\n",
            timeout=30,
        )
        require(b"crazyhouse-datagen-selfplay-capability-response/v1" not in normal_capability.stdout, "normal engine advertised DATAGEN capability")
        self.pass_row("normal-engine-capability-negative", advertised=False, returncode=normal_capability.returncode)

        verified = self.capture(
            "normal-engine-openbench-contract",
            (
                sys.executable,
                self.normal_engine_verifier,
                "--engine", self.normal_engine,
                "--runs", "2",
                "--timeout", "180",
                "--expected-nodes", str(EXPECTED_BENCH_NODES),
            ),
            timeout=420,
            cwd=self.normal_engine.parent,
        )
        require(verified.returncode == 0 and verified.stderr == b"", "normal engine verifier process")
        document = json.loads(verified.stdout.decode("utf-8"))
        require(document["bench"]["nodes"] == [EXPECTED_BENCH_NODES, EXPECTED_BENCH_NODES], "normal bench nodes")
        require(document["uci"]["uciok"] and document["capability"]["acknowledged"], "normal UCI/capability")
        require(document["negative"]["rejected"] and not document["negative"]["fallback_observed"], "normal missing-network negative")
        self.pass_row(
            "normal-engine-inventory-capability-bench",
            verifier_sha256=sha256_file(self.normal_engine_verifier),
            bench_nodes=document["bench"]["nodes"],
            uciok=True,
            missing_network_fallback=False,
        )
        return document

    def g9_regression(self) -> Mapping[str, Any]:
        artifact_dir = self.artifact_dir / "g9-regression"
        captured = self.capture(
            "g9-full-regression",
            (
                sys.executable,
                self.legacy_harness,
                "--producer", self.producer,
                "--dirty-producer", self.dirty_producer,
                "--normal-engine", self.g9_normal_engine,
                "--schema", self.physical_schema,
                "--contract", self.legacy_contract,
                "--corpus", self.legacy_corpus,
                "--independent-verifier", self.legacy_independent_verifier,
                "--artifact-dir", artifact_dir,
                "--source-commit", self.pins.source_commit,
                "--source-tree", self.pins.source_tree,
                "--src-tree", self.pins.src_tree,
                "--build-recipe-sha256", self.pins.build_recipe_sha256,
                "--toolchain-sha256", self.pins.toolchain_sha256,
            ),
            timeout=600,
        )
        require(captured.returncode == 0 and captured.stderr == b"", "G9 regression process")
        require(captured.stdout.startswith(b"PASS_CRAZYHOUSE_DATAGEN_G0 "), "G9 regression PASS marker")
        result_path = artifact_dir / "g0-result.canonical.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        require(result["status"] == "PASS" and result["matrix_failed"] == 0, "G9 regression result")
        require(result["producer"]["sha256"] == sha256_file(self.producer), "G9 producer join")
        self.pass_row(
            "g9-full-regression",
            rows=result["matrix_passed"],
            records=result["primary_generation"]["records"],
            trajectories=result["primary_generation"]["trajectories"],
            result_sha256=sha256_file(result_path),
        )
        return result

    def artifact_inventory(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for path in sorted(item for item in self.artifact_dir.rglob("*") if item.is_file()):
            output.append({
                "path": path.relative_to(self.artifact_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        return output

    def run(self) -> Mapping[str, Any]:
        self.static_admission()
        self.capability_matrix()
        primary, primary_result = self.positive_generation_matrix()
        self.identity_and_search_negative_matrix()
        self.stdin_negative_matrix()
        self.preexisting_output_matrix()
        self.artifact_mutation_matrix(primary)
        self.kill_retry_matrix()
        normal_result = self.normal_engine_matrix()
        g9_result = self.g9_regression()
        require(all(row["status"] == "PASS" for row in self.matrix), "matrix contains a non-PASS row")
        return {
            "schema": "crazyhouse-selfplay-datagen-local-g0-result/v1",
            "created_utc": utc_now(),
            "started_utc": self.started_utc,
            "status": "PASS_LOCAL_PRECONDITION_ONLY",
            "project": "Crazyhouse-Stockfish",
            "phase": "P11",
            "gate": "G11_LOCAL_PRECONDITION_ONLY",
            "evidence_class": "E1_ENGINEERING",
            "owner_task": "019ff608-f6fe-7792-b0c9-fa6d8be8e6d8",
            "source": {
                "commit": self.pins.source_commit,
                "tree": self.pins.source_tree,
                "src_tree": self.pins.src_tree,
            },
            "producer": {
                "path": str(self.producer),
                "bytes": self.producer.stat().st_size,
                "sha256": sha256_file(self.producer),
                "build_recipe_sha256": self.pins.build_recipe_sha256,
                "toolchain_sha256": self.pins.toolchain_sha256,
            },
            "dirty_control": {
                "path": str(self.dirty_producer),
                "bytes": self.dirty_producer.stat().st_size,
                "sha256": sha256_file(self.dirty_producer),
            },
            "normal_engine": {
                "path": str(self.normal_engine),
                "bytes": self.normal_engine.stat().st_size,
                "sha256": sha256_file(self.normal_engine),
                "bench_nodes": normal_result["bench"]["nodes"],
            },
            "primary_generation": primary_result,
            "g9_regression": {
                "status": g9_result["status"],
                "rows": g9_result["matrix_passed"],
                "producer_sha256": g9_result["producer"]["sha256"],
            },
            "matrix": self.matrix,
            "matrix_passed": len(self.matrix),
            "matrix_failed": 0,
            "artifact_inventory_before_result": self.artifact_inventory(),
            "resource_envelope": {
                "threads": 1,
                "hash_mib": 16,
                "network_connections": 0,
                "gpu": False,
                "foreign_process_mutation": False,
                "owned_child_kill_only": True,
                "timing_sensitive": False,
            },
            "claim_boundary": {
                "local_precondition_only": True,
                "training_admissible": False,
                "production_campaign_authorized": False,
                "official_openbench_evidence": False,
                "strength_claim": False,
                "model_selection_claim": False,
                "release_evidence": False,
            },
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--dirty-producer", type=Path, required=True)
    parser.add_argument("--normal-engine", type=Path, required=True)
    parser.add_argument("--g9-normal-engine", type=Path, required=True)
    parser.add_argument("--physical-schema", type=Path, required=True)
    parser.add_argument("--bundle-schema", type=Path, required=True)
    parser.add_argument("--selfplay-contract", type=Path, required=True)
    parser.add_argument("--legacy-contract", type=Path, required=True)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--legacy-corpus", type=Path, required=True)
    parser.add_argument("--independent-verifier", type=Path, required=True)
    parser.add_argument("--legacy-independent-verifier", type=Path, required=True)
    parser.add_argument("--legacy-harness", type=Path, required=True)
    parser.add_argument("--normal-engine-verifier", type=Path, required=True)
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
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError, G0Error) as exc:
        if harness is not None and harness.artifact_dir.is_dir():
            failure = {
                "schema": "crazyhouse-selfplay-datagen-local-g0-failure/v1",
                "created_utc": utc_now(),
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "completed_rows": harness.matrix,
                "claim_boundary": {
                    "gate_closed": False,
                    "training_admissible": False,
                    "official_openbench_evidence": False,
                    "strength_claim": False,
                },
            }
            failure_path = harness.artifact_dir / "g0-failure.json"
            if not failure_path.exists():
                write_new(failure_path, pretty_json(failure))
        print(f"FAIL_CRAZYHOUSE_SELFPLAY_DATAGEN_G0 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS_CRAZYHOUSE_SELFPLAY_DATAGEN_G0 "
        f"rows={result['matrix_passed']} records={result['primary_generation']['records']} "
        f"bundle_sha256={result['primary_generation']['bundle_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
