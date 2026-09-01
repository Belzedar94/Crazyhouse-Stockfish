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

#include "uci.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iterator>
#include <optional>
#include <sstream>
#include <string_view>
#include <filesystem>
#include <utility>
#include <variant>
#include <vector>

#include "benchmark.h"
#include "crazyhouse_move_codec.h"
#include "engine.h"
#include "memory.h"
#include "movegen.h"
#include "position.h"
#include "score.h"
#include "search.h"
#include "types.h"
#include "ucioption.h"

namespace Stockfish {

using Time = std::chrono::steady_clock;
using ms   = std::chrono::milliseconds;

constexpr auto BenchmarkCommand = "speedtest";

constexpr Engine::LegacyExecutionBackend configured_legacy_execution_backend() noexcept {
#ifdef CRAZYHOUSE_LEGACY_BACKEND_SIMD
    return Engine::LegacyExecutionBackend::Simd;
#else
    return Engine::LegacyExecutionBackend::Scalar;
#endif
}

template<typename... Ts>
struct overload: Ts... {
    using Ts::operator()...;
};

template<typename... Ts>
overload(Ts...) -> overload<Ts...>;

void UCIEngine::print_info_string(std::string_view str) {
    sync_cout_start();
    for (auto& line : split(str, "\n"))
    {
        if (!is_whitespace(line))
        {
            std::cout << "info string " << line << '\n';
        }
    }
    sync_cout_end();
}

UCIEngine::UCIEngine(CommandLine cli_) :
    engine(cli_.argc > 0 ? std::optional{path_from_utf8(cli_.argv[0])} : std::nullopt,
           configured_legacy_execution_backend()),
    cli(std::move(cli_)) {

    engine.get_options().add_info_listener([](const std::optional<std::string>& str) {
        if (str.has_value())
            print_info_string(*str);
    });

    init_search_update_listeners();
}

void UCIEngine::init_search_update_listeners() {
    engine.set_on_iter([](const auto& i) { on_iter(i); });
    engine.set_on_update_no_moves([](const auto& i) { on_update_no_moves(i); });
    engine.set_on_update_full(
      [this](const auto& i) { on_update_full(i, engine.get_options()["UCI_ShowWDL"]); });
    engine.set_on_start([]() {});
    engine.set_on_bestmove([](const auto& bm, const auto& p) { on_bestmove(bm, p); });
    engine.set_on_verify_network([](const auto& s) { print_info_string(s); });
}

void UCIEngine::loop() {
    set_console_utf8();
    std::string token, cmd;

    for (int i = 1; i < cli.argc; ++i)
        cmd += std::string(cli.argv[i]) + " ";

    do
    {
        if (cli.argc == 1
            && !getline(std::cin, cmd))  // Wait for an input or an end-of-file (EOF) indication
            cmd = "quit";

        currentCmd = cmd;
        std::istringstream is(cmd);

        token.clear();  // Avoid a stale if getline() returns nothing or a blank line
        is >> token;

        if (token == "quit" || token == "stop")
            engine.stop();

        // The GUI sends 'ponderhit' to tell that the user has played the expected move.
        // So, 'ponderhit' is sent if pondering was done on the same move that the user
        // has played. The search should continue, but should also switch from pondering
        // to the normal search.
        else if (token == "ponderhit")
            engine.set_ponderhit(false);

        else if (token == "uci")
        {
            sync_cout << "id name " << engine_info(true) << "\n"
                      << engine.get_options() << sync_endl;

            sync_cout << "uciok" << sync_endl;
        }

        else if (token == "setoption")
            setoption(is);
        else if (token == "go")
        {
            // send info strings after the go command is sent for old GUIs and python-chess
            print_info_string(engine.numa_config_information_as_string());
            print_info_string(engine.thread_allocation_information_as_string());
            go(is);
        }
        else if (token == "position")
            position(is);
        else if (token == "ucinewgame")
            engine.search_clear();
        else if (token == "isready")
        {
            if (apply_route_for_command("isready", true))
            {
                if (!crazyhouseCapabilityPending || acknowledge_crazyhouse_capability())
                    sync_cout << "readyok" << sync_endl;
            }
        }

        // Add custom non-UCI commands, mainly for debugging purposes.
        else if (token == "flip")
        {
            if (auto err = engine.flip())
            {
                terminate_on_critical_error(err->what());
            }
        }
        else if (token == "bench")
        {
            if (admit_bench_command())
                bench(is);
        }
        else if (token == BenchmarkCommand)
        {
            if (admit_chess_command("speedtest",
                                    EngineRouting::ErrorCode::CrazyhouseSpeedtestNotBound, false))
                benchmark(is);
        }
        else if (token == "d")
            sync_cout << engine.visualize() << sync_endl;
        else if (token == "eval")
        {
            if (admit_chess_command("eval", EngineRouting::ErrorCode::CrazyhouseEvalNotBound, true))
                engine.trace_eval();
        }
        else if (token == "compiler")
            sync_cout << compiler_info() << sync_endl;
        else if (token == "export_net")
        {
            if (admit_chess_command("export_net",
                                    EngineRouting::ErrorCode::CrazyhouseExportNetNotBound, false))
            {
                std::optional<std::filesystem::path> file;
                std::string                          filename;

                if (is >> filename)
                    file = path_from_utf8(filename);

                engine.save_network(file);
            }
        }
        else if (token == "--help" || token == "help" || token == "--license" || token == "license")
            sync_cout
              << "\nStockfish is a powerful chess engine for playing and analyzing."
                 "\nIt is released as free software licensed under the GNU GPLv3 License."
                 "\nStockfish is normally used with a graphical user interface (GUI) and implements"
                 "\nthe Universal Chess Interface (UCI) protocol to communicate with a GUI, an API, etc."
                 "\nFor any further information, visit https://github.com/official-stockfish/Stockfish#readme"
                 "\nor read the corresponding README.md and Copying.txt files distributed along with this program.\n"
              << sync_endl;
        else if (!token.empty() && token[0] != '#')
            sync_cout << "Unknown command: '" << cmd << "'. Type help for more information."
                      << sync_endl;

    } while (token != "quit" && cli.argc <= 1);  // The command-line arguments are one-shot
}

Search::LimitsType UCIEngine::parse_limits(std::istream& is) {
    Search::LimitsType limits;
    std::string        token;

    limits.startTime = now();  // The search starts as early as possible

    while (is >> token)
    {
        if (token == "searchmoves")  // Needs to be the last command on the line
        {
            while (is >> token)
                limits.searchmoves.push_back(to_lower(token));
            break;
        }

        else if (token == "wtime")
            is >> limits.time[WHITE];
        else if (token == "btime")
            is >> limits.time[BLACK];
        else if (token == "winc")
            is >> limits.inc[WHITE];
        else if (token == "binc")
            is >> limits.inc[BLACK];
        else if (token == "movestogo")
            is >> limits.movestogo;
        else if (token == "depth")
            is >> limits.depth;
        else if (token == "nodes")
            is >> limits.nodes;
        else if (token == "movetime")
            is >> limits.movetime;
        else if (token == "mate")
            is >> limits.mate;
        else if (token == "perft")
            is >> limits.perft;
        else if (token == "infinite")
            limits.infinite = 1;
        else if (token == "ponder")
            limits.ponderMode = true;

        if (is.fail())
            terminate_on_critical_error("Invalid argument for '" + token + "'");
    }

    return limits;
}

void UCIEngine::go(std::istringstream& is) {

    Search::LimitsType limits = parse_limits(is);

    if (limits.perft)
        perft(limits);
    else if (admit_search_command())
        engine.go(limits);
}

void UCIEngine::bench(std::istream& args) {
    std::string token;
    u64         num, nodes = 0, cnt = 1;
    u64         nodesSearched = 0;
    const auto& options       = engine.get_options();

    engine.set_on_update_full([&](const auto& i) {
        nodesSearched = i.nodes;
        on_update_full(i, options["UCI_ShowWDL"]);
    });

    const auto& snapshot = engine.routing_snapshot();
    if (!snapshot.active.has_value())
        std::abort();
    std::vector<std::string> list =
      Benchmark::setup_bench(engine.fen(), snapshot.active->ruleset, args);

    num = count_if(list.begin(), list.end(),
                   [](const std::string& s) { return s.find("go ") == 0 || s.find("eval") == 0; });

    TimePoint elapsed = now();

    for (const auto& cmd : list)
    {
        std::istringstream is(cmd);
        is >> token;

        if (token == "go" || token == "eval")
        {
            std::cerr << "\nPosition: " << cnt++ << '/' << num << " (" << engine.fen() << ")"
                      << std::endl;
            if (token == "go")
            {
                Search::LimitsType limits = parse_limits(is);

                if (limits.perft)
                    nodesSearched = perft(limits);
                else
                {
                    engine.go(limits);
                    engine.wait_for_search_finished();
                }

                nodes += nodesSearched;
                nodesSearched = 0;
            }
            else
                engine.trace_eval();
        }
        else if (token == "setoption")
        {
            setoption(is);
            if (!apply_route_for_command("bench", false))
                return;
        }
        else if (token == "position")
        {
            position(is);
            if (!EngineRouting::rule_position_ready(engine.routing_snapshot()))
                return;
        }
        else if (token == "ucinewgame")
        {
            engine.search_clear();  // search_clear may take a while
            elapsed = now();
        }
    }

    elapsed = now() - elapsed + 1;  // Ensure positivity to avoid a 'divide by zero'

    dbg_print();

    std::cerr << "\n==========================="    //
              << "\nTotal time (ms) : " << elapsed  //
              << "\nNodes searched  : " << nodes    //
              << "\nNodes/second    : " << 1000 * nodes / elapsed << std::endl;

    // reset callback, to not capture a dangling reference to nodesSearched
    engine.set_on_update_full([&](const auto& i) { on_update_full(i, options["UCI_ShowWDL"]); });
}

void UCIEngine::benchmark(std::istream& args) {
    // Probably not very important for a test this long, but include for completeness and sanity.
    static constexpr int NUM_WARMUP_POSITIONS = 3;

    std::string token;
    u64         cnt = 1;

    engine.set_on_update_full([](const auto&) {});
    engine.set_on_iter([](const auto&) {});
    engine.set_on_update_no_moves([](const auto&) {});
    engine.set_on_bestmove([](const auto&, const auto&) {});
    engine.set_on_verify_network([](const auto&) {});

    Benchmark::BenchmarkSetup setup = Benchmark::setup_benchmark(args);

    const auto numGoCommands = count_if(setup.commands.begin(), setup.commands.end(),
                                        [](const std::string& s) { return s.find("go ") == 0; });


    // Set options once at the start.
    auto ss = std::istringstream("name Threads value " + std::to_string(setup.threads));
    setoption(ss);
    ss = std::istringstream("name Hash value " + std::to_string(setup.ttSize));
    setoption(ss);
    ss = std::istringstream("name UCI_Chess960 value false");
    setoption(ss);
    if (!apply_route_for_command("speedtest", false))
        return;

    // Warmup
    for (const auto& cmd : setup.commands)
    {
        std::istringstream is(cmd);
        is >> token;

        if (token == "go")
        {
            // One new line is produced by the search, so omit it here
            std::cerr << "\rWarmup position " << cnt++ << '/' << NUM_WARMUP_POSITIONS;

            Search::LimitsType limits = parse_limits(is);

            // Run with silenced network verification
            engine.go(limits);
            engine.wait_for_search_finished();
        }
        else if (token == "position")
            position(is);
        else if (token == "ucinewgame")
        {
            engine.search_clear();  // search_clear may take a while
        }

        if (cnt > NUM_WARMUP_POSITIONS)
            break;
    }

    std::cerr << "\n";

    cnt = 1;

    int           numHashfullReadings = 0;
    constexpr int hashfullAges[]      = {0, 999};  // Only normal hashfull and touched hash.
    constexpr int hashfullAgeCount    = std::size(hashfullAges);
    int           totalHashfull[hashfullAgeCount] = {0};
    int           maxHashfull[hashfullAgeCount]   = {0};

    auto updateHashfullReadings = [&]() {
        numHashfullReadings += 1;

        for (int i = 0; i < hashfullAgeCount; ++i)
        {
            const int hashfull = engine.get_hashfull(hashfullAges[i]);
            maxHashfull[i]     = std::max(maxHashfull[i], hashfull);
            totalHashfull[i] += hashfull;
        }
    };

    engine.search_clear();  // search_clear may take a while

    Time::time_point elapsed;
    Time::duration   totalTime(0);

    u64 nodes = 0, nodesSearched = 0;

    engine.set_on_update_full([&](const Engine::InfoFull& i) { nodesSearched = i.nodes; });

    engine.set_on_start([&elapsed, &nodesSearched]() {
        elapsed       = Time::now();
        nodesSearched = 0;
    });

    engine.set_on_bestmove(
      [&totalTime, &elapsed, &nodes, &nodesSearched](const auto&, const auto&) {
          totalTime += Time::now() - elapsed;
          nodes += nodesSearched;
      });

    for (const auto& cmd : setup.commands)
    {
        std::istringstream is(cmd);
        is >> token;

        if (token == "go")
        {
            // One new line is produced by the search, so omit it here
            std::cerr << "\rPosition " << cnt++ << '/' << numGoCommands;

            Search::LimitsType limits = parse_limits(is);

            // Run with silenced network verification
            engine.go(limits);
            engine.wait_for_search_finished();

            updateHashfullReadings();
        }
        else if (token == "position")
            position(is);
        else if (token == "ucinewgame")
        {
            engine.search_clear();  // search_clear may take a while
        }
    }

    // Ensure positivity to avoid a 'divide by zero'
    const auto totalTimeMs = std::max<i64>(std::chrono::duration_cast<ms>(totalTime).count(), 1LL);

    dbg_print();

    std::cerr << "\n";

    static_assert(
      std::size(hashfullAges) == 2 && hashfullAges[0] == 0 && hashfullAges[1] == 999,
      "Hardcoded for display. Would complicate the code needlessly in the current state.");

    std::string threadBinding = engine.thread_binding_information_as_string();
    if (threadBinding.empty())
        threadBinding = "none";

    // clang-format off

    std::cerr << "==========================="
              << "\nVersion                    : "
              << engine_version_info()
              // "\nCompiled by                : "
              << compiler_info()
              << "Large pages                : " << (has_large_pages() ? "yes" : "no")
              << "\nUser invocation            : " << BenchmarkCommand << " "
              << setup.originalInvocation << "\nFilled invocation          : " << BenchmarkCommand
              << " " << setup.filledInvocation
              << "\nAvailable processors       : " << engine.get_numa_config_as_string()
              << "\nThread count               : " << setup.threads
              << "\nThread binding             : " << threadBinding
              << "\nTT size [MiB]              : " << setup.ttSize
              << "\nHash max, avg [per mille]  : "
              << "\n    single search          : " << maxHashfull[0] << ", "
              << totalHashfull[0] / numHashfullReadings
              << "\n    single game            : " << maxHashfull[1] << ", "
              << totalHashfull[1] / numHashfullReadings
              << "\nTotal nodes searched       : " << nodes
              << "\nTotal search time [s]      : " << totalTimeMs / 1000.0
              << "\nNodes/second               : " << 1000 * nodes / totalTimeMs << std::endl;

    // clang-format on

    init_search_update_listeners();
}

void UCIEngine::setoption(std::istringstream& is) {
    engine.wait_for_search_finished();

    std::istringstream preview(is.str());
    std::string        token, name, value;
    preview >> token;
    if (token == "setoption")
        preview >> token;
    if (token == "name")
    {
        while (preview >> token && token != "value")
            name += (name.empty() ? "" : " ") + token;
        while (preview >> token)
            value += (value.empty() ? "" : " ") + token;
    }

    if (to_lower(name) == "uci_variant")
        engine.stage_ruleset(to_lower(value));

    if (to_lower(name) == "crazyhousecapabilitynonce")
    {
        crazyhouseCapabilityNonce   = value;
        crazyhouseCapabilityPending = true;
    }

    engine.get_options().setoption(is);
}

bool UCIEngine::acknowledge_crazyhouse_capability() {
    const auto validHex = [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); };

    if (crazyhouseCapabilityNonce.size() != 32
        || !std::all_of(crazyhouseCapabilityNonce.begin(), crazyhouseCapabilityNonce.end(),
                        validHex))
    {
        sync_cout << "info string ERROR isready code=crazyhouse_capability_nonce_invalid"
                  << sync_endl;
        return false;
    }

    const auto& snapshot = engine.routing_snapshot();
    const bool  routedEvaluator =
      (snapshot.backend.kind == EngineRouting::BackendKind::LegacyCrazyhouseV1
       && engine.has_routed_legacy_network())
      || (snapshot.backend.kind == EngineRouting::BackendKind::LargeCrazyhouseV2A0
          && engine.has_routed_large_network());
    if (!snapshot.active.has_value() || snapshot.active->ruleset != Ruleset::CRAZYHOUSE
        || snapshot.backend.readiness != EngineRouting::BackendReadiness::Ready || !routedEvaluator)
    {
        sync_cout << "info string ERROR isready code=crazyhouse_capability_route_invalid"
                  << sync_endl;
        return false;
    }

    sync_cout << "info string crazyhouse_capability_ack status=ok profile=" << CrazyhouseProfile::Id
              << " profile_sha256=" << CrazyhouseProfile::Sha256
              << " nonce=" << crazyhouseCapabilityNonce << sync_endl;
    crazyhouseCapabilityPending = false;
    return true;
}

void UCIEngine::report_route_error(std::string_view           command,
                                   EngineRouting::ErrorCode   error,
                                   std::optional<std::size_t> moveIndex,
                                   std::string_view           token) {
    const auto& snapshot = engine.routing_snapshot();
    const auto  ruleset =
      snapshot.active.has_value() ? snapshot.active->ruleset : snapshot.pending.ruleset;

    std::ostringstream message;
    message << "info string ERROR " << command << " code=" << EngineRouting::error_code_name(error)
            << " ruleset=" << ruleset_name(ruleset) << " epoch=" << snapshot.configEpoch
            << " backend=" << EngineRouting::backend_kind_name(snapshot.backend.kind)
            << " position=" << (EngineRouting::rule_position_ready(snapshot) ? "valid" : "invalid");
    if (moveIndex.has_value())
        message << " move_index=" << *moveIndex;
    if (!token.empty())
        message << " token=" << token;
    sync_cout << message.str() << sync_endl;
}

bool UCIEngine::apply_route_for_command(std::string_view command, bool retryFailed) {
    const auto& before = engine.routing_snapshot();
    if (!retryFailed && !before.pendingDirty && !before.pendingError.has_value())
        return true;

    const auto  result   = engine.apply_pending_route();
    const auto& snapshot = engine.routing_snapshot();
    if (result.ready)
    {
        if (!snapshot.active.has_value())
            std::abort();
        const bool         crazyhouse = snapshot.active->ruleset == Ruleset::CRAZYHOUSE;
        std::ostringstream routeCommit;
        routeCommit << "info string route_commit status=ok ruleset="
                    << ruleset_name(snapshot.active->ruleset) << " profile="
                    << (crazyhouse ? CrazyhouseProfile::Id : std::string_view{"none"})
                    << " profile_sha256="
                    << (crazyhouse ? CrazyhouseProfile::Sha256 : std::string_view{"none"})
                    << " epoch=" << snapshot.configEpoch
                    << " backend=" << EngineRouting::backend_kind_name(snapshot.backend.kind)
                    << " identity=" << snapshot.backend.identity;
        if (crazyhouse)
        {
            const std::string_view evaluator = engine.routed_crazyhouse_evaluator_mode();
            if (evaluator == "none")
                std::abort();
            routeCommit << " evaluator=" << evaluator;
            const std::string_view simdBackend = engine.routed_crazyhouse_simd_backend();
            if (snapshot.backend.kind == EngineRouting::BackendKind::LargeCrazyhouseV2A0)
            {
                if (simdBackend == "none")
                    std::abort();
                routeCommit << " transformer_update=scalar-delta parity_simd_backend="
                            << simdBackend;
            }
            else if (evaluator == "incremental-simd")
            {
                if (simdBackend == "none")
                    std::abort();
                routeCommit << " simd_backend=" << simdBackend;
            }
        }
        sync_cout << routeCommit.str() << sync_endl;
        return true;
    }

    report_route_error(command, result.error);
    const auto ruleset =
      snapshot.active.has_value() ? snapshot.active->ruleset : snapshot.pending.ruleset;
    sync_cout << "info string READY state=failed code="
              << EngineRouting::error_code_name(result.error)
              << " ruleset=" << ruleset_name(ruleset) << " epoch=" << snapshot.configEpoch
              << " backend=" << EngineRouting::backend_kind_name(snapshot.backend.kind)
              << " position="
              << (EngineRouting::rule_position_ready(snapshot) ? "valid" : "invalid")
              << " readyok_withheld=1" << sync_endl;
    return false;
}

bool UCIEngine::admit_chess_command(std::string_view         command,
                                    EngineRouting::ErrorCode crazyhouseError,
                                    bool                     requirePosition) {
    using namespace EngineRouting;

    if (!apply_route_for_command(command, false))
        return false;

    const auto& snapshot = engine.routing_snapshot();
    if (!snapshot.active.has_value())
    {
        report_route_error(command, ErrorCode::PositionRequiresCommittedRoute);
        return false;
    }
    if (snapshot.active->ruleset == Ruleset::CRAZYHOUSE)
    {
        report_route_error(command, crazyhouseError);
        return false;
    }
    if (snapshot.backend.readiness != BackendReadiness::Ready)
    {
        report_route_error(command, ErrorCode::BackendNotReady);
        return false;
    }
    if (!backend_matches_epoch(snapshot) || snapshot.backend.kind != BackendKind::OfficialChess)
    {
        report_route_error(command, ErrorCode::BackendRouteMismatch);
        return false;
    }
    if (requirePosition && !rule_position_ready(snapshot))
    {
        report_route_error(command, ErrorCode::PositionEpochInvalid);
        return false;
    }
    return true;
}

bool UCIEngine::admit_bench_command() {
    using namespace EngineRouting;

    if (!apply_route_for_command("bench", false))
        return false;

    const auto& routed = engine.routing_snapshot();
    if (routed.active.has_value() && !rule_position_ready(routed))
    {
        const auto position =
          engine.set_routed_position(std::string(start_fen(routed.active->ruleset)), {});
        if (!position.committed)
        {
            report_route_error("bench", position.error, position.moveIndex, position.token);
            return false;
        }
    }

    const auto& snapshot = engine.routing_snapshot();
    if (!snapshot.active.has_value())
    {
        report_route_error("bench", ErrorCode::PositionRequiresCommittedRoute);
        return false;
    }
    if (snapshot.backend.readiness != BackendReadiness::Ready)
    {
        report_route_error("bench", ErrorCode::BackendNotReady);
        return false;
    }
    if (!backend_matches_epoch(snapshot))
    {
        report_route_error("bench", ErrorCode::BackendRouteMismatch);
        return false;
    }

    if (snapshot.active->ruleset == Ruleset::CRAZYHOUSE)
    {
        const bool routedEvaluator = (snapshot.backend.kind == BackendKind::LegacyCrazyhouseV1
                                      && engine.has_routed_legacy_network())
                                  || (snapshot.backend.kind == BackendKind::LargeCrazyhouseV2A0
                                      && engine.has_routed_large_network());
        if (snapshot.active->chess960 || !crazyhouse_search_ready(snapshot) || !routedEvaluator
            || !engine.crazyhouse_multipv_valid())
        {
            report_route_error("bench", ErrorCode::CrazyhouseBenchNotBound);
            return false;
        }
        return true;
    }

    if (snapshot.backend.kind != BackendKind::OfficialChess
        || !engine.has_routed_official_network())
    {
        report_route_error("bench", ErrorCode::BackendRouteMismatch);
        return false;
    }
    return true;
}

bool UCIEngine::admit_search_command() {
    using namespace EngineRouting;

    if (!apply_route_for_command("go", false))
        return false;

    const auto& snapshot = engine.routing_snapshot();
    if (!snapshot.active.has_value())
    {
        report_route_error("go", ErrorCode::PositionRequiresCommittedRoute);
        return false;
    }
    if (snapshot.backend.readiness == BackendReadiness::Failed)
    {
        if (!snapshot.activeError.has_value())
            std::abort();
        report_route_error("go", *snapshot.activeError);
        return false;
    }
    if (snapshot.backend.readiness != BackendReadiness::Ready)
    {
        report_route_error("go", ErrorCode::BackendNotReady);
        return false;
    }
    if (!rule_position_ready(snapshot))
    {
        report_route_error("go", ErrorCode::PositionEpochInvalid);
        return false;
    }

    if (snapshot.active->ruleset == Ruleset::CRAZYHOUSE)
    {
        const bool routedEvaluator = (snapshot.backend.kind == BackendKind::LegacyCrazyhouseV1
                                      && engine.has_routed_legacy_network())
                                  || (snapshot.backend.kind == BackendKind::LargeCrazyhouseV2A0
                                      && engine.has_routed_large_network());
        if (!backend_matches_epoch(snapshot) || !routedEvaluator)
        {
            report_route_error("go", ErrorCode::BackendRouteMismatch);
            return false;
        }
        if (!crazyhouse_search_ready(snapshot))
        {
            report_route_error("go", ErrorCode::CrazyhouseSearchNotBound);
            return false;
        }
        if (!engine.crazyhouse_multipv_valid())
        {
            report_route_error("go", ErrorCode::CrazyhouseMultiPVInvalid);
            return false;
        }
        return true;
    }

    if (!backend_matches_epoch(snapshot) || snapshot.backend.kind != BackendKind::OfficialChess
        || !engine.has_routed_official_network())
    {
        report_route_error("go", ErrorCode::BackendRouteMismatch);
        return false;
    }
    return true;
}

u64 UCIEngine::perft(const Search::LimitsType& limits) {
    if (!apply_route_for_command("go", false))
        return 0;

    auto result = engine.routed_perft(limits.perft);
    if (auto error = std::get_if<EngineRouting::ErrorCode>(&result))
    {
        report_route_error("go", *error);
        return 0;
    }

    auto nodes = std::get<u64>(result);
    sync_cout << "\nNodes searched: " << nodes << "\n" << sync_endl;
    return nodes;
}

void UCIEngine::position(std::istringstream& is) {
    std::string token, fen;

    if (!(is >> token))
    {
        engine.invalidate_routed_position();
        report_route_error("position", EngineRouting::ErrorCode::MalformedPosition);
        return;
    }

    const auto& snapshot = engine.routing_snapshot();
    if (!snapshot.active.has_value())
    {
        engine.invalidate_routed_position();
        report_route_error("position", EngineRouting::ErrorCode::PositionRequiresCommittedRoute);
        return;
    }
    if (token == "startpos")
    {
        fen = EngineRouting::start_fen(snapshot.active->ruleset);
        if (is >> token)
        {
            if (token != "moves")
            {
                engine.invalidate_routed_position();
                report_route_error("position", EngineRouting::ErrorCode::MalformedPosition);
                return;
            }
        }
    }
    else if (token == "fen")
        while (is >> token && token != "moves")
            fen += token + " ";
    else
    {
        engine.invalidate_routed_position();
        report_route_error("position", EngineRouting::ErrorCode::MalformedPosition);
        return;
    }

    std::vector<std::string> moves;

    while (is >> token)
    {
        moves.push_back(token);
    }

    const auto result = engine.set_routed_position(fen, moves);
    if (!result.committed)
        report_route_error("position", result.error, result.moveIndex, result.token);
}

namespace {

struct WinRateParams {
    double a;
    double b;
};

WinRateParams win_rate_params(const Position& pos) {

    int material = pos.count<PAWN>() + 3 * pos.count<KNIGHT>() + 3 * pos.count<BISHOP>()
                 + 5 * pos.count<ROOK>() + 9 * pos.count<QUEEN>();

    // The fitted model only uses data for material counts in [17, 78], and is anchored at count 58.
    double m = std::clamp(material, 17, 78) / 58.0;

    // Return a = p_a(material) and b = p_b(material), see github.com/official-stockfish/WDL_model
    constexpr double as[] = {-72.32565836, 185.93832038, -144.58862193, 416.44950446};
    constexpr double bs[] = {83.86794042, -136.06112997, 69.98820887, 47.62901433};

    double a = (((as[0] * m + as[1]) * m + as[2]) * m) + as[3];
    double b = (((bs[0] * m + bs[1]) * m + bs[2]) * m) + bs[3];

    return {a, b};
}

// The win rate model is 1 / (1 + exp((a - eval) / b)), where a = p_a(material) and b = p_b(material).
// It fits the LTC fishtest statistics rather accurately.
int win_rate_model(Value v, const Position& pos) {

    auto [a, b] = win_rate_params(pos);

    // Return the win rate in per mille units, rounded to the nearest integer.
    return int(0.5 + 1000 / (1 + std::exp((a - double(v)) / b)));
}
}

std::string UCIEngine::format_score(const Score& s) {
    constexpr int TB_CP = 20000;
    const auto    format =
      overload{[](Score::Mate mate) -> std::string {
                   auto m = (mate.plies > 0 ? (mate.plies + 1) : mate.plies) / 2;
                   return std::string("mate ") + std::to_string(m);
               },
               [TB_CP](Score::Tablebase tb) -> std::string {
                   return std::string("cp ") + std::to_string((tb.win ? TB_CP : -TB_CP) - tb.plies);
               },
               [](Score::InternalUnits units) -> std::string {
                   return std::string("cp ") + std::to_string(units.value);
               }};

    return s.visit(format);
}

// Turns a Value to an integer centipawn number,
// without treatment of mate and similar special scores.
int UCIEngine::to_cp(Value v, const Position& pos) {

    // In general, the score can be defined via the WDL as
    // (log(1/L - 1) - log(1/W - 1)) / (log(1/L - 1) + log(1/W - 1)).
    // Based on our win_rate_model, this simply yields v / a.

    auto [a, b] = win_rate_params(pos);

    return int(std::round(100 * int(v) / a));
}

std::string UCIEngine::wdl(Value v, const Position& pos) {
    std::stringstream ss;

    int wdl_w = win_rate_model(v, pos);
    int wdl_l = win_rate_model(-v, pos);
    int wdl_d = 1000 - wdl_w - wdl_l;
    ss << wdl_w << " " << wdl_d << " " << wdl_l;

    return ss.str();
}

std::string UCIEngine::square(Square s) {
    return std::string{char('a' + file_of(s)), char('1' + rank_of(s))};
}

std::string UCIEngine::move(Move m, bool chess960) {
    if (m == Move::none())
        return "(none)";

    if (m == Move::null())
        return "0000";

    if (m.is_drop())
        return format_drop_uci(m);

    Square from = m.from_sq();
    Square to   = m.to_sq();

    if (m.type_of() == CASTLING && !chess960)
        to = make_square(to > from ? FILE_G : FILE_C, rank_of(from));

    std::string move = square(from) + square(to);

    if (m.type_of() == PROMOTION)
        move += " pnbrqk"[m.promotion_type()];

    return move;
}


std::string UCIEngine::to_lower(std::string str) {
    std::transform(str.begin(), str.end(), str.begin(),
                   [](unsigned char c) { return std::tolower(c); });

    return str;
}

Move UCIEngine::to_move(const Position& pos, std::string str) {
    if (str.find('@') != std::string::npos)
    {
        const auto parsed = parse_drop_uci(str);
        if (!parsed)
            return Move::none();

        for (const auto& move : MoveList<LEGAL>(pos))
            if (move == *parsed)
                return move;

        return Move::none();
    }

    str = to_lower(str);

    for (const auto& m : MoveList<LEGAL>(pos))
        if (str == move(m, pos.is_chess960()))
            return m;

    return Move::none();
}

void UCIEngine::on_update_no_moves(const Engine::InfoShort& info) {
    sync_cout << "info depth " << info.depth << " score " << format_score(info.score) << sync_endl;
}

void UCIEngine::on_update_full(const Engine::InfoFull& info, bool showWDL) {
    std::stringstream ss;

    ss << "info";
    ss << " depth " << info.depth                 //
       << " seldepth " << info.selDepth           //
       << " multipv " << info.multiPV             //
       << " score " << format_score(info.score);  //

    if (!info.bound.empty())
        ss << " " << info.bound;

    if (showWDL)
        ss << " wdl " << info.wdl;

    ss << " nodes " << info.nodes        //
       << " nps " << info.nps            //
       << " hashfull " << info.hashfull  //
       << " tbhits " << info.tbHits      //
       << " time " << info.timeMs        //
       << " pv " << info.pv;             //

    sync_cout << ss.str() << sync_endl;
}

void UCIEngine::on_iter(const Engine::InfoIter& info) {
    std::stringstream ss;

    ss << "info";
    ss << " depth " << info.depth                     //
       << " currmove " << info.currmove               //
       << " currmovenumber " << info.currmovenumber;  //

    sync_cout << ss.str() << sync_endl;
}

void UCIEngine::on_bestmove(std::string_view bestmove, std::string_view ponder) {
    sync_cout << "bestmove " << bestmove;
    if (!ponder.empty())
        std::cout << " ponder " << ponder;
    std::cout << sync_endl;
}

void UCIEngine::terminate_on_critical_error(const std::string& message) {
    sync_cout << "info string CRITICAL ERROR: Command `" << currentCmd
              << "` failed. Reason: " << message << '\n'
              << sync_endl;
    std::exit(1);
}

}  // namespace Stockfish
