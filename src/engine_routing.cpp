/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "engine_routing.h"

namespace Stockfish::EngineRouting {

namespace {

constexpr std::string_view ChessStartFen =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
constexpr std::string_view CrazyhouseStartFen =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1";

bool route_options_valid(const RouteOptions& options) noexcept {
    return is_valid_ruleset(options.ruleset)
        && !(options.ruleset == Ruleset::CRAZYHOUSE && options.chess960)
        && (options.ruleset != Ruleset::CRAZYHOUSE
            || CrazyhouseProfile::classify(options.crazyhouseProfile)
                 == CrazyhouseProfile::TokenStatus::Valid);
}

bool uncommitted_route_failure_matches(const Snapshot& snapshot) noexcept {
    if (snapshot.active.has_value() || !snapshot.activeError.has_value()
        || snapshot.pending.ruleset != Ruleset::CRAZYHOUSE)
        return false;

    if (snapshot.pending.chess960
        && snapshot.activeError == std::optional{ErrorCode::CrazyhouseChess960Rejected})
        return true;

    const ErrorCode profileError = crazyhouse_profile_error(snapshot.pending.crazyhouseProfile);
    return profileError != ErrorCode::None && snapshot.activeError == std::optional{profileError};
}

bool binding_matches_ruleset(const Snapshot& snapshot) noexcept {
    if (!snapshot.active.has_value())
        return snapshot.backend.kind == BackendKind::None;

    switch (snapshot.backend.kind)
    {
    case BackendKind::None :
        return true;
    case BackendKind::OfficialChess :
        return snapshot.active->ruleset == Ruleset::CHESS;
    case BackendKind::LegacyCrazyhouseV1 :
        return snapshot.active->ruleset == Ruleset::CRAZYHOUSE && !snapshot.active->chess960;
    case BackendKind::LargeCrazyhouseV2A0 :
        return snapshot.active->ruleset == Ruleset::CRAZYHOUSE && !snapshot.active->chess960;
    }
    return false;
}

}  // namespace

std::string_view backend_kind_name(BackendKind kind) noexcept {
    switch (kind)
    {
    case BackendKind::None :
        return "none";
    case BackendKind::OfficialChess :
        return "official-chess";
    case BackendKind::LegacyCrazyhouseV1 :
        return "legacy-v1";
    case BackendKind::LargeCrazyhouseV2A0 :
        return "large-v2-a0";
    }
    return "invalid";
}

std::string_view backend_readiness_name(BackendReadiness readiness) noexcept {
    switch (readiness)
    {
    case BackendReadiness::None :
        return "none";
    case BackendReadiness::Ready :
        return "ready";
    case BackendReadiness::Failed :
        return "failed";
    }
    return "invalid";
}

std::string_view error_code_name(ErrorCode code) noexcept {
    switch (code)
    {
    case ErrorCode::None :
        return "none";
    case ErrorCode::InvalidVariant :
        return "invalid_variant";
    case ErrorCode::CrazyhouseProfileMissing :
        return "crazyhouse_profile_missing";
    case ErrorCode::CrazyhouseProfileUnknown :
        return "crazyhouse_profile_unknown";
    case ErrorCode::CrazyhouseProfileHashMismatch :
        return "crazyhouse_profile_hash_mismatch";
    case ErrorCode::CrazyhouseChess960Rejected :
        return "crazyhouse_chess960_rejected";
    case ErrorCode::RoutePending :
        return "route_pending";
    case ErrorCode::CrazyhouseEvaluatorUnknown :
        return "crazyhouse_evaluator_unknown";
    case ErrorCode::CrazyhouseEvalFileEmpty :
        return "crazyhouse_eval_file_empty";
    case ErrorCode::LegacyMissingFile :
        return "legacy_missing_file";
    case ErrorCode::LegacyFileReadFailure :
        return "legacy_file_read_failure";
    case ErrorCode::LegacyTruncatedFile :
        return "legacy_truncated_file";
    case ErrorCode::LegacyOversizedFile :
        return "legacy_oversized_file";
    case ErrorCode::LegacyVersionMismatch :
        return "legacy_version_mismatch";
    case ErrorCode::LegacyNetworkHashMismatch :
        return "legacy_network_hash_mismatch";
    case ErrorCode::LegacyDescriptionLengthMismatch :
        return "legacy_description_length_mismatch";
    case ErrorCode::LegacyDescriptionMismatch :
        return "legacy_description_mismatch";
    case ErrorCode::LegacyTransformerHashMismatch :
        return "legacy_transformer_hash_mismatch";
    case ErrorCode::LegacyArchitectureHashMismatch :
        return "legacy_architecture_hash_mismatch";
    case ErrorCode::LegacyTensorLayoutMismatch :
        return "legacy_tensor_layout_mismatch";
    case ErrorCode::LegacyDigestMismatch :
        return "legacy_digest_mismatch";
    case ErrorCode::LegacySimdUnavailable :
        return "legacy_simd_unavailable";
    case ErrorCode::LargeEvalFileEmpty :
        return "large_eval_file_empty";
    case ErrorCode::LargeSha256Invalid :
        return "large_sha256_invalid";
    case ErrorCode::LargeProvenanceInvalid :
        return "large_provenance_invalid";
    case ErrorCode::LargeMissingFile :
        return "large_missing_file";
    case ErrorCode::LargeNotRegularFile :
        return "large_not_regular_file";
    case ErrorCode::LargeFileReadFailure :
        return "large_file_read_failure";
    case ErrorCode::LargeWrongFileSize :
        return "large_wrong_file_size";
    case ErrorCode::LargeSha256Mismatch :
        return "large_sha256_mismatch";
    case ErrorCode::LargeSimdUnavailable :
        return "large_simd_unavailable";
    case ErrorCode::LargeContainerRejected :
        return "large_container_rejected";
    case ErrorCode::OfficialEvalNotLoaded :
        return "official_eval_not_loaded";
    case ErrorCode::PositionRequiresCommittedRoute :
        return "position_requires_committed_route";
    case ErrorCode::InvalidFen :
        return "invalid_fen";
    case ErrorCode::MalformedPosition :
        return "malformed_position";
    case ErrorCode::IllegalMove :
        return "illegal_move";
    case ErrorCode::PositionEpochInvalid :
        return "position_epoch_invalid";
    case ErrorCode::BackendNotReady :
        return "backend_not_ready";
    case ErrorCode::BackendRouteMismatch :
        return "backend_route_mismatch";
    case ErrorCode::CrazyhouseMultiPVInvalid :
        return "crazyhouse_multipv_invalid";
    case ErrorCode::CrazyhouseSearchNotBound :
        return "crazyhouse_search_not_bound";
    case ErrorCode::CrazyhouseEvalNotBound :
        return "crazyhouse_eval_not_bound";
    case ErrorCode::CrazyhouseBenchNotBound :
        return "crazyhouse_bench_not_bound";
    case ErrorCode::CrazyhouseSpeedtestNotBound :
        return "crazyhouse_speedtest_not_bound";
    case ErrorCode::CrazyhouseExportNetNotBound :
        return "crazyhouse_export_net_not_bound";
    }
    return "invalid";
}

std::string_view start_fen(Ruleset ruleset) noexcept {
    switch (ruleset)
    {
    case Ruleset::CHESS :
        return ChessStartFen;
    case Ruleset::CRAZYHOUSE :
        return CrazyhouseStartFen;
    }
    return {};
}

ErrorCode legacy_load_error(Eval::NNUE::LegacyCrazyhouseNetworkV1::LoadStatus status) noexcept {
    using LoadStatus = Eval::NNUE::LegacyCrazyhouseNetworkV1::LoadStatus;
    switch (status)
    {
    case LoadStatus::Success :
        return ErrorCode::None;
    case LoadStatus::MissingFile :
        return ErrorCode::LegacyMissingFile;
    case LoadStatus::FileReadFailure :
        return ErrorCode::LegacyFileReadFailure;
    case LoadStatus::TruncatedFile :
        return ErrorCode::LegacyTruncatedFile;
    case LoadStatus::OversizedFile :
        return ErrorCode::LegacyOversizedFile;
    case LoadStatus::VersionMismatch :
        return ErrorCode::LegacyVersionMismatch;
    case LoadStatus::NetworkHashMismatch :
        return ErrorCode::LegacyNetworkHashMismatch;
    case LoadStatus::DescriptionLengthMismatch :
        return ErrorCode::LegacyDescriptionLengthMismatch;
    case LoadStatus::DescriptionMismatch :
        return ErrorCode::LegacyDescriptionMismatch;
    case LoadStatus::TransformerHashMismatch :
        return ErrorCode::LegacyTransformerHashMismatch;
    case LoadStatus::ArchitectureHashMismatch :
        return ErrorCode::LegacyArchitectureHashMismatch;
    case LoadStatus::TensorLayoutMismatch :
        return ErrorCode::LegacyTensorLayoutMismatch;
    case LoadStatus::DigestMismatch :
        return ErrorCode::LegacyDigestMismatch;
    }
    return ErrorCode::LegacyDigestMismatch;
}

ErrorCode
large_runtime_load_error(Eval::NNUE::CrazyhouseV2::LargeRuntimeLoadStatus status) noexcept {
    using LoadStatus = Eval::NNUE::CrazyhouseV2::LargeRuntimeLoadStatus;
    switch (status)
    {
    case LoadStatus::SUCCESS :
        return ErrorCode::None;
    case LoadStatus::INVALID_SHA256 :
        return ErrorCode::LargeSha256Invalid;
    case LoadStatus::INVALID_PROVENANCE :
        return ErrorCode::LargeProvenanceInvalid;
    case LoadStatus::MISSING_FILE :
        return ErrorCode::LargeMissingFile;
    case LoadStatus::NOT_REGULAR_FILE :
        return ErrorCode::LargeNotRegularFile;
    case LoadStatus::FILE_READ_FAILURE :
        return ErrorCode::LargeFileReadFailure;
    case LoadStatus::WRONG_FILE_SIZE :
        return ErrorCode::LargeWrongFileSize;
    case LoadStatus::ARTIFACT_SHA256_MISMATCH :
        return ErrorCode::LargeSha256Mismatch;
    case LoadStatus::SIMD_UNAVAILABLE :
        return ErrorCode::LargeSimdUnavailable;
    case LoadStatus::CONTAINER_REJECTED :
        return ErrorCode::LargeContainerRejected;
    }
    return ErrorCode::LargeContainerRejected;
}

ErrorCode crazyhouse_profile_error(std::string_view token) noexcept {
    switch (CrazyhouseProfile::classify(token))
    {
    case CrazyhouseProfile::TokenStatus::Valid :
        return ErrorCode::None;
    case CrazyhouseProfile::TokenStatus::Missing :
        return ErrorCode::CrazyhouseProfileMissing;
    case CrazyhouseProfile::TokenStatus::UnknownId :
        return ErrorCode::CrazyhouseProfileUnknown;
    case CrazyhouseProfile::TokenStatus::HashMismatch :
        return ErrorCode::CrazyhouseProfileHashMismatch;
    }
    return ErrorCode::CrazyhouseProfileHashMismatch;
}

bool same_route_options(const RouteOptions& lhs, const RouteOptions& rhs) noexcept {
    return lhs.ruleset == rhs.ruleset && lhs.chess960 == rhs.chess960
        && lhs.crazyhouseProfile == rhs.crazyhouseProfile && lhs.chessEvalFile == rhs.chessEvalFile
        && lhs.crazyhouseEvaluator == rhs.crazyhouseEvaluator
        && lhs.crazyhouseEvalFile == rhs.crazyhouseEvalFile
        && lhs.crazyhouseEvalSha256 == rhs.crazyhouseEvalSha256
        && lhs.crazyhouseEvalProvenance == rhs.crazyhouseEvalProvenance;
}

bool snapshot_contract_valid(const Snapshot& snapshot) noexcept {
    if (snapshot.configEpoch == 0 || !is_valid_ruleset(snapshot.pending.ruleset))
        return false;
    if (snapshot.active.has_value() && !route_options_valid(*snapshot.active))
        return false;
    if (snapshot.pendingError.has_value() && !snapshot.pendingDirty)
        return false;
    if (!binding_matches_ruleset(snapshot))
        return false;
    if (!snapshot.active.has_value()
        && (snapshot.positionEpoch.has_value() || snapshot.backend.kind != BackendKind::None
            || snapshot.backend.readiness == BackendReadiness::Ready))
        return false;
    if (snapshot.positionEpoch.has_value()
        && (!snapshot.active.has_value() || *snapshot.positionEpoch != snapshot.configEpoch))
        return false;
    if (!snapshot.pendingDirty && !snapshot.pendingError.has_value())
    {
        if (snapshot.active.has_value() && !same_route_options(snapshot.pending, *snapshot.active))
            return false;
        if (!snapshot.active.has_value() && !uncommitted_route_failure_matches(snapshot))
            return false;
    }

    switch (snapshot.backend.readiness)
    {
    case BackendReadiness::None :
        return !snapshot.active.has_value() && !snapshot.activeError.has_value()
            && snapshot.backend.kind == BackendKind::None && snapshot.backend.epoch == 0
            && snapshot.backend.identity.empty();
    case BackendReadiness::Failed :
        return snapshot.backend.kind == BackendKind::None && snapshot.activeError.has_value()
            && *snapshot.activeError != ErrorCode::None && snapshot.backend.identity.empty();
    case BackendReadiness::Ready :
        return snapshot.active.has_value() && snapshot.backend.kind != BackendKind::None
            && snapshot.backend.epoch == snapshot.configEpoch && !snapshot.backend.identity.empty()
            && !snapshot.activeError.has_value();
    }
    return false;
}

bool rule_position_ready(const Snapshot& snapshot) noexcept {
    return snapshot.active.has_value() && route_options_valid(*snapshot.active)
        && snapshot.positionEpoch.has_value() && *snapshot.positionEpoch == snapshot.configEpoch
        && !snapshot.pendingDirty && !snapshot.pendingError.has_value();
}

bool backend_matches_epoch(const Snapshot& snapshot) noexcept {
    return snapshot.active.has_value() && binding_matches_ruleset(snapshot)
        && snapshot.backend.readiness == BackendReadiness::Ready
        && snapshot.backend.kind != BackendKind::None
        && snapshot.backend.epoch == snapshot.configEpoch && !snapshot.activeError.has_value();
}

bool chess960_only_official_transition(const Snapshot& snapshot) noexcept {
    if (!snapshot_contract_valid(snapshot) || !snapshot.pendingDirty
        || snapshot.pendingError.has_value() || !snapshot.active.has_value()
        || !backend_matches_epoch(snapshot) || snapshot.backend.kind != BackendKind::OfficialChess)
        return false;

    const RouteOptions& active = *snapshot.active;
    return active.ruleset == Ruleset::CHESS && snapshot.pending.ruleset == Ruleset::CHESS
        && active.chess960 != snapshot.pending.chess960
        && active.crazyhouseProfile == snapshot.pending.crazyhouseProfile
        && active.chessEvalFile == snapshot.pending.chessEvalFile
        && active.crazyhouseEvaluator == snapshot.pending.crazyhouseEvaluator
        && active.crazyhouseEvalFile == snapshot.pending.crazyhouseEvalFile
        && active.crazyhouseEvalSha256 == snapshot.pending.crazyhouseEvalSha256
        && active.crazyhouseEvalProvenance == snapshot.pending.crazyhouseEvalProvenance;
}

bool chess_search_ready(const Snapshot& snapshot) noexcept {
    return rule_position_ready(snapshot) && snapshot.active->ruleset == Ruleset::CHESS
        && backend_matches_epoch(snapshot) && snapshot.backend.kind == BackendKind::OfficialChess;
}

bool crazyhouse_search_ready(const Snapshot& snapshot) noexcept {
    if (!rule_position_ready(snapshot) || snapshot.active->ruleset != Ruleset::CRAZYHOUSE
        || snapshot.active->chess960 || !backend_matches_epoch(snapshot))
        return false;
    if (snapshot.backend.kind == BackendKind::LegacyCrazyhouseV1)
    {
        const std::string_view expectedSha256 =
          snapshot.active->crazyhouseEvalSha256.empty()
            ? Eval::NNUE::LegacyCrazyhouseNetworkV1::RegisteredSha256
            : std::string_view(snapshot.active->crazyhouseEvalSha256);
        return snapshot.active->crazyhouseEvaluator == "legacy-v1"
            && snapshot.backend.identity == expectedSha256;
    }
    if (snapshot.backend.kind == BackendKind::LargeCrazyhouseV2A0)
        return snapshot.active->crazyhouseEvaluator == "large-v2-a0"
            && snapshot.backend.identity == snapshot.active->crazyhouseEvalSha256;
    return false;
}

}  // namespace Stockfish::EngineRouting
