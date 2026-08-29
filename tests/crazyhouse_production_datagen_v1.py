#!/usr/bin/env python3
"""Runtime contract test for the production Crazyhouse physical DATAGEN route."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from typing import Any
import uuid


BOOK_BYTES = 39_922
BOOK_SHA256 = "1371e87ce3bdb875d922ad0061c96c4a123bc571daf4ae2bff24e5176287f0fa"
CAPABILITY_SHA256 = "23386f8c51307522b08fbe3bef309791c90e40022a62e073eaaaf08a9467397b"
FEATURE_SHA256 = "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6"
NETWORK_BYTES = 58_534_811
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
PHYSICAL_SHA256 = "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55"
POLICY_SHA256 = "475fd0fb9a929e964ff32357031a18d33ecc2543e8681cc73068858c10db3014"
PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
THRESHOLD = 1 << 61
G0_BOOK_SHA256 = "f99f8211316813924e52fb13fbb65a5bc27dcd585e2e32a86d90db0d113fd2f6"
G0_POLICY_SHA256 = "fc67430cb09eb28531889a6b8f99a02f4b033c5bd71cbef7d2e9add8a7d573c6"

IDENTITY_DOMAIN = b"Crazyhouse-Stockfish selfplay deterministic identity v1\0"
PARTITION_DOMAIN = b"Crazyhouse-Stockfish physical trajectory split v1\0"
CAMPAIGN_SET_DOMAIN = b"Crazyhouse-Stockfish campaign set v1\0"

CAMPAIGNS = (
    uuid.UUID("1c43a916-3a9c-59ed-809e-59da773e5c3e"),
    uuid.UUID("db9b8944-a1e4-5b7d-a237-e6883b9fc43c"),
)
BASE_SEED = 7_290_041


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def parse_canonical(payload: bytes, label: str) -> dict[str, Any]:
    require(payload.endswith(b"\n") and b"\r" not in payload and b"\0" not in payload, f"{label}: framing")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label}: invalid JSON: {exc}") from exc
    require(isinstance(value, dict) and canonical_json(value) == payload, f"{label}: noncanonical JSON")
    return value


def derive_id(kind: str, campaign: uuid.UUID, candidate_index: int = 0) -> bytes:
    payload = (
        IDENTITY_DOMAIN
        + kind.encode("ascii")
        + b"\0"
        + campaign.bytes
        + struct.pack("<QQ", 0, candidate_index)
    )
    output = bytearray(hashlib.sha256(payload).digest()[:16])
    output[6] = (output[6] & 0x0F) | 0x50
    output[8] = (output[8] & 0x3F) | 0x80
    return bytes(output)


def split_value(campaign: uuid.UUID, trajectory: bytes, split_seed: int) -> int:
    return int.from_bytes(
        hashlib.sha256(
            PARTITION_DOMAIN + struct.pack("<Q", split_seed) + campaign.bytes + trajectory
        ).digest()[:8],
        "little",
    )


def shared_split_seed() -> int:
    for split_seed in range(100_000):
        first_validation = split_value(CAMPAIGNS[0], derive_id("trajectory", CAMPAIGNS[0]), split_seed) < THRESHOLD
        second_validation = split_value(CAMPAIGNS[1], derive_id("trajectory", CAMPAIGNS[1]), split_seed) < THRESHOLD
        if not first_validation and second_validation:
            return split_seed
    raise ContractError("could not derive a shared train/validation test split")


def campaign_set_sha256() -> str:
    campaigns = sorted(campaign.bytes for campaign in CAMPAIGNS)
    digest = hashlib.sha256(CAMPAIGN_SET_DOMAIN + struct.pack("<Q", len(campaigns)))
    for campaign in campaigns:
        digest.update(campaign)
    return digest.hexdigest()


def partition_sha256(split_seed: int, campaign_set: str) -> str:
    body = {
        "campaign_set_sha256": campaign_set,
        "domain": PARTITION_DOMAIN.decode("ascii"),
        "feature_contract_sha256": FEATURE_SHA256,
        "method": "content-hash-complete-trajectory-v1",
        "physical_schema_sha256": PHYSICAL_SHA256,
        "rule_profile_sha256": PROFILE_SHA256,
        "split_seed_u64": split_seed,
        "validation_threshold_u64": THRESHOLD,
    }
    return hashlib.sha256(canonical_json(body)).hexdigest()


def quote_token(value: str) -> str:
    require(not any(character in value for character in "\r\n\0\""), "unsafe command token")
    return f'"{value}"' if any(character.isspace() for character in value) else value


def render(tokens: list[str]) -> bytes:
    return (" ".join(quote_token(token) for token in tokens) + "\nquit\n").encode("utf-8")


def capture(
    executable: Path,
    *,
    argv: tuple[str, ...] = (),
    stdin: bytes | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        (str(executable), *argv),
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def command(
    *,
    producer: Path,
    book: Path,
    network: Path,
    output: Path,
    campaign: uuid.UUID,
    role: str,
    count: int,
    split_seed: int,
    campaign_set: str,
    partition: str,
) -> list[str]:
    values = {
        "--artifact-repo-path": "artifacts/crazyhouse-stockfish-datagen",
        "--base-seed": str(BASE_SEED),
        "--book": str(book),
        "--book-repo-path": "openbench/books/CRAZYHOUSE_openings.epd",
        "--book-sha256": BOOK_SHA256,
        "--campaign-id": str(campaign),
        "--campaign-set-sha256": campaign_set,
        "--cohort": "g11-production-contract-v1",
        "--count": str(count),
        "--depth": "64",
        "--exploration-max-score-diff": "256",
        "--exploration-multipv": "4",
        "--exploration-plies": "8",
        "--external-workload-id": "g11-local-production-contract-v1",
        "--hash-mb": "128",
        "--max-candidate-games": "1",
        "--max-game-ply": "512",
        "--network": str(network),
        "--network-repo-path": "artifacts/networks/crazyhouse_run15rl_e190_l03.nnue",
        "--network-sha256": NETWORK_SHA256,
        "--nodes": "16384",
        "--openbench-protocol": "41",
        "--output": str(output),
        "--partition-sha256": partition,
        "--producer-sha256": sha256_file(producer),
        "--role": role,
        "--seed": str(BASE_SEED),
        "--selection-policy-sha256": POLICY_SHA256,
        "--split-seed": str(split_seed),
        "--threads": "1",
        "--validation-threshold": str(THRESHOLD),
    }
    tokens = ["crazyhouse_generate_physical_production_v1"]
    for key, value in values.items():
        tokens.extend((key, value))
    return tokens


def exercise_g0_control(producer: Path, book: Path, network: Path, output: Path) -> str:
    campaign = "42e04e75-21bb-5e7f-8617-54e5bc72b5a3"
    seed = 8_964_207_305_086_120_581
    values = {
        "--artifact-repo-path": "artifacts/crazyhouse-stockfish-datagen",
        "--base-seed": str(seed),
        "--book": str(book),
        "--book-repo-path": "tests/crazyhouse/data/crazyhouse-selfplay-g0-openings-v1.epd",
        "--book-sha256": G0_BOOK_SHA256,
        "--campaign-id": campaign,
        "--count": "4",
        "--depth": "1",
        "--hash-mb": "16",
        "--max-candidate-games": "2",
        "--max-game-ply": "4",
        "--network": str(network),
        "--network-repo-path": "artifacts/networks/crazyhouse_run15rl_e190_l03.nnue",
        "--network-sha256": NETWORK_SHA256,
        "--nodes": "0",
        "--output": str(output),
        "--producer-sha256": sha256_file(producer),
        "--seed": str(seed),
        "--selection-policy-sha256": G0_POLICY_SHA256,
        "--threads": "1",
    }
    tokens = ["crazyhouse_generate_physical_v1"]
    for key, value in values.items():
        tokens.extend((key, value))
    completed = capture(producer, stdin=render(tokens), timeout=60)
    require(completed.returncode == 0 and completed.stderr == b"", f"G0 control generation: {completed.stderr!r}")
    result = parse_canonical(completed.stdout, "G0 control result")
    require(
        result["schema"] == "crazyhouse-datagen-selfplay-result/v1"
        and result["status"] == "committed"
        and result["records"] == 4
        and result["trajectories"] == 2,
        "G0 control result",
    )
    payload = output.read_bytes()
    require(
        result["bundle_sha256"] == hashlib.sha256(payload).hexdigest(),
        "G0 control bundle identity",
    )
    return result["bundle_sha256"]


def crc32c(payload: bytes) -> int:
    crc = 0xFFFFFFFF
    for octet in payload:
        crc ^= octet
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def authenticate_bundle(
    path: Path,
    result: dict[str, Any],
    *,
    producer: Path,
    campaign: uuid.UUID,
    role: str,
    expected_records: int,
    split_seed: int,
    campaign_set: str,
    partition: str,
) -> bytes:
    payload = path.read_bytes()
    require(result["schema"] == "crazyhouse-datagen-production-result/v1", "result schema")
    require(result["status"] == "committed" and result["records"] == expected_records, "result status/count")
    require(result["bundle_bytes"] == len(payload) and result["bundle_sha256"] == hashlib.sha256(payload).hexdigest(), "bundle result identity")
    require(payload[:8] == b"CHBNDLV1" and payload[-128:-118] == b"CHBNDENDV1", "bundle framing")
    capability_size, provenance_size, chunk_size = struct.unpack_from("<QQQ", payload, 40)
    start = 256
    capability = payload[start : start + capability_size]
    start += capability_size
    provenance = payload[start : start + provenance_size]
    start += provenance_size
    chunk = payload[start : start + chunk_size]
    require(start + chunk_size + 128 == len(payload), "bundle section lengths")
    cap = parse_canonical(capability, "embedded capability")
    prov = parse_canonical(provenance, "embedded provenance")
    require(cap["artifact_sha256"] == sha256_file(producer), "embedded producer identity")
    require(cap["production_generation_authorized"] is True, "embedded production authorization")
    require(prov["campaign_id"] == str(campaign) and prov["partition"]["role"] == role, "provenance campaign/role")
    require(prov["partition"]["split_seed_u64"] == split_seed, "provenance split seed")
    require(prov["partition"]["campaign_set_sha256"] == campaign_set, "provenance campaign set")
    require(prov["partition"]["partition_sha256"] == partition, "provenance partition")
    require(prov["partition"]["domain"] == PARTITION_DOMAIN.decode("ascii"), "provenance partition domain")
    require(prov["generation_settings"]["training_admissible"] is True, "provenance admission")
    require(prov["generation_settings"]["fixture_only"] is False, "provenance fixture boundary")
    require(prov["official_openbench_origin"] == "https://belzedar.duckdns.org", "provenance origin")
    require(hashlib.sha256(capability).hexdigest() == result["capability_sha256"], "capability result identity")
    require(hashlib.sha256(provenance).hexdigest() == result["provenance_sha256"], "provenance result identity")
    require(hashlib.sha256(chunk).hexdigest() == result["chunk_sha256"], "chunk result identity")

    require(chunk[:8] == b"CHPHYSV1" and chunk[-128:-117] == b"CHPHYSENDV1", "chunk framing")
    count = struct.unpack_from("<Q", chunk, 40)[0]
    require(count == expected_records and len(chunk) == 256 + count * 256 + 128, "physical exact count")
    records = [chunk[256 + index * 256 : 512 + index * 256] for index in range(count)]
    trajectory = records[0][32:48]
    for index, record in enumerate(records):
        require(record[:4] == b"CHR1" and struct.unpack_from("<HH", record, 4) == (1, 256), "record framing")
        require(struct.unpack_from("<Q", record, 8)[0] == index, "record sequence")
        require(record[32:48] == trajectory and struct.unpack_from("<I", record, 48)[0] == index, "complete trajectory framing")
        require(struct.unpack_from("<I", record, 252)[0] == crc32c(record[:252]), "record CRC32C")
        terminal = record[111]
        require((terminal == 0) == (index + 1 < count), "terminal framing")
        require(record[126] in ({1, 2} if terminal == 0 else {0}), "teacher framing")
    expected_role = "validation" if split_value(campaign, trajectory, split_seed) < THRESHOLD else "train"
    require(expected_role == role, "record trajectory partition recomputation")
    require(result["trajectories"] == 1, "single-candidate trajectory count")
    return payload


def exercise_role(
    *,
    producer: Path,
    book: Path,
    network: Path,
    directory: Path,
    campaign: uuid.UUID,
    role: str,
    split_seed: int,
    campaign_set: str,
    partition: str,
) -> tuple[int, str]:
    probe_output = directory / f"{role}-probe.bin"
    probe = capture(
        producer,
        stdin=render(
            command(
                producer=producer,
                book=book,
                network=network,
                output=probe_output,
                campaign=campaign,
                role=role,
                count=1,
                split_seed=split_seed,
                campaign_set=campaign_set,
                partition=partition,
            )
        ),
    )
    require(probe.returncode != 0 and probe.stdout == b"" and not probe_output.exists(), f"{role}: quota probe")
    match = re.search(rb"complete trajectory record count ([0-9]+) exceeds", probe.stderr)
    require(match is not None, f"{role}: probe did not return a complete trajectory count: {probe.stderr!r}")
    count = int(match.group(1))
    require(1 < count <= 513, f"{role}: probed record count")

    output = directory / f"{role}.bin"
    completed = capture(
        producer,
        stdin=render(
            command(
                producer=producer,
                book=book,
                network=network,
                output=output,
                campaign=campaign,
                role=role,
                count=count,
                split_seed=split_seed,
                campaign_set=campaign_set,
                partition=partition,
            )
        ),
    )
    require(completed.returncode == 0 and completed.stderr == b"", f"{role}: generation: {completed.stderr!r}")
    result = parse_canonical(completed.stdout, f"{role} result")
    bundle = authenticate_bundle(
        output,
        result,
        producer=producer,
        campaign=campaign,
        role=role,
        expected_records=count,
        split_seed=split_seed,
        campaign_set=campaign_set,
        partition=partition,
    )
    return count, hashlib.sha256(bundle).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--normal-engine", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()

    root = args.source_root.resolve()
    producer = args.producer.resolve()
    normal_engine = args.normal_engine.resolve()
    network = args.network.resolve()
    book = root / "openbench/books/CRAZYHOUSE_openings.epd"
    g0_book = root / "tests/crazyhouse/data/crazyhouse-selfplay-g0-openings-v1.epd"
    policy = root / "openbench/books/CRAZYHOUSE_datagen_selection_policy_v1.txt"
    contract_path = root / "tests/crazyhouse/datagen-production-capability-v1.json"
    require(all(path.is_file() for path in (producer, normal_engine, network, book, g0_book, policy, contract_path)), "missing runtime input")
    require(book.stat().st_size == BOOK_BYTES and sha256_file(book) == BOOK_SHA256, "official book identity")
    require(network.stat().st_size == NETWORK_BYTES and sha256_file(network) == NETWORK_SHA256, "network identity")
    require(sha256_file(policy) == POLICY_SHA256 and sha256_file(contract_path) == CAPABILITY_SHA256, "contract/policy identity")
    require(sha256_file(g0_book) == G0_BOOK_SHA256, "G0 book identity")

    challenge = hashlib.sha256(b"Crazyhouse production DATAGEN capability test\0").hexdigest()[:32]
    cap_run = capture(
        producer,
        argv=("--datagen-production-capabilities-v1", "--challenge", challenge),
        timeout=20,
    )
    require(cap_run.returncode == 0 and cap_run.stderr == b"", "production capability invocation")
    capability = parse_canonical(cap_run.stdout, "production capability")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(
        set(capability) == set(contract["required_response_fields"]),
        "capability exact field inventory",
    )
    for key, value in contract["required_values"].items():
        require(capability.get(key) == value, f"capability exact field {key}")
    require(capability["challenge"] == challenge and capability["artifact_sha256"] == sha256_file(producer), "capability dynamic identity")
    require(capability["production_generation_authorized"] is True, "clean producer authorization")
    require(
        capability["trajectory_partition_domain"] == PARTITION_DOMAIN.decode("ascii"),
        "capability partition domain",
    )

    malformed = capture(
        producer,
        argv=("--datagen-production-capabilities-v1", "--challenge", challenge.upper()),
        timeout=20,
    )
    require(malformed.returncode != 0 and malformed.stdout == b"", "uppercase capability challenge admitted")
    legacy = capture(
        producer,
        argv=("--datagen-selfplay-capabilities-v1", "--challenge", challenge),
        timeout=20,
    )
    require(parse_canonical(legacy.stdout, "G0 capability")["schema"] == "crazyhouse-datagen-selfplay-capability-response/v1", "G0 capability drift")
    normal = capture(
        normal_engine,
        argv=("--datagen-production-capabilities-v1", "--challenge", challenge),
        timeout=20,
    )
    require(
        b"crazyhouse-datagen-production-capability-response/v1"
        not in normal.stdout + normal.stderr,
        "normal engine advertised production DATAGEN",
    )
    normal_stdin = capture(
        normal_engine,
        stdin=b"crazyhouse_generate_physical_production_v1\nquit\n",
        timeout=20,
    )
    require(
        b"crazyhouse-datagen-production-result/v1"
        not in normal_stdin.stdout + normal_stdin.stderr,
        "normal engine executed production DATAGEN stdin",
    )

    split_seed = shared_split_seed()
    campaign_set = campaign_set_sha256()
    partition = partition_sha256(split_seed, campaign_set)
    with tempfile.TemporaryDirectory(prefix="crazyhouse-production-datagen-") as temporary:
        directory = Path(temporary)
        g0_sha = exercise_g0_control(
            producer, g0_book, network, directory / "g0-control.bin"
        )
        wrong = command(
            producer=producer,
            book=book,
            network=network,
            output=directory / "wrong-partition.bin",
            campaign=CAMPAIGNS[0],
            role="train",
            count=1,
            split_seed=split_seed,
            campaign_set=campaign_set,
            partition="0" * 64,
        )
        rejected = capture(producer, stdin=render(wrong), timeout=20)
        require(rejected.returncode != 0 and rejected.stdout == b"", "wrong partition digest admitted")

        train_count, train_sha = exercise_role(
            producer=producer,
            book=book,
            network=network,
            directory=directory,
            campaign=CAMPAIGNS[0],
            role="train",
            split_seed=split_seed,
            campaign_set=campaign_set,
            partition=partition,
        )
        validation_count, validation_sha = exercise_role(
            producer=producer,
            book=book,
            network=network,
            directory=directory,
            campaign=CAMPAIGNS[1],
            role="validation",
            split_seed=split_seed,
            campaign_set=campaign_set,
            partition=partition,
        )

    print(
        json.dumps(
            {
                "campaign_set_sha256": campaign_set,
                "partition_sha256": partition,
                "producer_sha256": sha256_file(producer),
                "schema": "crazyhouse-production-datagen-runtime-test/v1",
                "split_seed_u64": split_seed,
                "status": "PASS",
                "g0_control_bundle_sha256": g0_sha,
                "train": {"bundle_sha256": train_sha, "records": train_count},
                "validation": {"bundle_sha256": validation_sha, "records": validation_count},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL crazyhouse production DATAGEN: {exc}", flush=True)
        raise SystemExit(1)
