#!/usr/bin/env python3
"""Unit tests for the result-blind Crazyhouse local strength tooling."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "strength" / "run_crazyhouse_local_strength_panel.py"
VERIFIER_PATH = ROOT / "tools" / "strength" / "verify_crazyhouse_local_strength_panel.py"
OVERHEAD_PATH = ROOT / "tools" / "strength" / "measure_crazyhouse_local_panel_timing.py"
SPEC = importlib.util.spec_from_file_location("crazyhouse_local_panel", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PANEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PANEL
SPEC.loader.exec_module(PANEL)
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "crazyhouse_local_panel_verify", VERIFIER_PATH
)
assert VERIFY_SPEC is not None and VERIFY_SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
sys.modules[VERIFY_SPEC.name] = VERIFY
VERIFY_SPEC.loader.exec_module(VERIFY)
OVERHEAD_SPEC = importlib.util.spec_from_file_location(
    "crazyhouse_local_adapter_overhead", OVERHEAD_PATH
)
assert OVERHEAD_SPEC is not None and OVERHEAD_SPEC.loader is not None
OVERHEAD = importlib.util.module_from_spec(OVERHEAD_SPEC)
sys.modules[OVERHEAD_SPEC.name] = OVERHEAD
OVERHEAD_SPEC.loader.exec_module(OVERHEAD)


def formal_host(
    contract: dict[str, object],
    *,
    schema: str,
    result: str,
    effective_time: datetime,
) -> dict[str, object]:
    empty_snapshot = {"total_processes": 300, "foreign": [], "crazyhouse": []}
    return {
        "schema": schema,
        "captured_utc": (effective_time - timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "valid_until_utc": (effective_time + timedelta(minutes=4)).isoformat().replace(
            "+00:00", "Z"
        ),
        "dry_run": False,
        "result": result,
        "producer": contract["host_precondition"]["attestation_producer"],
        "foreign_processes_mutated": False,
        "command_lines_recorded": False,
        "maximum_cpu_percent": 5.0,
        "requested_sample_seconds": 60,
        "process_snapshot_before": empty_snapshot,
        "process_snapshot_after": empty_snapshot,
        "cpu_summary": {
            "count": 60,
            "maximum": 1.0,
            "every_sample_strictly_below_limit": True,
        },
        "host": {"priority_or_affinity_changed": False},
    }


class HistoricalLosTests(unittest.TestCase):
    def test_matches_published_atomic_reference_row(self) -> None:
        stats = PANEL.historical_wld_statistics([50, 22, 38])
        self.assertTrue(stats["available"])
        self.assertAlmostEqual(stats["elo"], 90.4260936070, places=8)
        self.assertEqual(stats["display_los_percent"], "100.0")

    def test_swapping_wins_and_losses_reaches_lower_endpoint(self) -> None:
        stats = PANEL.historical_wld_statistics([22, 50, 38])
        self.assertEqual(stats["display_los_percent"], "0.0")

    def test_zero_variance_does_not_invent_a_historical_display(self) -> None:
        stats = PANEL.historical_wld_statistics([50, 0, 0])
        self.assertFalse(stats["available"])
        self.assertIsNone(stats["display_los_percent"])

    def test_minimum_is_inclusive_and_pair_safe(self) -> None:
        execution = {
            "minimum_games_before_los_decision": 50,
            "maximum_games_per_rung": 2048,
        }
        early = PANEL.endpoint_decision(
            {
                "games": 48,
                "statistics_historical_wld_method": PANEL.historical_wld_statistics(
                    [22, 2, 24]
                ),
            },
            execution,
        )
        eligible = PANEL.endpoint_decision(
            {
                "games": 50,
                "statistics_historical_wld_method": PANEL.historical_wld_statistics(
                    [24, 2, 24]
                ),
            },
            execution,
        )
        self.assertFalse(early["eligible"])
        self.assertIsNone(early["endpoint"])
        self.assertTrue(eligible["eligible"])
        self.assertEqual(eligible["endpoint"], "100.0")
        self.assertTrue(eligible["rung_pass"])


class PairAndBatchTests(unittest.TestCase):
    def test_batch_plan_starts_at_50_then_uses_16_and_hits_cap_exactly(self) -> None:
        execution = {
            "initial_batch_games": 50,
            "continuation_batch_games": 16,
            "maximum_games_per_rung": 2048,
        }
        self.assertEqual(PANEL.rung_batch_size(0, execution), 50)
        self.assertEqual(PANEL.rung_batch_size(50, execution), 16)
        self.assertEqual(PANEL.rung_batch_size(2034, execution), 14)

    def test_tracker_requires_swapped_colours(self) -> None:
        tracker = PANEL.BatchTracker(2)
        tracker.consume(
            "Finished game 1 (candidate vs fairy-adapted): 1-0 {White mates}"
        )
        tracker.consume(
            "Finished game 2 (fairy-adapted vs candidate): 0-1 {Black mates}"
        )
        records = tracker.require_complete()
        self.assertEqual(len(records), 2)
        aggregate = PANEL.RungAccumulator()
        aggregate.add_batch(
            batch_index=0,
            opening_start=1,
            records=records,
            defects=tracker.defects,
        )
        snapshot = aggregate.snapshot()
        self.assertEqual(snapshot["wld_candidate_pov"], [2, 0, 0])
        self.assertEqual(snapshot["pentanomial_candidate_pov"], [0, 0, 0, 0, 1])

    def test_timeloss_is_a_defect_not_a_normal_loss(self) -> None:
        tracker = PANEL.BatchTracker(2)
        tracker.consume(
            "Finished game 1 (candidate vs fairy-adapted): 0-1 {White loses on time}"
        )
        tracker.consume(
            "Finished game 2 (fairy-adapted vs candidate): 1-0 {Black loses on time}"
        )
        tracker.require_complete()
        self.assertEqual(len(tracker.defects), 2)


class TranscriptParserTests(unittest.TestCase):
    def test_real_debug_prefix_and_line_endings_are_admitted(self) -> None:
        first = "0123456789abcdef0123456789abcdef"
        second = "fedcba9876543210fedcba9876543210"
        transcript = (
            "742 >candidate(0): setoption name CrazyhouseCapabilityNonce value "
            f"{first}\r\n"
            "743 <candidate(0): info string crazyhouse_capability_ack status=ok "
            f"profile=x nonce={first}\r\n"
            ">fairy-adapted(1): setoption name CrazyhouseCapabilityNonce value "
            f"{second}\n"
            "<fairy-adapted(1): info string crazyhouse_capability_ack status=ok "
            f"profile=x nonce={second}\n"
        )
        for nonce_set, nonce_ack in (
            (PANEL.NONCE_SET_RE, PANEL.NONCE_ACK_RE),
            (VERIFY.NONCE_SET, VERIFY.NONCE_ACK),
        ):
            self.assertEqual(nonce_set.findall(transcript), [first, second])
            self.assertEqual(nonce_ack.findall(transcript), [first, second])


class IndependentVerifierTests(unittest.TestCase):
    def test_statistics_are_independently_identical(self) -> None:
        for wld in ([50, 22, 38], [22, 50, 38], [66, 33, 85], [25, 10, 15]):
            self.assertEqual(
                PANEL.historical_wld_statistics(list(wld)),
                VERIFY.historical_stats(list(wld)),
            )
        for penta in ([2, 7, 17, 19, 10], [0, 3, 20, 27, 4], [4, 3, 2, 1, 0]):
            self.assertEqual(
                PANEL.openbench_statistics(list(penta)),
                VERIFY.openbench_stats(list(penta)),
            )

    def test_verifier_reconstructs_the_exact_runner_command(self) -> None:
        asset = MODULE_PATH.resolve()
        batch_dir = (ROOT / "never-created" / "batch-0000").resolve()
        contract = {
            "inputs": {
                role: {"path": str(asset)}
                for role in (
                    "candidate",
                    "adapter",
                    "raw_fairy",
                    "network",
                    "referee",
                    "book",
                )
            },
            "engine_settings": {
                "time_margin_ms": 250,
                "threads": 1,
                "hash_mib": 64,
                "move_overhead_ms": 25,
            },
            "execution": {"concurrency": 8},
            "adjudication": {"safety_max_fullmoves": 400},
            "reporting": {
                "rating_interval_games": 50,
                "outcome_interval_games": 2,
            },
        }
        paths = {role: asset for role in contract["inputs"]}
        runner_command = PANEL.build_command(
            paths=paths,
            tc="2+0.02",
            contract=contract,
            batch_dir=batch_dir,
            games=50,
            opening_start=1,
            seed=2026082301,
            event="Crazyhouse local LOS gate vstc-2s batch 0",
            debug=False,
        )
        verifier_command = VERIFY.expected_command(
            contract=contract,
            batch_dir=batch_dir,
            tc="2+0.02",
            games=50,
            opening_start=1,
            seed=2026082301,
            event="Crazyhouse local LOS gate vstc-2s batch 0",
            debug=False,
        )
        self.assertEqual(runner_command, verifier_command)


class HostAttestationTests(unittest.TestCase):
    def test_runner_verifier_and_overhead_accept_same_fresh_producer(self) -> None:
        now = datetime.now(timezone.utc)
        panel_contract = json.loads(
            (ROOT / "tests/crazyhouse/p7-local-strength-panel-v2.json").read_text(
                encoding="utf-8"
            )
        )
        strength_host = formal_host(
            panel_contract,
            schema="crazyhouse-host-strength-attestation/v1",
            result="PASS_HOST_STRENGTH_READY",
            effective_time=now,
        )
        runner = PANEL.validate_host_attestation(
            strength_host,
            panel_contract,
            ROOT,
            expected_schema="crazyhouse-host-strength-attestation/v1",
            expected_result="PASS_HOST_STRENGTH_READY",
            effective_time=now,
        )
        verifier = VERIFY.validate_host_attestation(
            strength_host,
            panel_contract,
            ROOT,
            expected_schema="crazyhouse-host-strength-attestation/v1",
            expected_result="PASS_HOST_STRENGTH_READY",
            effective_time=now,
        )
        self.assertEqual(runner, verifier)

        overhead_contract = json.loads(
            (ROOT / "tests/crazyhouse/p7-local-adapter-overhead-v1.json").read_text(
                encoding="utf-8"
            )
        )
        timing_host = formal_host(
            overhead_contract,
            schema="crazyhouse-host-timing-attestation/v1",
            result="PASS_HOST_TIMING_CLEAN",
            effective_time=now,
        )
        overhead = OVERHEAD.validate_host_attestation(
            timing_host,
            overhead_contract,
            ROOT,
            effective_time=now,
        )
        self.assertEqual(overhead["valid_until_utc"], timing_host["valid_until_utc"])

    def test_expired_attestation_fails_closed(self) -> None:
        now = datetime.now(timezone.utc)
        contract = json.loads(
            (ROOT / "tests/crazyhouse/p7-local-strength-panel-v2.json").read_text(
                encoding="utf-8"
            )
        )
        host = formal_host(
            contract,
            schema="crazyhouse-host-strength-attestation/v1",
            result="PASS_HOST_STRENGTH_READY",
            effective_time=now - timedelta(minutes=10),
        )
        with self.assertRaisesRegex(RuntimeError, "not valid at run start"):
            PANEL.validate_host_attestation(
                host,
                contract,
                ROOT,
                expected_schema="crazyhouse-host-strength-attestation/v1",
                expected_result="PASS_HOST_STRENGTH_READY",
                effective_time=now,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
