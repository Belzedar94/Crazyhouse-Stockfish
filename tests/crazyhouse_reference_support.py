"""Shared fail-closed support for the Crazyhouse differential references."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator


SCALACHESS_COMMIT = "cbffc9d7e2c6f8ba33381c5403e1b4f992199626"
SCALACHESS_TREE = "f5410eb2a6ddb6ef7092317533f704158c86a4fc"
SCALACHESS_GUARD_PATH = "test-kit/src/test/resources/crazyhouse.perft"
SCALACHESS_GUARD_BLOB = "cff0ba34a14120d7576f3ecade74a4ca6279e1eb"
SCALACHESS_ARCHIVE_BYTES = 6_563_840
SCALACHESS_ARCHIVE_SHA256 = "ea74b9ac4b9a6ab21b71b205f80ffbdeb3d0dce7bc228955282da6c3cd20418f"


class ScalachessExportFailure(RuntimeError):
    pass


@dataclass
class ScalachessExport:
    build_root: Path
    identity_root: Path
    archive_path: Path
    evidence: dict[str, object]
    cleanup_verified: bool = False
    identity_clean_after: bool = False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path | None = None) -> bytes:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ScalachessExportFailure(f"launch failed for {command[0]!r}: {exc}") from exc
    if completed.returncode != 0:
        diagnostic = (completed.stdout + b"\n" + completed.stderr).decode("utf-8", errors="replace")
        raise ScalachessExportFailure(
            f"command exited {completed.returncode}: {json.dumps(command)}\n{diagnostic}"
        )
    return completed.stdout


def _git(git: str, root: Path, *args: str) -> bytes:
    return _run([git, "-C", str(root), *args])


def _authenticate_identity(git: str, root: Path) -> dict[str, object]:
    resolved = root.resolve(strict=True)
    top = Path(_git(git, resolved, "rev-parse", "--show-toplevel").decode().strip()).resolve(strict=True)
    if top != resolved:
        raise ScalachessExportFailure(f"reference root {resolved} is not its Git toplevel {top}")
    commit = _git(git, resolved, "rev-parse", "HEAD").decode().strip()
    tree = _git(git, resolved, "rev-parse", "HEAD^{tree}").decode().strip()
    if commit != SCALACHESS_COMMIT or tree != SCALACHESS_TREE:
        raise ScalachessExportFailure(
            f"wrong scalachess identity: expected {SCALACHESS_COMMIT}/{SCALACHESS_TREE}, got {commit}/{tree}"
        )
    status = _git(git, resolved, "status", "--porcelain=v1", "-z")
    if status:
        raise ScalachessExportFailure("pinned scalachess checkout is not clean")
    guard_blob = _git(git, resolved, "rev-parse", f"HEAD:{SCALACHESS_GUARD_PATH}").decode().strip()
    if guard_blob != SCALACHESS_GUARD_BLOB:
        raise ScalachessExportFailure(
            f"wrong guard blob: expected {SCALACHESS_GUARD_BLOB}, got {guard_blob}"
        )
    guard_bytes = _git(git, resolved, "show", f"HEAD:{SCALACHESS_GUARD_PATH}")
    git_path = Path(shutil.which(git) or git).resolve(strict=True)
    git_version = _run([str(git_path), "--version"]).decode().strip()
    return {
        "root": str(resolved),
        "commit": commit,
        "tree": tree,
        "clean": True,
        "git_path": str(git_path),
        "git_sha256": _sha256_file(git_path),
        "git_version": git_version,
        "guard_path": SCALACHESS_GUARD_PATH,
        "guard_blob": guard_blob,
        "guard_bytes": len(guard_bytes),
        "guard_sha256": _sha256_bytes(guard_bytes),
    }


def _validate_archive_members(members: list[tarfile.TarInfo], export_root: Path) -> None:
    root = export_root.resolve()
    for member in members:
        logical = PurePosixPath(member.name)
        if logical.is_absolute() or ".." in logical.parts:
            raise ScalachessExportFailure(f"unsafe archive path: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise ScalachessExportFailure(f"unsupported archive entry type: {member.name!r}")
        destination = (root / Path(*logical.parts)).resolve()
        if destination != root and root not in destination.parents:
            raise ScalachessExportFailure(f"archive entry escapes export root: {member.name!r}")


@contextmanager
def authenticated_scalachess_export(
    reference_root: Path,
    scratch_root: Path,
    git: str = "git",
) -> Iterator[ScalachessExport]:
    identity = _authenticate_identity(git, reference_root)
    scratch = scratch_root.resolve()
    if scratch.exists():
        raise ScalachessExportFailure(f"export scratch path already exists: {scratch}")
    scratch.mkdir(parents=True, exist_ok=False)
    archive = scratch / "scalachess-source.tar"
    export_root = scratch / "scalachess-source"
    export_root.mkdir(exist_ok=False)

    archive_command = [
        str(identity["git_path"]),
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.eol=lf",
        "-C",
        str(identity["root"]),
        "archive",
        "--format=tar",
        "--output",
        str(archive),
        SCALACHESS_COMMIT,
    ]
    export = ScalachessExport(
        build_root=export_root,
        identity_root=Path(str(identity["root"])),
        archive_path=archive,
        evidence={},
    )
    try:
        _run(archive_command)
        if not archive.is_file() or archive.stat().st_size == 0:
            raise ScalachessExportFailure("git archive produced no nonempty tar file")
        archive_sha256 = _sha256_file(archive)
        if archive.stat().st_size != SCALACHESS_ARCHIVE_BYTES or archive_sha256 != SCALACHESS_ARCHIVE_SHA256:
            raise ScalachessExportFailure(
                "Git archive bytes differ from the independently reproduced pinned export"
            )
        with tarfile.open(archive, mode="r:") as source:
            members = source.getmembers()
            _validate_archive_members(members, export_root)
            source.extractall(export_root, filter="data")

        extracted_guard = export_root / Path(*PurePosixPath(SCALACHESS_GUARD_PATH).parts)
        if not extracted_guard.is_file():
            raise ScalachessExportFailure("guard file is missing from extracted Git archive")
        guard_bytes = extracted_guard.read_bytes()
        if len(guard_bytes) != identity["guard_bytes"] or _sha256_bytes(guard_bytes) != identity["guard_sha256"]:
            raise ScalachessExportFailure("extracted guard bytes differ from the pinned Git blob")

        export.evidence = {
            "schema": "crazyhouse-scalachess-export/v1",
            "identity": identity,
            "archive_bytes": archive.stat().st_size,
            "archive_sha256": archive_sha256,
            "archive_matches_pinned_export": True,
            "archive_members": len(members),
            "archive_regular_files": sum(member.isfile() for member in members),
            "archive_directories": sum(member.isdir() for member in members),
            "nonregular_entries": 0,
            "extracted_guard_matches_blob": True,
            "build_root_is_git_checkout": (export_root / ".git").exists(),
        }
        yield export
    finally:
        if export_root.parent != scratch or archive.parent != scratch:
            raise ScalachessExportFailure("cleanup targets escaped the dedicated export scratch root")
        if export_root.exists():
            shutil.rmtree(export_root)
        if archive.exists():
            archive.unlink()
        if scratch.exists():
            scratch.rmdir()
        export.cleanup_verified = not export_root.exists() and not archive.exists() and not scratch.exists()
        after = _authenticate_identity(git, reference_root)
        export.identity_clean_after = bool(after["clean"])
        if not export.cleanup_verified or not export.identity_clean_after:
            raise ScalachessExportFailure("export cleanup or post-run reference authentication failed")
