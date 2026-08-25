#!/usr/bin/env python3
"""Aggregate successful Crazyhouse correctness artifacts into one hashed receipt."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


OFFICIAL_STOCKFISH_ANCESTOR = "229f6339e537a097a79831cd06dbfdb3e623d4ac"
FORBIDDEN_NETWORK_BASENAME = "Crazyhouse_v1.nnue"


class AggregationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AggregationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory_tree(root: Path) -> list[dict[str, object]]:
    root = root.resolve(strict=True)
    require(root.is_dir(), "artifact input root is not a directory")
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.is_symlink():
            raise AggregationError(f"artifact tree contains a symbolic link: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        require(path.name != FORBIDDEN_NETWORK_BASENAME, "legacy network leaked into evidence")
        records.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    require(bool(records), "artifact input tree is empty")
    return records


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    require(completed.returncode == 0, f"git {' '.join(arguments)} failed")
    return completed.stdout.strip()


def write_fresh(path: Path, value: str) -> None:
    require(not path.exists(), f"refusing to replace aggregate output: {path.name}")
    with path.open("x", encoding="utf-8", newline="\n") as destination:
        destination.write(value)
        destination.flush()
        os.fsync(destination.fileno())


def aggregate(
    repository: Path,
    input_root: Path,
    output_dir: Path,
    expected_artifacts: Sequence[str],
) -> dict[str, object]:
    repository = repository.resolve(strict=True)
    input_root = input_root.resolve(strict=True)
    output_dir = output_dir.resolve()
    require(repository.is_dir(), "repository is not a directory")
    require(input_root.is_dir(), "artifact input root is not a directory")
    require(not output_dir.exists(), "aggregate output directory already exists")
    require(output_dir.parent.is_dir(), "aggregate output parent is missing")
    require(bool(expected_artifacts), "expected artifact inventory is empty")
    require(len(set(expected_artifacts)) == len(expected_artifacts), "duplicate expected artifact")

    observed_children = sorted(path.name for path in input_root.iterdir())
    require(
        observed_children == sorted(expected_artifacts),
        f"artifact directory inventory mismatch: {observed_children}",
    )
    for name in expected_artifacts:
        require((input_root / name).is_dir(), f"expected artifact is not a directory: {name}")

    files = inventory_tree(input_root)
    head = git(repository, "rev-parse", "HEAD")
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    src_tree = git(repository, "rev-parse", "HEAD:src")
    tracked_status = git(repository, "status", "--porcelain", "--untracked-files=no")
    require(tracked_status == "", "tracked repository content changed during CI")
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            OFFICIAL_STOCKFISH_ANCESTOR,
            head,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(ancestor.returncode == 0, "official Stockfish baseline is not an ancestor")
    github_sha = os.environ.get("GITHUB_SHA")
    if github_sha:
        require(github_sha == head, "GitHub SHA does not match checked-out HEAD")

    output_dir.mkdir()
    manifest_path = output_dir / "crazyhouse-correctness-manifest.json"
    sums_path = output_dir / "SHA256SUMS"
    manifest: dict[str, object] = {
        "schema": "crazyhouse-public-correctness-ci-aggregate/v1",
        "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project": "Crazyhouse-Stockfish",
        "evidence_class": "E1_ENGINEERING",
        "result": "PASS_REQUIRED_PUBLIC_CORRECTNESS_JOBS",
        "repository": {
            "commit": head,
            "tree": tree,
            "src_tree": src_tree,
            "official_stockfish_ancestor": OFFICIAL_STOCKFISH_ANCESTOR,
            "official_stockfish_ancestor_verified": True,
            "fairy_stockfish_source_allowed": False,
            "tracked_status_clean": True,
        },
        "workflow": {
            "name": os.environ.get("GITHUB_WORKFLOW"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "runner_os": os.environ.get("RUNNER_OS"),
        },
        "required_artifacts": sorted(expected_artifacts),
        "input_file_count": len(files),
        "input_files": files,
        "legacy_network_included": False,
        "fallback_allowed": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "model_selection_claim": False,
        "release_claim": False,
    }
    write_fresh(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    sum_lines = [
        f"{record['sha256']}  artifacts/{record['path']}" for record in files
    ]
    sum_lines.append(f"{sha256(manifest_path)}  aggregate/{manifest_path.name}")
    write_fresh(sums_path, "\n".join(sum_lines) + "\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-artifact", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = aggregate(
            args.repository,
            args.input_root,
            args.output_dir,
            args.expected_artifact,
        )
    except (AggregationError, FileNotFoundError, PermissionError, OSError) as error:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED",
                    "code": type(error).__name__,
                    "message": str(error),
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": manifest["result"],
                "input_file_count": manifest["input_file_count"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
