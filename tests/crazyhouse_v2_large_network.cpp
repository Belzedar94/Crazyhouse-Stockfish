/*
  Dedicated engineering verifier for CH-NNUE-V2-LARGE-K64G1-SFNNV16.
  This verifier executable is not reachable through the normal-engine UCI surface.
  The production runtime under test is linked into the normal engine as an opt-in route.
*/

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "attacks.h"
#include "nnue/crazyhouse_v2_large_network.h"
#include "nnue/crazyhouse_v2_large_runtime.h"
#include "position.h"
#include "uci.h"

namespace {

using namespace Stockfish;
using namespace Stockfish::Eval::NNUE::CrazyhouseV2;
using Inventory   = LargeFeatureInventoryV1;
using Network     = LargeNetworkV1;
using Accumulator = LargeNetworkAccumulatorV1;
using Evaluation  = LargeNetworkEvaluationResultV1;

constexpr std::size_t TraceValuesPerEvaluation =
  1 + COLOR_NB * (LargeKTransformerLanes + LargeGTransformerLanes + LargePerspectiveOutputBytes)
  + LargeDenseInputBytes + LargeFc0Outputs * 3 + LargeFc1Outputs * 3 + 3;
static_assert(TraceValuesPerEvaluation == 4292);

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_large_network: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

std::vector<Byte> read_file(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    require(input.good(), "cannot open network file");
    std::vector<Byte> output((std::istreambuf_iterator<char>(input)),
                             std::istreambuf_iterator<char>());
    require(!input.bad(), "cannot read network file");
    return output;
}

Digest repeated_digest(Byte value) {
    Digest output{};
    output.fill(value);
    return output;
}

LargeExpectedProvenanceV1 expected_provenance() {
    return {repeated_digest(0x11), repeated_digest(0x22), repeated_digest(0x33),
            repeated_digest(0x44), repeated_digest(0x55), repeated_digest(0x66)};
}

template<typename Value, std::size_t Size>
std::string join(const std::array<Value, Size>& values) {
    std::ostringstream output;
    for (std::size_t index = 0; index < Size; ++index)
    {
        if (index)
            output << ',';
        if constexpr (std::is_same_v<Value, Byte> || std::is_same_v<Value, std::int8_t>)
            output << int(values[index]);
        else
            output << values[index];
    }
    return output.str();
}

void emit_trace(const std::string& id, Color sideToMove, const LargeNetworkTraceV1& trace) {
    std::cout << "TRACE\t" << id << '\t' << (sideToMove == WHITE ? "white" : "black") << '\t'
              << trace.bucket;
    for (Color perspective : {WHITE, BLACK})
        std::cout << '\t' << join(trace.kAccumulator[perspective]) << '\t'
                  << join(trace.gAccumulator[perspective]) << '\t'
                  << join(trace.perspectiveOutput[perspective]);
    std::cout << '\t' << join(trace.denseInput) << '\t' << join(trace.fc0) << '\t'
              << join(trace.fc0Squared) << '\t' << join(trace.fc0Clipped) << '\t' << join(trace.fc1)
              << '\t' << join(trace.fc1Squared) << '\t' << join(trace.fc1Clipped) << '\t'
              << trace.fc2 << '\t' << trace.fwdRaw << '\t' << trace.outputValue << '\n';
}

void require_error(const LargeNetworkV1&     network,
                   const Inventory::Result&  features,
                   Color                     sideToMove,
                   LargeNetworkEvaluateError expected,
                   const std::string&        label) {
    const auto evaluated = network.evaluate(features, sideToMove);
    require(evaluated.error == expected,
            label + " expected " + std::string(large_network_evaluate_error_name(expected))
              + " observed " + std::string(large_network_evaluate_error_name(evaluated.error)));
    require(evaluated.trace.bucket == 0 && evaluated.trace.outputValue == 0,
            label + " exposed a partial failure trace");
}

Inventory::Result empty_features() {
    Inventory::Result features;
    features.status = Inventory::Status::SUCCESS;
    return features;
}

Inventory::Result one_row_features() {
    Inventory::Result features                = empty_features();
    features.perspective[WHITE].k64.active[0] = 7;
    features.perspective[WHITE].k64.size      = 1;
    features.perspective[WHITE].g1.active[0]  = 9;
    features.perspective[WHITE].g1.size       = 1;
    features.perspective[BLACK].k64.active[0] = 13;
    features.perspective[BLACK].k64.size      = 1;
    features.perspective[BLACK].g1.active[0]  = 17;
    features.perspective[BLACK].g1.size       = 1;
    return features;
}

Inventory::Result bucket_seven_features() {
    Inventory::Result features = empty_features();
    features.totalPocketUnits  = 28;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
        for (std::size_t index = 0; index < features.totalPocketUnits; ++index)
        {
            features.perspective[perspective].k64.active[index] =
              static_cast<Inventory::Index>(Inventory::KPocketOffset + perspective * 64 + index);
            features.perspective[perspective].g1.active[index] =
              static_cast<Inventory::Index>(Inventory::GPocketOffset + index);
        }
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        features.perspective[perspective].k64.size = features.totalPocketUnits;
        features.perspective[perspective].g1.size  = features.totalPocketUnits;
    }
    return features;
}

bool same_trace(const LargeNetworkTraceV1& left, const LargeNetworkTraceV1& right) {
    return left.bucket == right.bucket && left.kAccumulator == right.kAccumulator
        && left.gAccumulator == right.gAccumulator
        && left.perspectiveOutput == right.perspectiveOutput && left.denseInput == right.denseInput
        && left.fc0 == right.fc0 && left.fc0Squared == right.fc0Squared
        && left.fc0Clipped == right.fc0Clipped && left.fc1 == right.fc1
        && left.fc1Squared == right.fc1Squared && left.fc1Clipped == right.fc1Clipped
        && left.fc2 == right.fc2 && left.fwdRaw == right.fwdRaw
        && left.outputValue == right.outputValue;
}

void require_same_evaluation(const Evaluation&  expected,
                             const Evaluation&  actual,
                             const std::string& label) {
    require(expected.error == actual.error,
            label + " status mismatch: expected "
              + std::string(large_network_evaluate_error_name(expected.error)) + ", got "
              + std::string(large_network_evaluate_error_name(actual.error)));
    require(same_trace(expected.trace, actual.trace), label + " trace mismatch");
}

void require_failure(const Evaluation&         actual,
                     LargeNetworkEvaluateError expected,
                     const std::string&        label) {
    require(actual.error == expected,
            label + " expected " + std::string(large_network_evaluate_error_name(expected))
              + ", got " + std::string(large_network_evaluate_error_name(actual.error)));
    require(same_trace(actual.trace, LargeNetworkTraceV1{}), label + " exposed a partial trace");
}

template<typename Value, std::size_t Size>
void mix_array(std::uint64_t& digest, const std::array<Value, Size>& values) {
    for (const Value value : values)
        digest ^= static_cast<std::uint64_t>(static_cast<std::int64_t>(value))
                + 0x9E3779B97F4A7C15ULL + (digest << 6) + (digest >> 2);
}

void mix(std::uint64_t& digest, std::uint64_t value) {
    digest ^= value + 0x9E3779B97F4A7C15ULL + (digest << 6) + (digest >> 2);
}

void mix_text(std::uint64_t& digest, std::string_view text) {
    for (const unsigned char value : text)
        mix(digest, value);
}

void mix_trace(std::uint64_t& digest, const LargeNetworkTraceV1& trace) {
    mix(digest, trace.bucket);
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        mix_array(digest, trace.kAccumulator[perspective]);
        mix_array(digest, trace.gAccumulator[perspective]);
        mix_array(digest, trace.perspectiveOutput[perspective]);
    }
    mix_array(digest, trace.denseInput);
    mix_array(digest, trace.fc0);
    mix_array(digest, trace.fc0Squared);
    mix_array(digest, trace.fc0Clipped);
    mix_array(digest, trace.fc1);
    mix_array(digest, trace.fc1Squared);
    mix_array(digest, trace.fc1Clipped);
    mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(trace.fc2)));
    mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(trace.fwdRaw)));
    mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(trace.outputValue)));
}

struct TransitionTotals {
    std::uint64_t cases                  = 0;
    std::uint64_t moves                  = 0;
    std::uint64_t undos                  = 0;
    std::uint64_t nullMoves              = 0;
    std::uint64_t nullUndos              = 0;
    std::uint64_t refreshes              = 0;
    std::uint64_t updates                = 0;
    std::uint64_t checkpoints            = 0;
    std::uint64_t sideToMoveEvaluations  = 0;
    std::uint64_t simdTraceValues        = 0;
    std::uint64_t incrementalTraceValues = 0;
    std::uint64_t operationNegatives     = 0;
    std::uint64_t evaluationNegatives    = 0;
    std::uint64_t digest                 = 0x434856324C415247ULL;
};

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

bool same_inventory(const Inventory::Result& left, const Inventory::Result& right) {
    if (left.status != right.status || left.totalPocketUnits != right.totalPocketUnits)
        return false;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
        for (const auto& [leftDomain, rightDomain] :
             {std::pair{&left.perspective[perspective].k64, &right.perspective[perspective].k64},
              std::pair{&left.perspective[perspective].g1, &right.perspective[perspective].g1}})
        {
            if (leftDomain->size != rightDomain->size)
                return false;
            for (std::size_t index = 0; index < leftDomain->size; ++index)
                if (leftDomain->active[index] != rightDomain->active[index])
                    return false;
        }
    return true;
}

std::string simd_backend_name() {
    switch (large_network_simd_backend())
    {
    case LargeNetworkSimdBackend::UNAVAILABLE :
        return "unavailable";
    case LargeNetworkSimdBackend::SSE2_X8_INT16_TO_INT32 :
        return "sse2-x8-int16-to-int32";
    }
    return "unknown";
}

void compare_position(const Network&           network,
                      const Accumulator&       accumulator,
                      const Position&          position,
                      const Inventory::Result& features,
                      const std::string&       label,
                      TransitionTotals&        totals) {
    require(features.ok(), label + " feature extraction failed: "
                             + std::string(Inventory::status_name(features.status)));
    require(accumulator.bound_to(network), label + " accumulator network binding mismatch");
    require(accumulator.matches(features), label + " accumulator inventory mismatch");
    mix_text(totals.digest, position.fen());
    for (Color sideToMove : {WHITE, BLACK})
    {
        const Evaluation scalar      = network.evaluate(features, sideToMove);
        const Evaluation simd        = network.evaluate_simd(features, sideToMove);
        const Evaluation incremental = accumulator.evaluate(features, sideToMove);
        require(scalar.ok(), label + " scalar full refresh rejected: "
                               + std::string(large_network_evaluate_error_name(scalar.error)));
        require_same_evaluation(scalar, simd, label + " scalar/SIMD");
        require_same_evaluation(scalar, incremental, label + " incremental/full");
        totals.simdTraceValues += TraceValuesPerEvaluation;
        totals.incrementalTraceValues += TraceValuesPerEvaluation;
        ++totals.sideToMoveEvaluations;
        mix_trace(totals.digest, scalar.trace);
    }
    ++totals.checkpoints;
}

std::array<Evaluation, COLOR_NB> snapshot(const Accumulator&       accumulator,
                                          const Inventory::Result& features) {
    return {accumulator.evaluate(features, WHITE), accumulator.evaluate(features, BLACK)};
}

void require_unchanged(const Accumulator&                      accumulator,
                       const Network&                          network,
                       const Inventory::Result&                source,
                       const std::array<Evaluation, COLOR_NB>& before,
                       const std::string&                      label) {
    require(accumulator.ready(), label + " cleared the committed accumulator");
    require(accumulator.bound_to(network), label + " changed the network binding");
    require(accumulator.matches(source), label + " changed committed membership");
    require_same_evaluation(before[WHITE], accumulator.evaluate(source, WHITE),
                            label + " white state");
    require_same_evaluation(before[BLACK], accumulator.evaluate(source, BLACK),
                            label + " black state");
}

Inventory::Result different_valid_inventory(const Inventory::Result& source) {
    Inventory::Result different         = source;
    auto              replace_board_row = [](auto& domain, std::size_t boardRows) {
        require(domain.size != 0, "cannot alter an empty stale-source domain");
        std::size_t activeIndex = 0;
        while (activeIndex < domain.size && domain.active[activeIndex] >= boardRows)
            ++activeIndex;
        require(activeIndex < domain.size, "stale-source domain has no board row");
        std::size_t replacement = 0;
        while (replacement < boardRows
               && std::find(domain.active.begin(),
                                         domain.active.begin() + static_cast<std::ptrdiff_t>(domain.size),
                                         replacement)
                    != domain.active.begin() + static_cast<std::ptrdiff_t>(domain.size))
            ++replacement;
        require(replacement < boardRows, "cannot find stale-source replacement row");
        domain.active[activeIndex] = static_cast<Inventory::Index>(replacement);
    };
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        replace_board_row(different.perspective[perspective].k64, Inventory::KBoardRows);
        replace_board_row(different.perspective[perspective].g1, Inventory::GBoardRows);
    }
    return different;
}

void run_accumulator_negatives(const Network&           network,
                               const Network&           otherNetwork,
                               const Inventory::Result& source,
                               Accumulator&             accumulator,
                               TransitionTotals&        totals) {
    const auto before = snapshot(accumulator, source);
    auto expect = [&](LargeNetworkAccumulatorResultV1 result, LargeNetworkAccumulatorError expected,
                      const std::string& label) {
        require(result.error == expected,
                label + " error mismatch: expected "
                  + std::string(large_network_accumulator_error_name(expected)) + ", got "
                  + std::string(large_network_accumulator_error_name(result.error)));
        require_unchanged(accumulator, network, source, before, label);
        ++totals.operationNegatives;
    };

    Accumulator unready;
    require(unready.update(network, source, source).error
              == LargeNetworkAccumulatorError::SOURCE_NOT_READY,
            "uninitialized update error mismatch");
    require(!unready.ready(), "uninitialized update committed state");
    ++totals.operationNegatives;

    expect(accumulator.update(otherNetwork, source, source),
           LargeNetworkAccumulatorError::NETWORK_MISMATCH, "network-mismatch");
    const Inventory::Result stale = different_valid_inventory(source);
    expect(accumulator.update(network, stale, source),
           LargeNetworkAccumulatorError::SOURCE_INVENTORY_MISMATCH, "stale-source");

    Inventory::Result invalidSource = source;
    invalidSource.status            = Inventory::Status::PROMOTED_MASK;
    expect(accumulator.update(network, invalidSource, source),
           LargeNetworkAccumulatorError::FEATURE_STATUS, "invalid-source");
    Inventory::Result invalidTarget = source;
    invalidTarget.status            = Inventory::Status::PROMOTED_MASK;
    expect(accumulator.update(network, source, invalidTarget),
           LargeNetworkAccumulatorError::FEATURE_STATUS, "invalid-target");
    Inventory::Result overflowTarget           = source;
    overflowTarget.perspective[WHITE].k64.size = Inventory::MaximumActivePerDomain + 1;
    expect(accumulator.update(network, source, overflowTarget),
           LargeNetworkAccumulatorError::ACTIVE_OVERFLOW, "target-overflow");
    Inventory::Result badIndexTarget               = source;
    badIndexTarget.perspective[WHITE].g1.size      = 1;
    badIndexTarget.perspective[WHITE].g1.active[0] = Inventory::GDimensions;
    expect(accumulator.update(network, source, badIndexTarget),
           LargeNetworkAccumulatorError::FEATURE_INDEX, "target-index");
    Inventory::Result duplicateTarget                = source;
    duplicateTarget.perspective[WHITE].k64.size      = 2;
    duplicateTarget.perspective[WHITE].k64.active[0] = 7;
    duplicateTarget.perspective[WHITE].k64.active[1] = 7;
    expect(accumulator.update(network, source, duplicateTarget),
           LargeNetworkAccumulatorError::DUPLICATE_FEATURE, "target-duplicate");
    Inventory::Result pocketOverflow = source;
    pocketOverflow.totalPocketUnits  = 31;
    expect(accumulator.update(network, source, pocketOverflow),
           LargeNetworkAccumulatorError::POCKET_UNITS, "target-pocket-overflow");
    Inventory::Result pocketMismatch = source;
    pocketMismatch.totalPocketUnits  = 1;
    expect(accumulator.update(network, source, pocketMismatch),
           LargeNetworkAccumulatorError::POCKET_ROUTING_MISMATCH, "target-pocket-mismatch");
    const std::unique_ptr<Network> emptyNetwork(new Network);
    expect(accumulator.update(*emptyNetwork, source, source),
           LargeNetworkAccumulatorError::NETWORK_NOT_READY, "not-ready-network");
    expect(accumulator.refresh(network, invalidTarget),
           LargeNetworkAccumulatorError::FEATURE_STATUS, "failed-refresh");

    Accumulator emptyAccumulator;
    require_failure(emptyAccumulator.evaluate(source, WHITE),
                    LargeNetworkEvaluateError::ACCUMULATOR_NOT_READY, "uninitialized-evaluation");
    ++totals.evaluationNegatives;
    require_failure(accumulator.evaluate(source, static_cast<Color>(COLOR_NB)),
                    LargeNetworkEvaluateError::SIDE_TO_MOVE, "invalid-side-to-move");
    require_unchanged(accumulator, network, source, before, "invalid-side-to-move");
    ++totals.evaluationNegatives;
    require_failure(accumulator.evaluate(stale, WHITE),
                    LargeNetworkEvaluateError::ACCUMULATOR_INVENTORY_MISMATCH, "stale-evaluation");
    require_unchanged(accumulator, network, source, before, "stale-evaluation");
    ++totals.evaluationNegatives;
    require_failure(accumulator.evaluate(invalidTarget, WHITE),
                    LargeNetworkEvaluateError::FEATURE_STATUS, "invalid-inventory-evaluation");
    require_unchanged(accumulator, network, source, before, "invalid-inventory-evaluation");
    ++totals.evaluationNegatives;
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

void require_update(Accumulator&             accumulator,
                    const Network&           network,
                    const Inventory::Result& source,
                    const Inventory::Result& target,
                    const std::string&       label) {
    const auto result = accumulator.update(network, source, target);
    require(result.ok(), label + " update rejected: "
                           + std::string(large_network_accumulator_error_name(result.error)));
}

void execute_transition_case(const Network&                  network,
                             const Network&                  otherNetwork,
                             const std::vector<std::string>& fields,
                             bool&                           ranAccumulatorNegatives,
                             TransitionTotals&               totals) {
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
    Inventory::Result rootFeatures   = Inventory::extract(position);
    require(rootFeatures.ok(), id + " root feature extraction failed");
    Accumulator accumulator;
    const auto  refresh = accumulator.refresh(network, rootFeatures);
    require(refresh.ok(), id + " refresh rejected: "
                            + std::string(large_network_accumulator_error_name(refresh.error)));
    ++totals.refreshes;
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
        std::vector<Move>              moves;
        std::vector<Inventory::Result> inventories{rootFeatures};
        for (const std::string& token : tokens)
        {
            const Move move = parse_move(position, token);
            states.emplace_back();
            position.do_move(move, states.back());
            const Inventory::Result target = Inventory::extract(position);
            require_update(accumulator, network, inventories.back(), target,
                           id + " after " + token);
            ++totals.updates;
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
            const Inventory::Result source = inventories.back();
            inventories.pop_back();
            const Move move = moves.back();
            moves.pop_back();
            position.undo_move(move);
            states.pop_back();
            const Inventory::Result target = Inventory::extract(position);
            require_update(accumulator, network, source, target, id + " undo");
            ++totals.updates;
            require(same_inventory(target, inventories.back()),
                    id + " undo inventory differs from saved source");
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
        const Inventory::Result afterNull = Inventory::extract(position);
        require(same_inventory(afterNull, rootFeatures), id + " null changed physical inventory");
        require_update(accumulator, network, rootFeatures, afterNull, id + " null");
        ++totals.updates;
        ++totals.nullMoves;
        compare_position(network, accumulator, position, afterNull, id + " null", totals);
        position.undo_null_move();
        const Inventory::Result afterUndo = Inventory::extract(position);
        require_update(accumulator, network, afterNull, afterUndo, id + " null undo");
        ++totals.updates;
        ++totals.nullUndos;
        compare_position(network, accumulator, position, afterUndo, id + " null undo", totals);
        require(position.fen() == expectedFen, id + " null round-trip FEN mismatch");
    }
    else
        fail(id + " has unknown mode " + mode);
    ++totals.cases;
}

void run_transition_suite(const Network& network, const Network& otherNetwork) {
    require(large_network_simd_backend() == LargeNetworkSimdBackend::SSE2_X8_INT16_TO_INT32,
            "required large-network SSE2 backend is unavailable");
    Attacks::init();
    Position::init();
    TransitionTotals totals;
    bool             ranAccumulatorNegatives = false;
    std::string      line;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "fixture stream contains an empty line");
        execute_transition_case(network, otherNetwork, split(line, '\t'), ranAccumulatorNegatives,
                                totals);
    }
    require(std::cin.eof(), "fixture stream read failed");
    require(ranAccumulatorNegatives, "accumulator negatives did not run");
    require(totals.cases == 13 && totals.moves == 17 && totals.undos == 17,
            "real transition count drifted");
    require(totals.nullMoves == 1 && totals.nullUndos == 1, "null transition count drifted");
    require(totals.refreshes == 13 && totals.updates == 36, "accumulator operation count drifted");
    require(totals.checkpoints == 49 && totals.sideToMoveEvaluations == 98,
            "evaluation checkpoint count drifted");
    require(totals.simdTraceValues == 420616 && totals.incrementalTraceValues == 420616,
            "trace comparison count drifted");
    require(totals.operationNegatives == 12 && totals.evaluationNegatives == 4,
            "negative-control count drifted");

    std::cout << "TRANSITIONS"
              << "\tbackend=" << simd_backend_name() << "\tcases=" << totals.cases
              << "\tmoves=" << totals.moves << "\tundos=" << totals.undos
              << "\tnulls=" << totals.nullMoves << "\tnull_undos=" << totals.nullUndos
              << "\trefreshes=" << totals.refreshes << "\tupdates=" << totals.updates
              << "\tcheckpoints=" << totals.checkpoints
              << "\tside_to_move_evaluations=" << totals.sideToMoveEvaluations
              << "\tsimd_trace_values=" << totals.simdTraceValues
              << "\tincremental_trace_values=" << totals.incrementalTraceValues
              << "\toperation_negatives=" << totals.operationNegatives
              << "\tevaluation_negatives=" << totals.evaluationNegatives << "\tdigest=" << std::hex
              << std::setw(16) << std::setfill('0') << totals.digest << std::dec
              << "\ttraining_admissible=false\tg12_closed=false\n";
}

struct RuntimeTransitionTotals {
    std::uint64_t cases       = 0;
    std::uint64_t moves       = 0;
    std::uint64_t undos       = 0;
    std::uint64_t nullMoves   = 0;
    std::uint64_t nullUndos   = 0;
    std::uint64_t checkpoints = 0;
    std::uint64_t comparisons = 0;
    std::uint64_t digest      = 0x52554E54494D4556ULL;
};

void compare_runtime(const LargeRuntimeV1&           runtime,
                     LargeRuntimeAccumulatorStackV1& stack,
                     const Position&                 position,
                     const std::string&              label,
                     RuntimeTransitionTotals&        totals) {
    const Evaluation scalar      = runtime.evaluate_full_refresh(position);
    const Evaluation simd        = runtime.evaluate_full_refresh_simd(position);
    const Evaluation incremental = runtime.evaluate_search_incremental(position, stack);
    const Evaluation reuse       = runtime.evaluate_search_incremental(position, stack);
    require(scalar.ok(), label + " scalar runtime evaluation rejected");
    require_same_evaluation(scalar, simd, label + " runtime scalar/SIMD");
    require_same_evaluation(scalar, incremental, label + " runtime incremental/full");
    require_same_evaluation(incremental, reuse, label + " runtime same-frame reuse");
    mix_text(totals.digest, position.fen());
    mix_trace(totals.digest, scalar.trace);
    ++totals.checkpoints;
    totals.comparisons += 3;
}

void execute_runtime_transition_case(const LargeRuntimeV1&           runtime,
                                     const std::vector<std::string>& fields,
                                     RuntimeTransitionTotals&        totals) {
    require(fields.size() == 5, "runtime fixture line does not have five fields");
    const std::string& id          = fields[0];
    const std::string& mode        = fields[1];
    const std::string& fen         = fields[2];
    const std::string& moveText    = fields[3];
    const std::string& expectedFen = fields[4];

    Position              position(Ruleset::CRAZYHOUSE);
    std::deque<StateInfo> states;
    states.emplace_back();
    set_position(position, fen, states.back());
    const std::string normalizedRoot = position.fen();

    LargeRuntimeAccumulatorStackV1 stack;
    require(stack.ensure_allocated(), id + " runtime stack allocation failed");
    compare_runtime(runtime, stack, position, id + " runtime root", totals);

    if (mode == "walk")
    {
        std::vector<std::string> tokens;
        if (!moveText.empty())
            tokens = split(moveText, ' ');
        std::vector<Move> moves;
        for (const std::string& token : tokens)
        {
            const Move move = parse_move(position, token);
            states.emplace_back();
            position.do_move(move, states.back());
            require(stack.push(), id + " runtime push failed");
            moves.push_back(move);
            ++totals.moves;
            compare_runtime(runtime, stack, position, id + " runtime after " + token, totals);
        }
        require(position.fen() == expectedFen, id + " runtime final FEN mismatch");
        while (!moves.empty())
        {
            const Move move = moves.back();
            moves.pop_back();
            position.undo_move(move);
            states.pop_back();
            require(stack.pop(), id + " runtime pop failed");
            ++totals.undos;
            compare_runtime(runtime, stack, position, id + " runtime undo", totals);
        }
        require(position.fen() == normalizedRoot, id + " runtime did not restore root FEN");
    }
    else if (mode == "null")
    {
        require(moveText.empty(), id + " runtime null case unexpectedly has moves");
        StateInfo nullState{};
        position.do_null_move(nullState);
        ++totals.nullMoves;
        compare_runtime(runtime, stack, position, id + " runtime null", totals);
        position.undo_null_move();
        ++totals.nullUndos;
        compare_runtime(runtime, stack, position, id + " runtime null undo", totals);
        require(position.fen() == expectedFen, id + " runtime null round-trip FEN mismatch");
    }
    else
        fail(id + " has unknown runtime mode " + mode);
    ++totals.cases;
}

void run_runtime_transition_suite(const std::filesystem::path& networkPath) {
    constexpr std::string_view ArtifactSha256 =
      "e305c386080c3d802deb23fad322ee04689d360d9b04526f7e5608e9fc055311";
    constexpr std::string_view Provenance =
      "1111111111111111111111111111111111111111111111111111111111111111:"
      "2222222222222222222222222222222222222222222222222222222222222222:"
      "3333333333333333333333333333333333333333333333333333333333333333:"
      "4444444444444444444444444444444444444444444444444444444444444444:"
      "5555555555555555555555555555555555555555555555555555555555555555:"
      "6666666666666666666666666666666666666666666666666666666666666666";

    Attacks::init();
    Position::init();
    LargeRuntimeV1 runtime;
    const auto     loaded = runtime.load_file(networkPath, ArtifactSha256, Provenance);
    require(loaded.ok(),
            "runtime load rejected: " + std::string(large_runtime_load_status_name(loaded.status))
              + " :: " + loaded.message);
    require(runtime.artifact_sha256() == ArtifactSha256, "runtime artifact identity mismatch");

    RuntimeTransitionTotals totals;
    std::string             line;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "runtime fixture stream contains an empty line");
        execute_runtime_transition_case(runtime, split(line, '\t'), totals);
    }
    require(std::cin.eof(), "runtime fixture stream read failed");
    require(totals.cases == 13 && totals.moves == 17 && totals.undos == 17,
            "runtime real transition count drifted");
    require(totals.nullMoves == 1 && totals.nullUndos == 1,
            "runtime null transition count drifted");
    require(totals.checkpoints == 49 && totals.comparisons == 147,
            "runtime comparison count drifted");

    std::cout << "RUNTIME_TRANSITIONS"
              << "\tbackend=" << LargeRuntimeV1::simd_backend_name() << "\tcases=" << totals.cases
              << "\tmoves=" << totals.moves << "\tundos=" << totals.undos
              << "\tnulls=" << totals.nullMoves << "\tnull_undos=" << totals.nullUndos
              << "\tcheckpoints=" << totals.checkpoints << "\tcomparisons=" << totals.comparisons
              << "\tdigest=" << std::hex << std::setw(16) << std::setfill('0') << totals.digest
              << std::dec
              << "\tnormal_engine_opt_in=true\tmodel_selected=false\tstrength_evidence=false\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    require(
      argc == 2 || argc == 3 || argc == 4,
      "usage: verifier NETWORK [--transition-suite|--runtime-transition-suite|--expect-error NAME]");
    const std::string networkPath    = argv[1];
    const bool        transitionMode = argc == 3 && std::string(argv[2]) == "--transition-suite";
    const bool        runtimeTransitionMode =
      argc == 3 && std::string(argv[2]) == "--runtime-transition-suite";
    const std::string expectedError =
      argc == 4 && std::string(argv[2]) == "--expect-error" ? argv[3] : std::string{};
    require(argc == 2 || transitionMode || runtimeTransitionMode || !expectedError.empty(),
            "invalid mode arguments");

    if (runtimeTransitionMode)
    {
        run_runtime_transition_suite(networkPath);
        return EXIT_SUCCESS;
    }

    std::vector<Byte>               bytes = read_file(networkPath);
    const LargeExpectedProvenanceV1 provenance =
      expectedError == "EXPECTED_PROVENANCE" ? LargeExpectedProvenanceV1{} : expected_provenance();
    LargeNetworkLoadResultV1 loaded = load_large_network_v1(bytes.data(), bytes.size(), provenance);
    if (!expectedError.empty())
    {
        require(!loaded.ok() && !loaded.network, "adversarial network exposed an object");
        const std::string observed(large_network_load_error_name(loaded.error));
        require(observed == expectedError, "expected " + expectedError + " observed " + observed);
        std::cout << "REJECT\t" << observed << "\tobject=false\n";
        return EXIT_SUCCESS;
    }
    require(loaded.ok(),
            "network rejected: " + std::string(large_network_load_error_name(loaded.error)));
    if (transitionMode)
    {
        LargeNetworkLoadResultV1 otherLoaded =
          load_large_network_v1(bytes.data(), bytes.size(), provenance);
        require(otherLoaded.ok(),
                "secondary network rejected: "
                  + std::string(large_network_load_error_name(otherLoaded.error)));
        bytes.clear();
        bytes.shrink_to_fit();
        run_transition_suite(*loaded.network, *otherLoaded.network);
        return EXIT_SUCCESS;
    }
    bytes.clear();
    bytes.shrink_to_fit();

    Inventory::Result              empty = empty_features();
    const std::unique_ptr<Network> unready(new Network);
    require_error(*unready, empty, WHITE, LargeNetworkEvaluateError::NETWORK_NOT_READY, "unready");
    Inventory::Result badStatus;
    require_error(*loaded.network, badStatus, WHITE, LargeNetworkEvaluateError::FEATURE_STATUS,
                  "feature-status");
    require_error(*loaded.network, empty, static_cast<Color>(COLOR_NB),
                  LargeNetworkEvaluateError::SIDE_TO_MOVE, "side-to-move");
    Inventory::Result pocketOverflow = empty;
    pocketOverflow.totalPocketUnits  = 31;
    require_error(*loaded.network, pocketOverflow, WHITE, LargeNetworkEvaluateError::POCKET_UNITS,
                  "pocket-units");
    Inventory::Result pocketMismatch = empty;
    pocketMismatch.totalPocketUnits  = 1;
    require_error(*loaded.network, pocketMismatch, WHITE,
                  LargeNetworkEvaluateError::POCKET_ROUTING_MISMATCH, "pocket-routing");
    Inventory::Result badIndex                = empty;
    badIndex.perspective[WHITE].k64.size      = 1;
    badIndex.perspective[WHITE].k64.active[0] = Inventory::KDimensions;
    require_error(*loaded.network, badIndex, WHITE, LargeNetworkEvaluateError::FEATURE_INDEX,
                  "feature-index");
    Inventory::Result duplicate               = empty;
    duplicate.perspective[BLACK].g1.size      = 2;
    duplicate.perspective[BLACK].g1.active[0] = 7;
    duplicate.perspective[BLACK].g1.active[1] = 7;
    require_error(*loaded.network, duplicate, WHITE, LargeNetworkEvaluateError::DUPLICATE_FEATURE,
                  "duplicate-feature");
    Inventory::Result overflow          = empty;
    overflow.perspective[WHITE].g1.size = Inventory::MaximumActivePerDomain + 1;
    require_error(*loaded.network, overflow, WHITE, LargeNetworkEvaluateError::ACTIVE_OVERFLOW,
                  "active-overflow");

    const std::array<std::pair<std::string, Inventory::Result>, 3> cases = {{
      {"empty", empty},
      {"one", one_row_features()},
      {"bucket7", bucket_seven_features()},
    }};
    for (const auto& [id, features] : cases)
        for (Color sideToMove : {WHITE, BLACK})
        {
            const auto evaluated = loaded.network->evaluate(features, sideToMove);
            require(
              evaluated.ok(),
              id + " rejected: " + std::string(large_network_evaluate_error_name(evaluated.error)));
            emit_trace(id, sideToMove, evaluated.trace);
        }

    std::cout << "SUMMARY\tnegative_cases=8\tpositive_cases=6\tcontainer_bytes="
              << LargeNetworkFileBytes
              << "\tarchitecture=CH-NNUE-V2-LARGE-K64G1-SFNNV16"
                 "\ttraining_admissible=false\tg12_closed=false\n";
    return EXIT_SUCCESS;
}
