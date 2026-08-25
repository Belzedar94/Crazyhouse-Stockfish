#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
from typing import Any


OWNER_TASK = "019ff608-f6fe-7792-b0c9-fa6d8be8e6d8"
EXPECTED_NETWORK_BYTES = 58_534_811
EXPECTED_NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
COMPILE_DIAGNOSTIC_RE = re.compile(r"(^|\s)(warning:|error:)", re.IGNORECASE)


class LeaseError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LeaseError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    require(resolved.is_file(), f"not a file: {resolved}")
    return {
        "path": resolved.as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def write_json_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as output:
        output.write(payload)


def git_text(git: Path, repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [str(git), "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(completed.returncode == 0, completed.stderr.decode("utf-8", "replace"))
    require(not completed.stderr, f"git wrote stderr for {args!r}")
    return completed.stdout.decode("utf-8", "strict").strip()


def tracked_paths(git: Path, repo: Path, commit: str) -> list[str]:
    completed = subprocess.run(
        [
            str(git),
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "-z",
            "--format=%(objectmode)%x09%(path)",
            commit,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(completed.returncode == 0, "git ls-tree failed")
    require(not completed.stderr, "git ls-tree wrote stderr")
    rows: list[str] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        mode, path = raw.decode("utf-8", "strict").split("\t", 1)
        require(mode in {"100644", "100755"}, f"unsupported archive mode {mode}: {path}")
        rows.append(path)
    require(rows == sorted(rows), "tracked path inventory is not sorted")
    require(len(rows) >= 100, "tracked path inventory is unexpectedly small")
    return rows


def source_entries(
    root: Path, paths: list[str], *, require_exact_inventory: bool = True
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for relative in paths:
        path = (root / Path(relative)).resolve(strict=True)
        require(path.is_relative_to(root.resolve()), f"tracked path escaped export: {relative}")
        record = file_record(path)
        entries.append(
            {
                "path": relative,
                "bytes": record["bytes"],
                "sha256": record["sha256"],
            }
        )
    if require_exact_inventory:
        actual = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
        require(actual == paths, "clean export contains a missing or extra source file")
    require(not (root / ".git").exists(), "clean export unexpectedly contains Git metadata")
    return entries


def extract_archive(archive: Path, destination: Path, expected_commit: str) -> None:
    require(not destination.exists(), f"refusing existing export: {destination}")
    destination.mkdir(parents=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:") as source:
        require(
            source.pax_headers.get("comment") == expected_commit,
            "git archive commit comment mismatch",
        )
        for member in source.getmembers():
            require(not member.issym() and not member.islnk(), f"archive link rejected: {member.name}")
            target = (root / member.name).resolve()
            require(target.is_relative_to(root), f"archive path escaped destination: {member.name}")
        source.extractall(root, filter="data")


def process_snapshot(pids: list[int]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    if os.name != "nt":
        for pid in pids:
            alive = True
            try:
                os.kill(pid, 0)
            except OSError:
                alive = False
            snapshots.append({"pid": pid, "alive": alive})
        return snapshots

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    query_name = kernel32.QueryFullProcessImageNameW
    query_name.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query_name.restype = wintypes.BOOL
    get_priority = kernel32.GetPriorityClass
    get_priority.argtypes = [wintypes.HANDLE]
    get_priority.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    for pid in pids:
        handle = open_process(0x1000, False, pid)
        if not handle:
            snapshots.append({"pid": pid, "alive": False, "image": None, "priority_class": None})
            continue
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            name_available = bool(query_name(handle, 0, buffer, ctypes.byref(size)))
            image = buffer.value if name_available else None
            priority = int(get_priority(handle)) or None
            snapshots.append(
                {
                    "pid": pid,
                    "alive": True,
                    "image": Path(image).name if image else None,
                    "priority_class": priority,
                }
            )
        finally:
            close_handle(handle)
    return snapshots


def terminate_owned_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.kill()


def run_step(
    *,
    step_id: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    logs: Path,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    stdout_path = logs / f"{step_id}.stdout.log"
    stderr_path = logs / f"{step_id}.stderr.log"
    started = time.monotonic()
    timed_out = False
    pid: int | None = None
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pid = process.pid
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_owned_tree(process)
            process.wait(timeout=30)
        exit_code = process.returncode
    record = {
        "id": step_id,
        "command": [str(part) for part in command],
        "working_directory": cwd.resolve().as_posix(),
        "pid": pid,
        "priority": "Normal",
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stdout": file_record(stdout_path),
        "stderr": file_record(stderr_path),
    }
    steps.append(record)
    return record


def read_log(record: dict[str, Any], stream: str) -> str:
    return Path(record[stream]["path"]).read_text(encoding="utf-8", errors="replace")


def parse_json_log(record: dict[str, Any]) -> dict[str, Any]:
    return json.loads(Path(record["stdout"]["path"]).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--lease-dir", type=Path, required=True)
    parser.add_argument("--lease", type=int, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--git", type=Path, required=True)
    parser.add_argument("--make", type=Path, required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--start-receipt", type=Path, required=True)
    parser.add_argument("--end-receipt", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--build-timeout", type=int, default=3600)
    parser.add_argument("--verify-timeout", type=int, default=900)
    parser.add_argument("--foreign-pid", type=int, action="append", default=[])
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    require(args.lease > 0, "lease must be positive")
    require(args.jobs == 1, "formal clean-export lease is frozen to one build job")
    require(COMMIT_RE.fullmatch(args.expected_commit) is not None, "invalid expected commit")
    require(COMMIT_RE.fullmatch(args.expected_tree) is not None, "invalid expected tree")

    repo = args.repo.resolve(strict=True)
    lease_dir = args.lease_dir.resolve(strict=False)
    network = args.network.resolve(strict=True)
    git = args.git.resolve(strict=True)
    make = args.make.resolve(strict=True)
    compiler = args.compiler.resolve(strict=True)
    python = args.python.resolve(strict=True)
    start_receipt = args.start_receipt.resolve(strict=False)
    end_receipt = args.end_receipt.resolve(strict=False)
    controller = Path(__file__).resolve(strict=True)
    independent_verifier = controller.with_name("verify_crazyhouse_openbench_clean_exports.py")
    independent_verifier.resolve(strict=True)

    require(not lease_dir.exists(), f"single-use lease exists: {lease_dir}")
    require(not start_receipt.exists(), f"start receipt exists: {start_receipt}")
    require(not end_receipt.exists(), f"end receipt exists: {end_receipt}")
    require(git_text(git, repo, "rev-parse", "HEAD") == args.expected_commit, "HEAD mismatch")
    require(git_text(git, repo, "rev-parse", "HEAD^{tree}") == args.expected_tree, "tree mismatch")
    require(not git_text(git, repo, "status", "--porcelain=v1"), "source worktree is dirty")
    require(network.stat().st_size == EXPECTED_NETWORK_BYTES, "legacy network byte mismatch")
    require(sha256_file(network) == EXPECTED_NETWORK_SHA256, "legacy network digest mismatch")
    require(shutil.disk_usage(lease_dir.parent).free >= 5 * 1024**3, "lease drive free-space floor failed")
    require(shutil.disk_usage(repo).free >= 2 * 1024**3, "source drive free-space floor failed")

    paths = tracked_paths(git, repo, args.expected_commit)
    commit_epoch = int(git_text(git, repo, "show", "-s", "--format=%ct", args.expected_commit))
    if args.preflight_only:
        print(
            json.dumps(
                {
                    "schema": "crazyhouse-openbench-clean-exports-preflight/v1",
                    "status": "PASS_NO_TARGET_PREVIEW",
                    "would_create_target": lease_dir.as_posix(),
                    "would_create_start_receipt": start_receipt.as_posix(),
                    "would_create_end_receipt": end_receipt.as_posix(),
                    "owner_task": OWNER_TASK,
                    "source": {
                        "commit": args.expected_commit,
                        "tree": args.expected_tree,
                        "tracked_files": len(paths),
                        "clean": True,
                    },
                    "network": file_record(network),
                    "target_profile": {
                        "arch": "x86-64",
                        "windows_comp": "mingw",
                        "legacy_evaluator": "scalar",
                        "build_jobs": 1,
                    },
                    "planned_steps": [
                        "archive-a",
                        "archive-b",
                        "unit-a",
                        "build-a",
                        "verify-a",
                        "unit-b",
                        "build-b",
                        "verify-b",
                    ],
                    "mutations": {
                        "foreign_processes": False,
                        "openbench": False,
                        "publication": False,
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    foreign_before = process_snapshot(sorted(set(args.foreign_pid)))
    start_value = {
        "schema": "crazyhouse-resource-handoff-start/v1",
        "created_utc": utc_now(),
        "project": "Crazyhouse-Stockfish",
        "phase": "P10",
        "gate": "G10",
        "lease": args.lease,
        "owner_task": OWNER_TASK,
        "supervisor_pid": os.getpid(),
        "controller_pid": os.getpid(),
        "controller": file_record(controller),
        "independent_verifier": file_record(independent_verifier),
        "sanitized_command": [str(part) for part in sys.argv],
        "target": lease_dir.as_posix(),
        "resources": {
            "build_jobs": 1,
            "builds_sequential": True,
            "timing_evidence": False,
            "connection": "none-local-only",
            "priority": "Normal",
        },
        "authorization": {
            "local_cpu": "AUTHORIZED_BY_OWNER",
            "openbench_submission": False,
            "publication": False,
        },
        "source": {
            "repo": repo.as_posix(),
            "commit": args.expected_commit,
            "tree": args.expected_tree,
            "tracked_files": len(paths),
            "clean": True,
        },
        "network": file_record(network),
        "toolchain": {
            "git": file_record(git),
            "make": file_record(make),
            "compiler": file_record(compiler),
            "python": file_record(python),
        },
        "foreign_processes": foreign_before,
        "foreign_process_policy": "READ_ONLY_NEVER_MUTATE",
        "restoration": "none-required",
    }
    write_json_new(start_receipt, start_value)
    lease_dir.mkdir(parents=True)
    logs = lease_dir / "logs"
    logs.mkdir()
    steps: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []
    result_status = "REJECTED_OPENBENCH_ENGINE_CLEAN_EXPORTS"
    failure: str | None = None
    archives: list[dict[str, Any]] = []
    manifest_record: dict[str, Any] | None = None

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{compiler.parent};{make.parent};{env.get('PATH', '')}",
            "MSYSTEM": "MINGW64",
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
            "SOURCE_DATE_EPOCH": str(commit_epoch),
        }
    )

    try:
        archive_paths = [lease_dir / "source-a.tar", lease_dir / "source-b.tar"]
        for label, archive in zip(("a", "b"), archive_paths, strict=True):
            archive_step = run_step(
                step_id=f"archive-{label}",
                command=[
                    str(git),
                    "-C",
                    str(repo),
                    "archive",
                    "--format=tar",
                    f"--output={archive}",
                    args.expected_commit,
                ],
                cwd=lease_dir,
                env=env,
                timeout_seconds=300,
                logs=logs,
                steps=steps,
            )
            require(not archive_step["timed_out"] and archive_step["exit_code"] == 0, "archive failed")
            require(archive_step["stderr"]["bytes"] == 0, "archive wrote stderr")
            archives.append(file_record(archive))
        require(archives[0]["sha256"] == archives[1]["sha256"], "clean archives differ")
        require(archives[0]["bytes"] == archives[1]["bytes"], "clean archive sizes differ")

        roots = [lease_dir / "export-a", lease_dir / "export-b"]
        entry_sets: list[list[dict[str, Any]]] = []
        for archive, root in zip(archive_paths, roots, strict=True):
            extract_archive(archive, root, args.expected_commit)
            entry_sets.append(source_entries(root, paths))
        require(entry_sets[0] == entry_sets[1], "clean export file manifests differ")
        manifest_path = lease_dir / "source-manifest.json"
        write_json_new(
            manifest_path,
            {
                "schema": "crazyhouse-clean-export-source-manifest/v1",
                "created_utc": utc_now(),
                "commit": args.expected_commit,
                "tree": args.expected_tree,
                "archive_sha256": archives[0]["sha256"],
                "tracked_files": len(paths),
                "entries": entry_sets[0],
                "exports_equal": True,
            },
        )
        manifest_record = file_record(manifest_path)

        for label, root in zip(("a", "b"), roots, strict=True):
            unit_step = run_step(
                step_id=f"unit-{label}",
                command=[
                    str(python),
                    "-m",
                    "unittest",
                    "tests/crazyhouse_openbench_engine_contract_unit.py",
                    "tests/crazyhouse_openbench_clean_exports_unit.py",
                    "-v",
                ],
                cwd=root,
                env=env,
                timeout_seconds=120,
                logs=logs,
                steps=steps,
            )
            require(not unit_step["timed_out"] and unit_step["exit_code"] == 0, f"unit {label} failed")
            unit_text = read_log(unit_step, "stdout") + read_log(unit_step, "stderr")
            require("Ran 12 tests" in unit_text and "OK" in unit_text, f"unit {label} inventory mismatch")

            engine = root / "src" / f"crazyhouse-openbench-{label}.exe"
            build_step = run_step(
                step_id=f"build-{label}",
                command=[
                    str(make),
                    f"-j{args.jobs}",
                    f"EXE={engine.name}",
                    f"GIT_SHA_FULL={args.expected_commit}",
                    "CXX=g++",
                    f"EVALFILE={network.as_posix()}",
                ],
                cwd=root / "src",
                env=env,
                timeout_seconds=args.build_timeout,
                logs=logs,
                steps=steps,
            )
            require(not build_step["timed_out"] and build_step["exit_code"] == 0, f"build {label} failed")
            require(build_step["stderr"]["bytes"] == 0, f"build {label} wrote stderr")
            build_text = read_log(build_step, "stdout")
            diagnostics = [line for line in build_text.splitlines() if COMPILE_DIAGNOSTIC_RE.search(line)]
            require(not diagnostics, f"build {label} emitted compiler diagnostics")
            for marker in (
                "ARCH=x86-64 COMP=mingw",
                "CRAZYHOUSE_LEGACY_BACKEND=scalar",
                "OPENBENCH_PLAY_BUILD=1",
                "-DNNUE_EMBEDDING_OFF",
                "-DCRAZYHOUSE_LEGACY_EMBED_FILE=",
                "-DARCH=x86-64",
                "-msse2",
                f"-DGIT_SHA={args.expected_commit[:8]}",
            ):
                require(marker in build_text, f"build {label} missing marker: {marker}")
            require("-mavx2" not in build_text and "-DUSE_AVX2" not in build_text, f"build {label} drifted to AVX2")
            compile_units = sum(" -c -o " in line for line in build_text.splitlines())
            require(compile_units >= 25, f"build {label} compiled too few units")
            engine_record = file_record(engine)

            verify_step = run_step(
                step_id=f"verify-{label}",
                command=[
                    str(python),
                    "tools/ci/verify_crazyhouse_openbench_engine.py",
                    "--engine",
                    str(engine),
                    "--runs",
                    "2",
                    "--timeout",
                    "180",
                ],
                cwd=root,
                env=env,
                timeout_seconds=args.verify_timeout,
                logs=logs,
                steps=steps,
            )
            require(not verify_step["timed_out"] and verify_step["exit_code"] == 0, f"verify {label} failed")
            require(verify_step["stderr"]["bytes"] == 0, f"verify {label} wrote stderr")
            verification = parse_json_log(verify_step)
            require(verification["bench"]["runs"] == 2, f"verify {label} wrong run count")
            require(verification["bench"]["deterministic"] is True, f"verify {label} nondeterministic")
            require(
                verification["capability"]["network_identity"] == EXPECTED_NETWORK_SHA256,
                f"verify {label} network mismatch",
            )
            require(verification["negative"]["fallback_observed"] is False, f"verify {label} fallback")
            require(
                source_entries(root, paths, require_exact_inventory=False) == entry_sets[0],
                f"build {label} changed tracked source",
            )
            exports.append(
                {
                    "id": label,
                    "root": root.resolve().as_posix(),
                    "engine": engine_record,
                    "unit_step": unit_step["id"],
                    "build_step": build_step["id"],
                    "verification_step": verify_step["id"],
                    "compile_units": compile_units,
                    "tracked_source_unchanged": True,
                    "verification": verification,
                }
            )

        nodes = [entry["verification"]["bench"]["nodes"][0] for entry in exports]
        require(len(set(nodes)) == 1, "clean exports disagree on benchmark nodes")
        require(all(entry["verification"]["bench"]["nodes"] == [nodes[0], nodes[0]] for entry in exports), "per-export bench drift")
        require(exports[0]["compile_units"] == exports[1]["compile_units"], "compile unit counts differ")
        result_status = "PASS_TWO_CLEAN_OPENBENCH_ENGINE_EXPORTS"
    except Exception as error:  # preserve a terminal receipt for every admitted lease
        failure = f"{type(error).__name__}: {error}"

    foreign_after = process_snapshot(sorted(set(args.foreign_pid)))
    completed_utc = utc_now()
    result_path = lease_dir / "result.json"
    result_value = {
        "schema": "crazyhouse-openbench-clean-exports-result/v1",
        "created_utc": start_value["created_utc"],
        "completed_utc": completed_utc,
        "project": "Crazyhouse-Stockfish",
        "phase": "P10",
        "gate": "G10",
        "lease": args.lease,
        "owner_task": OWNER_TASK,
        "result": result_status,
        "failure": failure,
        "source": start_value["source"],
        "network": start_value["network"],
        "toolchain": start_value["toolchain"],
        "start_receipt": file_record(start_receipt),
        "archives": archives,
        "source_manifest": manifest_record,
        "exports": exports,
        "steps": steps,
        "equality": {
            "archive_bytes": (
                len(archives) == 2
                and archives[0]["bytes"] == archives[1]["bytes"]
                and archives[0]["sha256"] == archives[1]["sha256"]
            ),
            "source_entries": len(exports) == 2 and all(entry["tracked_source_unchanged"] for entry in exports),
            "engine_bytes": len(exports) == 2 and exports[0]["engine"]["sha256"] == exports[1]["engine"]["sha256"],
            "bench_nodes": (
                exports[0]["verification"]["bench"]["nodes"][0]
                if len(exports) == 2
                and exports[0]["verification"]["bench"]["nodes"]
                == exports[1]["verification"]["bench"]["nodes"]
                else None
            ),
        },
        "resource_handoff": {
            "supervisor_pid": os.getpid(),
            "controller_pid": os.getpid(),
            "build_jobs": 1,
            "foreign_before": foreign_before,
            "foreign_after": foreign_after,
            "foreign_processes_mutated": False,
            "controller_process_mutation_calls": 0,
            "owned_timeout_kills": [step["pid"] for step in steps if step["timed_out"]],
            "restoration": "none-required",
        },
        "claims": {
            "engineering_only": True,
            "strength": False,
            "openbench_official": False,
            "publication": False,
            "release": False,
        },
    }
    write_json_new(result_path, result_value)
    end_value = {
        "schema": "crazyhouse-resource-handoff-end/v1",
        "created_utc": completed_utc,
        "project": "Crazyhouse-Stockfish",
        "phase": "P10",
        "gate": "G10",
        "lease": args.lease,
        "owner_task": OWNER_TASK,
        "supervisor_pid": os.getpid(),
        "controller_pid": os.getpid(),
        "start_receipt": file_record(start_receipt),
        "result": file_record(result_path),
        "status": result_status,
        "foreign_processes_mutated": False,
        "owned_timeout_kills": [step["pid"] for step in steps if step["timed_out"]],
        "restoration": "none-required",
    }
    write_json_new(end_receipt, end_value)
    completion_path = lease_dir / "completion.json"
    write_json_new(
        completion_path,
        {
            "schema": "crazyhouse-openbench-clean-exports-completion/v1",
            "created_utc": utc_now(),
            "lease": args.lease,
            "supervisor_pid": os.getpid(),
            "controller_reported_exit_code": 0 if result_status.startswith("PASS_") else 1,
            "start_receipt": file_record(start_receipt),
            "result": file_record(result_path),
            "end_receipt": file_record(end_receipt),
            "status": result_status,
        },
    )
    print(
        json.dumps(
            {
                "status": result_status,
                "result": file_record(result_path),
                "end_receipt": file_record(end_receipt),
                "completion": file_record(completion_path),
            },
            sort_keys=True,
        )
    )
    return 0 if result_status.startswith("PASS_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
