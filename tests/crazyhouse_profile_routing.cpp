/*
  Fixture-first contract for the versioned Crazyhouse profile token and its
  routing vocabulary. The pre-implementation build must fail because the
  profile API and stable route errors do not exist yet.
*/

#include <cstdlib>
#include <iostream>
#include <string>

#include "crazyhouse_profile.h"
#include "engine_routing.h"

namespace {

using namespace Stockfish;
namespace Profile = CrazyhouseProfile;
namespace Routing = EngineRouting;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_profile_routing: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

void verify_profile_identity() {
    require(Profile::Id == "LICHESS_CRAZYHOUSE_2026_08_12", "profile ID drifted");
    require(Profile::Sha256 == "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68",
            "profile digest drifted");
    require(Profile::Token
              == "LICHESS_CRAZYHOUSE_2026_08_12@d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68",
            "profile token drifted");

    require(Profile::classify(Profile::Token) == Profile::TokenStatus::Valid,
            "exact profile token was rejected");
    require(Profile::classify("") == Profile::TokenStatus::Missing,
            "empty profile token was not missing");
    require(Profile::classify("UNKNOWN_PROFILE@d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68")
              == Profile::TokenStatus::UnknownId,
            "unknown profile ID was not rejected");
    require(Profile::classify("LICHESS_CRAZYHOUSE_2026_08_12@0000000000000000000000000000000000000000000000000000000000000000")
              == Profile::TokenStatus::HashMismatch,
            "wrong profile hash was not rejected");
    require(Profile::classify("LICHESS_CRAZYHOUSE_2026_08_12")
              == Profile::TokenStatus::HashMismatch,
            "missing profile hash was not rejected");
}

void verify_routing_mapping() {
    require(Routing::crazyhouse_profile_error(Profile::Token) == Routing::ErrorCode::None,
            "exact profile did not map to routing success");
    require(Routing::crazyhouse_profile_error("")
              == Routing::ErrorCode::CrazyhouseProfileMissing,
            "missing profile route error drifted");
    require(Routing::crazyhouse_profile_error("UNKNOWN_PROFILE@0")
              == Routing::ErrorCode::CrazyhouseProfileUnknown,
            "unknown profile route error drifted");
    require(Routing::crazyhouse_profile_error("LICHESS_CRAZYHOUSE_2026_08_12@0")
              == Routing::ErrorCode::CrazyhouseProfileHashMismatch,
            "profile hash route error drifted");

    require(Routing::error_code_name(Routing::ErrorCode::CrazyhouseProfileMissing)
              == "crazyhouse_profile_missing",
            "missing profile error name drifted");
    require(Routing::error_code_name(Routing::ErrorCode::CrazyhouseProfileUnknown)
              == "crazyhouse_profile_unknown",
            "unknown profile error name drifted");
    require(Routing::error_code_name(Routing::ErrorCode::CrazyhouseProfileHashMismatch)
              == "crazyhouse_profile_hash_mismatch",
            "profile hash error name drifted");
}

void verify_route_identity() {
    Routing::RouteOptions options;
    require(options.crazyhouseProfile == Profile::Token, "default route profile drifted");

    Routing::Snapshot valid;
    valid.pending       = options;
    valid.active        = options;
    valid.pendingDirty  = false;
    valid.configEpoch   = 3;
    valid.backend.kind  = Routing::BackendKind::LegacyCrazyhouseV1;
    valid.backend.readiness = Routing::BackendReadiness::Ready;
    valid.backend.epoch = valid.configEpoch;
    valid.backend.identity = "fixture";
    require(Routing::snapshot_contract_valid(valid), "exact profile snapshot was rejected");

    Routing::Snapshot invalid = valid;
    invalid.active->crazyhouseProfile = "LICHESS_CRAZYHOUSE_2026_08_12@0";
    invalid.pending = *invalid.active;
    require(!Routing::snapshot_contract_valid(invalid), "wrong profile snapshot was valid");

    Routing::RouteOptions changed = options;
    changed.crazyhouseProfile = "LICHESS_CRAZYHOUSE_2026_08_12@0";
    require(!Routing::same_route_options(options, changed), "profile mutation did not dirty route");
}

}  // namespace

int main() {
    verify_profile_identity();
    verify_routing_mapping();
    verify_route_identity();
    std::cout << "PASS crazyhouse_profile_routing identity=PASS errors=PASS snapshots=PASS\n";
    return EXIT_SUCCESS;
}
