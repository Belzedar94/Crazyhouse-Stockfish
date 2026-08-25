#!/usr/bin/env python3
"""Verify bounded Crazyhouse worker search through the exact legacy backend."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crazyhouse_uci_routing_verify import (
    PROFILE_TOKEN,
    UciProcess,
    VerificationFailure,
    require,
    setoption,
    sha256_file,
    wait_ready_success,
)


def load_evaluator_context(contract_path: Path, addendum_path: Path | None) -> dict:
    if addendum_path is None:
        return {
            "mode": "full-refresh",
            "summary": "backend=legacy-v1 full_refresh=PASS strength_claim=false",
            "addendum": None,
        }

    require(addendum_path.is_file(), f"missing input: {addendum_path}")
    addendum = json.loads(addendum_path.read_text(encoding="utf-8"))
    require(
        addendum["schema"] == "crazyhouse-worker-search-contract-addendum/v1",
        "Worker addendum schema mismatch",
    )
    require(addendum["addendum"] in (1, 2, 3, 4, 5, 6, 7, 8), "Worker addendum number mismatch")

    base_pin = addendum["pins"]["worker_search_contract_v1"]
    require(contract_path.stat().st_size == base_pin["bytes"], "Worker base contract size mismatch")
    require(
        sha256_file(contract_path) == base_pin["sha256"],
        "Worker base contract SHA-256 mismatch",
    )
    if addendum["addendum"] == 8:
        require(addendum_path.stat().st_size == 5420, "Worker addendum 008 size mismatch")
        require(
            sha256_file(addendum_path)
            == "6a79595ee499257175f129cda4bbf61c9cbc690ba840e9026eacc0e9c386fe85",
            "Worker addendum 008 SHA-256 mismatch",
        )
        source_root = addendum_path.resolve().parents[2]
        prior_pin = addendum["pins"]["worker_search_addendum_007"]
        prior_path = addendum_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), "Worker addendum 007 missing")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "Worker addendum 007 identity mismatch",
        )
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        require(
            prior["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
            and prior["addendum"] == 7
            and prior["pins"]["worker_search_contract_v1"] == base_pin,
            "Worker addendum 008 predecessor mismatch",
        )

        expected_red_pin = addendum["pins"]["rule_only_expected_red_record"]
        expected_red_path = (addendum_path.parent / expected_red_pin["path"]).resolve()
        require(expected_red_path.is_file(), "rule-only expected-red record missing")
        require(
            expected_red_path.stat().st_size == expected_red_pin["bytes"]
            and sha256_file(expected_red_path) == expected_red_pin["sha256"],
            "rule-only expected-red record identity mismatch",
        )
        expected_red_record = json.loads(expected_red_path.read_text(encoding="utf-8"))
        require(
            expected_red_record["record_id"] == 148
            and expected_red_record["result"] == "PASS_EXPECTED_RED"
            and expected_red_record["observed"]["rule_only_perft_allowed"] is False
            and expected_red_record["observed"]["search_error"]
            == "legacy_simd_unavailable"
            and expected_red_record["observed"]["scalar_fallback"] is False,
            "rule-only expected-red observation mismatch",
        )

        rebase = addendum["source_rebase"]
        require(
            rebase["descendant_commit"]
            == "2e64efd6c32abaa6740494d48d80c9c85534afa7"
            and rebase["descendant_tree"]
            == "239371fd309e04ffa2aabd7d79cf010e15438e3d"
            and rebase["official_stockfish_ancestor"]
            == "229f6339e537a097a79831cd06dbfdb3e623d4ac"
            and rebase["official_stockfish_ancestor_verified"] is True
            and rebase["fairy_stockfish_source_allowed"] is False
            and rebase["changed_source_pin_count"] == 1
            and rebase["changed_source_pin_path"] == "src/uci.cpp",
            "rule-only source-rebase identity mismatch",
        )
        require(
            rebase["replaced_pin"]["before"]
            == prior["source_rebase"]["replaced_pin"]["after"],
            "rule-only source-rebase before-pin mismatch",
        )
        after_pin = rebase["replaced_pin"]["after"]
        require(after_pin["path"] == "src/uci.cpp", "rule-only source-rebase path mismatch")

        addendum_006_pin = prior["pins"]["worker_search_addendum_006"]
        addendum_006_path = addendum_path.parent / Path(addendum_006_pin["path"]).name
        require(addendum_006_path.is_file(), "Worker addendum 006 missing from rule-only chain")
        require(
            addendum_006_path.stat().st_size == addendum_006_pin["bytes"]
            and sha256_file(addendum_006_path) == addendum_006_pin["sha256"],
            "Worker addendum 006 rule-only-chain identity mismatch",
        )
        addendum_006 = json.loads(addendum_006_path.read_text(encoding="utf-8"))
        addendum_005_pin = addendum_006["pins"]["worker_search_addendum_005"]
        addendum_005_path = addendum_path.parent / Path(addendum_005_pin["path"]).name
        require(addendum_005_path.is_file(), "Worker addendum 005 missing from rule-only chain")
        require(
            addendum_005_path.stat().st_size == addendum_005_pin["bytes"]
            and sha256_file(addendum_005_path) == addendum_005_pin["sha256"],
            "Worker addendum 005 rule-only-chain identity mismatch",
        )
        base_source = json.loads(addendum_005_path.read_text(encoding="utf-8"))
        require(base_source["addendum"] == 5, "Worker rule-only-chain base number mismatch")
        for pin in base_source["source_line"]["source_pins"]:
            if pin["path"] == "src/uci.cpp":
                continue
            source_path = source_root / pin["path"]
            require(source_path.is_file(), f"rule-only source pin missing: {source_path}")
            require(
                source_path.stat().st_size == pin["bytes"]
                and sha256_file(source_path) == pin["sha256"],
                f"rule-only unchanged source identity mismatch: {pin['path']}",
            )
        uci_path = source_root / after_pin["path"]
        require(uci_path.is_file(), f"rule-only UCI source missing: {uci_path}")
        require(
            uci_path.stat().st_size == after_pin["bytes"]
            and sha256_file(uci_path) == after_pin["sha256"],
            "rule-only UCI source identity mismatch",
        )

        behavior = rebase["exact_behavior_correction"]
        require(
            behavior["preserved_no_active_route_position_error"]
            == "position_requires_committed_route"
            and behavior["active_failed_backend_position_semantics"]
            == "rule-only route admitted"
            and behavior["active_failed_backend_perft_semantics"]
            == "rule-only perft admitted"
            and behavior["active_failed_backend_search_error"] == "stored activeError"
            and behavior["required_unavailable_simd_error"]
            == "legacy_simd_unavailable"
            and behavior["isready_retry_semantics_changed"] is False
            and behavior["worker_evaluator_semantics_changed"] is False
            and behavior["worker_search_semantics_changed"] is False
            and behavior["routing_success_digest_changed"] is False
            and behavior["fallback_allowed"] is False
            and behavior["strength_claim"] is False,
            "rule-only behavior-correction boundary mismatch",
        )
        transition = addendum["single_variable_transition"]
        require(
            transition["from"]["worker_mode"] == transition["to"]["worker_mode"]
            == "INCREMENTAL_SCALAR"
            and transition["from"]["route_commit_evaluator_field"]
            == transition["to"]["route_commit_evaluator_field"]
            == "evaluator=incremental-scalar"
            and transition["from"]["uci_source_sha256"]
            == rebase["replaced_pin"]["before"]["sha256"]
            and transition["to"]["uci_source_sha256"] == after_pin["sha256"]
            and transition["to"]["failed_evaluator_search"] == "stored activeError"
            and transition["to"]["build_selector_supplied"] is False
            and transition["to"]["make_default"] == "CRAZYHOUSE_LEGACY_BACKEND=scalar"
            and transition["to"]["fallback_allowed"] is False,
            "rule-only Worker transition mismatch",
        )
        expected_red = addendum["expected_red"]
        require(
            expected_red["record"] == 148
            and expected_red["rule_only_perft_allowed"] is False
            and expected_red["search_error_preserved"] is True
            and expected_red["engine_started"] is True,
            "rule-only expected-red contract mismatch",
        )
        runtime = addendum["runtime_replay"]
        expected_summary = (
            "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
            "evaluator=incremental-scalar route_telemetry=PASS worker_binding=PASS "
            "scalar_default_unchanged=true strength_claim=false"
        )
        require(
            runtime["base_cases_unchanged"] is True
            and runtime["case_count"] == 2
            and runtime["runs"] == 4
            and runtime["route_backend"] == "legacy-v1"
            and runtime["route_identity_sha256"]
            == "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
            and runtime["required_route_token"] == "evaluator=incremental-scalar"
            and runtime["forbidden_route_tokens"]
            == ["evaluator=incremental-simd", "simd_backend="]
            and runtime["required_worker_summary"] == expected_summary
            and runtime["source_to_binary_binding_required"] is True
            and runtime["same_target_routing_correction_required"] is True
            and runtime["unavailable_simd_negative_required"] is True
            and runtime["exact_standard_control_required"] is True,
            "rule-only Worker replay contract mismatch",
        )
        return {
            "mode": "incremental-scalar",
            "summary": expected_summary.removeprefix(
                "PASS crazyhouse_worker_search cases=2 runs=4 "
            ),
            "required_route_token": runtime["required_route_token"],
            "forbidden_route_tokens": runtime["forbidden_route_tokens"],
            "telemetry_label": "incremental scalar",
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256_file(addendum_path),
            },
        }
    if addendum["addendum"] == 7:
        require(addendum_path.stat().st_size == 5267, "Worker addendum 007 size mismatch")
        require(
            sha256_file(addendum_path)
            == "e8c5ae2afec63ff4f1f30017334bf5338267b219bdefb91ff7676689802d6b91",
            "Worker addendum 007 SHA-256 mismatch",
        )
        source_root = addendum_path.resolve().parents[2]
        prior_pin = addendum["pins"]["worker_search_addendum_006"]
        prior_path = addendum_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), "Worker addendum 006 missing")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "Worker addendum 006 identity mismatch",
        )
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        require(
            prior["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
            and prior["addendum"] == 6
            and prior["pins"]["worker_search_contract_v1"] == base_pin,
            "Worker addendum 007 predecessor mismatch",
        )

        for name in ("overbroad_route_end_receipt", "overbroad_route_rejection_record"):
            pin = addendum["pins"][name]
            receipt_path = (addendum_path.parent / pin["path"]).resolve()
            require(receipt_path.is_file(), f"scoped-route receipt missing: {receipt_path}")
            require(
                receipt_path.stat().st_size == pin["bytes"]
                and sha256_file(receipt_path) == pin["sha256"],
                f"scoped-route receipt identity mismatch: {name}",
            )

        rebase = addendum["source_rebase"]
        require(
            rebase["descendant_commit"]
            == "b6f756a93a8f0ce0ea21afdff5bdbb32c8c428ad"
            and rebase["descendant_tree"]
            == "d7956e1ee0ca7abb3714e3fae4171ac3493e614f"
            and rebase["official_stockfish_ancestor"]
            == "229f6339e537a097a79831cd06dbfdb3e623d4ac"
            and rebase["official_stockfish_ancestor_verified"] is True
            and rebase["fairy_stockfish_source_allowed"] is False
            and rebase["changed_source_pin_count"] == 1
            and rebase["changed_source_pin_path"] == "src/uci.cpp",
            "scoped-route source-rebase identity mismatch",
        )
        require(
            rebase["replaced_pin"]["before"]
            == prior["source_rebase"]["replaced_pin"]["after"],
            "scoped-route source-rebase before-pin mismatch",
        )
        after_pin = rebase["replaced_pin"]["after"]
        require(after_pin["path"] == "src/uci.cpp", "scoped-route source-rebase path mismatch")

        base_source_pin = prior["pins"]["worker_search_addendum_005"]
        base_source_path = addendum_path.parent / Path(base_source_pin["path"]).name
        require(base_source_path.is_file(), "Worker addendum 005 missing from scoped chain")
        require(
            base_source_path.stat().st_size == base_source_pin["bytes"]
            and sha256_file(base_source_path) == base_source_pin["sha256"],
            "Worker addendum 005 scoped-chain identity mismatch",
        )
        base_source = json.loads(base_source_path.read_text(encoding="utf-8"))
        require(base_source["addendum"] == 5, "Worker scoped-chain base number mismatch")
        for pin in base_source["source_line"]["source_pins"]:
            if pin["path"] == "src/uci.cpp":
                continue
            source_path = source_root / pin["path"]
            require(source_path.is_file(), f"scoped-route source pin missing: {source_path}")
            require(
                source_path.stat().st_size == pin["bytes"]
                and sha256_file(source_path) == pin["sha256"],
                f"scoped-route unchanged source identity mismatch: {pin['path']}",
            )
        uci_path = source_root / after_pin["path"]
        require(uci_path.is_file(), f"scoped-route UCI source missing: {uci_path}")
        require(
            uci_path.stat().st_size == after_pin["bytes"]
            and sha256_file(uci_path) == after_pin["sha256"],
            "scoped-route UCI source identity mismatch",
        )

        behavior = rebase["exact_behavior_refinement"]
        require(
            behavior["preserved_no_active_route_position_error"]
            == "position_requires_committed_route"
            and behavior["active_failed_backend_position_error"] == "stored activeError"
            and behavior["active_failed_backend_search_error"] == "stored activeError"
            and behavior["active_failed_backend_perft_error"] == "stored activeError"
            and behavior["required_unavailable_simd_error"] == "legacy_simd_unavailable"
            and behavior["isready_retry_semantics_changed"] is False
            and behavior["worker_evaluator_semantics_changed"] is False
            and behavior["worker_search_semantics_changed"] is False
            and behavior["routing_success_digest_changed"] is False
            and behavior["fallback_allowed"] is False
            and behavior["strength_claim"] is False,
            "scoped-route behavior-refinement boundary mismatch",
        )
        transition = addendum["single_variable_transition"]
        require(
            transition["from"]["worker_mode"] == transition["to"]["worker_mode"]
            == "INCREMENTAL_SCALAR"
            and transition["from"]["route_commit_evaluator_field"]
            == transition["to"]["route_commit_evaluator_field"]
            == "evaluator=incremental-scalar"
            and transition["from"]["uci_source_sha256"]
            == rebase["replaced_pin"]["before"]["sha256"]
            and transition["to"]["uci_source_sha256"] == after_pin["sha256"]
            and transition["to"]["build_selector_supplied"] is False
            and transition["to"]["make_default"] == "CRAZYHOUSE_LEGACY_BACKEND=scalar"
            and transition["to"]["fallback_allowed"] is False,
            "scoped-route Worker transition mismatch",
        )
        runtime = addendum["runtime_replay"]
        expected_summary = (
            "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
            "evaluator=incremental-scalar route_telemetry=PASS worker_binding=PASS "
            "scalar_default_unchanged=true strength_claim=false"
        )
        require(
            runtime["base_cases_unchanged"] is True
            and runtime["case_count"] == 2
            and runtime["runs"] == 4
            and runtime["route_backend"] == "legacy-v1"
            and runtime["route_identity_sha256"]
            == "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
            and runtime["required_route_token"] == "evaluator=incremental-scalar"
            and runtime["forbidden_route_tokens"]
            == ["evaluator=incremental-simd", "simd_backend="]
            and runtime["required_worker_summary"] == expected_summary
            and runtime["source_to_binary_binding_required"] is True
            and runtime["same_target_routing_correction_required"] is True
            and runtime["unavailable_simd_negative_required"] is True
            and runtime["exact_standard_control_required"] is True,
            "scoped-route Worker replay contract mismatch",
        )
        return {
            "mode": "incremental-scalar",
            "summary": expected_summary.removeprefix(
                "PASS crazyhouse_worker_search cases=2 runs=4 "
            ),
            "required_route_token": runtime["required_route_token"],
            "forbidden_route_tokens": runtime["forbidden_route_tokens"],
            "telemetry_label": "incremental scalar",
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256_file(addendum_path),
            },
        }

    if addendum["addendum"] == 6:
        require(addendum_path.stat().st_size == 5428, "Worker addendum 006 size mismatch")
        require(
            sha256_file(addendum_path)
            == "ef01655ceae8ba9fab6b26829b48c2c4bfca0ca8938f87255bdd5ddd27cdd5b7",
            "Worker addendum 006 SHA-256 mismatch",
        )
        source_root = addendum_path.resolve().parents[2]
        prior_pin = addendum["pins"]["worker_search_addendum_005"]
        prior_path = addendum_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), "Worker addendum 005 missing")
        require(
            prior_path.stat().st_size == prior_pin["bytes"]
            and sha256_file(prior_path) == prior_pin["sha256"],
            "Worker addendum 005 identity mismatch",
        )
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        require(
            prior["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
            and prior["addendum"] == 5
            and prior["pins"]["worker_search_contract_v1"] == base_pin,
            "Worker addendum 006 predecessor mismatch",
        )

        for name in ("formal_rejection_end_receipt", "failed_route_expected_red_record"):
            pin = addendum["pins"][name]
            receipt_path = (addendum_path.parent / pin["path"]).resolve()
            require(receipt_path.is_file(), f"Worker provenance receipt missing: {receipt_path}")
            require(
                receipt_path.stat().st_size == pin["bytes"]
                and sha256_file(receipt_path) == pin["sha256"],
                f"Worker provenance receipt identity mismatch: {name}",
            )

        rebase = addendum["source_rebase"]
        require(
            rebase["descendant_commit"]
            == "f945846c31ee802d2493e53f573414f94d4bd03e"
            and rebase["descendant_tree"]
            == "c667c1b7cbc5c05672acbf34f52c3cf49135b6fb"
            and rebase["official_stockfish_ancestor"]
            == "229f6339e537a097a79831cd06dbfdb3e623d4ac"
            and rebase["official_stockfish_ancestor_verified"] is True
            and rebase["fairy_stockfish_source_allowed"] is False,
            "failed-route source-rebase identity mismatch",
        )
        require(
            rebase["changed_source_pin_count"] == 1
            and rebase["changed_source_pin_path"] == "src/uci.cpp",
            "failed-route source-rebase changed-pin boundary mismatch",
        )
        prior_pins = prior["source_line"]["source_pins"]
        prior_by_path = {pin["path"]: pin for pin in prior_pins}
        unchanged_paths = [pin["path"] for pin in prior_pins if pin["path"] != "src/uci.cpp"]
        require(
            rebase["unchanged_source_pin_paths"] == unchanged_paths,
            "failed-route source-rebase unchanged-pin inventory mismatch",
        )
        require(
            rebase["replaced_pin"]["before"] == prior_by_path["src/uci.cpp"],
            "failed-route source-rebase before-pin mismatch",
        )
        after_pin = rebase["replaced_pin"]["after"]
        require(after_pin["path"] == "src/uci.cpp", "failed-route source-rebase path mismatch")
        for path in unchanged_paths:
            pin = prior_by_path[path]
            source_path = source_root / path
            require(source_path.is_file(), f"failed-route source pin missing: {source_path}")
            require(
                source_path.stat().st_size == pin["bytes"]
                and sha256_file(source_path) == pin["sha256"],
                f"failed-route unchanged source identity mismatch: {path}",
            )
        uci_path = source_root / after_pin["path"]
        require(uci_path.is_file(), f"failed-route UCI source missing: {uci_path}")
        require(
            uci_path.stat().st_size == after_pin["bytes"]
            and sha256_file(uci_path) == after_pin["sha256"],
            "failed-route UCI source identity mismatch",
        )

        behavior = rebase["exact_behavior_transition"]
        require(
            behavior["required_error"] == "legacy_simd_unavailable"
            and behavior["position_epoch_must_remain_invalid"] is True
            and behavior["worker_evaluator_semantics_changed"] is False
            and behavior["worker_search_semantics_changed"] is False
            and behavior["routing_success_digest_changed"] is False
            and behavior["fallback_allowed"] is False
            and behavior["strength_claim"] is False,
            "failed-route behavior-transition boundary mismatch",
        )
        transition = addendum["single_variable_transition"]
        require(
            transition["from"]["worker_mode"] == transition["to"]["worker_mode"]
            == "INCREMENTAL_SCALAR"
            and transition["from"]["route_commit_evaluator_field"]
            == transition["to"]["route_commit_evaluator_field"]
            == "evaluator=incremental-scalar"
            and transition["from"]["uci_source_sha256"]
            == rebase["replaced_pin"]["before"]["sha256"]
            and transition["to"]["uci_source_sha256"] == after_pin["sha256"]
            and transition["to"]["build_selector_supplied"] is False
            and transition["to"]["make_default"] == "CRAZYHOUSE_LEGACY_BACKEND=scalar"
            and transition["to"]["fallback_allowed"] is False,
            "failed-route Worker transition mismatch",
        )
        runtime = addendum["runtime_replay"]
        expected_summary = (
            "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
            "evaluator=incremental-scalar route_telemetry=PASS worker_binding=PASS "
            "scalar_default_unchanged=true strength_claim=false"
        )
        require(
            runtime["base_cases_unchanged"] is True
            and runtime["case_count"] == 2
            and runtime["runs"] == 4
            and runtime["route_backend"] == "legacy-v1"
            and runtime["route_identity_sha256"]
            == "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
            and runtime["required_route_token"] == "evaluator=incremental-scalar"
            and runtime["forbidden_route_tokens"]
            == ["evaluator=incremental-simd", "simd_backend="]
            and runtime["required_worker_summary"] == expected_summary
            and runtime["source_to_binary_binding_required"] is True
            and runtime["same_target_routing_correction_required"] is True
            and runtime["exact_standard_control_required"] is True,
            "failed-route Worker replay contract mismatch",
        )
        return {
            "mode": "incremental-scalar",
            "summary": expected_summary.removeprefix(
                "PASS crazyhouse_worker_search cases=2 runs=4 "
            ),
            "required_route_token": runtime["required_route_token"],
            "forbidden_route_tokens": runtime["forbidden_route_tokens"],
            "telemetry_label": "incremental scalar",
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256_file(addendum_path),
            },
        }

    if addendum["addendum"] == 5:
        require(addendum_path.stat().st_size == 6267, "Worker addendum 005 size mismatch")
        require(
            sha256_file(addendum_path)
            == "e2cb31fe1c2879a5c0515fbbc9e95c65be052dc348d6b48247ddbbdba6fd557c",
            "Worker addendum 005 SHA-256 mismatch",
        )
        source_root = addendum_path.resolve().parents[2]
        for name, number in (
            ("worker_search_addendum_002", 2),
            ("worker_search_addendum_004", 4),
        ):
            pin = addendum["pins"][name]
            prior_path = addendum_path.parent / Path(pin["path"]).name
            require(prior_path.is_file(), f"Worker addendum {number:03d} missing")
            require(
                prior_path.stat().st_size == pin["bytes"]
                and sha256_file(prior_path) == pin["sha256"],
                f"Worker addendum {number:03d} identity mismatch",
            )
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            require(prior["addendum"] == number, f"Worker addendum {number:03d} number mismatch")

        routing_pin = addendum["pins"]["routing_addendum_007"]
        routing_path = source_root / routing_pin["path"]
        require(routing_path.is_file(), "default-scalar routing addendum missing")
        require(
            routing_path.stat().st_size == routing_pin["bytes"]
            and sha256_file(routing_path) == routing_pin["sha256"],
            "default-scalar routing addendum identity mismatch",
        )
        routing = json.loads(routing_path.read_text(encoding="utf-8"))
        require(
            routing["addendum"] == 7
            and routing["runtime_replay"]["expected_binding"]
            == "BOUND_LEGACY_V1_INCREMENTAL_SCALAR_AUTHENTICATED",
            "default-scalar routing binding mismatch",
        )

        diagnostic_pin = addendum["pins"]["architecture_diagnostic_record"]
        diagnostic_path = (addendum_path.parent / diagnostic_pin["path"]).resolve()
        require(diagnostic_path.is_file(), "default-scalar architecture diagnostic missing")
        require(
            diagnostic_path.stat().st_size == diagnostic_pin["bytes"]
            and sha256_file(diagnostic_path) == diagnostic_pin["sha256"],
            "default-scalar architecture diagnostic identity mismatch",
        )
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        require(
            diagnostic["result"] == "CLASSIFIED_CONTRACT_CORRECTION_REQUIRED"
            and diagnostic["controlled_comparison"]["fresh_x86_64_avx2_default_scalar"][
                "backend"
            ]
            == "scalar",
            "default-scalar architecture diagnosis mismatch",
        )

        source_line = addendum["source_line"]
        require(
            source_line["product_implementation_commit"]
            == "9f3ae6ca13ad5a73386db42ad2eda89c99a9cbe2"
            and source_line["product_implementation_tree"]
            == "2e441204eab98d7e0e54ae23a66765153ce8fe0e"
            and source_line["official_stockfish_ancestor"]
            == "229f6339e537a097a79831cd06dbfdb3e623d4ac"
            and source_line["official_stockfish_ancestor_verified"] is True
            and source_line["fairy_stockfish_source_allowed"] is False,
            "default-scalar source-line identity mismatch",
        )
        for pin in source_line["source_pins"]:
            source_path = source_root / pin["path"]
            require(source_path.is_file(), f"default-scalar source pin missing: {source_path}")
            require(
                source_path.stat().st_size == pin["bytes"]
                and sha256_file(source_path) == pin["sha256"],
                f"default-scalar source identity mismatch: {pin['path']}",
            )

        control = addendum["single_variable_control"]
        target = control["to"]
        require(
            control["from"]["target_arch"] == target["target_arch"] == "x86-64-avx2"
            and target["build_selector_supplied"] is False
            and target["make_default"] == "CRAZYHOUSE_LEGACY_BACKEND=scalar"
            and target["worker_mode"] == "INCREMENTAL_SCALAR"
            and target["route_commit_evaluator_field"] == "evaluator=incremental-scalar"
            and target["simd_backend_field"] is None
            and target["runtime_selector_present"] is False
            and target["environment_selector_present"] is False,
            "default-scalar Worker selection boundary mismatch",
        )
        runtime = addendum["runtime_replay"]
        expected_summary = (
            "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
            "evaluator=incremental-scalar route_telemetry=PASS worker_binding=PASS "
            "scalar_default_unchanged=true strength_claim=false"
        )
        require(
            runtime["required_route_token"] == "evaluator=incremental-scalar"
            and runtime["forbidden_route_tokens"]
            == ["evaluator=incremental-simd", "simd_backend="]
            and runtime["required_worker_summary"] == expected_summary,
            "default-scalar Worker replay contract mismatch",
        )
        return {
            "mode": "incremental-scalar",
            "summary": expected_summary.removeprefix(
                "PASS crazyhouse_worker_search cases=2 runs=4 "
            ),
            "required_route_token": runtime["required_route_token"],
            "forbidden_route_tokens": runtime["forbidden_route_tokens"],
            "telemetry_label": "incremental scalar",
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256_file(addendum_path),
            },
        }

    if addendum["addendum"] == 4:
        require(addendum_path.stat().st_size == 8165, "Worker addendum 004 size mismatch")
        require(
            sha256_file(addendum_path)
            == "2078c8018a5f55e508eaee3ceac1f365cd9cc063edf3a3f2a35f2575e6795ef5",
            "Worker addendum 004 SHA-256 mismatch",
        )

        prior_documents: dict[int, dict] = {}
        for number, pin_name in (
            (1, "worker_search_addendum_001"),
            (2, "worker_search_addendum_002"),
            (3, "worker_search_addendum_003"),
        ):
            pin = addendum["pins"][pin_name]
            prior_path = addendum_path.parent / Path(pin["path"]).name
            require(prior_path.is_file(), f"prior Worker addendum missing: {prior_path}")
            require(
                prior_path.stat().st_size == pin["bytes"],
                f"Worker addendum {number:03d} size mismatch",
            )
            require(
                sha256_file(prior_path) == pin["sha256"],
                f"Worker addendum {number:03d} SHA-256 mismatch",
            )
            prior_documents[number] = json.loads(prior_path.read_text(encoding="utf-8"))
        require(
            all(
                document["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
                and document["addendum"] == number
                for number, document in prior_documents.items()
            ),
            "Worker SIMD prior-addendum identity mismatch",
        )
        require(
            all(
                document["pins"]["worker_search_contract_v1"] == base_pin
                for document in prior_documents.values()
            ),
            "Worker SIMD prior-addendum base-chain mismatch",
        )

        source_root = addendum_path.resolve().parents[2]
        metadata_path = (
            addendum_path.parent
            / "simd-worker-routing-preregistration-metadata.addendum.001.json"
        )
        require(metadata_path.is_file(), "SIMD preregistration metadata addendum missing")
        require(
            metadata_path.stat().st_size == 1950
            and sha256_file(metadata_path)
            == "37cbe3c1e289c28257fe18461886d16d535457c73fabc5f20bf2cfcbb62e7696",
            "SIMD preregistration metadata addendum identity mismatch",
        )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        require(
            metadata["pins"]["freeze_commit"]["commit"]
            == "ec277a25b3c43908679aa4c70bff760a29def58e"
            and metadata["scientific_contract_changed"] is False
            and metadata["expected_red_changed"] is False,
            "SIMD preregistration metadata correction boundary mismatch",
        )
        routing_pin = addendum["pins"]["routing_addendum_004"]
        routing_path = source_root / routing_pin["path"]
        require(routing_path.is_file(), "authenticated scalar routing addendum missing")
        require(
            routing_path.stat().st_size == routing_pin["bytes"]
            and sha256_file(routing_path) == routing_pin["sha256"],
            "authenticated scalar routing addendum identity mismatch",
        )

        for name in (
            "legacy_v1_simd_parity_contract",
            "legacy_v1_simd_parity_end_receipt",
            "legacy_v1_simd_parity_gate_record",
        ):
            pin = addendum["pins"][name]
            pin_path = (
                source_root / pin["path"]
                if name == "legacy_v1_simd_parity_contract"
                else (addendum_path.parent / pin["path"]).resolve()
            )
            require(pin_path.is_file(), f"SIMD prerequisite missing: {pin_path}")
            require(
                pin_path.stat().st_size == pin["bytes"]
                and sha256_file(pin_path) == pin["sha256"],
                f"SIMD prerequisite identity mismatch: {name}",
            )

        pre_source = addendum["pre_transition_source"]
        require(
            pre_source["commit"] == "7555542a3e24e7a3861f5cbc10140da13cdf849e"
            and pre_source["tree"] == "94c80c587a6dbf0b7db3828635b467724623dd50",
            "Worker SIMD pre-transition source identity mismatch",
        )
        require(
            pre_source["official_stockfish_ancestor"]
            == "229f6339e537a097a79831cd06dbfdb3e623d4ac"
            and pre_source["official_stockfish_ancestor_verified"] is True
            and pre_source["fairy_stockfish_source_allowed"] is False,
            "Worker SIMD source-lineage boundary mismatch",
        )
        for name in (
            "worker_callsite",
            "worker_state",
            "legacy_network_header",
            "legacy_network_implementation",
        ):
            pin = pre_source[name]
            source_path = source_root / pin["path"]
            require(source_path.is_file(), f"Worker SIMD source pin missing: {source_path}")
            require(
                source_path.stat().st_size == pin["bytes"]
                and sha256_file(source_path) == pin["sha256"],
                f"Worker SIMD source identity mismatch: {pin['path']}",
            )

        transition = addendum["single_variable_transition"]
        require(
            transition["from"]["worker_mode"] == "INCREMENTAL_SCALAR"
            and transition["from"]["route_commit_evaluator_field"]
            == "evaluator=incremental-scalar",
            "Worker SIMD transition origin mismatch",
        )
        target = transition["to"]
        require(
            target["build_selector"] == "CRAZYHOUSE_LEGACY_BACKEND=simd"
            and target["worker_mode"] == "INCREMENTAL_SIMD"
            and target["route_commit_evaluator_field"] == "evaluator=incremental-simd"
            and target["simd_backend_field"] == "simd_backend=avx2",
            "Worker SIMD transition target mismatch",
        )
        require(target["fallback_allowed"] is False, "Worker SIMD fallback was enabled")
        require(target["strength_claim"] is False, "Worker SIMD addendum claims strength")

        implementation = addendum["implementation_contract"]
        require(
            implementation["unavailable_backend"]
            == "A SIMD capability build whose compiled_simd_backend() is none rejects route admission with legacy_simd_unavailable before readyok, position admission or search.",
            "Worker SIMD unavailable-backend boundary mismatch",
        )
        require(
            implementation["forbidden_changes"]
            == [
                "src/search.cpp",
                "src/search.h",
                "src/nnue/crazyhouse_legacy_network.h",
                "src/nnue/crazyhouse_legacy_network.cpp",
                "legacy network bytes or registered digest",
                "UCI option inventory",
                "runtime environment-variable selection",
                "automatic scalar fallback",
                "search parameters or rules",
            ],
            "Worker SIMD forbidden-change boundary mismatch",
        )

        runtime = addendum["runtime_replay"]
        expected_token = "evaluator=incremental-simd simd_backend=avx2"
        expected_summary = (
            "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
            "evaluator=incremental-simd simd_backend=avx2 route_telemetry=PASS "
            "worker_binding=PASS scalar_default_unchanged=true strength_claim=false"
        )
        require(runtime["required_route_token"] == expected_token,
                "Worker SIMD route token mismatch")
        require(runtime["forbidden_route_token"] == "evaluator=incremental-scalar",
                "Worker SIMD forbidden route token mismatch")
        require(runtime["required_worker_summary"] == expected_summary,
                "Worker SIMD replay summary mismatch")
        return {
            "mode": "incremental-simd",
            "summary": expected_summary.removeprefix(
                "PASS crazyhouse_worker_search cases=2 runs=4 "
            ),
            "required_route_token": expected_token,
            "forbidden_route_token": "evaluator=incremental-scalar",
            "telemetry_label": "SIMD",
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256_file(addendum_path),
            },
        }

    if addendum["addendum"] == 3:
        require(addendum_path.stat().st_size == 6829, "Worker addendum 003 size mismatch")
        require(
            sha256_file(addendum_path)
            == "ad3f69dcaf02828614a522d70e63944c4a85b35c5e26bc147abfd6a6195368cc",
            "Worker addendum 003 SHA-256 mismatch",
        )

        prior_documents: dict[int, dict] = {}
        for number, pin_name in (
            (1, "worker_search_addendum_001"),
            (2, "worker_search_addendum_002"),
        ):
            pin = addendum["pins"][pin_name]
            prior_path = addendum_path.parent / Path(pin["path"]).name
            require(prior_path.is_file(), f"prior Worker addendum missing: {prior_path}")
            require(
                prior_path.stat().st_size == pin["bytes"],
                f"Worker addendum {number:03d} size mismatch",
            )
            require(
                sha256_file(prior_path) == pin["sha256"],
                f"Worker addendum {number:03d} SHA-256 mismatch",
            )
            prior_documents[number] = json.loads(prior_path.read_text(encoding="utf-8"))

        prior_one = prior_documents[1]
        prior_two = prior_documents[2]
        require(
            prior_one["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
            and prior_one["addendum"] == 1,
            "Worker addendum 001 identity mismatch",
        )
        require(
            prior_two["schema"] == "crazyhouse-worker-search-contract-addendum/v1"
            and prior_two["addendum"] == 2,
            "Worker addendum 002 identity mismatch",
        )
        require(
            prior_one["pins"]["worker_search_contract_v1"] == base_pin
            and prior_two["pins"]["worker_search_contract_v1"] == base_pin,
            "Worker prior addendum base-chain mismatch",
        )
        require(
            prior_two["pins"]["worker_search_addendum_001"]
            == addendum["pins"]["worker_search_addendum_001"],
            "Worker addendum 002 prior-chain mismatch",
        )

        inherited = addendum["inherited_incremental_implementation"]
        require(
            inherited["commit"] == prior_one["implementation"]["commit"]
            == prior_two["inherited_incremental_implementation"]["commit"]
            == "eb13ec473b6e5339ec914e6de36c0c640fb05b1b",
            "Worker incremental implementation commit mismatch",
        )
        require(
            inherited["tree"] == prior_one["implementation"]["tree"]
            == prior_two["inherited_incremental_implementation"]["tree"]
            == "43ee5becef2122f3eac7dfdc268ef945923c6c75",
            "Worker incremental implementation tree mismatch",
        )
        require(
            inherited["official_stockfish_ancestor"]
            == "229f6339e537a097a79831cd06dbfdb3e623d4ac",
            "official Stockfish ancestor mismatch",
        )

        rebase = addendum["provenance_rebase"]
        require(
            rebase["descendant_commit"]
            == "33256716e894f08a75836f0745a6d74433a7df5d"
            and rebase["descendant_tree"]
            == "7dba6bd6be7e0e50b2e303d6804508479db89ed7",
            "Worker provenance descendant identity mismatch",
        )
        require(rebase["changed_incremental_pin_count"] == 1,
                "Worker provenance changed-pin count mismatch")
        require(
            rebase["changed_incremental_pin_path"]
            == "src/nnue/crazyhouse_legacy_network.cpp",
            "Worker provenance changed-pin path mismatch",
        )
        original_pins = prior_one["implementation"]["source_pins"]
        require(
            rebase["unchanged_incremental_pins"] == original_pins[:3],
            "Worker unchanged incremental pins mismatch",
        )
        require(
            rebase["replaced_pin"]["before"] == original_pins[3],
            "Worker replaced source before-pin mismatch",
        )
        expected_after = {
            "path": "src/nnue/crazyhouse_legacy_network.cpp",
            "bytes": 43923,
            "sha256": "454aaba18408929b9ab4a23b9c2ea8479803db51edb1d6849bd7c779399eb5c8",
            "git_blob": "2d7f6e7af4f03cceed3667287afafb572530715c",
        }
        require(
            rebase["replaced_pin"]["after"] == expected_after,
            "Worker replaced source after-pin mismatch",
        )

        source_root = addendum_path.resolve().parents[2]
        for pin in rebase["unchanged_incremental_pins"] + [expected_after]:
            source_path = source_root / pin["path"]
            require(source_path.is_file(), f"incremental source pin missing: {source_path}")
            require(
                source_path.stat().st_size == pin["bytes"],
                f"incremental source size mismatch: {pin['path']}",
            )
            require(
                sha256_file(source_path) == pin["sha256"],
                f"incremental source SHA-256 mismatch: {pin['path']}",
            )

        portability_pin = addendum["pins"]["sanitizer_portability_addendum_004"]
        portability_path = source_root / portability_pin["path"]
        require(portability_path.is_file(), "sanitizer portability addendum missing")
        require(
            portability_path.stat().st_size == portability_pin["bytes"]
            and sha256_file(portability_path) == portability_pin["sha256"],
            "sanitizer portability addendum identity mismatch",
        )
        receipt_pins = {
            "portable_classification_red_end_receipt": (
                5984,
                "0edf8cf4401c40aaedd3f93a63b7d55a53d38b03d73f0fe638be61e58908f5b5",
            ),
            "portable_classification_rejection_record": (
                1528,
                "d0c5e9d6a293eafb3cb19f55ba1e1f9023d93e0388cf58b42c000f103586cc76",
            ),
            "stale_worker_pin_red_end_receipt": (
                6255,
                "a852ae0fb70bf63245a08f66f8cc94408b2f937828efeeb8e31d51d53d5273a0",
            ),
            "stale_worker_pin_rejection_record": (
                1974,
                "1fe699f02b605214b0152c69ee607353665bc74f0c9a4a37f89f2c6de84a8199",
            ),
        }
        for name, (size, digest) in receipt_pins.items():
            pin = addendum["pins"][name]
            require(
                pin["bytes"] == size and pin["sha256"] == digest,
                f"Worker provenance receipt pin mismatch: {name}",
            )

        source_transition = rebase["exact_source_transition"]
        require(
            source_transition["from"]
            == "error == std::make_error_code(std::errc::no_such_file_or_directory)"
            and source_transition["to"]
            == "error == std::errc::no_such_file_or_directory",
            "Worker portable source transition mismatch",
        )
        require(source_transition["worker_evaluator_semantics_changed"] is False,
                "Worker provenance rebase changed evaluator semantics")
        require(source_transition["search_semantics_changed"] is False,
                "Worker provenance rebase changed search semantics")
        require(source_transition["fallback_allowed"] is False,
                "Worker provenance rebase enabled fallback")
        require(source_transition["simd_claim"] is False,
                "Worker provenance rebase improperly claims SIMD")

        transition = addendum["single_variable_transition"]
        require(
            transition["from"]["worker_mode"] == transition["to"]["worker_mode"]
            == "INCREMENTAL_SCALAR",
            "Worker provenance rebase changed evaluator mode",
        )
        require(
            transition["from"]["route_commit_evaluator_field"]
            == transition["to"]["route_commit_evaluator_field"]
            == "evaluator=incremental-scalar",
            "Worker provenance rebase changed route telemetry",
        )
        expected_summary = (
            "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
            "evaluator=incremental-scalar route_telemetry=PASS "
            "worker_binding=PASS strength_claim=false"
        )
        require(
            addendum["runtime_replay"]["required_worker_summary"] == expected_summary
            and prior_two["runtime_replay"]["required_worker_summary"] == expected_summary,
            "Worker provenance replay summary mismatch",
        )
        require(
            addendum["runtime_replay"]["required_route_token"]
            == "evaluator=incremental-scalar",
            "Worker provenance route token mismatch",
        )
        return {
            "mode": "incremental-scalar",
            "summary": expected_summary.removeprefix(
                "PASS crazyhouse_worker_search cases=2 runs=4 "
            ),
            "required_route_token": "evaluator=incremental-scalar",
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256_file(addendum_path),
            },
        }

    if addendum["addendum"] == 2:
        prior_pin = addendum["pins"]["worker_search_addendum_001"]
        prior_path = addendum_path.parent / Path(prior_pin["path"]).name
        require(prior_path.is_file(), f"prior Worker addendum missing: {prior_path}")
        require(prior_path.stat().st_size == prior_pin["bytes"], "prior Worker addendum size mismatch")
        require(
            sha256_file(prior_path) == prior_pin["sha256"],
            "prior Worker addendum SHA-256 mismatch",
        )
        inherited = load_evaluator_context(contract_path, prior_path)
        require(inherited["mode"] == "incremental-scalar", "inherited evaluator mode mismatch")
        transition = addendum["single_variable_transition"]
        require(
            transition["from"]["worker_mode"] == "INCREMENTAL_SCALAR",
            "telemetry transition origin mismatch",
        )
        target = transition["to"]
        require(
            target["worker_mode"] == "INCREMENTAL_SCALAR",
            "telemetry transition changed evaluator mode",
        )
        require(
            target["route_commit_evaluator_field"] == "evaluator=incremental-scalar",
            "telemetry route token mismatch",
        )
        require(target["fallback_allowed"] is False, "telemetry addendum enabled fallback")
        require(target["simd_claim"] is False, "telemetry addendum improperly claims SIMD")
        expected_summary = (
            "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
            "evaluator=incremental-scalar route_telemetry=PASS "
            "worker_binding=PASS strength_claim=false"
        )
        require(
            addendum["runtime_replay"]["required_worker_summary"] == expected_summary,
            "telemetry Worker summary mismatch",
        )
        return {
            "mode": "incremental-scalar",
            "summary": expected_summary.removeprefix(
                "PASS crazyhouse_worker_search cases=2 runs=4 "
            ),
            "required_route_token": "evaluator=incremental-scalar",
            "addendum": {
                "bytes": addendum_path.stat().st_size,
                "sha256": sha256_file(addendum_path),
            },
        }

    require(
        addendum["pins"]["incremental_scalar_parity_gate_record"]["sha256"]
        == "7278da1bd1112e9c176a8a92efa9330ea8832bb68188d79fc4eb7ee2bd8a9e75",
        "incremental scalar parity gate pin mismatch",
    )
    require(
        addendum["pins"]["incremental_scalar_parity_end_receipt"]["sha256"]
        == "0434430209b80d3f9c3845d00fb5b441fe7bbac1c459333e51598316f12eeb13",
        "incremental scalar parity receipt pin mismatch",
    )

    implementation = addendum["implementation"]
    require(
        implementation["commit"] == "eb13ec473b6e5339ec914e6de36c0c640fb05b1b",
        "incremental implementation commit mismatch",
    )
    require(
        implementation["tree"] == "43ee5becef2122f3eac7dfdc268ef945923c6c75",
        "incremental implementation tree mismatch",
    )
    require(
        implementation["official_stockfish_ancestor"]
        == "229f6339e537a097a79831cd06dbfdb3e623d4ac",
        "official Stockfish ancestor mismatch",
    )
    source_root = addendum_path.resolve().parents[2]
    for pin in implementation["source_pins"]:
        source_path = source_root / pin["path"]
        require(source_path.is_file(), f"incremental source pin missing: {source_path}")
        require(
            source_path.stat().st_size == pin["bytes"],
            f"incremental source size mismatch: {pin['path']}",
        )
        require(
            sha256_file(source_path) == pin["sha256"],
            f"incremental source SHA-256 mismatch: {pin['path']}",
        )

    transition = addendum["single_variable_transition"]
    require(transition["from"]["mode"] == "FULL_REFRESH", "Worker transition origin mismatch")
    target = transition["to"]
    require(target["mode"] == "INCREMENTAL_SCALAR", "Worker transition target mismatch")
    require(
        target["entrypoint"] == "LegacyCrazyhouseNetworkV1::evaluate_legacy_incremental",
        "Worker incremental entrypoint mismatch",
    )
    require(target["accumulator_owner"] == "Search::Worker", "Worker accumulator owner mismatch")
    require(target["fallback_allowed"] is False, "Worker incremental fallback was enabled")
    require(target["simd_claim"] is False, "Worker addendum improperly claims SIMD")
    expected_summary = (
        "PASS crazyhouse_worker_search cases=2 runs=4 backend=legacy-v1 "
        "evaluator=incremental-scalar worker_binding=PASS strength_claim=false"
    )
    require(
        addendum["runtime_replay"]["required_worker_summary"] == expected_summary,
        "Worker addendum summary mismatch",
    )
    return {
        "mode": "incremental-scalar",
        "summary": expected_summary.removeprefix("PASS crazyhouse_worker_search cases=2 runs=4 "),
        "addendum": {
            "bytes": addendum_path.stat().st_size,
            "sha256": sha256_file(addendum_path),
        },
        "required_route_token": None,
    }


def run_once(engine: Path, legacy: Path, case: dict, evaluator_context: dict) -> dict:
    proc = UciProcess(engine)
    try:
        proc.send("uci")
        handshake = proc.wait_for(lambda line: line == "uciok", "uciok", 30)
        require(any(line.startswith("option name UCI_Variant ") for line in handshake),
                "UCI_Variant option missing")
        require(any(line.startswith("option name CrazyhouseEvalFile ") for line in handshake),
                "CrazyhouseEvalFile option missing")
        require(any(line.startswith("option name CrazyhouseProfile ") for line in handshake),
                "CrazyhouseProfile option missing")

        setoption(proc, "Threads", "1")
        setoption(proc, "Hash", "16")
        setoption(proc, "MultiPV", "1")
        setoption(proc, "UCI_Chess960", "false")
        setoption(proc, "UCI_Variant", "crazyhouse")
        setoption(proc, "CrazyhouseProfile", PROFILE_TOKEN)
        setoption(proc, "CrazyhouseEvalFile", str(legacy))
        ready = wait_ready_success(proc, "crazyhouse")
        commit = next(
            line for line in ready
            if "info string route_commit status=ok ruleset=crazyhouse" in line
        )
        require("backend=legacy-v1" in commit, f"wrong backend acknowledgement: {commit}")
        require(
            "identity=8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
            in commit,
            f"wrong legacy identity acknowledgement: {commit}",
        )
        if evaluator_context.get("required_route_token") is not None:
            require(
                evaluator_context["required_route_token"] in commit,
                "missing engine-authored "
                f"{evaluator_context.get('telemetry_label', 'incremental')} "
                f"route telemetry: {commit}",
            )
        if evaluator_context.get("forbidden_route_token") is not None:
            require(
                evaluator_context["forbidden_route_token"] not in commit,
                f"forbidden evaluator route telemetry observed: {commit}",
            )
        for forbidden_token in evaluator_context.get("forbidden_route_tokens", []):
            require(
                forbidden_token not in commit,
                f"forbidden evaluator route telemetry observed: {commit}",
            )

        proc.send(case["position_command"])
        proc.send(case["go_command"])
        search = proc.wait_for(
            lambda line: line.startswith("bestmove ")
            or ("info string ERROR go" in line),
            f"{case['id']} bestmove or fail-closed refusal",
            60,
        )
        terminal = search[-1]
        if "info string ERROR go" in terminal:
            raise VerificationFailure(
                f"{case['id']}: bounded search remained refused: {terminal}"
            )
        require(terminal.startswith("bestmove "), f"{case['id']}: malformed terminal line")
        tokens = terminal.split()
        require(len(tokens) in (2, 4), f"{case['id']}: malformed bestmove tokens: {tokens!r}")
        if len(tokens) == 4:
            require(tokens[2] == "ponder", f"{case['id']}: malformed ponder clause")
        bestmove = tokens[1]
        require(bestmove not in ("0000", "(none)"), f"{case['id']}: sentinel bestmove")
        require(
            bestmove in case["allowed_bestmoves"],
            f"{case['id']}: bestmove outside preregistered legal root set: {bestmove}",
        )
        require(
            any(line.startswith("info depth 1 ") or line == "info depth 1" for line in search),
            f"{case['id']}: no depth-1 search information observed",
        )
        proc.close()
        return {
            "id": case["id"],
            "route_commit": commit,
            "bestmove_line": terminal,
            "bestmove": bestmove,
            "search_lines": search,
            "stderr": list(proc.stderr_all),
            "exit_code": proc.process.returncode,
        }
    except Exception:
        if proc.process.poll() is None:
            proc.close(expect_stderr_empty=False)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--legacy-network", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--contract-addendum", type=Path)
    parser.add_argument("--transcript-out", type=Path)
    args = parser.parse_args()

    try:
        for path in (args.engine, args.legacy_network, args.contract):
            require(path.is_file(), f"missing input: {path}")
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        require(contract["schema"] == "crazyhouse-worker-search-contract/v1",
                "contract schema mismatch")
        require(contract["profile"]["token"] == PROFILE_TOKEN, "profile token mismatch")
        evaluator_context = load_evaluator_context(args.contract, args.contract_addendum)
        require(
            args.legacy_network.stat().st_size == contract["legacy_network"]["bytes"],
            "legacy network size mismatch",
        )
        legacy_before = sha256_file(args.legacy_network)
        engine_before = sha256_file(args.engine)
        require(
            legacy_before == contract["legacy_network"]["sha256"],
            "legacy network SHA-256 mismatch",
        )

        observations: list[dict] = []
        for case in contract["cases"]:
            case_runs = [
                run_once(args.engine, args.legacy_network, case, evaluator_context)
                for _ in range(case["repetitions"])
            ]
            if case["require_same_bestmove"]:
                require(
                    len({run["bestmove"] for run in case_runs}) == 1,
                    f"{case['id']}: repeated bestmove drift",
                )
            observations.append({"id": case["id"], "runs": case_runs})

        legacy_after = sha256_file(args.legacy_network)
        engine_after = sha256_file(args.engine)
        require(legacy_after == legacy_before, "legacy network changed during search")
        require(engine_after == engine_before, "engine binary changed during search")

        transcript = {
            "schema": "crazyhouse-worker-search-observation/v1",
            "engine": {"bytes": args.engine.stat().st_size, "sha256": engine_before},
            "legacy_network": {
                "bytes": args.legacy_network.stat().st_size,
                "sha256": legacy_before,
            },
            "contract": {
                "bytes": args.contract.stat().st_size,
                "sha256": sha256_file(args.contract),
            },
            "contract_addendum": evaluator_context["addendum"],
            "evaluator": evaluator_context["mode"],
            "observations": observations,
            "artifact_identities_stable": True,
            "timing_evidence": False,
            "strength_claim": False,
        }
        if args.transcript_out:
            args.transcript_out.parent.mkdir(parents=True, exist_ok=True)
            args.transcript_out.write_text(
                json.dumps(transcript, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
        print("PASS crazyhouse_worker_search cases=2 runs=4 " + evaluator_context["summary"])
        return 0
    except (OSError, KeyError, TypeError, ValueError, VerificationFailure) as exc:
        print(f"FAIL crazyhouse_worker_search: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
