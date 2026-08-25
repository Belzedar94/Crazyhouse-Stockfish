/*
  Transactional Crazyhouse FEN fixture. This freezes physical parsing and
  canonical serialization only; it does not assert complete Crazyhouse keys,
  move transitions, drop legality, game results or evaluator behavior.
*/

#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <string_view>

#include "position.h"

namespace {

using namespace Stockfish;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_fen: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

void expect_canonical(std::string_view input, std::string_view expected) {
    StateInfo state;
    Position  position(Ruleset::CRAZYHOUSE);
    const auto error = position.set(std::string(input), false, Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), "accepted case rejected: " + std::string(input)
                                  + (error ? " :: " + std::string(error->what()) : ""));
    require(position.fen() == expected,
            "canonical mismatch: " + position.fen() + " != " + std::string(expected));
}

void expect_rejected(std::string_view input, std::string_view diagnostic,
                     bool isChess960 = false) {
    StateInfo state;
    Position  position(Ruleset::CRAZYHOUSE);
    const auto error =
      position.set(std::string(input), isChess960, Ruleset::CRAZYHOUSE, &state);
    require(error.has_value(), "rejected case was accepted: " + std::string(input));
    require(std::string_view(error->what()).find(diagnostic) != std::string_view::npos,
            "diagnostic mismatch: " + std::string(error->what()));
}

void verify_positive_dialects_and_state() {
    expect_canonical("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1",
                     "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1");
    expect_canonical("2k5/8/8/8/8/8/8/4K3[QRBNPqrbnp] w - - 0 1",
                     "2k5/8/8/8/8/8/8/4K3[PNBRQpnbrq] w - - 0 1");
    expect_canonical("2k5/8/8/8/8/8/8/4K3/Qn w - - 0 1",
                     "2k5/8/8/8/8/8/8/4K3[Qn] w - - 0 1");
    expect_canonical("7k/8/8/8/8/8/Q~7/K7[] w - - 0 1",
                     "7k/8/8/8/8/8/Q~7/K7[] w - - 0 1");
    expect_canonical("8/7k/8/8/8/Q~7/QQ6/K7[] w - - 7 9",
                     "8/7k/8/8/8/Q~7/QQ6/K7[] w - - 7 9");
    expect_canonical("7k/8/8/8/8/8/4K3/8[QPRPqn] w - - 0 1",
                     "7k/8/8/8/8/8/4K3/8[PPRQnq] w - - 0 1");
    expect_canonical("7k/8/8/8/8/8/4K3/8[PNBRQ] w - - 0 1",
                     "7k/8/8/8/8/8/4K3/8[PNBRQ] w - - 0 1");
    expect_canonical("4r2k/8/8/3pP3/8/8/8/4K3[] w - d6 0 2",
                     "4r2k/8/8/3pP3/8/8/8/4K3[] w - - 0 2");

    StateInfo state;
    Position  position(Ruleset::CRAZYHOUSE);
    const auto error = position.set("2k5/8/8/8/8/8/Q~7/4K3[Qn] w - - 0 1", false,
                                    Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), "state inspection setup failed");
    require(position.pocket_count(WHITE, QUEEN) == 1, "white queen pocket mismatch");
    require(position.pocket_count(BLACK, KNIGHT) == 1, "black knight pocket mismatch");
    require(position.promoted_pieces() == square_bb(SQ_A2), "promoted bitboard mismatch");
}

void verify_fail_closed_domain() {
    const std::string pawnOverflow(17, 'P');

    expect_rejected("7k/8/8/8/8/8/8/K7 w - - 0 1", "Missing pocket field");
    expect_rejected("7k/8/8/8/8/8/8/K7[Q w - - 0 1", "Unterminated pocket");
    expect_rejected("7k/8/8/8/8/8/8/K7[]x w - - 0 1", "Unexpected data after pocket");
    expect_rejected("7k/8/8/8/8/8/8/K7[K] w - - 0 1", "Invalid pocket piece");
    expect_rejected("7k/8/8/8/8/8/8/K7[1] w - - 0 1", "Invalid pocket piece");
    expect_rejected("7k/8/8/8/8/8/8/K7[Q~] w - - 0 1", "Invalid pocket piece");
    expect_rejected("7k/8/8/8/8/8/8/K7[" + pawnOverflow + "] w - - 0 1",
                    "Pocket count exceeds type limit");
    expect_rejected("7k/8/8/8/8/8/8/K7[NNNNN] w - - 0 1",
                    "Pocket count exceeds type limit");
    expect_rejected("7k/8/8/8/8/8/8/K7[rrrrr] w - - 0 1",
                    "Pocket count exceeds type limit");
    expect_rejected("7k/8/8/8/8/8/8/K7[QQQ] w - - 0 1",
                    "Pocket count exceeds type limit");

    expect_rejected("7k/8/8/8/8/8/P~7/K7[] w - - 0 1",
                    "Promoted marker on pawn or king");
    expect_rejected("7k/8/8/8/8/8/8/K~7[] w - - 0 1",
                    "Promoted marker on pawn or king");
    expect_rejected("7k/8/8/8/8/8/Q~~6/K7[] w - - 0 1", "Invalid promoted marker");
    expect_rejected("7k/8/8/8/8/8/~Q6/K7[] w - - 0 1", "Invalid promoted marker");

    expect_rejected("4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3[P] w - - 0 1",
                    "Pawn physical-unit total exceeds 16");
    expect_rejected("7k/8/8/8/8/8/NNNN4/K7[N] w - - 0 1",
                    "Knight physical-unit total exceeds 4");
    expect_rejected("7k/8/8/8/8/8/QQ6/K7[Q] w - - 0 1",
                    "Queen physical-unit total exceeds 2");

    expect_rejected("4k3/8/8/8/8/8/8/4K3[] w K - 0 1", "Invalid castling right");
    expect_rejected("4k3/8/8/8/8/8/8/4K2R~[] w K - 0 1",
                    "Promoted castling piece");
    expect_rejected("4k3/8/8/8/8/8/8/4K1R1[] w K - 0 1",
                    "Invalid castling right");
    expect_rejected("4k3/8/8/8/8/8/8/3K3R[] w K - 0 1",
                    "Invalid castling right");
    expect_rejected("7k/8/8/8/8/8/8/K7[] w - - 0 1 trailing", "Trailing FEN data");
    expect_rejected("7k/8/8/8/8/8/8/K7[] w - - 0", "halfmove and fullmove");
    expect_rejected("7k/8/8/8/8/8/8/K7[] w - - 0 1", "Chess960 is not supported", true);
}

void verify_transactional_commit() {
    StateInfo state;
    Position  position(Ruleset::CRAZYHOUSE);
    auto error = position.set("7k/8/8/8/8/8/Q~7/K7[PN] w - - 3 7", false,
                              Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), "transaction baseline setup failed");

    const std::string beforeFen = position.fen();
    const Key         beforeKey = position.key();
    const StateInfo*  beforePtr = position.state();
    std::array<unsigned char, sizeof(StateInfo)> beforeState{};
    std::memcpy(beforeState.data(), &state, sizeof(state));

    error = position.set("7k/8/8/8/8/8/8/K7[K] w - - 0 1", false,
                         Ruleset::CRAZYHOUSE, &state);
    require(error.has_value(), "same-state transactional failure was accepted");
    require(position.fen() == beforeFen && position.key() == beforeKey,
            "failed parse mutated the committed Position");
    require(position.state() == beforePtr, "failed parse changed the active StateInfo pointer");
    require(std::memcmp(beforeState.data(), &state, sizeof(state)) == 0,
            "failed parse mutated the caller StateInfo");

    StateInfo alternate;
    std::memset(&alternate, 0x5A, sizeof(alternate));
    std::array<unsigned char, sizeof(StateInfo)> alternateBefore{};
    std::memcpy(alternateBefore.data(), &alternate, sizeof(alternate));
    error = position.set("7k/8/8/8/8/8/8/K7 w - - 0 1", false,
                         Ruleset::CRAZYHOUSE, &alternate);
    require(error.has_value(), "alternate-state transactional failure was accepted");
    require(position.fen() == beforeFen && position.state() == beforePtr,
            "alternate failed parse mutated the committed Position");
    require(std::memcmp(alternateBefore.data(), &alternate, sizeof(alternate)) == 0,
            "alternate failed parse mutated its StateInfo target");

    error = position.set("7k/8/8/8/8/8/8/K7[] b - - 8 11", false,
                         Ruleset::CRAZYHOUSE, &alternate);
    require(!error.has_value(), "valid replacement did not commit");
    require(position.state() == &alternate, "valid replacement did not select target StateInfo");
    require(position.fen() == "7k/8/8/8/8/8/8/K7[] b - - 8 11",
            "valid replacement committed the wrong state");
}

void verify_chess_is_unchanged_and_separate() {
    StateInfo state;
    Position  position(Ruleset::CHESS);
    auto error = position.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                              false, Ruleset::CHESS, &state);
    require(!error.has_value(), "standard chess FEN was rejected");
    require(position.fen() == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "standard chess FEN serialization changed");

    // Stockfish's own benchmark corpus contains legacy four-field FENs. Keep
    // that established Chess ingress behavior while Crazyhouse remains strict.
    error = position.set("8/8/8/8/8/6k1/6p1/6K1 w - -", false, Ruleset::CHESS,
                         &state);
    require(!error.has_value(), "Stockfish four-field benchmark FEN was rejected");
    require(position.fen() == "8/8/8/8/8/6k1/6p1/6K1 w - - 0 1",
            "Stockfish four-field benchmark FEN canonicalization changed");

    const std::string before = position.fen();
    error = position.set("7k/8/8/8/8/8/8/K7[] w - - 0 1", false, Ruleset::CHESS, &state);
    require(error.has_value(), "chess accepted Crazyhouse pocket syntax");
    require(position.fen() == before && position.ruleset() == Ruleset::CHESS,
            "failed cross-dialect parse mutated chess state");
}

}  // namespace

int main(int argc, char** argv) {
    Attacks::init();
    Position::init();

    if (argc == 2 && std::strcmp(argv[1], "--invalid-pocket-type-control") == 0)
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        const auto error = position.set("7k/8/8/8/8/8/8/K7[] w - - 0 1", false,
                                        Ruleset::CRAZYHOUSE, &state);
        require(!error.has_value(), "invalid-type control setup failed");
        static_cast<void>(position.pocket_count(WHITE, KING));
        fail("invalid pocket type returned instead of aborting");
    }
    if (argc == 2 && std::strcmp(argv[1], "--invalid-pocket-color-control") == 0)
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        const auto error = position.set("7k/8/8/8/8/8/8/K7[] w - - 0 1", false,
                                        Ruleset::CRAZYHOUSE, &state);
        require(!error.has_value(), "invalid-color control setup failed");
        static_cast<void>(position.pocket_count(COLOR_NB, PAWN));
        fail("invalid pocket color returned instead of aborting");
    }

    require(argc == 1, "unknown command-line argument");

    verify_positive_dialects_and_state();
    verify_fail_closed_domain();
    verify_transactional_commit();
    verify_chess_is_unchanged_and_separate();

    std::cout << "PASS crazyhouse_fen canonical=8 rejected=24 transactional=PASS "
                 "bracket=PASS slash=PASS promoted=PASS chess_unchanged=PASS\n";
    return EXIT_SUCCESS;
}
