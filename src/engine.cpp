/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "engine.h"

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdlib>
#include <filesystem>
#include <deque>
#include <iosfwd>
#include <limits>
#include <memory>
#include <ostream>
#include <sstream>
#include <string_view>
#include <utility>
#include <vector>

#include "evaluate.h"
#include "misc.h"
#include "nnue/network.h"
#include "nnue/nnue_common.h"
#include "numa.h"
#include "perft.h"
#include "position.h"
#include "search.h"
#include "shm.h"
#include "syzygy/tbprobe.h"
#include "types.h"
#include "uci.h"
#include "ucioption.h"

namespace Stockfish {

namespace NN = Eval::NNUE;

int MaxThreads = std::max(1024, 4 * int(get_hardware_concurrency()));

// The default configuration will attempt to group L3 domains up to 32 threads.
// This size was found to be a good balance between the Elo gain of increased
// history sharing and the speed loss from more cross-cache accesses (see
// PR#6526). The user can always explicitly override this behavior.
constexpr NumaAutoPolicy DefaultNumaPolicy = BundledL3Policy{32};

Engine::PositionSlot::PositionSlot(Ruleset ruleset) :
    position(ruleset),
    states(std::make_unique<std::deque<StateInfo>>(1)) {}

Engine::Engine(std::optional<std::filesystem::path> path,
               LegacyExecutionBackend               legacyExecutionBackend_) :
    binaryDirectory(path ? CommandLine::get_binary_directory(*path) : std::filesystem::path{}),
    legacyExecutionBackend(legacyExecutionBackend_),
    numaContext(NumaConfig::from_system(DefaultNumaPolicy)),
    positionSlot(std::make_unique<PositionSlot>(Ruleset::CHESS)),
    threads(),
    networkFile{std::nullopt, ""},
    network(numaContext, get_default_network()) {

    positionSlot->position.set(StartFEN, false, &positionSlot->states->back());

    std::string crazyhouseEvalDefault;
    std::string crazyhouseEvalShaDefault;
#ifdef OPENBENCH_CRAZYHOUSE_EXTERNAL_NETWORKS
    // Public OpenBench invokes bench without workload UCI options. Both
    // branch networks have already been downloaded at that point, so bind the
    // benchmark to a known Legacy artifact common to both engine copies.
    // Game play still supplies an explicit file and digest per side.
    constexpr std::array<std::pair<std::string_view, std::string_view>, 2>
      OpenBenchBenchNetworks{{
        {"../Networks/F1D0CD5A",
         "f1d0cd5a974e02a9c51dbeb37c64c7d97ca78399693a378e221c430fcb9e0a11"},
        {"../Networks/8EBF8478",
         "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"},
      }};
    for (const auto& [file, sha256] : OpenBenchBenchNetworks)
    {
        std::error_code error;
        const auto candidate =
          (binaryDirectory / path_from_utf8(std::string(file))).lexically_normal();
        if (std::filesystem::is_regular_file(candidate, error) && !error)
        {
            crazyhouseEvalDefault    = std::string(file);
            crazyhouseEvalShaDefault = std::string(sha256);
            break;
        }
    }
#else
    crazyhouseEvalDefault = NN::LegacyCrazyhouseNetworkV1::embedded_available()
                              ? std::string(NN::LegacyCrazyhouseNetworkV1::EmbeddedFileToken)
                              : std::string{};
#endif
    routing.pending.crazyhouseEvalFile   = crazyhouseEvalDefault;
    routing.pending.crazyhouseEvalSha256 = crazyhouseEvalShaDefault;

    options.add(  //
      "Debug Log File", Option("", [](const Option& o) {
          start_logger(path_from_utf8(std::string(o)));
          return std::nullopt;
      }));

    options.add(  //
      "NumaPolicy", Option("auto", [this](const Option& o) {
          if (!set_numa_config_from_option(o))
              return "NumaPolicy: invalid value '" + std::string(o) + "', keeping previous config.";
          return numa_config_information_as_string() + "\n"
               + thread_allocation_information_as_string();
      }));

    options.add(  //
      "Threads", Option(1, 1, MaxThreads, [this](const Option&) {
          resize_threads();
          return thread_allocation_information_as_string();
      }));

    options.add(  //
      "Hash", Option(16, 1, MaxHashMB, [this](const Option& o) {
          set_tt_size(o);
          return std::nullopt;
      }));

    options.add(  //
      "Clear Hash", Option([this](const Option&) {
          search_clear();
          return std::nullopt;
      }));

    options.add(  //
      "Ponder", Option(false));

    options.add(  //
      "MultiPV", Option(1, 1, MAX_MOVES));

    options.add(  //
      "CrazyhouseMultiPV", Option(
                             0, 0, std::numeric_limits<int>::max(),
                             [this](const Option&) {
                                 crazyhouseMultiPVValid = true;
                                 return std::nullopt;
                             },
                             [this](std::string_view) {
                                 crazyhouseMultiPVValid = false;
                                 return "ERROR setoption code=crazyhouse_multipv_invalid "
                                        "option=CrazyhouseMultiPV";
                             }));

    options.add("Skill Level", Option(20, 0, 20));

    options.add("Move Overhead", Option(10, 0, 5000));

    options.add("nodestime", Option(0, 0, 10000));

    options.add(  //
      "UCI_Chess960", Option(false, [this](const Option& o) {
          stage_chess960(int(o) != 0);
          return std::nullopt;
      }));

    options.add(  //
      "UCI_Variant",
      Option("crazyhouse var chess var crazyhouse", "crazyhouse", [this](const Option& o) {
          stage_ruleset(o == "chess" ? "chess" : "crazyhouse");
          return std::nullopt;
      }));

    options.add(  //
      "CrazyhouseProfile", Option(CrazyhouseProfile::Token.data(), [this](const Option& o) {
          stage_crazyhouse_profile(std::string(o));
          return std::nullopt;
      }));

    options.add("CrazyhouseCapabilityNonce", Option(""));

    options.add("UCI_LimitStrength", Option(false));

    options.add("UCI_Elo",
                Option(Stockfish::Search::Skill::LowestElo, Stockfish::Search::Skill::LowestElo,
                       Stockfish::Search::Skill::HighestElo));

    options.add("UCI_ShowWDL", Option(false));

    options.add(  //
      "SyzygyPath", Option("", [](const Option& o) {
          Tablebases::init(o);
          return std::nullopt;
      }));

    options.add("SyzygyProbeDepth", Option(1, 1, 100));

    options.add("Syzygy50MoveRule", Option(true));

    options.add("SyzygyProbeLimit", Option(7, 0, 7));

    options.add(  //
      "EvalFile", Option(EvalFileDefaultName, [this](const Option& o) {
          stage_chess_eval_file(std::string(o));
          return std::nullopt;
      }));

    options.add(  //
      "CrazyhouseEvaluator", Option(
                               "legacy-v1 var legacy-v1 var large-v2-a0", "legacy-v1",
                               [this](const Option& o) {
                                   if (o == "legacy-v1")
                                       stage_crazyhouse_evaluator("legacy-v1");
                                   else if (o == "large-v2-a0")
                                       stage_crazyhouse_evaluator("large-v2-a0");
                                   else
                                       std::abort();
                                   return std::nullopt;
                               },
                               [this](std::string_view value) {
                                   stage_crazyhouse_evaluator(std::string(value));
                                   return std::nullopt;
                               }));

    options.add(  //
      "CrazyhouseEvalFile", Option(crazyhouseEvalDefault.c_str(), [this](const Option& o) {
          stage_crazyhouse_eval_file(std::string(o));
          return std::nullopt;
      }));

    options.add(  //
      "CrazyhouseEvalSHA256", Option(crazyhouseEvalShaDefault.c_str(), [this](const Option& o) {
          stage_crazyhouse_eval_sha256(std::string(o));
          return std::nullopt;
      }));

    options.add(  //
      "CrazyhouseEvalProvenance", Option("", [this](const Option& o) {
          stage_crazyhouse_eval_provenance(std::string(o));
          return std::nullopt;
      }));

    threads.clear();
    threads.ensure_network_replicated();
    resize_threads();
}

void Engine::refresh_pending_dirty() noexcept {
    routing.pendingDirty = routing.pendingError.has_value() || !routing.active.has_value()
                        || !EngineRouting::same_route_options(routing.pending, *routing.active);
}

bool Engine::stage_ruleset(std::string_view value) {
    wait_for_search_finished();

    const auto parsed = ruleset_from_uci(value);
    if (!parsed.has_value())
    {
        routing.pendingError = EngineRouting::ErrorCode::InvalidVariant;
        routing.pendingDirty = true;
        return false;
    }

    routing.pending.ruleset = *parsed;
    if (routing.pendingError == std::optional{EngineRouting::ErrorCode::InvalidVariant})
        routing.pendingError.reset();
    refresh_pending_dirty();
    return true;
}

void Engine::stage_chess960(bool value) {
    wait_for_search_finished();
    routing.pending.chess960 = value;
    refresh_pending_dirty();
}

void Engine::stage_crazyhouse_profile(std::string value) {
    wait_for_search_finished();
    routing.pending.crazyhouseProfile = std::move(value);
    refresh_pending_dirty();
}

void Engine::stage_chess_eval_file(std::string value) {
    wait_for_search_finished();
    routing.pending.chessEvalFile = std::move(value);
    refresh_pending_dirty();
}

void Engine::stage_crazyhouse_evaluator(std::string value) {
    wait_for_search_finished();
    routing.pending.crazyhouseEvaluator = std::move(value);
    refresh_pending_dirty();
}

void Engine::stage_crazyhouse_eval_file(std::string value) {
    wait_for_search_finished();
    routing.pending.crazyhouseEvalFile = std::move(value);
    refresh_pending_dirty();
}

void Engine::stage_crazyhouse_eval_sha256(std::string value) {
    wait_for_search_finished();
    routing.pending.crazyhouseEvalSha256 = std::move(value);
    refresh_pending_dirty();
}

void Engine::stage_crazyhouse_eval_provenance(std::string value) {
    wait_for_search_finished();
    routing.pending.crazyhouseEvalProvenance = std::move(value);
    refresh_pending_dirty();
}

EngineRouting::Epoch Engine::advance_route_epoch() {
    if (routing.configEpoch == std::numeric_limits<EngineRouting::Epoch>::max())
        std::abort();
    return ++routing.configEpoch;
}

void Engine::clear_routed_backends() {
    legacyNetwork.reset();
    largeNetwork.reset();
    officialRouteInstalled = false;
    networkFile            = {std::nullopt, ""};
    network                = std::make_unique<NN::Network>();
}

void Engine::clear_route_runtime_state() {
    tt.clear(threads);
    threads.clear();
}

EngineRouting::ApplyResult Engine::apply_pending_route() {
    using namespace EngineRouting;

    wait_for_search_finished();

    if (routing.pendingError.has_value())
        return {false, false, *routing.pendingError};

    if (!routing.pendingDirty && routing.backend.readiness == BackendReadiness::Ready)
    {
        if (!backend_matches_epoch(routing))
            std::abort();
        return {true, false, ErrorCode::None};
    }

    const bool retryingFailure =
      !routing.pendingDirty && routing.backend.readiness == BackendReadiness::Failed;
    if (!routing.pendingDirty && !retryingFailure)
        std::abort();

    const RouteOptions requested = routing.pending;

    if (chess960_only_official_transition(routing))
    {
        if (!officialRouteInstalled || legacyNetwork || largeNetwork
            || !networkFile.current.has_value())
            std::abort();

        advance_route_epoch();
        routing.active       = requested;
        routing.pendingDirty = false;
        routing.pendingError.reset();
        routing.activeError.reset();
        routing.positionEpoch.reset();
        routing.backend.epoch = routing.configEpoch;

        if (!snapshot_contract_valid(routing) || !backend_matches_epoch(routing))
            std::abort();
        return {true, true, ErrorCode::None};
    }

    auto commit_failure = [&](ErrorCode error, std::optional<RouteOptions> active) {
        const bool advance = routing.pendingDirty;
        if (advance)
        {
            advance_route_epoch();
            routing.positionEpoch.reset();
            clear_route_runtime_state();
        }

        routing.active       = std::move(active);
        routing.pendingDirty = false;
        routing.pendingError.reset();
        routing.activeError = error;
        routing.backend = {BackendKind::None, BackendReadiness::Failed, routing.configEpoch, ""};

        if (!snapshot_contract_valid(routing))
            std::abort();
        return ApplyResult{false, advance, error};
    };

    if (requested.ruleset == Ruleset::CRAZYHOUSE)
    {
        const ErrorCode profileError = crazyhouse_profile_error(requested.crazyhouseProfile);
        if (profileError != ErrorCode::None)
        {
            if (retryingFailure)
                return {false, false, profileError};
            clear_routed_backends();
            return commit_failure(profileError, std::nullopt);
        }
    }

    if (requested.ruleset == Ruleset::CRAZYHOUSE && requested.chess960)
    {
        if (retryingFailure)
            return {false, false, ErrorCode::CrazyhouseChess960Rejected};
        clear_routed_backends();
        return commit_failure(ErrorCode::CrazyhouseChess960Rejected, std::nullopt);
    }

    clear_routed_backends();

    ErrorCode                                         loadError   = ErrorCode::None;
    BackendKind                                       backendKind = BackendKind::None;
    std::string                                       backendIdentity;
    std::unique_ptr<NN::Network>                      officialCandidate;
    NN::EvalFile                                      officialCandidateFile{std::nullopt, ""};
    std::unique_ptr<NN::LegacyCrazyhouseNetworkV1>    legacyCandidate;
    std::unique_ptr<NN::CrazyhouseV2::LargeRuntimeV1> largeCandidate;

    if (requested.ruleset == Ruleset::CRAZYHOUSE)
    {
        if (requested.crazyhouseEvaluator == "legacy-v1")
        {
            if (requested.crazyhouseEvalFile.empty())
                loadError = ErrorCode::CrazyhouseEvalFileEmpty;
            else
            {
                legacyCandidate =
                  std::make_unique<NN::LegacyCrazyhouseNetworkV1>(legacyExecutionBackend);
                const std::string_view expectedSha256 =
                  requested.crazyhouseEvalSha256.empty()
                    ? NN::LegacyCrazyhouseNetworkV1::RegisteredSha256
                    : std::string_view(requested.crazyhouseEvalSha256);
                NN::LegacyCrazyhouseNetworkV1::LoadResult result{
                  NN::LegacyCrazyhouseNetworkV1::LoadStatus::MissingFile, "network not loaded"};
                if (std::string_view(requested.crazyhouseEvalFile)
                    == NN::LegacyCrazyhouseNetworkV1::EmbeddedFileToken)
                    result = legacyCandidate->load_embedded();
                else
                {
                    auto path = path_from_utf8(requested.crazyhouseEvalFile);
                    if (path.is_relative())
                        path = binaryDirectory / path;
                    path   = path.lexically_normal();
                    result = legacyCandidate->load_file(path, expectedSha256);
                }
                loadError = legacy_load_error(result.status);

                if (loadError == ErrorCode::None && !legacyCandidate->loaded())
                    loadError = ErrorCode::LegacyDigestMismatch;
                if (loadError == ErrorCode::None
                    && legacyCandidate->description()
                         != NN::LegacyCrazyhouseNetworkV1::RegisteredDescription)
                    loadError = ErrorCode::LegacyDescriptionMismatch;
                if (loadError == ErrorCode::None
                    && legacyCandidate->artifact_sha256() != expectedSha256)
                    loadError = ErrorCode::LegacyDigestMismatch;
                if (loadError == ErrorCode::None
                    && legacyCandidate->execution_backend()
                         == NN::LegacyCrazyhouseNetworkV1::ExecutionBackend::Simd
                    && NN::LegacyCrazyhouseNetworkV1::compiled_simd_backend() == "none")
                    loadError = ErrorCode::LegacySimdUnavailable;

                if (loadError == ErrorCode::None)
                {
                    backendKind     = BackendKind::LegacyCrazyhouseV1;
                    backendIdentity = std::string(legacyCandidate->artifact_sha256());
                }
            }
        }
        else if (requested.crazyhouseEvaluator == "large-v2-a0")
        {
            if (requested.crazyhouseEvalFile.empty())
                loadError = ErrorCode::LargeEvalFileEmpty;
            else
            {
                auto path = path_from_utf8(requested.crazyhouseEvalFile);
                if (path.is_relative())
                    path = binaryDirectory / path;
                path = path.lexically_normal();

                largeCandidate    = std::make_unique<NN::CrazyhouseV2::LargeRuntimeV1>();
                const auto result = largeCandidate->load_file(path, requested.crazyhouseEvalSha256,
                                                              requested.crazyhouseEvalProvenance);
                loadError         = large_runtime_load_error(result.status);
                if (loadError == ErrorCode::None && !largeCandidate->loaded())
                    loadError = ErrorCode::LargeContainerRejected;
                if (loadError == ErrorCode::None
                    && largeCandidate->artifact_sha256() != requested.crazyhouseEvalSha256)
                    loadError = ErrorCode::LargeSha256Mismatch;
                if (loadError == ErrorCode::None)
                {
                    backendKind     = BackendKind::LargeCrazyhouseV2A0;
                    backendIdentity = std::string(largeCandidate->artifact_sha256());
                }
            }
        }
        else
            loadError = ErrorCode::CrazyhouseEvaluatorUnknown;
    }
    else
    {
        auto requestedPath = path_from_utf8(requested.chessEvalFile);
        if (requestedPath.empty())
            requestedPath = path_from_utf8(EvalFileDefaultName);

        officialCandidate = std::make_unique<NN::Network>();
        officialCandidate->load(binaryDirectory, requestedPath, officialCandidateFile);
        if (officialCandidateFile.current != std::optional{requestedPath})
            loadError = ErrorCode::OfficialEvalNotLoaded;
        else
        {
            backendKind = BackendKind::OfficialChess;
            backendIdentity =
              "official-content-hash:" + std::to_string(officialCandidate->get_content_hash());
        }
    }

    if (loadError != ErrorCode::None)
    {
        legacyCandidate.reset();
        largeCandidate.reset();
        officialCandidate.reset();
        return commit_failure(loadError, requested);
    }

    advance_route_epoch();
    routing.active       = requested;
    routing.pendingDirty = false;
    routing.pendingError.reset();
    routing.activeError.reset();
    routing.positionEpoch.reset();

    if (backendKind == BackendKind::OfficialChess)
    {
        networkFile            = std::move(officialCandidateFile);
        network                = std::move(officialCandidate);
        officialRouteInstalled = true;
        threads.ensure_network_replicated();
    }
    else if (backendKind == BackendKind::LegacyCrazyhouseV1)
        legacyNetwork = std::move(legacyCandidate);
    else if (backendKind == BackendKind::LargeCrazyhouseV2A0)
        largeNetwork = std::move(largeCandidate);
    else
        std::abort();

    routing.backend = {backendKind, BackendReadiness::Ready, routing.configEpoch,
                       std::move(backendIdentity)};
    clear_route_runtime_state();

    if (!snapshot_contract_valid(routing))
        std::abort();
    return {true, true, ErrorCode::None};
}

EngineRouting::PositionResult Engine::set_routed_position(const std::string&              fen,
                                                          const std::vector<std::string>& moves) {
    using namespace EngineRouting;

    wait_for_search_finished();

    PositionResult result;
    if (!routing.active.has_value())
    {
        routing.positionEpoch.reset();
        result.error  = ErrorCode::PositionRequiresCommittedRoute;
        result.detail = "position requires a committed route";
        return result;
    }

    auto candidate = std::make_unique<PositionSlot>(routing.active->ruleset);
    if (auto error = candidate->position.set(fen, routing.active->chess960, routing.active->ruleset,
                                             &candidate->states->back()))
    {
        routing.positionEpoch.reset();
        result.error  = ErrorCode::InvalidFen;
        result.detail = error->what();
        return result;
    }

    for (std::size_t index = 0; index < moves.size(); ++index)
    {
        const Move move = UCIEngine::to_move(candidate->position, moves[index]);
        if (move == Move::none())
        {
            routing.positionEpoch.reset();
            result.error     = ErrorCode::IllegalMove;
            result.moveIndex = index + 1;
            result.token     = moves[index];
            result.detail    = "illegal move";
            return result;
        }

        candidate->states->emplace_back();
        candidate->position.do_move(move, candidate->states->back());
    }

    positionSlot.swap(candidate);
    routing.positionEpoch = routing.configEpoch;
    result.committed      = true;

    if (!snapshot_contract_valid(routing) || !rule_position_ready(routing))
        std::abort();
    return result;
}

void Engine::invalidate_routed_position() noexcept { routing.positionEpoch.reset(); }

const EngineRouting::Snapshot& Engine::routing_snapshot() const noexcept { return routing; }

bool Engine::has_routed_official_network() const noexcept { return officialRouteInstalled; }

bool Engine::has_routed_legacy_network() const noexcept {
    return legacyNetwork && legacyNetwork->loaded();
}

bool Engine::has_routed_large_network() const noexcept {
    return largeNetwork && largeNetwork->loaded();
}

std::string_view Engine::routed_legacy_evaluator_mode() const noexcept {
    if (!has_routed_legacy_network())
        return "none";
    return legacyNetwork->execution_backend() == LegacyExecutionBackend::Simd
           ? "incremental-simd"
           : "incremental-scalar";
}

std::string_view Engine::routed_legacy_simd_backend() const noexcept {
    if (!has_routed_legacy_network()
        || legacyNetwork->execution_backend() != LegacyExecutionBackend::Simd)
        return "none";
    return NN::LegacyCrazyhouseNetworkV1::compiled_simd_backend();
}

std::string_view Engine::routed_crazyhouse_evaluator_mode() const noexcept {
    if (has_routed_legacy_network())
        return routed_legacy_evaluator_mode();
    if (has_routed_large_network())
        return "large-v2-a0-incremental";
    return "none";
}

std::string_view Engine::routed_crazyhouse_simd_backend() const noexcept {
    if (has_routed_legacy_network())
        return routed_legacy_simd_backend();
    if (has_routed_large_network())
        return NN::CrazyhouseV2::LargeRuntimeV1::simd_backend_name();
    return "none";
}

bool Engine::crazyhouse_multipv_valid() const noexcept { return crazyhouseMultiPVValid; }

std::variant<u64, PositionSetError>
Engine::perft(const std::string& fen, Depth depth, bool isChess960) {
    verify_network();

    return Benchmark::perft(fen, depth, isChess960);
}

std::variant<u64, EngineRouting::ErrorCode> Engine::routed_perft(Depth depth) {
    using namespace EngineRouting;

    wait_for_search_finished();

    if (!routing.active.has_value())
        return ErrorCode::PositionRequiresCommittedRoute;
    if (!routing.positionEpoch.has_value() || *routing.positionEpoch != routing.configEpoch)
        return ErrorCode::PositionEpochInvalid;
    if (!rule_position_ready(routing))
        std::abort();

    const u64 nodes = Benchmark::perft<true>(positionSlot->position, depth);
    if (!rule_position_ready(routing))
        std::abort();
    return nodes;
}

void Engine::go(Search::LimitsType& limits) {
    assert(limits.perft == 0);
    const bool chessReady      = EngineRouting::chess_search_ready(routing);
    const bool crazyhouseReady = EngineRouting::crazyhouse_search_ready(routing);
    if (chessReady == crazyhouseReady)
        std::abort();
    if (chessReady)
        verify_network();
    else
    {
        const bool backendLoaded = has_routed_legacy_network() != has_routed_large_network();
        if (!crazyhouseMultiPVValid || !backendLoaded)
            std::abort();
    }

    threads.start_thinking(options, positionSlot->position, positionSlot->states, limits);
}
void Engine::stop() { threads.stop = true; }

void Engine::search_clear() {
    wait_for_search_finished();

    tt.clear(threads);
    threads.clear();

    // TODO: does not work with multiple instances
    Tablebases::init(options["SyzygyPath"]);  // Free mapped files
}

void Engine::set_on_update_no_moves(std::function<void(const Engine::InfoShort&)>&& f) {
    updateContext.onUpdateNoMoves = std::move(f);
}

void Engine::set_on_update_full(std::function<void(const Engine::InfoFull&)>&& f) {
    updateContext.onUpdateFull = std::move(f);
}

void Engine::set_on_iter(std::function<void(const Engine::InfoIter&)>&& f) {
    updateContext.onIter = std::move(f);
}

void Engine::set_on_bestmove(std::function<void(std::string_view, std::string_view)>&& f) {
    updateContext.onBestmove = std::move(f);
}

void Engine::set_on_start(std::function<void()>&& f) { updateContext.onStart = std::move(f); }

void Engine::set_on_verify_network(std::function<void(std::string_view)>&& f) {
    onVerifyNetwork = std::move(f);
}

void Engine::wait_for_search_finished() { threads.main_thread()->wait_for_search_finished(); }

std::optional<PositionSetError> Engine::set_position(const std::string&              fen,
                                                     const std::vector<std::string>& moves) {
    auto candidate = std::make_unique<PositionSlot>(positionSlot->position.ruleset());
    auto err       = candidate->position.set(
      fen, options["UCI_Chess960"], positionSlot->position.ruleset(), &candidate->states->back());
    if (err.has_value())
        return err;

    for (const auto& move : moves)
    {
        auto m = UCIEngine::to_move(candidate->position, move);

        if (m == Move::none())
            return PositionSetError("Illegal move: " + move);

        candidate->states->emplace_back();
        candidate->position.do_move(m, candidate->states->back());
    }

    positionSlot.swap(candidate);
    return std::nullopt;
}

// modifiers

bool Engine::set_numa_config_from_option(const std::string& o) {
    if (o == "auto" || o == "system")
    {
        numaContext.set_numa_config(NumaConfig::from_system(DefaultNumaPolicy));
    }
    else if (o == "hardware")
    {
        // Don't respect affinity set in the system.
        numaContext.set_numa_config(NumaConfig::from_system(DefaultNumaPolicy, false));
    }
    else if (o == "none")
    {
        numaContext.set_numa_config(NumaConfig{});
    }
    else
    {
        auto parsed = NumaConfig::from_string(o);
        if (!parsed.has_value())
            return false;
        numaContext.set_numa_config(std::move(*parsed));
    }

    // Force reallocation of threads in case affinities need to change.
    resize_threads();
    threads.ensure_network_replicated();
    return true;
}

void Engine::resize_threads() {
    threads.wait_for_search_finished();
    threads.set(numaContext.get_numa_config(),
                {options, threads, tt, sharedHists, network, legacyNetwork, largeNetwork},
                updateContext);

    // Reallocate the hash with the new threadpool size
    set_tt_size(options["Hash"]);
    threads.ensure_network_replicated();
}

void Engine::set_tt_size(usize mb) {
    wait_for_search_finished();
    tt.resize(mb, threads);
}

void Engine::set_ponderhit(bool b) { threads.main_manager()->ponder = b; }

// network related

void Engine::verify_network() const {
    const auto file = path_from_utf8(std::string(options["EvalFile"]));
    network->verify(onVerifyNetwork, networkFile, file);

    auto statuses = network.get_status_and_errors();
    for (usize i = 0; i < statuses.size(); ++i)
    {
        const auto [status, error] = statuses[i];
        std::string message        = "Network replica " + std::to_string(i + 1) + ": ";
        if (status == SystemWideSharedConstantAllocationStatus::NoAllocation)
        {
            message += "No allocation.";
        }
        else if (status == SystemWideSharedConstantAllocationStatus::LocalMemory)
        {
            message += "Local memory.";
        }
        else if (status == SystemWideSharedConstantAllocationStatus::SharedMemory)
        {
            message += "Shared memory.";
        }
        else
        {
            message += "Unknown status.";
        }

        if (error.has_value())
        {
            message += " " + *error;
        }

        onVerifyNetwork(message);
    }
}

std::unique_ptr<Eval::NNUE::Network> Engine::get_default_network() {

    auto network_ = std::make_unique<NN::Network>();

    network_->load(binaryDirectory, std::filesystem::path{}, networkFile);

    return network_;
}

void Engine::load_network(const std::filesystem::path& file) {
    network.modify_and_replicate(
      [this, &file](NN::Network& network_) { network_.load(binaryDirectory, file, networkFile); });
    threads.clear();
    threads.ensure_network_replicated();
}

void Engine::save_network(const std::optional<std::filesystem::path>& file) {
    network.modify_and_replicate(
      [&file, this](NN::Network& network_) { network_.save(networkFile, file); });
}

// utility functions

void Engine::trace_eval() const {
    if (!EngineRouting::chess_search_ready(routing))
        std::abort();

    StateListPtr trace_states(new std::deque<StateInfo>(1));
    Position     p(positionSlot->position.ruleset());
    p.set(positionSlot->position.fen(), options["UCI_Chess960"], positionSlot->position.ruleset(),
          &trace_states->back());

    verify_network();

    sync_cout << "\n" << Eval::trace(p, *network) << sync_endl;
}

const OptionsMap& Engine::get_options() const { return options; }
OptionsMap&       Engine::get_options() { return options; }

std::string Engine::fen() const { return positionSlot->position.fen(); }

std::optional<PositionSetError> Engine::flip() { return positionSlot->position.flip(); }

std::string Engine::visualize() const {
    std::stringstream ss;
    ss << positionSlot->position;
    return ss.str();
}

int Engine::get_hashfull(int maxAge) const { return tt.hashfull(maxAge); }

std::vector<std::pair<usize, usize>> Engine::get_bound_thread_count_by_numa_node() const {
    auto                                 counts = threads.get_bound_thread_count_by_numa_node();
    const NumaConfig&                    cfg    = numaContext.get_numa_config();
    std::vector<std::pair<usize, usize>> ratios;
    NumaIndex                            n = 0;
    for (; n < counts.size(); ++n)
        ratios.emplace_back(counts[n], cfg.num_cpus_in_numa_node(n));
    if (!counts.empty())
        for (; n < cfg.num_numa_nodes(); ++n)
            ratios.emplace_back(0, cfg.num_cpus_in_numa_node(n));
    return ratios;
}

std::string Engine::get_numa_config_as_string() const {
    return numaContext.get_numa_config().to_string();
}

std::string Engine::numa_config_information_as_string() const {
    auto cfgStr = get_numa_config_as_string();
    return "Available processors: " + cfgStr;
}

std::string Engine::thread_binding_information_as_string() const {
    auto              boundThreadsByNode = get_bound_thread_count_by_numa_node();
    std::stringstream ss;
    if (boundThreadsByNode.empty())
        return ss.str();

    bool isFirst = true;

    for (auto&& [current, total] : boundThreadsByNode)
    {
        if (!isFirst)
            ss << ":";
        ss << current << "/" << total;
        isFirst = false;
    }

    return ss.str();
}

std::string Engine::thread_allocation_information_as_string() const {
    std::stringstream ss;

    usize threadsSize = threads.size();
    ss << "Using " << threadsSize << (threadsSize > 1 ? " threads" : " thread");

    auto boundThreadsByNodeStr = thread_binding_information_as_string();
    if (boundThreadsByNodeStr.empty())
        return ss.str();

    ss << " with NUMA node thread binding: ";
    ss << boundThreadsByNodeStr;

    return ss.str();
}
}
