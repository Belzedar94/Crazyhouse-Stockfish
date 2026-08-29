/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef ENGINE_ROUTING_H_INCLUDED
#define ENGINE_ROUTING_H_INCLUDED

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

#include "crazyhouse_profile.h"
#include "evaluate.h"
#include "nnue/crazyhouse_legacy_network.h"
#include "nnue/crazyhouse_v2_large_runtime.h"
#include "ruleset.h"

namespace Stockfish::EngineRouting {

using Epoch = std::uint64_t;

enum class BackendKind : std::uint8_t {
    None,
    OfficialChess,
    LegacyCrazyhouseV1,
    LargeCrazyhouseV2A0
};

enum class BackendReadiness : std::uint8_t {
    None,
    Ready,
    Failed
};

enum class ErrorCode : std::uint8_t {
    None,
    InvalidVariant,
    CrazyhouseProfileMissing,
    CrazyhouseProfileUnknown,
    CrazyhouseProfileHashMismatch,
    CrazyhouseChess960Rejected,
    RoutePending,
    CrazyhouseEvaluatorUnknown,
    CrazyhouseEvalFileEmpty,
    LegacyMissingFile,
    LegacyFileReadFailure,
    LegacyTruncatedFile,
    LegacyOversizedFile,
    LegacyVersionMismatch,
    LegacyNetworkHashMismatch,
    LegacyDescriptionLengthMismatch,
    LegacyDescriptionMismatch,
    LegacyTransformerHashMismatch,
    LegacyArchitectureHashMismatch,
    LegacyTensorLayoutMismatch,
    LegacyDigestMismatch,
    LegacySimdUnavailable,
    LargeEvalFileEmpty,
    LargeSha256Invalid,
    LargeProvenanceInvalid,
    LargeMissingFile,
    LargeNotRegularFile,
    LargeFileReadFailure,
    LargeWrongFileSize,
    LargeSha256Mismatch,
    LargeSimdUnavailable,
    LargeContainerRejected,
    OfficialEvalNotLoaded,
    PositionRequiresCommittedRoute,
    InvalidFen,
    MalformedPosition,
    IllegalMove,
    PositionEpochInvalid,
    BackendNotReady,
    BackendRouteMismatch,
    CrazyhouseMultiPVInvalid,
    CrazyhouseSearchNotBound,
    CrazyhouseEvalNotBound,
    CrazyhouseBenchNotBound,
    CrazyhouseSpeedtestNotBound,
    CrazyhouseExportNetNotBound
};

struct RouteOptions {
    Ruleset     ruleset             = Ruleset::CRAZYHOUSE;
    bool        chess960            = false;
    std::string crazyhouseProfile   = std::string(CrazyhouseProfile::Token);
    std::string chessEvalFile       = EvalFileDefaultName;
    std::string crazyhouseEvaluator = "legacy-v1";
    std::string crazyhouseEvalFile;
    std::string crazyhouseEvalSha256;
    std::string crazyhouseEvalProvenance;
};

struct BackendBinding {
    BackendKind      kind      = BackendKind::None;
    BackendReadiness readiness = BackendReadiness::None;
    Epoch            epoch     = 0;
    std::string      identity;
};

struct Snapshot {
    RouteOptions                pending;
    std::optional<RouteOptions> active;
    bool                        pendingDirty = true;
    std::optional<ErrorCode>    pendingError;
    std::optional<ErrorCode>    activeError;
    Epoch                       configEpoch = 1;
    std::optional<Epoch>        positionEpoch;
    BackendBinding              backend;
};

struct ApplyResult {
    bool      ready         = false;
    bool      epochAdvanced = false;
    ErrorCode error         = ErrorCode::None;
};

struct PositionResult {
    bool                       committed = false;
    ErrorCode                  error     = ErrorCode::None;
    std::optional<std::size_t> moveIndex;
    std::string                token;
    std::string                detail;
};

std::string_view backend_kind_name(BackendKind kind) noexcept;
std::string_view backend_readiness_name(BackendReadiness readiness) noexcept;
std::string_view error_code_name(ErrorCode code) noexcept;
std::string_view start_fen(Ruleset ruleset) noexcept;

ErrorCode legacy_load_error(Eval::NNUE::LegacyCrazyhouseNetworkV1::LoadStatus status) noexcept;
ErrorCode
large_runtime_load_error(Eval::NNUE::CrazyhouseV2::LargeRuntimeLoadStatus status) noexcept;
ErrorCode crazyhouse_profile_error(std::string_view token) noexcept;

bool same_route_options(const RouteOptions& lhs, const RouteOptions& rhs) noexcept;
bool snapshot_contract_valid(const Snapshot& snapshot) noexcept;
bool rule_position_ready(const Snapshot& snapshot) noexcept;
bool backend_matches_epoch(const Snapshot& snapshot) noexcept;
bool chess960_only_official_transition(const Snapshot& snapshot) noexcept;
bool chess_search_ready(const Snapshot& snapshot) noexcept;
bool crazyhouse_search_ready(const Snapshot& snapshot) noexcept;

}  // namespace Stockfish::EngineRouting

#endif  // ENGINE_ROUTING_H_INCLUDED
