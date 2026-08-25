#!/usr/bin/env python3

from __future__ import annotations

from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = load_module(
    "crazyhouse_openbench_clean_exports_runner",
    ROOT / "tools" / "ci" / "run_crazyhouse_openbench_clean_exports.py",
)
VERIFIER = load_module(
    "crazyhouse_openbench_clean_exports_verifier",
    ROOT / "tools" / "ci" / "verify_crazyhouse_openbench_clean_exports.py",
)


class CrazyhouseOpenBenchCleanExportTests(unittest.TestCase):
    def test_file_record_authenticates_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(b"crazyhouse\x00fixture")
            record = RUNNER.file_record(path)
            self.assertEqual(record["bytes"], 18)
            self.assertEqual(record["sha256"], RUNNER.sha256_file(path))
            self.assertEqual(VERIFIER.authenticate(record), path.resolve())

    def test_authenticator_rejects_post_record_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(b"before")
            record = RUNNER.file_record(path)
            path.write_bytes(b"after")
            with self.assertRaises(VERIFIER.VerificationError):
                VERIFIER.authenticate(record)

    def test_json_writer_is_single_use_and_utf8_lf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            RUNNER.write_json_new(path, {"value": "Crazyhouse"})
            self.assertEqual(path.read_bytes(), b'{\n  "value": "Crazyhouse"\n}\n')
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "Crazyhouse")
            with self.assertRaises(FileExistsError):
                RUNNER.write_json_new(path, {"value": "replacement"})

    def test_git_style_archive_extract_and_manifest(self) -> None:
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "source.tar"
            payload = b"frozen\n"
            with tarfile.open(
                archive,
                "w",
                format=tarfile.PAX_FORMAT,
                pax_headers={"comment": commit},
            ) as output:
                member = tarfile.TarInfo("fixture.txt")
                member.size = len(payload)
                output.addfile(member, BytesIO(payload))
            root = base / "export"
            RUNNER.extract_archive(archive, root, commit)
            entries = RUNNER.source_entries(root, ["fixture.txt"])
            self.assertEqual(entries[0]["bytes"], len(payload))
            self.assertEqual((root / "fixture.txt").read_bytes(), payload)
            (root / "generated.o").write_bytes(b"generated")
            with self.assertRaises(RUNNER.LeaseError):
                RUNNER.source_entries(root, ["fixture.txt"])
            self.assertEqual(
                RUNNER.source_entries(root, ["fixture.txt"], require_exact_inventory=False),
                entries,
            )

    def test_archive_commit_and_process_ownership_fail_closed(self) -> None:
        commit = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "source.tar"
            with tarfile.open(
                archive,
                "w",
                format=tarfile.PAX_FORMAT,
                pax_headers={"comment": commit},
            ) as output:
                payload = b"x"
                member = tarfile.TarInfo("fixture.txt")
                member.size = len(payload)
                output.addfile(member, BytesIO(payload))
            with self.assertRaises(RUNNER.LeaseError):
                RUNNER.extract_archive(archive, base / "wrong-export", "c" * 40)
        snapshot = RUNNER.process_snapshot([os.getpid()])
        self.assertEqual(snapshot[0]["pid"], os.getpid())
        self.assertTrue(snapshot[0]["alive"])


if __name__ == "__main__":
    unittest.main()
