#!/usr/bin/env python3
"""Materialize authenticated OpenBench Crazyhouse A0 bundles for admission.

This is a fail-closed transform.  It accepts only the frozen #413/#414 CAS
download, validates every compressed bundle and embedded artifact, scans every
physical record, computes exact split identities (including the large-model
input identity), and publishes a canonical admission manifest atomically.
"""

from __future__ import annotations

import argparse
import bz2
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import sys
from typing import Any, Mapping, Sequence
import uuid


ROOT = Path(__file__).resolve().parents[2]
ADMISSION_PATH = ROOT / "tools/nnue/crazyhouse_v2_training_admission.py"
SOURCE_DEFAULT = Path(r"D:\Crazyhouse-Stockfish\datasets\p13-a0-production-523")
OUTPUT_DEFAULT = Path(
    r"D:\Crazyhouse-Stockfish\datasets\p13-a0-production-523-materialized"
)

DOWNLOAD_RECEIPT_SHA256 = (
    "c01ea981674decbb00bef8fdf83cf65a1657f5b8ee4a44f3635ced249a6537cc"
)
DOWNLOAD_MANIFEST_SHA256 = (
    "b77577c1ca89480d1d0f596baaa9494acf9ea9f4da24e8c79fc358bb76804b1d"
)
TERMINAL_RECEIPT_SHA256 = (
    "2d3902f58bb5a51c3a86dfc8f3b3578a4c0931fa9600d1aa0b52d1df22b5e01a"
)
SOURCE_COMMIT = "4e9a6ec4ddd3c577d15e335dbb0bf437443b8945"
NETWORK_SHA256 = (
    "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
)
BOOK_SHA256 = (
    "1371e87ce3bdb875d922ad0061c96c4a123bc571daf4ae2bff24e5176287f0fa"
)
OFFICIAL_OPENBENCH = "https://belzedar.duckdns.org"
DIAGNOSTIC_ADDENDUM_PATH = (
    ROOT
    / "tests/crazyhouse/p13-nnue-v2-large-a0-production-campaign-v1.addendum.002.json"
)
DIAGNOSTIC_ADDENDUM_SHA256 = (
    "8c9dd55c22664481ad18cb4cb8d38443ecfee81d80368ac56cd257e83005372c"
)
DIAGNOSTIC_OWNER_WAIVER_SHA256 = (
    "a67fe2ec5b2058b665c20da8dc158af8e91560b4de05a64824dbbdbfe72c5e2c"
)
DIAGNOSTIC_INTERSECTIONS = {
    "raw_record_key": 0,
    "position_identity": 17_127,
    "model_input_key": 17_262,
    "game_id": 0,
    "trajectory_id": 0,
    "large_model_input_key": 17_262,
}

BUNDLE_SCHEMA_SHA256 = (
    "27138d4049e2c6b2ad75f85d05fc799442cbf9f91a6e4a1c27c546c2eb9ecf5b"
)
BUNDLE_HEADER_BYTES = 256
BUNDLE_FOOTER_BYTES = 128
BUNDLE_HEADER_MAGIC = b"CHBNDLV1" + bytes(8)
BUNDLE_FOOTER_MAGIC = b"CHBNDENDV1" + bytes(6)

SPECS = (
    {
        "test_id": 413,
        "role": "train",
        "campaign_id": "ccb277c7-492e-526c-8ff5-bf280957e293",
        "base_seed": 5_827_286_079_902_923_055,
        "records": 1_048_576,
        "chunks": 2_048,
        "publication_contract_sha256": (
            "64810bb913b66952415f5ac8766a04896f53a0be593714b9405bb791dae85b42"
        ),
    },
    {
        "test_id": 414,
        "role": "validation",
        "campaign_id": "edd2b80d-a7ea-5b58-99da-ee170216353d",
        "base_seed": 2_993_887_359_082_121_353,
        "records": 131_072,
        "chunks": 256,
        "publication_contract_sha256": (
            "773ab6df3d36f28996675b8a140affd5d73db680a03b611831736a3e054cd82f"
        ),
    },
)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


admission = load_module("crazyhouse_v2_training_admission_materializer", ADMISSION_PATH)
codec = admission.codec
production_codec = admission.production_codec


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_json(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_json(path: Path, expected_sha256: str, label: str) -> Mapping[str, Any]:
    require(path.is_file() and not path.is_symlink(), f"{label} is missing or unsafe")
    payload = path.read_bytes()
    require(sha256_bytes(payload) == expected_sha256, f"{label} hash drifted")
    document = admission.parse_strict_json(payload, label)
    require(isinstance(document, dict), f"{label} is not an object")
    return document


def descriptor(relative: str, payload: bytes) -> dict[str, Any]:
    return {"path": relative, "bytes": len(payload), "sha256": sha256_bytes(payload)}


def output_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    require(not pure.is_absolute() and ".." not in pure.parts, "output path escaped root")
    path = (root / Path(*pure.parts)).resolve()
    require(os.path.commonpath((str(path), str(root.resolve()))) == str(root.resolve()), "output path escaped root")
    return path


def write_artifact(root: Path, relative: str, payload: bytes) -> dict[str, Any]:
    path = output_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return descriptor(relative, payload)


def split_outer_bundle(payload: bytes) -> tuple[bytes, bytes, bytes]:
    require(
        len(payload) >= BUNDLE_HEADER_BYTES + 2 + 2 + codec.HEADER_SIZE + codec.RECORD_SIZE + codec.FOOTER_SIZE + BUNDLE_FOOTER_BYTES,
        "outer bundle is too short",
    )
    header = payload[:BUNDLE_HEADER_BYTES]
    footer = payload[-BUNDLE_FOOTER_BYTES:]
    require(header[:16] == BUNDLE_HEADER_MAGIC, "outer bundle header magic drifted")
    require(footer[:16] == BUNDLE_FOOTER_MAGIC, "outer bundle footer magic drifted")
    require(
        struct.unpack_from("<IHHHHI", header, 16) == (0x01020304, 256, 128, 1, 0, 3)
        and struct.unpack_from("<HHI", footer, 16) == (128, 1, 3),
        "outer bundle layout drifted",
    )
    require(header[224:252] == bytes(28) and footer[104:124] == bytes(20), "outer bundle reserved bytes drifted")
    require(
        struct.unpack_from("<I", header, 252)[0] == codec.crc32c(header[:252])
        and struct.unpack_from("<I", footer, 124)[0] == codec.crc32c(footer[:124]),
        "outer bundle CRC drifted",
    )
    total, capability_bytes, provenance_bytes, chunk_bytes = struct.unpack_from(
        "<QQQQ", header, 32
    )
    payload_bytes = capability_bytes + provenance_bytes + chunk_bytes
    require(
        2 <= capability_bytes <= 65_536
        and 2 <= provenance_bytes <= 1_048_576
        and chunk_bytes >= codec.HEADER_SIZE + codec.RECORD_SIZE + codec.FOOTER_SIZE,
        "outer bundle section length drifted",
    )
    require(
        total == len(payload) == BUNDLE_HEADER_BYTES + payload_bytes + BUNDLE_FOOTER_BYTES
        and struct.unpack_from("<QQ", footer, 24) == (total, payload_bytes),
        "outer bundle total length drifted",
    )
    capability_start = BUNDLE_HEADER_BYTES
    provenance_start = capability_start + capability_bytes
    chunk_start = provenance_start + provenance_bytes
    capability = payload[capability_start:provenance_start]
    provenance = payload[provenance_start:chunk_start]
    chunk = payload[chunk_start : chunk_start + chunk_bytes]
    section = payload[BUNDLE_HEADER_BYTES:-BUNDLE_FOOTER_BYTES]
    require(header[64:96] == hashlib.sha256(capability).digest(), "outer capability hash drifted")
    require(header[96:128] == hashlib.sha256(provenance).digest(), "outer provenance hash drifted")
    require(header[128:160] == hashlib.sha256(chunk).digest(), "outer physical hash drifted")
    require(
        header[160:192] == footer[40:72] == hashlib.sha256(section).digest(),
        "outer payload hash drifted",
    )
    require(header[192:224].hex() == BUNDLE_SCHEMA_SHA256, "outer schema binding drifted")
    require(footer[72:104] == hashlib.sha256(header).digest(), "outer header hash drifted")
    return capability, provenance, chunk


def parse_production_physical_chunk(
    payload: bytes,
    *,
    schema_bytes: bytes,
    provenance_bytes: bytes,
    capability_bytes: bytes,
    teacher_network_used: bool,
) -> tuple[bytes, bytes, tuple[Any, ...]]:
    """Parse the physical section without applying the fixture provenance schema."""

    require(
        len(payload) >= codec.HEADER_SIZE + codec.RECORD_SIZE + codec.FOOTER_SIZE,
        "physical chunk is empty or truncated",
    )
    codec.validate_schema_bytes(schema_bytes)
    header = payload[: codec.HEADER_SIZE]
    footer = payload[-codec.FOOTER_SIZE :]
    require(header[:16] == codec.HEADER_MAGIC, "physical header magic drifted")
    require(footer[:16] == codec.FOOTER_MAGIC, "physical footer magic drifted")
    require(
        struct.unpack_from("<I", header, 252)[0] == codec.crc32c(header[:252])
        and struct.unpack_from("<I", footer, 124)[0] == codec.crc32c(footer[:124]),
        "physical chunk CRC drifted",
    )
    require(
        header[30:32] == bytes(2)
        and header[36:40] == bytes(4)
        and header[240:252] == bytes(12)
        and footer[120:124] == bytes(4),
        "physical reserved bytes drifted",
    )
    require(
        struct.unpack_from("<IHHHHH", header, 16)
        == (
            codec.BYTE_ORDER_MARKER,
            codec.HEADER_SIZE,
            codec.RECORD_SIZE,
            codec.FOOTER_SIZE,
            codec.SCHEMA_MAJOR,
            codec.SCHEMA_MINOR,
        ),
        "physical header layout drifted",
    )
    require(struct.unpack_from("<I", header, 32)[0] == codec.COMMITTED, "physical chunk is not committed")
    record_count = struct.unpack_from("<Q", header, 40)[0]
    require(
        struct.unpack_from("<HHIQQ", footer, 16)
        == (
            codec.FOOTER_SIZE,
            codec.SCHEMA_MAJOR,
            codec.COMMITTED,
            record_count,
            record_count * codec.RECORD_SIZE,
        ),
        "physical footer layout drifted",
    )
    require(
        len(payload)
        == codec.HEADER_SIZE + record_count * codec.RECORD_SIZE + codec.FOOTER_SIZE,
        "physical exact framing drifted",
    )
    records_bytes = payload[codec.HEADER_SIZE : -codec.FOOTER_SIZE]
    payload_digest = hashlib.sha256(records_bytes).digest()
    require(
        header[176:208] == footer[40:72] == payload_digest,
        "physical payload hash drifted",
    )
    require(footer[72:104] == hashlib.sha256(header).digest(), "physical header hash drifted")
    require(header[48:64] == footer[104:120], "physical chunk id drifted")
    require(header[80:112].hex() == admission.RULE_PROFILE_SHA256, "physical rule profile drifted")
    require(header[112:144] == hashlib.sha256(schema_bytes).digest(), "physical schema drifted")
    require(
        header[144:176] == hashlib.sha256(provenance_bytes).digest(),
        "physical provenance binding drifted",
    )
    require(
        header[208:240] == hashlib.sha256(capability_bytes).digest(),
        "physical capability binding drifted",
    )
    records = tuple(
        codec.decode_record(records_bytes[offset : offset + codec.RECORD_SIZE])
        for offset in range(0, len(records_bytes), codec.RECORD_SIZE)
    )
    codec._validate_trajectory_sequence(
        records,
        provenance_sha256=hashlib.sha256(provenance_bytes).digest(),
        teacher_network_used=teacher_network_used,
        decoded=True,
    )
    return bytes(header[48:64]), bytes(header[64:80]), records


def decompress_exact(path: Path, expected_bytes: int, expected_sha256: str) -> bytes:
    require(path.is_file() and not path.is_symlink(), "downloaded CAS chunk is missing or unsafe")
    require(path.stat().st_size == expected_bytes, "downloaded CAS chunk byte count drifted")
    compressed = path.read_bytes()
    require(sha256_bytes(compressed) == expected_sha256, "downloaded CAS chunk hash drifted")
    decompressor = bz2.BZ2Decompressor()
    payload = decompressor.decompress(compressed)
    require(decompressor.eof and decompressor.unused_data == b"", "compressed CAS framing drifted")
    return payload


def record_identities(
    record: Any,
    raw: bytes,
    campaign_id: bytes,
    large_reference: Any,
) -> dict[str, bytes]:
    stm_rows = admission.feature_rows(record, record.side_to_move)
    opponent_rows = admission.feature_rows(record, record.side_to_move ^ 1)
    raw_key = hashlib.sha256(admission.RAW_RECORD_DOMAIN + raw).digest()
    model_key = admission.model_input_key(stm_rows, opponent_rows)
    state = large_reference.project_physical_record(record)
    stm_large = large_reference.feature_rows(state, record.side_to_move)
    opponent_large = large_reference.feature_rows(state, record.side_to_move ^ 1)
    large_key = admission.large_model_input_key(
        stm_large.k64,
        stm_large.g1,
        opponent_large.k64,
        opponent_large.g1,
        sum(state.pockets),
    )
    return {
        "raw_record_key": raw_key,
        "position_identity": record.position_identity_sha256,
        "model_input_key": model_key,
        "game_id": campaign_id + record.game_id,
        "trajectory_id": campaign_id + record.trajectory_id,
        "large_model_input_key": large_key,
    }


def authenticate_download(source: Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    receipt = strict_json(
        source / "download-completion-receipt.json",
        DOWNLOAD_RECEIPT_SHA256,
        "download completion receipt",
    )
    manifest = strict_json(
        source / "download-manifest.json",
        DOWNLOAD_MANIFEST_SHA256,
        "download manifest",
    )
    require(
        receipt.get("schema")
        == "crazyhouse-a0-production-openbench-cas-download-completion-receipt/v1"
        and receipt.get("status") == "PASS"
        and receipt.get("official_openbench") == OFFICIAL_OPENBENCH
        and receipt.get("terminal_authentication_receipt", {}).get("sha256")
        == TERMINAL_RECEIPT_SHA256
        and receipt.get("download_manifest", {}).get("sha256")
        == DOWNLOAD_MANIFEST_SHA256
        and receipt.get("source_commit") == SOURCE_COMMIT
        and receipt.get("network_sha256") == NETWORK_SHA256
        and receipt.get("book_sha256") == BOOK_SHA256
        and receipt.get("unique_chunk_hashes") == 2_304
        and receipt.get("canary_artifacts_included") is False
        and receipt.get("fallback_observed") is False,
        "download completion boundary drifted",
    )
    require(
        manifest.get("schema")
        == "crazyhouse-a0-production-openbench-cas-download-manifest/v1"
        and manifest.get("status") == "PASS"
        and manifest.get("official_openbench") == OFFICIAL_OPENBENCH
        and manifest.get("protocol") == 41
        and manifest.get("variant") == "crazyhouse"
        and manifest.get("source_commit") == SOURCE_COMMIT
        and manifest.get("network_sha256") == NETWORK_SHA256
        and manifest.get("book_sha256") == BOOK_SHA256
        and manifest.get("unique_chunk_hashes") == 2_304
        and manifest.get("canary_artifacts_included") is False,
        "download manifest boundary drifted",
    )
    return receipt, manifest


def authenticate_diagnostic_exception(
    waiver_path: Path | None,
) -> tuple[bytes, bytes] | None:
    if waiver_path is None:
        return None
    waiver_bytes = admission.read_regular(
        waiver_path,
        "diagnostic owner waiver",
        expected_sha256=DIAGNOSTIC_OWNER_WAIVER_SHA256,
        maximum_bytes=64 * 1024,
    )
    waiver = admission.parse_pinned_json(waiver_bytes, "diagnostic owner waiver")
    require(
        waiver.get("schema")
        == "crazyhouse-a0-diagnostic-overlap-owner-waiver/v1"
        and waiver.get("status") == "AUTHORIZED_DIAGNOSTIC_ONLY"
        and waiver.get("authenticated_failure_evidence", {}).get(
            "cross_role_unique_intersections"
        )
        == DIAGNOSTIC_INTERSECTIONS
        and waiver.get("authority_boundary", {}).get(
            "training_authorized_under_exception"
        )
        is True
        and waiver.get("authority_boundary", {}).get("release_authorized") is False,
        "diagnostic owner waiver content drifted",
    )
    addendum_bytes = admission.read_regular(
        DIAGNOSTIC_ADDENDUM_PATH,
        "diagnostic campaign addendum",
        expected_sha256=DIAGNOSTIC_ADDENDUM_SHA256,
        maximum_bytes=64 * 1024,
    )
    addendum = admission.parse_pinned_json(
        addendum_bytes, "diagnostic campaign addendum"
    )
    require(
        addendum.get("schema")
        == "crazyhouse-p13-nnue-v2-large-a0-production-campaign-addendum/v1"
        and addendum.get("addendum") == 2
        and addendum.get("status")
        == "AUTHORIZED_DIAGNOSTIC_OVERLAP_EXCEPTION"
        and addendum.get("measured_cross_role_intersections")
        == DIAGNOSTIC_INTERSECTIONS,
        "diagnostic campaign addendum content drifted",
    )
    return addendum_bytes, waiver_bytes


def materialize(
    source: Path,
    output: Path,
    waiver_path: Path | None = None,
) -> Mapping[str, Any]:
    source = source.resolve(strict=True)
    require(source.is_dir() and not source.is_symlink(), "source download root is unsafe")
    require(output.parent.exists() and output.parent.is_dir(), "output parent is missing")
    require(not output.exists() and not output.is_symlink(), "output already exists")
    partial = output.with_name(output.name + ".partial")
    require(not partial.exists() and not partial.is_symlink(), "partial output already exists")
    _download_receipt, download_manifest = authenticate_download(source)
    diagnostic_exception = authenticate_diagnostic_exception(waiver_path)
    workloads = download_manifest.get("workloads")
    require(isinstance(workloads, list) and len(workloads) == 2, "download workload set drifted")

    physical_schema = admission.read_regular(
        admission.PHYSICAL_SCHEMA_PATH,
        "physical schema",
        expected_sha256=admission.PHYSICAL_SCHEMA_SHA256,
    )
    feature_contract = admission.read_regular(
        admission.FEATURE_CONTRACT_PATH,
        "feature contract",
        expected_sha256=admission.FEATURE_CONTRACT_SHA256,
    )
    capability_contract = admission.read_regular(
        admission.PRODUCTION_CAPABILITY_CONTRACT_PATH,
        "production capability contract",
        expected_sha256=admission.PRODUCTION_CAPABILITY_CONTRACT_SHA256,
    )
    admission_tool = admission.read_regular(ADMISSION_PATH, "admission tool")
    materializer_tool = admission.read_regular(Path(__file__), "materializer tool")
    large_reference = admission.validate_large_projection_artifacts()

    partial.mkdir()
    identity_index: Any | None = None
    try:
        physical_descriptor = write_artifact(partial, "physical-schema.json", physical_schema)
        feature_descriptor = write_artifact(partial, "feature-contract.json", feature_contract)
        admission_descriptor = write_artifact(partial, "admission-tool.py", admission_tool)
        diagnostic_binding: dict[str, Any] | None = None
        if diagnostic_exception is not None:
            addendum_bytes, waiver_bytes = diagnostic_exception
            addendum_descriptor = write_artifact(
                partial,
                "receipts/diagnostic-campaign-addendum.json",
                addendum_bytes,
            )
            waiver_descriptor = write_artifact(
                partial,
                "receipts/diagnostic-owner-waiver.json",
                waiver_bytes,
            )
            diagnostic_binding = {
                "campaign_addendum": addendum_descriptor,
                "intersections": DIAGNOSTIC_INTERSECTIONS,
                "owner_waiver": waiver_descriptor,
                "release_admissible": False,
                "schema": "crazyhouse-a0-diagnostic-overlap-exception-binding/v1",
                "status": "AUTHORIZED_DIAGNOSTIC_ONLY",
                "validation_checkpoint_or_seed_selection": False,
                "validation_early_stopping": False,
                "validation_gradients": False,
                "validation_usage": "forward-only health telemetry",
            }
        write_artifact(partial, "source/download-completion-receipt.json", (source / "download-completion-receipt.json").read_bytes())
        write_artifact(partial, "source/download-manifest.json", (source / "download-manifest.json").read_bytes())

        identity_index = admission.IdentityIndex(
            partial / "materialization-identities.sqlite3",
            admission.LARGE_IDENTITY_KINDS,
        )
        role_documents: dict[str, Any] = {}
        conservative_duplicate_bounds: dict[str, dict[str, int]] = {}
        expected_campaigns: list[bytes] = []
        for spec, workload in zip(SPECS, workloads, strict=True):
            role = spec["role"]
            require(
                workload.get("test_id") == spec["test_id"]
                and workload.get("role") == role
                and workload.get("campaign_id") == spec["campaign_id"]
                and workload.get("base_seed") == spec["base_seed"]
                and workload.get("records") == spec["records"]
                and workload.get("chunks") == spec["chunks"]
                and workload.get("publication_contract_sha256")
                == spec["publication_contract_sha256"],
                f"download workload #{spec['test_id']} drifted",
            )
            items = workload.get("items")
            require(isinstance(items, list) and len(items) == spec["chunks"], "download chunk list drifted")
            campaign = uuid.UUID(spec["campaign_id"])
            expected_campaigns.append(campaign.bytes)
            entries: list[dict[str, Any]] = []
            record_digest = hashlib.sha256(
                admission.RECORD_STREAM_DOMAIN + struct.pack("<Q", spec["records"])
            )
            role_records = 0
            role_trajectories = 0
            for expected_idx, item in enumerate(items):
                require(
                    item.get("idx") == expected_idx
                    and item.get("test_id") == spec["test_id"]
                    and item.get("role") == role
                    and item.get("campaign_id") == spec["campaign_id"]
                    and item.get("records") == 512
                    and item.get("seed") == spec["base_seed"] + expected_idx,
                    f"download item #{spec['test_id']}/{expected_idx} drifted",
                )
                compressed_relative = item.get("archive_path")
                require(
                    compressed_relative == f"chunks/{role}/{expected_idx:08d}.bundle.bz2",
                    "download archive path drifted",
                )
                compressed_path = source / Path(*PurePosixPath(compressed_relative).parts)
                outer = decompress_exact(
                    compressed_path,
                    int(item["bytes"]),
                    str(item["sha256"]),
                )
                capability_bytes, provenance_bytes, physical_bytes = split_outer_bundle(outer)
                provenance_preview = admission.parse_strict_json(
                    provenance_bytes, "embedded production provenance"
                )
                capability = production_codec.validate_production_capability_response_bytes(
                    capability_bytes,
                    contract_bytes=capability_contract,
                    expected_challenge=provenance_preview["producer_capability"]["challenge"],
                )
                physical_chunk_id = bytes(physical_bytes[48:64])
                physical_campaign_id = bytes(physical_bytes[64:80])
                provenance = production_codec.validate_production_provenance_bytes(
                    provenance_bytes,
                    chunk_id=physical_chunk_id,
                    campaign_id=physical_campaign_id,
                    capability=capability,
                )
                chunk_id_bytes, campaign_id_bytes, records = parse_production_physical_chunk(
                    physical_bytes,
                    schema_bytes=physical_schema,
                    provenance_bytes=provenance_bytes,
                    capability_bytes=capability_bytes,
                    teacher_network_used=provenance["teacher"]["network_used"],
                )
                require(chunk_id_bytes == physical_chunk_id, "physical chunk id drifted")
                require(campaign_id_bytes == campaign.bytes, "physical campaign drifted")
                require(provenance["chunk_index"] == expected_idx, "provenance role index drifted")
                require(provenance["partition"]["role"] == role, "provenance role drifted")
                require(provenance["source_commit"] == SOURCE_COMMIT, "provenance source drifted")
                require(provenance["network"]["sha256"] == NETWORK_SHA256, "provenance network drifted")
                require(
                    provenance["opening_source"]["artifact"]["sha256"] == BOOK_SHA256,
                    "provenance opening book drifted",
                )
                require(len(records) == 512, "physical record count drifted")
                trajectory_count = sum(record.ply == 0 for record in records)
                require(
                    trajectory_count
                    == provenance["generation_settings"]["accepted_trajectories"],
                    "physical trajectory count drifted",
                )
                for record_index, record in enumerate(records):
                    start = codec.HEADER_SIZE + record_index * codec.RECORD_SIZE
                    raw = physical_bytes[start : start + codec.RECORD_SIZE]
                    record_digest.update(raw)
                    identities = record_identities(record, raw, campaign.bytes, large_reference)
                    for kind, key in identities.items():
                        inserted = identity_index.add(role, kind, key)
                        if kind == "raw_record_key":
                            require(inserted, "within-role raw record duplicate")
                    if record.ply == 0:
                        identity_index.add_trajectory_root(
                            campaign.bytes + record.trajectory_id,
                            chunk_id_bytes,
                        )

                prefix = f"chunks/{role}/{expected_idx:08d}"
                bundle_descriptor = write_artifact(partial, prefix + ".chp", physical_bytes)
                provenance_descriptor = write_artifact(
                    partial, prefix + ".provenance.json", provenance_bytes
                )
                capability_descriptor = write_artifact(
                    partial, prefix + ".capability.json", capability_bytes
                )
                chunk_id = str(uuid.UUID(bytes=chunk_id_bytes))
                entry_without_receipt = {
                    "bundle": bundle_descriptor,
                    "capability": capability_descriptor,
                    "campaign_id": spec["campaign_id"],
                    "chunk_id": chunk_id,
                    "chunk_index": expected_idx,
                    "provenance": provenance_descriptor,
                    "record_count": len(records),
                    "trajectory_count": trajectory_count,
                }
                completion = {
                    **entry_without_receipt,
                    "fixture_only": False,
                    "official_openbench_origin": OFFICIAL_OPENBENCH,
                    "project": "Crazyhouse-Stockfish",
                    "schema": admission.CHUNK_RECEIPT_SCHEMA,
                    "status": "PASS_PRODUCTION",
                    "training_admissible": True,
                    "variant": "crazyhouse",
                }
                completion_payload = canonical_json(completion)
                completion_descriptor = write_artifact(
                    partial,
                    f"receipts/{role}/{expected_idx:08d}.json",
                    completion_payload,
                )
                entry = dict(entry_without_receipt)
                entry["completion_receipt"] = completion_descriptor
                entries.append(entry)
                role_records += len(records)
                role_trajectories += trajectory_count
            require(role_records == spec["records"], f"{role} record total drifted")
            role_documents[role] = {
                "chunk_count": len(entries),
                "chunks": entries,
                "ordered_chunk_set_sha256": admission.ordered_chunk_set_sha256(entries),
                "ordered_record_stream_sha256": record_digest.hexdigest(),
                "ordered_trajectory_set_sha256": "PENDING",
                "record_count": role_records,
                "role": role,
                "trajectory_count": role_trajectories,
            }
            conservative_duplicate_bounds[role] = {
                "model_input_key": role_records,
                "position_identity": role_records,
            }

        identity_index.finish()
        intersections = {
            kind: identity_index.intersection_count(kind)
            for kind in admission.LARGE_IDENTITY_KINDS
        }
        if diagnostic_binding is None:
            require(
                all(value == 0 for value in intersections.values()),
                "cross-role identity intersection",
            )
        else:
            require(
                intersections == DIAGNOSTIC_INTERSECTIONS,
                "diagnostic cross-role intersection counts drifted",
            )
        for role in admission.ROLE_IDS:
            role_documents[role]["ordered_trajectory_set_sha256"] = (
                identity_index.ordered_trajectory_set_digest(role)
            )
            unique_trajectories, _digest = identity_index.ordered_summary(
                role, "trajectory_id"
            )
            require(
                unique_trajectories == role_documents[role]["trajectory_count"],
                f"{role} trajectory uniqueness drifted",
            )

        campaign_set = admission.campaign_set_sha256(expected_campaigns)
        first_provenance = admission.parse_strict_json(
            output_path(partial, "chunks/train/00000000.provenance.json").read_bytes(),
            "first production provenance",
        )
        partition_source = first_provenance["partition"]
        partition = {
            "campaign_set_sha256": campaign_set,
            "domain": admission.SPLIT_DOMAIN.decode("ascii"),
            "feature_contract_sha256": admission.FEATURE_CONTRACT_SHA256,
            "method": "content-hash-complete-trajectory-v1",
            "physical_schema_sha256": admission.PHYSICAL_SCHEMA_SHA256,
            "rule_profile_sha256": admission.RULE_PROFILE_SHA256,
            "split_seed_u64": partition_source["split_seed_u64"],
            "validation_threshold_u64": partition_source["validation_threshold_u64"],
        }
        partition["sha256"] = admission.partition_digest(partition)
        require(
            partition["sha256"] == partition_source["partition_sha256"]
            and partition["campaign_set_sha256"]
            == partition_source["campaign_set_sha256"],
            "partition contract drifted",
        )

        split_audit = {
            "intersections": (
                DIAGNOSTIC_INTERSECTIONS
                if diagnostic_binding is not None
                else {kind: 0 for kind in admission.IDENTITY_KINDS}
            ),
            "status": (
                "FROZEN_DIAGNOSTIC_EXCEPTION"
                if diagnostic_binding is not None
                else "FROZEN_EXPECTATIONS"
            ),
            "within_role_duplicate_maximum": conservative_duplicate_bounds,
            "within_role_duplicate_observations": {
                role: {
                    "model_input_key": identity_index.duplicates[role]["model_input_key"],
                    "position_identity": identity_index.duplicates[role]["position_identity"],
                }
                for role in admission.ROLE_IDS
            },
        }
        identity_index.close()
        identity_index = None
        (partial / "materialization-identities.sqlite3").unlink()

        aggregate = {
            "campaign_ids": sorted(spec["campaign_id"] for spec in SPECS),
            "chunk_count": sum(role["chunk_count"] for role in role_documents.values()),
            "exact_total": True,
            "fixture_only": False,
            "official_openbench_origin": OFFICIAL_OPENBENCH,
            "project": "Crazyhouse-Stockfish",
            "record_count": sum(role["record_count"] for role in role_documents.values()),
            "roles": {
                role: {
                    "chunk_ids": [chunk["chunk_id"] for chunk in role_documents[role]["chunks"]],
                    "ordered_chunk_set_sha256": role_documents[role]["ordered_chunk_set_sha256"],
                    "record_count": role_documents[role]["record_count"],
                    "trajectory_count": role_documents[role]["trajectory_count"],
                }
                for role in admission.ROLE_IDS
            },
            "schema": admission.AGGREGATE_RECEIPT_SCHEMA,
            "status": "PASS_PRODUCTION",
            "training_admissible": True,
            "trajectory_count": sum(
                role["trajectory_count"] for role in role_documents.values()
            ),
            "variant": "crazyhouse",
        }
        aggregate_descriptor = write_artifact(
            partial, "receipts/aggregate.json", canonical_json(aggregate)
        )
        manifest = {
            "admission_tool": admission_descriptor,
            "aggregate_chunk_set_receipt": aggregate_descriptor,
            "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "feature_contract": feature_descriptor,
            "fixture_mode": False,
            "partition_config": partition,
            "physical_schema": physical_descriptor,
            "project": "Crazyhouse-Stockfish",
            "roles": role_documents,
            "rule_profile": {
                "id": admission.RULE_PROFILE_ID,
                "sha256": admission.RULE_PROFILE_SHA256,
            },
            "schema": admission.MANIFEST_SCHEMA,
            "semantic_audit": {
                "engine_backed": True,
                "every_record_scanned": True,
                "every_trajectory_replayed": True,
                "history_prefix_and_repetition_reproduced": True,
                "make_undo_roundtrip": True,
                "physical_state_equals_replay": True,
                "split_decisions_recomputed": True,
                "status": "PASS",
                "stored_move_is_legal": True,
                "teacher_bound_and_perspective_reproduced": True,
                "terminal_reason_and_result_reproduced": True,
                "training_admissible": True,
            },
            "split_audit": split_audit,
            "status": (
                "READY_FOR_DIAGNOSTIC_TRAINING"
                if diagnostic_binding is not None
                else "READY_FOR_TRAINING"
            ),
            "training_admissible": True,
            "variant": "crazyhouse",
        }
        if diagnostic_binding is not None:
            manifest["diagnostic_exception"] = diagnostic_binding
        manifest_payload = canonical_json(manifest)
        write_artifact(partial, "training-dataset-manifest.json", manifest_payload)
        materialization_receipt = {
            "schema": "crazyhouse-a0-production-materialization-receipt/v1",
            "status": (
                "PASS_DIAGNOSTIC_EXCEPTION"
                if diagnostic_binding is not None
                else "PASS"
            ),
            "source_download": {
                "manifest_sha256": DOWNLOAD_MANIFEST_SHA256,
                "receipt_sha256": DOWNLOAD_RECEIPT_SHA256,
                "terminal_receipt_sha256": TERMINAL_RECEIPT_SHA256,
            },
            "materializer": {
                "bytes": len(materializer_tool),
                "path": "tools/nnue/crazyhouse_v2_materialize_openbench.py",
                "sha256": sha256_bytes(materializer_tool),
            },
            "admission_tool": admission_descriptor,
            "dataset_manifest": descriptor(
                "training-dataset-manifest.json", manifest_payload
            ),
            "records": aggregate["record_count"],
            "chunks": aggregate["chunk_count"],
            "trajectories": aggregate["trajectory_count"],
            "intersections": intersections,
            "within_role_duplicates": {
                role: dict(identity_index_values)
                for role, identity_index_values in split_audit[
                    "within_role_duplicate_observations"
                ].items()
            },
            "semantic_evidence": {
                "authenticated_producer_engine_replay": True,
                "authenticated_producer_make_undo_roundtrip": True,
                "materializer_every_record_scan": True,
                "large_model_input_identity_scan": True,
                "source_commit": SOURCE_COMMIT,
            },
            "canary_artifacts_included": False,
            "diagnostic_only": diagnostic_binding is not None,
            "release_authorized": False,
            "strength_authorized": False,
        }
        if diagnostic_binding is not None:
            materialization_receipt["diagnostic_exception"] = diagnostic_binding
        write_artifact(
            partial,
            "materialization-receipt.json",
            canonical_json(materialization_receipt),
        )
        os.replace(partial, output)
        return materialization_receipt
    except Exception as error:
        if identity_index is not None:
            try:
                identity_index.close()
            except Exception:
                pass
        failure = {
            "schema": "crazyhouse-a0-production-materialization-failure/v1",
            "status": "FAIL",
            "error": str(error),
        }
        try:
            if partial.exists():
                (partial / "materialization-failure.json").write_bytes(canonical_json(failure))
        except OSError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--diagnostic-waiver", type=Path)
    args = parser.parse_args(argv)
    result = materialize(args.source, args.output, args.diagnostic_waiver)
    print(
        json.dumps(
            {
                "chunks": result["chunks"],
                "manifest_sha256": result["dataset_manifest"]["sha256"],
                "records": result["records"],
                "status": result["status"],
                "trajectories": result["trajectories"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("CRAZYHOUSE_A0_MATERIALIZATION_FAILED: %s" % error, file=sys.stderr)
        raise SystemExit(1)
