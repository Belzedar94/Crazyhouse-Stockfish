/*
  Exhaustive Crazyhouse UCI drop-codec fixture. This tests syntax and
  normalization only; it does not assert that a parsed drop is legal.
*/

#include <array>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <string>

#include "crazyhouse_move_codec.h"

namespace {

using namespace Stockfish;

[[noreturn]] void fail(const char* message) {
    std::cerr << "FAIL crazyhouse_move_codec: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const char* message) {
    if (!condition)
        fail(message);
}

void verify_exhaustive_round_trip() {
    constexpr std::array<PieceType, 5> PocketTypes = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    constexpr std::array<char, 5>      UpperRoles  = {'P', 'N', 'B', 'R', 'Q'};
    constexpr std::array<char, 5>      LowerRoles  = {'p', 'n', 'b', 'r', 'q'};

    for (usize typeIndex = 0; typeIndex < PocketTypes.size(); ++typeIndex)
        for (int square = 0; square < SQUARE_NB; ++square)
        {
            const Square      to        = static_cast<Square>(square);
            const Move        drop      = Move::make_drop(PocketTypes[typeIndex], to);
            const std::string canonical = format_drop_uci(drop);

            require(canonical.size() == 4, "canonical drop length mismatch");
            require(canonical[0] == UpperRoles[typeIndex], "canonical role is not uppercase");
            require(canonical[1] == '@', "canonical delimiter mismatch");
            require(canonical[2] == char('a' + file_of(to)), "canonical file mismatch");
            require(canonical[3] == char('1' + rank_of(to)), "canonical rank mismatch");

            const auto upperParsed = parse_drop_uci(canonical);
            require(upperParsed && *upperParsed == drop, "uppercase role round-trip mismatch");

            std::string lowercase  = canonical;
            lowercase[0]           = LowerRoles[typeIndex];
            const auto lowerParsed = parse_drop_uci(lowercase);
            require(lowerParsed && *lowerParsed == drop, "lowercase role round-trip mismatch");
            require(format_drop_uci(*lowerParsed) == canonical,
                    "lowercase input did not normalize to uppercase output");
        }
}

void verify_rejections() {
    constexpr std::array<const char*, 32> Rejected = {
      "",      "P",     "P@",    "P@e",   "P@e40", " P@e4", "P@e4 ", "P@@e4",
      "P-e4",  "P@E4",  "P@a0",  "P@a9",  "P@i4",  "K@e4",  "k@e4",  "X@e4",
      "1@e4",  "@@e4",  "PPe4",  "pe4",   "P@é4",  "Ｐ@e4", "P\te4", "P\ne4",
      "p@a00", "q@h80", "N@a2x", "n @a2", "N@ a2", "N@a 2", "0@a1",  "_@a1"};

    for (const char* text : Rejected)
        require(!parse_drop_uci(text), "malformed drop syntax was accepted");

    require(!parse_drop_uci("e2e4"), "orthodox move was misparsed as a drop");
    require(!parse_drop_uci("0000"), "null move was misparsed as a drop");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--format-nondrop-control") == 0)
    {
        static_cast<void>(format_drop_uci(Move(SQ_E2, SQ_E4)));
        fail("format-nondrop control returned instead of asserting");
    }

    require(argc == 1, "unknown command-line argument");
    verify_exhaustive_round_trip();
    verify_rejections();

    std::cout << "PASS crazyhouse_move_codec canonical=320 lowercase=320 rejected=34 "
                 "output_role=uppercase square=lowercase\n";
    return EXIT_SUCCESS;
}
