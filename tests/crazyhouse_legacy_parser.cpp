/*
  Fixture for the standalone legacy Crazyhouse NNUE container parser. It
  certifies exact registered bytes and fail-closed structural rejection only.
  It does not certify feature indices, numerical evaluation, search or strength.
*/

#include <cstdlib>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "nnue/crazyhouse_legacy_network.h"

namespace {

using Network = Stockfish::Eval::NNUE::LegacyCrazyhouseNetworkV1;
using Status  = Network::LoadStatus;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_legacy_parser: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

std::vector<unsigned char> read_exact(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    require(bool(stream), "cannot open registered network fixture");
    const std::streamoff end = stream.tellg();
    require(end == std::streamoff(Network::FileBytes), "fixture size mismatch before parser");
    stream.seekg(0);

    std::vector<unsigned char> bytes(static_cast<std::size_t>(end));
    stream.read(reinterpret_cast<char*>(bytes.data()), end);
    require(stream.gcount() == end && bool(stream), "fixture read was incomplete");
    return bytes;
}

void put_u32(std::vector<unsigned char>& bytes, std::size_t offset, std::uint32_t value) {
    require(offset + 4 <= bytes.size(), "test mutation offset is outside fixture");
    for (int byte = 0; byte < 4; ++byte)
        bytes[offset + std::size_t(byte)] = static_cast<unsigned char>(value >> (8 * byte));
}

void expect_status(Network&                   network,
                   const std::vector<unsigned char>& bytes,
                   Status                     expected,
                   const std::string&          label) {
    const auto result = network.load_bytes(bytes.data(), bytes.size());
    require(result.status == expected,
            label + " returned " + std::string(Network::status_name(result.status)) + " instead of "
              + std::string(Network::status_name(expected)));
    require(!network.loaded(), label + " retained a usable network after failure");
    require(!result.message.empty(), label + " returned an empty diagnostic");
}

void verify_constants() {
    require(Network::FileVersion == 0x7af32f20U, "file version constant drifted");
    require(Network::NetworkHash == 0x3c103e72U, "network hash constant drifted");
    require(Network::TransformerHash == 0x5f2348b8U, "transformer hash constant drifted");
    require(Network::ArchitectureHash == 0x633376caU, "architecture hash constant drifted");
    require(Network::FeatureDimensions == 55296, "feature dimensions drifted");
    require(Network::TransformerDimensions == 512, "transformer dimensions drifted");
    require(Network::PsqtBuckets == 8 && Network::LayerStacks == 8,
            "bucket or stack count drifted");
    require(Network::TransformerSectionBytes == 58393604,
            "transformer section byte equation drifted");
    require(Network::LayerParameterBytes == 17636, "layer parameter byte equation drifted");
    require(Network::LayerStackBytes == 17640, "layer stack byte equation drifted");
    require(Network::FileBytes == 58534811, "complete file byte equation drifted");
}

void verify_positive(const std::filesystem::path& path, const std::vector<unsigned char>& bytes) {
    Network network;

    auto result = network.load_file(path);
    require(result.status == Status::Success, "registered file path did not load: " + result.message);
    require(network.loaded(), "successful file load did not commit the backend");
    require(network.description() == Network::RegisteredDescription,
            "loaded description differs from registered description");
    require(network.artifact_sha256() == Network::RegisteredSha256,
            "loaded identity differs from registered SHA-256");

    result = network.load_bytes(bytes.data(), bytes.size());
    require(result.status == Status::Success, "byte-identical alias did not load: " + result.message);
    require(network.loaded(), "byte-identical alias did not commit the backend");

    result = network.load_file(path, Network::RegisteredSha256);
    require(result.status == Status::Success && network.loaded(),
            "explicit registered file digest did not load");

    result = network.load_bytes(bytes.data(), bytes.size(), Network::RegisteredSha256);
    require(result.status == Status::Success && network.loaded(), "repeat exact load was not deterministic");
}

void verify_negative(const std::filesystem::path& path, std::vector<unsigned char> bytes) {
    Network network;

    auto result = network.load_file(path.string() + ".definitely-missing");
    require(result.status == Status::MissingFile, "missing path was not classified as MissingFile");
    require(!network.loaded() && !result.message.empty(), "missing path did not fail closed");

    result = network.load_file(path, std::string(64, 'A'));
    require(result.status == Status::DigestMismatch && !network.loaded(),
            "uppercase expected digest did not fail closed");
    result = network.load_bytes(bytes.data(), bytes.size(), std::string(64, '0'));
    require(result.status == Status::DigestMismatch && !network.loaded(),
            "wrong explicit digest did not fail closed");

    std::vector<unsigned char> shortBytes(bytes.begin(), bytes.end() - 1);
    expect_status(network, shortBytes, Status::TruncatedFile, "truncated file");

    bytes.push_back(0);
    expect_status(network, bytes, Status::OversizedFile, "oversized file");
    bytes.pop_back();

    const auto mutate_u32 = [&](std::size_t offset, std::uint32_t value, Status status,
                                const std::string& label) {
        unsigned char saved[4];
        for (int byte = 0; byte < 4; ++byte)
            saved[byte] = bytes[offset + std::size_t(byte)];
        put_u32(bytes, offset, value);
        expect_status(network, bytes, status, label);
        for (int byte = 0; byte < 4; ++byte)
            bytes[offset + std::size_t(byte)] = saved[byte];
    };

    mutate_u32(0, Network::FileVersion ^ 1U, Status::VersionMismatch, "version mutation");
    mutate_u32(4, Network::NetworkHash ^ 1U, Status::NetworkHashMismatch,
               "network-hash mutation");
    mutate_u32(8, 74U, Status::DescriptionLengthMismatch, "description-length mutation");

    bytes[12] ^= 1U;
    expect_status(network, bytes, Status::DescriptionMismatch, "description mutation");
    bytes[12] ^= 1U;

    mutate_u32(87, Network::TransformerHash ^ 1U, Status::TransformerHashMismatch,
               "transformer-hash mutation");
    mutate_u32(58393691, Network::ArchitectureHash ^ 1U, Status::ArchitectureHashMismatch,
               "first architecture-hash mutation");
    mutate_u32(58517171, Network::ArchitectureHash ^ 1U, Status::ArchitectureHashMismatch,
               "last architecture-hash mutation");

    bytes[91] ^= 1U;
    expect_status(network, bytes, Status::DigestMismatch, "transformer-parameter mutation");
    bytes[91] ^= 1U;

    result = network.load_bytes(bytes.data(), bytes.size());
    require(result.status == Status::Success && network.loaded(),
            "valid bytes did not recover after negative replacements");
    bytes[91] ^= 1U;
    expect_status(network, bytes, Status::DigestMismatch,
                  "failed replacement after a successful load");
}

}  // namespace

int main(int argc, char** argv) {
    require(argc == 2, "usage: crazyhouse_legacy_parser <registered-network.nnue>");
    verify_constants();

    const std::filesystem::path path(argv[1]);
    const auto                  bytes = read_exact(path);
    verify_positive(path, bytes);
    verify_negative(path, bytes);

    std::cout << "PASS crazyhouse_legacy_parser exact=PASS alias=PASS repeat=PASS "
                 "negative=13 explicit_digest=PASS failure_invalidation=PASS parser_only=PASS\n";
    return EXIT_SUCCESS;
}
