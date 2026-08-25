#!/usr/bin/env python3
"""Mutation tests for the fail-closed Crazyhouse release-workflow guard."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "release" / "verify_crazyhouse_release_workflow_guard.py"
CONTRACT_PATH = ROOT / "tests" / "crazyhouse" / "p15-release-workflow-guard-v1.json"

SPEC = importlib.util.spec_from_file_location("crazyhouse_release_workflow_guard", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load release-workflow guard verifier")
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class ReleaseWorkflowGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = GUARD.load_contract(CONTRACT_PATH)
        cls.canonical = GUARD.canonical_payload(cls.contract)

    def run_fixture(self, workflow: bytes, contract: bytes | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-release-guard-") as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            workflow_path = root / "official_release.yml"
            contract_path.write_bytes(contract if contract is not None else CONTRACT_PATH.read_bytes())
            workflow_path.write_bytes(workflow)
            return GUARD.verify(contract_path, workflow_path)

    def assert_rejected(self, workflow: bytes, contract: bytes | None = None) -> str:
        with self.assertRaises(GUARD.GuardError) as raised:
            self.run_fixture(workflow, contract)
        return str(raised.exception)

    def test_canonical_guard_passes(self) -> None:
        result = self.run_fixture(self.canonical)
        self.assertEqual(result["status"], "PASS_RELEASE_WORKFLOW_GUARD")
        self.assertEqual(result["github_writes"], 0)

    def test_each_forbidden_fragment_is_rejected_and_named(self) -> None:
        for fragment in self.contract["forbidden_fragments"]:
            with self.subTest(fragment=fragment):
                payload = self.canonical + f"# {fragment}\n".encode("utf-8")
                message = self.assert_rejected(payload)
                self.assertIn(fragment, message)

    def test_single_byte_drift_is_rejected(self) -> None:
        payload = bytearray(self.canonical)
        payload[0] = ord("N")
        self.assert_rejected(bytes(payload))

    def test_crlf_is_rejected(self) -> None:
        self.assert_rejected(self.canonical.replace(b"\n", b"\r\n"))

    def test_utf8_bom_is_rejected(self) -> None:
        self.assert_rejected(b"\xef\xbb\xbf" + self.canonical)

    def test_missing_trailing_lf_is_rejected(self) -> None:
        self.assert_rejected(self.canonical[:-1])

    def test_linked_workflow_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-release-guard-link-") as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            original = root / "original.yml"
            linked = root / "official_release.yml"
            contract_path.write_bytes(CONTRACT_PATH.read_bytes())
            original.write_bytes(self.canonical)
            os.link(original, linked)
            with self.assertRaises(GUARD.GuardError):
                GUARD.verify(contract_path, linked)

    def test_duplicate_contract_key_is_rejected(self) -> None:
        value = CONTRACT_PATH.read_text(encoding="utf-8")
        duplicate = value.replace(
            '  "project": "Crazyhouse-Stockfish",\n',
            '  "project": "Crazyhouse-Stockfish",\n  "project": "Crazyhouse-Stockfish",\n',
            1,
        ).encode("utf-8")
        self.assert_rejected(self.canonical, duplicate)

    def test_contract_digest_drift_is_rejected(self) -> None:
        value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        value["canonical_guard"]["sha256"] = "0" * 64
        payload = (json.dumps(value, indent=2) + "\n").encode("utf-8")
        self.assert_rejected(self.canonical, payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
