#!/usr/bin/env python3
"""Independently verify a Crazyhouse post-release checkpoint hash chain."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Optional, Sequence


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
UTC_TEXT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
REPOSITORY_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

SCHEDULE = (
    ("T0", 0, 300),
    ("T+15m", 900, 300),
    ("T+1h", 3_600, 600),
    ("T+6h", 21_600, 1_800),
    ("T+24h", 86_400, 3_600),
    ("T+72h", 259_200, 14_400),
    ("T+168h", 604_800, 43_200),
)
TARGETS = ("windows-x86-64", "windows-x86-64-avx2")
STATES = {
    "HEALTHY",
    "DEGRADED_INVESTIGATING",
    "ROLLBACK_RECOMMENDED",
    "CORRECTIVE_RELEASE_ACTIVE",
    "INITIAL_WINDOW_COMPLETE",
}
CRITICAL_SIGNAL_TYPES = {
    "TAG_MOVED_OR_RECREATED",
    "ASSET_INVENTORY_OR_BYTES_DRIFT",
    "MANIFEST_CHECKSUM_PROVENANCE_DRIFT",
    "SOURCE_OR_LICENSE_DRIFT",
    "NETWORK_ALIAS_OR_FALLBACK_DRIFT",
    "REPRODUCIBLE_CRASH",
    "ILLEGAL_MOVE_OR_STATE_CORRUPTION",
    "WRONG_RESULT_OR_PROTOCOL_REGRESSION",
    "MATERIAL_RULE_AUTHORITY_CHANGE",
    "SECURITY_OR_DISTRIBUTION_LICENSE_DEFECT",
}
NETWORK = {
    "alias": "Crazyhouse_v1.nnue",
    "bytes": 58_534_811,
    "sha256": "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43",
    "license": "CC0-1.0",
}

EXPECTATION_KEYS = {
    "schema",
    "project",
    "variant",
    "version",
    "repository",
    "releaseId",
    "tagName",
    "tagObject",
    "peeledCommit",
    "candidateTree",
    "sourceDateEpoch",
    "publicationUtc",
    "publishedAt",
    "draft",
    "prerelease",
    "assets",
    "nativeTargets",
    "network",
    "monitorOwner",
    "rollbackOwner",
    "monitorScheduleActiveThrough",
    "rollbackDecisionPathActive",
    "evidenceMode",
    "publicationReceipt",
}
CHECKPOINT_KEYS = {
    "schema",
    "project",
    "variant",
    "version",
    "checkpointId",
    "publicationUtc",
    "dueUtc",
    "capturedUtc",
    "state",
    "repository",
    "releaseId",
    "tagName",
    "tagObject",
    "peeledCommit",
    "draft",
    "prerelease",
    "publishedAt",
    "assets",
    "freshDownloadNamespace",
    "globalVerification",
    "nativePackageVerification",
    "runtimeVerification",
    "issueQuery",
    "criticalSignals",
    "monitorOwner",
    "rollbackOwner",
    "previousCheckpointSha256",
    "nextCheckpointId",
    "nextDueUtc",
}
ASSET_KEYS = {"name", "bytes", "sha256"}
NATIVE_TARGET_KEYS = {"asset", "executableBytes", "executableSha256"}
GLOBAL_KEYS = {
    "status",
    "verifier",
    "authenticatedFiles",
    "evidenceMode",
    "publicationReceipt",
    "candidateTree",
    "sourceDateEpoch",
    "manifest",
    "checksum",
    "correspondingSource",
    "freshBytes",
}
MANIFEST_KEYS = {"status", "schemaVersion", "canonical", "duplicateKeys"}
CHECKSUM_KEYS = {
    "status",
    "strictAscii",
    "rows",
    "duplicates",
    "malformedRows",
    "pathRows",
}
SOURCE_KEYS = {"status", "asset", "commit", "tree"}
FRESH_KEYS = {"status", "namespace", "reused"}
NATIVE_KEYS = {"status", "verifier", "packages"}
PACKAGE_KEYS = {
    "schema",
    "status",
    "asset",
    "bytes",
    "sha256",
    "members",
    "version",
    "target",
    "commit",
    "tree",
    "sourceDateEpoch",
    "executableBytes",
    "executableSha256",
    "networkPolicy",
    "releaseEvidenceNetwork",
    "networkBytes",
    "networkSha256",
    "packageInventorySha256",
    "sbomSha256",
    "networkAlias",
    "networkLicense",
    "sbomStatus",
    "licenseStatus",
}
RUNTIME_KEYS = {"schema", "status", "results"}
RUNTIME_RESULT_KEYS = {
    "target",
    "asset",
    "executableBytes",
    "executableSha256",
    "hostFeatureFloorSatisfied",
    "exactReleasedExecutable",
    "exactPackagedNetwork",
    "explicitCrazyhouseEvalFile",
    "legacyRouteMarker",
    "fallbackObserved",
    "uciCapabilityPassed",
    "optionInventoryPassed",
    "deterministicSmokePassed",
    "crashObserved",
    "illegalMoveObserved",
    "protocolRegressionObserved",
}
ISSUE_KEYS = {
    "schema",
    "status",
    "querySucceeded",
    "attempts",
    "isolatedTimeouts",
    "retrySucceeded",
    "categories",
}
ISSUE_CATEGORIES = {"crash", "illegalMove", "loader", "protocol", "gui", "ruleDrift"}
SIGNAL_KEYS = {"type", "status", "evidence", "publicMutationPerformed"}
PUBLICATION_RECEIPT_KEYS = {"path", "bytes", "sha256"}


class ChainVerificationError(RuntimeError):
    """The monitoring chain differs from the frozen release expectation."""


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ChainVerificationError("duplicate JSON key: " + key)
        value[key] = item
    return value


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except ChainVerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ChainVerificationError("invalid " + label) from error
    if not isinstance(value, dict):
        raise ChainVerificationError(label + " must be an object")
    if canonical_json(value) != payload:
        raise ChainVerificationError(label + " is not canonical JSON")
    return value


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ChainVerificationError(label + " keys differ")
    return value


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_TEXT.fullmatch(value):
        raise ChainVerificationError(label + " must be an RFC3339 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as error:
        raise ChainVerificationError(label + " is not a real timestamp") from error


def _format_utc(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    if value.microsecond:
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _asset_names(version: str) -> list[str]:
    return sorted(
        [
            f"crazyhouse-stockfish-{version}-windows-x86-64.zip",
            f"crazyhouse-stockfish-{version}-windows-x86-64-avx2.zip",
            f"crazyhouse-stockfish-{version}-source.tar.xz",
            "crazyhouse-stockfish-release-manifest.json",
            "SHA256SUMS",
        ]
    )


def _validate_expectation(value: dict[str, Any]) -> dict[str, Any]:
    _exact(value, EXPECTATION_KEYS, "expectation")
    if (
        value["schema"] != "crazyhouse-release-monitor-expectation/v1"
        or value["project"] != "Crazyhouse-Stockfish"
        or value["variant"] != "crazyhouse"
    ):
        raise ChainVerificationError("expectation identity differs")
    version = value["version"]
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ChainVerificationError("expectation version differs")
    if value["tagName"] != "v" + version:
        raise ChainVerificationError("expectation tag differs")
    if not isinstance(value["repository"], str) or not REPOSITORY_URL.fullmatch(
        value["repository"]
    ):
        raise ChainVerificationError("expectation repository differs")
    if not isinstance(value["releaseId"], int) or isinstance(value["releaseId"], bool) or value["releaseId"] <= 0:
        raise ChainVerificationError("expectation release ID differs")
    for key in ("tagObject", "peeledCommit", "candidateTree"):
        if not isinstance(value[key], str) or not OBJECT_ID.fullmatch(value[key]):
            raise ChainVerificationError("expectation object ID differs: " + key)
    if not isinstance(value["sourceDateEpoch"], int) or isinstance(value["sourceDateEpoch"], bool) or value["sourceDateEpoch"] < 0:
        raise ChainVerificationError("expectation source epoch differs")
    _utc(value["publicationUtc"], "expectation publicationUtc")
    _utc(value["publishedAt"], "expectation publishedAt")
    if value["publishedAt"] != value["publicationUtc"]:
        raise ChainVerificationError("expectation publication timestamp differs")
    if value["draft"] is not False or value["prerelease"] is not False:
        raise ChainVerificationError("expectation is not stable")
    assets = value["assets"]
    if not isinstance(assets, list):
        raise ChainVerificationError("expectation assets differ")
    names: list[str] = []
    for asset in assets:
        _exact(asset, ASSET_KEYS, "expectation asset")
        if (
            not isinstance(asset["name"], str)
            or not isinstance(asset["bytes"], int)
            or isinstance(asset["bytes"], bool)
            or asset["bytes"] <= 0
            or not isinstance(asset["sha256"], str)
            or not DIGEST.fullmatch(asset["sha256"])
        ):
            raise ChainVerificationError("expectation asset identity differs")
        names.append(asset["name"])
    if names != _asset_names(version):
        raise ChainVerificationError("expectation asset inventory differs")
    targets = value["nativeTargets"]
    if not isinstance(targets, dict) or set(targets) != set(TARGETS):
        raise ChainVerificationError("expectation native targets differ")
    for target in TARGETS:
        item = _exact(targets[target], NATIVE_TARGET_KEYS, "native target")
        if item["asset"] != f"crazyhouse-stockfish-{version}-{target}.zip":
            raise ChainVerificationError("expectation native asset differs")
        if (
            not isinstance(item["executableBytes"], int)
            or isinstance(item["executableBytes"], bool)
            or item["executableBytes"] <= 0
            or not isinstance(item["executableSha256"], str)
            or not DIGEST.fullmatch(item["executableSha256"])
        ):
            raise ChainVerificationError("expectation executable identity differs")
    if value["network"] != NETWORK:
        raise ChainVerificationError("expectation network authority differs")
    if any(
        not isinstance(value[key], str) or not value[key].strip()
        for key in ("monitorOwner", "rollbackOwner")
    ):
        raise ChainVerificationError("expectation owner is missing")
    if value["monitorScheduleActiveThrough"] != "T+168h":
        raise ChainVerificationError("future monitor schedule is not active")
    if value["rollbackDecisionPathActive"] is not True:
        raise ChainVerificationError("rollback decision path is not active")
    if value["evidenceMode"] not in {"SYNTHETIC", "REAL"}:
        raise ChainVerificationError("expectation evidence mode differs")
    receipt = value["publicationReceipt"]
    if value["evidenceMode"] == "SYNTHETIC":
        if receipt is not None:
            raise ChainVerificationError(
                "synthetic expectation names a publication receipt"
            )
    else:
        receipt = _exact(receipt, PUBLICATION_RECEIPT_KEYS, "publication receipt")
        if (
            not isinstance(receipt["path"], str)
            or not receipt["path"]
            or not isinstance(receipt["bytes"], int)
            or isinstance(receipt["bytes"], bool)
            or receipt["bytes"] <= 0
            or not isinstance(receipt["sha256"], str)
            or not DIGEST.fullmatch(receipt["sha256"])
        ):
            raise ChainVerificationError("publication receipt pin differs")
    return value


def _authenticate_publication_receipt(
    expectation: dict[str, Any], expectation_path: Path
) -> None:
    if expectation["evidenceMode"] == "SYNTHETIC":
        return
    receipt = expectation["publicationReceipt"]
    if not isinstance(receipt, dict):
        raise ChainVerificationError(
            "real expectation omits its publication receipt"
        )
    receipt_path = Path(receipt["path"])
    if not receipt_path.is_absolute():
        receipt_path = expectation_path.resolve().parent / receipt_path
    try:
        metadata = receipt_path.lstat()
    except OSError as error:
        raise ChainVerificationError("publication receipt is missing") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or receipt_path.is_symlink()
        or metadata.st_nlink != 1
        or metadata.st_size != receipt["bytes"]
        or sha256_file(receipt_path) != receipt["sha256"]
    ):
        raise ChainVerificationError("publication receipt authentication failed")


def _open_signals(value: object) -> set[str]:
    if not isinstance(value, list):
        raise ChainVerificationError("criticalSignals must be a list")
    seen: set[str] = set()
    opened: set[str] = set()
    for item in value:
        signal = _exact(item, SIGNAL_KEYS, "critical signal")
        signal_type = signal["type"]
        if signal_type not in CRITICAL_SIGNAL_TYPES or signal_type in seen:
            raise ChainVerificationError("critical signal type differs or repeats")
        seen.add(signal_type)
        if signal["status"] not in {"OPEN", "CLOSED"}:
            raise ChainVerificationError("critical signal status differs")
        if not isinstance(signal["evidence"], str) or not signal["evidence"].strip():
            raise ChainVerificationError("critical signal evidence is missing")
        if signal["publicMutationPerformed"] is not False:
            raise ChainVerificationError("forbidden public mutation was recorded")
        if signal["status"] == "OPEN":
            opened.add(signal_type)
    return opened


def _runtime_defects(value: object, expectation: dict[str, Any]) -> set[str]:
    runtime = _exact(value, RUNTIME_KEYS, "runtimeVerification")
    if runtime["schema"] != "crazyhouse-release-runtime-verification/v1":
        raise ChainVerificationError("runtime schema differs")
    if runtime["status"] not in {"PASS", "FAIL"}:
        raise ChainVerificationError("runtime status differs")
    results = runtime["results"]
    if not isinstance(results, list) or len(results) != len(TARGETS):
        raise ChainVerificationError("runtime result count differs")
    defects: set[str] = set()
    observed: list[str] = []
    for item in results:
        result = _exact(item, RUNTIME_RESULT_KEYS, "runtime result")
        target = result["target"]
        if target not in TARGETS:
            raise ChainVerificationError("runtime target differs")
        observed.append(target)
        frozen = expectation["nativeTargets"][target]
        if (
            result["asset"] != frozen["asset"]
            or result["executableBytes"] != frozen["executableBytes"]
            or result["executableSha256"] != frozen["executableSha256"]
        ):
            raise ChainVerificationError("runtime executable identity differs")
        boolean_keys = RUNTIME_RESULT_KEYS - {
            "target",
            "asset",
            "executableBytes",
            "executableSha256",
        }
        if any(not isinstance(result[key], bool) for key in boolean_keys):
            raise ChainVerificationError("runtime flag type differs")
        if not result["hostFeatureFloorSatisfied"]:
            defects.add("MISSING_HOST_FEATURE_FLOOR")
        if not result["exactReleasedExecutable"]:
            defects.add("ASSET_INVENTORY_OR_BYTES_DRIFT")
        if (
            not result["exactPackagedNetwork"]
            or not result["explicitCrazyhouseEvalFile"]
            or not result["legacyRouteMarker"]
            or result["fallbackObserved"]
        ):
            defects.add("NETWORK_ALIAS_OR_FALLBACK_DRIFT")
        if (
            not result["uciCapabilityPassed"]
            or not result["optionInventoryPassed"]
            or result["protocolRegressionObserved"]
        ):
            defects.add("WRONG_RESULT_OR_PROTOCOL_REGRESSION")
        if not result["deterministicSmokePassed"] or result["crashObserved"]:
            defects.add("REPRODUCIBLE_CRASH")
        if result["illegalMoveObserved"]:
            defects.add("ILLEGAL_MOVE_OR_STATE_CORRUPTION")
    if observed != list(TARGETS):
        raise ChainVerificationError("runtime target order differs")
    if (runtime["status"] == "PASS") != (not defects):
        raise ChainVerificationError("runtime status contradicts results")
    return defects


def _issue_contract(value: object) -> tuple[set[str], bool, bool]:
    issue = _exact(value, ISSUE_KEYS, "issueQuery")
    if issue["schema"] != "crazyhouse-release-issue-query/v1":
        raise ChainVerificationError("issue schema differs")
    if issue["status"] not in {"PASS", "FAIL"}:
        raise ChainVerificationError("issue status differs")
    if not isinstance(issue["querySucceeded"], bool) or not isinstance(
        issue["retrySucceeded"], bool
    ):
        raise ChainVerificationError("issue query flags differ")
    for key in ("attempts", "isolatedTimeouts"):
        if not isinstance(issue[key], int) or isinstance(issue[key], bool) or issue[key] < 0:
            raise ChainVerificationError("issue counters differ")
    if issue["attempts"] < 1 or issue["isolatedTimeouts"] > issue["attempts"]:
        raise ChainVerificationError("issue attempt accounting differs")
    if (issue["status"] == "PASS") != issue["querySucceeded"]:
        raise ChainVerificationError("issue status contradicts query result")
    if issue["isolatedTimeouts"]:
        if (
            not issue["querySucceeded"]
            or not issue["retrySucceeded"]
            or issue["attempts"] <= issue["isolatedTimeouts"]
        ):
            raise ChainVerificationError("isolated timeout lacks retry evidence")
    elif issue["retrySucceeded"]:
        raise ChainVerificationError("issue retry lacks a timeout")
    categories = issue["categories"]
    if not isinstance(categories, dict) or set(categories) != ISSUE_CATEGORIES:
        raise ChainVerificationError("issue categories differ")
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in categories.values()
    ):
        raise ChainVerificationError("issue category count differs")
    mapping = {
        "crash": "REPRODUCIBLE_CRASH",
        "illegalMove": "ILLEGAL_MOVE_OR_STATE_CORRUPTION",
        "loader": "NETWORK_ALIAS_OR_FALLBACK_DRIFT",
        "protocol": "WRONG_RESULT_OR_PROTOCOL_REGRESSION",
        "gui": "WRONG_RESULT_OR_PROTOCOL_REGRESSION",
        "ruleDrift": "MATERIAL_RULE_AUTHORITY_CHANGE",
    }
    signals = {mapping[key] for key, count in categories.items() if count}
    return signals, bool(issue["isolatedTimeouts"]), not issue["querySucceeded"]


def _verify_global(value: object, checkpoint: dict[str, Any], expectation: dict[str, Any]) -> None:
    global_result = _exact(value, GLOBAL_KEYS, "globalVerification")
    if (
        global_result["status"] != "PASS"
        or global_result["verifier"] != "verify_crazyhouse_release_download.py"
        or global_result["evidenceMode"] != expectation["evidenceMode"]
        or global_result["publicationReceipt"] != expectation["publicationReceipt"]
        or global_result["authenticatedFiles"] != len(expectation["assets"])
        or global_result["candidateTree"] != expectation["candidateTree"]
        or global_result["sourceDateEpoch"] != expectation["sourceDateEpoch"]
    ):
        raise ChainVerificationError("global verification identity differs")
    manifest = _exact(global_result["manifest"], MANIFEST_KEYS, "manifest verification")
    if manifest != {
        "status": "PASS",
        "schemaVersion": 1,
        "canonical": True,
        "duplicateKeys": False,
    }:
        raise ChainVerificationError("manifest verification differs")
    checksum = _exact(global_result["checksum"], CHECKSUM_KEYS, "checksum verification")
    if checksum != {
        "status": "PASS",
        "strictAscii": True,
        "rows": len(expectation["assets"]) - 1,
        "duplicates": 0,
        "malformedRows": 0,
        "pathRows": 0,
    }:
        raise ChainVerificationError("checksum verification differs")
    source = _exact(
        global_result["correspondingSource"], SOURCE_KEYS, "source verification"
    )
    if source != {
        "status": "PASS",
        "asset": f"crazyhouse-stockfish-{expectation['version']}-source.tar.xz",
        "commit": expectation["peeledCommit"],
        "tree": expectation["candidateTree"],
    }:
        raise ChainVerificationError("corresponding-source verification differs")
    fresh = _exact(global_result["freshBytes"], FRESH_KEYS, "fresh download verification")
    if fresh != {
        "status": "PASS",
        "namespace": checkpoint["freshDownloadNamespace"],
        "reused": False,
    }:
        raise ChainVerificationError("fresh download verification differs")


def _verify_native(value: object, expectation: dict[str, Any]) -> None:
    native = _exact(value, NATIVE_KEYS, "nativePackageVerification")
    if native["status"] != "PASS" or native["verifier"] != "verify_crazyhouse_native_package.py":
        raise ChainVerificationError("native package verification status differs")
    packages = native["packages"]
    if not isinstance(packages, list) or len(packages) != len(TARGETS):
        raise ChainVerificationError("native package result count differs")
    assets = {item["name"]: item for item in expectation["assets"]}
    for target, package_value in zip(TARGETS, packages):
        package = _exact(package_value, PACKAGE_KEYS, "native package result")
        frozen = expectation["nativeTargets"][target]
        asset = assets[frozen["asset"]]
        required = {
            "schema": "crazyhouse-native-package-verification/v1",
            "status": "PASS_NATIVE_PACKAGE_VERIFICATION",
            "asset": frozen["asset"],
            "bytes": asset["bytes"],
            "sha256": asset["sha256"],
            "version": expectation["version"],
            "target": target,
            "commit": expectation["peeledCommit"],
            "tree": expectation["candidateTree"],
            "sourceDateEpoch": expectation["sourceDateEpoch"],
            "executableBytes": frozen["executableBytes"],
            "executableSha256": frozen["executableSha256"],
            "networkPolicy": "crazyhouse-native-network-production/v1",
            "releaseEvidenceNetwork": True,
            "networkBytes": NETWORK["bytes"],
            "networkSha256": NETWORK["sha256"],
            "networkAlias": NETWORK["alias"],
            "networkLicense": NETWORK["license"],
            "sbomStatus": "PASS",
            "licenseStatus": "PASS",
        }
        if any(package[key] != expected for key, expected in required.items()):
            raise ChainVerificationError("native package result identity differs")
        if not isinstance(package["members"], int) or package["members"] <= 0:
            raise ChainVerificationError("native package member count differs")
        if not DIGEST.fullmatch(str(package["packageInventorySha256"])) or not DIGEST.fullmatch(
            str(package["sbomSha256"])
        ):
            raise ChainVerificationError("native package inventory digest differs")


def _state_contract(
    checkpoint: dict[str, Any],
    due: datetime,
    lateness: int,
    runtime_defects: set[str],
    issue_signals: set[str],
    isolated_timeout: bool,
    query_failed: bool,
    opened: set[str],
) -> None:
    state = checkpoint["state"]
    captured = _utc(checkpoint["capturedUtc"], "capturedUtc")
    late = captured > due + timedelta(seconds=lateness)
    critical_defects = {item for item in runtime_defects if item != "MISSING_HOST_FEATURE_FLOOR"} | issue_signals
    if not critical_defects.issubset(opened):
        raise ChainVerificationError("observed critical defect is suppressed")
    if opened and state not in {"ROLLBACK_RECOMMENDED", "CORRECTIVE_RELEASE_ACTIVE"}:
        raise ChainVerificationError("open critical signal lacks rollback state")
    if late and state != "DEGRADED_INVESTIGATING":
        raise ChainVerificationError("late checkpoint is not degraded")
    if state == "HEALTHY":
        if runtime_defects or isolated_timeout or query_failed or opened or late:
            raise ChainVerificationError("healthy checkpoint contains a defect")
    elif state == "DEGRADED_INVESTIGATING":
        if opened or not (late or isolated_timeout or query_failed or runtime_defects):
            raise ChainVerificationError("degraded state does not match evidence")
    elif state in {"ROLLBACK_RECOMMENDED", "CORRECTIVE_RELEASE_ACTIVE"}:
        if not opened:
            raise ChainVerificationError("rollback state lacks an open signal")
    elif state == "INITIAL_WINDOW_COMPLETE":
        if (
            checkpoint["checkpointId"] != "T+168h"
            or runtime_defects
            or query_failed
            or opened
            or late
        ):
            raise ChainVerificationError("initial window completion is invalid")


def verify_chain(expectation_path: Path, checkpoints: Sequence[Path]) -> dict[str, Any]:
    expectation = _validate_expectation(_load(expectation_path, "expectation"))
    _authenticate_publication_receipt(expectation, expectation_path)
    if not checkpoints:
        raise ChainVerificationError("monitor chain is empty")
    if len(checkpoints) > len(SCHEDULE):
        raise ChainVerificationError("monitor chain exceeds the frozen schedule")
    publication = _utc(expectation["publicationUtc"], "publicationUtc")
    previous_value: Optional[dict[str, Any]] = None
    previous_path: Optional[Path] = None
    namespaces: set[str] = set()
    current_open: set[str] = set()
    first_three_healthy = True

    static = {
        "project": "Crazyhouse-Stockfish",
        "variant": "crazyhouse",
        "version": expectation["version"],
        "publicationUtc": expectation["publicationUtc"],
        "repository": expectation["repository"],
        "releaseId": expectation["releaseId"],
        "tagName": expectation["tagName"],
        "tagObject": expectation["tagObject"],
        "peeledCommit": expectation["peeledCommit"],
        "draft": False,
        "prerelease": False,
        "publishedAt": expectation["publishedAt"],
        "assets": expectation["assets"],
        "monitorOwner": expectation["monitorOwner"],
        "rollbackOwner": expectation["rollbackOwner"],
    }

    for index, path in enumerate(checkpoints):
        checkpoint = _load(path, "checkpoint")
        _exact(checkpoint, CHECKPOINT_KEYS, "checkpoint")
        if checkpoint["schema"] != "crazyhouse-release-monitor-checkpoint/v1":
            raise ChainVerificationError("checkpoint schema differs")
        for key, expected in static.items():
            if checkpoint[key] != expected:
                raise ChainVerificationError("checkpoint differs from expectation: " + key)
        expected_id, offset, lateness = SCHEDULE[index]
        if checkpoint["checkpointId"] != expected_id:
            raise ChainVerificationError("checkpoint ID is missing, duplicate or out of order")
        due = publication + timedelta(seconds=offset)
        if checkpoint["dueUtc"] != _format_utc(due):
            raise ChainVerificationError("checkpoint due time differs")
        next_id = SCHEDULE[index + 1][0] if index + 1 < len(SCHEDULE) else None
        next_due = (
            _format_utc(publication + timedelta(seconds=SCHEDULE[index + 1][1]))
            if next_id is not None
            else None
        )
        if checkpoint["nextCheckpointId"] != next_id or checkpoint["nextDueUtc"] != next_due:
            raise ChainVerificationError("checkpoint next schedule differs")
        captured = _utc(checkpoint["capturedUtc"], "capturedUtc")
        if previous_value is None:
            if checkpoint["previousCheckpointSha256"] is not None:
                raise ChainVerificationError("T0 predecessor digest is not null")
        else:
            if previous_path is None:
                raise ChainVerificationError("internal predecessor path is missing")
            if checkpoint["previousCheckpointSha256"] != sha256_file(previous_path):
                raise ChainVerificationError("previous checkpoint digest mismatch")
            if _utc(previous_value["capturedUtc"], "previous capturedUtc") > captured:
                raise ChainVerificationError("checkpoint capture time regressed")
        namespace = checkpoint["freshDownloadNamespace"]
        if (
            not isinstance(namespace, str)
            or not Path(namespace).name.startswith(
                f"crazyhouse-monitor-{expectation['version']}-"
            )
            or namespace in namespaces
        ):
            raise ChainVerificationError("fresh download namespace differs or repeats")
        namespaces.add(namespace)

        _verify_global(checkpoint["globalVerification"], checkpoint, expectation)
        _verify_native(checkpoint["nativePackageVerification"], expectation)
        opened = _open_signals(checkpoint["criticalSignals"])
        current_signal_status = {
            item["type"]: item["status"] for item in checkpoint["criticalSignals"]
        }
        for signal_type in current_open:
            if signal_type not in current_signal_status:
                raise ChainVerificationError(
                    "open critical signal disappeared without closure"
                )
        for signal_type, status in current_signal_status.items():
            if status == "CLOSED" and signal_type not in current_open:
                raise ChainVerificationError(
                    "critical signal closed without an open predecessor"
                )
        runtime_defects = _runtime_defects(
            checkpoint["runtimeVerification"], expectation
        )
        issue_signals, isolated_timeout, query_failed = _issue_contract(
            checkpoint["issueQuery"]
        )
        if checkpoint["state"] not in STATES:
            raise ChainVerificationError("checkpoint state differs")
        _state_contract(
            checkpoint,
            due,
            lateness,
            runtime_defects,
            issue_signals,
            isolated_timeout,
            query_failed,
            opened,
        )
        current_open = opened
        if index < 3 and checkpoint["state"] != "HEALTHY":
            first_three_healthy = False
        previous_value = checkpoint
        previous_path = path

    if previous_value is None or previous_path is None:
        raise ChainVerificationError("monitor chain ended without a checkpoint")
    required_first_three_present = len(checkpoints) >= 3
    eligible = (
        required_first_three_present
        and first_three_healthy
        and not current_open
        and expectation["monitorScheduleActiveThrough"] == "T+168h"
        and expectation["rollbackDecisionPathActive"] is True
    )
    return {
        "schema": "crazyhouse-release-monitor-chain-verification/v1",
        "status": "PASS_MONITOR_CHAIN_VERIFICATION",
        "checkpoints": len(checkpoints),
        "latestCheckpointId": previous_value["checkpointId"],
        "latestCheckpointSha256": sha256_file(previous_path),
        "latestState": previous_value["state"],
        "openCriticalSignals": sorted(current_open),
        "futureScheduleActiveThrough": expectation["monitorScheduleActiveThrough"],
        "monitorOwner": expectation["monitorOwner"],
        "rollbackOwner": expectation["rollbackOwner"],
        "releasedMonitoredEligible": eligible,
        "realPublicationAuthenticated": expectation["evidenceMode"] == "REAL",
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expectation", type=Path, required=True)
    parser.add_argument("--chain-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    chain_dir = args.chain_dir.resolve(strict=True)
    if not chain_dir.is_dir():
        raise ChainVerificationError("chain path is not a directory")
    checkpoints = sorted(chain_dir.glob("*.json"), key=lambda item: item.name)
    result = verify_chain(args.expectation, checkpoints)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
