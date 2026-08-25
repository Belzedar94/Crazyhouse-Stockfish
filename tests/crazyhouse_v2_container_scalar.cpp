/*
  Fixture protocol for the Crazyhouse V2 scalar probe container. This target
  is deliberately separate from, and unreachable through, the normal engine.
*/

#include <algorithm>
#include <array>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

#include "nnue/crazyhouse_v2_features.h"
#include "nnue/crazyhouse_v2_physical.h"
#include "nnue/crazyhouse_v2_probe.h"

namespace {

using namespace Stockfish;
using namespace Stockfish::Eval::NNUE::CrazyhouseV2;
using Inventory = ScalarFeatureInventoryV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_container_scalar: " << message << '\n';
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

std::string join_lanes(const ScalarProbeEvaluationResult& evaluation) {
    std::ostringstream output;
    for (std::size_t lane = 0; lane < evaluation.lanes.size(); ++lane)
    {
        if (lane)
            output << ',';
        output << evaluation.lanes[lane];
    }
    return output.str();
}

void require_zero_lanes(const ScalarProbeEvaluationResult& evaluation, const std::string& message) {
    require(std::all_of(evaluation.lanes.begin(), evaluation.lanes.end(),
                        [](std::int32_t value) { return value == 0; }),
            message);
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string networkPath;
    std::string expectedError;
    for (int index = 1; index < argc; ++index)
    {
        const std::string argument = argv[index];
        if (argument == "--network" && index + 1 < argc)
            networkPath = argv[++index];
        else if (argument == "--expect-network-error" && index + 1 < argc)
            expectedError = argv[++index];
        else
            fail("unknown or incomplete command-line argument");
    }
    require(!networkPath.empty(), "--network is required");

    ScalarProbeNetworkV1 defaultNetwork;
    Inventory::Result    emptyFeatures;
    emptyFeatures.status         = Inventory::Status::SUCCESS;
    const auto defaultEvaluation = defaultNetwork.evaluate(emptyFeatures, WHITE);
    require(defaultEvaluation.error == ScalarProbeEvaluateError::NETWORK_NOT_READY,
            "default network became evaluable");
    require_zero_lanes(defaultEvaluation, "default network exposed partial output");

    const std::vector<Byte>     bytes  = read_file(networkPath);
    const ScalarProbeLoadResult loaded = load_scalar_probe_v1(bytes.data(), bytes.size());
    if (!expectedError.empty())
    {
        require(!loaded.ok(), "adversarial network was accepted");
        const std::string observed(scalar_probe_load_error_name(loaded.error));
        require(observed == expectedError,
                "expected " + expectedError + " but observed " + observed);
        require(!loaded.network.ready(), "failed parser exposed a ready network");
        const auto evaluation = loaded.network.evaluate(emptyFeatures, WHITE);
        require(evaluation.error == ScalarProbeEvaluateError::NETWORK_NOT_READY,
                "failed parser exposed an evaluable network");
        require_zero_lanes(evaluation, "failed parser exposed partial output");
        std::cout << "REJECT\tnetwork\t" << observed << "\tready=false\n";
        return EXIT_SUCCESS;
    }

    require(loaded.ok(),
            "network rejected: " + std::string(scalar_probe_load_error_name(loaded.error)));
    std::string line;
    std::size_t valid = 0;
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
        const ScalarProbeEvaluationResult white = loaded.network.evaluate(features, WHITE);
        const ScalarProbeEvaluationResult black = loaded.network.evaluate(features, BLACK);
        require(white.ok(), fields[1] + " White evaluation rejected: "
                              + std::string(scalar_probe_evaluate_error_name(white.error)));
        require(black.ok(), fields[1] + " Black evaluation rejected: "
                              + std::string(scalar_probe_evaluate_error_name(black.error)));
        std::cout << "OK\t" << fields[1] << '\t' << digest_hex(decoded.record.positionIdentity)
                  << '\t' << join_rows(features, WHITE) << '\t' << join_rows(features, BLACK)
                  << '\t' << join_lanes(white) << '\t' << join_lanes(black) << '\n';
        ++valid;
    }
    require(std::cin.eof(), "stdin read failed");
    require(valid > 0, "protocol did not exercise a physical record");
    std::cout << "SUMMARY\tvalid=" << valid << "\tperspectives=" << valid * 2
              << "\tlane_values=" << valid * 2 * ScalarProbeOutputLanes
              << "\tdimensions=" << Inventory::Dimensions << "\tlanes=" << ScalarProbeOutputLanes
              << '\n';
    return EXIT_SUCCESS;
}
