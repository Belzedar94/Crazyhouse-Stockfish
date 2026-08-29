/*
  Dedicated engineering verifier for CH-NNUE-V2-LARGE-K64G1-SFNNV16.
  This executable is not linked into or reachable through the normal engine.
*/

#include <algorithm>
#include <array>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>
#include <type_traits>
#include <vector>

#include "nnue/crazyhouse_v2_large_network.h"

namespace {

using namespace Stockfish;
using namespace Stockfish::Eval::NNUE::CrazyhouseV2;
using Inventory = LargeFeatureInventoryV1;

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
              << join(trace.fc0Squared) << '\t' << join(trace.fc0Clipped) << '\t'
              << join(trace.fc1) << '\t' << join(trace.fc1Squared) << '\t'
              << join(trace.fc1Clipped) << '\t' << trace.fc2 << '\t' << trace.fwdRaw << '\t'
              << trace.outputValue << '\n';
}

void require_error(const LargeNetworkV1& network,
                   const Inventory::Result& features,
                   Color sideToMove,
                   LargeNetworkEvaluateError expected,
                   const std::string& label) {
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
    Inventory::Result features = empty_features();
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
    features.totalPocketUnits = 28;
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

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 2 || argc == 4, "usage: verifier NETWORK [--expect-error NAME]");
    const std::string networkPath = argv[1];
    const std::string expectedError = argc == 4 && std::string(argv[2]) == "--expect-error"
                                    ? argv[3]
                                    : std::string{};
    require(argc == 2 || !expectedError.empty(), "invalid expected-error arguments");

    std::vector<Byte> bytes = read_file(networkPath);
    const LargeExpectedProvenanceV1 provenance =
      expectedError == "EXPECTED_PROVENANCE" ? LargeExpectedProvenanceV1{}
                                             : expected_provenance();
    LargeNetworkLoadResultV1 loaded =
      load_large_network_v1(bytes.data(), bytes.size(), provenance);
    if (!expectedError.empty())
    {
        require(!loaded.ok() && !loaded.network, "adversarial network exposed an object");
        const std::string observed(large_network_load_error_name(loaded.error));
        require(observed == expectedError,
                "expected " + expectedError + " observed " + observed);
        std::cout << "REJECT\t" << observed << "\tobject=false\n";
        return EXIT_SUCCESS;
    }
    require(loaded.ok(),
            "network rejected: " + std::string(large_network_load_error_name(loaded.error)));
    bytes.clear();
    bytes.shrink_to_fit();

    Inventory::Result empty = empty_features();
    LargeNetworkV1 unready;
    require_error(unready, empty, WHITE, LargeNetworkEvaluateError::NETWORK_NOT_READY, "unready");
    Inventory::Result badStatus;
    require_error(*loaded.network, badStatus, WHITE, LargeNetworkEvaluateError::FEATURE_STATUS,
                  "feature-status");
    require_error(*loaded.network, empty, static_cast<Color>(COLOR_NB),
                  LargeNetworkEvaluateError::SIDE_TO_MOVE, "side-to-move");
    Inventory::Result pocketOverflow = empty;
    pocketOverflow.totalPocketUnits = 31;
    require_error(*loaded.network, pocketOverflow, WHITE, LargeNetworkEvaluateError::POCKET_UNITS,
                  "pocket-units");
    Inventory::Result pocketMismatch = empty;
    pocketMismatch.totalPocketUnits = 1;
    require_error(*loaded.network, pocketMismatch, WHITE,
                  LargeNetworkEvaluateError::POCKET_ROUTING_MISMATCH, "pocket-routing");
    Inventory::Result badIndex = empty;
    badIndex.perspective[WHITE].k64.size = 1;
    badIndex.perspective[WHITE].k64.active[0] = Inventory::KDimensions;
    require_error(*loaded.network, badIndex, WHITE, LargeNetworkEvaluateError::FEATURE_INDEX,
                  "feature-index");
    Inventory::Result duplicate = empty;
    duplicate.perspective[BLACK].g1.size = 2;
    duplicate.perspective[BLACK].g1.active[0] = 7;
    duplicate.perspective[BLACK].g1.active[1] = 7;
    require_error(*loaded.network, duplicate, WHITE, LargeNetworkEvaluateError::DUPLICATE_FEATURE,
                  "duplicate-feature");
    Inventory::Result overflow = empty;
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
            require(evaluated.ok(),
                    id + " rejected: "
                      + std::string(large_network_evaluate_error_name(evaluated.error)));
            emit_trace(id, sideToMove, evaluated.trace);
        }

    std::cout << "SUMMARY\tnegative_cases=8\tpositive_cases=6\tcontainer_bytes="
              << LargeNetworkFileBytes
              << "\tarchitecture=CH-NNUE-V2-LARGE-K64G1-SFNNV16"
                 "\ttraining_admissible=false\tg12_closed=false\n";
    return EXIT_SUCCESS;
}
