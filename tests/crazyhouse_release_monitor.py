#!/usr/bin/env python3
"""Exercise the frozen Crazyhouse synthetic monitoring and rollback matrix."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "release"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import crazyhouse_release_monitor as monitor
import verify_crazyhouse_monitor_chain as chain_verifier


VERSION = "0.0.0"
COMMIT = "1" * 40
TREE = "2" * 40
TAG_OBJECT = "3" * 40
SOURCE_DATE_EPOCH = 1_700_000_000
PUBLICATION = datetime(2026, 1, 1, tzinfo=timezone.utc)


class HarnessError(RuntimeError):
    """The synthetic qualification harness did not prove its frozen matrix."""


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def expected_names() -> list[str]:
    return sorted(
        [
            f"crazyhouse-stockfish-{VERSION}-windows-x86-64.zip",
            f"crazyhouse-stockfish-{VERSION}-windows-x86-64-avx2.zip",
            f"crazyhouse-stockfish-{VERSION}-source.tar.xz",
            "crazyhouse-stockfish-release-manifest.json",
            "SHA256SUMS",
        ]
    )


def make_release(root: Path) -> list[dict[str, object]]:
    root.mkdir(parents=True)
    for index, name in enumerate(expected_names()):
        payload = (f"synthetic-release-fixture-{index}-{name}\n").encode("ascii")
        (root / name).write_bytes(payload)
    return [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.iterdir(), key=lambda item: item.name)
    ]


def copy_release(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for path in sorted(source.iterdir(), key=lambda item: item.name):
        shutil.copyfile(path, destination / path.name)


def executable_identity(target: str) -> tuple[int, str]:
    payload = ("fixture-engine-" + target).encode("ascii")
    return len(payload), hashlib.sha256(payload).hexdigest()


def make_expectation(assets: list[dict[str, object]]) -> dict[str, Any]:
    native_targets: dict[str, object] = {}
    for target in monitor.TARGETS:
        size, digest = executable_identity(target)
        native_targets[target] = {
            "asset": f"crazyhouse-stockfish-{VERSION}-{target}.zip",
            "executableBytes": size,
            "executableSha256": digest,
        }
    published = utc(PUBLICATION)
    return {
        "schema": "crazyhouse-release-monitor-expectation/v1",
        "project": "Crazyhouse-Stockfish",
        "variant": "crazyhouse",
        "version": VERSION,
        "repository": "https://github.com/example/Crazyhouse-Stockfish",
        "releaseId": 101,
        "tagName": "v" + VERSION,
        "tagObject": TAG_OBJECT,
        "peeledCommit": COMMIT,
        "candidateTree": TREE,
        "sourceDateEpoch": SOURCE_DATE_EPOCH,
        "publicationUtc": published,
        "publishedAt": published,
        "draft": False,
        "prerelease": False,
        "assets": assets,
        "nativeTargets": native_targets,
        "network": monitor.NETWORK,
        "monitorOwner": "release-monitor-owner",
        "rollbackOwner": "release-rollback-owner",
        "monitorScheduleActiveThrough": "T+168h",
        "rollbackDecisionPathActive": True,
        "evidenceMode": "SYNTHETIC",
        "publicationReceipt": None,
    }


def healthy_runtime(expectation: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for target in monitor.TARGETS:
        frozen = expectation["nativeTargets"][target]
        results.append(
            {
                "target": target,
                "asset": frozen["asset"],
                "executableBytes": frozen["executableBytes"],
                "executableSha256": frozen["executableSha256"],
                "hostFeatureFloorSatisfied": True,
                "exactReleasedExecutable": True,
                "exactPackagedNetwork": True,
                "explicitCrazyhouseEvalFile": True,
                "legacyRouteMarker": True,
                "fallbackObserved": False,
                "uciCapabilityPassed": True,
                "optionInventoryPassed": True,
                "deterministicSmokePassed": True,
                "crashObserved": False,
                "illegalMoveObserved": False,
                "protocolRegressionObserved": False,
            }
        )
    return {
        "schema": "crazyhouse-release-runtime-verification/v1",
        "status": "PASS",
        "results": results,
    }


def healthy_issue_query() -> dict[str, Any]:
    return {
        "schema": "crazyhouse-release-issue-query/v1",
        "status": "PASS",
        "querySucceeded": True,
        "attempts": 1,
        "isolatedTimeouts": 0,
        "retrySucceeded": False,
        "categories": {
            "crash": 0,
            "illegalMove": 0,
            "loader": 0,
            "protocol": 0,
            "gui": 0,
            "ruleDrift": 0,
        },
    }


def observation(
    checkpoint_id: str,
    expectation: dict[str, Any],
    kind: str = "healthy",
) -> dict[str, Any]:
    index = monitor.SCHEDULE_INDEX[checkpoint_id]
    due = PUBLICATION + timedelta(seconds=monitor.SCHEDULE[index][1])
    runtime = healthy_runtime(expectation)
    issue = healthy_issue_query()
    signals: list[dict[str, Any]] = []
    state = "HEALTHY"
    if kind == "timeout":
        state = "DEGRADED_INVESTIGATING"
        issue.update(
            {
                "attempts": 2,
                "isolatedTimeouts": 1,
                "retrySucceeded": True,
            }
        )
    elif kind == "rollback":
        state = "ROLLBACK_RECOMMENDED"
        runtime["status"] = "FAIL"
        runtime["results"][0]["deterministicSmokePassed"] = False
        runtime["results"][0]["crashObserved"] = True
        signals.append(
            {
                "type": "REPRODUCIBLE_CRASH",
                "status": "OPEN",
                "evidence": "synthetic reproducible crash fixture",
                "publicMutationPerformed": False,
            }
        )
    return {
        "schema": "crazyhouse-release-monitor-observation/v1",
        "checkpointId": checkpoint_id,
        "capturedUtc": utc(due + timedelta(seconds=1)),
        "state": state,
        "runtimeVerification": runtime,
        "issueQuery": issue,
        "criticalSignals": signals,
    }


class FixtureVerifiers:
    def __init__(self, expectation: dict[str, Any]) -> None:
        self.expectation = expectation
        self.release_calls = 0
        self.native_calls: list[str] = []

    def release(
        self,
        local: Path,
        downloaded: Path,
        version: str,
        commit: str,
        tree: str,
        source_date_epoch: int,
    ) -> int:
        self.release_calls += 1
        if (
            version != VERSION
            or commit != COMMIT
            or tree != TREE
            or source_date_epoch != SOURCE_DATE_EPOCH
        ):
            raise HarnessError("global verifier received a drifted identity")
        local_identity = [(path.name, path.stat().st_size, sha256(path)) for path in sorted(local.iterdir())]
        downloaded_identity = [
            (path.name, path.stat().st_size, sha256(path))
            for path in sorted(downloaded.iterdir())
        ]
        if local_identity != downloaded_identity:
            raise HarnessError("global verifier received differing fixture bytes")
        return len(self.expectation["assets"])

    def native(
        self,
        archive: Path,
        version: str,
        commit: str,
        tree: str,
        source_date_epoch: int,
        target: str,
        executable_bytes: int,
        executable_sha256: str,
    ) -> dict[str, object]:
        frozen = self.expectation["nativeTargets"][target]
        if (
            version != VERSION
            or commit != COMMIT
            or tree != TREE
            or source_date_epoch != SOURCE_DATE_EPOCH
            or archive.name != frozen["asset"]
            or executable_bytes != frozen["executableBytes"]
            or executable_sha256 != frozen["executableSha256"]
        ):
            raise HarnessError("native verifier received a drifted identity")
        self.native_calls.append(target)
        return {
            "schema": "crazyhouse-native-package-verification/v1",
            "status": "PASS_NATIVE_PACKAGE_VERIFICATION",
            "asset": archive.name,
            "bytes": archive.stat().st_size,
            "sha256": sha256(archive),
            "members": 11,
            "version": version,
            "target": target,
            "commit": commit,
            "tree": tree,
            "sourceDateEpoch": source_date_epoch,
            "executableBytes": executable_bytes,
            "executableSha256": executable_sha256,
            "networkPolicy": "crazyhouse-native-network-production/v1",
            "releaseEvidenceNetwork": True,
            "networkBytes": monitor.NETWORK["bytes"],
            "networkSha256": monitor.NETWORK["sha256"],
            "packageInventorySha256": "4" * 64,
            "sbomSha256": "5" * 64,
        }


def build_scenario(
    root: Path, specs: Sequence[tuple[str, str]]
) -> tuple[Path, list[Path], dict[str, Any]]:
    local = root / "local-draft"
    assets = make_release(local)
    expectation = make_expectation(assets)
    expectation_path = root / "expectation.json"
    write_json(expectation_path, expectation)
    callbacks = FixtureVerifiers(expectation)
    chain_dir = root / "chain"
    checkpoints: list[Path] = []
    for index, (checkpoint_id, kind) in enumerate(specs):
        slug = checkpoint_id.replace("+", "plus").replace("m", "min").replace("h", "hour")
        fresh = root / f"crazyhouse-monitor-{VERSION}-{index:03d}-{slug}"
        copy_release(local, fresh)
        observation_path = root / "observations" / f"{index:03d}.json"
        write_json(observation_path, observation(checkpoint_id, expectation, kind))
        output = chain_dir / f"{index:03d}-{slug}.json"
        monitor.create_checkpoint(
            expectation_path,
            observation_path,
            local,
            fresh,
            output,
            checkpoints[-1] if checkpoints else None,
            release_verifier=callbacks.release,
            native_verifier=callbacks.native,
        )
        checkpoints.append(output)
    if callbacks.release_calls != len(specs):
        raise HarnessError("global verifier composition count differs")
    if callbacks.native_calls != list(monitor.TARGETS) * len(specs):
        raise HarnessError("native verifier composition order differs")
    result = chain_verifier.verify_chain(expectation_path, checkpoints)
    return expectation_path, checkpoints, result


def write_mutant_chain(
    root: Path,
    values: list[dict[str, Any]],
    *,
    rechain: bool = True,
) -> list[Path]:
    paths: list[Path] = []
    for index, value in enumerate(values):
        if rechain:
            value["previousCheckpointSha256"] = sha256(paths[-1]) if paths else None
        path = root / f"{index:03d}.json"
        write_json(path, value)
        paths.append(path)
    return paths


def run_contract() -> tuple[int, int]:
    positive_specs = [
        [("T0", "healthy")],
        [("T0", "healthy"), ("T+15m", "healthy")],
        [("T0", "healthy"), ("T+15m", "healthy"), ("T+1h", "healthy")],
        [
            ("T0", "healthy"),
            ("T+15m", "healthy"),
            ("T+1h", "healthy"),
            ("T+6h", "healthy"),
        ],
        [("T0", "healthy"), ("T+15m", "timeout")],
        [("T0", "healthy"), ("T+15m", "healthy"), ("T+1h", "rollback")],
    ]
    positive_results: list[dict[str, Any]] = []
    negative_labels: list[str] = []

    with tempfile.TemporaryDirectory(prefix="crazyhouse-monitor-contract-") as temporary:
        root = Path(temporary)
        scenarios: list[tuple[Path, list[Path], dict[str, Any]]] = []
        for index, specs in enumerate(positive_specs):
            scenarios.append(build_scenario(root / f"positive-{index}", specs))
            positive_results.append(scenarios[-1][2])
        if positive_results[0]["releasedMonitoredEligible"] is not False:
            raise HarnessError("T0 alone became terminally eligible")
        if positive_results[2]["releasedMonitoredEligible"] is not True:
            raise HarnessError("healthy T+1h chain did not become eligible")
        if positive_results[4]["latestState"] != "DEGRADED_INVESTIGATING":
            raise HarnessError("isolated-timeout fixture did not remain degraded")
        if (
            positive_results[5]["latestState"] != "ROLLBACK_RECOMMENDED"
            or positive_results[5]["releasedMonitoredEligible"] is not False
            or positive_results[5]["openCriticalSignals"] != ["REPRODUCIBLE_CRASH"]
        ):
            raise HarnessError("rollback fixture boundary differs")

        expectation_path, base_paths, _ = scenarios[2]
        base_values = [json.loads(path.read_text(encoding="utf-8")) for path in base_paths]

        def negative(
            label: str,
            mutate: Callable[[list[dict[str, Any]]], None],
            *,
            count: int = 1,
            rechain: bool = True,
        ) -> None:
            case = root / "negative" / f"{len(negative_labels):02d}"
            values = deepcopy(base_values[:count])
            mutate(values)
            paths = write_mutant_chain(case, values, rechain=rechain)
            try:
                chain_verifier.verify_chain(expectation_path, paths)
            except chain_verifier.ChainVerificationError:
                negative_labels.append(label)
                return
            raise HarnessError("negative was accepted: " + label)

        negative("missing global manifest", lambda values: values[0]["globalVerification"].pop("manifest"))
        negative("malformed manifest JSON", lambda values: values[0]["globalVerification"].__setitem__("manifest", "{not-json"))
        negative("duplicate manifest JSON key", lambda values: values[0]["globalVerification"]["manifest"].__setitem__("duplicateKeys", True))
        negative("wrong manifest schema", lambda values: values[0]["globalVerification"]["manifest"].__setitem__("schemaVersion", 2))
        negative("release version or tag mismatch", lambda values: values[0].__setitem__("version", "9.9.9"))
        negative("candidate commit mismatch", lambda values: values[0].__setitem__("peeledCommit", "6" * 40))
        negative("candidate tree mismatch", lambda values: values[0]["globalVerification"].__setitem__("candidateTree", "6" * 40))
        negative("asset count mismatch", lambda values: values[0]["globalVerification"].__setitem__("authenticatedFiles", 4))
        negative("missing asset", lambda values: values[0]["assets"].pop())
        negative("extra asset", lambda values: values[0]["assets"].append({"name": "extra.bin", "bytes": 1, "sha256": "6" * 64}))
        negative("asset size drift", lambda values: values[0]["assets"][0].__setitem__("bytes", values[0]["assets"][0]["bytes"] + 1))
        negative("asset digest drift", lambda values: values[0]["assets"][0].__setitem__("sha256", "6" * 64))
        negative("missing checksum row", lambda values: values[0]["globalVerification"]["checksum"].__setitem__("rows", 3))
        negative("duplicate checksum row", lambda values: values[0]["globalVerification"]["checksum"].__setitem__("duplicates", 1))
        negative("malformed checksum row", lambda values: values[0]["globalVerification"]["checksum"].__setitem__("malformedRows", 1))
        negative("path-bearing checksum row", lambda values: values[0]["globalVerification"]["checksum"].__setitem__("pathRows", 1))
        negative("non-ASCII checksum data", lambda values: values[0]["globalVerification"]["checksum"].__setitem__("strictAscii", False))
        negative("corresponding-source relationship drift", lambda values: values[0]["globalVerification"]["correspondingSource"].__setitem__("tree", "6" * 40))
        negative("x86-64 native package verification failure", lambda values: values[0]["nativePackageVerification"]["packages"][0].__setitem__("status", "FAIL"))
        negative("AVX2 native package verification failure", lambda values: values[0]["nativePackageVerification"]["packages"][1].__setitem__("status", "FAIL"))
        negative("network alias identity drift", lambda values: values[0]["nativePackageVerification"]["packages"][0].__setitem__("networkAlias", "wrong.nnue"))
        negative("SBOM or license authority drift", lambda values: values[0]["nativePackageVerification"]["packages"][0].__setitem__("licenseStatus", "FAIL"))
        negative("release remains draft", lambda values: values[0].__setitem__("draft", True))
        negative("release is prerelease", lambda values: values[0].__setitem__("prerelease", True))
        negative("stable tag peeled commit moved", lambda values: values[0].__setitem__("peeledCommit", "7" * 40))
        negative("stable tag object recreated", lambda values: values[0].__setitem__("tagObject", "7" * 40))
        negative("publication timestamp drift", lambda values: values[0].__setitem__("publishedAt", "2026-01-01T00:00:01Z"))
        negative("duplicate checkpoint ID", lambda values: values[1].__setitem__("checkpointId", "T0"), count=2)
        negative("checkpoint capture time regression", lambda values: values[1].__setitem__("capturedUtc", "2025-12-31T23:59:59Z"), count=2)
        negative("checkpoint exceeds lateness without degraded state", lambda values: values[1].__setitem__("capturedUtc", "2026-01-01T00:20:01Z"), count=2)
        negative("previous checkpoint digest mismatch", lambda values: values[1].__setitem__("previousCheckpointSha256", "0" * 64), count=2, rechain=False)
        negative("rollback owner missing", lambda values: values[0].__setitem__("rollbackOwner", ""))
        negative("runtime result missing", lambda values: values[0].pop("runtimeVerification"))

        def runtime_mutation(values: list[dict[str, Any]], key: str, value: bool) -> None:
            values[0]["runtimeVerification"]["status"] = "FAIL"
            values[0]["runtimeVerification"]["results"][0][key] = value

        negative("runtime evaluator route mismatch", lambda values: runtime_mutation(values, "legacyRouteMarker", False))
        negative("runtime evaluator fallback observed", lambda values: runtime_mutation(values, "fallbackObserved", True))
        negative("illegal move signal suppressed", lambda values: runtime_mutation(values, "illegalMoveObserved", True))

        def crash_mutation(values: list[dict[str, Any]]) -> None:
            runtime_mutation(values, "crashObserved", True)
            values[0]["runtimeVerification"]["results"][0]["deterministicSmokePassed"] = False

        negative("crash signal suppressed", crash_mutation)
        negative("protocol regression signal suppressed", lambda values: runtime_mutation(values, "protocolRegressionObserved", True))
        negative("asset download remains inaccessible but state is healthy", lambda values: values[0]["globalVerification"]["freshBytes"].__setitem__("status", "FAIL"))
        negative("issue query fails but is recorded as an empty successful result", lambda values: values[0]["issueQuery"].__setitem__("querySucceeded", False))

        contract = json.loads(
            (
                REPO_ROOT
                / "tests"
                / "crazyhouse"
                / "p16-monitoring-rollback-v1.json"
            ).read_text(encoding="utf-8")
        )
        if contract["positive_count"] != len(positive_results) or contract[
            "negative_count"
        ] != len(negative_labels):
            raise HarnessError("positive or negative count differs from contract")
        if contract["negative_matrix"] != negative_labels:
            raise HarnessError("negative label order differs from contract")

    return len(positive_results), len(negative_labels)


def main() -> int:
    positive, negative = run_contract()
    print(
        "PASS_MONITORING_ROLLBACK_FIXTURES "
        f"positive={positive} negative={negative} "
        "real_publication=false tag=false release_claim=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
