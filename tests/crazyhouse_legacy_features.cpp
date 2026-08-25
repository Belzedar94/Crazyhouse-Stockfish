/*
  Fixture executable for independent Crazyhouse legacy feature enumeration.
  It deliberately excludes network weights and numerical propagation. A Python
  verifier compares this raw protocol against the immutable golden corpus.
*/

#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "bitboard.h"
#include "nnue/crazyhouse_legacy_features.h"
#include "position.h"

namespace {

using namespace Stockfish;
using Features = Eval::NNUE::LegacyCrazyhouseFeaturesV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_legacy_features: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

std::string join(const std::vector<Features::Index>& indices) {
    std::ostringstream output;
    for (std::size_t i = 0; i < indices.size(); ++i)
    {
        if (i)
            output << ',';
        output << indices[i];
    }
    return output.str();
}

void verify_wrong_ruleset_fails_closed() {
    StateInfo state;
    Position  position(Ruleset::CHESS);
    const auto error = position.set(
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false,
      Ruleset::CHESS, &state);
    require(!error.has_value(), "standard control setup failed");

    const Features::Result result = Features::extract(position);
    require(result.status == Features::Status::WrongRuleset,
            "standard chess was accepted by the Crazyhouse feature extractor");
    require(result.active[WHITE].empty() && result.active[BLACK].empty(),
            "failed extraction retained partial active features");
    require(!result.message.empty(), "failed extraction returned no diagnostic");
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();
    verify_wrong_ruleset_fails_closed();

    std::string fen;
    int         count = 0;
    while (std::getline(std::cin, fen))
    {
        require(!fen.empty(), "input contains an empty FEN line");
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        const auto error = position.set(fen, false, Ruleset::CRAZYHOUSE, &state);
        require(!error.has_value(), "Crazyhouse setup rejected: " + fen
                                      + (error ? " :: " + std::string(error->what()) : ""));

        const Features::Result result = Features::extract(position);
        require(result.status == Features::Status::Success,
                "feature extraction rejected " + fen + ": " + result.message);
        require(result.boardPieceCount == std::size_t(popcount(position.pieces())),
                "reported board count differs from Position");

        std::cout << "OK\t" << position.fen() << '\t' << result.boardPieceCount << '\t'
                  << result.layerBucket << '\t' << join(result.active[WHITE]) << '\t'
                  << join(result.active[BLACK]) << '\n';
        ++count;
    }
    require(std::cin.eof(), "stdin read failed");
    require(count > 0, "no FEN cases were supplied");
    return EXIT_SUCCESS;
}
