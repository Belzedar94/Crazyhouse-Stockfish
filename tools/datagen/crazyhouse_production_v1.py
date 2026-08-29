#!/usr/bin/env python3
"""Fail-closed validators for the protocol-41 Crazyhouse production route.

The frozen physical codec remains byte-identical to its G8 golden identity.
Production-only capability and provenance policy lives in this separate module.
"""

from __future__ import annotations

import json
from typing import Any, Mapping
import uuid

import crazyhouse_physical_v1 as physical


DuplicateJsonKeyError = physical.DuplicateJsonKeyError
FormatError = physical.FormatError
RULE_PROFILE_SHA256 = physical.RULE_PROFILE_SHA256
_hex = physical._hex
_relative_artifact = physical._relative_artifact
_strict_object = physical._strict_object
canonical_json_bytes = physical.canonical_json_bytes
require = physical.require
sha256 = physical.sha256

PRODUCTION_NETWORK_BYTES = 58_534_811
PRODUCTION_NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
PRODUCTION_BOOK_BYTES = 39_922
PRODUCTION_BOOK_ROOTS = 599
PRODUCTION_BOOK_SHA256 = "1371e87ce3bdb875d922ad0061c96c4a123bc571daf4ae2bff24e5176287f0fa"
PRODUCTION_SELECTION_POLICY_SHA256 = (
    "475fd0fb9a929e964ff32357031a18d33ecc2543e8681cc73068858c10db3014"
)
PRODUCTION_OPENBENCH = "https://belzedar.duckdns.org"
PRODUCTION_PARTITION_DOMAIN = "Crazyhouse-Stockfish physical trajectory split v1\0"


def validate_production_capability_response_bytes(
    payload: bytes,
    *,
    contract_bytes: bytes,
    expected_challenge: str,
) -> Mapping[str, Any]:
    """Validate the dedicated protocol-41 production producer handshake."""

    require(not payload.startswith(b"\xef\xbb\xbf"), "production capability must not contain a BOM")
    require(b"\r" not in payload, "production capability must use LF line endings")
    require(
        payload.endswith(b"\n") and not payload.endswith(b"\n\n"),
        "production capability must end with exactly one LF",
    )
    require(not contract_bytes.startswith(b"\xef\xbb\xbf"), "production contract must not contain a BOM")
    require(b"\r" not in contract_bytes, "production contract must use LF line endings")
    try:
        response = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
        contract = json.loads(contract_bytes.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise FormatError(f"invalid production capability JSON: {exc}") from exc
    require(isinstance(response, dict) and isinstance(contract, dict), "production capability roots must be objects")
    require(payload == canonical_json_bytes(response), "production capability response is not canonical")
    require(
        set(contract)
        == {
            "schema",
            "project",
            "variant",
            "artifact_role",
            "response_schema",
            "required_response_fields",
            "required_values",
            "dynamic_fields",
            "normal_play_engine_must_not_advertise",
            "fixture_g0_response_is_not_substitutable",
            "unknown_or_missing_field_policy",
        },
        "production capability contract keys drifted",
    )
    require(
        contract["schema"] == "crazyhouse-datagen-production-capability-contract/v1"
        and contract["project"] == "Crazyhouse-Stockfish"
        and contract["variant"] == "crazyhouse"
        and contract["artifact_role"] == "crazyhouse-physical-datagen-production-v1"
        and contract["response_schema"]
        == "crazyhouse-datagen-production-capability-response/v1"
        and contract["normal_play_engine_must_not_advertise"] is True
        and contract["fixture_g0_response_is_not_substitutable"] is True
        and contract["unknown_or_missing_field_policy"] == "reject",
        "production capability contract identity drifted",
    )
    fields = contract["required_response_fields"]
    required_values = contract["required_values"]
    dynamic_fields = contract["dynamic_fields"]
    require(
        isinstance(fields, list)
        and fields
        and all(isinstance(field, str) and field for field in fields)
        and len(fields) == len(set(fields)),
        "production capability field inventory invalid",
    )
    require(
        isinstance(required_values, dict)
        and isinstance(dynamic_fields, dict)
        and set(fields) == set(required_values) | set(dynamic_fields) | {"schema"},
        "production capability contract field classes drifted",
    )
    require(set(response) == set(fields), "production capability response keys drifted")
    require(response["schema"] == contract["response_schema"], "production capability schema drifted")
    for key, expected in required_values.items():
        require(response[key] == expected, f"production capability {key} drifted")
    require(
        isinstance(expected_challenge, str)
        and len(expected_challenge) == 32
        and expected_challenge == expected_challenge.lower(),
        "expected production capability challenge invalid",
    )
    _hex(expected_challenge, 32, "expected production capability challenge")
    require(response["challenge"] == expected_challenge, "production capability challenge mismatch")
    require(
        response["capability_contract_sha256"] == sha256(contract_bytes).hex(),
        "production capability contract digest mismatch",
    )
    require(type(response["artifact_bytes"]) is int and response["artifact_bytes"] > 0, "production artifact bytes invalid")
    require(isinstance(response["toolchain_identity"], str) and response["toolchain_identity"], "production toolchain identity missing")
    for key in (
        "artifact_sha256",
        "build_recipe_sha256",
        "capability_contract_sha256",
        "registered_network_sha256",
        "rule_profile_sha256",
        "selection_policy_sha256",
        "toolchain_sha256",
    ):
        _hex(response[key], 64, f"production capability.{key}")
    for key in ("producer_source_commit", "producer_source_tree", "producer_src_tree"):
        _hex(response[key], 40, f"production capability.{key}")
    require(
        response["selection_policy_sha256"] == PRODUCTION_SELECTION_POLICY_SHA256,
        "production selection policy drifted",
    )
    return response


def validate_production_provenance_bytes(
    payload: bytes,
    *,
    chunk_id: bytes,
    campaign_id: bytes,
    capability: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate intrinsic provenance emitted by the dedicated production route."""

    require(not payload.startswith(b"\xef\xbb\xbf"), "production provenance must not contain a BOM")
    require(b"\r" not in payload, "production provenance must use LF line endings")
    require(
        payload.endswith(b"\n") and not payload.endswith(b"\n\n"),
        "production provenance must end with exactly one LF",
    )
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise FormatError(f"invalid production provenance JSON: {exc}") from exc
    require(isinstance(document, dict), "production provenance root must be an object")
    require(payload == canonical_json_bytes(document), "production provenance is not canonical")
    require(
        set(document)
        == {
            "adjudication",
            "campaign_id",
            "chunk_id",
            "chunk_index",
            "cohort",
            "external_workload_id",
            "generation_settings",
            "invalid_game_policy",
            "network",
            "official_openbench_origin",
            "openbench_assignment",
            "openbench_publication_protocol",
            "opening_source",
            "partition",
            "producer_artifact",
            "producer_capability",
            "project",
            "rule_profile",
            "schema",
            "seed",
            "source_commit",
            "source_dirty",
            "source_tree",
            "src_tree",
            "teacher",
            "toolchain",
            "variant",
        },
        "production provenance keys drifted",
    )
    require(
        document["schema"] == "crazyhouse-datagen-provenance/v1"
        and document["project"] == "Crazyhouse-Stockfish"
        and document["variant"] == "crazyhouse",
        "production provenance project/variant drifted",
    )
    rule = document["rule_profile"]
    require(
        rule == {
            "id": "LICHESS_CRAZYHOUSE_2026_08_12",
            "sha256": RULE_PROFILE_SHA256.hex(),
        },
        "production rule profile drifted",
    )
    for key in ("source_commit", "source_tree", "src_tree"):
        _hex(document[key], 40, f"production provenance.{key}")
    require(document["source_dirty"] is False, "dirty production source is inadmissible")
    require(
        document["source_commit"] == capability["producer_source_commit"]
        and document["source_tree"] == capability["producer_source_tree"]
        and document["src_tree"] == capability["producer_src_tree"]
        and document["source_dirty"] == capability["producer_source_dirty"],
        "production source binding drifted",
    )
    try:
        observed_campaign = uuid.UUID(document["campaign_id"]).bytes
        observed_chunk = uuid.UUID(document["chunk_id"]).bytes
    except (AttributeError, ValueError) as exc:
        raise FormatError(f"production campaign/chunk UUID invalid: {exc}") from exc
    require(observed_campaign == campaign_id and observed_chunk == chunk_id, "production campaign/chunk binding drifted")
    require(type(document["chunk_index"]) is int and document["chunk_index"] >= 0, "production chunk index invalid")
    require(isinstance(document["seed"], str) and document["seed"].isdigit(), "production seed must be decimal text")
    for key in ("cohort", "external_workload_id"):
        value = document[key]
        require(
            isinstance(value, str)
            and 0 < len(value) <= 256
            and all(32 <= ord(character) < 127 for character in value),
            f"production {key} invalid",
        )
    require(
        document["official_openbench_origin"] == PRODUCTION_OPENBENCH
        and document["openbench_publication_protocol"] == 41,
        "production OpenBench origin/protocol drifted",
    )
    assignment = document["openbench_assignment"]
    require(
        isinstance(assignment, dict)
        and set(assignment) == {"worker_threads_capacity"}
        and type(assignment["worker_threads_capacity"]) is int
        and 0 < assignment["worker_threads_capacity"] <= 0xFFFFFFFF,
        "production OpenBench worker assignment drifted",
    )

    adjudication = document["adjudication"]
    require(
        adjudication
        == {
            "claim_policy": "automatic-only",
            "fivefold_automatic": True,
            "insufficient_material": False,
            "resignation": False,
            "rule50": False,
            "threefold_claim": False,
        },
        "production adjudication drifted",
    )
    settings = document["generation_settings"]
    settings_keys = {
        "accepted_trajectories",
        "base_seed",
        "candidate_games_examined",
        "complete_trajectory_only",
        "depth_cap",
        "exact_count",
        "exact_quota_algorithm",
        "exploration_max_score_diff_internal",
        "exploration_multipv",
        "exploration_plies",
        "fixture_only",
        "hash_mib",
        "max_candidate_games",
        "max_game_ply",
        "nodes_per_position",
        "production_generation_authorized",
        "record_count",
        "role_eligible_complete_candidates",
        "role_ineligible_candidates",
        "subset_candidates_omitted",
        "threads",
        "training_admissible",
        "wall_time_encoded",
    }
    require(isinstance(settings, dict) and set(settings) == settings_keys, "production generation settings drifted")
    for key in (
        "accepted_trajectories",
        "base_seed",
        "candidate_games_examined",
        "depth_cap",
        "exploration_max_score_diff_internal",
        "exploration_multipv",
        "exploration_plies",
        "hash_mib",
        "max_candidate_games",
        "max_game_ply",
        "nodes_per_position",
        "record_count",
        "role_eligible_complete_candidates",
        "role_ineligible_candidates",
        "subset_candidates_omitted",
        "threads",
    ):
        require(type(settings[key]) is int and settings[key] >= 0, f"production setting {key} invalid")
    require(
        settings["accepted_trajectories"] > 0
        and settings["candidate_games_examined"] > 0
        and settings["depth_cap"] > 0
        and settings["exploration_multipv"] > 0
        and settings["hash_mib"] > 0
        and settings["max_candidate_games"] > 0
        and settings["max_game_ply"] > 0
        and settings["nodes_per_position"] > 0
        and settings["record_count"] > 0
        and settings["threads"] == 1,
        "production generation work bounds invalid",
    )
    require(
        settings["complete_trajectory_only"] is True
        and settings["exact_count"] is True
        and settings["exact_quota_algorithm"]
        == "deterministic-first-reachable-exact-subset-v1"
        and settings["fixture_only"] is False
        and settings["production_generation_authorized"] is True
        and settings["training_admissible"] is True
        and settings["wall_time_encoded"] is False,
        "production generation boundary drifted",
    )
    invalid = document["invalid_game_policy"]
    require(
        isinstance(invalid, dict)
        and set(invalid)
        == {
            "bound_or_missing_pv",
            "complete_trajectory_oversize",
            "crash",
            "illegal_move",
            "observed_rejections",
            "safety_limit",
            "unreachable_exact_quota",
        }
        and invalid["bound_or_missing_pv"] == "quarantine-game"
        and invalid["complete_trajectory_oversize"] == "quarantine-game"
        and invalid["crash"] == "abort-chunk"
        and invalid["illegal_move"] == "quarantine-game"
        and invalid["safety_limit"] == "quarantine-game"
        and invalid["unreachable_exact_quota"] == "abort-chunk"
        and isinstance(invalid["observed_rejections"], list),
        "production invalid-game policy drifted",
    )
    for rejection in invalid["observed_rejections"]:
        require(
            isinstance(rejection, dict)
            and set(rejection) == {"candidate_index", "reason", "root_id"}
            and type(rejection["candidate_index"]) is int
            and rejection["candidate_index"] >= 0
            and isinstance(rejection["reason"], str)
            and rejection["reason"]
            and isinstance(rejection["root_id"], str)
            and rejection["root_id"],
            "production rejection entry drifted",
        )
    require(
        settings["candidate_games_examined"]
        == settings["role_eligible_complete_candidates"]
        + settings["role_ineligible_candidates"]
        + len(invalid["observed_rejections"])
        and settings["candidate_games_examined"]
        <= settings["max_candidate_games"]
        and settings["role_eligible_complete_candidates"]
        == settings["accepted_trajectories"]
        + settings["subset_candidates_omitted"],
        "production candidate accounting drifted",
    )

    network = document["network"]
    require(
        isinstance(network, dict)
        and set(network)
        == {"bytes", "compatibility", "format", "license", "path", "sha256", "used"}
        and network["bytes"] == PRODUCTION_NETWORK_BYTES
        and network["compatibility"] == "qualified-positive-and-negative-load"
        and network["format"] == "legacy-halfkav2variants-v1"
        and network["license"] == "CC0-1.0"
        and network["sha256"] == PRODUCTION_NETWORK_SHA256
        and network["used"] is True,
        "production network identity drifted",
    )
    require(
        isinstance(network["path"], str)
        and network["path"]
        and "\\" not in network["path"]
        and not network["path"].startswith("/")
        and ":" not in network["path"]
        and ".." not in network["path"].split("/"),
        "production network path invalid",
    )
    require(
        capability["registered_network_bytes"] == network["bytes"]
        and capability["registered_network_sha256"] == network["sha256"],
        "production network/capability binding drifted",
    )
    opening = document["opening_source"]
    require(
        isinstance(opening, dict)
        and set(opening)
        == {
            "artifact",
            "engine_selected",
            "kind",
            "match_result_selected",
            "selection_policy_sha256",
        }
        and opening["engine_selected"] is False
        and opening["kind"] == "deterministic-authenticated-book-order"
        and opening["match_result_selected"] is False
        and opening["selection_policy_sha256"] == PRODUCTION_SELECTION_POLICY_SHA256,
        "production opening policy drifted",
    )
    opening_artifact = opening["artifact"]
    require(
        isinstance(opening_artifact, dict)
        and set(opening_artifact) == {"bytes", "kind", "license", "path", "roots", "sha256"}
        and opening_artifact["bytes"] == PRODUCTION_BOOK_BYTES
        and opening_artifact["kind"] == "official-crazyhouse-epd-physical-roots-v1"
        and opening_artifact["license"] == "GPL-3.0-or-later"
        and opening_artifact["roots"] == PRODUCTION_BOOK_ROOTS
        and opening_artifact["sha256"] == PRODUCTION_BOOK_SHA256,
        "production opening artifact drifted",
    )
    require(
        isinstance(opening_artifact["path"], str)
        and opening_artifact["path"]
        and "\\" not in opening_artifact["path"]
        and not opening_artifact["path"].startswith("/")
        and ":" not in opening_artifact["path"]
        and ".." not in opening_artifact["path"].split("/"),
        "production opening path invalid",
    )
    require(
        opening["selection_policy_sha256"] == capability["selection_policy_sha256"],
        "production opening/capability binding drifted",
    )

    partition = document["partition"]
    require(
        isinstance(partition, dict)
        and set(partition)
        == {
            "campaign_set_sha256",
            "domain",
            "label_free",
            "method",
            "partition_sha256",
            "posthoc_rebalance",
            "role",
            "split_seed_u64",
            "validation_threshold_u64",
        }
        and partition["domain"] == PRODUCTION_PARTITION_DOMAIN
        and partition["label_free"] is True
        and partition["method"] == "content-hash-complete-trajectory-v1"
        and partition["posthoc_rebalance"] is False
        and partition["role"] in {"train", "validation"}
        and type(partition["split_seed_u64"]) is int
        and 0 <= partition["split_seed_u64"] < 1 << 64
        and type(partition["validation_threshold_u64"]) is int
        and 0 <= partition["validation_threshold_u64"] < 1 << 64,
        "production partition drifted",
    )
    _hex(partition["campaign_set_sha256"], 64, "production partition.campaign_set_sha256")
    _hex(partition["partition_sha256"], 64, "production partition.partition_sha256")

    producer = document["producer_artifact"]
    _relative_artifact(producer, "production provenance.producer_artifact")
    require(
        producer["kind"] == capability["artifact_role"]
        and producer["bytes"] == capability["artifact_bytes"]
        and producer["sha256"] == capability["artifact_sha256"],
        "production producer/capability binding drifted",
    )
    producer_capability = document["producer_capability"]
    require(
        isinstance(producer_capability, dict)
        and set(producer_capability) == {"bytes", "challenge", "schema", "sha256"}
        and type(producer_capability["bytes"]) is int
        and producer_capability["bytes"] > 0
        and producer_capability["challenge"] == capability["challenge"]
        and producer_capability["schema"]
        == "crazyhouse-datagen-production-capability-response/v1",
        "production producer capability binding drifted",
    )
    _hex(producer_capability["challenge"], 32, "production producer capability challenge")
    _hex(producer_capability["sha256"], 64, "production producer capability sha256")

    teacher = document["teacher"]
    require(
        isinstance(teacher, dict)
        and set(teacher)
        == {
            "artifact",
            "bound_policy",
            "evaluator_mode",
            "kind",
            "network_used",
            "route_backend_identity",
            "score_perspective",
            "search_settings_sha256",
            "selected_line_owns_score_and_pv",
            "synthetic",
        }
        and teacher["bound_policy"] == "selected-line-exact-only"
        and isinstance(teacher["evaluator_mode"], str)
        and teacher["evaluator_mode"]
        and teacher["kind"] == "legacy-network-product-search"
        and teacher["network_used"] is True
        and isinstance(teacher["route_backend_identity"], str)
        and teacher["route_backend_identity"]
        and teacher["score_perspective"] == "side-to-move"
        and teacher["selected_line_owns_score_and_pv"] is True
        and teacher["synthetic"] is False,
        "production teacher drifted",
    )
    teacher_artifact = teacher["artifact"]
    require(
        isinstance(teacher_artifact, dict)
        and set(teacher_artifact) == {"bytes", "path", "sha256"}
        and teacher_artifact["bytes"] == producer["bytes"]
        and teacher_artifact["path"] == producer["path"]
        and teacher_artifact["sha256"] == producer["sha256"],
        "production teacher artifact drifted",
    )
    _hex(teacher["search_settings_sha256"], 64, "production teacher search settings")
    toolchain = document["toolchain"]
    require(
        isinstance(toolchain, dict)
        and set(toolchain) == {"build_recipe_sha256", "identity", "sha256"}
        and toolchain["build_recipe_sha256"] == capability["build_recipe_sha256"]
        and toolchain["identity"] == capability["toolchain_identity"]
        and toolchain["sha256"] == capability["toolchain_sha256"],
        "production toolchain binding drifted",
    )
    return document
