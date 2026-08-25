#!/usr/bin/env python3
"""Unit tests for the public Crazyhouse CI artifact aggregator."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.ci.aggregate_crazyhouse_ci_artifacts import (
    AggregationError,
    aggregate,
    inventory_tree,
    write_fresh,
)


class ArtifactAggregateTests(unittest.TestCase):
    def test_inventory_is_sorted_and_hashes_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z").mkdir()
            (root / "z" / "last.log").write_bytes(b"last")
            (root / "first.log").write_bytes(b"first")
            records = inventory_tree(root)
            self.assertEqual([record["path"] for record in records], ["first.log", "z/last.log"])
            self.assertEqual(records[0]["bytes"], 5)
            self.assertEqual(records[0]["sha256"], hashlib.sha256(b"first").hexdigest())

    def test_empty_inventory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AggregationError):
                inventory_tree(Path(directory))

    def test_network_bytes_cannot_leak_into_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "Crazyhouse_v1.nnue"
            network.write_bytes(b"forbidden")
            with self.assertRaisesRegex(AggregationError, "network leaked"):
                inventory_tree(Path(directory))

    def test_write_fresh_never_replaces_owner_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            output.write_text("owner-data", encoding="utf-8")
            with self.assertRaises(AggregationError):
                write_fresh(output, "replacement")
            self.assertEqual(output.read_text(encoding="utf-8"), "owner-data")

    def test_aggregate_binds_current_official_descendant(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            expected = ["lane-a", "lane-b"]
            for lane in expected:
                lane_root = artifacts / lane
                lane_root.mkdir()
                (lane_root / "result.log").write_text(f"PASS {lane}\n", encoding="utf-8")
            output = root / "aggregate"
            manifest = aggregate(repository, artifacts, output, expected)
            self.assertEqual(manifest["result"], "PASS_REQUIRED_PUBLIC_CORRECTNESS_JOBS")
            self.assertTrue(manifest["repository"]["official_stockfish_ancestor_verified"])
            self.assertFalse(manifest["repository"]["fairy_stockfish_source_allowed"])
            self.assertTrue((output / "crazyhouse-correctness-manifest.json").is_file())
            self.assertTrue((output / "SHA256SUMS").is_file())


if __name__ == "__main__":
    unittest.main()
