/*
  Fixture executable for the explicit Crazyhouse legacy value/outer adapter.
  The adapter remains independent of UCI routing, search and current Stockfish
  piece values.
*/

#include <array>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <limits>
#include <string>

#include "bitboard.h"
#include "nnue/crazyhouse_legacy_network.h"
#include "position.h"

namespace {

using namespace Stockfish;
using Network = Eval::NNUE::LegacyCrazyhouseNetworkV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_legacy_adapter: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

void set_position(Position& position, Ruleset ruleset, const std::string& fen, bool chess960,
                  StateInfo& state) {
    const auto error = position.set(fen, chess960, ruleset, &state);
    require(!error.has_value(), "position setup rejected: " + fen
                                  + (error ? " :: " + std::string(error->what()) : ""));
}

Network::LegacyBoardInventory empty_inventory() {
    return Network::LegacyBoardInventory{};
}

void verify_adapter_microfixtures() {
    const Network::RawComponents equalPositive{300000, 300000};
    const Network::AdapterResult high =
      Network::adapt_legacy_components(equalPositive, empty_inventory());
    require(high.ok(), "positive clamp microfixture failed");
    require(high.output->adjusted == 37500 && high.output->outerPreClamp == 33068,
            "positive clamp precondition drifted");
    require(high.output->outer == 31507 && high.output->clamped,
            "positive donor clamp drifted");

    const Network::RawComponents equalNegative{-300000, -300000};
    const Network::AdapterResult low =
      Network::adapt_legacy_components(equalNegative, empty_inventory());
    require(low.ok(), "negative clamp microfixture failed");
    require(low.output->adjusted == -37500 && low.output->outerPreClamp == -33068,
            "negative clamp precondition drifted");
    require(low.output->outer == -31507 && low.output->clamped,
            "negative donor clamp drifted");

    Network::LegacyBoardInventory threshold;
    threshold.whiteNonPawns[Network::LegacyKnight] = 1;
    threshold.blackNonPawns[Network::LegacyBishop] = 1;
    const Network::AdapterResult atThreshold =
      Network::adapt_legacy_components({1000, 2000}, threshold);
    require(atThreshold.ok() && atThreshold.output->whiteNonPawnMaterial == 781
              && atThreshold.output->blackNonPawnMaterial == 825
              && atThreshold.output->entertainmentApplied,
            "difference-44 entertainment threshold drifted");

    Network::LegacyBoardInventory outside;
    outside.whiteNonPawns[Network::LegacyKnight] = 1;
    const Network::AdapterResult overThreshold =
      Network::adapt_legacy_components({1000, 2000}, outside);
    require(overThreshold.ok() && !overThreshold.output->entertainmentApplied,
            "above-threshold entertainment was applied");

    const Network::AdapterResult truncation =
      Network::adapt_legacy_components({-9, -8}, empty_inventory());
    require(truncation.ok() && truncation.output->unadjusted == -1,
            "negative blend division did not truncate toward zero");

    Network::LegacyBoardInventory invalid;
    invalid.boardPawns = 31;
    const Network::AdapterResult badInventory =
      Network::adapt_legacy_components({0, 0}, invalid);
    require(badInventory.status == Network::AdapterStatus::InvalidInventory
              && !badInventory.output.has_value(),
            "physically impossible inventory did not fail closed");

    const Network::RawComponents overflow{std::numeric_limits<std::int32_t>::max(),
                                           std::numeric_limits<std::int32_t>::max()};
    const Network::AdapterResult badArithmetic =
      Network::adapt_legacy_components(overflow, empty_inventory());
    require(badArithmetic.status == Network::AdapterStatus::ArithmeticOutOfRange
              && !badArithmetic.output.has_value(),
            "donor signed-intermediate overflow did not fail closed");
}

void verify_profile_boundaries(Network& network, const std::filesystem::path& artifact) {
    const std::string start =
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1";
    StateInfo state;
    Position  position(Ruleset::CRAZYHOUSE);
    set_position(position, Ruleset::CRAZYHOUSE, start, false, state);

    const Network::LegacyEvalResult unloaded = network.evaluate_legacy(position);
    require(unloaded.status == Network::EvalStatus::NetworkNotLoaded
              && !unloaded.output.has_value(),
            "unloaded legacy adapter did not fail closed");
    require(network.load_file(artifact).status == Network::LoadStatus::Success,
            "registered artifact failed to load");

    StateInfo chess960State;
    Position  chess960(Ruleset::CRAZYHOUSE);
    const auto chess960Error =
      chess960.set(start, true, Ruleset::CRAZYHOUSE, &chess960State);
    require(chess960Error.has_value()
              && std::string(chess960Error->what())
                   == "Unsupported Crazyhouse position. Chess960 is not supported.",
            "Chess960-tagged Crazyhouse input did not fail closed at the Position boundary");

    std::filesystem::path missing = artifact;
    missing += ".adapter-fixture-missing";
    require(network.load_file(missing).status == Network::LoadStatus::MissingFile,
            "missing replacement returned the wrong parser status");
    const Network::LegacyEvalResult stale = network.evaluate_legacy(position);
    require(stale.status == Network::EvalStatus::NetworkNotLoaded && !stale.output.has_value(),
            "failed replacement retained a usable adapter");
    require(network.load_file(artifact).status == Network::LoadStatus::Success,
            "valid adapter recovery load failed");
}

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 2, "usage: crazyhouse_legacy_adapter <registered-network>");
    const std::filesystem::path artifact(argv[1]);
    Attacks::init();
    Position::init();

    verify_adapter_microfixtures();
    Network network;
    verify_profile_boundaries(network, artifact);

    std::string fen;
    int         count = 0;
    while (std::getline(std::cin, fen))
    {
        require(!fen.empty(), "input contains an empty FEN line");
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        set_position(position, Ruleset::CRAZYHOUSE, fen, false, state);
        const Network::LegacyEvalResult result = network.evaluate_legacy(position);
        require(result.ok(), "legacy adapter rejected " + fen + ": " + result.message);

        const Network::LegacyEvaluation& value = *result.output;
        const Network::RawComponents& raw = value.raw.selected();
        const Network::LegacyAdapterOutput& adapter = value.adapter;
        std::cout << "OK\t" << position.fen() << '\t'
                  << unsigned(value.raw.selectedBucket) << '\t' << raw.psqt << '\t'
                  << raw.positional << '\t' << adapter.boardPawns << '\t'
                  << adapter.whiteNonPawnMaterial << '\t' << adapter.blackNonPawnMaterial << '\t'
                  << (adapter.entertainmentApplied ? 1 : 0) << '\t' << adapter.scale << '\t'
                  << adapter.unadjusted << '\t' << adapter.adjusted << '\t'
                  << adapter.outerPreClamp << '\t' << adapter.outer << '\t'
                  << (adapter.clamped ? 1 : 0) << '\n';
        ++count;
    }
    require(std::cin.eof(), "stdin read failed");
    require(count > 0, "no FEN cases were supplied");
    return EXIT_SUCCESS;
}
