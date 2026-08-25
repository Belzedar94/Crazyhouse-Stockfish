/*
  Dedicated fixture protocol for the productive Crazyhouse NNUE V2 scalar
  container.  This executable is not linked into or reachable through the
  normal engine.
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

#include "nnue/crazyhouse_v2_features.h"
#include "nnue/crazyhouse_v2_physical.h"
#include "nnue/crazyhouse_v2_productive.h"

namespace {

using namespace Stockfish;
using namespace Stockfish::Eval::NNUE::CrazyhouseV2;
using Inventory = ScalarFeatureInventoryV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_productive: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

std::vector<std::string> split_tabs(const std::string& input) {
    std::vector<std::string> output;
    std::size_t              start = 0;
    while (true)
    {
        const std::size_t delimiter = input.find('\t', start);
        output.push_back(input.substr(start, delimiter - start));
        if (delimiter == std::string::npos)
            return output;
        start = delimiter + 1;
    }
}

int nibble(char value) {
    if (value >= '0' && value <= '9')
        return value - '0';
    if (value >= 'a' && value <= 'f')
        return value - 'a' + 10;
    return -1;
}

std::vector<Byte> parse_hex(const std::string& text) {
    require(text.size() % 2 == 0, "hex payload has odd width");
    std::vector<Byte> output(text.size() / 2);
    for (std::size_t index = 0; index < output.size(); ++index)
    {
        const int high = nibble(text[index * 2]);
        const int low  = nibble(text[index * 2 + 1]);
        require(high >= 0 && low >= 0, "payload is not lowercase hexadecimal");
        output[index] = Byte((high << 4) | low);
    }
    return output;
}

Digest parse_digest(const std::string& text, const std::string& label, bool allowZero = false) {
    require(text.size() == 64, label + " must contain 64 lowercase hexadecimal characters");
    Digest output{};
    for (std::size_t index = 0; index < output.size(); ++index)
    {
        const int high = nibble(text[index * 2]);
        const int low  = nibble(text[index * 2 + 1]);
        require(high >= 0 && low >= 0, label + " is not lowercase hexadecimal");
        output[index] = Byte((high << 4) | low);
    }
    require(allowZero
              || std::any_of(output.begin(), output.end(), [](Byte value) { return value != 0; }),
            label + " is zero");
    return output;
}

std::vector<Byte> read_file(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    require(input.good(), "cannot open network file");
    std::vector<Byte> output((std::istreambuf_iterator<char>(input)),
                             std::istreambuf_iterator<char>());
    require(!input.bad(), "cannot read network file");
    return output;
}

std::string digest_hex(const Digest& digest) {
    constexpr char Digits[] = "0123456789abcdef";
    std::string    output;
    output.reserve(64);
    for (Byte value : digest)
    {
        output.push_back(Digits[value >> 4]);
        output.push_back(Digits[value & 15]);
    }
    return output;
}

std::string join_rows(const Inventory::Result& features, Color perspective) {
    std::ostringstream output;
    for (std::size_t index = 0; index < features.size[perspective]; ++index)
    {
        if (index)
            output << ',';
        output << features.active[perspective][index];
    }
    return output.str();
}

template<typename Value, std::size_t Size>
std::string join_values(const std::array<Value, Size>& values) {
    std::ostringstream output;
    for (std::size_t index = 0; index < values.size(); ++index)
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

void emit_trace(const std::string&       id,
                const std::string&       identity,
                const Inventory::Result& features,
                Color                    perspective,
                const ProductiveTraceV1& trace) {
    std::cout << "OK\t" << id << '\t' << (perspective == WHITE ? "white" : "black") << '\t'
              << identity << '\t' << join_rows(features, WHITE) << '\t'
              << join_rows(features, BLACK) << '\t' << join_values(trace.transformerStm) << '\t'
              << join_values(trace.transformerOpponent) << '\t'
              << join_values(trace.transformerStmActivation) << '\t'
              << join_values(trace.transformerOpponentActivation) << '\t'
              << join_values(trace.dense0) << '\t' << join_values(trace.dense0Activation) << '\t'
              << join_values(trace.dense1) << '\t' << join_values(trace.dense1Activation) << '\t'
              << trace.outputRaw << '\t' << trace.outputCentipawns << '\n';
}

constexpr std::size_t TraceValuesPerEvaluation =
  ProductiveTransformerLanes * 4 + ProductiveDense0Outputs * 2 + ProductiveDense1Outputs * 2 + 2;

struct SimdTotals {
    std::size_t negativeCases            = 0;
    std::size_t biasEvaluations          = 0;
    std::size_t singleRowEvaluations     = 0;
    std::size_t maximumActiveEvaluations = 0;
    std::size_t physicalEvaluations      = 0;
    std::size_t traceValues              = 0;
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

bool same_trace(const ProductiveTraceV1& left, const ProductiveTraceV1& right) {
    return left.transformerStm == right.transformerStm
        && left.transformerOpponent == right.transformerOpponent
        && left.transformerStmActivation == right.transformerStmActivation
        && left.transformerOpponentActivation == right.transformerOpponentActivation
        && left.dense0 == right.dense0 && left.dense0Activation == right.dense0Activation
        && left.dense1 == right.dense1 && left.dense1Activation == right.dense1Activation
        && left.outputRaw == right.outputRaw && left.outputCentipawns == right.outputCentipawns;
}

ProductiveEvaluationResultV1 compare_scalar_simd(const ProductiveNetworkV1& network,
                                                 const Inventory::Result&   features,
                                                 Color                      sideToMove,
                                                 const std::string&         label,
                                                 SimdTotals&                totals) {
    const ProductiveEvaluationResultV1 scalar = network.evaluate(features, sideToMove);
    const ProductiveEvaluationResultV1 simd   = network.evaluate_simd(features, sideToMove);
    require(scalar.error == simd.error,
            label + " error mismatch: scalar="
              + std::string(productive_evaluate_error_name(scalar.error))
              + " SIMD=" + std::string(productive_evaluate_error_name(simd.error)));
    require(scalar.ok(), label + " scalar evaluation rejected: "
                           + std::string(productive_evaluate_error_name(scalar.error)));
    require(same_trace(scalar.trace, simd.trace), label + " trace mismatch");
    totals.traceValues += TraceValuesPerEvaluation;
    return simd;
}

void require_error_pair(const ProductiveNetworkV1& network,
                        const Inventory::Result&   features,
                        Color                      sideToMove,
                        ProductiveEvaluateError    expected,
                        const std::string&         label,
                        SimdTotals&                totals) {
    const ProductiveEvaluationResultV1 scalar = network.evaluate(features, sideToMove);
    const ProductiveEvaluationResultV1 simd   = network.evaluate_simd(features, sideToMove);
    require(scalar.error == expected, label + " scalar error mismatch");
    require(simd.error == expected, label + " SIMD error mismatch");
    require(same_trace(scalar.trace, ProductiveTraceV1{}), label + " scalar failure trace");
    require(same_trace(simd.trace, ProductiveTraceV1{}), label + " SIMD failure trace");
    ++totals.negativeCases;
}

SimdTotals run_simd_selftest(const ProductiveNetworkV1& network) {
    require(productive_simd_backend() == ProductiveSimdBackend::SSE2_X8_INT16_TO_INT32,
            "productive SIMD backend is not SSE2 x8 int16-to-int32");
    SimdTotals        totals;
    Inventory::Result valid;
    valid.status = Inventory::Status::SUCCESS;

    ProductiveNetworkV1 unready;
    require_error_pair(unready, valid, WHITE, ProductiveEvaluateError::NETWORK_NOT_READY, "unready",
                       totals);
    require_error_pair(network, valid, static_cast<Color>(COLOR_NB),
                       ProductiveEvaluateError::SIDE_TO_MOVE, "side-to-move", totals);
    Inventory::Result invalidStatus;
    require_error_pair(network, invalidStatus, WHITE, ProductiveEvaluateError::FEATURE_STATUS,
                       "feature-status", totals);
    Inventory::Result overflow = valid;
    overflow.size[WHITE]       = Inventory::MaximumActive + 1;
    require_error_pair(network, overflow, WHITE, ProductiveEvaluateError::ACTIVE_OVERFLOW,
                       "active-overflow", totals);
    Inventory::Result invalidIndex = valid;
    invalidIndex.size[WHITE]       = 1;
    invalidIndex.active[WHITE][0]  = Inventory::Dimensions;
    require_error_pair(network, invalidIndex, WHITE, ProductiveEvaluateError::FEATURE_INDEX,
                       "feature-index", totals);
    Inventory::Result duplicate = valid;
    duplicate.size[WHITE]       = 2;
    duplicate.active[WHITE][0]  = 7;
    duplicate.active[WHITE][1]  = 7;
    require_error_pair(network, duplicate, WHITE, ProductiveEvaluateError::DUPLICATE_FEATURE,
                       "duplicate-feature", totals);

    for (Color sideToMove : {WHITE, BLACK})
    {
        compare_scalar_simd(network, valid, sideToMove, "bias-only", totals);
        ++totals.biasEvaluations;
    }
    for (std::size_t row = 0; row < Inventory::Dimensions; ++row)
        for (Color owner : {WHITE, BLACK})
        {
            Inventory::Result one = valid;
            one.size[owner]       = 1;
            one.active[owner][0]  = static_cast<Inventory::Index>(row);
            for (Color sideToMove : {WHITE, BLACK})
            {
                compare_scalar_simd(network, one, sideToMove, "single-row-" + std::to_string(row),
                                    totals);
                ++totals.singleRowEvaluations;
            }
        }
    Inventory::Result maximum = valid;
    maximum.size[WHITE] = maximum.size[BLACK] = Inventory::MaximumActive;
    for (std::size_t index = 0; index < Inventory::MaximumActive; ++index)
    {
        maximum.active[WHITE][index] = static_cast<Inventory::Index>(index);
        maximum.active[BLACK][index] =
          static_cast<Inventory::Index>(Inventory::Dimensions - 1 - index);
    }
    for (Color sideToMove : {WHITE, BLACK})
    {
        compare_scalar_simd(network, maximum, sideToMove, "maximum-active", totals);
        ++totals.maximumActiveEvaluations;
    }
    return totals;
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string networkPath;
    std::string datasetSha;
    std::string configSha;
    std::string expectedError;
    std::string backend      = "scalar";
    bool        simdSelftest = false;
    for (int index = 1; index < argc; ++index)
    {
        const std::string argument = argv[index];
        if (argument == "--network" && index + 1 < argc)
            networkPath = argv[++index];
        else if (argument == "--dataset-sha256" && index + 1 < argc)
            datasetSha = argv[++index];
        else if (argument == "--training-config-sha256" && index + 1 < argc)
            configSha = argv[++index];
        else if (argument == "--expect-network-error" && index + 1 < argc)
            expectedError = argv[++index];
        else if (argument == "--backend" && index + 1 < argc)
            backend = argv[++index];
        else if (argument == "--simd-selftest")
            simdSelftest = true;
        else
            fail("unknown or incomplete command-line argument");
    }
    require(!networkPath.empty(), "--network is required");
    require(backend == "scalar" || backend == "simd", "--backend must be scalar or simd");
    require(!simdSelftest || backend == "scalar", "--simd-selftest cannot select --backend");
    const bool expectedProvenanceFailure = expectedError == "EXPECTED_PROVENANCE";
    ProductiveExpectedProvenanceV1 provenance;
    provenance.datasetManifest =
      parse_digest(datasetSha, "dataset SHA-256", expectedProvenanceFailure);
    provenance.trainingConfig =
      parse_digest(configSha, "training config SHA-256", expectedProvenanceFailure);

    const std::vector<Byte> bytes  = read_file(networkPath);
    ProductiveLoadResultV1  loaded = load_productive_v1(bytes.data(), bytes.size(), provenance);
    if (!expectedError.empty())
    {
        require(!loaded.ok(), "adversarial network was accepted");
        require(!loaded.network, "failed parser exposed a partial network object");
        const std::string observed(productive_load_error_name(loaded.error));
        require(observed == expectedError,
                "expected " + expectedError + " but observed " + observed);
        std::cout << "REJECT\tnetwork\t" << observed << "\tobject=false\n";
        return EXIT_SUCCESS;
    }
    require(loaded.ok(),
            "network rejected: " + std::string(productive_load_error_name(loaded.error)));

    Inventory::Result emptyFeatures;
    emptyFeatures.status = Inventory::Status::SUCCESS;
    ProductiveNetworkV1 defaultNetwork;
    const auto          defaultEvaluation = defaultNetwork.evaluate(emptyFeatures, WHITE);
    require(defaultEvaluation.error == ProductiveEvaluateError::NETWORK_NOT_READY,
            "default network became evaluable");
    const auto defaultSimdEvaluation = defaultNetwork.evaluate_simd(emptyFeatures, WHITE);
    require(defaultSimdEvaluation.error == ProductiveEvaluateError::NETWORK_NOT_READY,
            "default network became SIMD-evaluable");

    if (simdSelftest)
    {
        const SimdTotals totals = run_simd_selftest(*loaded.network);
        require(totals.negativeCases == 6, "SIMD negative count drifted");
        require(totals.biasEvaluations == 2, "SIMD bias count drifted");
        require(totals.singleRowEvaluations == 3608, "SIMD single-row count drifted");
        require(totals.maximumActiveEvaluations == 2, "SIMD maximum-active count drifted");
        require(totals.traceValues == 7866936, "SIMD selftest trace count drifted");
        std::cout << "SIMD_SELFTEST\tbackend=" << simd_backend_name()
                  << "\tnegative_cases=" << totals.negativeCases
                  << "\tbias_evaluations=" << totals.biasEvaluations
                  << "\tsingle_row_evaluations=" << totals.singleRowEvaluations
                  << "\tmaximum_active_evaluations=" << totals.maximumActiveEvaluations
                  << "\ttotal_evaluations="
                  << totals.biasEvaluations + totals.singleRowEvaluations
                       + totals.maximumActiveEvaluations
                  << "\ttrace_values=" << totals.traceValues << '\n';
        return EXIT_SUCCESS;
    }

    std::size_t records = 0;
    SimdTotals  physicalTotals;
    std::string line;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "protocol contains an empty line");
        const std::vector<std::string> fields = split_tabs(line);
        require(fields.size() == 3 && fields[0] == "VALID" && !fields[1].empty(),
                "VALID protocol field count");
        const std::vector<Byte>    recordBytes = parse_hex(fields[2]);
        const PhysicalDecodeResult decoded =
          decode_physical_record_v1(recordBytes.data(), recordBytes.size());
        require(decoded.ok(), fields[1] + " record rejected: "
                                + std::string(physical_decode_error_name(decoded.error)));
        const Inventory::Result features = Inventory::extract(decoded.record.state);
        require(features.ok(), fields[1] + " feature extraction rejected: "
                                 + std::string(Inventory::status_name(features.status)));
        const std::string identity = digest_hex(decoded.record.positionIdentity);
        for (Color perspective : {WHITE, BLACK})
        {
            ProductiveEvaluationResultV1 evaluated;
            if (backend == "simd")
            {
                require(productive_simd_backend() == ProductiveSimdBackend::SSE2_X8_INT16_TO_INT32,
                        "productive SIMD backend is not SSE2 x8 int16-to-int32");
                evaluated = compare_scalar_simd(*loaded.network, features, perspective,
                                                fields[1] + " physical", physicalTotals);
                ++physicalTotals.physicalEvaluations;
            }
            else
                evaluated = loaded.network->evaluate(features, perspective);
            require(evaluated.ok(),
                    fields[1] + " evaluation rejected: "
                      + std::string(productive_evaluate_error_name(evaluated.error)));
            emit_trace(fields[1], identity, features, perspective, evaluated.trace);
        }
        ++records;
    }
    require(std::cin.eof(), "stdin read failed");
    require(records > 0, "protocol did not exercise a physical record");
    std::cout << "SUMMARY\trecords=" << records << "\tevaluations=" << records * 2
              << "\ttransformer_lanes=" << ProductiveTransformerLanes
              << "\tcontainer_bytes=" << ProductiveFileBytes
              << "\ttraining_admissible=false\tg12_closed=false";
    if (backend == "simd")
    {
        require(physicalTotals.physicalEvaluations == records * 2,
                "physical SIMD evaluation count drifted");
        require(physicalTotals.traceValues == records * 2 * TraceValuesPerEvaluation,
                "physical SIMD trace count drifted");
        std::cout << "\tbackend=" << simd_backend_name()
                  << "\tscalar_simd_evaluations=" << physicalTotals.physicalEvaluations
                  << "\ttrace_values=" << physicalTotals.traceValues;
    }
    std::cout << '\n';
    return EXIT_SUCCESS;
}
