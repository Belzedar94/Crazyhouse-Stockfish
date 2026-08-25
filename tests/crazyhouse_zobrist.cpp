/*
  Crazyhouse full-identity setup fixture. This freezes setup-time Zobrist
  identity only; it does not certify make/undo/null/drop transitions,
  repetition scanning, speculative keys, TT prefetch or game results.
*/

#include <array>
#include <cstdlib>
#include <iostream>
#include <set>
#include <string>
#include <string_view>

#include "position.h"

namespace {

using namespace Stockfish;

struct IdentitySnapshot {
    Key raw;
    Key publicKey;
    Key pocket;
    Key promoted;
};

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_zobrist: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

IdentitySnapshot snapshot(std::string_view fen, Ruleset ruleset) {
    StateInfo state;
    Position  position(ruleset);
    const auto error = position.set(std::string(fen), false, ruleset, &state);
    require(!error.has_value(), "setup rejected: " + std::string(fen)
                                  + (error ? " :: " + std::string(error->what()) : ""));
    return {state.key, position.key(), state.crazyhouse.pocketKey,
            state.crazyhouse.promotedKey};
}

std::string pocket_fen(char piece, int count) {
    return "7k/8/8/8/8/8/8/K7[" + std::string(usize(count), piece) + "] w - - 0 1";
}

void verify_chess_identity_is_unsalted() {
    const auto normal = snapshot("7k/8/8/8/8/8/8/K7 w - - 0 1", Ruleset::CHESS);
    require(normal.pocket == 0 && normal.promoted == 0,
            "Chess carried Crazyhouse component keys");
    require(normal.raw == normal.publicKey, "low-rule50 Chess key was adjusted");

    const auto highRule50 = snapshot("7k/8/8/8/8/8/8/K7 w - - 14 1", Ruleset::CHESS);
    require(highRule50.raw != highRule50.publicKey,
            "Chess lost its established rule50 key adjustment");
}

void verify_ruleset_and_component_formula() {
    constexpr std::string_view chessFen = "7k/8/8/8/8/8/Q7/K7 w - - 0 1";
    constexpr std::string_view emptyFen = "7k/8/8/8/8/8/Q7/K7[] w - - 0 1";
    constexpr std::string_view pocketFen = "7k/8/8/8/8/8/Q7/K7[Nq] w - - 0 1";
    constexpr std::string_view markedFen = "7k/8/8/8/8/8/Q~7/K7[] w - - 0 1";
    constexpr std::string_view combinedFen = "7k/8/8/8/8/8/Q~7/K7[Nq] w - - 0 1";

    const auto chess = snapshot(chessFen, Ruleset::CHESS);
    const auto empty = snapshot(emptyFen, Ruleset::CRAZYHOUSE);
    const auto pocket = snapshot(pocketFen, Ruleset::CRAZYHOUSE);
    const auto marked = snapshot(markedFen, Ruleset::CRAZYHOUSE);
    const auto combined = snapshot(combinedFen, Ruleset::CRAZYHOUSE);

    const Key rulesetSalt = chess.raw ^ empty.raw;
    require(rulesetSalt != 0, "Crazyhouse ruleset salt is zero");
    require(empty.pocket == 0 && empty.promoted == 0,
            "empty Crazyhouse state has nonzero component keys");
    require(pocket.pocket != 0 && pocket.promoted == 0,
            "pocket-only component classification failed");
    require(marked.pocket == 0 && marked.promoted != 0,
            "promoted-only component classification failed");
    require(combined.pocket == pocket.pocket && combined.promoted == marked.promoted,
            "combined components do not match isolated components");
    require(pocket.raw == (empty.raw ^ pocket.pocket), "pocket full-key formula mismatch");
    require(marked.raw == (empty.raw ^ marked.promoted),
            "promoted full-key formula mismatch");
    require(combined.raw == (empty.raw ^ combined.pocket ^ combined.promoted),
            "combined full-key formula mismatch");

    for (const auto identity : {empty, pocket, marked, combined})
        require(identity.raw == identity.publicKey,
                "Crazyhouse public key applied a rule50 adjustment");

    const auto highRule50 =
      snapshot("7k/8/8/8/8/8/Q~7/K7[Nq] w - - 200 1", Ruleset::CRAZYHOUSE);
    require(highRule50.raw == highRule50.publicKey,
            "high-rule50 Crazyhouse key was not raw");
    require(highRule50.raw == combined.raw,
            "Crazyhouse raw identity incorrectly includes rule50");
}

void verify_exact_pocket_count_domain() {
    constexpr std::array<char, Crazyhouse::POCKET_TYPE_NB> white = {'P', 'N', 'B', 'R', 'Q'};
    constexpr std::array<char, Crazyhouse::POCKET_TYPE_NB> black = {'p', 'n', 'b', 'r', 'q'};
    std::set<Key> nonzeroComponentKeys;

    for (Color color : {WHITE, BLACK})
        for (usize i = 0; i < Crazyhouse::POCKET_TYPE_NB; ++i)
        {
            const PieceType type = Crazyhouse::PocketPieceTypes[i];
            Key             previous = 0;
            for (int count = 1; count <= Crazyhouse::max_pocket_count(type); ++count)
            {
                const auto identity = snapshot(
                  pocket_fen(color == WHITE ? white[i] : black[i], count), Ruleset::CRAZYHOUSE);
                require(identity.pocket != 0, "nonempty pocket component is zero");
                require(identity.pocket != previous, "adjacent pocket counts collide");
                require(nonzeroComponentKeys.insert(identity.pocket).second,
                        "color/type/count pocket component collision");
                previous = identity.pocket;
            }
        }

    require(nonzeroComponentKeys.size() == 60,
            "did not cover all 60 admitted nonzero color/type/count states");
}

void verify_promoted_square_identity_and_determinism() {
    constexpr std::string_view a2 = "7k/8/8/8/8/8/Q~7/K7[] w - - 0 1";
    constexpr std::string_view b3 = "7k/8/8/8/8/1Q~6/8/K7[] w - - 0 1";
    const auto a2First = snapshot(a2, Ruleset::CRAZYHOUSE);
    const auto a2Second = snapshot(a2, Ruleset::CRAZYHOUSE);
    const auto b3Identity = snapshot(b3, Ruleset::CRAZYHOUSE);

    require(a2First.raw == a2Second.raw && a2First.promoted == a2Second.promoted,
            "repeated setup is nondeterministic");
    require(a2First.promoted != 0 && b3Identity.promoted != 0,
            "promoted square component is zero");
    require(a2First.promoted != b3Identity.promoted,
            "distinct promoted squares share a component key");
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();

    verify_chess_identity_is_unsalted();
    verify_ruleset_and_component_formula();
    verify_exact_pocket_count_domain();
    verify_promoted_square_identity_and_determinism();

    std::cout << "PASS crazyhouse_zobrist ruleset_salt=PASS pocket_states=60 "
                 "promoted_squares=PASS formula=PASS chess_unchanged=PASS raw_key=PASS\n";
    return EXIT_SUCCESS;
}
