/*
  Typed ruleset-boundary tests. These checks do not enable Crazyhouse rules,
  drops, pockets, evaluation or UCI routing.
*/

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <type_traits>

#include "position.h"
#include "ruleset.h"

namespace {

using Stockfish::Position;
using Stockfish::Ruleset;
using Stockfish::is_valid_ruleset;
using Stockfish::ruleset_from_uci;
using Stockfish::ruleset_name;
using Stockfish::uses_growable_move_storage;

[[noreturn]] void fail(const char* message) {
    std::cerr << "FAIL crazyhouse_ruleset_boundary: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const char* message) {
    if (!condition)
        fail(message);
}

void verify_identity_and_parse() {
    static_assert(sizeof(Ruleset) == 1, "Ruleset must retain its one-byte frozen ABI");
    static_assert(std::is_trivially_copyable_v<Ruleset>, "Ruleset must be trivially copyable");

    require(is_valid_ruleset(Ruleset::CHESS), "CHESS is not a valid ruleset");
    require(is_valid_ruleset(Ruleset::CRAZYHOUSE), "CRAZYHOUSE is not a valid ruleset");
    require(!is_valid_ruleset(static_cast<Ruleset>(2)), "unknown ruleset 2 was accepted");
    require(!is_valid_ruleset(static_cast<Ruleset>(255)), "unknown ruleset 255 was accepted");

    require(ruleset_name(Ruleset::CHESS) == "chess", "CHESS name mismatch");
    require(ruleset_name(Ruleset::CRAZYHOUSE) == "crazyhouse", "CRAZYHOUSE name mismatch");
    require(ruleset_from_uci("chess") == Ruleset::CHESS, "chess parser mismatch");
    require(ruleset_from_uci("crazyhouse") == Ruleset::CRAZYHOUSE, "crazyhouse parser mismatch");

    for (const char* rejected :
         {"", "standard", "Chess", "CRAZYHOUSE", " crazyhouse", "crazyhouse ", "atomic"})
        require(!ruleset_from_uci(rejected).has_value(), "malformed ruleset token was accepted");
}

void verify_position_ownership_and_storage_mapping() {
    Position chess;
    Position crazyhouse(Ruleset::CRAZYHOUSE);

    require(chess.ruleset() == Ruleset::CHESS, "default Position is not CHESS");
    require(crazyhouse.ruleset() == Ruleset::CRAZYHOUSE,
            "explicit Position lost CRAZYHOUSE ownership");
    require(!uses_growable_move_storage(chess.ruleset()), "CHESS selected growable storage");
    require(uses_growable_move_storage(crazyhouse.ruleset()),
            "CRAZYHOUSE did not select growable storage");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--invalid-storage-control") == 0)
    {
        static_cast<void>(uses_growable_move_storage(static_cast<Ruleset>(2)));
        fail("invalid-storage control returned instead of aborting");
    }
    if (argc == 2 && std::strcmp(argv[1], "--invalid-position-control") == 0)
    {
        Position invalid(static_cast<Ruleset>(255));
        static_cast<void>(invalid);
        fail("invalid-position control returned instead of aborting");
    }

    require(argc == 1, "unknown command-line argument");
    verify_identity_and_parse();
    verify_position_ownership_and_storage_mapping();

    std::cout << "PASS crazyhouse_ruleset_boundary values=chess,crazyhouse parser=exact "
                 "position_owner=PASS storage_mapping=PASS invalid_controls=SEPARATE\n";
    return EXIT_SUCCESS;
}
