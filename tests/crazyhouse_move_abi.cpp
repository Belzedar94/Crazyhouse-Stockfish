/*
  Exhaustive CH_DROP16_V1 classification and structural-validation fixture.
  This freezes representation only; it does not assert Crazyhouse legality.
*/

#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <type_traits>

#include "types.h"

namespace {

using namespace Stockfish;

[[noreturn]] void fail(const char* message) {
    std::cerr << "FAIL crazyhouse_move_abi: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const char* message) {
    if (!condition)
        fail(message);
}

void verify_frozen_identity() {
    static_assert(sizeof(Move) == 2, "Move must retain its 16-bit ABI");
    static_assert(std::is_trivially_copyable_v<Move>, "Move must remain trivially copyable");
    static_assert(static_cast<unsigned>(MoveKind::INVALID) == 0);
    static_assert(static_cast<unsigned>(MoveKind::NORMAL) == 1);
    static_assert(static_cast<unsigned>(MoveKind::PROMOTION) == 2);
    static_assert(static_cast<unsigned>(MoveKind::EN_PASSANT) == 3);
    static_assert(static_cast<unsigned>(MoveKind::CASTLING) == 4);
    static_assert(static_cast<unsigned>(MoveKind::DROP) == 5);
    static_assert(DROP_TAG == 0x3000);
    static_assert(DROP_SOURCE_BASE == 56);

    require(Move::none().raw() == 0, "Move::none identity changed");
    require(Move::null().raw() == 65, "Move::null identity changed");
    require(Move::none().kind() == MoveKind::INVALID, "Move::none kind is not INVALID");
    require(Move::null().kind() == MoveKind::INVALID, "Move::null kind is not INVALID");
    require(Move::none().is_structurally_valid(Ruleset::CHESS), "Move::none sentinel was rejected");
    require(Move::null().is_structurally_valid(Ruleset::CRAZYHOUSE),
            "Move::null sentinel was rejected");

    const Move normal(SQ_E2, SQ_E4);
    require(normal.raw() == 0x031C, "orthodox normal encoding changed");
    require(normal.kind() == MoveKind::NORMAL, "normal kind mismatch");

    const Move promotion = Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN);
    require(promotion.raw() == 0x7C38, "orthodox promotion encoding changed");
    require(promotion.kind() == MoveKind::PROMOTION, "promotion kind mismatch");
    require(promotion.promotion_type() == QUEEN, "promotion payload mismatch");

    const Move enPassant = Move::make<EN_PASSANT>(SQ_E5, SQ_D6);
    const Move castling  = Move::make<CASTLING>(SQ_E1, SQ_H1);
    require(enPassant.kind() == MoveKind::EN_PASSANT, "en-passant kind mismatch");
    require(castling.kind() == MoveKind::CASTLING, "castling kind mismatch");
}

void verify_drop_ranges_and_round_trip() {
    constexpr std::array<PieceType, 5> PocketTypes = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    constexpr std::array<u16, 5>       FirstRaw    = {0x3E00, 0x3E40, 0x3E80, 0x3EC0, 0x3F00};

    for (usize typeIndex = 0; typeIndex < PocketTypes.size(); ++typeIndex)
        for (int square = 0; square < SQUARE_NB; ++square)
        {
            const Square to   = static_cast<Square>(square);
            const Move   drop = Move::make_drop(PocketTypes[typeIndex], to);
            require(drop.raw() == FirstRaw[typeIndex] + square, "drop raw range mismatch");
            require(drop.kind() == MoveKind::DROP, "drop kind mismatch");
            require(drop.is_drop(), "drop predicate mismatch");
            require(drop.type_of() == NORMAL, "drop changed the orthodox class bits");
            require(drop.drop_piece_type() == PocketTypes[typeIndex],
                    "drop piece round-trip mismatch");
            require(drop.to_sq() == to, "drop destination round-trip mismatch");
            require(!drop.is_structurally_valid(Ruleset::CHESS),
                    "chess accepted a structurally valid drop");
            require(drop.is_structurally_valid(Ruleset::CRAZYHOUSE),
                    "Crazyhouse rejected a canonical drop");
        }

    require(Move::make_drop(PAWN, SQ_A1).is_structurally_valid(Ruleset::CRAZYHOUSE),
            "structural layer applied pawn-drop rank legality");
    require(Move::make_drop(PAWN, SQ_H8).is_structurally_valid(Ruleset::CRAZYHOUSE),
            "structural layer applied pawn-drop rank legality");

    for (std::uint32_t raw = 0x3F40; raw <= 0x3FFF; ++raw)
        require(Move(static_cast<u16>(raw)).kind() == MoveKind::INVALID,
                "reserved drop payload was classified as a move");
}

void verify_exhaustive_classification() {
    std::array<std::uint32_t, 6> kindCounts{};
    std::uint32_t                chessStructural      = 0;
    std::uint32_t                crazyhouseStructural = 0;

    for (std::uint32_t raw = 0; raw <= 0xFFFF; ++raw)
    {
        const Move     move(static_cast<u16>(raw));
        const MoveKind kind  = move.kind();
        const auto     index = static_cast<usize>(kind);
        require(index < kindCounts.size(), "MoveKind escaped its frozen range");
        ++kindCounts[index];

        if (kind == MoveKind::DROP)
        {
            require(move.is_drop(), "DROP kind disagrees with is_drop");
            require(move.drop_piece_type() >= PAWN && move.drop_piece_type() <= QUEEN,
                    "DROP decoded a non-pocket piece");
            require(Move::make_drop(move.drop_piece_type(), move.to_sq()) == move,
                    "DROP exhaustive round-trip mismatch");
        }
        else
            require(!move.is_drop(), "non-DROP kind passed is_drop");

        chessStructural += move.is_structurally_valid(Ruleset::CHESS);
        crazyhouseStructural += move.is_structurally_valid(Ruleset::CRAZYHOUSE);
    }

    const std::array<std::uint32_t, 6> expected = {36546, 4094, 16384, 4096, 4096, 320};
    require(kindCounts == expected, "exhaustive MoveKind counts changed");
    require(chessStructural == 28226, "chess structural count changed");
    require(crazyhouseStructural == 28546, "Crazyhouse structural count changed");

    for (u16 raw : {u16(0x1000), u16(0x2000), u16(0x3000), u16(0x3DFF), u16(0x3F40), u16(0x3FFF),
                    u16(0x9000), u16(0xBFFF), u16(0xD000), u16(0xFFFF)})
        require(Move(raw).kind() == MoveKind::INVALID, "malformed raw value was accepted");

    const Move sameSquareNormal(SQ_C1, SQ_C1);
    require(sameSquareNormal.kind() == MoveKind::NORMAL,
            "same-square normal lost its encoding class");
    require(!sameSquareNormal.is_structurally_valid(Ruleset::CHESS),
            "non-sentinel same-square normal was structurally accepted");
    require(!Move::make<PROMOTION>(SQ_A7, SQ_A7, QUEEN).is_structurally_valid(Ruleset::CRAZYHOUSE),
            "same-square promotion was structurally accepted");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--drop-from-control") == 0)
    {
        static_cast<void>(Move::make_drop(KNIGHT, SQ_E4).from_sq());
        fail("drop-from control returned instead of asserting");
    }
    if (argc == 2 && std::strcmp(argv[1], "--nondrop-piece-control") == 0)
    {
        static_cast<void>(Move(SQ_E2, SQ_E4).drop_piece_type());
        fail("nondrop-piece control returned instead of asserting");
    }
    if (argc == 2 && std::strcmp(argv[1], "--nonpromotion-control") == 0)
    {
        static_cast<void>(Move(SQ_E2, SQ_E4).promotion_type());
        fail("nonpromotion control returned instead of asserting");
    }
    if (argc == 2 && std::strcmp(argv[1], "--invalid-drop-piece-control") == 0)
    {
        static_cast<void>(Move::make_drop(KING, SQ_E4));
        fail("invalid-drop-piece control returned instead of asserting");
    }
    if (argc == 2 && std::strcmp(argv[1], "--invalid-ruleset-control") == 0)
    {
        static_cast<void>(Move(SQ_E2, SQ_E4).is_structurally_valid(static_cast<Ruleset>(255)));
        fail("invalid-ruleset control returned instead of aborting");
    }

    require(argc == 1, "unknown command-line argument");
    verify_frozen_identity();
    verify_drop_ranges_and_round_trip();
    verify_exhaustive_classification();

    std::cout << "PASS crazyhouse_move_abi raw=65536 invalid=36546 normal=4094 "
                 "promotion=16384 ep=4096 castling=4096 drop=320 structural_chess=28226 "
                 "structural_crazyhouse=28546 controls=SEPARATE\n";
    return EXIT_SUCCESS;
}
