#!/usr/bin/env python3
"""Verify the source-visible MinGW reproducible-link contract.

This is deliberately a small linker and Makefile-routing gate. It does not
claim that a complete engine or release archive is reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any


SCHEMA = "crazyhouse-mingw-reproducibility-result/v1"
PROBE_PREFIX = "CRAZYHOUSE_MINGW_REPRODUCIBILITY"
LINK_FLAG = "-Wl,--no-insert-timestamp"
LDFLAGS_LINE = "\tLDFLAGS += $(MINGW_REPRODUCIBLE_LINK_FLAG)\n"


class VerificationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Verify Crazyhouse MinGW reproducible-link routing and a tiny PE link."
    )
    parser.add_argument("--makefile", type=Path, default=repo_root / "src" / "Makefile")
    parser.add_argument("--compiler", type=Path)
    parser.add_argument("--make", dest="make_program", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "bytes": stat.st_size,
        "sha256": sha256(resolved),
    }


def run(command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "command": command,
        "cwd": str(cwd.resolve()),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def executable(raw: Path | None, fallback: str) -> Path:
    if raw is not None:
        resolved = raw.resolve()
    else:
        found = shutil.which(fallback)
        require(found is not None, f"required executable is unavailable: {fallback}")
        resolved = Path(found).resolve()
    require(resolved.is_file(), f"required executable is not a file: {resolved}")
    return resolved


def parse_probe(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.startswith(PROBE_PREFIX)]
    require(len(lines) == 1, f"expected exactly one probe line, got {len(lines)}")
    tokens = lines[0].split()
    require(tokens[0] == PROBE_PREFIX, "unexpected probe prefix")
    values: dict[str, str] = {}
    for token in tokens[1:]:
        require("=" in token, f"malformed probe token: {token}")
        key, value = token.split("=", 1)
        require(key not in values, f"duplicate probe key: {key}")
        values[key] = value
    require(set(values) == {"mode", "target_windows", "flag_count"}, "unexpected probe keys")
    try:
        flag_count = int(values["flag_count"])
    except ValueError as exc:
        raise VerificationError("probe flag_count is not an integer") from exc
    return {
        "line": lines[0],
        "mode": values["mode"],
        "target_windows": values["target_windows"],
        "flag_count": flag_count,
    }


def invoke_probe(
    make_program: Path,
    makefile_text: str,
    root: Path,
    name: str,
    assignments: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    case_dir = root / name
    case_dir.mkdir()
    case_makefile = case_dir / "Makefile"
    case_makefile.write_text(makefile_text, encoding="utf-8", newline="\n")
    command = [
        str(make_program),
        "--no-print-directory",
        "--old-file=.depend",
        "-f",
        "Makefile",
        "ARCH=x86-64",
        *assignments,
        "crazyhouse_mingw_reproducibility_probe",
    ]
    execution = run(command, case_dir)
    parsed = parse_probe(execution["stdout"]) if execution["exit_code"] == 0 else None
    return execution, parsed


def pe_timestamp(path: Path) -> int:
    data = path.read_bytes()
    require(len(data) >= 0x40, f"PE file is too short: {path}")
    require(data[:2] == b"MZ", f"missing MZ signature: {path}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    require(pe_offset + 12 <= len(data), f"invalid PE header offset: {path}")
    require(data[pe_offset : pe_offset + 4] == b"PE\0\0", f"missing PE signature: {path}")
    return struct.unpack_from("<I", data, pe_offset + 8)[0]


def tiny_link_case(compiler: Path, root: Path, arch: str, flags: list[str]) -> dict[str, Any]:
    case_dir = root / f"tiny-{arch}"
    case_dir.mkdir()
    source = case_dir / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8", newline="\n")
    obj = case_dir / "main.o"
    compile_command = [str(compiler), "-std=c++17", "-O2", *flags, "-c", "main.cpp", "-o", "main.o"]
    compile_execution = run(compile_command, case_dir)
    require(compile_execution["exit_code"] == 0, f"tiny compile failed for {arch}")

    links: list[dict[str, Any]] = []
    for index in (1, 2):
        output = case_dir / f"tiny-{index}.exe"
        link_command = [str(compiler), "-static", LINK_FLAG, "main.o", "-o", output.name]
        link_execution = run(link_command, case_dir)
        require(link_execution["exit_code"] == 0, f"tiny link {index} failed for {arch}")
        timestamp = pe_timestamp(output)
        require(timestamp == 0, f"nonzero PE timestamp for {arch} link {index}: {timestamp}")
        links.append(
            {
                "execution": link_execution,
                "artifact": identity(output),
                "pe_coff_timestamp": timestamp,
            }
        )

    require(
        links[0]["artifact"]["sha256"] == links[1]["artifact"]["sha256"],
        f"same-architecture tiny links are not byte-identical for {arch}",
    )
    return {
        "arch": arch,
        "compile_flags": flags,
        "compile": compile_execution,
        "object": identity(obj),
        "links": links,
        "byte_identical": True,
    }


def normalize_temporary_paths(value: Any, temporary_root: Path) -> Any:
    """Remove per-run temporary directory names from the evidence payload."""
    if isinstance(value, dict):
        return {key: normalize_temporary_paths(item, temporary_root) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_temporary_paths(item, temporary_root) for item in value]
    if isinstance(value, str):
        return value.replace(str(temporary_root.resolve()), "<TEMP>").replace(
            temporary_root.resolve().as_posix(), "<TEMP>"
        )
    return value


def verify(args: argparse.Namespace) -> dict[str, Any]:
    started = utc_now()
    makefile = args.makefile.resolve()
    require(makefile.is_file(), f"Makefile is unavailable: {makefile}")
    make_program = executable(args.make_program, "make")
    compiler = executable(args.compiler, "g++")
    source_text = makefile.read_text(encoding="utf-8")

    require(source_text.count("mingw_reproducible = no") == 1, "default mode assignment is missing or duplicated")
    require(source_text.count("MINGW_REPRODUCIBLE_LINK_FLAG := " + LINK_FLAG) == 1, "link flag definition is missing or duplicated")
    require(source_text.count(LDFLAGS_LINE) == 1, "link flag routing is missing or duplicated")
    require(source_text.count("crazyhouse_mingw_reproducibility_probe:") == 1, "probe target is missing or duplicated")

    with tempfile.TemporaryDirectory(prefix="crazyhouse-mingw-repro-") as temp_name:
        temp_root = Path(temp_name)

        enabled_exec, enabled = invoke_probe(
            make_program,
            source_text,
            temp_root,
            "enabled",
            ["COMP=mingw", "OS=Windows_NT", "mingw_reproducible=yes"],
        )
        require(enabled_exec["exit_code"] == 0 and enabled is not None, "enabled probe failed")
        require(enabled == {
            "line": f"{PROBE_PREFIX} mode=yes target_windows=yes flag_count=1",
            "mode": "yes",
            "target_windows": "yes",
            "flag_count": 1,
        }, "enabled probe did not expose exactly one deterministic link flag")

        disabled_exec, disabled = invoke_probe(
            make_program,
            source_text,
            temp_root,
            "disabled",
            ["COMP=mingw", "OS=Windows_NT"],
        )
        require(disabled_exec["exit_code"] == 0 and disabled is not None, "disabled probe failed")
        require(disabled["mode"] == "no" and disabled["target_windows"] == "yes" and disabled["flag_count"] == 0,
                "ordinary Windows build routing changed")

        invalid_exec, invalid = invoke_probe(
            make_program,
            source_text,
            temp_root,
            "invalid-mode",
            ["COMP=mingw", "OS=Windows_NT", "mingw_reproducible=maybe"],
        )
        require(invalid is None and invalid_exec["exit_code"] != 0, "invalid mode was accepted")
        require("mingw_reproducible must be yes or no" in invalid_exec["stderr"], "invalid-mode failure was not semantic")

        non_windows_exec, non_windows = invoke_probe(
            make_program,
            source_text,
            temp_root,
            "non-windows",
            ["COMP=gcc", "OS=GNU/Linux", "mingw_reproducible=yes"],
        )
        require(non_windows is None and non_windows_exec["exit_code"] != 0, "non-Windows enabled mode was accepted")
        require("mingw_reproducible=yes requires target_windows=yes" in non_windows_exec["stderr"],
                "non-Windows failure was not semantic")

        missing_text = source_text.replace(LDFLAGS_LINE, "", 1)
        missing_exec, missing = invoke_probe(
            make_program,
            missing_text,
            temp_root,
            "mutation-missing",
            ["COMP=mingw", "OS=Windows_NT", "mingw_reproducible=yes"],
        )
        require(missing_exec["exit_code"] == 0 and missing is not None and missing["flag_count"] == 0,
                "missing-flag mutation did not produce the expected detectable defect")

        duplicate_text = source_text.replace(LDFLAGS_LINE, LDFLAGS_LINE * 2, 1)
        duplicate_exec, duplicate = invoke_probe(
            make_program,
            duplicate_text,
            temp_root,
            "mutation-duplicate",
            ["COMP=mingw", "OS=Windows_NT", "mingw_reproducible=yes"],
        )
        require(duplicate_exec["exit_code"] == 0 and duplicate is not None and duplicate["flag_count"] == 2,
                "duplicate-flag mutation did not produce the expected detectable defect")

        tiny_links = [
            tiny_link_case(compiler, temp_root, "x86-64", ["-m64", "-msse2"]),
            tiny_link_case(
                compiler,
                temp_root,
                "x86-64-avx2",
                ["-m64", "-msse2", "-mavx2", "-mbmi", "-msse4.1", "-mssse3", "-mpopcnt"],
            ),
        ]

        make_version = run([str(make_program), "--version"], temp_root)
        compiler_version = run([str(compiler), "--version"], temp_root)
        require(make_version["exit_code"] == 0, "make version query failed")
        require(compiler_version["exit_code"] == 0, "compiler version query failed")

    result = {
        "schema": SCHEMA,
        "status": "PASS",
        "started_utc": started,
        "completed_utc": utc_now(),
        "scope": "MAKE_ROUTING_AND_TINY_PE_LINK_ONLY",
        "release_reproducibility_claimed": False,
        "full_engine_reproducibility_claimed": False,
        "makefile": identity(makefile),
        "toolchain": {
            "make": identity(make_program),
            "make_version": make_version,
            "compiler": identity(compiler),
            "compiler_version": compiler_version,
        },
        "probes": {
            "enabled": {"execution": enabled_exec, "parsed": enabled},
            "disabled": {"execution": disabled_exec, "parsed": disabled},
            "invalid_mode": invalid_exec,
            "non_windows": non_windows_exec,
        },
        "mutation_controls": {
            "missing_flag": {"detected": True, "execution": missing_exec, "parsed": missing},
            "duplicate_flag": {"detected": True, "execution": duplicate_exec, "parsed": duplicate},
        },
        "tiny_pe_links": tiny_links,
    }
    return normalize_temporary_paths(result, temp_root)


def write_result(result: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
        return
    resolved = output.resolve()
    require(not resolved.exists(), f"refusing to rewrite existing receipt: {resolved}")
    require(resolved.parent.is_dir(), f"output parent does not exist: {resolved.parent}")
    partial = resolved.with_name(resolved.name + ".partial")
    require(not partial.exists(), f"partial output already exists: {partial}")
    try:
        partial.write_text(rendered, encoding="utf-8", newline="\n")
        os.replace(partial, resolved)
    finally:
        if partial.exists():
            partial.unlink()


def main() -> int:
    try:
        args = parse_args()
        result = verify(args)
        write_result(result, args.output)
        return 0
    except (OSError, VerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
