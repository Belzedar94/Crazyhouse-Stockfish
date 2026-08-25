/*
  Crazyhouse repetition and terminal-policy fixture derived from the frozen
  LICHESS_CRAZYHOUSE_2026_08_12 authority corpus. This certifies history
  horizon, occurrence policy, no-move precedence and explicit Syzygy bypass.
  It does not certify UCI routing, evaluation, referee behavior or strength.
*/

#include <array>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <string>
#include <string_view>

#include "movegen.h"
#include "position.h"
#include "syzygy/tbprobe.h"

namespace {

using namespace Stockfish;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_repetition_terminal: " << message << '\n';
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

void play(Position& position, std::deque<StateInfo>& states, Move move) {
    require(MoveList<LEGAL>(position).contains(move), "fixture move is not legal");
    position.do_move(move, states.emplace_back());
}

void play_king_cycle(Position& position, std::deque<StateInfo>& states) {
    play(position, states, Move(SQ_A1, SQ_A2));
    play(position, states, Move(SQ_H8, SQ_H7));
    play(position, states, Move(SQ_A2, SQ_A1));
    play(position, states, Move(SQ_H7, SQ_H8));
}

void require_status(const CrazyhouseTerminalStatus& status, CrazyhouseTerminalReason reason,
                    std::string_view label) {
    require(status.reason == reason, std::string(label) + " terminal reason mismatch");
    require(status.ended() == (reason != CrazyhouseTerminalReason::ONGOING),
            std::string(label) + " ended flag mismatch");
}

template<usize N>
void attach_same_key_history(StateInfo& current, std::array<StateInfo, N>& history) {
    static_assert(N % 2 == 0);
    StateInfo* cursor = &current;
    for (usize i = 0; i < N; ++i)
    {
        cursor->previous = &history[i];
        history[i].key   = (i % 2 == 1) ? current.key : current.key ^ make_key(i + 1);
        cursor           = &history[i];
    }
    cursor->previous        = nullptr;
    current.pliesFromNull   = int(N);
    current.rule50          = 0;
    current.repetition      = -2;
}

void verify_repetition_policy() {
    StateInfo             initial;
    std::deque<StateInfo> states;
    Position              position(Ruleset::CRAZYHOUSE);
    require_set(position, initial, "7k/8/8/8/8/8/8/K7[] w - - 0 1");

    play_king_cycle(position, states);
    play_king_cycle(position, states);
    require(position.repetition_occurrences() == 3, "threefold occurrence count mismatch");
    require(position.is_repetition(1), "threefold search-cycle predicate is false");
    require_status(position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY),
                   CrazyhouseTerminalReason::ONGOING, "threefold automatic policy");
    require_status(
      position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::THREEFOLD_IMMEDIATE_CLAIM),
      CrazyhouseTerminalReason::THREEFOLD_REPETITION_CLAIM, "threefold claim proxy");

    play_king_cycle(position, states);
    play_king_cycle(position, states);
    require(position.repetition_occurrences() == 5, "fivefold occurrence count mismatch");
    require_status(position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY),
                   CrazyhouseTerminalReason::FIVEFOLD_REPETITION,
                   "fivefold automatic policy");
}

void verify_history_horizon_and_identity() {
    {
        StateInfo                current;
        std::array<StateInfo, 4> history{};
        Position                 position(Ruleset::CRAZYHOUSE);
        require_set(position, current, "7k/8/8/8/8/8/8/K7[] w - - 0 1");
        attach_same_key_history(current, history);
        require(position.rule50_count() == 0, "synthetic zeroing boundary is not frozen");
        require(position.repetition_occurrences() == 3,
                "Crazyhouse repetition was truncated by rule50");
        require(position.has_repeated(), "Crazyhouse history scan stopped at rule50");
    }

    {
        StateInfo                current;
        StateInfo                boardOnly;
        std::array<StateInfo, 4> history{};
        Position                 withPocket(Ruleset::CRAZYHOUSE);
        Position                 withoutPocket(Ruleset::CRAZYHOUSE);
        require_set(withPocket, current, "7k/8/8/8/8/8/8/K7[P] w - - 0 1");
        require_set(withoutPocket, boardOnly, "7k/8/8/8/8/8/8/K7[] w - - 0 1");
        attach_same_key_history(current, history);
        history[1].key = history[3].key = boardOnly.key;
        require(current.key != boardOnly.key, "pocket identity fixture collided");
        require(withPocket.repetition_occurrences() == 1,
                "board-only identity created a false pocket repetition");
    }

    {
        StateInfo                current;
        StateInfo                unmarked;
        std::array<StateInfo, 4> history{};
        Position                 promoted(Ruleset::CRAZYHOUSE);
        Position                 ordinary(Ruleset::CRAZYHOUSE);
        require_set(promoted, current, "7k/Q~7/8/8/8/8/8/K7[] w - - 0 1");
        require_set(ordinary, unmarked, "7k/Q7/8/8/8/8/8/K7[] w - - 0 1");
        attach_same_key_history(current, history);
        history[1].key = history[3].key = unmarked.key;
        require(current.key != unmarked.key, "promoted identity fixture collided");
        require(promoted.repetition_occurrences() == 1,
                "board-only identity created a false provenance repetition");
    }
}

void verify_upcoming_shortcut_boundary() {
    {
        StateInfo             initial;
        std::deque<StateInfo> states;
        Position              position(Ruleset::CRAZYHOUSE);
        require_set(position, initial, "7k/8/8/8/8/8/8/K7[] w - - 0 1");
        play(position, states, Move(SQ_A1, SQ_A2));
        play(position, states, Move(SQ_H8, SQ_H7));
        play(position, states, Move(SQ_A2, SQ_A1));
        require(!position.upcoming_repetition(4),
                "orthodox upcoming-repetition shortcut ran in Crazyhouse");
    }

    {
        StateInfo             initial;
        std::deque<StateInfo> states;
        Position              position(Ruleset::CHESS);
        require_set(position, initial, "7k/8/8/8/8/8/8/K7 w - - 0 1");
        play(position, states, Move(SQ_A1, SQ_A2));
        play(position, states, Move(SQ_H8, SQ_H7));
        play(position, states, Move(SQ_A2, SQ_A1));
        require(position.upcoming_repetition(4),
                "Chess upcoming-repetition control changed");
    }
}

void verify_no_move_and_draw_precedence() {
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "k7/1Q6/2K5/8/8/8/8/8[] b - - 0 1");
        const auto status =
          position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY);
        require_status(status, CrazyhouseTerminalReason::CHECKMATE, "checkmate");
        require(status.winner && *status.winner == WHITE, "checkmate winner mismatch");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "k7/2Q5/2K5/8/8/8/8/8[] b - - 0 1");
        const auto status =
          position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY);
        require_status(status, CrazyhouseTerminalReason::STALEMATE, "stalemate");
        require(!status.winner, "stalemate has a winner");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "k7/2Q5/2K5/8/8/8/8/8[n] b - - 0 1");
        require(MoveList<LEGAL>(position).contains(Move::make_drop(KNIGHT, SQ_A1)),
                "pocket stalemate escape is absent");
        require_status(position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY),
                       CrazyhouseTerminalReason::ONGOING, "pocket prevents stalemate");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/8/8/K7[] w - - 100 1");
        require(!position.is_draw(1), "Crazyhouse applied the 50-move rule");
        require_status(position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY),
                       CrazyhouseTerminalReason::ONGOING,
                       "no-50 and no-insufficient boundary");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CHESS);
        require_set(position, state, "7k/8/8/8/8/8/8/K7 w - - 100 1");
        require(position.is_draw(1), "Chess 50-move control changed");
    }

    {
        StateInfo                state;
        std::array<StateInfo, 8> history{};
        Position                 position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "k7/1Q6/2K5/8/8/8/8/8[] b - - 0 1");
        attach_same_key_history(state, history);
        require(position.repetition_occurrences() == 5,
                "synthetic precedence fixture is not fivefold");
        require_status(position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY),
                       CrazyhouseTerminalReason::CHECKMATE,
                       "checkmate precedence over fivefold");
    }

    {
        StateInfo             initial;
        StateInfo             after;
        Position              position(Ruleset::CRAZYHOUSE);
        require_set(position, initial, "5N1k/5K2/8/8/8/8/8/8[P] w - - 0 1");
        const Move drop = Move::make_drop(PAWN, SQ_G7);
        require(MoveList<LEGAL>(position).contains(drop), "drop-mate move is not legal");
        position.do_move(drop, after);
        require_status(position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY),
                       CrazyhouseTerminalReason::CHECKMATE, "drop checkmate");
    }
}

void verify_castling_identity_and_syzygy_bypass() {
    {
        StateInfo             initial;
        std::deque<StateInfo> states;
        Position              position(Ruleset::CRAZYHOUSE);
        require_set(position, initial,
                    "r3k2r/8/8/8/8/8/8/R3K2R[] w KQkq - 0 1");
        for (int cycle = 0; cycle < 2; ++cycle)
        {
            play(position, states, Move(SQ_E1, SQ_F1));
            play(position, states, Move(SQ_E8, SQ_F8));
            play(position, states, Move(SQ_F1, SQ_E1));
            play(position, states, Move(SQ_F8, SQ_E8));
        }
        require(position.repetition_occurrences() == 2,
                "castling-right loss was ignored in repetition identity");
        require_status(position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::THREEFOLD_IMMEDIATE_CLAIM),
                       CrazyhouseTerminalReason::ONGOING,
                       "castling-right-distinct history");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/8/8/K7[] w - - 0 1");
        require(!position.tablebases_applicable(), "Crazyhouse reports Syzygy applicable");

        Tablebases::ProbeState probe = Tablebases::OK;
        require(Tablebases::probe_wdl(position, &probe) == Tablebases::WDLDraw
                  && probe == Tablebases::FAIL,
                "Crazyhouse WDL probe did not fail closed");
        probe = Tablebases::OK;
        require(Tablebases::probe_dtz(position, &probe) == 0 && probe == Tablebases::FAIL,
                "Crazyhouse DTZ probe did not fail closed");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CHESS);
        require_set(position, state, "7k/8/8/8/8/8/8/K7 w - - 0 1");
        require(position.tablebases_applicable(), "Chess Syzygy applicability changed");
    }
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();

    verify_repetition_policy();
    verify_history_horizon_and_identity();
    verify_upcoming_shortcut_boundary();
    verify_no_move_and_draw_precedence();
    verify_castling_identity_and_syzygy_bypass();

    std::cout << "PASS crazyhouse_repetition_terminal horizon=PASS threefold=PASS "
                 "fivefold=PASS precedence=PASS no_50=PASS no_insufficient=PASS "
                 "stalemate=PASS syzygy=PASS chess_isolation=PASS\n";
    return EXIT_SUCCESS;
}
