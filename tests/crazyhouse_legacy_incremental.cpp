/*
  Fixture executable for transactional Crazyhouse legacy V1 incremental state.
  Cases arrive as a frozen tab-separated stream produced from the JSON contract.
*/

#include <array>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "nnue/crazyhouse_legacy_network.h"
#include "position.h"
#include "uci.h"

namespace {

using namespace Stockfish;
using Network = Eval::NNUE::LegacyCrazyhouseNetworkV1;
using Stack   = Eval::NNUE::LegacyCrazyhouseAccumulatorStackV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_legacy_incremental: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

std::vector<std::string> split(std::string_view text, char delimiter) {
    std::vector<std::string> fields;
    std::size_t              begin = 0;
    while (true)
    {
        const std::size_t end = text.find(delimiter, begin);
        fields.emplace_back(text.substr(begin, end == std::string_view::npos ? end : end - begin));
        if (end == std::string_view::npos)
            return fields;
        begin = end + 1;
    }
}

std::uint64_t parse_u64(const std::string& text, const std::string& label) {
    require(!text.empty(), label + " is empty");
    std::size_t parsed = 0;
    const auto  value  = std::stoull(text, &parsed, 10);
    require(parsed == text.size(), label + " is not an unsigned integer");
    return value;
}

void set_position(Position& position, const std::string& fen, StateInfo& state) {
    const auto error = position.set(fen, false, Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), "position setup rejected: " + fen
                                  + (error ? " :: " + std::string(error->what()) : ""));
}

Move parse_move(const Position& position, const std::string& token) {
    const Move move = UCIEngine::to_move(position, token);
    require(move != Move::none(), "illegal move token: " + token + " in " + position.fen());
    return move;
}

bool same_raw(const Network::RawEvaluation& left, const Network::RawEvaluation& right) {
    if (left.selectedBucket != right.selectedBucket)
        return false;
    for (std::size_t bucket = 0; bucket < Network::LayerStacks; ++bucket)
        if (left.buckets[bucket].psqt != right.buckets[bucket].psqt
            || left.buckets[bucket].positional != right.buckets[bucket].positional)
            return false;
    return true;
}

bool same_adapter(const Network::LegacyAdapterOutput& left,
                  const Network::LegacyAdapterOutput& right) {
    return left.boardPawns == right.boardPawns
        && left.whiteNonPawnMaterial == right.whiteNonPawnMaterial
        && left.blackNonPawnMaterial == right.blackNonPawnMaterial
        && left.entertainmentApplied == right.entertainmentApplied && left.scale == right.scale
        && left.unadjusted == right.unadjusted && left.adjusted == right.adjusted
        && left.outerPreClamp == right.outerPreClamp && left.outer == right.outer
        && left.clamped == right.clamped;
}

void mix(std::uint64_t& digest, std::uint64_t value) {
    digest ^= value + 0x9E3779B97F4A7C15ULL + (digest << 6) + (digest >> 2);
}

void mix_text(std::uint64_t& digest, std::string_view text) {
    for (const unsigned char value : text)
        mix(digest, value);
}

void require_parity(const Network& network,
                    Stack&         stack,
                    const Position& position,
                    std::uint64_t& digest,
                    const std::string& label) {
    const Network::LegacyEvalResult full = network.evaluate_legacy(position);
    const Network::LegacyEvalResult incremental =
      network.evaluate_legacy_incremental(position, stack);
    Stack searchStack;
    searchStack.reset();
    const Network::LegacyEvalResult search =
      network.evaluate_legacy_search_incremental(position, searchStack);
    require(full.ok(), label + " full refresh failed: " + full.message);
    require(incremental.ok(), label + " incremental evaluation failed: " + incremental.message);
    require(search.ok(), label + " selected-bucket search evaluation failed: " + search.message);
    require(same_raw(full.output->raw, incremental.output->raw), label + " raw output mismatch");
    require(same_adapter(full.output->adapter, incremental.output->adapter),
            label + " legacy adapter mismatch");
    require(full.output->raw.selectedBucket == search.output->raw.selectedBucket
              && full.output->raw.selected().psqt == search.output->raw.selected().psqt
              && full.output->raw.selected().positional == search.output->raw.selected().positional,
            label + " selected-bucket search raw output mismatch");
    require(same_adapter(full.output->adapter, search.output->adapter),
            label + " selected-bucket search adapter mismatch");

    mix_text(digest, position.fen());
    mix(digest, full.output->raw.selectedBucket);
    for (const auto& bucket : full.output->raw.buckets)
    {
        mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(bucket.psqt)));
        mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(bucket.positional)));
    }
    mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(full.output->adapter.outer)));
}

struct Totals {
    std::uint64_t cases       = 0;
    std::uint64_t transitions = 0;
    std::uint64_t undos       = 0;
    std::uint64_t nulls       = 0;
    std::uint64_t rejections  = 0;
    std::uint64_t digest      = 0x43485F494E435231ULL;
};

void require_counters(const Stack&         stack,
                      const std::vector<std::string>& fields,
                      const std::string&   id) {
    const auto& counters = stack.counters();
    require(counters.fullRefreshes == parse_u64(fields[5], id + " full refreshes"),
            id + " full-refresh counter mismatch");
    require(counters.deltaUpdates == parse_u64(fields[6], id + " delta updates"),
            id + " delta-update counter mismatch");
    require(counters.sameFrameReuses == parse_u64(fields[7], id + " same-frame reuses"),
            id + " same-frame-reuse counter mismatch");
    require(counters.kingPerspectiveRefreshes == parse_u64(fields[8], id + " king refreshes"),
            id + " king-perspective counter mismatch");
    require(counters.maxSourceDistance == parse_u64(fields[9], id + " source distance"),
            id + " maximum source-distance mismatch");
}

void execute_case(const Network& network, const std::vector<std::string>& fields, Totals& totals) {
    require(fields.size() == 10, "fixture line does not have ten tab-separated fields");
    const std::string& id          = fields[0];
    const std::string& mode        = fields[1];
    const std::string& fen         = fields[2];
    const std::string& moveText    = fields[3];
    const std::string& expectedFen = fields[4];
    require(!id.empty(), "fixture id is empty");

    std::vector<std::string> tokens;
    if (!moveText.empty())
        tokens = split(moveText, ' ');

    Position              position(Ruleset::CRAZYHOUSE);
    std::deque<StateInfo> states;
    states.emplace_back();
    set_position(position, fen, states.back());

    Stack stack;
    stack.reset();
    require_parity(network, stack, position, totals.digest, id + " root");

    std::vector<Move> moves;
    if (mode == "walk" || mode == "lazy")
    {
        for (const std::string& token : tokens)
        {
            const Move move = parse_move(position, token);
            states.emplace_back();
            position.do_move(move, states.back());
            require(stack.push(), id + " stack overflow on push");
            moves.push_back(move);
            ++totals.transitions;
            if (mode == "walk")
                require_parity(network, stack, position, totals.digest, id + " after " + token);
        }
        if (mode == "lazy")
            require_parity(network, stack, position, totals.digest, id + " lazy leaf");

        require(position.fen() == expectedFen,
                id + " final FEN mismatch: expected " + expectedFen + " got " + position.fen());

        while (!moves.empty())
        {
            position.undo_move(moves.back());
            moves.pop_back();
            require(stack.pop(), id + " stack underflow on undo");
            require_parity(network, stack, position, totals.digest, id + " undo");
            ++totals.undos;
        }
        require(!stack.pop(), id + " root stack pop did not fail closed");
    }
    else if (mode == "null")
    {
        require(tokens.empty(), id + " null case unexpectedly has moves");
        StateInfo nullState{};
        position.do_null_move(nullState);
        require_parity(network, stack, position, totals.digest, id + " null");
        position.undo_null_move();
        require_parity(network, stack, position, totals.digest, id + " null undo");
        require(position.fen() == expectedFen, id + " null round-trip FEN mismatch");
        ++totals.nulls;
    }
    else if (mode == "unsynchronized")
    {
        require(tokens.size() == 1, id + " unsynchronized case requires one move");
        const Move move = parse_move(position, tokens.front());
        states.emplace_back();
        position.do_move(move, states.back());
        require(position.fen() == expectedFen, id + " unsynchronized final FEN mismatch");
        const Network::LegacyEvalResult rejected =
          network.evaluate_legacy_incremental(position, stack);
        require(rejected.status == Network::EvalStatus::ContractViolation
                  && !rejected.output.has_value(),
                id + " same-frame physical mutation was not rejected");
        position.undo_move(move);
        require_parity(network, stack, position, totals.digest, id + " rejected rollback");
        ++totals.rejections;
    }
    else
        fail(id + " has unknown mode " + mode);

    require_counters(stack, fields, id);
    ++totals.cases;
}

void verify_boundaries(const Network& network, const std::filesystem::path& artifact, Totals& totals) {
    constexpr std::array<std::string_view, Network::LayerStacks> BucketFens = {
      "1n2k3/8/8/8/8/8/8/1N2K3[] w - - 0 1",
      "1n2k3/8/8/8/8/8/P7/1N2K3[] w - - 0 1",
      "1n2k3/pp6/8/8/8/8/PPP5/1N2K3[] w - - 0 1",
      "1n2k3/pppp4/8/8/8/8/PPPPP3/1N2K3[] w - - 0 1",
      "1n2k3/pppppp2/8/8/8/8/PPPPPPP1/1N2K3[] w - - 0 1",
      "1n2k3/pppppppp/8/8/8/8/PPPPPPPP/RN2K3[] w - - 0 1",
      "rn2k2r/pppppppp/8/8/8/8/PPPPPPPP/RNB1K2R[] w - - 0 1",
      "rnb1kb1r/pppppppp/8/8/8/8/PPPPPPPP/RNBQKB1R[] w - - 0 1",
    };
    // The immutable corpus digest predates this selected-bucket coverage. Keep
    // the additive probes in a separate trace domain so they cannot rewrite
    // the frozen scalar/SIMD protocol identity.
    std::uint64_t bucketDigest = 0x43485F4255434B54ULL;
    for (std::size_t bucket = 0; bucket < BucketFens.size(); ++bucket)
    {
        StateInfo bucketState{};
        Position  bucketPosition(Ruleset::CRAZYHOUSE);
        set_position(bucketPosition, std::string(BucketFens[bucket]), bucketState);
        Stack bucketStack;
        bucketStack.reset();
        require_parity(network, bucketStack, bucketPosition, bucketDigest,
                       "selected search bucket " + std::to_string(bucket));
    }

    StateInfo state{};
    Position  position(Ruleset::CRAZYHOUSE);
    set_position(position, "7k/8/8/8/8/8/P7/K7[] w - - 0 1", state);

    Stack bound;
    bound.reset();
    require_parity(network, bound, position, totals.digest, "network-boundary root");

    Network other;
    const auto loaded = other.load_file(artifact);
    require(loaded.status == Network::LoadStatus::Success, "second network failed to load");
    const auto mismatch = other.evaluate_legacy_incremental(position, bound);
    require(mismatch.status == Network::EvalStatus::ContractViolation
              && !mismatch.output.has_value(),
            "different network object was accepted by a bound stack");
    ++totals.rejections;

    StateInfo chessState{};
    Position  chess(Ruleset::CHESS);
    const auto error = chess.set(
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false,
      Ruleset::CHESS, &chessState);
    require(!error.has_value(), "standard control position failed to load");
    Stack wrongRuleset;
    wrongRuleset.reset();
    const auto rejected = network.evaluate_legacy_incremental(chess, wrongRuleset);
    require(rejected.status == Network::EvalStatus::FeatureRejected
              && rejected.featureStatus
                   == Eval::NNUE::LegacyCrazyhouseFeaturesV1::Status::WrongRuleset
              && !rejected.output.has_value(),
            "standard Chess was accepted by the Crazyhouse incremental backend");
    ++totals.rejections;
}

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 2, "usage: crazyhouse_legacy_incremental <registered-network>");
    const std::filesystem::path artifact(argv[1]);

    Attacks::init();
    Position::init();

    Network network;
    const auto loaded = network.load_file(artifact);
    require(loaded.status == Network::LoadStatus::Success && network.loaded(),
            "registered network failed to load: " + loaded.message);

    Totals      totals;
    std::string line;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "fixture stream contains an empty line");
        execute_case(network, split(line, '\t'), totals);
    }
    require(std::cin.eof(), "fixture stream read failed");
    require(totals.cases > 0, "fixture stream contains no cases");
    verify_boundaries(network, artifact, totals);

    std::cout << "PASS crazyhouse_legacy_incremental cases=" << totals.cases
              << " transitions=" << totals.transitions << " undos=" << totals.undos
              << " nulls=" << totals.nulls << " rejections=" << totals.rejections
              << " trace_digest=" << std::hex << totals.digest << std::dec << '\n';
    return EXIT_SUCCESS;
}
