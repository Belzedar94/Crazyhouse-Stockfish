/*
  Fixture for Engine-owned routing epochs, candidate backend ownership and
  heap-swappable transactional positions. UCI integration remains separate.
*/

#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "engine.h"
#include "engine_routing.h"
#include "misc.h"
#include "nnue/crazyhouse_legacy_network.h"
#include "position.h"
#include "ruleset.h"

namespace {

using namespace Stockfish;
namespace Routing = EngineRouting;
namespace Profile = CrazyhouseProfile;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_engine_state: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

void require_active(const Routing::Snapshot&  snapshot,
                    Ruleset                   ruleset,
                    Routing::Epoch            epoch,
                    Routing::BackendKind      backend,
                    Routing::BackendReadiness readiness) {
    require(Routing::snapshot_contract_valid(snapshot), "snapshot contract invalid");
    require(snapshot.configEpoch == epoch, "configuration epoch drifted");
    require(snapshot.active.has_value(), "active route missing");
    require(snapshot.active->ruleset == ruleset, "active ruleset drifted");
    require(snapshot.backend.kind == backend, "backend kind drifted");
    require(snapshot.backend.readiness == readiness, "backend readiness drifted");
}

void require_apply(const Routing::ApplyResult& result,
                   bool                        ready,
                   bool                        advanced,
                   Routing::ErrorCode          error,
                   std::string_view            label) {
    require(result.ready == ready, std::string(label) + " readiness drifted");
    require(result.epochAdvanced == advanced, std::string(label) + " epoch-advance flag drifted");
    require(result.error == error, std::string(label) + " error drifted");
}

void require_position(const Routing::PositionResult& result,
                      Routing::ErrorCode             error,
                      std::string_view               label) {
    require(result.error == error, std::string(label) + " position error drifted");
    require(result.committed == (error == Routing::ErrorCode::None),
            std::string(label) + " committed flag drifted");
}

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 3, "expected legacy and official network paths");

    const std::filesystem::path legacyPath   = path_from_utf8(argv[1]);
    const std::filesystem::path officialPath = path_from_utf8(argv[2]);
    const std::filesystem::path missingPath =
      path_from_utf8(std::string(argv[1]) + ".engine-state-missing");
    std::error_code missingError;
    require(!std::filesystem::exists(missingPath, missingError) && !missingError,
            "missing-path precondition failed");

    Attacks::init();
    Position::init();

    Engine engine(std::optional{path_from_utf8(argv[0])});

    const auto& startup = engine.routing_snapshot();
    require(Routing::snapshot_contract_valid(startup), "startup snapshot invalid");
    require(startup.configEpoch == 1 && startup.pendingDirty && !startup.active.has_value(),
            "startup route drifted");
    require(startup.pending.crazyhouseProfile == Profile::Token,
            "startup profile token drifted");
    require(!startup.positionEpoch.has_value(), "startup position was admitted");
    require(!engine.has_routed_official_network(), "startup official route was active");
    require(!engine.has_routed_legacy_network(), "startup legacy route was active");

    engine.stage_crazyhouse_eval_file(legacyPath.string());
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "legacy success");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 2,
                   Routing::BackendKind::LegacyCrazyhouseV1, Routing::BackendReadiness::Ready);
    require(engine.has_routed_legacy_network(), "legacy storage missing after success");
    require(!engine.has_routed_official_network(), "inactive official route retained");
    require(engine.routing_snapshot().backend.identity
              == Eval::NNUE::LegacyCrazyhouseNetworkV1::RegisteredSha256,
            "legacy identity drifted");

    const std::string crazyhouseStart(Routing::start_fen(Ruleset::CRAZYHOUSE));
    require_position(engine.set_routed_position(crazyhouseStart, {}), Routing::ErrorCode::None,
                     "Crazyhouse start");
    require(engine.routing_snapshot().positionEpoch == std::optional<Routing::Epoch>{2},
            "Crazyhouse position epoch drifted");
    const std::string physicalStart = engine.fen();

    require_position(engine.set_routed_position("not-a-fen", {}), Routing::ErrorCode::InvalidFen,
                     "invalid FEN");
    require(engine.fen() == physicalStart, "invalid FEN replaced physical position");
    require(!engine.routing_snapshot().positionEpoch.has_value(),
            "invalid FEN retained position admission");

    const auto illegal = engine.set_routed_position(crazyhouseStart, {"e2e4", "e7e5", "e4e5"});
    require_position(illegal, Routing::ErrorCode::IllegalMove, "illegal suffix");
    require(illegal.moveIndex == std::optional<std::size_t>{3},
            "illegal move index is not one-based");
    require(illegal.token == "e4e5", "illegal move token drifted");
    require(engine.fen() == physicalStart, "illegal suffix replaced physical position");

    require_position(engine.set_routed_position(crazyhouseStart, {}), Routing::ErrorCode::None,
                     "Crazyhouse reset");
    engine.stage_crazyhouse_eval_file(missingPath.string());
    require_apply(engine.apply_pending_route(), false, true, Routing::ErrorCode::LegacyMissingFile,
                  "missing replacement");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 3, Routing::BackendKind::None,
                   Routing::BackendReadiness::Failed);
    require(!engine.has_routed_legacy_network(), "stale legacy storage survived failure");
    require(!engine.has_routed_official_network(), "official route appeared on failure");
    require(!engine.routing_snapshot().positionEpoch.has_value(),
            "replacement failure retained position admission");

    require_position(engine.set_routed_position(crazyhouseStart, {}), Routing::ErrorCode::None,
                     "rule-only position");
    require(engine.routing_snapshot().positionEpoch == std::optional<Routing::Epoch>{3},
            "rule-only position epoch drifted");
    require_apply(engine.apply_pending_route(), false, false, Routing::ErrorCode::LegacyMissingFile,
                  "failed retry");
    require(engine.routing_snapshot().configEpoch == 3,
            "failed retry advanced configuration epoch");
    require(engine.routing_snapshot().positionEpoch == std::optional<Routing::Epoch>{3},
            "failed retry invalidated rule-only position");

    engine.stage_crazyhouse_eval_file(legacyPath.string());
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "legacy retry success");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 4,
                   Routing::BackendKind::LegacyCrazyhouseV1, Routing::BackendReadiness::Ready);
    require(!engine.routing_snapshot().positionEpoch.has_value(),
            "successful retry retained stale position");

    require(engine.stage_ruleset("chess"), "chess staging failed");
    engine.stage_chess_eval_file(officialPath.string());
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "chess success");
    require_active(engine.routing_snapshot(), Ruleset::CHESS, 5,
                   Routing::BackendKind::OfficialChess, Routing::BackendReadiness::Ready);
    require(engine.has_routed_official_network(), "official route missing after success");
    require(!engine.has_routed_legacy_network(), "inactive legacy storage retained");
    require(engine.routing_snapshot().backend.identity.rfind("official-content-hash:", 0) == 0,
            "official diagnostic identity drifted");

    const std::string chessStart(Routing::start_fen(Ruleset::CHESS));
    require_position(engine.set_routed_position(chessStart, {}), Routing::ErrorCode::None,
                     "chess start");
    require(Routing::chess_search_ready(engine.routing_snapshot()),
            "valid chess state did not admit chess control");

    require(engine.stage_ruleset("crazyhouse"), "Crazyhouse staging failed");
    engine.stage_crazyhouse_eval_file(officialPath.string());
    require_apply(engine.apply_pending_route(), false, true,
                  Routing::ErrorCode::LegacyOversizedFile, "crossed legacy route");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 6, Routing::BackendKind::None,
                   Routing::BackendReadiness::Failed);
    require(!engine.has_routed_official_network() && !engine.has_routed_legacy_network(),
            "crossed route retained physical backend storage");

    require(engine.stage_ruleset("chess"), "persisted chess staging failed");
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "persisted chess success");
    require_active(engine.routing_snapshot(), Ruleset::CHESS, 7,
                   Routing::BackendKind::OfficialChess, Routing::BackendReadiness::Ready);
    require(engine.routing_snapshot().active->crazyhouseEvalFile == officialPath.string(),
            "Crazyhouse evaluator option did not persist through chess");

    require(engine.stage_ruleset("crazyhouse"), "Chess960 Crazyhouse staging failed");
    engine.stage_crazyhouse_eval_file(legacyPath.string());
    engine.stage_chess960(true);
    require_apply(engine.apply_pending_route(), false, true,
                  Routing::ErrorCode::CrazyhouseChess960Rejected, "Crazyhouse Chess960 rejection");
    const auto& invalidCombination = engine.routing_snapshot();
    require(Routing::snapshot_contract_valid(invalidCombination),
            "invalid-combination committed snapshot invalid");
    require(invalidCombination.configEpoch == 8 && !invalidCombination.active.has_value(),
            "invalid combination retained active ruleset");
    require(invalidCombination.backend.readiness == Routing::BackendReadiness::Failed,
            "invalid combination did not commit failed backend");
    require(!engine.has_routed_official_network() && !engine.has_routed_legacy_network(),
            "invalid combination retained physical backend storage");

    engine.stage_chess960(false);
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "Crazyhouse recovery");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 9,
                   Routing::BackendKind::LegacyCrazyhouseV1, Routing::BackendReadiness::Ready);
    require(!Routing::crazyhouse_search_ready(engine.routing_snapshot()),
            "ready legacy backend admitted search without a current position");
    require_position(engine.set_routed_position(crazyhouseStart, {}), Routing::ErrorCode::None,
                     "Crazyhouse recovery position");
    require(Routing::crazyhouse_search_ready(engine.routing_snapshot()),
            "ready legacy Engine state did not admit Crazyhouse search");

    require(!engine.stage_ruleset("atomic"), "invalid variant was accepted");
    require(engine.routing_snapshot().pendingError
              == std::optional{Routing::ErrorCode::InvalidVariant},
            "invalid variant did not latch pending error");
    require_apply(engine.apply_pending_route(), false, false, Routing::ErrorCode::InvalidVariant,
                  "invalid variant apply");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 9,
                   Routing::BackendKind::LegacyCrazyhouseV1, Routing::BackendReadiness::Ready);
    require(engine.stage_ruleset("crazyhouse"), "invalid variant recovery staging failed");
    require_apply(engine.apply_pending_route(), true, false, Routing::ErrorCode::None,
                  "invalid variant recovery no-op");
    require(engine.routing_snapshot().configEpoch == 9,
            "invalid variant recovery reloaded unchanged route");

    engine.stage_crazyhouse_profile("LICHESS_CRAZYHOUSE_2026_08_12@0");
    require_apply(engine.apply_pending_route(), false, true,
                  Routing::ErrorCode::CrazyhouseProfileHashMismatch,
                  "wrong profile hash rejection");
    const auto& wrongHash = engine.routing_snapshot();
    require(wrongHash.configEpoch == 10 && !wrongHash.active.has_value(),
            "wrong profile hash retained an active route");
    require(wrongHash.backend.readiness == Routing::BackendReadiness::Failed,
            "wrong profile hash did not fail the backend");

    engine.stage_crazyhouse_profile(
      "UNKNOWN_PROFILE@d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68");
    require_apply(engine.apply_pending_route(), false, true,
                  Routing::ErrorCode::CrazyhouseProfileUnknown,
                  "unknown profile rejection");
    require(engine.routing_snapshot().configEpoch == 11
              && !engine.routing_snapshot().active.has_value(),
            "unknown profile retained an active route");

    engine.stage_crazyhouse_profile("");
    require_apply(engine.apply_pending_route(), false, true,
                  Routing::ErrorCode::CrazyhouseProfileMissing,
                  "missing profile rejection");
    require(engine.routing_snapshot().configEpoch == 12
              && !engine.routing_snapshot().active.has_value(),
            "missing profile retained an active route");

    engine.stage_crazyhouse_profile(std::string(Profile::Token));
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "exact profile recovery");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 13,
                   Routing::BackendKind::LegacyCrazyhouseV1,
                   Routing::BackendReadiness::Ready);

    require(engine.stage_ruleset("chess"), "profile chess staging failed");
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "profile chess route");
    require_active(engine.routing_snapshot(), Ruleset::CHESS, 14,
                   Routing::BackendKind::OfficialChess, Routing::BackendReadiness::Ready);

    engine.stage_crazyhouse_profile("LICHESS_CRAZYHOUSE_2026_08_12@0");
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "chess ignores Crazyhouse profile validity");
    require_active(engine.routing_snapshot(), Ruleset::CHESS, 15,
                   Routing::BackendKind::OfficialChess, Routing::BackendReadiness::Ready);

    require(engine.stage_ruleset("crazyhouse"), "invalid-profile Crazyhouse staging failed");
    require_apply(engine.apply_pending_route(), false, true,
                  Routing::ErrorCode::CrazyhouseProfileHashMismatch,
                  "persisted wrong profile rejection");
    require(engine.routing_snapshot().configEpoch == 16
              && !engine.routing_snapshot().active.has_value(),
            "persisted wrong profile committed Crazyhouse");

    engine.stage_crazyhouse_profile(std::string(Profile::Token));
    require_apply(engine.apply_pending_route(), true, true, Routing::ErrorCode::None,
                  "final exact profile recovery");
    require_active(engine.routing_snapshot(), Ruleset::CRAZYHOUSE, 17,
                   Routing::BackendKind::LegacyCrazyhouseV1,
                   Routing::BackendReadiness::Ready);
    require(!Routing::crazyhouse_search_ready(engine.routing_snapshot()),
            "final legacy backend admitted search without a current position");
    require_position(engine.set_routed_position(crazyhouseStart, {}), Routing::ErrorCode::None,
                     "final exact profile position");
    require(Routing::crazyhouse_search_ready(engine.routing_snapshot()),
            "exact profile route did not admit Crazyhouse search");

    std::cout << "PASS crazyhouse_engine_state transitions=20 epochs=1..17 "
                 "backend_ownership=PASS position_transaction=PASS "
                 "profile_fail_closed=PASS crazyhouse_search=READY_EXACT_ROUTE_ONLY\n";
    return EXIT_SUCCESS;
}
