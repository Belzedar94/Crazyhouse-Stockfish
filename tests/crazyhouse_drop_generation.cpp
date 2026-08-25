/*
  Crazyhouse drop-generation fixture derived from the frozen
  LICHESS_CRAZYHOUSE_2026_08_12 authority corpus. This certifies move-set
  construction and drop legality only; it does not certify terminal results,
  repetition, evaluation, search, referee behavior or strength.
*/

#include <array>
#include <cstdlib>
#include <iostream>
#include <set>
#include <string>
#include <string_view>

#include "movegen.h"
#include "position.h"

namespace {

using namespace Stockfish;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_drop_generation: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

void require_set(Position& position, StateInfo& state, std::string_view fen) {
    const auto error = position.set(std::string(fen), false, position.ruleset(), &state);
    require(!error.has_value(), "setup rejected: " + std::string(fen)
                                  + (error ? " :: " + std::string(error->what()) : ""));
}

template<typename MoveRange>
usize count_drops(const MoveRange& moves) {
    usize count = 0;
    for (Move move : moves)
        count += move.is_drop();
    return count;
}

template<typename MoveRange>
void require_no_duplicates(const MoveRange& moves, std::string_view label) {
    std::set<u16> unique;
    for (Move move : moves)
        require(unique.insert(move.raw()).second,
                std::string(label) + " contains a duplicate raw move");
}

template<typename MoveRange>
void require_exact_drop_squares(const MoveRange& moves, PieceType type,
                                Bitboard expectedSquares, std::string_view label) {
    Bitboard actualSquares = 0;
    int      actualCount   = 0;

    for (Move move : moves)
        if (move.is_drop())
        {
            require(move.drop_piece_type() == type,
                    std::string(label) + " contains an unexpected drop type");
            require(!(actualSquares & move.to_sq()),
                    std::string(label) + " contains a duplicate destination");
            actualSquares |= move.to_sq();
            ++actualCount;
        }

    require(actualSquares == expectedSquares,
            std::string(label) + " drop destination set mismatch");
    require(actualCount == popcount(expectedSquares),
            std::string(label) + " drop count mismatch");
}

void verify_303_move_root() {
    constexpr std::string_view Fen = "7k/8/8/8/8/8/4K3/8[PNBRQ] w - - 0 1";

    StateInfo state;
    Position  position(Ruleset::CRAZYHOUSE);
    require_set(position, state, Fen);

    MoveList<LEGAL>         legal(position);
    MoveList<NON_EVASIONS> nonEvasions(position);
    MoveList<QUIETS>        quiets(position);
    MoveList<CAPTURES>      captures(position);

    require(legal.is_growable() && nonEvasions.is_growable() && quiets.is_growable(),
            "Crazyhouse did not select growable list storage");
    require(legal.size() == 303, "303-root legal move count mismatch");
    require(nonEvasions.size() == 303, "303-root non-evasion count mismatch");
    require(quiets.size() == 303, "303-root quiet count mismatch");
    require(captures.size() == 0, "drops entered the capture generation class");
    require(count_drops(legal) == 295, "303-root legal drop count mismatch");
    require(count_drops(nonEvasions) == 295, "303-root pseudo drop count mismatch");
    require(count_drops(quiets) == 295, "303-root quiet drop count mismatch");
    require_no_duplicates(legal, "303-root legal list");

    constexpr std::array<PieceType, 5> PocketTypes = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    for (PieceType type : PocketTypes)
        for (Square to = SQ_A1; to <= SQ_H8; ++to)
        {
            const bool allowed = position.empty(to)
                              && (type != PAWN
                                  || (rank_of(to) != RANK_1 && rank_of(to) != RANK_8));
            require(legal.contains(Move::make_drop(type, to)) == allowed,
                    "303-root exact drop membership mismatch");
        }

    constexpr std::array<Move, 8> KingMoves = {
      Move(SQ_E2, SQ_D1), Move(SQ_E2, SQ_D2), Move(SQ_E2, SQ_D3), Move(SQ_E2, SQ_E1),
      Move(SQ_E2, SQ_E3), Move(SQ_E2, SQ_F1), Move(SQ_E2, SQ_F2), Move(SQ_E2, SQ_F3)};
    for (Move move : KingMoves)
        require(legal.contains(move), "303-root board move missing");

    for (Move move : legal)
        if (!move.is_drop())
        {
            bool expected = false;
            for (Move kingMove : KingMoves)
                expected |= move == kingMove;
            require(expected, "303-root contains an unexpected board move");
        }
}

void verify_single_and_double_check_evasions() {
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "4r2k/8/8/8/8/8/8/4K3[N] w - - 0 1");
        require(position.checkers() && !more_than_one(position.checkers()),
                "single-check fixture does not contain one checker");

        const Bitboard expected = square_bb(SQ_E2) | SQ_E3 | SQ_E4 | SQ_E5 | SQ_E6 | SQ_E7;
        MoveList<EVASIONS> evasions(position);
        MoveList<LEGAL>    legal(position);
        require_exact_drop_squares(evasions, KNIGHT, expected, "single-check evasions");
        require_exact_drop_squares(legal, KNIGHT, expected, "single-check legal list");
        require(position.pseudo_legal(Move::make_drop(KNIGHT, SQ_E2)),
                "blocking drop is not pseudo-legal");
        require(position.legal(Move::make_drop(KNIGHT, SQ_E2)),
                "blocking drop is not legal");
        require(!position.pseudo_legal(Move::make_drop(KNIGHT, SQ_D2)),
                "nonblocking drop evades a sliding check");
        require(!position.legal(Move::make_drop(KNIGHT, SQ_D2)),
                "direct legality accepted a nonblocking drop");
        require(!position.pseudo_legal(Move::make_drop(KNIGHT, SQ_E8)),
                "drop captured an occupied checker");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "4r2k/8/8/8/1b6/8/8/4K3[N] w - - 0 1");
        require(more_than_one(position.checkers()),
                "double-check fixture does not contain two checkers");
        MoveList<EVASIONS> evasions(position);
        MoveList<LEGAL>    legal(position);
        require(count_drops(evasions) == 0 && count_drops(legal) == 0,
                "a drop evaded double check");
        require(!position.pseudo_legal(Move::make_drop(KNIGHT, SQ_E2)),
                "double-check drop is pseudo-legal");
        require(!position.legal(Move::make_drop(KNIGHT, SQ_E2)),
                "direct legality accepted a double-check drop");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/5n2/8/4K3[N] w - - 0 1");
        require(position.checkers() && !more_than_one(position.checkers()),
                "knight-check fixture does not contain one checker");
        MoveList<EVASIONS> evasions(position);
        require(count_drops(evasions) == 0, "a drop evaded a knight check");
    }
}

void verify_drop_restrictions_and_checks() {
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/4p3/8/8/K7[PNBRQ] w - - 0 1");
        MoveList<LEGAL> legal(position);
        for (PieceType type : Crazyhouse::PocketPieceTypes)
        {
            require(legal.contains(Move::make_drop(type, SQ_E3)),
                    "empty-square drop missing");
            require(!legal.contains(Move::make_drop(type, SQ_E4)),
                    "occupied-square drop generated");
            require(!position.pseudo_legal(Move::make_drop(type, SQ_E4)),
                    "occupied-square drop is pseudo-legal");
            require(!position.legal(Move::make_drop(type, SQ_E4)),
                    "direct legality accepted an occupied-square drop");
        }
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/4P3/8/K7[P] w - - 0 1");
        MoveList<LEGAL> legal(position);
        for (Square square : {SQ_E2, SQ_E4, SQ_E7})
            require(legal.contains(Move::make_drop(PAWN, square)),
                    "same-file pawn drop was rejected");
        for (Square square : {SQ_E1, SQ_E8})
        {
            require(!legal.contains(Move::make_drop(PAWN, square)),
                    "back-rank pawn drop was generated");
            require(!position.pseudo_legal(Move::make_drop(PAWN, square)),
                    "back-rank pawn drop is pseudo-legal");
            require(!position.legal(Move::make_drop(PAWN, square)),
                    "direct legality accepted a back-rank pawn drop");
        }
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "5N1k/5K2/8/8/8/8/8/8[P] w - - 0 1");
        const Move drop = Move::make_drop(PAWN, SQ_G7);
        MoveList<LEGAL> legal(position);
        require(legal.contains(drop) && position.pseudo_legal(drop) && position.legal(drop),
                "checking pawn drop is not legal");
        require(position.gives_check(drop), "checking pawn drop was not classified as check");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/8/8/K7[PP] w - - 0 1");
        MoveList<LEGAL> legal(position);
        require_no_duplicates(legal, "multi-count pocket legal list");
        require(count_drops(legal) == 48,
                "pocket multiplicity generated duplicate pawn drops");
    }
}

void verify_black_ownership_and_chess_isolation() {
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/8/8/K7[pnbrq] b - - 0 1");
        MoveList<LEGAL> legal(position);
        for (PieceType type : Crazyhouse::PocketPieceTypes)
            require(legal.contains(Move::make_drop(type, SQ_D4)),
                    "black-owned pocket type did not generate");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CHESS);
        require_set(position, state,
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
        MoveList<LEGAL> legal(position);
        require(!legal.is_growable(), "Chess selected growable list storage");
        require(count_drops(legal) == 0, "Chess generated a drop");
        require(!Move::make_drop(PAWN, SQ_E4).is_structurally_valid(Ruleset::CHESS),
                "DROP is structurally valid in Chess");
    }
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();

    verify_303_move_root();
    verify_single_and_double_check_evasions();
    verify_drop_restrictions_and_checks();
    verify_black_ownership_and_chess_isolation();

    std::cout << "PASS crazyhouse_drop_generation exact303=PASS all_types=PASS restrictions=PASS "
                 "single_check=PASS double_check=PASS check_drop=PASS ownership=PASS "
                 "duplicates=PASS chess_isolation=PASS\n";
    return EXIT_SUCCESS;
}
