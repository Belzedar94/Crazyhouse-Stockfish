#!/usr/bin/env python3
"""Unit tests for the independent adapter-overhead verifier."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MEASURE = load(
    "crazyhouse_adapter_overhead_measure_unit",
    "tools/strength/measure_crazyhouse_local_panel_timing.py",
)
VERIFY = load(
    "crazyhouse_adapter_overhead_verify_unit",
    "tools/strength/verify_crazyhouse_local_adapter_overhead.py",
)


class StatisticsTests(unittest.TestCase):
    def test_independent_statistics_match_measurement_implementation(self) -> None:
        raw = [10.0, 11.0, 9.0, 12.0, 10.5]
        adapter = [10.1, 11.2, 9.1, 12.3, 10.4]
        deltas = [a - r for a, r in zip(adapter, raw)]
        self.assertEqual(
            VERIFY.percentile(deltas, 0.95),
            MEASURE.percentile_nearest_rank(deltas, 0.95),
        )
        self.assertEqual(VERIFY.mean_ucb99(deltas), MEASURE.one_sided_mean_ucb99(deltas))
        self.assertEqual(VERIFY.ratio_ucb99(adapter, raw), MEASURE.log_ratio_ucb99(adapter, raw))

    def test_recompute_rejects_a_normalized_transcript_mismatch(self) -> None:
        contract = {
            "adapter_overhead": {
                "expected_paired_searches": 2,
                "pass_limits": {
                    "median_delta_ms_max": 1.0,
                    "p95_delta_ms_max": 1.0,
                    "mean_delta_ms_ucb99_max": 2.0,
                    "geometric_mean_ratio_ucb99_max": 1.5,
                },
            }
        }
        raw = {
            "role": "raw_fairy",
            "searches": [
                {"id": "a", "elapsed_ms": 10.0, "normalized_sha256": "same"},
                {"id": "b", "elapsed_ms": 10.0, "normalized_sha256": "same"},
            ],
        }
        adapter = {
            "role": "adapter",
            "searches": [
                {"id": "a", "elapsed_ms": 10.1, "normalized_sha256": "same"},
                {"id": "b", "elapsed_ms": 10.1, "normalized_sha256": "different"},
            ],
        }
        result = VERIFY.recompute([raw, adapter], contract)
        self.assertFalse(result["checks"]["transcript_identity"])
        self.assertFalse(result["pass"])


class TranscriptTests(unittest.TestCase):
    def test_normalization_removes_only_declared_records_and_fields(self) -> None:
        lines = [
            "info string route_commit status=ok ruleset=crazyhouse backend=x",
            "info string crazyhouse_capability_ack status=ok nonce=x",
            "info depth 12 seldepth 18 score cp 7 nodes 100 time 5 nps 20000 hashfull 1 pv e2e4",
            "bestmove e2e4 ponder e7e5",
        ]
        expected = [
            "info depth 12 seldepth 18 score cp 7 nodes 100 pv e2e4",
            "bestmove e2e4 ponder e7e5",
        ]
        self.assertEqual(VERIFY.normalize(lines), expected)
        self.assertEqual(VERIFY.normalize(lines), MEASURE.normalize_search(lines))

    def test_search_extraction_is_fen_and_node_bound(self) -> None:
        records = [
            {"sequence": 0, "direction": "in", "line": "position fen board[] w - - 0 1"},
            {"sequence": 1, "direction": "in", "line": "go nodes 16"},
            {"sequence": 2, "direction": "out", "line": "info depth 1 nodes 16 pv e2e4"},
            {"sequence": 3, "direction": "out", "line": "bestmove e2e4"},
        ]
        self.assertEqual(
            VERIFY.extract_searches(records),
            [
                {
                    "nodes": 16,
                    "fen": "board[] w - - 0 1",
                    "output": ["info depth 1 nodes 16 pv e2e4", "bestmove e2e4"],
                }
            ],
        )


class SampleTests(unittest.TestCase):
    def test_frozen_sample_is_reconstructed_independently(self) -> None:
        contract = json.loads(
            (ROOT / "tests/crazyhouse/p7-local-adapter-overhead-v1.json").read_text(
                encoding="utf-8"
            )
        )
        corpus = ROOT / contract["inputs"]["corpus"]["path"]
        rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()]
        selected = VERIFY.select_sample(rows, contract)
        self.assertEqual(len(selected), 64)
        self.assertEqual(
            [row["accepted_index"] for row in selected],
            contract["sample"]["accepted_indices"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
