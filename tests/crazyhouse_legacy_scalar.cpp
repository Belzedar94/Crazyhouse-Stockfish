/*
  Fixture executable for the independent Crazyhouse legacy scalar full refresh.
  It reports every raw PSQT/positional bucket and exercises fail-closed load and
  rule boundaries before reading the immutable golden FEN stream.
*/

#include <array>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <string>

#include "bitboard.h"
#include "nnue/crazyhouse_legacy_network.h"
#include "position.h"

namespace {

using namespace Stockfish;
using Network = Eval::NNUE::LegacyCrazyhouseNetworkV1;
using Stack   = Eval::NNUE::LegacyCrazyhouseAccumulatorStackV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_legacy_scalar: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

template<typename Getter>
std::string join_buckets(const Network::RawEvaluation& evaluation, Getter getter) {
    std::ostringstream output;
    for (std::size_t bucket = 0; bucket < Network::LayerStacks; ++bucket)
    {
        if (bucket)
            output << ',';
        output << getter(evaluation.buckets[bucket]);
    }
    return output.str();
}

void set_position(Position& position, Ruleset ruleset, const std::string& fen, StateInfo& state) {
    const auto error = position.set(fen, false, ruleset, &state);
    require(!error.has_value(), "position setup rejected: " + fen
                                  + (error ? " :: " + std::string(error->what()) : ""));
}

void verify_fail_closed_boundaries(Network& network, const std::filesystem::path& artifact) {
    const std::string crazyhouseStart =
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1";
    StateInfo crazyhouseState;
    Position  crazyhouse(Ruleset::CRAZYHOUSE);
    set_position(crazyhouse, Ruleset::CRAZYHOUSE, crazyhouseStart, crazyhouseState);

    const Network::EvalResult beforeLoad = network.evaluate_full_refresh(crazyhouse);
    require(beforeLoad.status == Network::EvalStatus::NetworkNotLoaded,
            "unloaded evaluation did not report NetworkNotLoaded");
    require(!beforeLoad.output.has_value(), "unloaded evaluation retained partial output");

    const Network::LoadResult loaded = network.load_file(artifact);
    require(loaded.status == Network::LoadStatus::Success && network.loaded(),
            "registered artifact failed to load: " + loaded.message);
    const Network::EvalResult positive = network.evaluate_full_refresh(crazyhouse);
    require(positive.ok(), "loaded Crazyhouse evaluation failed: " + positive.message);

    std::filesystem::path missing = artifact;
    missing += ".scalar-fixture-missing";
    require(!std::filesystem::exists(missing), "missing-file control unexpectedly exists");
    const Network::LoadResult failedReplacement = network.load_file(missing);
    require(failedReplacement.status == Network::LoadStatus::MissingFile,
            "missing replacement returned the wrong parser status");
    require(!network.loaded(), "failed replacement retained the prior network");
    const Network::EvalResult afterFailure = network.evaluate_full_refresh(crazyhouse);
    require(afterFailure.status == Network::EvalStatus::NetworkNotLoaded,
            "evaluation after failed replacement did not report NetworkNotLoaded");
    require(!afterFailure.output.has_value(), "failed replacement retained partial output");

    const Network::LoadResult reloaded = network.load_file(artifact);
    require(reloaded.status == Network::LoadStatus::Success && network.loaded(),
            "registered artifact did not recover after failed replacement");

    StateInfo chessState;
    Position  chess(Ruleset::CHESS);
    set_position(chess, Ruleset::CHESS,
                 "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", chessState);
    const Network::EvalResult wrongRuleset = network.evaluate_full_refresh(chess);
    require(wrongRuleset.status == Network::EvalStatus::FeatureRejected,
            "standard Chess was accepted by the legacy Crazyhouse evaluator");
    require(wrongRuleset.featureStatus == Eval::NNUE::LegacyCrazyhouseFeaturesV1::Status::WrongRuleset,
            "wrong-ruleset evaluation lost the extractor status");
    require(!wrongRuleset.output.has_value(), "wrong-ruleset evaluation retained partial output");
}

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 2, "usage: crazyhouse_legacy_scalar <registered-network>");
    const std::filesystem::path artifact(argv[1]);

    Attacks::init();
    Position::init();

    Network network;
    verify_fail_closed_boundaries(network, artifact);

    std::string fen;
    int         count = 0;
    while (std::getline(std::cin, fen))
    {
        require(!fen.empty(), "input contains an empty FEN line");
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        set_position(position, Ruleset::CRAZYHOUSE, fen, state);
        const Network::EvalResult result = network.evaluate_full_refresh(position);
        require(result.ok(), "scalar full refresh rejected " + fen + ": " + result.message);

        Stack searchStack;
        searchStack.reset();
        const Network::LegacyEvalResult search =
          network.evaluate_legacy_search_incremental(position, searchStack);
        require(search.ok(), "selected-bucket search rejected " + fen + ": " + search.message);

        const Network::RawEvaluation& evaluation = *result.output;
        require(evaluation.selectedBucket < Network::LayerStacks,
                "scalar full refresh returned an out-of-range selected bucket");
        const Network::RawComponents& selected = evaluation.selected();
        require(evaluation.selectedBucket == search.output->raw.selectedBucket
                  && selected.psqt == search.output->raw.selected().psqt
                  && selected.positional == search.output->raw.selected().positional,
                "selected-bucket search output mismatch for " + fen);

        std::cout << "OK\t" << position.fen() << '\t'
                  << unsigned(evaluation.selectedBucket) << '\t'
                  << join_buckets(evaluation,
                                  [](const Network::RawComponents& value) { return value.psqt; })
                  << '\t'
                  << join_buckets(evaluation, [](const Network::RawComponents& value) {
                         return value.positional;
                     })
                  << '\t' << selected.psqt << '\t' << selected.positional << '\n';
        ++count;
    }
    require(std::cin.eof(), "stdin read failed");
    require(count > 0, "no FEN cases were supplied");
    return EXIT_SUCCESS;
}
