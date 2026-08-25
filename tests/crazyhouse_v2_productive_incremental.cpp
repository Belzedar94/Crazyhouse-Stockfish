/*
  Dedicated fixture for the productive Crazyhouse NNUE V2 transactional
  accumulator. Cases arrive as the frozen tab-separated transition stream.
  This executable is not linked into or reachable through the normal engine.
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
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "nnue/crazyhouse_v2_features.h"
#include "nnue/crazyhouse_v2_productive.h"
#include "position.h"
#include "uci.h"

namespace {

using namespace Stockfish;
using namespace Eval::NNUE::CrazyhouseV2;

using Features    = ScalarFeatureInventoryV1::Result;
using Network     = ProductiveNetworkV1;
using Accumulator = ProductiveAccumulatorV1;
using Evaluation  = ProductiveEvaluationResultV1;

constexpr std::size_t TraceValuesPerEvaluation =
  ProductiveTransformerLanes * 4 + ProductiveDense0Outputs * 2 + ProductiveDense1Outputs * 2 + 2;
static_assert(TraceValuesPerEvaluation == 2178);

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_productive_incremental: " << message << '\n';
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

int nibble(char value) {
    if (value >= '0' && value <= '9')
        return value - '0';
    if (value >= 'a' && value <= 'f')
        return value - 'a' + 10;
    return -1;
}

Digest parse_digest(const std::string& text, const std::string& label) {
    require(text.size() == 64, label + " must contain 64 lowercase hexadecimal characters");
    Digest output{};
    for (std::size_t index = 0; index < output.size(); ++index)
    {
        const int high = nibble(text[index * 2]);
        const int low  = nibble(text[index * 2 + 1]);
        require(high >= 0 && low >= 0, label + " is not lowercase hexadecimal");
        output[index] = Byte((high << 4) | low);
    }
    require(std::any_of(output.begin(), output.end(), [](Byte value) { return value != 0; }),
            label + " is zero");
    return output;
}

std::vector<Byte> read_bytes(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    require(stream.good(), "cannot open productive network artifact");
    const auto size = stream.tellg();
    require(size >= 0, "cannot determine productive network artifact size");
    std::vector<Byte> bytes(static_cast<std::size_t>(size));
    stream.seekg(0);
    stream.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    require(stream.good(), "cannot read complete productive network artifact");
    return bytes;
}

bool same_trace(const ProductiveTraceV1& left, const ProductiveTraceV1& right) {
    return left.transformerStm == right.transformerStm
        && left.transformerOpponent == right.transformerOpponent
        && left.transformerStmActivation == right.transformerStmActivation
        && left.transformerOpponentActivation == right.transformerOpponentActivation
        && left.dense0 == right.dense0 && left.dense0Activation == right.dense0Activation
        && left.dense1 == right.dense1 && left.dense1Activation == right.dense1Activation
        && left.outputRaw == right.outputRaw && left.outputCentipawns == right.outputCentipawns;
}

void require_same_evaluation(const Evaluation&  expected,
                             const Evaluation&  actual,
                             const std::string& label) {
    require(expected.error == actual.error,
            label + " status mismatch: expected "
              + std::string(productive_evaluate_error_name(expected.error)) + ", got "
              + std::string(productive_evaluate_error_name(actual.error)));
    require(same_trace(expected.trace, actual.trace), label + " trace mismatch");
}

void require_failure(const Evaluation&       actual,
                     ProductiveEvaluateError expected,
                     const std::string&      label) {
    require(actual.error == expected,
            label + " status mismatch: expected "
              + std::string(productive_evaluate_error_name(expected)) + ", got "
              + std::string(productive_evaluate_error_name(actual.error)));
    require(same_trace(actual.trace, ProductiveTraceV1{}), label + " exposed a partial trace");
}

void mix(std::uint64_t& digest, std::uint64_t value) {
    digest ^= value + 0x9E3779B97F4A7C15ULL + (digest << 6) + (digest >> 2);
}

void mix_text(std::uint64_t& digest, std::string_view text) {
    for (const unsigned char value : text)
        mix(digest, value);
}

template<typename Value, std::size_t Size>
void mix_array(std::uint64_t& digest, const std::array<Value, Size>& values) {
    for (const Value value : values)
        mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(value)));
}

void mix_trace(std::uint64_t& digest, const ProductiveTraceV1& trace) {
    mix_array(digest, trace.transformerStm);
    mix_array(digest, trace.transformerOpponent);
    mix_array(digest, trace.transformerStmActivation);
    mix_array(digest, trace.transformerOpponentActivation);
    mix_array(digest, trace.dense0);
    mix_array(digest, trace.dense0Activation);
    mix_array(digest, trace.dense1);
    mix_array(digest, trace.dense1Activation);
    mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(trace.outputRaw)));
    mix(digest, static_cast<std::uint64_t>(static_cast<std::int64_t>(trace.outputCentipawns)));
}

struct Totals {
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
    std::uint64_t digest                 = 0x4348563250524F44ULL;
};

std::string simd_backend_name() {
    switch (productive_simd_backend())
    {
    case ProductiveSimdBackend::UNAVAILABLE :
        return "unavailable";
    case ProductiveSimdBackend::SSE2_X8_INT16_TO_INT32 :
        return "sse2-x8-int16-to-int32";
    }
    return "unknown";
}

void compare_position(const Network&     network,
                      const Accumulator& accumulator,
                      const Position&    position,
                      const Features&    features,
                      const std::string& label,
                      Totals&            totals) {
    require(features.ok(), label + " feature extraction failed: "
                             + std::string(ScalarFeatureInventoryV1::status_name(features.status)));
    require(accumulator.bound_to(network), label + " accumulator network binding mismatch");
    require(accumulator.matches(features), label + " accumulator membership mismatch");
    mix_text(totals.digest, position.fen());
    for (Color sideToMove : {WHITE, BLACK})
    {
        const Evaluation scalar      = network.evaluate(features, sideToMove);
        const Evaluation simd        = network.evaluate_simd(features, sideToMove);
        const Evaluation incremental = accumulator.evaluate(features, sideToMove);
        require(scalar.ok(), label + " scalar full refresh rejected: "
                               + std::string(productive_evaluate_error_name(scalar.error)));
        require_same_evaluation(scalar, simd, label + " scalar/SIMD");
        require_same_evaluation(scalar, incremental, label + " incremental/full");
        totals.simdTraceValues += TraceValuesPerEvaluation;
        totals.incrementalTraceValues += TraceValuesPerEvaluation;
        ++totals.sideToMoveEvaluations;
        mix_trace(totals.digest, scalar.trace);
    }
    ++totals.checkpoints;
}

std::array<Evaluation, COLOR_NB> snapshot(const Accumulator& accumulator,
                                          const Features&    features) {
    return {accumulator.evaluate(features, WHITE), accumulator.evaluate(features, BLACK)};
}

void require_unchanged(const Accumulator&                      accumulator,
                       const Network&                          network,
                       const Features&                         source,
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
    const auto before = snapshot(accumulator, source);
    auto expect = [&](ProductiveAccumulatorResultV1 result, ProductiveAccumulatorError expected,
                      const std::string& label) {
        require(result.error == expected,
                label + " error mismatch: expected "
                  + std::string(productive_accumulator_error_name(expected)) + ", got "
                  + std::string(productive_accumulator_error_name(result.error)));
        require_unchanged(accumulator, network, source, before, label);
        ++totals.operationNegatives;
    };

    Accumulator unready;
    const auto  unreadyUpdate = unready.update(network, source, source);
    require(unreadyUpdate.error == ProductiveAccumulatorError::SOURCE_NOT_READY,
            "uninitialized update error mismatch");
    require(!unready.ready(), "uninitialized update committed state");
    ++totals.operationNegatives;

    expect(accumulator.update(otherNetwork, source, source),
           ProductiveAccumulatorError::NETWORK_MISMATCH, "network-mismatch");

    const Features stale = different_valid_inventory(source);
    expect(accumulator.update(network, stale, source),
           ProductiveAccumulatorError::SOURCE_INVENTORY_MISMATCH, "stale-source");

    Features invalidSource = source;
    invalidSource.status   = ScalarFeatureInventoryV1::Status::PROMOTED_MASK;
    expect(accumulator.update(network, invalidSource, source),
           ProductiveAccumulatorError::FEATURE_STATUS, "invalid-source");

    Features invalidTarget = source;
    invalidTarget.status   = ScalarFeatureInventoryV1::Status::PROMOTED_MASK;
    expect(accumulator.update(network, source, invalidTarget),
           ProductiveAccumulatorError::FEATURE_STATUS, "invalid-target");

    Features overflowTarget    = source;
    overflowTarget.size[WHITE] = ScalarFeatureInventoryV1::MaximumActive + 1;
    expect(accumulator.update(network, source, overflowTarget),
           ProductiveAccumulatorError::ACTIVE_OVERFLOW, "target-overflow");

    Features badIndexTarget         = source;
    badIndexTarget.size[WHITE]      = 1;
    badIndexTarget.active[WHITE][0] = ScalarFeatureInventoryV1::Dimensions;
    expect(accumulator.update(network, source, badIndexTarget),
           ProductiveAccumulatorError::FEATURE_INDEX, "target-index");

    Features duplicateTarget         = source;
    duplicateTarget.size[WHITE]      = 2;
    duplicateTarget.active[WHITE][0] = 7;
    duplicateTarget.active[WHITE][1] = 7;
    expect(accumulator.update(network, source, duplicateTarget),
           ProductiveAccumulatorError::DUPLICATE_FEATURE, "target-duplicate");

    Network emptyNetwork;
    expect(accumulator.update(emptyNetwork, source, source),
           ProductiveAccumulatorError::NETWORK_NOT_READY, "not-ready-network");
    expect(accumulator.refresh(network, invalidTarget), ProductiveAccumulatorError::FEATURE_STATUS,
           "failed-refresh");

    Accumulator emptyAccumulator;
    require_failure(emptyAccumulator.evaluate(source, WHITE),
                    ProductiveEvaluateError::ACCUMULATOR_NOT_READY, "uninitialized-evaluation");
    ++totals.evaluationNegatives;
    require_failure(accumulator.evaluate(source, static_cast<Color>(COLOR_NB)),
                    ProductiveEvaluateError::SIDE_TO_MOVE, "invalid-side-to-move");
    require_unchanged(accumulator, network, source, before, "invalid-side-to-move");
    ++totals.evaluationNegatives;
    require_failure(accumulator.evaluate(stale, WHITE),
                    ProductiveEvaluateError::ACCUMULATOR_INVENTORY_MISMATCH, "stale-evaluation");
    require_unchanged(accumulator, network, source, before, "stale-evaluation");
    ++totals.evaluationNegatives;
    require_failure(accumulator.evaluate(invalidTarget, WHITE),
                    ProductiveEvaluateError::FEATURE_STATUS, "invalid-inventory-evaluation");
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

void require_update(Accumulator&       accumulator,
                    const Network&     network,
                    const Features&    source,
                    const Features&    target,
                    const std::string& label) {
    const ProductiveAccumulatorResultV1 result = accumulator.update(network, source, target);
    require(result.ok(), label + " update rejected: "
                           + std::string(productive_accumulator_error_name(result.error)));
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
                            + std::string(productive_accumulator_error_name(refresh.error)));
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
            const Features source = inventories.back();
            inventories.pop_back();
            const Move move = moves.back();
            moves.pop_back();
            position.undo_move(move);
            states.pop_back();
            const Features target = ScalarFeatureInventoryV1::extract(position);
            require_update(accumulator, network, source, target, id + " undo");
            ++totals.updates;
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
        require(afterNull.active == rootFeatures.active && afterNull.size == rootFeatures.size,
                id + " null move changed physical feature inventory");
        require_update(accumulator, network, rootFeatures, afterNull, id + " null");
        ++totals.updates;
        ++totals.nullMoves;
        compare_position(network, accumulator, position, afterNull, id + " null", totals);

        position.undo_null_move();
        const Features afterUndo = ScalarFeatureInventoryV1::extract(position);
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

void require_frozen_counts(const Totals& totals) {
    require(totals.cases == 13, "case count drifted");
    require(totals.moves == 17, "move count drifted");
    require(totals.undos == 17, "undo count drifted");
    require(totals.nullMoves == 1, "null-move count drifted");
    require(totals.nullUndos == 1, "null-undo count drifted");
    require(totals.refreshes == 13, "refresh count drifted");
    require(totals.updates == 36, "update count drifted");
    require(totals.checkpoints == 49, "position checkpoint count drifted");
    require(totals.sideToMoveEvaluations == 98, "side-to-move evaluation count drifted");
    require(totals.simdTraceValues == 213444, "SIMD trace-value count drifted");
    require(totals.incrementalTraceValues == 213444, "incremental trace-value count drifted");
    require(totals.operationNegatives == 10, "operation negative count drifted");
    require(totals.evaluationNegatives == 4, "evaluation negative count drifted");
}

}  // namespace

int main(int argc, char* argv[]) {
    std::filesystem::path networkPath;
    std::string           datasetSha;
    std::string           configSha;
    for (int index = 1; index < argc; ++index)
    {
        const std::string argument = argv[index];
        if (argument == "--network" && index + 1 < argc)
            networkPath = argv[++index];
        else if (argument == "--dataset-sha256" && index + 1 < argc)
            datasetSha = argv[++index];
        else if (argument == "--training-config-sha256" && index + 1 < argc)
            configSha = argv[++index];
        else
            fail("unknown or incomplete command-line argument");
    }
    require(!networkPath.empty(), "--network is required");

    ProductiveExpectedProvenanceV1 provenance;
    provenance.datasetManifest     = parse_digest(datasetSha, "dataset SHA-256");
    provenance.trainingConfig      = parse_digest(configSha, "training config SHA-256");
    const std::vector<Byte> bytes  = read_bytes(networkPath);
    ProductiveLoadResultV1  loaded = load_productive_v1(bytes.data(), bytes.size(), provenance);
    require(loaded.ok(),
            "primary network rejected: " + std::string(productive_load_error_name(loaded.error)));
    ProductiveLoadResultV1 otherLoaded = load_productive_v1(bytes.data(), bytes.size(), provenance);
    require(otherLoaded.ok(), "secondary network rejected: "
                                + std::string(productive_load_error_name(otherLoaded.error)));
    require(productive_simd_backend() == ProductiveSimdBackend::SSE2_X8_INT16_TO_INT32,
            "required productive SSE2 backend is unavailable");

    Attacks::init();
    Position::init();

    Totals      totals;
    bool        ranAccumulatorNegatives = false;
    std::string line;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "fixture stream contains an empty line");
        execute_case(*loaded.network, *otherLoaded.network, split(line, '\t'),
                     ranAccumulatorNegatives, totals);
    }
    require(std::cin.eof(), "fixture stream read failed");
    require(ranAccumulatorNegatives, "accumulator negatives did not run");
    require_frozen_counts(totals);

    std::cout << "PASS crazyhouse_v2_productive_incremental"
              << " backend=" << simd_backend_name() << " cases=" << totals.cases
              << " moves=" << totals.moves << " undos=" << totals.undos
              << " nulls=" << totals.nullMoves << " null_undos=" << totals.nullUndos
              << " refreshes=" << totals.refreshes << " updates=" << totals.updates
              << " checkpoints=" << totals.checkpoints
              << " side_to_move_evaluations=" << totals.sideToMoveEvaluations
              << " simd_trace_values=" << totals.simdTraceValues
              << " incremental_trace_values=" << totals.incrementalTraceValues
              << " operation_negatives=" << totals.operationNegatives
              << " evaluation_negatives=" << totals.evaluationNegatives << " digest=" << std::hex
              << std::setw(16) << std::setfill('0') << totals.digest << std::dec
              << " training_admissible=false g12_closed=false\n";
    return EXIT_SUCCESS;
}
