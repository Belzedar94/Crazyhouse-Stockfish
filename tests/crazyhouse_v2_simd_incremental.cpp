/*
  Test-only fixture for Crazyhouse V2 SIMD parity and transactional incremental
  state. Cases arrive as a frozen tab-separated stream produced from the JSON
  preregistration.
*/

#include <array>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "nnue/crazyhouse_v2_features.h"
#include "nnue/crazyhouse_v2_probe.h"
#include "position.h"
#include "uci.h"

namespace {

using namespace Stockfish;
using namespace Eval::NNUE::CrazyhouseV2;

using Features    = ScalarFeatureInventoryV1::Result;
using Network     = ScalarProbeNetworkV1;
using Accumulator = ScalarProbeAccumulatorV1;
using Evaluation  = ScalarProbeEvaluationResult;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_simd_incremental: " << message << '\n';
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

std::vector<Byte> read_bytes(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    require(stream.good(), "cannot open network artifact");
    const auto size = stream.tellg();
    require(size >= 0, "cannot determine network artifact size");
    std::vector<Byte> bytes(static_cast<std::size_t>(size));
    stream.seekg(0);
    stream.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    require(stream.good(), "cannot read complete network artifact");
    return bytes;
}

void mix(std::uint64_t& digest, std::uint64_t value) {
    digest ^= value + 0x9E3779B97F4A7C15ULL + (digest << 6) + (digest >> 2);
}

void mix_text(std::uint64_t& digest, std::string_view text) {
    for (const unsigned char value : text)
        mix(digest, value);
}

void require_same_lanes(const Evaluation&  expected,
                        const Evaluation&  actual,
                        const std::string& label) {
    require(expected.error == actual.error,
            label + " status mismatch: expected "
              + std::string(scalar_probe_evaluate_error_name(expected.error)) + ", got "
              + std::string(scalar_probe_evaluate_error_name(actual.error)));
    require(expected.lanes == actual.lanes, label + " lane mismatch");
}

struct Totals {
    std::uint64_t cases                   = 0;
    std::uint64_t moves                   = 0;
    std::uint64_t undos                   = 0;
    std::uint64_t nullMoves               = 0;
    std::uint64_t nullUndos               = 0;
    std::uint64_t checkpoints             = 0;
    std::uint64_t perspectiveCheckpoints  = 0;
    std::uint64_t simdTransitionLanes     = 0;
    std::uint64_t incrementalLanes        = 0;
    std::uint64_t singleRowSimdLanes      = 0;
    std::uint64_t biasSimdLanes           = 0;
    std::uint64_t multirowSimdLanes       = 0;
    std::uint64_t featureNegativeControls = 0;
    std::uint64_t accumulatorNegatives    = 0;
    std::uint64_t digest                  = 0x4348563253494D44ULL;
};

void compare_scalar_simd(const Network&     network,
                         const Features&    features,
                         Color              perspective,
                         const std::string& label,
                         std::uint64_t&     laneCounter,
                         std::uint64_t&     digest) {
    const Evaluation scalar = network.evaluate(features, perspective);
    const Evaluation simd   = network.evaluate_simd(features, perspective);
    require(scalar.ok(), label + " scalar rejected: "
                           + std::string(scalar_probe_evaluate_error_name(scalar.error)));
    require_same_lanes(scalar, simd, label + " scalar/SIMD");
    laneCounter += ScalarProbeOutputLanes;
    for (const std::int32_t lane : scalar.lanes)
        mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(lane)));
}

void run_simd_matrix(const Network& network, Totals& totals) {
    require(scalar_probe_simd_backend() == ScalarProbeSimdBackend::SSE2_X16_SCALAR_TAIL1,
            "required SSE2 backend is unavailable");

    Features features;
    features.status = ScalarFeatureInventoryV1::Status::SUCCESS;
    for (Color perspective : {WHITE, BLACK})
        compare_scalar_simd(network, features, perspective, "bias-only", totals.biasSimdLanes,
                            totals.digest);

    for (std::size_t row = 0; row < ScalarFeatureInventoryV1::Dimensions; ++row)
    {
        features        = {};
        features.status = ScalarFeatureInventoryV1::Status::SUCCESS;
        for (unsigned side = 0; side < COLOR_NB; ++side)
        {
            features.size[side]      = 1;
            features.active[side][0] = static_cast<ScalarFeatureInventoryV1::Index>(row);
        }
        for (Color perspective : {WHITE, BLACK})
            compare_scalar_simd(network, features, perspective, "single-row-" + std::to_string(row),
                                totals.singleRowSimdLanes, totals.digest);
    }

    for (const std::size_t active :
         {std::size_t{16}, std::size_t{17}, ScalarFeatureInventoryV1::MaximumActive})
    {
        features        = {};
        features.status = ScalarFeatureInventoryV1::Status::SUCCESS;
        for (unsigned side = 0; side < COLOR_NB; ++side)
        {
            features.size[side] = active;
            for (std::size_t index = 0; index < active; ++index)
                features.active[side][index] = static_cast<ScalarFeatureInventoryV1::Index>(
                  (index * 37 + side * 11) % ScalarFeatureInventoryV1::Dimensions);
        }
        for (Color perspective : {WHITE, BLACK})
            compare_scalar_simd(network, features, perspective,
                                "multirow-" + std::to_string(active), totals.multirowSimdLanes,
                                totals.digest);
    }
}

void require_matching_errors(const Network&           network,
                             const Features&          features,
                             Color                    perspective,
                             ScalarProbeEvaluateError expected,
                             const std::string&       label,
                             Totals&                  totals) {
    const Evaluation scalar = network.evaluate(features, perspective);
    const Evaluation simd   = network.evaluate_simd(features, perspective);
    require(scalar.error == expected,
            label + " scalar error mismatch: got "
              + std::string(scalar_probe_evaluate_error_name(scalar.error)));
    require(simd.error == expected, label + " SIMD error mismatch: got "
                                      + std::string(scalar_probe_evaluate_error_name(simd.error)));
    require(scalar.lanes == Evaluation{}.lanes && simd.lanes == Evaluation{}.lanes,
            label + " exposed partial lanes");
    ++totals.featureNegativeControls;
}

void run_feature_negatives(const Network& network, Totals& totals) {
    Features valid;
    valid.status = ScalarFeatureInventoryV1::Status::SUCCESS;

    Network empty;
    require_matching_errors(empty, valid, WHITE, ScalarProbeEvaluateError::NETWORK_NOT_READY,
                            "not-ready", totals);

    Features invalidStatus = valid;
    invalidStatus.status   = ScalarFeatureInventoryV1::Status::PROMOTED_MASK;
    require_matching_errors(network, invalidStatus, WHITE, ScalarProbeEvaluateError::FEATURE_STATUS,
                            "invalid-status", totals);

    require_matching_errors(network, valid, Color(COLOR_NB), ScalarProbeEvaluateError::PERSPECTIVE,
                            "invalid-perspective", totals);

    Features overflow    = valid;
    overflow.size[WHITE] = ScalarFeatureInventoryV1::MaximumActive + 1;
    require_matching_errors(network, overflow, WHITE, ScalarProbeEvaluateError::ACTIVE_OVERFLOW,
                            "active-overflow", totals);

    Features badIndex         = valid;
    badIndex.size[WHITE]      = 1;
    badIndex.active[WHITE][0] = ScalarFeatureInventoryV1::Dimensions;
    require_matching_errors(network, badIndex, WHITE, ScalarProbeEvaluateError::FEATURE_INDEX,
                            "feature-index", totals);

    Features duplicate         = valid;
    duplicate.size[WHITE]      = 2;
    duplicate.active[WHITE][0] = 7;
    duplicate.active[WHITE][1] = 7;
    require_matching_errors(network, duplicate, WHITE, ScalarProbeEvaluateError::DUPLICATE_FEATURE,
                            "duplicate", totals);
}

void set_position(Position& position, const std::string& fen, StateInfo& state) {
    const auto error = position.set(fen, false, Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(),
            "position setup rejected: " + fen + (error ? " :: " + std::string(error->what()) : ""));
}

Move parse_move(const Position& position, const std::string& token) {
    const Move move = UCIEngine::to_move(position, token);
    require(move != Move::none(), "illegal move token: " + token + " in " + position.fen());
    return move;
}

void compare_position(const Network&     network,
                      const Accumulator& accumulator,
                      const Position&    position,
                      const Features&    features,
                      const std::string& label,
                      Totals&            totals) {
    require(features.ok(), label + " feature extraction failed: "
                             + std::string(ScalarFeatureInventoryV1::status_name(features.status)));
    require(accumulator.matches(features), label + " accumulator membership mismatch");
    mix_text(totals.digest, position.fen());
    for (Color perspective : {WHITE, BLACK})
    {
        const Evaluation scalar = network.evaluate(features, perspective);
        const Evaluation simd   = network.evaluate_simd(features, perspective);
        const Evaluation delta  = accumulator.evaluate(perspective);
        require(scalar.ok(), label + " scalar full refresh rejected");
        require_same_lanes(scalar, simd, label + " SIMD full refresh");
        require_same_lanes(scalar, delta, label + " incremental");
        totals.simdTransitionLanes += ScalarProbeOutputLanes;
        totals.incrementalLanes += ScalarProbeOutputLanes;
        ++totals.perspectiveCheckpoints;
        for (const std::int32_t lane : scalar.lanes)
            mix(totals.digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(lane)));
    }
    ++totals.checkpoints;
}

std::array<Evaluation, COLOR_NB> snapshot(const Accumulator& accumulator) {
    return {accumulator.evaluate(WHITE), accumulator.evaluate(BLACK)};
}

void require_unchanged(const Accumulator&                      accumulator,
                       const Features&                         source,
                       const std::array<Evaluation, COLOR_NB>& before,
                       const std::string&                      label) {
    require(accumulator.matches(source), label + " changed committed membership");
    require_same_lanes(before[WHITE], accumulator.evaluate(WHITE), label + " white state");
    require_same_lanes(before[BLACK], accumulator.evaluate(BLACK), label + " black state");
}

Features different_valid_inventory(const Features& source) {
    Features different = source;
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        require(different.size[side] != 0, "cannot construct stale source from empty inventory");
        std::array<bool, ScalarFeatureInventoryV1::Dimensions> used{};
        for (std::size_t index = 0; index < different.size[side]; ++index)
            used[different.active[side][index]] = true;
        std::size_t replacement = 0;
        while (replacement < used.size() && used[replacement])
            ++replacement;
        require(replacement < used.size(), "cannot find stale-source replacement row");
        different.active[side][0] = static_cast<ScalarFeatureInventoryV1::Index>(replacement);
    }
    return different;
}

void run_accumulator_negatives(const Network&  network,
                               const Network&  otherNetwork,
                               const Features& source,
                               Accumulator&    accumulator,
                               Totals&         totals) {
    const auto before = snapshot(accumulator);
    auto expect = [&](ScalarProbeAccumulatorResult result, ScalarProbeAccumulatorError expected,
                      const std::string& label) {
        require(result.error == expected,
                label + " error mismatch: got "
                  + std::string(scalar_probe_accumulator_error_name(result.error)));
        require_unchanged(accumulator, source, before, label);
        ++totals.accumulatorNegatives;
    };

    Accumulator unready;
    const auto  unreadyResult = unready.update(network, source, source);
    require(unreadyResult.error == ScalarProbeAccumulatorError::SOURCE_NOT_READY,
            "unready accumulator did not reject update");
    ++totals.accumulatorNegatives;

    expect(accumulator.update(otherNetwork, source, source),
           ScalarProbeAccumulatorError::NETWORK_MISMATCH, "network-mismatch");

    const Features stale = different_valid_inventory(source);
    expect(accumulator.update(network, stale, source),
           ScalarProbeAccumulatorError::SOURCE_INVENTORY_MISMATCH, "stale-source");

    Features invalidTarget = source;
    invalidTarget.status   = ScalarFeatureInventoryV1::Status::PROMOTED_MASK;
    expect(accumulator.update(network, source, invalidTarget),
           ScalarProbeAccumulatorError::FEATURE_STATUS, "invalid-target");

    Network empty;
    expect(accumulator.update(empty, source, source),
           ScalarProbeAccumulatorError::NETWORK_NOT_READY, "not-ready-network");
}

void require_update(Accumulator&       accumulator,
                    const Network&     network,
                    const Features&    source,
                    const Features&    target,
                    const std::string& label) {
    const ScalarProbeAccumulatorResult result = accumulator.update(network, source, target);
    require(result.ok(), label + " update rejected: "
                           + std::string(scalar_probe_accumulator_error_name(result.error)));
}

void execute_case(const Network&                  network,
                  const Network&                  otherNetwork,
                  const std::vector<std::string>& fields,
                  bool&                           ranAccumulatorNegatives,
                  Totals&                         totals) {
    require(fields.size() == 5, "fixture line does not have five tab-separated fields");
    const std::string& id          = fields[0];
    const std::string& mode        = fields[1];
    const std::string& fen         = fields[2];
    const std::string& moveText    = fields[3];
    const std::string& expectedFen = fields[4];
    require(!id.empty(), "fixture id is empty");

    Position              position(Ruleset::CRAZYHOUSE);
    std::deque<StateInfo> states;
    states.emplace_back();
    set_position(position, fen, states.back());
    const std::string normalizedRoot = position.fen();

    Features rootFeatures = ScalarFeatureInventoryV1::extract(position);
    require(rootFeatures.ok(), id + " root feature extraction failed");
    Accumulator accumulator;
    const auto  refresh = accumulator.refresh(network, rootFeatures);
    require(refresh.ok(), id + " refresh rejected: "
                            + std::string(scalar_probe_accumulator_error_name(refresh.error)));
    compare_position(network, accumulator, position, rootFeatures, id + " root", totals);

    if (!ranAccumulatorNegatives)
    {
        run_accumulator_negatives(network, otherNetwork, rootFeatures, accumulator, totals);
        ranAccumulatorNegatives = true;
    }

    if (mode == "walk")
    {
        std::vector<std::string> tokens;
        if (!moveText.empty())
            tokens = split(moveText, ' ');
        std::vector<Move>     moves;
        std::vector<Features> inventories{rootFeatures};
        for (const std::string& token : tokens)
        {
            const Move move = parse_move(position, token);
            states.emplace_back();
            position.do_move(move, states.back());
            const Features target = ScalarFeatureInventoryV1::extract(position);
            require_update(accumulator, network, inventories.back(), target,
                           id + " after " + token);
            inventories.push_back(target);
            moves.push_back(move);
            ++totals.moves;
            compare_position(network, accumulator, position, inventories.back(),
                             id + " after " + token, totals);
        }
        require(position.fen() == expectedFen,
                id + " final FEN mismatch: expected " + expectedFen + ", got " + position.fen());

        while (!moves.empty())
        {
            const Features source = inventories.back();
            inventories.pop_back();
            const Move move = moves.back();
            moves.pop_back();
            position.undo_move(move);
            states.pop_back();
            const Features target = ScalarFeatureInventoryV1::extract(position);
            require_update(accumulator, network, source, target, id + " undo");
            require(target.active == inventories.back().active
                      && target.size == inventories.back().size,
                    id + " undo feature inventory differs from saved source");
            ++totals.undos;
            compare_position(network, accumulator, position, target, id + " undo", totals);
        }
        require(position.fen() == normalizedRoot, id + " did not restore root FEN");
    }
    else if (mode == "null")
    {
        require(moveText.empty(), id + " null case unexpectedly has moves");
        StateInfo nullState{};
        position.do_null_move(nullState);
        const Features afterNull = ScalarFeatureInventoryV1::extract(position);
        require_update(accumulator, network, rootFeatures, afterNull, id + " null");
        ++totals.nullMoves;
        compare_position(network, accumulator, position, afterNull, id + " null", totals);

        position.undo_null_move();
        const Features afterUndo = ScalarFeatureInventoryV1::extract(position);
        require_update(accumulator, network, afterNull, afterUndo, id + " null undo");
        ++totals.nullUndos;
        compare_position(network, accumulator, position, afterUndo, id + " null undo", totals);
        require(position.fen() == expectedFen, id + " null round-trip FEN mismatch");
    }
    else
        fail(id + " has unknown mode " + mode);

    ++totals.cases;
}

void require_frozen_counts(const Totals& totals) {
    require(totals.cases == 13, "case count drifted");
    require(totals.moves == 17, "move count drifted");
    require(totals.undos == 17, "undo count drifted");
    require(totals.nullMoves == 1, "null-move count drifted");
    require(totals.nullUndos == 1, "null-undo count drifted");
    require(totals.checkpoints == 49, "position checkpoint count drifted");
    require(totals.perspectiveCheckpoints == 98, "perspective checkpoint count drifted");
    require(totals.simdTransitionLanes == 1666, "transition SIMD lane count drifted");
    require(totals.incrementalLanes == 1666, "incremental lane count drifted");
    require(totals.singleRowSimdLanes == 30668, "single-row SIMD lane count drifted");
    require(totals.biasSimdLanes == 34, "bias SIMD lane count drifted");
    require(totals.multirowSimdLanes == 102, "multi-row SIMD lane count drifted");
    require(totals.featureNegativeControls == 6, "feature negative count drifted");
    require(totals.accumulatorNegatives == 5, "accumulator negative count drifted");
}

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 2, "usage: crazyhouse_v2_simd_incremental <synthetic-container>");
    const std::vector<Byte> bytes  = read_bytes(argv[1]);
    const auto              loaded = load_scalar_probe_v1(bytes.data(), bytes.size());
    require(loaded.ok(), "primary container rejected: "
                           + std::string(scalar_probe_load_error_name(loaded.error)));
    const auto otherLoaded = load_scalar_probe_v1(bytes.data(), bytes.size());
    require(otherLoaded.ok(), "secondary container rejected");

    Attacks::init();
    Position::init();

    Totals totals;
    run_simd_matrix(loaded.network, totals);
    run_feature_negatives(loaded.network, totals);

    bool        ranAccumulatorNegatives = false;
    std::string line;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "fixture stream contains an empty line");
        execute_case(loaded.network, otherLoaded.network, split(line, '\t'),
                     ranAccumulatorNegatives, totals);
    }
    require(std::cin.eof(), "fixture stream read failed");
    require(ranAccumulatorNegatives, "accumulator negatives did not run");
    require_frozen_counts(totals);

    std::cout << "PASS crazyhouse_v2_simd_incremental"
              << " backend=sse2-x16-scalar-tail1"
              << " cases=" << totals.cases << " moves=" << totals.moves << " undos=" << totals.undos
              << " nulls=" << totals.nullMoves << " null_undos=" << totals.nullUndos
              << " checkpoints=" << totals.checkpoints
              << " perspective_checkpoints=" << totals.perspectiveCheckpoints
              << " simd_transition_lanes=" << totals.simdTransitionLanes
              << " incremental_lanes=" << totals.incrementalLanes
              << " single_row_simd_lanes=" << totals.singleRowSimdLanes
              << " bias_simd_lanes=" << totals.biasSimdLanes
              << " multirow_simd_lanes=" << totals.multirowSimdLanes
              << " feature_negatives=" << totals.featureNegativeControls
              << " accumulator_negatives=" << totals.accumulatorNegatives << " digest=" << std::hex
              << totals.digest << std::dec << " training_admissible=false g12_closed=false\n";
    return EXIT_SUCCESS;
}
