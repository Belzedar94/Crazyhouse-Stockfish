/*
  Crazyhouse origin-free search primitive fixture. This freezes conservative
  correctness routing before any Crazyhouse evaluator or UCI search route is
  admitted. It does not certify evaluation, search strength or time behavior.
*/

#include <array>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <memory>
#include <set>
#include <string>
#include <string_view>

#include "history.h"
#include "movepick.h"
#include "position.h"

namespace {

using namespace Stockfish;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_search_primitives: " << message << '\n';
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

struct PickerContext {
    std::unique_ptr<ButterflyHistory>      mainHistory = std::make_unique<ButterflyHistory>();
    std::unique_ptr<LowPlyHistory>         lowPlyHistory = std::make_unique<LowPlyHistory>();
    std::unique_ptr<CapturePieceToHistory> captureHistory =
      std::make_unique<CapturePieceToHistory>();
    std::array<std::unique_ptr<PieceToHistory>, 6> continuationStorage;
    std::array<const PieceToHistory*, 6>           continuation{};
    SharedHistories                                shared{1};

    PickerContext() {
        mainHistory->fill(0);
        lowPlyHistory->fill(0);
        captureHistory->fill(0);
        shared.pawnHistory.clear_range(0, 0, 1);
        shared.correctionHistory.clear_range(0, 0, 1);

        for (usize i = 0; i < continuationStorage.size(); ++i)
        {
            continuationStorage[i] = std::make_unique<PieceToHistory>();
            continuationStorage[i]->fill(0);
            continuation[i] = continuationStorage[i].get();
        }
    }

    MovePicker picker(const Position& position, Depth depth) {
        return MovePicker(position, Move::none(), depth, mainHistory.get(), lowPlyHistory.get(),
                          captureHistory.get(), continuation.data(), &shared, 0);
    }
};

struct PickedMoves {
    int           total = 0;
    int           drops = 0;
    std::set<u16> raw;
};

PickedMoves collect(MovePicker& picker) {
    PickedMoves result;
    for (Move move = picker.next_move(); move != Move::none(); move = picker.next_move())
    {
        require(++result.total <= 1024, "move picker did not terminate");
        result.drops += move.is_drop();
        require(result.raw.insert(move.raw()).second, "move picker emitted a duplicate");
    }
    return result;
}

void verify_typed_drop_piece_and_prefetch() {
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/8/8/K7[N] w - - 0 1");
        const Move drop = Move::make_drop(KNIGHT, SQ_E4);
        require(position.moved_piece(drop) == W_KNIGHT,
                "white drop did not resolve a typed moved piece");
        require(!position.prefetch_key(drop).has_value(),
                "drop produced an orthodox speculative key");
        require(!position.capture_stage(drop), "drop entered capture stage");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/8/8/K7[n] b - - 0 1");
        require(position.moved_piece(Move::make_drop(KNIGHT, SQ_E4)) == B_KNIGHT,
                "black drop did not resolve a typed moved piece");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/n7/8/8/8/8/R7/K7[] w - - 0 1");
        require(!position.prefetch_key(Move(SQ_A2, SQ_A7)).has_value(),
                "Crazyhouse board capture produced an incomplete speculative key");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CHESS);
        require_set(position, state,
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
        require(position.moved_piece(Move(SQ_E2, SQ_E4)) == W_PAWN,
                "Chess moved-piece control changed");
        require(position.prefetch_key(Move(SQ_E2, SQ_E4)).has_value(),
                "Chess speculative-key control was disabled");
    }
}

void verify_enabled_see() {
    StateInfo state;
    Position  position(Ruleset::CRAZYHOUSE);
    require_set(position, state, "7k/8/8/8/8/8/R7/K7[N] w - - 0 1");

    require(!position.see_ge(Move::make_drop(KNIGHT, SQ_E4), std::numeric_limits<int>::max()),
            "drop bypassed enabled Crazyhouse SEE");
    require(!position.see_ge(Move(SQ_A2, SQ_A3), std::numeric_limits<int>::max()),
            "Crazyhouse board move bypassed enabled SEE");
}

void verify_move_picker_drop_paths() {
    PickerContext context;

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "7k/8/8/8/8/8/4K3/8[PNBRQ] w - - 0 1");
        MovePicker picker = context.picker(position, 1);
        const auto picked = collect(picker);
        require(picked.total == 303, "303-root move-picker count mismatch");
        require(picked.drops == 295, "303-root move-picker drop count mismatch");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        require_set(position, state, "4r2k/8/8/8/8/8/8/4K3[N] w - - 0 1");
        MovePicker picker = context.picker(position, 1);
        const auto picked = collect(picker);
        for (Square square : {SQ_E2, SQ_E3, SQ_E4, SQ_E5, SQ_E6, SQ_E7})
            require(picked.raw.count(Move::make_drop(KNIGHT, square).raw()) == 1,
                    "drop interposition did not survive move-picker scoring");
        require(picked.drops == 6, "single-check move picker drop count mismatch");
    }

    {
        StateInfo state;
        Position  position(Ruleset::CHESS);
        require_set(position, state,
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
        MovePicker picker = context.picker(position, 1);
        const auto picked = collect(picker);
        require(picked.total == 20 && picked.drops == 0,
                "Chess start-position move-picker control changed");
    }
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();

    verify_typed_drop_piece_and_prefetch();
    verify_enabled_see();
    verify_move_picker_drop_paths();

    std::cout << "PASS crazyhouse_search_primitives moved_piece=PASS prefetch=DISABLED "
                 "see=ENABLED move_picker_303=PASS drop_evasions=PASS "
                 "chess_isolation=PASS\n";
    return EXIT_SUCCESS;
}
