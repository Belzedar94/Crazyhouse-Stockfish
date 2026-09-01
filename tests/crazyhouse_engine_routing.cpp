/*
  Fixture for the Engine-owned Crazyhouse routing vocabulary and admission
  invariants. UCI transcripts exercise transitions separately.
*/

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>

#include "engine_routing.h"
#include "nnue/crazyhouse_legacy_network.h"

namespace {

using namespace Stockfish;
namespace Routing = EngineRouting;
using namespace std::literals;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_engine_routing: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

void verify_names_and_start_fens() {
    require(Routing::backend_kind_name(Routing::BackendKind::None) == "none",
            "none backend name drifted");
    require(Routing::backend_kind_name(Routing::BackendKind::OfficialChess) == "official-chess",
            "official backend name drifted");
    require(Routing::backend_kind_name(Routing::BackendKind::LegacyCrazyhouseV1) == "legacy-v1",
            "legacy backend name drifted");
    require(Routing::backend_readiness_name(Routing::BackendReadiness::None) == "none",
            "none readiness name drifted");
    require(Routing::backend_readiness_name(Routing::BackendReadiness::Ready) == "ready",
            "ready name drifted");
    require(Routing::backend_readiness_name(Routing::BackendReadiness::Failed) == "failed",
            "failed name drifted");

    require(Routing::start_fen(Ruleset::CHESS)
              == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "chess start FEN drifted");
    require(Routing::start_fen(Ruleset::CRAZYHOUSE)
              == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1",
            "Crazyhouse start FEN drifted");
}

void verify_error_vocabulary() {
    using ErrorCode = Routing::ErrorCode;
    constexpr std::array expected = {
      std::pair{ErrorCode::None, "none"sv},
      std::pair{ErrorCode::InvalidVariant, "invalid_variant"sv},
      std::pair{ErrorCode::CrazyhouseProfileMissing, "crazyhouse_profile_missing"sv},
      std::pair{ErrorCode::CrazyhouseProfileUnknown, "crazyhouse_profile_unknown"sv},
      std::pair{ErrorCode::CrazyhouseProfileHashMismatch,
                "crazyhouse_profile_hash_mismatch"sv},
      std::pair{ErrorCode::CrazyhouseChess960Rejected, "crazyhouse_chess960_rejected"sv},
      std::pair{ErrorCode::RoutePending, "route_pending"sv},
      std::pair{ErrorCode::CrazyhouseEvalFileEmpty, "crazyhouse_eval_file_empty"sv},
      std::pair{ErrorCode::LegacyMissingFile, "legacy_missing_file"sv},
      std::pair{ErrorCode::LegacyFileReadFailure, "legacy_file_read_failure"sv},
      std::pair{ErrorCode::LegacyTruncatedFile, "legacy_truncated_file"sv},
      std::pair{ErrorCode::LegacyOversizedFile, "legacy_oversized_file"sv},
      std::pair{ErrorCode::LegacyVersionMismatch, "legacy_version_mismatch"sv},
      std::pair{ErrorCode::LegacyNetworkHashMismatch, "legacy_network_hash_mismatch"sv},
      std::pair{ErrorCode::LegacyDescriptionLengthMismatch,
                "legacy_description_length_mismatch"sv},
      std::pair{ErrorCode::LegacyDescriptionMismatch, "legacy_description_mismatch"sv},
      std::pair{ErrorCode::LegacyTransformerHashMismatch, "legacy_transformer_hash_mismatch"sv},
      std::pair{ErrorCode::LegacyArchitectureHashMismatch,
                "legacy_architecture_hash_mismatch"sv},
      std::pair{ErrorCode::LegacyTensorLayoutMismatch, "legacy_tensor_layout_mismatch"sv},
      std::pair{ErrorCode::LegacyDigestMismatch, "legacy_digest_mismatch"sv},
      std::pair{ErrorCode::OfficialEvalNotLoaded, "official_eval_not_loaded"sv},
      std::pair{ErrorCode::PositionRequiresCommittedRoute,
                "position_requires_committed_route"sv},
      std::pair{ErrorCode::InvalidFen, "invalid_fen"sv},
      std::pair{ErrorCode::MalformedPosition, "malformed_position"sv},
      std::pair{ErrorCode::IllegalMove, "illegal_move"sv},
      std::pair{ErrorCode::PositionEpochInvalid, "position_epoch_invalid"sv},
      std::pair{ErrorCode::BackendNotReady, "backend_not_ready"sv},
      std::pair{ErrorCode::BackendRouteMismatch, "backend_route_mismatch"sv},
      std::pair{ErrorCode::CrazyhouseSearchNotBound, "crazyhouse_search_not_bound"sv},
      std::pair{ErrorCode::CrazyhouseEvalNotBound, "crazyhouse_eval_not_bound"sv},
      std::pair{ErrorCode::CrazyhouseBenchNotBound, "crazyhouse_bench_not_bound"sv},
      std::pair{ErrorCode::CrazyhouseSpeedtestNotBound, "crazyhouse_speedtest_not_bound"sv},
      std::pair{ErrorCode::CrazyhouseExportNetNotBound, "crazyhouse_export_net_not_bound"sv},
    };
    for (const auto& [code, name] : expected)
        require(Routing::error_code_name(code) == name,
                "routing error name drifted: " + std::string(name));
}

void verify_legacy_error_mapping() {
    using LoadStatus = Eval::NNUE::LegacyCrazyhouseNetworkV1::LoadStatus;
    using ErrorCode  = Routing::ErrorCode;
    constexpr std::array expected = {
      std::pair{LoadStatus::Success, ErrorCode::None},
      std::pair{LoadStatus::MissingFile, ErrorCode::LegacyMissingFile},
      std::pair{LoadStatus::FileReadFailure, ErrorCode::LegacyFileReadFailure},
      std::pair{LoadStatus::TruncatedFile, ErrorCode::LegacyTruncatedFile},
      std::pair{LoadStatus::OversizedFile, ErrorCode::LegacyOversizedFile},
      std::pair{LoadStatus::VersionMismatch, ErrorCode::LegacyVersionMismatch},
      std::pair{LoadStatus::NetworkHashMismatch, ErrorCode::LegacyNetworkHashMismatch},
      std::pair{LoadStatus::DescriptionLengthMismatch,
                ErrorCode::LegacyDescriptionLengthMismatch},
      std::pair{LoadStatus::DescriptionMismatch, ErrorCode::LegacyDescriptionMismatch},
      std::pair{LoadStatus::TransformerHashMismatch,
                ErrorCode::LegacyTransformerHashMismatch},
      std::pair{LoadStatus::ArchitectureHashMismatch,
                ErrorCode::LegacyArchitectureHashMismatch},
      std::pair{LoadStatus::TensorLayoutMismatch, ErrorCode::LegacyTensorLayoutMismatch},
      std::pair{LoadStatus::DigestMismatch, ErrorCode::LegacyDigestMismatch},
    };
    for (const auto& [status, code] : expected)
        require(Routing::legacy_load_error(status) == code,
                "legacy load status mapping drifted");
}

Routing::Snapshot base_snapshot(Ruleset ruleset, Routing::Epoch epoch) {
    Routing::Snapshot snapshot;
    snapshot.pending.ruleset = ruleset;
    snapshot.active          = snapshot.pending;
    snapshot.pendingDirty    = false;
    snapshot.configEpoch     = epoch;
    return snapshot;
}

void verify_admission_invariants() {
    Routing::Snapshot startup;
    startup.pending.ruleset = Ruleset::CRAZYHOUSE;
    startup.pendingDirty    = true;
    startup.configEpoch     = 1;
    require(Routing::snapshot_contract_valid(startup), "startup snapshot rejected");
    require(!Routing::rule_position_ready(startup), "startup position became ready");
    require(!Routing::backend_matches_epoch(startup), "startup backend became ready");
    require(!Routing::chess_search_ready(startup), "startup chess search became ready");
    require(!Routing::crazyhouse_search_ready(startup), "startup Crazyhouse search became ready");

    Routing::Snapshot failed = base_snapshot(Ruleset::CRAZYHOUSE, 7);
    failed.backend.readiness = Routing::BackendReadiness::Failed;
    failed.activeError       = Routing::ErrorCode::LegacyMissingFile;
    require(Routing::snapshot_contract_valid(failed), "backend-failed snapshot rejected");
    require(!Routing::backend_matches_epoch(failed), "failed backend matched epoch");

    failed.positionEpoch = failed.configEpoch;
    require(Routing::rule_position_ready(failed), "rule-only position was not admitted");
    require(!Routing::crazyhouse_search_ready(failed), "failed backend admitted Crazyhouse search");

    Routing::Snapshot legacy = base_snapshot(Ruleset::CRAZYHOUSE, 8);
    legacy.positionEpoch      = legacy.configEpoch;
    legacy.backend.kind       = Routing::BackendKind::LegacyCrazyhouseV1;
    legacy.backend.readiness  = Routing::BackendReadiness::Ready;
    legacy.backend.epoch      = legacy.configEpoch;
    legacy.backend.identity =
      std::string(Eval::NNUE::LegacyCrazyhouseNetworkV1::RegisteredSha256);
    require(Routing::snapshot_contract_valid(legacy), "ready legacy snapshot rejected");
    require(Routing::backend_matches_epoch(legacy), "ready legacy backend missed epoch");
    require(!Routing::chess_search_ready(legacy), "legacy backend admitted chess search");
    require(Routing::crazyhouse_search_ready(legacy),
            "ready legacy route did not admit Crazyhouse search");

    Routing::Snapshot freshLegacy = legacy;
    freshLegacy.active->crazyhouseEvalSha256 = std::string(64, '1');
    freshLegacy.pending                         = *freshLegacy.active;
    freshLegacy.backend.identity               = freshLegacy.active->crazyhouseEvalSha256;
    require(Routing::snapshot_contract_valid(freshLegacy),
            "authenticated fresh legacy snapshot rejected");
    require(Routing::crazyhouse_search_ready(freshLegacy),
            "authenticated fresh legacy route did not admit Crazyhouse search");
    freshLegacy.backend.identity = std::string(64, '2');
    require(!Routing::crazyhouse_search_ready(freshLegacy),
            "wrong fresh legacy digest admitted Crazyhouse search");

    Routing::Snapshot chess = base_snapshot(Ruleset::CHESS, 9);
    chess.positionEpoch      = chess.configEpoch;
    chess.backend.kind       = Routing::BackendKind::OfficialChess;
    chess.backend.readiness  = Routing::BackendReadiness::Ready;
    chess.backend.epoch      = chess.configEpoch;
    chess.backend.identity   = "official-content-hash:test";
    require(Routing::snapshot_contract_valid(chess), "ready chess snapshot rejected");
    require(Routing::chess_search_ready(chess), "valid chess route did not admit search");

    chess.pending.chess960 = true;
    chess.pendingDirty     = true;
    require(!Routing::chess_search_ready(chess), "pending route admitted chess search");
    require(Routing::chess960_only_official_transition(chess),
            "sole chess Chess960 transition was not recognized");

    Routing::Snapshot changedEval = chess;
    changedEval.pending.chessEvalFile += ".other";
    require(!Routing::chess960_only_official_transition(changedEval),
            "evaluator replacement entered Chess960-only transition");

    Routing::Snapshot changedLegacy          = chess;
    changedLegacy.pending.crazyhouseEvalFile = "other-legacy.nnue";
    require(!Routing::chess960_only_official_transition(changedLegacy),
            "inactive evaluator mutation entered Chess960-only transition");

    Routing::Snapshot changedProfile          = chess;
    changedProfile.pending.crazyhouseProfile = "LICHESS_CRAZYHOUSE_2026_08_12@0";
    require(!Routing::chess960_only_official_transition(changedProfile),
            "profile mutation entered Chess960-only transition");

    Routing::Snapshot staleBackend = chess;
    --staleBackend.backend.epoch;
    require(!Routing::chess960_only_official_transition(staleBackend),
            "stale backend entered Chess960-only transition");

    chess.pendingDirty  = false;
    chess.pending       = *chess.active;
    chess.positionEpoch = chess.configEpoch - 1;
    require(!Routing::rule_position_ready(chess), "stale position epoch was admitted");

    Routing::Snapshot crossed = legacy;
    crossed.active->ruleset    = Ruleset::CHESS;
    require(!Routing::snapshot_contract_valid(crossed), "crossed legacy/chess route was valid");

    Routing::Snapshot ch960 = legacy;
    ch960.active->chess960   = true;
    require(!Routing::snapshot_contract_valid(ch960), "Crazyhouse Chess960 route was valid");
}

}  // namespace

int main() {
    static_assert(std::is_same_v<Routing::Epoch, std::uint64_t>);
    static_assert(std::numeric_limits<Routing::Epoch>::max() == UINT64_MAX);
    verify_names_and_start_fens();
    verify_error_vocabulary();
    verify_legacy_error_mapping();
    verify_admission_invariants();
    std::cout << "PASS crazyhouse_engine_routing vocabulary=PASS legacy_errors=13 "
                 "snapshots=PASS crazyhouse_search=READY_EXACT_ROUTE_ONLY\n";
    return EXIT_SUCCESS;
}
