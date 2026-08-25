#!/usr/bin/env python3
"""Create one fail-closed Crazyhouse post-release monitoring checkpoint."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Optional, Sequence


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
SCHEDULE_INDEX = {item[0]: index for index, item in enumerate(SCHEDULE)}
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
OBSERVATION_KEYS = {
    "schema",
    "checkpointId",
    "capturedUtc",
    "state",
    "runtimeVerification",
    "issueQuery",
    "criticalSignals",
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
SIGNAL_KEYS = {"type", "status", "evidence", "publicMutationPerformed"}
PUBLICATION_RECEIPT_KEYS = {"path", "bytes", "sha256"}
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


class MonitorError(RuntimeError):
    """The checkpoint cannot be emitted without weakening the frozen contract."""


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
            raise MonitorError("duplicate JSON key: " + key)
        value[key] = item
    return value


def load_canonical_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except MonitorError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MonitorError("invalid " + label) from error
    if not isinstance(value, dict):
        raise MonitorError(label + " must be a JSON object")
    if canonical_json(value) != payload:
        raise MonitorError(label + " is not canonical JSON")
    return value


def parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_TEXT.fullmatch(value):
        raise MonitorError(label + " must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise MonitorError(label + " is not a real timestamp") from error
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    if value.microsecond:
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _expected_asset_names(version: str) -> list[str]:
    return sorted(
        [
            f"crazyhouse-stockfish-{version}-windows-x86-64.zip",
            f"crazyhouse-stockfish-{version}-windows-x86-64-avx2.zip",
            f"crazyhouse-stockfish-{version}-source.tar.xz",
            "crazyhouse-stockfish-release-manifest.json",
            "SHA256SUMS",
        ]
    )


def _require_exact_keys(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise MonitorError(label + " keys differ")
    return value


def validate_expectation(value: dict[str, Any]) -> dict[str, Any]:
    _require_exact_keys(value, EXPECTATION_KEYS, "expectation")
    if (
        value["schema"] != "crazyhouse-release-monitor-expectation/v1"
        or value["project"] != "Crazyhouse-Stockfish"
        or value["variant"] != "crazyhouse"
    ):
        raise MonitorError("expectation identity differs")
    version = value["version"]
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise MonitorError("expectation version must be X.Y.Z")
    if value["tagName"] != "v" + version:
        raise MonitorError("expectation tag does not match version")
    if not isinstance(value["repository"], str) or not REPOSITORY_URL.fullmatch(
        value["repository"]
    ):
        raise MonitorError("expectation repository must be a public GitHub URL")
    if not isinstance(value["releaseId"], int) or isinstance(value["releaseId"], bool) or value["releaseId"] <= 0:
        raise MonitorError("expectation releaseId must be a positive integer")
    for key in ("tagObject", "peeledCommit", "candidateTree"):
        if not isinstance(value[key], str) or not OBJECT_ID.fullmatch(value[key]):
            raise MonitorError("expectation " + key + " must be a full object ID")
    if not isinstance(value["sourceDateEpoch"], int) or isinstance(value["sourceDateEpoch"], bool) or value["sourceDateEpoch"] < 0:
        raise MonitorError("expectation sourceDateEpoch must be non-negative")
    parse_utc(value["publicationUtc"], "expectation publicationUtc")
    parse_utc(value["publishedAt"], "expectation publishedAt")
    if value["publishedAt"] != value["publicationUtc"]:
        raise MonitorError("expectation publication timestamps differ")
    if value["draft"] is not False or value["prerelease"] is not False:
        raise MonitorError("expectation is not a stable release")
    assets = value["assets"]
    if not isinstance(assets, list):
        raise MonitorError("expectation assets must be a list")
    asset_names: list[str] = []
    for index, asset in enumerate(assets):
        _require_exact_keys(asset, ASSET_KEYS, f"expectation asset {index}")
        if (
            not isinstance(asset["name"], str)
            or not isinstance(asset["bytes"], int)
            or isinstance(asset["bytes"], bool)
            or asset["bytes"] <= 0
            or not isinstance(asset["sha256"], str)
            or not DIGEST.fullmatch(asset["sha256"])
        ):
            raise MonitorError("invalid expectation asset identity")
        asset_names.append(asset["name"])
    if asset_names != _expected_asset_names(version):
        raise MonitorError("expectation asset inventory differs from frozen set")
    native_targets = value["nativeTargets"]
    if not isinstance(native_targets, dict) or set(native_targets) != set(TARGETS):
        raise MonitorError("expectation native target set differs")
    for target in TARGETS:
        item = _require_exact_keys(
            native_targets[target], NATIVE_TARGET_KEYS, "expectation native target"
        )
        if item["asset"] != f"crazyhouse-stockfish-{version}-{target}.zip":
            raise MonitorError("expectation native asset name differs")
        if (
            not isinstance(item["executableBytes"], int)
            or isinstance(item["executableBytes"], bool)
            or item["executableBytes"] <= 0
            or not isinstance(item["executableSha256"], str)
            or not DIGEST.fullmatch(item["executableSha256"])
        ):
            raise MonitorError("expectation executable identity differs")
    if value["network"] != NETWORK:
        raise MonitorError("expectation network authority differs")
    for key in ("monitorOwner", "rollbackOwner"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise MonitorError("expectation " + key + " is missing")
    if value["monitorScheduleActiveThrough"] != "T+168h":
        raise MonitorError("monitor schedule is not active through T+168h")
    if value["rollbackDecisionPathActive"] is not True:
        raise MonitorError("rollback decision path is not active")
    if value["evidenceMode"] not in {"SYNTHETIC", "REAL"}:
        raise MonitorError("expectation evidence mode differs")
    receipt = value["publicationReceipt"]
    if value["evidenceMode"] == "SYNTHETIC":
        if receipt is not None:
            raise MonitorError("synthetic expectation names a publication receipt")
    else:
        receipt = _require_exact_keys(
            receipt, PUBLICATION_RECEIPT_KEYS, "publication receipt"
        )
        if (
            not isinstance(receipt["path"], str)
            or not receipt["path"]
            or not isinstance(receipt["bytes"], int)
            or isinstance(receipt["bytes"], bool)
            or receipt["bytes"] <= 0
            or not isinstance(receipt["sha256"], str)
            or not DIGEST.fullmatch(receipt["sha256"])
        ):
            raise MonitorError("publication receipt pin differs")
    return value


def _authenticate_publication_receipt(
    expectation: dict[str, Any], expectation_path: Path
) -> None:
    if expectation["evidenceMode"] == "SYNTHETIC":
        return
    receipt = expectation["publicationReceipt"]
    if not isinstance(receipt, dict):
        raise MonitorError("real expectation omits its publication receipt")
    receipt_path = Path(receipt["path"])
    if not receipt_path.is_absolute():
        receipt_path = expectation_path.resolve().parent / receipt_path
    try:
        metadata = receipt_path.lstat()
    except OSError as error:
        raise MonitorError("publication receipt is missing") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or receipt_path.is_symlink()
        or metadata.st_nlink != 1
        or metadata.st_size != receipt["bytes"]
        or sha256_file(receipt_path) != receipt["sha256"]
    ):
        raise MonitorError("publication receipt authentication failed")


def _inventory(root: Path) -> list[dict[str, object]]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise MonitorError("release root is not a directory")
    values: list[dict[str, object]] = []
    seen: set[str] = set()
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        metadata = path.lstat()
        folded = path.name.casefold()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_nlink != 1
            or folded in seen
        ):
            raise MonitorError("release root contains a non-unique regular file")
        seen.add(folded)
        values.append(
            {"name": path.name, "bytes": metadata.st_size, "sha256": sha256_file(path)}
        )
    return values


def _validate_signals(value: object) -> set[str]:
    if not isinstance(value, list):
        raise MonitorError("criticalSignals must be a list")
    seen: set[str] = set()
    open_signals: set[str] = set()
    for item in value:
        signal = _require_exact_keys(item, SIGNAL_KEYS, "critical signal")
        signal_type = signal["type"]
        if signal_type not in CRITICAL_SIGNAL_TYPES or signal_type in seen:
            raise MonitorError("critical signal type differs or repeats")
        seen.add(signal_type)
        if signal["status"] not in {"OPEN", "CLOSED"}:
            raise MonitorError("critical signal status differs")
        if not isinstance(signal["evidence"], str) or not signal["evidence"].strip():
            raise MonitorError("critical signal evidence is missing")
        if signal["publicMutationPerformed"] is not False:
            raise MonitorError("monitor performed a forbidden public mutation")
        if signal["status"] == "OPEN":
            open_signals.add(signal_type)
    return open_signals


def _validate_runtime(value: object, expectation: dict[str, Any]) -> set[str]:
    runtime = _require_exact_keys(value, RUNTIME_KEYS, "runtimeVerification")
    if runtime["schema"] != "crazyhouse-release-runtime-verification/v1":
        raise MonitorError("runtime verification schema differs")
    if runtime["status"] not in {"PASS", "FAIL"}:
        raise MonitorError("runtime verification status differs")
    results = runtime["results"]
    if not isinstance(results, list) or len(results) != len(TARGETS):
        raise MonitorError("runtime target result count differs")
    defects: set[str] = set()
    observed_targets: list[str] = []
    for item in results:
        result = _require_exact_keys(item, RUNTIME_RESULT_KEYS, "runtime result")
        target = result["target"]
        if target not in TARGETS:
            raise MonitorError("runtime target differs")
        observed_targets.append(target)
        frozen = expectation["nativeTargets"][target]
        if (
            result["asset"] != frozen["asset"]
            or result["executableBytes"] != frozen["executableBytes"]
            or result["executableSha256"] != frozen["executableSha256"]
        ):
            raise MonitorError("runtime executable identity differs")
        boolean_keys = RUNTIME_RESULT_KEYS - {
            "target",
            "asset",
            "executableBytes",
            "executableSha256",
        }
        if any(not isinstance(result[key], bool) for key in boolean_keys):
            raise MonitorError("runtime result flags must be booleans")
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
    if observed_targets != list(TARGETS):
        raise MonitorError("runtime targets are not in frozen order")
    if (runtime["status"] == "PASS") != (not defects):
        raise MonitorError("runtime status does not match observed defects")
    return defects


def _validate_issue_query(value: object) -> tuple[set[str], bool, bool]:
    issue = _require_exact_keys(value, ISSUE_KEYS, "issueQuery")
    if issue["schema"] != "crazyhouse-release-issue-query/v1":
        raise MonitorError("issue query schema differs")
    if issue["status"] not in {"PASS", "FAIL"}:
        raise MonitorError("issue query status differs")
    if not isinstance(issue["querySucceeded"], bool) or not isinstance(
        issue["retrySucceeded"], bool
    ):
        raise MonitorError("issue query result flags differ")
    for key in ("attempts", "isolatedTimeouts"):
        if not isinstance(issue[key], int) or isinstance(issue[key], bool) or issue[key] < 0:
            raise MonitorError("issue query counters differ")
    if issue["attempts"] < 1 or issue["isolatedTimeouts"] > issue["attempts"]:
        raise MonitorError("issue query attempt accounting differs")
    if (issue["status"] == "PASS") != issue["querySucceeded"]:
        raise MonitorError("issue query status contradicts querySucceeded")
    if issue["isolatedTimeouts"]:
        if (
            not issue["querySucceeded"]
            or not issue["retrySucceeded"]
            or issue["attempts"] <= issue["isolatedTimeouts"]
        ):
            raise MonitorError("isolated timeout lacks successful retry evidence")
    elif issue["retrySucceeded"]:
        raise MonitorError("issue query claims a retry without a timeout")
    categories = issue["categories"]
    if not isinstance(categories, dict) or set(categories) != ISSUE_CATEGORIES:
        raise MonitorError("issue query categories differ")
    for count in categories.values():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise MonitorError("issue query category counts differ")
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


def _state_contract(
    checkpoint_id: str,
    state: str,
    captured: datetime,
    due: datetime,
    lateness_seconds: int,
    runtime_defects: set[str],
    issue_signals: set[str],
    isolated_timeout: bool,
    query_failed: bool,
    open_signals: set[str],
) -> None:
    late = captured > due + timedelta(seconds=lateness_seconds)
    signal_defects = {item for item in runtime_defects if item != "MISSING_HOST_FEATURE_FLOOR"} | issue_signals
    if not signal_defects.issubset(open_signals):
        raise MonitorError("observed critical defect is not represented by an open signal")
    if open_signals and state not in {"ROLLBACK_RECOMMENDED", "CORRECTIVE_RELEASE_ACTIVE"}:
        raise MonitorError("open critical signal lacks rollback state")
    if late and state != "DEGRADED_INVESTIGATING":
        raise MonitorError("late checkpoint is not degraded")
    if state == "HEALTHY":
        if runtime_defects or isolated_timeout or query_failed or open_signals or late:
            raise MonitorError("healthy checkpoint contains a defect")
    elif state == "DEGRADED_INVESTIGATING":
        if open_signals:
            raise MonitorError("critical signal cannot remain only degraded")
        if not (late or isolated_timeout or query_failed or runtime_defects):
            raise MonitorError("degraded checkpoint has no degradation evidence")
    elif state in {"ROLLBACK_RECOMMENDED", "CORRECTIVE_RELEASE_ACTIVE"}:
        if not open_signals:
            raise MonitorError("rollback state lacks an open critical signal")
    elif state == "INITIAL_WINDOW_COMPLETE":
        if checkpoint_id != "T+168h" or runtime_defects or query_failed or open_signals or late:
            raise MonitorError("initial window cannot be closed")


def _production_release_verifier(
    local: Path,
    downloaded: Path,
    version: str,
    commit: str,
    tree: str,
    source_date_epoch: int,
) -> int:
    from verify_crazyhouse_release_download import verify_release_download

    return verify_release_download(
        local, downloaded, version, commit, tree, source_date_epoch
    )


def _production_native_verifier(*args: object, **kwargs: object) -> dict[str, object]:
    from verify_crazyhouse_native_package import verify_native_package

    return verify_native_package(*args, **kwargs)


def _validate_native_result(
    result: object,
    archive: Path,
    expectation: dict[str, Any],
    target: str,
) -> dict[str, object]:
    if not isinstance(result, dict):
        raise MonitorError("native package verifier returned a non-object")
    frozen = expectation["nativeTargets"][target]
    required = {
        "schema": "crazyhouse-native-package-verification/v1",
        "status": "PASS_NATIVE_PACKAGE_VERIFICATION",
        "asset": frozen["asset"],
        "bytes": archive.stat().st_size,
        "sha256": sha256_file(archive),
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
    }
    for key, expected in required.items():
        if result.get(key) != expected:
            raise MonitorError("native package verifier result differs: " + key)
    for key in ("members", "packageInventorySha256", "sbomSha256"):
        if key not in result:
            raise MonitorError("native package verifier result omits " + key)
    if not isinstance(result["members"], int) or result["members"] <= 0:
        raise MonitorError("native package member count differs")
    if not DIGEST.fullmatch(str(result["packageInventorySha256"])) or not DIGEST.fullmatch(
        str(result["sbomSha256"])
    ):
        raise MonitorError("native package inventory or SBOM digest differs")
    wrapped = dict(result)
    wrapped.update(
        {
            "networkAlias": NETWORK["alias"],
            "networkLicense": NETWORK["license"],
            "sbomStatus": "PASS",
            "licenseStatus": "PASS",
        }
    )
    return wrapped


def create_checkpoint(
    expectation_path: Path,
    observation_path: Path,
    local_draft: Path,
    fresh_download: Path,
    output: Path,
    previous: Optional[Path] = None,
    *,
    release_verifier: Optional[Callable[..., int]] = None,
    native_verifier: Optional[Callable[..., dict[str, object]]] = None,
) -> dict[str, Any]:
    expectation = validate_expectation(
        load_canonical_json(expectation_path, "monitor expectation")
    )
    _authenticate_publication_receipt(expectation, expectation_path)
    observation = load_canonical_json(observation_path, "monitor observation")
    _require_exact_keys(observation, OBSERVATION_KEYS, "observation")
    if observation["schema"] != "crazyhouse-release-monitor-observation/v1":
        raise MonitorError("observation schema differs")
    checkpoint_id = observation["checkpointId"]
    if checkpoint_id not in SCHEDULE_INDEX:
        raise MonitorError("checkpoint ID is not frozen")
    state = observation["state"]
    if state not in STATES:
        raise MonitorError("monitor state is not frozen")
    captured = parse_utc(observation["capturedUtc"], "capturedUtc")
    publication = parse_utc(expectation["publicationUtc"], "publicationUtc")
    index = SCHEDULE_INDEX[checkpoint_id]
    _, offset_seconds, lateness_seconds = SCHEDULE[index]
    due = publication + timedelta(seconds=offset_seconds)

    previous_value: Optional[dict[str, Any]] = None
    previous_digest: Optional[str] = None
    if previous is None:
        if index != 0:
            raise MonitorError("non-T0 checkpoint requires a predecessor")
    else:
        if index == 0:
            raise MonitorError("T0 cannot have a predecessor")
        previous_value = load_canonical_json(previous, "previous checkpoint")
        _require_exact_keys(previous_value, CHECKPOINT_KEYS, "previous checkpoint")
        if previous_value["checkpointId"] != SCHEDULE[index - 1][0]:
            raise MonitorError("previous checkpoint is not the immediate predecessor")
        if previous_value["nextCheckpointId"] != checkpoint_id:
            raise MonitorError("previous checkpoint next ID differs")
        if previous_value["project"] != "Crazyhouse-Stockfish" or previous_value[
            "variant"
        ] != "crazyhouse":
            raise MonitorError("previous checkpoint project identity differs")
        for key in (
            "version",
            "repository",
            "releaseId",
            "tagName",
            "tagObject",
            "peeledCommit",
            "publicationUtc",
            "publishedAt",
            "assets",
            "monitorOwner",
            "rollbackOwner",
        ):
            expected_key = {
                "version": "version",
                "repository": "repository",
                "releaseId": "releaseId",
                "tagName": "tagName",
                "tagObject": "tagObject",
                "peeledCommit": "peeledCommit",
                "publicationUtc": "publicationUtc",
                "publishedAt": "publishedAt",
                "assets": "assets",
                "monitorOwner": "monitorOwner",
                "rollbackOwner": "rollbackOwner",
            }[key]
            if previous_value[key] != expectation[expected_key]:
                raise MonitorError("previous checkpoint differs from expectation: " + key)
        if parse_utc(previous_value["capturedUtc"], "previous capturedUtc") > captured:
            raise MonitorError("checkpoint capture time regressed")
        previous_digest = sha256_file(previous)

    local_draft = local_draft.resolve(strict=True)
    fresh_download = fresh_download.resolve(strict=True)
    if local_draft == fresh_download or local_draft in fresh_download.parents or fresh_download in local_draft.parents:
        raise MonitorError("local draft and fresh download namespaces overlap")
    expected_prefix = f"crazyhouse-monitor-{expectation['version']}-"
    if not fresh_download.name.startswith(expected_prefix):
        raise MonitorError("fresh download namespace prefix differs")
    if previous_value is not None and previous_value["freshDownloadNamespace"] == fresh_download.as_posix():
        raise MonitorError("fresh download namespace was reused")
    local_inventory = _inventory(local_draft)
    downloaded_inventory = _inventory(fresh_download)
    if local_inventory != expectation["assets"] or downloaded_inventory != expectation["assets"]:
        raise MonitorError("release asset inventory differs from expectation")

    release_verifier = release_verifier or _production_release_verifier
    native_verifier = native_verifier or _production_native_verifier
    authenticated_files = release_verifier(
        local_draft,
        fresh_download,
        expectation["version"],
        expectation["peeledCommit"],
        expectation["candidateTree"],
        expectation["sourceDateEpoch"],
    )
    if authenticated_files != len(expectation["assets"]):
        raise MonitorError("global verifier authenticated file count differs")

    package_results: list[dict[str, object]] = []
    for target in TARGETS:
        frozen = expectation["nativeTargets"][target]
        archive = fresh_download / frozen["asset"]
        raw_result = native_verifier(
            archive,
            expectation["version"],
            expectation["peeledCommit"],
            expectation["candidateTree"],
            expectation["sourceDateEpoch"],
            target,
            frozen["executableBytes"],
            frozen["executableSha256"],
        )
        package_results.append(
            _validate_native_result(raw_result, archive, expectation, target)
        )

    open_signals = _validate_signals(observation["criticalSignals"])
    current_signal_status = {
        item["type"]: item["status"] for item in observation["criticalSignals"]
    }
    if previous_value is not None:
        previous_open = _validate_signals(previous_value["criticalSignals"])
        for signal_type in previous_open:
            if signal_type not in current_signal_status:
                raise MonitorError("open critical signal disappeared without closure")
        for signal_type, status in current_signal_status.items():
            if status == "CLOSED" and signal_type not in previous_open:
                raise MonitorError("critical signal closed without an open predecessor")
    elif any(status == "CLOSED" for status in current_signal_status.values()):
        raise MonitorError("T0 closes a signal that was never open")
    runtime_defects = _validate_runtime(
        observation["runtimeVerification"], expectation
    )
    issue_signals, isolated_timeout, query_failed = _validate_issue_query(
        observation["issueQuery"]
    )
    _state_contract(
        checkpoint_id,
        state,
        captured,
        due,
        lateness_seconds,
        runtime_defects,
        issue_signals,
        isolated_timeout,
        query_failed,
        open_signals,
    )

    next_id = SCHEDULE[index + 1][0] if index + 1 < len(SCHEDULE) else None
    next_due = (
        format_utc(publication + timedelta(seconds=SCHEDULE[index + 1][1]))
        if next_id is not None
        else None
    )
    source_name = f"crazyhouse-stockfish-{expectation['version']}-source.tar.xz"
    checkpoint: dict[str, Any] = {
        "schema": "crazyhouse-release-monitor-checkpoint/v1",
        "project": "Crazyhouse-Stockfish",
        "variant": "crazyhouse",
        "version": expectation["version"],
        "checkpointId": checkpoint_id,
        "publicationUtc": expectation["publicationUtc"],
        "dueUtc": format_utc(due),
        "capturedUtc": observation["capturedUtc"],
        "state": state,
        "repository": expectation["repository"],
        "releaseId": expectation["releaseId"],
        "tagName": expectation["tagName"],
        "tagObject": expectation["tagObject"],
        "peeledCommit": expectation["peeledCommit"],
        "draft": False,
        "prerelease": False,
        "publishedAt": expectation["publishedAt"],
        "assets": expectation["assets"],
        "freshDownloadNamespace": fresh_download.as_posix(),
        "globalVerification": {
            "status": "PASS",
            "verifier": "verify_crazyhouse_release_download.py",
            "evidenceMode": expectation["evidenceMode"],
            "publicationReceipt": expectation["publicationReceipt"],
            "authenticatedFiles": authenticated_files,
            "candidateTree": expectation["candidateTree"],
            "sourceDateEpoch": expectation["sourceDateEpoch"],
            "manifest": {
                "status": "PASS",
                "schemaVersion": 1,
                "canonical": True,
                "duplicateKeys": False,
            },
            "checksum": {
                "status": "PASS",
                "strictAscii": True,
                "rows": len(expectation["assets"]) - 1,
                "duplicates": 0,
                "malformedRows": 0,
                "pathRows": 0,
            },
            "correspondingSource": {
                "status": "PASS",
                "asset": source_name,
                "commit": expectation["peeledCommit"],
                "tree": expectation["candidateTree"],
            },
            "freshBytes": {
                "status": "PASS",
                "namespace": fresh_download.as_posix(),
                "reused": False,
            },
        },
        "nativePackageVerification": {
            "status": "PASS",
            "verifier": "verify_crazyhouse_native_package.py",
            "packages": package_results,
        },
        "runtimeVerification": observation["runtimeVerification"],
        "issueQuery": observation["issueQuery"],
        "criticalSignals": observation["criticalSignals"],
        "monitorOwner": expectation["monitorOwner"],
        "rollbackOwner": expectation["rollbackOwner"],
        "previousCheckpointSha256": previous_digest,
        "nextCheckpointId": next_id,
        "nextDueUtc": next_due,
    }
    payload = canonical_json(checkpoint)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise MonitorError("checkpoint output already exists") from error
    return checkpoint


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expectation", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--local-draft", type=Path, required=True)
    parser.add_argument("--fresh-download", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    checkpoint = create_checkpoint(
        args.expectation,
        args.observation,
        args.local_draft,
        args.fresh_download,
        args.output,
        args.previous,
    )
    print(
        "PASS_MONITOR_CHECKPOINT "
        f"checkpoint={checkpoint['checkpointId']} sha256={sha256_file(args.output)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
