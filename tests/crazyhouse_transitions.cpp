/*
  Crazyhouse physical transition fixture. This exercises assumed-legal move
  application, reconstruction and undo. It does not certify move generation,
  pseudo-legality, terminal results, repetition adjudication or evaluation.
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
    std::cerr << "FAIL crazyhouse_transitions: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

void require_set(Position& position, StateInfo& state, std::string_view fen) {
    const auto error =
      position.set(std::string(fen), false, Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), "setup rejected: " + std::string(fen)
                                  + (error ? " :: " + std::string(error->what()) : ""));
}

void expect_transition(std::string_view label, std::string_view initialFen, Move move,
                       std::string_view expectedFen) {
    StateInfo initialState;
    StateInfo nextState;
    Position  position(Ruleset::CRAZYHOUSE);
    require_set(position, initialState, initialFen);

    const std::string beforeFen = position.fen();
    const Key         beforeRaw = initialState.key;
    const auto        beforeBoard = position.piece_array();
    std::array<unsigned char, sizeof(StateInfo)> beforeState{};
    std::memcpy(beforeState.data(), &initialState, sizeof(initialState));

    position.do_move(move, nextState);
    require(position.fen() == expectedFen,
            std::string(label) + " FEN mismatch: " + position.fen() + " != "
              + std::string(expectedFen));
    require(position.key() == nextState.key,
            std::string(label) + " public key is not the raw Crazyhouse key");

    StateInfo expectedState;
    Position  reconstructed(Ruleset::CRAZYHOUSE);
    require_set(reconstructed, expectedState, expectedFen);
    require(nextState.key == expectedState.key,
            std::string(label) + " incremental raw key differs from reconstruction");
    require(nextState.crazyhouse.pocketKey == expectedState.crazyhouse.pocketKey,
            std::string(label) + " pocket key differs from reconstruction");
    require(nextState.crazyhouse.promotedKey == expectedState.crazyhouse.promotedKey,
            std::string(label) + " promoted key differs from reconstruction");
    require(std::memcmp(&nextState.crazyhouse.pockets, &expectedState.crazyhouse.pockets,
                        sizeof(PocketCounts)) == 0,
            std::string(label) + " pocket counts differ from reconstruction");
    require(nextState.crazyhouse.promoted == expectedState.crazyhouse.promoted,
            std::string(label) + " promoted markers differ from reconstruction");

    position.undo_move(move);
    require(position.state() == &initialState,
            std::string(label) + " undo did not restore the initial StateInfo pointer");
    require(position.fen() == beforeFen && position.key() == beforeRaw,
            std::string(label) + " undo did not restore FEN/key");
    require(position.piece_array() == beforeBoard,
            std::string(label) + " undo did not restore the board");
    require(std::memcmp(beforeState.data(), &initialState, sizeof(initialState)) == 0,
            std::string(label) + " move mutated the previous StateInfo");
}

void verify_captures_and_provenance() {
    expect_transition("white capture", "7k/n7/8/8/8/8/R7/K7[] w - - 5 1",
                      Move(SQ_A2, SQ_A7), "7k/R7/8/8/8/8/8/K7[N] b - - 0 1");

    expect_transition("black capture", "7k/r7/8/8/8/8/N7/K7[] b - - 6 4",
                      Move(SQ_A7, SQ_A2), "7k/8/8/8/8/8/r7/K7[n] w - - 0 5");

    expect_transition("promoted capture", "7k/q~7/8/8/8/8/R7/K7[] w - - 2 1",
                      Move(SQ_A2, SQ_A7), "7k/R7/8/8/8/8/8/K7[P] b - - 0 1");

    expect_transition("promoted motion", "7k/8/8/8/8/8/Q~7/K7[] w - - 3 1",
                      Move(SQ_A2, SQ_A3), "7k/8/8/8/8/Q~7/8/K7[] b - - 4 1");
}

void verify_promotion_and_en_passant() {
    expect_transition("promotion", "7k/P7/8/8/8/8/8/K7[] w - - 4 1",
                      Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN),
                      "Q~6k/8/8/8/8/8/8/K7[] b - - 0 1");

    expect_transition("en passant", "7k/8/8/3pP3/8/8/8/K7[] w - d6 9 2",
                      Move::make<EN_PASSANT>(SQ_E5, SQ_D6),
                      "7k/8/3P4/8/8/8/8/K7[P] b - - 0 2");
}

void verify_drops_and_castling() {
    expect_transition("pawn drop", "7k/8/8/8/8/8/8/K7[P] w - - 9 3",
                      Move::make_drop(PAWN, SQ_E4),
                      "7k/8/8/8/4P3/8/8/K7[] b - - 0 3");

    expect_transition("knight drop", "7k/8/8/8/8/8/8/K7[N] w - - 9 3",
                      Move::make_drop(KNIGHT, SQ_E4),
                      "7k/8/8/8/4N3/8/8/K7[] b - - 10 3");

    expect_transition("castling", "4k3/8/8/8/8/8/8/4K2R[] w K - 0 1",
                      Move::make<CASTLING>(SQ_E1, SQ_H1),
                      "4k3/8/8/8/8/8/8/5RK1[] b - - 1 1");
}

void verify_null_move() {
    constexpr std::string_view initialFen = "7k/8/8/3pP3/8/8/8/K7[Nq] w - d6 9 2";
    constexpr std::string_view reconstructedFen =
      "7k/8/8/3pP3/8/8/8/K7[Nq] b - - 9 2";

    StateInfo initialState;
    StateInfo nullState;
    Position  position(Ruleset::CRAZYHOUSE);
    require_set(position, initialState, initialFen);

    const std::string beforeFen = position.fen();
    const Key         beforeRaw = initialState.key;
    const auto        beforeBoard = position.piece_array();
    std::array<unsigned char, sizeof(StateInfo)> beforeState{};
    std::memcpy(beforeState.data(), &initialState, sizeof(initialState));

    position.do_null_move(nullState);
    StateInfo expectedState;
    Position  reconstructed(Ruleset::CRAZYHOUSE);
    require_set(reconstructed, expectedState, reconstructedFen);
    require(position.piece_array() == beforeBoard, "null move changed the board");
    require(nullState.key == expectedState.key && position.key() == nullState.key,
            "null move key differs from reconstruction");
    require(std::memcmp(&nullState.crazyhouse.pockets, &initialState.crazyhouse.pockets,
                        sizeof(PocketCounts)) == 0
              && nullState.crazyhouse.promoted == initialState.crazyhouse.promoted
              && nullState.crazyhouse.pocketKey == initialState.crazyhouse.pocketKey
              && nullState.crazyhouse.promotedKey == initialState.crazyhouse.promotedKey,
            "null move changed Crazyhouse physical/component state");
    require(nullState.epSquare == SQ_NONE && nullState.pliesFromNull == 0,
            "null move did not clear EP/pliesFromNull");

    position.undo_null_move();
    require(position.state() == &initialState && position.fen() == beforeFen
              && position.key() == beforeRaw && position.piece_array() == beforeBoard,
            "null undo did not restore the position");
    require(std::memcmp(beforeState.data(), &initialState, sizeof(initialState)) == 0,
            "null move mutated the previous StateInfo");
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();

    verify_captures_and_provenance();
    verify_promotion_and_en_passant();
    verify_drops_and_castling();
    verify_null_move();

    std::cout << "PASS crazyhouse_transitions captures=PASS demotion=PASS promotion=PASS "
                 "ep=PASS drops=PASS castling=PASS null=PASS undo=PASS keys=PASS\n";
    return EXIT_SUCCESS;
}
