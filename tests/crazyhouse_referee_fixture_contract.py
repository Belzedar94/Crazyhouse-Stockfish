#!/usr/bin/env python3
"""Authenticate the frozen Crazyhouse referee source map and fixture contract.

This verifier is intentionally behavior-independent.  It proves that the
pre-implementation fixture package is internally consistent and, when a clean
CuteChess checkout is supplied, that the mapped upstream bytes are exact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "tests" / "crazyhouse" / "g4-referee-cases-v1.json"
DEFAULT_ADDENDUM = (
    ROOT / "tests" / "crazyhouse" / "g4-referee-cases-v1.addendum.001.json"
)
DEFAULT_SOURCE_MAP = ROOT / "tests" / "crazyhouse" / "g4-referee-source-map-v1.json"
DEFAULT_MATRIX = (
    ROOT / "tests" / "crazyhouse" / "g4-participant-matrix-v1.addendum.002.json"
)
DEFAULT_LINEAGE = (
    ROOT / "tests" / "crazyhouse" / "g4-participant-matrix-v1.addendum.001.json"
)
PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
PROFILE_TOKEN = f"{PROFILE_ID}@{PROFILE_SHA256}"
NONCE = "0123456789abcdef0123456789abcdef"
HEX_64 = re.compile(r"[0-9a-f]{64}")
HEX_32 = re.compile(r"[0-9a-f]{32}")


class ContractFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    require(not duplicates, f"duplicate JSON object keys: {duplicates}")
    return dict(pairs)


def load_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    require(not raw.startswith(b"\xef\xbb\xbf"), f"{path.name}: UTF-8 BOM is forbidden")
    require(b"\r" not in raw, f"{path.name}: CR bytes are forbidden")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractFailure(f"{path.name}: invalid UTF-8 JSON: {exc}") from exc
    require(isinstance(value, dict), f"{path.name}: top level must be an object")
    return value


def run_git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ContractFailure(f"git {' '.join(args)} failed: {stderr}")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def resolve_pin(document: Path, value: str) -> Path:
    candidate = Path(value)
    require(not candidate.is_absolute(), f"{document.name}: absolute pin path is forbidden")
    if value.startswith(("tests/", "docs/", "src/", "Makefile")):
        return (ROOT / candidate).resolve()
    return (document.parent / candidate).resolve()


def authenticate_pin(
    document: Path,
    pin: dict[str, Any],
    *,
    external: bool,
) -> bool:
    require(set(("path", "bytes", "sha256")) <= pin.keys(), f"{document.name}: malformed pin")
    path = resolve_pin(document, str(pin["path"]))
    inside_repo = path == ROOT or ROOT in path.parents
    if not inside_repo and not external:
        return False
    require(path.is_file(), f"{document.name}: pinned file missing: {path}")
    raw = path.read_bytes()
    require(len(raw) == pin["bytes"], f"{document.name}: byte drift for {pin['path']}")
    require(
        sha256_bytes(raw) == pin["sha256"],
        f"{document.name}: SHA-256 drift for {pin['path']}",
    )
    return True


def iter_pins(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if set(("path", "bytes", "sha256")) <= value.keys():
            yield value
            return
        for child in value.values():
            yield from iter_pins(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_pins(child)


def collect_case_ids(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        identifier = value.get("id")
        if isinstance(identifier, str) and identifier.startswith("CH-G4-REF-"):
            found.append(identifier)
        for child in value.values():
            found.extend(collect_case_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_case_ids(child))
    return found


def verify_profiles(*documents: dict[str, Any]) -> None:
    for document in documents:
        if "profile_id" in document:
            require(document["profile_id"] == PROFILE_ID, "profile ID drifted")
        if "profile_sha256" in document:
            require(document["profile_sha256"] == PROFILE_SHA256, "profile SHA-256 drifted")


def verify_ids(cases: dict[str, Any], addendum: dict[str, Any]) -> int:
    base_ids = [case.get("id") for case in cases.get("cases", [])]
    require(len(base_ids) == 14, "base referee case count is not 14")
    require(all(isinstance(value, str) for value in base_ids), "base case ID is missing")
    addendum_ids = collect_case_ids(addendum)
    combined = [str(value) for value in base_ids] + addendum_ids
    duplicates = sorted(key for key, count in Counter(combined).items() if count > 1)
    require(not duplicates, f"referee fixture IDs are not unique: {duplicates}")
    require(len(addendum_ids) == 42, f"expected 42 addendum IDs, got {len(addendum_ids)}")
    return len(combined)


def verify_history(addendum: dict[str, Any]) -> int:
    contract = addendum["history_digest_contract"]
    require(contract["schema_token"] == "crazyhouse-history-digest/v1", "history schema drifted")
    vectors = contract["golden_vectors"]
    require(len(vectors) == 4, "history vector count drifted")
    parsed: dict[str, tuple[list[str], str]] = {}
    for vector in vectors:
        identifier = vector["id"]
        payload = vector["payload_utf8"].encode("utf-8")
        require(not payload.startswith(b"\xef\xbb\xbf"), f"{identifier}: BOM is forbidden")
        require(b"\r" not in payload, f"{identifier}: CR is forbidden")
        require(payload.endswith(b"\n"), f"{identifier}: final LF is required")
        require(len(payload) == vector["bytes"], f"{identifier}: byte count drifted")
        require(sha256_bytes(payload) == vector["sha256"], f"{identifier}: digest drifted")
        lines = payload.decode("utf-8").splitlines()
        require(lines[0] == "schema\tcrazyhouse-history-digest/v1", f"{identifier}: schema line drifted")
        require(
            lines[1] == f"profile\t{PROFILE_ID}\t{PROFILE_SHA256}",
            f"{identifier}: profile line drifted",
        )
        records = [line.split("\t") for line in lines[2:]]
        require(all(len(record) == 3 for record in records), f"{identifier}: record framing drifted")
        require(
            [int(record[0]) for record in records] == list(range(len(records))),
            f"{identifier}: ply sequence drifted",
        )
        require(records[0][1] == "-", f"{identifier}: root move must be dash")
        for _, _, fen in records:
            require(len(fen.split()) == 6, f"{identifier}: FEN field count drifted")
            require("[" in fen.split()[0] and "]" in fen.split()[0], f"{identifier}: noncanonical pocket FEN")
            require("[-]" not in fen, f"{identifier}: dash-empty pocket is forbidden")
        parsed[identifier] = ([record[2] for record in records], vector["sha256"])

    path_a = parsed["CH-G4-REF-HISTORY-PATH-A"]
    path_b = parsed["CH-G4-REF-HISTORY-PATH-B"]
    require(path_a[0][0] == path_b[0][0], "history A/B roots differ")
    require(path_a[0][-1] == path_b[0][-1], "history A/B final states differ")
    require(path_a[1] != path_b[1], "history A/B digests must differ")

    by_id = {vector["id"]: vector for vector in vectors}
    deadline = addendum["clock_contract"]["cases"][0]
    require(
        deadline["expected"]["history_sha256"]
        == by_id["CH-G4-REF-DEADLINE-EQUAL-LEGAL-HISTORY"]["sha256"],
        "deadline history cross-link drifted",
    )
    join = addendum["same_executable_join"]
    require(
        join["expected"]["history_sha256"]
        == by_id["CH-G4-REF-HISTORY-JOIN"]["sha256"],
        "probe/match history cross-link drifted",
    )
    require(
        join["expected"]["history_bytes"]
        == by_id["CH-G4-REF-HISTORY-JOIN"]["bytes"],
        "probe/match history byte cross-link drifted",
    )
    return len(vectors)


def verify_capability(addendum: dict[str, Any]) -> None:
    contract = addendum["engine_capability_contract"]
    require(HEX_32.fullmatch(contract["nonce"]["fixture_value"]) is not None, "fixture nonce grammar drifted")
    require(contract["nonce"]["fixture_value"] == NONCE, "fixture nonce value drifted")
    advertisements = contract["required_advertisements"]
    require(
        advertisements
        == [
            "option name UCI_Variant type combo default crazyhouse var chess var crazyhouse",
            f"option name CrazyhouseProfile type string default {PROFILE_TOKEN}",
            "option name CrazyhouseCapabilityNonce type string default <empty>",
        ],
        "capability advertisements drifted",
    )
    expected_ack = (
        "info string crazyhouse_capability_ack status=ok "
        f"profile={PROFILE_ID} profile_sha256={PROFILE_SHA256} nonce={NONCE}"
    )
    wire = contract["positive_wire_order"]
    require(len(wire) == 6, "positive capability wire length drifted")
    require(wire[3] == "isready" and wire[4] == expected_ack and wire[5] == "readyok", "capability barrier order drifted")
    require(wire.count(expected_ack) == 1, "capability acknowledgement is not unique")
    require(len(contract["negative_cases"]) == 18, "capability negative-case count drifted")


def verify_contract_links(
    source_map: dict[str, Any], addendum: dict[str, Any], matrix: dict[str, Any]
) -> None:
    boundaries = source_map["boundaries"]
    boundary_ids = [value["id"] for value in boundaries]
    require(len(boundary_ids) == 12 and len(set(boundary_ids)) == 12, "source boundary IDs drifted")
    mapped_paths = {entry["path"] for entry in source_map["source_files"]}
    for boundary in boundaries:
        missing = sorted(set(boundary.get("paths", ())) - mapped_paths)
        require(not missing, f"{boundary['id']}: unmapped source paths: {missing}")
    require(len(mapped_paths) == 34, f"expected 34 source files, got {len(mapped_paths)}")
    require(len(source_map["discovered_gap_ids"]) == 11, "discovered gap count drifted")
    require(source_map["behavior_source_edits_observed"] is False, "source map is not pre-behavior")

    participant_ids = [entry["id"] for entry in matrix["participant_assignments"]]
    require(len(participant_ids) == 8 and len(set(participant_ids)) == 8, "participant assignments drifted")
    required = {
        "scalachess",
        "python-chess",
        "chessops",
        "official_base_engine_uci",
        "exact_referee_probe",
        "exact_referee_match",
        "deterministic_rule_free_uci_actors",
        "retired_horde_derived_referee_binary",
    }
    require(set(participant_ids) == required, "participant assignment set drifted")
    require(matrix["fixture_freeze_status"] == "CLOSED_BEFORE_BEHAVIOR_CODE", "fixture is not closed")
    require(matrix["candidate_referee_branch_created"] is False, "fixture matrix no longer describes pre-branch state")
    require(addendum["effective_fixture_status"] == "FROZEN_BEFORE_BEHAVIOR_CODE", "fixture status drifted")
    require(not addendum["strength_claim"] and not addendum["release_claim"], "fixture makes a forbidden claim")


def verify_referee_checkout(
    root: Path, source_map: dict[str, Any], lineage: dict[str, Any]
) -> int:
    root = root.resolve(strict=True)
    require((root / ".git").exists(), f"referee root is not a Git checkout: {root}")
    target = lineage["target_referee_lineage"]
    expected_commit = source_map["upstream"]["commit"]
    expected_tree = source_map["upstream"]["tree"]
    require(expected_commit == target["mandatory_clean_root_commit"], "clean root commit pins disagree")
    require(expected_tree == target["mandatory_clean_root_tree"], "clean root tree pins disagree")
    require(run_git(root, "rev-parse", "HEAD") == expected_commit, "referee HEAD drifted")
    require(run_git(root, "rev-parse", "HEAD^{tree}") == expected_tree, "referee tree drifted")
    require(run_git(root, "status", "--porcelain=v1", "--untracked-files=all") == "", "referee checkout is dirty")
    require(run_git(root, "rev-parse", "--is-shallow-repository") == "false", "referee checkout is shallow")
    require(run_git(root, "replace", "-l") == "", "referee checkout has replace refs")
    grafts = Path(run_git(root, "rev-parse", "--git-path", "info/grafts"))
    if not grafts.is_absolute():
        grafts = root / grafts
    require(not grafts.exists() or grafts.stat().st_size == 0, "referee checkout has grafts")
    forbidden = target["forbidden_ancestor_commit"]
    probe = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{forbidden}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(probe.returncode != 0, "forbidden Horde-derived commit exists in clean checkout")
    fsck = subprocess.run(
        ["git", "-C", str(root), "fsck", "--full"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(fsck.returncode == 0, "referee git fsck --full failed")

    for entry in source_map["source_files"]:
        path = root / entry["path"]
        require(path.is_file(), f"mapped referee source missing: {entry['path']}")
        raw = path.read_bytes()
        require(len(raw) == entry["bytes"], f"mapped referee byte drift: {entry['path']}")
        require(sha256_bytes(raw) == entry["sha256"], f"mapped referee hash drift: {entry['path']}")
    return len(source_map["source_files"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--addendum", type=Path, default=DEFAULT_ADDENDUM)
    parser.add_argument("--source-map", type=Path, default=DEFAULT_SOURCE_MAP)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--lineage", type=Path, default=DEFAULT_LINEAGE)
    parser.add_argument(
        "--referee-root",
        type=Path,
        required=True,
        help="clean non-shallow CuteChess checkout at the frozen upstream commit",
    )
    parser.add_argument(
        "--verify-external-pins",
        action="store_true",
        help="also authenticate private receipt pins outside the engine repository",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        paths = [args.cases, args.addendum, args.source_map, args.matrix, args.lineage]
        require(all(path.resolve().is_file() for path in paths), "fixture input file is missing")
        cases, addendum, source_map, matrix, lineage = [load_json(path.resolve()) for path in paths]
        require(cases["schema"] == "crazyhouse-g4-referee-cases/v1", "base case schema drifted")
        require(addendum["schema"] == "crazyhouse-g4-referee-cases-addendum/v1", "addendum schema drifted")
        require(source_map["schema"] == "crazyhouse-g4-referee-source-map/v1", "source-map schema drifted")
        require(matrix["schema"] == "crazyhouse-g4-participant-matrix-addendum/v1", "matrix schema drifted")
        require(lineage["schema"] == "crazyhouse-g4-participant-matrix-addendum/v1", "lineage schema drifted")
        require(addendum["addendum"] == 1 and matrix["addendum"] == 2 and lineage["addendum"] == 1, "addendum number drifted")
        verify_profiles(cases, addendum, source_map, matrix)

        pins_checked = 0
        pins_skipped = 0
        source_map_receipt_pins = {
            "fetch_start_receipt": source_map["upstream"]["fetch_start_receipt"],
            "fetch_end_receipt": source_map["upstream"]["fetch_end_receipt"],
            "advisory_review": source_map["advisory_review"],
        }
        for document, value in (
            (args.source_map.resolve(), source_map_receipt_pins),
            (args.addendum.resolve(), addendum["pins"]),
            (args.matrix.resolve(), matrix["pins"]),
        ):
            for pin in iter_pins(value):
                if authenticate_pin(document, pin, external=args.verify_external_pins):
                    pins_checked += 1
                else:
                    pins_skipped += 1

        fixture_ids = verify_ids(cases, addendum)
        history_vectors = verify_history(addendum)
        verify_capability(addendum)
        verify_contract_links(source_map, addendum, matrix)
        source_files = verify_referee_checkout(args.referee_root, source_map, lineage)
        require(all(HEX_64.fullmatch(value["sha256"]) for value in source_map["source_files"]), "source hash grammar drifted")
        print(
            "PASS crazyhouse_referee_fixture_contract "
            f"fixture_ids={fixture_ids} history_vectors={history_vectors} "
            f"source_files={source_files} pins_checked={pins_checked} "
            f"external_pins_skipped={pins_skipped} referee_commit={source_map['upstream']['commit']} "
            "behavior_code=ABSENT strength_claim=FALSE release_claim=FALSE"
        )
        return 0
    except (ContractFailure, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"FAIL crazyhouse_referee_fixture_contract: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
