/*
  Canonical per-ply Crazyhouse physical-state fixture. This freezes layout,
  bounds and copy semantics only; it does not implement FEN, move transitions,
  complete keys or Crazyhouse legality.
*/

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <type_traits>

#include "crazyhouse_state.h"
#include "position.h"

namespace {

using namespace Stockfish;

[[noreturn]] void fail(const char* message) {
    std::cerr << "FAIL crazyhouse_state_layout: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const char* message) {
    if (!condition)
        fail(message);
}

void verify_types_and_limits() {
    static_assert(std::is_standard_layout_v<PocketCounts>);
    static_assert(std::is_trivially_copyable_v<PocketCounts>);
    static_assert(std::is_standard_layout_v<CrazyhouseState>);
    static_assert(std::is_trivially_copyable_v<CrazyhouseState>);
    static_assert(std::is_trivially_copyable_v<StateInfo>);
    static_assert(sizeof(PocketCounts) == COLOR_NB * Crazyhouse::POCKET_TYPE_NB);
    static_assert(offsetof(CrazyhouseState, pockets) == 0);
    static_assert(offsetof(CrazyhouseState, promoted)
                  >= offsetof(CrazyhouseState, pockets) + sizeof(PocketCounts));
    static_assert(offsetof(CrazyhouseState, pocketKey)
                  >= offsetof(CrazyhouseState, promoted) + sizeof(Bitboard));
    static_assert(offsetof(CrazyhouseState, promotedKey)
                  >= offsetof(CrazyhouseState, pocketKey) + sizeof(Key));
    static_assert(offsetof(StateInfo, crazyhouse) < offsetof(StateInfo, key));
    static_assert(offsetof(StateInfo, crazyhouse) + sizeof(CrazyhouseState)
                  <= offsetof(StateInfo, key));

    require(Crazyhouse::pocket_index(PAWN) == 0, "pawn pocket index mismatch");
    require(Crazyhouse::pocket_index(KNIGHT) == 1, "knight pocket index mismatch");
    require(Crazyhouse::pocket_index(BISHOP) == 2, "bishop pocket index mismatch");
    require(Crazyhouse::pocket_index(ROOK) == 3, "rook pocket index mismatch");
    require(Crazyhouse::pocket_index(QUEEN) == 4, "queen pocket index mismatch");
    require(Crazyhouse::pocket_index(NO_PIECE_TYPE) == -1,
            "empty piece type entered the pocket domain");
    require(Crazyhouse::pocket_index(KING) == -1, "king entered the pocket domain");
    require(Crazyhouse::pocket_index(PIECE_TYPE_NB) == -1,
            "out-of-range piece type entered the pocket domain");

    require(Crazyhouse::max_pocket_count(PAWN) == 16, "pawn pocket limit mismatch");
    require(Crazyhouse::max_pocket_count(KNIGHT) == 4, "knight pocket limit mismatch");
    require(Crazyhouse::max_pocket_count(BISHOP) == 4, "bishop pocket limit mismatch");
    require(Crazyhouse::max_pocket_count(ROOK) == 4, "rook pocket limit mismatch");
    require(Crazyhouse::max_pocket_count(QUEEN) == 2, "queen pocket limit mismatch");
    require(Crazyhouse::max_pocket_count(KING) == -1, "king received a pocket limit");
}

void verify_copied_prefix() {
    CrazyhouseState expected{};
    expected.pockets.count[WHITE][Crazyhouse::pocket_index(PAWN)]   = 16;
    expected.pockets.count[WHITE][Crazyhouse::pocket_index(QUEEN)]  = 2;
    expected.pockets.count[BLACK][Crazyhouse::pocket_index(KNIGHT)] = 4;
    expected.pockets.count[BLACK][Crazyhouse::pocket_index(ROOK)]   = 3;
    expected.promoted    = (Bitboard(1) << SQ_A1) | (Bitboard(1) << SQ_H8);
    expected.pocketKey   = UINT64_C(0x0123456789ABCDEF);
    expected.promotedKey = UINT64_C(0xFEDCBA9876543210);

    StateInfo source{};
    source.crazyhouse = expected;
    source.key        = UINT64_C(0x1111111111111111);

    StateInfo copied;
    std::memset(&copied, 0xA5, sizeof(copied));
    std::memcpy(&copied, &source, offsetof(StateInfo, key));

    require(std::memcmp(&copied.crazyhouse, &expected, sizeof(expected)) == 0,
            "Crazyhouse state was not copied byte-exactly with the StateInfo prefix");
    require(copied.key != source.key, "StateInfo key crossed the copied-prefix boundary");
}

void verify_zero_initial_state(Ruleset ruleset) {
    StateInfo         state;
    Position          position(ruleset);
    const char* const fen   = ruleset == Ruleset::CRAZYHOUSE
                              ? "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1"
                              : "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    const auto        error = position.set(fen, false, ruleset, &state);
    require(!error.has_value(), "orthodox start position setup failed");

    const CrazyhouseState& physical = position.crazyhouse_state();
    const CrazyhouseState  zero{};
    require(std::memcmp(&physical, &zero, sizeof(zero)) == 0,
            "new Position did not start with zero Crazyhouse state");

    for (Color color : {WHITE, BLACK})
        for (PieceType type : Crazyhouse::PocketPieceTypes)
            require(position.pocket_count(color, type) == 0,
                    "new Position exposed a nonzero pocket count");
    require(position.promoted_pieces() == 0,
            "new Position exposed promoted provenance without a marker");
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();

    verify_types_and_limits();
    verify_copied_prefix();
    verify_zero_initial_state(Ruleset::CHESS);
    verify_zero_initial_state(Ruleset::CRAZYHOUSE);

    std::cout << "PASS crazyhouse_state_layout pocket_bytes=10 pocket_limits=16,4,4,4,2 "
                 "stateinfo_prefix=PASS chess_zero=PASS crazyhouse_zero=PASS\n";
    return EXIT_SUCCESS;
}
