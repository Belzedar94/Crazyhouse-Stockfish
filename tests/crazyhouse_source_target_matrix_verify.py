#!/usr/bin/env python3
"""Validate the source-only Crazyhouse release target matrix without building."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any


EXPECTED_TARGETS = ["windows-x86-64", "windows-x86-64-avx2"]
EXPECTED_ARCHES = ["x86-64", "x86-64-avx2"]
PROBE_PREFIX = "CRAZYHOUSE_SOURCE_TARGET"


class MatrixError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pin(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {"path": str(resolved), "bytes": resolved.stat().st_size, "sha256": sha256(resolved)}


def executable(raw: Path | None, fallback: str) -> Path:
    if raw is not None:
        result = raw.resolve()
    else:
        found = shutil.which(fallback)
        require(found is not None, f"missing executable: {fallback}")
        result = Path(found).resolve()
    require(result.is_file(), f"executable is unavailable: {result}")
    return result


def run_probe(make: Path, makefile_text: str, arch: str, enabled: bool, root: Path) -> dict[str, Any]:
    case = root / f"{arch}-{'enabled' if enabled else 'disabled'}"
    case.mkdir()
    (case / "Makefile").write_text(makefile_text, encoding="utf-8", newline="\n")
    fields = " ".join(
        [
            "arch=$(ARCH)",
            "target_windows=$(target_windows)",
            "bits=$(bits)",
            "sse2=$(sse2)",
            "ssse3=$(ssse3)",
            "sse41=$(sse41)",
            "avx2=$(avx2)",
            "pext=$(pext)",
            "popcnt=$(popcnt)",
            "flag_count=$(words $(filter $(MINGW_REPRODUCIBLE_LINK_FLAG),$(LDFLAGS)))",
        ]
    )
    rule = f"crazyhouse_source_target_probe: ; @printf '%s\\n' \"{PROBE_PREFIX} {fields}\""
    command = [
        str(make),
        "--no-print-directory",
        "--old-file=.depend",
        "--eval",
        rule,
        f"ARCH={arch}",
        "COMP=gcc",
        "OS=Windows_NT",
        f"mingw_reproducible={'yes' if enabled else 'no'}",
        "crazyhouse_source_target_probe",
    ]
    completed = subprocess.run(
        command,
        cwd=case,
        env={
            **os.environ,
            "PATH": os.pathsep.join(
                [r"C:\msys64\usr\bin", r"C:\msys64\mingw64\bin", r"C:\Windows\System32"]
            ),
            "SOURCE_DATE_EPOCH": "0",
            "TZ": "UTC",
        },
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(completed.returncode == 0, f"Make feature probe failed for {arch}")
    require(completed.stderr == "", f"Make feature probe emitted stderr for {arch}")
    lines = [line for line in completed.stdout.splitlines() if line.startswith(PROBE_PREFIX)]
    require(len(lines) == 1, f"unexpected probe line count for {arch}")
    values: dict[str, str] = {}
    for token in lines[0].split()[1:]:
        require("=" in token, f"malformed probe token for {arch}: {token}")
        key, value = token.split("=", 1)
        require(key not in values, f"duplicate probe token for {arch}: {key}")
        values[key] = value
    generated = sorted(path.name for path in case.iterdir() if path.name != "Makefile")
    require(generated == [".build_date.txt", ".build_diffindex.txt", ".build_sha.txt"],
            f"unexpected Make parse artifacts for {arch}: {generated}")
    require(not any(path.suffix.lower() in {".o", ".obj", ".exe"} for path in case.rglob("*")),
            f"compiler artifact appeared during source-only probe: {arch}")
    return {
        "arch": arch,
        "mode": "yes" if enabled else "no",
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
        "parsed": values,
        "generated_make_parse_files": generated,
        "compiler_artifacts": 0,
    }


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=root / "tests" / "crazyhouse" / "p15-source-target-matrix-v1.json")
    parser.add_argument("--makefile", type=Path, default=root / "src" / "Makefile")
    parser.add_argument("--p15-result", type=Path, default=root / "tests" / "crazyhouse" / "p15-mingw-release-reproducibility-v1.result.001.json")
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--make", dest="make_program", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def verify(args: argparse.Namespace) -> dict[str, Any]:
    contract_path = args.contract.resolve()
    makefile_path = args.makefile.resolve()
    p15_path = args.p15_result.resolve()
    network_path = args.network.resolve()
    make = executable(args.make_program, "make")
    for path in (contract_path, makefile_path, p15_path, network_path):
        require(path.is_file(), f"required input is unavailable: {path}")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(contract.get("schema") == "crazyhouse-p15-source-target-matrix/v1", "matrix schema drift")
    require(contract.get("status") == "FROZEN_SOURCE_ONLY_NOT_RELEASE_QUALIFIED", "matrix status drift")
    targets = contract.get("targets")
    require(isinstance(targets, list), "matrix targets are missing")
    require([target.get("id") for target in targets] == EXPECTED_TARGETS, "target IDs or order drift")
    require([target.get("arch") for target in targets] == EXPECTED_ARCHES, "target ARCH values or order drift")
    require(len(set(EXPECTED_TARGETS)) == len(targets), "duplicate matrix target")
    require(contract.get("common_build_contract", {}).get("legacy_backend_assignment_present") is False,
            "matrix attempts to override the production legacy backend")
    require(contract.get("boundaries", {}).get("full_engine_build_performed") is False,
            "source-only matrix overclaims a full build")
    require(contract.get("boundaries", {}).get("release_claimed") is False, "matrix overclaims release")

    network = contract.get("network", {})
    require(network_path.stat().st_size == network.get("bytes"), "network byte count mismatch")
    require(sha256(network_path) == network.get("sha256"), "network digest mismatch")
    require(network.get("candidate_public_alias") == "Crazyhouse_v1.nnue", "network alias drift")
    require(network.get("alias_must_be_byte_identical") is True, "network alias is not byte-bound")
    require(network.get("source_default_change_allowed") is False, "matrix permits a source-default change")
    require(network.get("license") == "CC0-1.0", "network license identity drift")

    p15 = json.loads(p15_path.read_text(encoding="utf-8"))
    require(p15.get("status") == "PASS_MINGW_REPRODUCIBILITY_PLUMBING", "P15 plumbing result is not passing")
    require(p15.get("boundaries", {}).get("release_build_mode_qualified") is True,
            "P15 deterministic build mode is not qualified")
    require(p15.get("boundaries", {}).get("full_engine_reproducible") is False,
            "P15 plumbing result overclaims a full engine")

    makefile_text = makefile_path.read_text(encoding="utf-8")
    require(makefile_text.count("mingw_reproducible = no") == 1, "Make reproducibility default drift")
    require(makefile_text.count("-Wl,--no-insert-timestamp") == 1, "Make deterministic link flag drift")
    for arch in EXPECTED_ARCHES:
        require(arch in makefile_text, f"matrix ARCH is not exposed by Make: {arch}")

    with tempfile.TemporaryDirectory(prefix="crazyhouse-source-matrix-") as temp_name:
        temp_root = Path(temp_name)
        probes: list[dict[str, Any]] = []
        for target in targets:
            enabled = run_probe(make, makefile_text, target["arch"], True, temp_root)
            disabled = run_probe(make, makefile_text, target["arch"], False, temp_root)
            expected = {**target["make_features"], "arch": target["arch"], "target_windows": "yes"}
            expected["flag_count"] = "1"
            require(enabled["parsed"] == expected, f"enabled Make features drift: {target['id']}")
            disabled_expected = dict(expected)
            disabled_expected["flag_count"] = "0"
            require(disabled["parsed"] == disabled_expected, f"disabled Make features drift: {target['id']}")
            probes.extend([enabled, disabled])

    return {
        "schema": "crazyhouse-p15-source-target-matrix-result/v1",
        "created_utc": utc_now(),
        "status": "PASS_SOURCE_TARGET_MATRIX",
        "evidence_class": "R4_RELEASE",
        "claim_scope": "SOURCE_TARGET_DECISION_ONLY",
        "contract": pin(contract_path),
        "makefile": pin(makefile_path),
        "p15_plumbing_result": pin(p15_path),
        "network": pin(network_path),
        "make": pin(make),
        "targets": EXPECTED_TARGETS,
        "arches": EXPECTED_ARCHES,
        "probes": probes,
        "full_engine_build_performed": False,
        "release_executable_reproducible": False,
        "release_archive_reproducible": False,
        "release_candidate_selected": False,
        "strength_credit": False,
        "openbench_used": False,
        "release_claimed": False,
    }


def write_result(payload: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
        return
    destination = output.resolve()
    require(not destination.exists(), f"refusing to rewrite result: {destination}")
    require(destination.parent.is_dir(), f"output parent is unavailable: {destination.parent}")
    partial = destination.with_name(destination.name + ".partial")
    require(not partial.exists(), f"partial result exists: {partial}")
    try:
        partial.write_text(rendered, encoding="utf-8", newline="\n")
        os.replace(partial, destination)
    finally:
        if partial.exists():
            partial.unlink()


def main() -> int:
    try:
        args = parse_args()
        write_result(verify(args), args.output)
        return 0
    except (OSError, json.JSONDecodeError, MatrixError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
