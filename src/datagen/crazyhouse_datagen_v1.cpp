/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.

  This is the separate physical DATAGEN artifact. It deliberately does not
  expose UCI and it never stores NNUE feature rows as canonical data.
*/

#include "crazyhouse_datagen_v1.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cctype>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <exception>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

#ifdef _WIN32
    #include <fcntl.h>
    #include <io.h>
    #include <sys/stat.h>
    #include <windows.h>
#else
    #include <fcntl.h>
    #include <unistd.h>
#endif

#include "../attacks.h"
#include "../crazyhouse_profile.h"
#include "../engine.h"
#include "../movegen.h"
#include "../position.h"
#include "../score.h"
#include "../search.h"
#include "../thread.h"
#include "../tt.h"
#include "../uci.h"

#ifndef DATAGEN_SOURCE_COMMIT
    #define DATAGEN_SOURCE_COMMIT ""
#endif
#ifndef DATAGEN_SOURCE_TREE
    #define DATAGEN_SOURCE_TREE ""
#endif
#ifndef DATAGEN_SRC_TREE
    #define DATAGEN_SRC_TREE ""
#endif
#ifndef DATAGEN_SOURCE_DIRTY
    #define DATAGEN_SOURCE_DIRTY 1
#endif
#ifndef DATAGEN_BUILD_RECIPE_SHA256
    #define DATAGEN_BUILD_RECIPE_SHA256 ""
#endif
#ifndef DATAGEN_TOOLCHAIN_SHA256
    #define DATAGEN_TOOLCHAIN_SHA256 ""
#endif
#ifndef DATAGEN_TOOLCHAIN_IDENTITY
    #define DATAGEN_TOOLCHAIN_IDENTITY ""
#endif

namespace Stockfish::CrazyhouseDatagen {
namespace {

using Byte     = std::uint8_t;
using Digest   = std::array<Byte, 32>;
using IdBytes  = std::array<Byte, 16>;
using Record   = std::array<Byte, 256>;
using ByteList = std::vector<Byte>;

constexpr std::size_t HeaderBytes = 256;
constexpr std::size_t RecordBytes = 256;
constexpr std::size_t FooterBytes = 128;
constexpr Byte        NoSquare    = 255;

constexpr std::string_view CapabilityContractSha256 =
  "dc6af06c3d18fb2ff06e27e35ab691e35555ef03a5948b23cb2a198e6b89eb96";
constexpr std::string_view PhysicalSchemaSha256 =
  "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55";
constexpr std::string_view SelectionPolicyG0Sha256 =
  "e5b39bd15c78b00ce0f6acc01da49103e71685c95f7b6fbde09334933d8bfb18";
constexpr std::string_view SearchSettingsSha256 =
  "f6eadbf76d6c37756f4dca4a3a2b0893a9a0ec7eaf164f309f54493185ff25d6";
constexpr std::string_view SelfplayCapabilityContractSha256 =
  "482fd210ed4009aaf145c34d44b18fc05f99b11969e69dd9f69d9907204c87dd";
constexpr std::string_view DatagenBundleSchemaSha256 =
  "27138d4049e2c6b2ad75f85d05fc799442cbf9f91a6e4a1c27c546c2eb9ecf5b";
constexpr std::string_view SelfplaySelectionPolicySha256 =
  "fc67430cb09eb28531889a6b8f99a02f4b033c5bd71cbef7d2e9add8a7d573c6";
constexpr std::string_view SelfplayG0BookSha256 =
  "f99f8211316813924e52fb13fbb65a5bc27dcd585e2e32a86d90db0d113fd2f6";
constexpr std::uint64_t SelfplayG0BookBytes = 158;
constexpr std::string_view ProductionCapabilityContractSha256 =
  "96abf35a3a526d3cecdf4a6a3b55ff15b9ce6f1b644fa38375af65242d113357";
constexpr std::string_view ProductionSelectionPolicySha256 =
  "475fd0fb9a929e964ff32357031a18d33ecc2543e8681cc73068858c10db3014";
constexpr std::string_view ProductionBookSha256 =
  "1371e87ce3bdb875d922ad0061c96c4a123bc571daf4ae2bff24e5176287f0fa";
constexpr std::string_view ProductionFeatureContractSha256 =
  "1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6";
constexpr std::uint64_t    ProductionBookBytes               = 39922;
constexpr std::size_t      ProductionBookRoots               = 599;
constexpr std::uint32_t    ProductionOpenBenchProtocol       = 41;
constexpr std::uint32_t    ProductionThreads                 = 1;
constexpr std::uint32_t    ProductionHashMb                  = 128;
constexpr Depth            ProductionDepthCap                = 64;
constexpr std::uint64_t    ProductionNodes                   = 16384;
constexpr std::uint32_t    ProductionMaxGamePly              = 512;
constexpr std::uint32_t    ProductionExplorationPlies        = 8;
constexpr std::uint32_t    ProductionExplorationMultiPv      = 4;
constexpr int              ProductionExplorationMaxScoreDiff = 256;
constexpr std::uint64_t    ProductionValidationThreshold     = std::uint64_t(1) << 61;
constexpr std::string_view RegisteredLegacyNetworkSha256 =
  "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43";
constexpr std::uint64_t RegisteredLegacyNetworkBytes = 58534811;

constexpr char PositionDomain[] = "Crazyhouse-Stockfish physical repetition identity v1\0";
constexpr char HistoryInitialDomain[] = "Crazyhouse-Stockfish physical history initial v1\0";
constexpr char HistoryStepDomain[] = "Crazyhouse-Stockfish physical history step v1\0";
constexpr char PartitionDomain[] = "Crazyhouse-Stockfish physical trajectory split v1\0";
constexpr char ExplorationDomain[] = "Crazyhouse-Stockfish production exploration choice v1\0";

class DatagenError: public std::runtime_error {
   public:
    using std::runtime_error::runtime_error;
};

void require(bool condition, std::string message) {
    if (!condition)
        throw DatagenError(std::move(message));
}

constexpr std::uint32_t rotate_right(std::uint32_t value, unsigned shift) {
    return (value >> shift) | (value << (32U - shift));
}

class Sha256 {
   public:
    Sha256() = default;

    void update(const Byte* data, std::size_t size) {
        totalBytes += size;
        while (size != 0)
        {
            const std::size_t amount = std::min(size, block.size() - buffered);
            std::copy_n(data, amount, block.begin() + static_cast<std::ptrdiff_t>(buffered));
            buffered += amount;
            data += amount;
            size -= amount;
            if (buffered == block.size())
            {
                transform(block.data());
                buffered = 0;
            }
        }
    }

    void update(const ByteList& data) { update(data.data(), data.size()); }

    void update(std::string_view data) {
        update(reinterpret_cast<const Byte*>(data.data()), data.size());
    }

    Digest final() const {
        Sha256 copy = *this;
        const std::uint64_t bitLength = copy.totalBytes * 8U;
        copy.block[copy.buffered++]    = 0x80;
        if (copy.buffered > 56)
        {
            std::fill(copy.block.begin() + static_cast<std::ptrdiff_t>(copy.buffered),
                      copy.block.end(), Byte{0});
            copy.transform(copy.block.data());
            copy.buffered = 0;
        }
        std::fill(copy.block.begin() + static_cast<std::ptrdiff_t>(copy.buffered),
                  copy.block.begin() + 56, Byte{0});
        for (unsigned index = 0; index < 8; ++index)
            copy.block[63 - index] = Byte(bitLength >> (8U * index));
        copy.transform(copy.block.data());

        Digest output{};
        for (std::size_t word = 0; word < state.size(); ++word)
            for (unsigned index = 0; index < 4; ++index)
                output[word * 4 + index] = Byte(copy.state[word] >> (24U - 8U * index));
        return output;
    }

   private:
    void transform(const Byte* data) {
        static constexpr std::array<std::uint32_t, 64> Constants = {
          0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U,
          0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
          0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U,
          0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
          0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
          0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
          0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
          0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
          0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU,
          0x5b9cca4fU, 0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
          0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U};

        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16; ++index)
            words[index] = (std::uint32_t(data[index * 4]) << 24U)
                         | (std::uint32_t(data[index * 4 + 1]) << 16U)
                         | (std::uint32_t(data[index * 4 + 2]) << 8U)
                         | std::uint32_t(data[index * 4 + 3]);
        for (std::size_t index = 16; index < words.size(); ++index)
        {
            const std::uint32_t s0 = rotate_right(words[index - 15], 7)
                                   ^ rotate_right(words[index - 15], 18)
                                   ^ (words[index - 15] >> 3U);
            const std::uint32_t s1 = rotate_right(words[index - 2], 17)
                                   ^ rotate_right(words[index - 2], 19)
                                   ^ (words[index - 2] >> 10U);
            words[index] = words[index - 16] + s0 + words[index - 7] + s1;
        }

        std::uint32_t a = state[0];
        std::uint32_t b = state[1];
        std::uint32_t c = state[2];
        std::uint32_t d = state[3];
        std::uint32_t e = state[4];
        std::uint32_t f = state[5];
        std::uint32_t g = state[6];
        std::uint32_t h = state[7];
        for (std::size_t index = 0; index < words.size(); ++index)
        {
            const std::uint32_t sum1 = rotate_right(e, 6) ^ rotate_right(e, 11)
                                     ^ rotate_right(e, 25);
            const std::uint32_t choose = (e & f) ^ (~e & g);
            const std::uint32_t temp1  = h + sum1 + choose + Constants[index] + words[index];
            const std::uint32_t sum0 = rotate_right(a, 2) ^ rotate_right(a, 13)
                                     ^ rotate_right(a, 22);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t temp2    = sum0 + majority;
            h = g;
            g = f;
            f = e;
            e = d + temp1;
            d = c;
            c = b;
            b = a;
            a = temp1 + temp2;
        }
        state[0] += a;
        state[1] += b;
        state[2] += c;
        state[3] += d;
        state[4] += e;
        state[5] += f;
        state[6] += g;
        state[7] += h;
    }

    std::array<std::uint32_t, 8> state = {0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U,
                                          0xa54ff53aU, 0x510e527fU, 0x9b05688cU,
                                          0x1f83d9abU, 0x5be0cd19U};
    std::array<Byte, 64>          block{};
    std::size_t                   buffered   = 0;
    std::uint64_t                 totalBytes = 0;
};

Digest sha256(const Byte* data, std::size_t size) {
    Sha256 hash;
    hash.update(data, size);
    return hash.final();
}

Digest sha256(const ByteList& data) { return sha256(data.data(), data.size()); }

Digest sha256(std::string_view data) {
    return sha256(reinterpret_cast<const Byte*>(data.data()), data.size());
}

std::string hex(const Digest& digest) {
    constexpr char Digits[] = "0123456789abcdef";
    std::string    output;
    output.reserve(digest.size() * 2);
    for (Byte byte : digest)
    {
        output.push_back(Digits[byte >> 4]);
        output.push_back(Digits[byte & 0x0F]);
    }
    return output;
}

std::uint32_t crc32c(const Byte* data, std::size_t size) {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < size; ++index)
    {
        crc ^= data[index];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1U) ^ ((crc & 1U) != 0 ? 0x82F63B78U : 0U);
    }
    return crc ^ 0xFFFFFFFFU;
}

bool lowercase_hex(std::string_view value, std::size_t width) {
    if (value.size() != width)
        return false;
    return std::all_of(value.begin(), value.end(), [](unsigned char ch) {
        return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
    });
}

ByteList read_file(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    require(input.good(), "cannot open file: " + path.string());
    input.seekg(0, std::ios::end);
    const std::streamoff length = input.tellg();
    require(length >= 0, "cannot determine file length: " + path.string());
    require(static_cast<std::uint64_t>(length)
              <= static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()),
            "file is too large for this build: " + path.string());
    ByteList output(static_cast<std::size_t>(length));
    input.seekg(0, std::ios::beg);
    if (!output.empty())
        input.read(reinterpret_cast<char*>(output.data()),
                   static_cast<std::streamsize>(output.size()));
    require(input.good() || input.eof(), "cannot read file: " + path.string());
    require(static_cast<std::size_t>(input.gcount()) == output.size(),
            "short read: " + path.string());
    return output;
}

std::string json_string(std::string_view value) {
    constexpr char Digits[] = "0123456789abcdef";
    std::string    output;
    output.reserve(value.size() + 2);
    output.push_back('"');
    for (unsigned char ch : value)
    {
        switch (ch)
        {
        case '"': output += "\\\""; break;
        case '\\': output += "\\\\"; break;
        case '\b': output += "\\b"; break;
        case '\f': output += "\\f"; break;
        case '\n': output += "\\n"; break;
        case '\r': output += "\\r"; break;
        case '\t': output += "\\t"; break;
        default:
            if (ch < 0x20)
            {
                output += "\\u00";
                output.push_back(Digits[ch >> 4]);
                output.push_back(Digits[ch & 0x0F]);
            }
            else
                output.push_back(char(ch));
        }
    }
    output.push_back('"');
    return output;
}

std::vector<std::string> split(std::string_view text, char delimiter) {
    std::vector<std::string> output;
    std::size_t              begin = 0;
    for (;;)
    {
        const std::size_t end = text.find(delimiter, begin);
        output.emplace_back(text.substr(begin, end == std::string_view::npos
                                                 ? text.size() - begin
                                                 : end - begin));
        if (end == std::string_view::npos)
            break;
        begin = end + 1;
    }
    return output;
}

template<typename Integer>
Integer parse_integer(std::string_view text, Integer minimum, Integer maximum, std::string_view label) {
    Integer value{};
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    require(!text.empty() && result.ec == std::errc()
              && result.ptr == text.data() + text.size() && value >= minimum && value <= maximum,
            "invalid " + std::string(label));
    return value;
}

Byte parse_square(std::string_view value) {
    require(value.size() == 2 && value[0] >= 'a' && value[0] <= 'h' && value[1] >= '1'
              && value[1] <= '8',
            "invalid square: " + std::string(value));
    return Byte((value[1] - '1') * 8 + value[0] - 'a');
}

template<typename Integer>
void put_le(Byte* output, Integer value) {
    using Unsigned = std::make_unsigned_t<Integer>;
    Unsigned data  = static_cast<Unsigned>(value);
    for (std::size_t index = 0; index < sizeof(Integer); ++index)
        output[index] = Byte(data >> (8U * index));
}

template<typename Integer>
Integer get_le(const Byte* input) {
    using Unsigned = std::make_unsigned_t<Integer>;
    Unsigned value = 0;
    for (std::size_t index = 0; index < sizeof(Integer); ++index)
        value |= Unsigned(input[index]) << (8U * index);
    return static_cast<Integer>(value);
}

IdBytes parse_uuid(std::string_view text, std::string_view label) {
    require(text.size() == 36 && text[8] == '-' && text[13] == '-' && text[18] == '-'
              && text[23] == '-',
            "invalid " + std::string(label));
    std::string compact;
    compact.reserve(32);
    for (char ch : text)
        if (ch != '-')
            compact.push_back(ch);
    require(lowercase_hex(compact, 32), "invalid " + std::string(label));
    IdBytes output{};
    for (std::size_t index = 0; index < output.size(); ++index)
    {
        auto nibble = [](char ch) -> Byte { return Byte(ch <= '9' ? ch - '0' : ch - 'a' + 10); };
        output[index] = Byte((nibble(compact[index * 2]) << 4) | nibble(compact[index * 2 + 1]));
    }
    require(std::any_of(output.begin(), output.end(), [](Byte byte) { return byte != 0; }),
            "zero " + std::string(label));
    return output;
}

std::string uuid_text(const IdBytes& id) {
    constexpr char Digits[] = "0123456789abcdef";
    std::string    output;
    output.reserve(36);
    for (std::size_t index = 0; index < id.size(); ++index)
    {
        if (index == 4 || index == 6 || index == 8 || index == 10)
            output.push_back('-');
        output.push_back(Digits[id[index] >> 4]);
        output.push_back(Digits[id[index] & 0x0F]);
    }
    return output;
}

void append(ByteList& output, const Byte* data, std::size_t size) {
    output.insert(output.end(), data, data + size);
}

template<std::size_t Size>
void append(ByteList& output, const std::array<Byte, Size>& data) {
    append(output, data.data(), data.size());
}

void append(ByteList& output, std::string_view data) {
    append(output, reinterpret_cast<const Byte*>(data.data()), data.size());
}

IdBytes derive_selfplay_id(std::string_view kind,
                           const IdBytes&  campaign,
                           std::uint64_t   chunkIndex,
                           std::uint64_t   candidateIndex) {
    constexpr char Domain[] = "Crazyhouse-Stockfish selfplay deterministic identity v1\0";
    ByteList       payload;
    append(payload, std::string_view(Domain, sizeof(Domain) - 1));
    append(payload, kind);
    payload.push_back(0);
    append(payload, campaign);
    std::array<Byte, 16> indices{};
    put_le<std::uint64_t>(indices.data(), chunkIndex);
    put_le<std::uint64_t>(indices.data() + 8, candidateIndex);
    append(payload, indices);
    const Digest digest = sha256(payload);
    IdBytes      output{};
    std::copy_n(digest.begin(), output.size(), output.begin());
    // Use RFC 4122 variant/version bits for a familiar textual identity. The
    // bytes remain SHA-256-derived; this is not UUIDv5/SHA-1.
    output[6] = Byte((output[6] & 0x0F) | 0x50);
    output[8] = Byte((output[8] & 0x3F) | 0x80);
    return output;
}

std::string derive_selfplay_challenge(const IdBytes& campaign,
                                      const IdBytes& chunk,
                                      std::uint64_t  assignedSeed,
                                      const Digest& artifactDigest) {
    constexpr char Domain[] = "Crazyhouse-Stockfish selfplay capability challenge v1\0";
    ByteList       payload;
    append(payload, std::string_view(Domain, sizeof(Domain) - 1));
    append(payload, campaign);
    append(payload, chunk);
    std::array<Byte, 8> seed{};
    put_le<std::uint64_t>(seed.data(), assignedSeed);
    append(payload, seed);
    append(payload, artifactDigest);
    const Digest digest = sha256(payload);
    return hex(digest).substr(0, 32);
}

struct ArtifactIdentity {
    std::filesystem::path path;
    std::uint64_t         bytes = 0;
    Digest                digest{};
};

ArtifactIdentity identify_artifact(const char* argv0) {
    std::error_code ec;
    auto            path = std::filesystem::absolute(std::filesystem::path(argv0), ec);
    require(!ec, "cannot resolve producer executable path");
    path = std::filesystem::weakly_canonical(path, ec);
    require(!ec && std::filesystem::is_regular_file(path), "producer executable is not a file");
    const ByteList bytes = read_file(path);
    require(!bytes.empty(), "producer executable is empty");
    return ArtifactIdentity{path, static_cast<std::uint64_t>(bytes.size()), sha256(bytes)};
}

void validate_compiled_identity() {
    require(lowercase_hex(DATAGEN_SOURCE_COMMIT, 40), "compiled source commit is invalid");
    require(lowercase_hex(DATAGEN_SOURCE_TREE, 40), "compiled source tree is invalid");
    require(lowercase_hex(DATAGEN_SRC_TREE, 40), "compiled src tree is invalid");
    require(lowercase_hex(DATAGEN_BUILD_RECIPE_SHA256, 64), "compiled build recipe digest is invalid");
    require(lowercase_hex(DATAGEN_TOOLCHAIN_SHA256, 64), "compiled toolchain digest is invalid");
    require(std::string_view(DATAGEN_TOOLCHAIN_IDENTITY).size() > 0,
            "compiled toolchain identity is empty");
    require(DATAGEN_SOURCE_DIRTY == 0 || DATAGEN_SOURCE_DIRTY == 1,
            "compiled dirty flag is invalid");
}

std::string capability_response(const ArtifactIdentity& artifact, std::string_view challenge) {
    require(lowercase_hex(challenge, 32), "capability challenge must be 32 lowercase hex characters");
    validate_compiled_identity();
    std::ostringstream output;
    output << "{\"artifact_bytes\":" << artifact.bytes
           << ",\"artifact_role\":\"crazyhouse-physical-datagen\""
           << ",\"artifact_sha256\":" << json_string(hex(artifact.digest))
           << ",\"atomic_rename\":true"
           << ",\"build_recipe_sha256\":" << json_string(DATAGEN_BUILD_RECIPE_SHA256)
           << ",\"byte_order\":\"little-endian\""
           << ",\"canonical_source\":\"physical-state-not-nnue-features\""
           << ",\"capability_contract_sha256\":" << json_string(CapabilityContractSha256)
           << ",\"challenge\":" << json_string(challenge)
           << ",\"crc32c\":true"
           << ",\"footer_bytes\":128"
           << ",\"fsync\":true"
           << ",\"header_bytes\":256"
           << ",\"kill_retry_unique_chunk_id\":true"
           << ",\"label_result\":\"absolute-white-and-side-to-move\""
           << ",\"partial_quarantine\":true"
           << ",\"physical_schema_id\":\"crazyhouse-physical-v1\""
           << ",\"physical_schema_sha256\":" << json_string(PhysicalSchemaSha256)
           << ",\"production_generation_authorized\":true"
           << ",\"project\":\"Crazyhouse-Stockfish\""
           << ",\"record_bytes\":256"
           << ",\"rule_profile_id\":" << json_string(CrazyhouseProfile::Id)
           << ",\"rule_profile_sha256\":" << json_string(CrazyhouseProfile::Sha256)
           << ",\"schema\":\"crazyhouse-datagen-capability-response/v1\""
           << ",\"sha256\":true"
           << ",\"source_commit\":" << json_string(DATAGEN_SOURCE_COMMIT)
           << ",\"source_dirty\":" << (DATAGEN_SOURCE_DIRTY ? "true" : "false")
           << ",\"source_tree\":" << json_string(DATAGEN_SOURCE_TREE)
           << ",\"src_tree\":" << json_string(DATAGEN_SRC_TREE)
           << ",\"supported_claim_policies\":[0,1]"
           << ",\"supported_move_kinds\":[0,1,2,3,4,5]"
           << ",\"supported_record_flags\":[1,2,4,8,16,32,64]"
           << ",\"supported_terminal_reasons\":[0,1,2,3,4,5,6]"
           << ",\"teacher_score_perspective\":\"side-to-move\""
           << ",\"toolchain_sha256\":" << json_string(DATAGEN_TOOLCHAIN_SHA256)
           << ",\"transaction\":\"exclusive-partial-verify-atomic-rename\""
           << ",\"variant\":\"crazyhouse\"}\n";
    return output.str();
}

std::string selfplay_capability_response(const ArtifactIdentity& artifact,
                                         std::string_view          challenge) {
    require(lowercase_hex(challenge, 32),
            "self-play capability challenge must be 32 lowercase hex characters");
    validate_compiled_identity();
    std::ostringstream output;
    output << "{\"artifact_bytes\":" << artifact.bytes
           << ",\"artifact_role\":\"crazyhouse-physical-datagen-selfplay-v1\""
           << ",\"artifact_sha256\":" << json_string(hex(artifact.digest))
           << ",\"build_recipe_sha256\":" << json_string(DATAGEN_BUILD_RECIPE_SHA256)
           << ",\"bundle_schema_sha256\":" << json_string(DatagenBundleSchemaSha256)
           << ",\"capability_contract_sha256\":"
           << json_string(SelfplayCapabilityContractSha256)
           << ",\"challenge\":" << json_string(challenge)
           << ",\"command\":\"crazyhouse_generate_physical_v1\""
           << ",\"complete_trajectory_only\":true"
           << ",\"count_unit\":\"physical-records\""
           << ",\"max_threads\":1"
           << ",\"normal_engine_exposes_command\":false"
           << ",\"physical_schema_id\":\"crazyhouse-physical-v1\""
           << ",\"physical_schema_sha256\":" << json_string(PhysicalSchemaSha256)
           << ",\"project\":\"Crazyhouse-Stockfish\""
           << ",\"registered_network_bytes\":" << RegisteredLegacyNetworkBytes
           << ",\"registered_network_sha256\":" << json_string(RegisteredLegacyNetworkSha256)
           << ",\"rule_profile_id\":" << json_string(CrazyhouseProfile::Id)
           << ",\"rule_profile_sha256\":" << json_string(CrazyhouseProfile::Sha256)
           << ",\"schema\":\"crazyhouse-datagen-selfplay-capability-response/v1\""
           << ",\"search_backend\":\"product-crazyhouse-search\""
           << ",\"search_score_bound\":\"exact-only\""
           << ",\"source_commit\":" << json_string(DATAGEN_SOURCE_COMMIT)
           << ",\"source_dirty\":" << (DATAGEN_SOURCE_DIRTY ? "true" : "false")
           << ",\"source_tree\":" << json_string(DATAGEN_SOURCE_TREE)
           << ",\"src_tree\":" << json_string(DATAGEN_SRC_TREE)
           << ",\"teacher_score_kinds\":[\"centipawn\",\"mate-plies\"]"
           << ",\"teacher_score_perspective\":\"side-to-move\""
           << ",\"toolchain_sha256\":" << json_string(DATAGEN_TOOLCHAIN_SHA256)
           << ",\"transaction\":\"exclusive-partial-verify-atomic-rename\""
           << ",\"variant\":\"crazyhouse\"}\n";
    return output.str();
}

std::string production_capability_response(const ArtifactIdentity& artifact,
                                           std::string_view        challenge) {
    require(lowercase_hex(challenge, 32),
            "production capability challenge must be 32 lowercase hex characters");
    validate_compiled_identity();
    std::ostringstream output;
    output << "{\"artifact_bytes\":" << artifact.bytes
           << ",\"artifact_role\":\"crazyhouse-physical-datagen-production-v1\""
           << ",\"artifact_sha256\":" << json_string(hex(artifact.digest))
           << ",\"build_recipe_sha256\":" << json_string(DATAGEN_BUILD_RECIPE_SHA256)
           << ",\"capability_contract_sha256\":" << json_string(ProductionCapabilityContractSha256)
           << ",\"challenge\":" << json_string(challenge)
           << ",\"command\":\"crazyhouse_generate_physical_production_v1\""
           << ",\"openbench_publication_protocol\":" << ProductionOpenBenchProtocol
           << ",\"physical_record_bytes\":" << RecordBytes
           << ",\"physical_schema_sha256\":" << json_string(PhysicalSchemaSha256)
           << ",\"producer_source_commit\":" << json_string(DATAGEN_SOURCE_COMMIT)
           << ",\"producer_source_dirty\":" << (DATAGEN_SOURCE_DIRTY ? "true" : "false")
           << ",\"producer_source_tree\":" << json_string(DATAGEN_SOURCE_TREE)
           << ",\"producer_src_tree\":" << json_string(DATAGEN_SRC_TREE)
           << ",\"production_generation_authorized\":" << (DATAGEN_SOURCE_DIRTY ? "false" : "true")
           << ",\"registered_network_bytes\":" << RegisteredLegacyNetworkBytes
           << ",\"registered_network_sha256\":" << json_string(RegisteredLegacyNetworkSha256)
           << ",\"rule_profile_id\":" << json_string(CrazyhouseProfile::Id)
           << ",\"rule_profile_sha256\":" << json_string(CrazyhouseProfile::Sha256)
           << ",\"schema\":\"crazyhouse-datagen-production-capability-response/v1\""
           << ",\"selection_policy_sha256\":" << json_string(ProductionSelectionPolicySha256)
           << ",\"toolchain_identity\":" << json_string(DATAGEN_TOOLCHAIN_IDENTITY)
           << ",\"toolchain_sha256\":" << json_string(DATAGEN_TOOLCHAIN_SHA256)
           << ",\"trajectory_partition_domain\":"
              "\"Crazyhouse-Stockfish physical trajectory split v1\\\\0\""
           << ",\"variant\":\"crazyhouse\"}\n";
    return output.str();
}

struct TeacherLabel {
    Byte          kind       = 0;
    std::int32_t  value      = 0;
    std::uint64_t nodes      = 0;
    std::uint16_t depth      = 0;
    std::uint16_t selDepth   = 0;
    std::uint32_t moveTimeMs = 0;
};

struct Trajectory {
    std::string              gameText;
    std::string              trajectoryText;
    IdBytes                  gameId{};
    IdBytes                  trajectoryId{};
    Byte                     claimPolicy = 0;
    std::int8_t              resultWhite = 0;
    Byte                     terminalReason = 0;
    bool                     nonstandardRoot = false;
    bool                     teacherUsedNetwork = false;
    std::string              rootFen;
    std::vector<std::string> moves;
    std::vector<std::optional<TeacherLabel>> labels;
};

struct TrajectoryCorpus {
    std::vector<Trajectory> trajectories;
    std::size_t             recordCount = 0;
};

TrajectoryCorpus parse_trajectory_corpus(const ByteList& bytes,
                                         std::size_t     expectedTrajectories,
                                         std::size_t     expectedRecords) {
    require(!bytes.empty(), "trajectory stream is empty");
    require(!(bytes.size() >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF),
            "trajectory stream has a BOM");
    require(std::find(bytes.begin(), bytes.end(), Byte{'\r'}) == bytes.end(),
            "trajectory stream must use LF line endings");
    require(bytes.back() == '\n' && (bytes.size() == 1 || bytes[bytes.size() - 2] != '\n'),
            "trajectory stream must end with exactly one LF");
    require(std::find(bytes.begin(), bytes.end(), Byte{0}) == bytes.end(),
            "trajectory stream contains NUL");
    const std::string text(reinterpret_cast<const char*>(bytes.data()), bytes.size() - 1);
    const auto        lines = split(text, '\n');
    require(lines.size() >= 3, "trajectory stream has too few lines");

    const auto header = split(lines.front(), '\t');
    require(header.size() == 3 && header[0] == "CRAZYHOUSE_TRAJECTORIES_V1",
            "trajectory header is invalid");
    const std::size_t declaredTrajectories =
      parse_integer<std::size_t>(header[1], 1, 1000000, "declared trajectory count");
    const std::size_t declaredRecords =
      parse_integer<std::size_t>(header[2], 1, 100000000, "declared record count");
    require(declaredTrajectories == expectedTrajectories
              && declaredRecords == expectedRecords,
            "trajectory header count does not match admission");

    const auto ending = split(lines.back(), '\t');
    require(ending.size() == 3 && ending[0] == "END",
            "trajectory end marker is invalid");
    require(parse_integer<std::size_t>(ending[1], 1, 1000000, "ending trajectory count")
                == declaredTrajectories
              && parse_integer<std::size_t>(ending[2], 1, 100000000, "ending record count")
                   == declaredRecords,
            "trajectory end count drifted");

    TrajectoryCorpus output;
    std::set<std::string> games;
    std::set<std::string> trajectories;
    for (std::size_t lineIndex = 1; lineIndex + 1 < lines.size(); ++lineIndex)
    {
        const auto fields = split(lines[lineIndex], '\t');
        require(fields.size() == 10 && fields[0] == "T",
                "malformed trajectory row " + std::to_string(lineIndex));
        Trajectory trajectory;
        trajectory.gameText       = fields[1];
        trajectory.trajectoryText = fields[2];
        trajectory.gameId         = parse_uuid(fields[1], "game UUID");
        trajectory.trajectoryId   = parse_uuid(fields[2], "trajectory UUID");
        require(games.insert(fields[1]).second, "duplicate game UUID");
        require(trajectories.insert(fields[2]).second, "duplicate trajectory UUID");
        trajectory.claimPolicy = parse_integer<Byte>(fields[3], 0, 1, "claim policy");
        trajectory.resultWhite = parse_integer<std::int8_t>(fields[4], -1, 1, "White result");
        trajectory.terminalReason = parse_integer<Byte>(fields[5], 1, 6, "terminal reason");
        const Byte nonstandard = parse_integer<Byte>(fields[6], 0, 1, "nonstandard-root flag");
        trajectory.nonstandardRoot = nonstandard != 0;
        trajectory.rootFen         = fields[7];
        require(split(trajectory.rootFen, ' ').size() == 6,
                "root FEN must contain six fields");
        trajectory.moves = fields[8] == "-" ? std::vector<std::string>{}
                                               : split(fields[8], ',');
        require(std::all_of(trajectory.moves.begin(), trajectory.moves.end(),
                            [](const std::string& move) { return !move.empty(); }),
                "empty move token");
        const auto scoreTokens = split(fields[9], ',');
        require(scoreTokens.size() == trajectory.moves.size() + 1,
                "teacher score count does not match physical records");
        for (std::size_t index = 0; index < scoreTokens.size(); ++index)
        {
            if (scoreTokens[index] == "-")
                trajectory.labels.emplace_back(std::nullopt);
            else
                trajectory.labels.emplace_back(TeacherLabel{
                  1,
                  parse_integer<std::int32_t>(scoreTokens[index],
                                              std::numeric_limits<std::int32_t>::min(),
                                              std::numeric_limits<std::int32_t>::max(),
                                              "teacher score"),
                  1024,
                  8,
                  10,
                  5});
            require(trajectory.labels.back().has_value() == (index < trajectory.moves.size()),
                    "teacher score/terminal framing mismatch");
        }
        require((trajectory.terminalReason == 1 && trajectory.resultWhite != 0)
                  || (trajectory.terminalReason == 5 && trajectory.resultWhite != 0)
                  || ((trajectory.terminalReason == 2 || trajectory.terminalReason == 3
                       || trajectory.terminalReason == 4 || trajectory.terminalReason == 6)
                      && trajectory.resultWhite == 0),
                "terminal reason/result contradiction");
        require(trajectory.terminalReason != 4 || trajectory.claimPolicy == 1,
                "threefold proxy requires immediate claim policy");
        require(trajectory.terminalReason != 3 || trajectory.claimPolicy == 0,
                "fivefold fixture must use core-only policy");
        output.recordCount += trajectory.moves.size() + 1;
        output.trajectories.push_back(std::move(trajectory));
    }
    require(output.trajectories.size() == declaredTrajectories
              && output.recordCount == declaredRecords,
            "parsed trajectory counts drifted");
    return output;
}

bool repository_relative_path(std::string_view value) {
    if (value.empty() || value.front() == '/' || value.find('\\') != std::string_view::npos
        || value.find(':') != std::string_view::npos)
        return false;
    for (const auto& component : split(value, '/'))
        if (component.empty() || component == "." || component == "..")
            return false;
    return true;
}

struct GenerationOptions {
    std::string           challenge;
    std::filesystem::path schemaPath;
    std::filesystem::path contractPath;
    std::filesystem::path inputPath;
    std::string           inputRepoPath;
    std::filesystem::path outputPath;
    std::string           artifactRepoPath;
    std::string           campaignText;
    std::string           chunkText;
    IdBytes               campaignId{};
    IdBytes               chunkId{};
    std::uint64_t         chunkIndex = 0;
    std::string           seed;
    std::size_t           expectedTrajectories = 0;
    std::size_t           expectedRecords = 0;
    std::string           openingKind;
    std::string           selectionPolicySha256;
    std::uint32_t         pauseAfterPartialMs = 0;
};

GenerationOptions parse_generation_options(int argc, char* argv[]) {
    require(argc >= 2 && std::string_view(argv[1]) == "--generate-trajectories-v1",
            "unknown producer operation");
    require((argc - 2) % 2 == 0, "generation options must be name/value pairs");
    const std::set<std::string> allowed = {
      "--artifact-repo-path",       "--campaign-id",       "--challenge",
      "--chunk-id",                 "--chunk-index",       "--contract",
      "--expected-records",         "--expected-trajectories",
      "--input",                    "--input-repo-path",   "--opening-kind",
      "--output",                   "--schema",            "--seed",
      "--selection-policy-sha256",  "--test-pause-after-partial-ms"};
    std::map<std::string, std::string> values;
    for (int index = 2; index < argc; index += 2)
    {
        const std::string key   = argv[index];
        const std::string value = argv[index + 1];
        require(allowed.count(key) != 0, "unknown generation argument: " + key);
        require(values.emplace(key, value).second, "duplicate generation argument: " + key);
    }
    const std::set<std::string> required = {
      "--artifact-repo-path",      "--campaign-id",      "--challenge",
      "--chunk-id",                "--chunk-index",      "--contract",
      "--expected-records",        "--expected-trajectories",
      "--input",                   "--input-repo-path",  "--opening-kind",
      "--output",                  "--schema",           "--seed",
      "--selection-policy-sha256"};
    for (const std::string& key : required)
        require(values.count(key) == 1, "missing generation argument: " + key);

    GenerationOptions output;
    output.challenge = values.at("--challenge");
    require(lowercase_hex(output.challenge, 32), "generation challenge is invalid");
    output.schemaPath       = values.at("--schema");
    output.contractPath     = values.at("--contract");
    output.inputPath        = values.at("--input");
    output.inputRepoPath    = values.at("--input-repo-path");
    output.outputPath       = values.at("--output");
    output.artifactRepoPath = values.at("--artifact-repo-path");
    require(repository_relative_path(output.inputRepoPath), "input repo path is invalid");
    require(repository_relative_path(output.artifactRepoPath), "artifact repo path is invalid");
    output.campaignText = values.at("--campaign-id");
    output.chunkText    = values.at("--chunk-id");
    output.campaignId   = parse_uuid(output.campaignText, "campaign UUID");
    output.chunkId      = parse_uuid(output.chunkText, "chunk UUID");
    require(output.campaignId != output.chunkId, "campaign and chunk UUIDs must differ");
    output.chunkIndex = parse_integer<std::uint64_t>(
      values.at("--chunk-index"), 0, std::numeric_limits<std::uint64_t>::max(), "chunk index");
    output.seed = values.at("--seed");
    require(!output.seed.empty()
              && std::all_of(output.seed.begin(), output.seed.end(),
                             [](unsigned char ch) { return ch >= '0' && ch <= '9'; }),
            "seed must be unsigned decimal text");
    output.expectedTrajectories = parse_integer<std::size_t>(
      values.at("--expected-trajectories"), 1, 1000000, "expected trajectory count");
    output.expectedRecords = parse_integer<std::size_t>(
      values.at("--expected-records"), 1, 100000000, "expected record count");
    output.openingKind = values.at("--opening-kind");
    require(!output.openingKind.empty()
              && std::all_of(output.openingKind.begin(), output.openingKind.end(),
                             [](unsigned char ch) {
                                 return (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9')
                                     || ch == '-';
                             }),
            "opening kind is invalid");
    output.selectionPolicySha256 = values.at("--selection-policy-sha256");
    require(lowercase_hex(output.selectionPolicySha256, 64),
            "selection policy digest is invalid");
    require(output.selectionPolicySha256 == SelectionPolicyG0Sha256,
            "this G0 producer build requires the frozen selection policy");
    if (values.count("--test-pause-after-partial-ms") != 0)
    {
        output.pauseAfterPartialMs = parse_integer<std::uint32_t>(
          values.at("--test-pause-after-partial-ms"), 1, 60000, "test pause");
        const char* enabled = std::getenv("CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION");
        require(enabled != nullptr && std::string_view(enabled) == "1",
                "G0 fault injection was not explicitly enabled");
    }
    require(output.outputPath.extension() == ".chp1", "output must use .chp1 extension");
    require(!output.outputPath.filename().empty(), "output filename is empty");
    return output;
}

enum class SelfplayTestCandidateFault : std::uint8_t {
    None,
    MissingPv,
    IllegalPv,
    SafetyLimit,
};

enum class SelfplayMode : std::uint8_t {
    FixtureG0,
    ProductionV1,
};

struct SelfplayOptions {
    SelfplayMode               mode = SelfplayMode::FixtureG0;
    std::filesystem::path      bookPath;
    std::filesystem::path      networkPath;
    std::filesystem::path      outputPath;
    std::string                artifactRepoPath;
    std::string                bookRepoPath;
    std::string                networkRepoPath;
    std::string                bookSha256;
    std::string                networkSha256;
    std::string                producerSha256;
    std::string                selectionPolicySha256;
    std::string                externalWorkloadId;
    std::string                role;
    std::string                cohort;
    std::string                campaignSetSha256;
    std::string                partitionSha256;
    std::string                campaignText;
    std::string                chunkText;
    std::string                seedText;
    std::string                challenge;
    IdBytes                    campaignId{};
    IdBytes                    chunkId{};
    std::uint64_t              baseSeed                = 0;
    std::uint64_t              assignedSeed            = 0;
    std::uint64_t              chunkIndex              = 0;
    std::uint64_t              splitSeed               = 0;
    std::uint64_t              validationThreshold     = 0;
    std::size_t                expectedRecords         = 0;
    std::size_t                maxCandidateGames       = 0;
    std::uint32_t              threads                 = 0;
    std::uint32_t              hashMb                  = 0;
    Depth                      depth                   = 0;
    std::uint64_t              nodes                   = 0;
    std::uint32_t              maxGamePly              = 0;
    std::uint32_t              openbenchProtocol       = 0;
    std::uint32_t              explorationPlies        = 0;
    std::uint32_t              explorationMultiPv      = 1;
    int                        explorationMaxScoreDiff = 0;
    std::uint32_t              pauseAfterPartialMs     = 0;
    SelfplayTestCandidateFault testCandidateFault      = SelfplayTestCandidateFault::None;
};

Digest production_partition_config_digest(const SelfplayOptions& options) {
    std::ostringstream body;
    body << "{\"campaign_set_sha256\":" << json_string(options.campaignSetSha256) << ",\"domain\":"
         << json_string(std::string_view(PartitionDomain, sizeof(PartitionDomain) - 1))
         << ",\"feature_contract_sha256\":" << json_string(ProductionFeatureContractSha256)
         << ",\"method\":\"content-hash-complete-trajectory-v1\""
         << ",\"physical_schema_sha256\":" << json_string(PhysicalSchemaSha256)
         << ",\"rule_profile_sha256\":" << json_string(CrazyhouseProfile::Sha256)
         << ",\"split_seed_u64\":" << options.splitSeed
         << ",\"validation_threshold_u64\":" << options.validationThreshold << "}\n";
    return sha256(body.str());
}

std::vector<std::string> tokenize_rendered_command(std::string_view line) {
    require(!line.empty() && line.size() <= 1024 * 1024, "rendered command length is invalid");
    std::vector<std::string> output;
    std::size_t              cursor = 0;
    while (cursor < line.size())
    {
        while (cursor < line.size() && (line[cursor] == ' ' || line[cursor] == '\t'))
            ++cursor;
        if (cursor == line.size())
            break;

        std::string token;
        if (line[cursor] == '"')
        {
            ++cursor;
            while (cursor < line.size() && line[cursor] != '"')
            {
                const unsigned char ch = static_cast<unsigned char>(line[cursor]);
                require(ch >= 0x20 && ch != 0x7F, "control byte in quoted command token");
                token.push_back(line[cursor++]);
            }
            require(cursor < line.size() && line[cursor] == '"',
                    "unterminated quoted command token");
            ++cursor;
            require(cursor == line.size() || line[cursor] == ' ' || line[cursor] == '\t',
                    "quoted command token has an attached suffix");
        }
        else
        {
            while (cursor < line.size() && line[cursor] != ' ' && line[cursor] != '\t')
            {
                const unsigned char ch = static_cast<unsigned char>(line[cursor]);
                require(ch >= 0x21 && ch != 0x7F && ch != '"',
                        "invalid byte in unquoted command token");
                token.push_back(line[cursor++]);
            }
        }
        require(!token.empty(), "empty rendered command token");
        output.push_back(std::move(token));
    }
    require(!output.empty(), "rendered command has no tokens");
    return output;
}

std::vector<std::string> read_selfplay_stdin() {
    std::string input((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
    require(!input.empty() && input.size() <= 1024 * 1024,
            "stdin generation request length is invalid");
    require(!(input.size() >= 3 && static_cast<unsigned char>(input[0]) == 0xEF
              && static_cast<unsigned char>(input[1]) == 0xBB
              && static_cast<unsigned char>(input[2]) == 0xBF),
            "stdin generation request has a BOM");
    require(input.find('\0') == std::string::npos, "stdin generation request contains NUL");
    if (input.find('\r') != std::string::npos)
    {
        std::string normalized;
        normalized.reserve(input.size());
        for (std::size_t index = 0; index < input.size(); ++index)
        {
            if (input[index] == '\r')
            {
                require(index + 1 < input.size() && input[index + 1] == '\n',
                        "stdin generation request contains a bare CR");
                normalized.push_back('\n');
                ++index;
            }
            else
            {
                require(input[index] != '\n', "stdin generation request mixes LF and CRLF framing");
                normalized.push_back(input[index]);
            }
        }
        input = std::move(normalized);
    }
    const std::size_t commandEnd = input.find('\n');
    require(commandEnd != std::string::npos && commandEnd != 0,
            "stdin generation command line is missing");
    const std::size_t quitEnd = input.find('\n', commandEnd + 1);
    require(quitEnd == input.size() - 1
              && input.substr(commandEnd + 1, quitEnd - commandEnd - 1) == "quit",
            "stdin must contain exactly one command followed by quit and EOF");
    require(input.find('\n', quitEnd + 1) == std::string::npos,
            "stdin generation request has an extra line");
    return tokenize_rendered_command(std::string_view(input).substr(0, commandEnd));
}

SelfplayOptions parse_selfplay_options(const std::vector<std::string>& tokens) {
    require(!tokens.empty(), "stdin generation command is empty");
    const bool production = tokens.front() == "crazyhouse_generate_physical_production_v1";
    require(production || tokens.front() == "crazyhouse_generate_physical_v1",
            "unknown stdin generation command");
    require((tokens.size() - 1) % 2 == 0, "self-play options must be name/value pairs");
    std::set<std::string> allowed = {"--artifact-repo-path",
                                     "--base-seed",
                                     "--book",
                                     "--book-repo-path",
                                     "--book-sha256",
                                     "--campaign-id",
                                     "--count",
                                     "--depth",
                                     "--hash-mb",
                                     "--max-candidate-games",
                                     "--max-game-ply",
                                     "--network",
                                     "--network-repo-path",
                                     "--network-sha256",
                                     "--nodes",
                                     "--output",
                                     "--producer-sha256",
                                     "--seed",
                                     "--selection-policy-sha256",
                                     "--test-candidate-fault",
                                     "--threads",
                                     "--test-pause-after-partial-ms"};
    if (production)
        allowed.insert({"--campaign-set-sha256", "--cohort", "--exploration-max-score-diff",
                        "--exploration-multipv", "--exploration-plies", "--external-workload-id",
                        "--openbench-protocol", "--partition-sha256", "--role", "--split-seed",
                        "--validation-threshold"});
    std::map<std::string, std::string> values;
    for (std::size_t index = 1; index < tokens.size(); index += 2)
    {
        const std::string& key = tokens[index];
        require(allowed.count(key) != 0, "unknown self-play argument: " + key);
        require(values.emplace(key, tokens[index + 1]).second,
                "duplicate self-play argument: " + key);
    }
    std::set<std::string> required = {"--artifact-repo-path",
                                      "--base-seed",
                                      "--book",
                                      "--book-repo-path",
                                      "--book-sha256",
                                      "--campaign-id",
                                      "--count",
                                      "--depth",
                                      "--hash-mb",
                                      "--max-candidate-games",
                                      "--max-game-ply",
                                      "--network",
                                      "--network-repo-path",
                                      "--network-sha256",
                                      "--nodes",
                                      "--output",
                                      "--producer-sha256",
                                      "--seed",
                                      "--selection-policy-sha256",
                                      "--threads"};
    if (production)
        required.insert({"--campaign-set-sha256", "--cohort", "--exploration-max-score-diff",
                         "--exploration-multipv", "--exploration-plies", "--external-workload-id",
                         "--openbench-protocol", "--partition-sha256", "--role", "--split-seed",
                         "--validation-threshold"});
    for (const std::string& key : required)
        require(values.count(key) == 1, "missing self-play argument: " + key);

    SelfplayOptions output;
    output.mode             = production ? SelfplayMode::ProductionV1 : SelfplayMode::FixtureG0;
    output.artifactRepoPath = values.at("--artifact-repo-path");
    output.bookRepoPath     = values.at("--book-repo-path");
    output.networkRepoPath  = values.at("--network-repo-path");
    require(repository_relative_path(output.artifactRepoPath),
            "self-play artifact repo path is invalid");
    require(repository_relative_path(output.bookRepoPath), "self-play book repo path is invalid");
    require(repository_relative_path(output.networkRepoPath),
            "self-play network repo path is invalid");
    output.bookPath    = values.at("--book");
    output.networkPath = values.at("--network");
    output.outputPath  = values.at("--output");
    require(!output.bookPath.empty() && !output.networkPath.empty(),
            "self-play input path is empty");
    require(!output.outputPath.empty() && !output.outputPath.filename().empty(),
            "self-play output path is empty");

    output.bookSha256            = values.at("--book-sha256");
    output.networkSha256         = values.at("--network-sha256");
    output.producerSha256        = values.at("--producer-sha256");
    output.selectionPolicySha256 = values.at("--selection-policy-sha256");
    for (const auto& binding :
         {std::pair<std::string_view, const std::string&>{"book SHA-256", output.bookSha256},
          {"network SHA-256", output.networkSha256},
          {"producer SHA-256", output.producerSha256},
          {"selection-policy SHA-256", output.selectionPolicySha256}})
        require(lowercase_hex(binding.second, 64), std::string(binding.first) + " is invalid");
    require(output.networkSha256 == RegisteredLegacyNetworkSha256,
            "self-play network is not the registered legacy network");
    if (production)
    {
        require(output.bookSha256 == ProductionBookSha256,
                "production book is not the frozen official book");
        require(output.selectionPolicySha256 == ProductionSelectionPolicySha256,
                "production selection policy is not frozen for this producer");
        require(output.bookRepoPath == "openbench/books/CRAZYHOUSE_openings.epd",
                "production book repository path is not frozen");

        auto valid_slug = [](std::string_view value) {
            return !value.empty() && value.size() <= 128
                && std::isalnum(static_cast<unsigned char>(value.front()))
                && std::isalnum(static_cast<unsigned char>(value.back()))
                && std::all_of(value.begin(), value.end(), [](unsigned char ch) {
                       return (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-';
                   });
        };
        output.externalWorkloadId = values.at("--external-workload-id");
        output.role               = values.at("--role");
        output.cohort             = values.at("--cohort");
        output.campaignSetSha256  = values.at("--campaign-set-sha256");
        output.partitionSha256    = values.at("--partition-sha256");
        require(valid_slug(output.externalWorkloadId),
                "production external workload id is invalid");
        require(output.role == "train" || output.role == "validation",
                "production role is invalid");
        require(valid_slug(output.cohort), "production cohort is invalid");
        require(lowercase_hex(output.campaignSetSha256, 64),
                "production campaign-set SHA-256 is invalid");
        require(lowercase_hex(output.partitionSha256, 64),
                "production partition SHA-256 is invalid");
    }
    else
    {
        require(output.bookSha256 == SelfplayG0BookSha256,
                "self-play book is not the frozen G0 book");
        require(output.selectionPolicySha256 == SelfplaySelectionPolicySha256,
                "self-play selection policy is not frozen for this producer");
    }

    output.campaignText = values.at("--campaign-id");
    output.campaignId   = parse_uuid(output.campaignText, "self-play campaign UUID");
    output.baseSeed     = parse_integer<std::uint64_t>(
      values.at("--base-seed"), 0, std::numeric_limits<std::uint64_t>::max(), "base seed");
    output.seedText     = values.at("--seed");
    output.assignedSeed = parse_integer<std::uint64_t>(
      output.seedText, 0, std::numeric_limits<std::uint64_t>::max(), "assigned seed");
    require(output.assignedSeed >= output.baseSeed,
            "assigned seed precedes the campaign base seed");
    output.chunkIndex = output.assignedSeed - output.baseSeed;
    output.chunkId    = derive_selfplay_id("chunk", output.campaignId, output.chunkIndex, 0);
    output.chunkText  = uuid_text(output.chunkId);
    require(output.chunkId != output.campaignId,
            "derived chunk identity collides with campaign identity");

    output.expectedRecords =
      parse_integer<std::size_t>(values.at("--count"), 1, 100000000, "physical-record count");
    output.maxCandidateGames = parse_integer<std::size_t>(values.at("--max-candidate-games"), 1,
                                                          1000000, "candidate-game budget");
    output.threads =
      parse_integer<std::uint32_t>(values.at("--threads"), 1, 1, "self-play thread count");
    output.hashMb =
      parse_integer<std::uint32_t>(values.at("--hash-mb"), 1, 32768, "self-play hash MiB");
    output.depth =
      parse_integer<Depth>(values.at("--depth"), Depth(1), Depth(MAX_PLY - 1), "self-play depth");
    output.nodes = parse_integer<std::uint64_t>(
      values.at("--nodes"), 0, std::numeric_limits<std::uint64_t>::max(), "self-play node limit");
    output.maxGamePly = parse_integer<std::uint32_t>(values.at("--max-game-ply"), 1, 1000000,
                                                     "self-play maximum game ply");
    if (production)
    {
        output.openbenchProtocol = parse_integer<std::uint32_t>(
          values.at("--openbench-protocol"), ProductionOpenBenchProtocol,
          ProductionOpenBenchProtocol, "OpenBench publication protocol");
        output.splitSeed           = parse_integer<std::uint64_t>(values.at("--split-seed"), 0,
                                                                  std::numeric_limits<std::uint64_t>::max(),
                                                                  "partition split seed");
        output.validationThreshold = parse_integer<std::uint64_t>(
          values.at("--validation-threshold"), ProductionValidationThreshold,
          ProductionValidationThreshold, "partition validation threshold");
        output.explorationPlies =
          parse_integer<std::uint32_t>(values.at("--exploration-plies"), ProductionExplorationPlies,
                                       ProductionExplorationPlies, "production exploration plies");
        output.explorationMultiPv = parse_integer<std::uint32_t>(
          values.at("--exploration-multipv"), ProductionExplorationMultiPv,
          ProductionExplorationMultiPv, "production exploration MultiPV");
        output.explorationMaxScoreDiff = parse_integer<int>(
          values.at("--exploration-max-score-diff"), ProductionExplorationMaxScoreDiff,
          ProductionExplorationMaxScoreDiff, "production exploration score difference");
        require(output.threads == ProductionThreads && output.hashMb == ProductionHashMb
                  && output.depth == ProductionDepthCap && output.nodes == ProductionNodes
                  && output.maxGamePly == ProductionMaxGamePly,
                "production fixed-work settings do not match the frozen contract");
        require(output.partitionSha256 == hex(production_partition_config_digest(output)),
                "production partition SHA-256 does not match the frozen configuration");
    }
    if (values.count("--test-pause-after-partial-ms") != 0)
    {
        output.pauseAfterPartialMs = parse_integer<std::uint32_t>(
          values.at("--test-pause-after-partial-ms"), 1, 60000, "self-play test pause");
        const char* enabled = std::getenv("CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION");
        require(enabled != nullptr && std::string_view(enabled) == "1",
                "G0 self-play fault injection was not explicitly enabled");
    }
    if (values.count("--test-candidate-fault") != 0)
    {
        const char* enabled = std::getenv("CRAZYHOUSE_DATAGEN_G0_FAULT_INJECTION");
        require(enabled != nullptr && std::string_view(enabled) == "1",
                "G0 self-play candidate fault injection was not explicitly enabled");
        const std::string& value = values.at("--test-candidate-fault");
        if (value == "missing-pv")
            output.testCandidateFault = SelfplayTestCandidateFault::MissingPv;
        else if (value == "illegal-pv")
            output.testCandidateFault = SelfplayTestCandidateFault::IllegalPv;
        else if (value == "safety-limit")
            output.testCandidateFault = SelfplayTestCandidateFault::SafetyLimit;
        else
            throw DatagenError("unknown self-play candidate fault injection");
    }
    return output;
}

std::string build_provenance(const GenerationOptions& options,
                             const ArtifactIdentity&  artifact,
                             const Digest&            capabilityDigest,
                             std::size_t              capabilityBytes,
                             const Digest&            inputDigest,
                             std::size_t              inputBytes) {
    std::ostringstream output;
    output << "{\"adjudication\":{\"allowed_claim_policies\":[\"core-only\",\"immediate-threefold-proxy\"]"
           << ",\"fivefold_automatic\":true,\"insufficient_material\":false"
           << ",\"policy_scope\":\"per-trajectory-g0-matrix\",\"rule50\":false"
           << ",\"threefold_claim_proxy\":\"only-when-record-claim-policy-is-immediate\"}"
           << ",\"campaign_id\":" << json_string(options.campaignText)
           << ",\"chunk_id\":" << json_string(options.chunkText)
           << ",\"chunk_index\":" << options.chunkIndex
           << ",\"generation_settings\":{\"fixture_only\":true,\"hash_mib\":16"
           << ",\"search_nodes\":1024,\"threads\":1,\"training_admissible\":false}"
           << ",\"invalid_game_policy\":{\"crash\":\"quarantine-game\""
           << ",\"illegal_move\":\"quarantine-game\",\"safety_limit\":\"quarantine-game\""
           << ",\"timeloss\":\"quarantine-game\"}"
           << ",\"network\":{\"bytes\":0,\"format\":null,\"license\":null"
           << ",\"path\":null,\"sha256\":null,\"used\":false}"
           << ",\"opening_source\":{\"artifact\":{\"bytes\":" << inputBytes
           << ",\"kind\":\"physical-trajectory-stream\",\"path\":"
           << json_string(options.inputRepoPath) << ",\"sha256\":" << json_string(hex(inputDigest))
           << "},\"engine_selected\":false,\"kind\":" << json_string(options.openingKind)
           << ",\"match_result_selected\":false,\"selection_policy_sha256\":"
           << json_string(options.selectionPolicySha256) << "}"
           << ",\"producer_artifact\":{\"bytes\":" << artifact.bytes
           << ",\"kind\":\"crazyhouse-physical-datagen\",\"path\":"
           << json_string(options.artifactRepoPath) << ",\"sha256\":"
           << json_string(hex(artifact.digest)) << "}"
           << ",\"producer_capability\":{\"bytes\":" << capabilityBytes
           << ",\"challenge\":" << json_string(options.challenge)
           << ",\"schema\":\"crazyhouse-datagen-capability-response/v1\",\"sha256\":"
           << json_string(hex(capabilityDigest)) << "}"
           << ",\"project\":\"Crazyhouse-Stockfish\""
           << ",\"rule_profile\":{\"id\":" << json_string(CrazyhouseProfile::Id)
           << ",\"sha256\":" << json_string(CrazyhouseProfile::Sha256) << "}"
           << ",\"schema\":\"crazyhouse-datagen-provenance/v1\""
           << ",\"seed\":" << json_string(options.seed)
           << ",\"source_commit\":" << json_string(DATAGEN_SOURCE_COMMIT)
           << ",\"source_dirty\":false"
           << ",\"source_tree\":" << json_string(DATAGEN_SOURCE_TREE)
           << ",\"src_tree\":" << json_string(DATAGEN_SRC_TREE)
           << ",\"teacher\":{\"artifact\":null"
           << ",\"bound_policy\":\"exact-only-for-ongoing-records\""
           << ",\"kind\":\"golden-fixture\",\"network_used\":false"
           << ",\"score_perspective\":\"side-to-move\",\"search_settings_sha256\":"
           << json_string(SearchSettingsSha256) << ",\"synthetic\":true}"
           << ",\"toolchain\":{\"build_recipe_sha256\":"
           << json_string(DATAGEN_BUILD_RECIPE_SHA256) << ",\"identity\":"
           << json_string(DATAGEN_TOOLCHAIN_IDENTITY) << ",\"sha256\":"
           << json_string(DATAGEN_TOOLCHAIN_SHA256) << "}"
           << ",\"variant\":\"crazyhouse\"}\n";
    return output.str();
}

struct MoveWire {
    Byte kind = 0;
    Byte from = NoSquare;
    Byte to   = NoSquare;
    Byte aux  = 0;
};

MoveWire move_wire(const std::string& token, Move move) {
    const MoveKind kind = move.kind();
    require(kind != MoveKind::INVALID, "engine returned an invalid move kind");
    if (kind == MoveKind::DROP)
    {
        require(token.size() == 4 && token[1] == '@', "drop UCI framing drifted");
        const char piece = char(std::tolower(static_cast<unsigned char>(token[0])));
        const auto aux = std::string_view(" pnbrq").find(piece);
        require(aux >= 1 && aux <= 5, "drop piece drifted");
        return MoveWire{5, NoSquare, parse_square(std::string_view(token).substr(2, 2)), Byte(aux)};
    }
    require(token.size() == 4 || token.size() == 5, "move UCI framing drifted");
    const Byte from = parse_square(std::string_view(token).substr(0, 2));
    const Byte to   = parse_square(std::string_view(token).substr(2, 2));
    switch (kind)
    {
    case MoveKind::NORMAL: require(token.size() == 4, "normal move has promotion suffix"); return {1, from, to, 0};
    case MoveKind::PROMOTION: {
        require(token.size() == 5, "promotion suffix is missing");
        const char piece = char(std::tolower(static_cast<unsigned char>(token[4])));
        const auto aux = std::string_view("  nbrq").find(piece);
        require(aux >= 2 && aux <= 5, "promotion piece drifted");
        return {2, from, to, Byte(aux)};
    }
    case MoveKind::EN_PASSANT: require(token.size() == 4, "en-passant framing drifted"); return {3, from, to, 0};
    case MoveKind::CASTLING: require(token.size() == 4, "castling framing drifted"); return {4, from, to, 0};
    case MoveKind::DROP:
    case MoveKind::INVALID: break;
    }
    throw DatagenError("unhandled move kind");
}

std::array<Byte, 32> packed_board(const Position& position) {
    std::array<Byte, 32> output{};
    const auto&          board = position.piece_array();
    for (std::size_t square = 0; square < board.size(); ++square)
    {
        const Byte code = static_cast<Byte>(board[square]);
        require(code != 7 && code != 8 && code != 15, "reserved piece code in production Position");
        output[square / 2] |= Byte(code << (4U * (square & 1U)));
    }
    return output;
}

std::array<Byte, 10> pockets(const Position& position) {
    constexpr std::array<PieceType, 5> Types = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    std::array<Byte, 10>               output{};
    for (Color color : {WHITE, BLACK})
        for (std::size_t index = 0; index < Types.size(); ++index)
        {
            const int count = position.pocket_count(color, Types[index]);
            require(count >= 0 && count <= 255, "pocket count is outside wire range");
            output[static_cast<std::size_t>(color) * Types.size() + index] = Byte(count);
        }
    return output;
}

Byte castling_rights(const Position& position) {
    Byte output = 0;
    if (position.can_castle(WHITE_OO))
        output |= 1;
    if (position.can_castle(WHITE_OOO))
        output |= 2;
    if (position.can_castle(BLACK_OO))
        output |= 4;
    if (position.can_castle(BLACK_OOO))
        output |= 8;
    return output;
}

Digest position_identity(const Position& position) {
    ByteList payload;
    append(payload, std::string_view(PositionDomain, sizeof(PositionDomain) - 1));
    append(payload, packed_board(position));
    payload.push_back(Byte(position.side_to_move()));
    payload.push_back(castling_rights(position));
    payload.push_back(position.ep_square() == SQ_NONE ? NoSquare : Byte(position.ep_square()));
    append(payload, pockets(position));
    std::array<Byte, 8> promoted{};
    put_le<std::uint64_t>(promoted.data(), position.promoted_pieces());
    append(payload, promoted);
    return sha256(payload);
}

Digest history_initial(const IdBytes& trajectoryId, const Digest& provenance) {
    ByteList payload;
    append(payload, std::string_view(HistoryInitialDomain, sizeof(HistoryInitialDomain) - 1));
    append(payload, trajectoryId);
    append(payload, provenance);
    return sha256(payload);
}

Digest history_step(const Digest& previous,
                    std::uint32_t ply,
                    const Digest& position,
    const MoveWire& move) {
    ByteList payload;
    append(payload, std::string_view(HistoryStepDomain, sizeof(HistoryStepDomain) - 1));
    append(payload, previous);
    std::array<Byte, 4> plyBytes{};
    put_le<std::uint32_t>(plyBytes.data(), ply);
    append(payload, plyBytes);
    append(payload, position);
    payload.push_back(move.kind);
    payload.push_back(move.from);
    payload.push_back(move.to);
    payload.push_back(move.aux);
    return sha256(payload);
}

Byte raw_ep_from_fen(std::string_view fen) {
    const auto fields = split(fen, ' ');
    require(fields.size() == 6, "root FEN field count drifted");
    return fields[3] == "-" ? NoSquare : parse_square(fields[3]);
}

struct PositionSnapshot {
    std::string          fen;
    Key                  key = 0;
    Bitboard             promoted = 0;
    std::array<Byte, 10> pocket{};
    int                  repetition = 0;
    int                  rule50 = 0;
    int                  gamePly = 0;
};

PositionSnapshot snapshot(const Position& position) {
    return PositionSnapshot{position.fen(),       position.key(),
                            position.promoted_pieces(), pockets(position),
                            position.repetition_occurrences(), position.rule50_count(),
                            position.game_ply()};
}

void require_restored(const Position& position, const PositionSnapshot& before) {
    const PositionSnapshot after = snapshot(position);
    require(after.fen == before.fen && after.key == before.key
              && after.promoted == before.promoted && after.pocket == before.pocket
              && after.repetition == before.repetition && after.rule50 == before.rule50
              && after.gamePly == before.gamePly,
            "make/undo did not restore the complete physical state");
}

Byte map_terminal_reason(CrazyhouseTerminalReason reason) {
    switch (reason)
    {
    case CrazyhouseTerminalReason::ONGOING: return 0;
    case CrazyhouseTerminalReason::CHECKMATE: return 1;
    case CrazyhouseTerminalReason::STALEMATE: return 2;
    case CrazyhouseTerminalReason::FIVEFOLD_REPETITION: return 3;
    case CrazyhouseTerminalReason::THREEFOLD_REPETITION_CLAIM: return 4;
    }
    throw DatagenError("unknown production terminal reason");
}

void validate_terminal(const Position& position,
                       const Trajectory& trajectory,
                       bool finalRecord) {
    const auto policy = trajectory.claimPolicy == 0 ? CrazyhouseClaimPolicy::AUTOMATIC_ONLY
                                                     : CrazyhouseClaimPolicy::THREEFOLD_IMMEDIATE_CLAIM;
    const CrazyhouseTerminalStatus status = position.crazyhouse_terminal_status(policy);
    if (!finalRecord)
    {
        require(!status.ended(), "trajectory continues after production terminal state");
        return;
    }
    if (trajectory.terminalReason <= 4)
    {
        require(status.ended() && map_terminal_reason(status.reason) == trajectory.terminalReason,
                "declared terminal reason disagrees with production Position");
        if (trajectory.terminalReason == 1)
        {
            require(status.winner.has_value(), "checkmate has no production winner");
            const std::int8_t expected = *status.winner == WHITE ? 1 : -1;
            require(trajectory.resultWhite == expected, "checkmate result disagrees with production winner");
        }
        else
            require(!status.winner.has_value() && trajectory.resultWhite == 0,
                    "draw terminal has a winner");
    }
    else
        require(!status.ended(), "adjudicated terminal overlaps a production terminal state");
}

struct BookRoot {
    std::string id;
    std::string fen;
    std::string sourceLine;
    std::size_t sourceIndex = 0;
};

std::uint32_t parse_epd_counter(std::string_view token, std::string_view label) {
    require(token.size() >= 2 && token.back() == ';',
            std::string(label) + " EPD token is missing its semicolon");
    token.remove_suffix(1);
    return parse_integer<std::uint32_t>(token, 0,
                                        std::numeric_limits<std::uint32_t>::max(), label);
}

std::vector<BookRoot> parse_selfplay_book(const ByteList& bytes) {
    require(!bytes.empty(), "self-play book is empty");
    require(!(bytes.size() >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF),
            "self-play book has a BOM");
    require(std::find(bytes.begin(), bytes.end(), Byte{'\r'}) == bytes.end()
              && std::find(bytes.begin(), bytes.end(), Byte{0}) == bytes.end(),
            "self-play book contains CR or NUL");
    require(bytes.back() == '\n' && (bytes.size() == 1 || bytes[bytes.size() - 2] != '\n'),
            "self-play book must end with exactly one LF");
    const std::string text(reinterpret_cast<const char*>(bytes.data()), bytes.size() - 1);
    const auto        lines = split(text, '\n');
    require(!lines.empty(), "self-play book has no roots");

    std::vector<BookRoot> roots;
    std::set<std::string> ids;
    std::set<std::string> fens;
    for (std::size_t index = 0; index < lines.size(); ++index)
    {
        require(!lines[index].empty(), "self-play book contains an empty line");
        const auto fields = split(lines[index], ' ');
        require(fields.size() == 10 && fields[4] == "hmvc" && fields[6] == "fmvn"
                  && fields[8] == "id",
                "self-play EPD row is not canonical at index " + std::to_string(index));
        const std::uint32_t halfmove = parse_epd_counter(fields[5], "halfmove counter");
        const std::uint32_t fullmove = parse_epd_counter(fields[7], "fullmove counter");
        require(fullmove >= 1, "self-play EPD fullmove counter must be positive");
        require(fields[9].size() >= 4 && fields[9].front() == '"'
                  && fields[9][fields[9].size() - 2] == '"' && fields[9].back() == ';',
                "self-play EPD id framing is invalid");
        const std::string id = fields[9].substr(1, fields[9].size() - 3);
        require(!id.empty()
                  && std::all_of(id.begin(), id.end(), [](unsigned char ch) {
                         return ch >= 0x21 && ch <= 0x7E && ch != '"' && ch != '\\';
                     }),
                "self-play EPD id is invalid");
        std::ostringstream fen;
        fen << fields[0] << ' ' << fields[1] << ' ' << fields[2] << ' ' << fields[3] << ' '
            << halfmove << ' ' << fullmove;

        std::deque<StateInfo> states(1);
        Position              position(Ruleset::CRAZYHOUSE);
        if (const auto error = position.set(fen.str(), false, Ruleset::CRAZYHOUSE,
                                            &states.back()))
            throw DatagenError("self-play book root rejected: " + std::string(error->what()));
        require(position.ruleset() == Ruleset::CRAZYHOUSE,
                "self-play book routed outside Crazyhouse");
        require(position.repetition_occurrences() == 1,
                "self-play book root does not start with fresh history");
        require(!position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY).ended(),
                "self-play book contains a terminal root");
        require(ids.insert(id).second, "duplicate self-play EPD id");
        require(fens.insert(fen.str()).second, "duplicate self-play EPD root");
        roots.push_back(BookRoot{id, fen.str(), lines[index], index});
    }
    return roots;
}

std::vector<BookRoot> parse_production_book(const ByteList& bytes) {
    require(!bytes.empty(), "production book is empty");
    require(!(bytes.size() >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF),
            "production book has a BOM");
    require(std::find(bytes.begin(), bytes.end(), Byte{'\r'}) == bytes.end()
              && std::find(bytes.begin(), bytes.end(), Byte{0}) == bytes.end(),
            "production book contains CR or NUL");
    require(bytes.back() == '\n' && (bytes.size() == 1 || bytes[bytes.size() - 2] != '\n'),
            "production book must end with exactly one LF");
    const std::string text(reinterpret_cast<const char*>(bytes.data()), bytes.size() - 1);
    const auto        lines = split(text, '\n');
    require(!lines.empty(), "production book has no roots");

    std::vector<BookRoot> roots;
    std::set<std::string> ids;
    roots.reserve(lines.size());
    for (std::size_t index = 0; index < lines.size(); ++index)
    {
        require(!lines[index].empty(), "production book contains an empty line");
        const auto fields = split(lines[index], ' ');
        require(fields.size() == 6,
                "production book row is not a six-field Crazyhouse FEN at index "
                  + std::to_string(index));
        const std::uint32_t halfmove = parse_integer<std::uint32_t>(
          fields[4], 0, std::numeric_limits<std::uint32_t>::max(), "production halfmove counter");
        const std::uint32_t fullmove = parse_integer<std::uint32_t>(
          fields[5], 1, std::numeric_limits<std::uint32_t>::max(), "production fullmove counter");
        require(fields[4] == std::to_string(halfmove) && fields[5] == std::to_string(fullmove),
                "production book counters are not canonical");

        std::deque<StateInfo> states(1);
        Position              position(Ruleset::CRAZYHOUSE);
        if (const auto error =
              position.set(lines[index], false, Ruleset::CRAZYHOUSE, &states.back()))
            throw DatagenError("production book root rejected: " + std::string(error->what()));
        require(position.ruleset() == Ruleset::CRAZYHOUSE,
                "production book routed outside Crazyhouse");
        require(position.repetition_occurrences() == 1,
                "production book root does not start with fresh history");
        require(!position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY).ended(),
                "production book contains a terminal root");

        std::ostringstream idBuilder;
        idBuilder << "CHOB-" << std::setw(6) << std::setfill('0') << (index + 1) << '-'
                  << hex(sha256(lines[index])).substr(0, 12);
        const std::string id = idBuilder.str();
        require(ids.insert(id).second, "duplicate production book content identity");
        roots.push_back(BookRoot{id, lines[index], lines[index], index});
    }
    return roots;
}

struct CandidateRejection {
    std::uint64_t candidateIndex = 0;
    std::string   rootId;
    std::string   reason;
};

class CandidateRejected: public std::runtime_error {
   public:
    using std::runtime_error::runtime_error;
};

Search::TrainingSearchResult run_training_search(Position&                       position,
                                                 const Search::TrainingSearchRequest& request,
                                                 ThreadPool&                     threads,
                                                 TranspositionTable&             tt) {
    threads.wait_for_search_finished();
    threads.clear();
    tt.clear(threads);
    tt.new_search();
    threads.stop          = false;
    threads.increaseDepth = true;

    Search::TrainingSearchResult result;
    std::exception_ptr           failure;
    Search::Worker* const        worker = threads.main_thread()->worker.get();
    threads.run_on_thread(0, [&]() {
        try
        {
            result = worker->training_search(position, request);
        }
        catch (...)
        {
            failure = std::current_exception();
        }
    });
    threads.wait_on_thread(0);
    if (failure)
        std::rethrow_exception(failure);
    return result;
}

TeacherLabel teacher_label(const Search::TrainingSearchResult& search,
                           const Position&                     position) {
    if (!search.exact || search.lines.size() != 1 || !search.lines.front().exact
        || search.pv.empty() || search.lines.front().pv.empty()
        || search.pv[0] != search.lines.front().pv[0])
        throw CandidateRejected("teacher search did not return one exact principal variation");
    if (search.value <= -VALUE_INFINITE || search.value >= VALUE_INFINITE
        || search.value == VALUE_NONE)
        throw CandidateRejected("teacher search returned an invalid value");
    if (search.nodes == 0 || search.depth <= 0 || search.selDepth <= 0)
        throw CandidateRejected("teacher search returned incomplete work metadata");
    require(search.depth <= std::numeric_limits<std::uint16_t>::max()
              && search.selDepth <= std::numeric_limits<std::uint16_t>::max(),
            "teacher search depth exceeds the physical wire range");

    const Score score(search.value, position);
    TeacherLabel label;
    label.nodes    = search.nodes;
    label.depth    = static_cast<std::uint16_t>(search.depth);
    label.selDepth = static_cast<std::uint16_t>(search.selDepth);
    // V1 is a deterministic fixed-work producer. Wall time is deliberately not
    // encoded because it is host state, not a physical or teacher identity.
    label.moveTimeMs = 0;
    if (score.is<Score::InternalUnits>())
    {
        label.kind  = 1;
        label.value = score.get<Score::InternalUnits>().value;
    }
    else if (score.is<Score::Mate>())
    {
        label.kind  = 2;
        label.value = score.get<Score::Mate>().plies;
    }
    else
        throw CandidateRejected("tablebase teacher values are not admitted in Crazyhouse V1");
    return label;
}

TeacherLabel production_teacher_label(const Search::TrainingSearchResult& search,
                                      const Position&                     position,
                                      std::size_t                         lineIndex) {
    if (lineIndex >= search.lines.size() || !search.lines[lineIndex].exact
        || search.lines[lineIndex].pv.empty())
        throw CandidateRejected("selected production teacher line is not exact and complete");
    const Value value = search.lines[lineIndex].value;
    if (value <= -VALUE_INFINITE || value >= VALUE_INFINITE || value == VALUE_NONE)
        throw CandidateRejected("selected production teacher line has an invalid value");
    if (search.nodes == 0 || search.depth <= 0 || search.selDepth <= 0)
        throw CandidateRejected("production teacher search returned incomplete work metadata");
    require(search.depth <= std::numeric_limits<std::uint16_t>::max()
              && search.selDepth <= std::numeric_limits<std::uint16_t>::max(),
            "production teacher search depth exceeds the physical wire range");

    const Score  score(value, position);
    TeacherLabel label;
    label.nodes      = search.nodes;
    label.depth      = static_cast<std::uint16_t>(search.depth);
    label.selDepth   = static_cast<std::uint16_t>(search.selDepth);
    label.moveTimeMs = 0;
    if (score.is<Score::InternalUnits>())
    {
        label.kind  = 1;
        label.value = score.get<Score::InternalUnits>().value;
    }
    else if (score.is<Score::Mate>())
    {
        label.kind  = 2;
        label.value = score.get<Score::Mate>().plies;
    }
    else
        throw CandidateRejected("tablebase teacher values are not admitted in production V1");
    return label;
}

std::uint64_t production_partition_value(const SelfplayOptions& options,
                                         const IdBytes&         trajectoryId) {
    ByteList payload;
    append(payload, std::string_view(PartitionDomain, sizeof(PartitionDomain) - 1));
    std::array<Byte, 8> splitSeed{};
    put_le<std::uint64_t>(splitSeed.data(), options.splitSeed);
    append(payload, splitSeed);
    append(payload, options.campaignId);
    append(payload, trajectoryId);
    const Digest digest = sha256(payload);
    return get_le<std::uint64_t>(digest.data());
}

bool production_role_eligible(const SelfplayOptions& options, const IdBytes& trajectoryId) {
    const bool validation =
      production_partition_value(options, trajectoryId) < options.validationThreshold;
    return validation == (options.role == "validation");
}

std::size_t select_production_line(const SelfplayOptions&              options,
                                   const Search::TrainingSearchResult& search,
                                   const Position&                     position,
                                   const IdBytes&                      trajectoryId,
                                   std::uint32_t                       ply) {
    if (search.lines.empty() || !search.lines.front().exact || search.lines.front().pv.empty())
        throw CandidateRejected("production teacher search has no exact best line");
    if (ply >= options.explorationPlies)
        return 0;

    const Value best = search.lines.front().value;
    if (best <= -VALUE_INFINITE || best >= VALUE_INFINITE || best == VALUE_NONE)
        throw CandidateRejected("production teacher search has an invalid best value");
    std::vector<std::size_t> eligible;
    const std::size_t        lineLimit =
      std::min<std::size_t>(options.explorationMultiPv, search.lines.size());
    for (std::size_t index = 0; index < lineLimit; ++index)
    {
        const Search::TrainingSearchLine& line = search.lines[index];
        if (!line.exact || line.pv.empty() || line.value <= -VALUE_INFINITE
            || line.value >= VALUE_INFINITE || line.value == VALUE_NONE)
            continue;
        const int difference = static_cast<int>(best) - static_cast<int>(line.value);
        if (difference >= 0 && difference <= options.explorationMaxScoreDiff)
            eligible.push_back(index);
    }
    if (eligible.empty() || eligible.front() != 0)
        throw CandidateRejected("production exploration has no eligible exact best line");

    ByteList payload;
    append(payload, std::string_view(ExplorationDomain, sizeof(ExplorationDomain) - 1));
    append(payload, options.campaignId);
    append(payload, options.chunkId);
    append(payload, trajectoryId);
    std::array<Byte, 12> numeric{};
    put_le<std::uint64_t>(numeric.data(), options.assignedSeed);
    put_le<std::uint32_t>(numeric.data() + 8, ply);
    append(payload, numeric);
    append(payload, position_identity(position));
    const Digest digest = sha256(payload);
    return eligible[get_le<std::uint64_t>(digest.data()) % eligible.size()];
}

std::vector<std::size_t> deterministic_book_order(const std::vector<BookRoot>& roots,
                                                  std::uint64_t                seed) {
    struct RankedRoot {
        Digest      key{};
        std::size_t index = 0;
    };
    std::vector<RankedRoot> ranked;
    ranked.reserve(roots.size());
    for (std::size_t index = 0; index < roots.size(); ++index)
    {
        constexpr char Domain[] = "Crazyhouse-Stockfish selfplay book order v1\0";
        ByteList       payload;
        append(payload, std::string_view(Domain, sizeof(Domain) - 1));
        std::array<Byte, 16> numeric{};
        put_le<std::uint64_t>(numeric.data(), seed);
        put_le<std::uint64_t>(numeric.data() + 8, index);
        append(payload, numeric);
        append(payload, roots[index].sourceLine);
        ranked.push_back({sha256(payload), index});
    }
    std::sort(ranked.begin(), ranked.end(), [](const RankedRoot& lhs, const RankedRoot& rhs) {
        return lhs.key != rhs.key ? lhs.key < rhs.key : lhs.index < rhs.index;
    });
    std::vector<std::size_t> output;
    output.reserve(ranked.size());
    for (const RankedRoot& root : ranked)
        output.push_back(root.index);
    return output;
}

struct LiveCorpus {
    TrajectoryCorpus                corpus;
    std::vector<CandidateRejection> rejected;
    std::size_t                     candidatesExamined             = 0;
    std::size_t                     roleIneligibleCandidates       = 0;
    std::size_t                     roleEligibleCompleteCandidates = 0;
    std::size_t                     subsetCandidatesOmitted        = 0;
};



LiveCorpus generate_live_corpus(const SelfplayOptions&    options,
                                const std::vector<BookRoot>& roots,
                                ThreadPool&               threads,
                                TranspositionTable&       tt) {
    require(options.maxCandidateGames <= roots.size(),
            "V1 candidate budget exceeds the authenticated unique-root count");
    const auto order = deterministic_book_order(roots, options.assignedSeed);
    LiveCorpus output;
    output.corpus.trajectories.reserve(options.maxCandidateGames);
    Search::TrainingSearchRequest request{options.depth, options.nodes, 1};

    for (std::uint64_t candidateIndex = 0;
         candidateIndex < options.maxCandidateGames
         && output.corpus.recordCount < options.expectedRecords;
         ++candidateIndex)
    {
        ++output.candidatesExamined;
        const BookRoot& root = roots[order[static_cast<std::size_t>(candidateIndex)]];
        try
        {
            Trajectory trajectory;
            trajectory.gameId = derive_selfplay_id("game", options.campaignId,
                                                   options.chunkIndex, candidateIndex);
            trajectory.trajectoryId = derive_selfplay_id(
              "trajectory", options.campaignId, options.chunkIndex, candidateIndex);
            trajectory.gameText          = uuid_text(trajectory.gameId);
            trajectory.trajectoryText    = uuid_text(trajectory.trajectoryId);
            trajectory.claimPolicy       = 0;
            trajectory.nonstandardRoot   = true;
            trajectory.teacherUsedNetwork = true;
            trajectory.rootFen            = root.fen;

            std::deque<StateInfo> states(1);
            Position              position(Ruleset::CRAZYHOUSE);
            if (const auto error =
                  position.set(root.fen, false, Ruleset::CRAZYHOUSE, &states.back()))
                throw CandidateRejected("book root became invalid: " + std::string(error->what()));
            if (position.repetition_occurrences() != 1)
                throw CandidateRejected("candidate root history is not fresh");

            for (;;)
            {
                const CrazyhouseTerminalStatus terminal =
                  position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY);
                if (terminal.ended())
                {
                    trajectory.terminalReason = map_terminal_reason(terminal.reason);
                    if (trajectory.terminalReason < 1 || trajectory.terminalReason > 3)
                        throw CandidateRejected("non-automatic terminal reason reached");
                    trajectory.resultWhite =
                      terminal.winner.has_value() ? (*terminal.winner == WHITE ? 1 : -1) : 0;
                    trajectory.labels.emplace_back(std::nullopt);
                    break;
                }
                const std::size_t effectiveMaxGamePly =
                  options.testCandidateFault == SelfplayTestCandidateFault::SafetyLimit
                    ? 0
                    : options.maxGamePly;
                if (trajectory.moves.size() >= effectiveMaxGamePly)
                    throw CandidateRejected("nonterminal safety limit reached");

                const PositionSnapshot       beforeSearch = snapshot(position);
                Search::TrainingSearchResult search =
                  run_training_search(position, request, threads, tt);
                require_restored(position, beforeSearch);
                if (options.nodes == 0 && search.depth != options.depth)
                    throw CandidateRejected(
                      "fixed-depth teacher search did not complete its target");
                if (options.testCandidateFault == SelfplayTestCandidateFault::MissingPv)
                {
                    search.exact = false;
                    search.pv.clear();
                    search.lines.clear();
                }
                TeacherLabel label = teacher_label(search, position);
                const Move   move =
                  options.testCandidateFault == SelfplayTestCandidateFault::IllegalPv
                      ? Move::none()
                      : search.pv[0];
                const std::string token = UCIEngine::move(move, false);
                if (move == Move::none() || UCIEngine::to_move(position, token) != move)
                    throw CandidateRejected("teacher principal move is absent or illegal");
                static_cast<void>(move_wire(token, move));

                const PositionSnapshot beforeMove = snapshot(position);
                StateInfo              temporary;
                position.do_move(move, temporary);
                position.undo_move(move);
                require_restored(position, beforeMove);

                trajectory.moves.push_back(token);
                trajectory.labels.emplace_back(label);
                states.emplace_back();
                position.do_move(move, states.back());
            }

            require(trajectory.labels.size() == trajectory.moves.size() + 1
                      && !trajectory.labels.back().has_value(),
                    "live trajectory label framing drifted");
            const std::size_t trajectoryRecords = trajectory.moves.size() + 1;
            if (trajectoryRecords > options.expectedRecords - output.corpus.recordCount)
                throw CandidateRejected("complete trajectory does not fit the remaining record quota");
            output.corpus.recordCount += trajectoryRecords;
            output.corpus.trajectories.push_back(std::move(trajectory));
        }
        catch (const CandidateRejected& error)
        {
            output.rejected.push_back({candidateIndex, root.id, error.what()});
        }
    }
    if (output.corpus.recordCount != options.expectedRecords)
    {
        std::ostringstream error;
        error << "candidate budget could not produce the exact complete-trajectory record quota";
        for (const CandidateRejection& rejected : output.rejected)
            error << "; candidate=" << rejected.candidateIndex << " root=" << rejected.rootId
                  << " reason=" << rejected.reason;
        throw DatagenError(error.str());
    }
    require(!output.corpus.trajectories.empty(), "self-play accepted no complete trajectories");
    return output;
}

LiveCorpus generate_live_corpus_production(const SelfplayOptions&       options,
                                           const std::vector<BookRoot>& roots,
                                           ThreadPool&                  threads,
                                           TranspositionTable&          tt) {
    require(options.maxCandidateGames <= roots.size(),
            "production candidate budget exceeds the authenticated unique-root count");
    require(options.expectedRecords
              <= options.maxCandidateGames * (std::size_t(options.maxGamePly) + 1),
            "production exact quota exceeds the candidate budget's physical maximum");
    const auto order = deterministic_book_order(roots, options.assignedSeed);

    struct CompleteCandidate {
        Trajectory  trajectory;
        std::size_t records = 0;
    };
    std::vector<CompleteCandidate> complete;
    complete.reserve(options.maxCandidateGames);
    std::vector<std::int32_t> predecessorTrajectory(options.expectedRecords + 1, -1);
    std::vector<std::int32_t> predecessorSum(options.expectedRecords + 1, -1);
    predecessorTrajectory[0] = -2;

    LiveCorpus output;
    bool       quotaReachable = false;
    for (std::uint64_t candidateIndex = 0;
         candidateIndex < options.maxCandidateGames && !quotaReachable; ++candidateIndex)
    {
        ++output.candidatesExamined;
        const BookRoot& root = roots[order[static_cast<std::size_t>(candidateIndex)]];
        const IdBytes   trajectoryId =
          derive_selfplay_id("trajectory", options.campaignId, options.chunkIndex, candidateIndex);
        if (!production_role_eligible(options, trajectoryId))
        {
            ++output.roleIneligibleCandidates;
            continue;
        }

        try
        {
            Trajectory trajectory;
            trajectory.gameId =
              derive_selfplay_id("game", options.campaignId, options.chunkIndex, candidateIndex);
            trajectory.trajectoryId       = trajectoryId;
            trajectory.gameText           = uuid_text(trajectory.gameId);
            trajectory.trajectoryText     = uuid_text(trajectory.trajectoryId);
            trajectory.claimPolicy        = 0;
            trajectory.nonstandardRoot    = true;
            trajectory.teacherUsedNetwork = true;
            trajectory.rootFen            = root.fen;

            std::deque<StateInfo> states(1);
            Position              position(Ruleset::CRAZYHOUSE);
            if (const auto error =
                  position.set(root.fen, false, Ruleset::CRAZYHOUSE, &states.back()))
                throw CandidateRejected("book root became invalid: " + std::string(error->what()));
            if (position.repetition_occurrences() != 1)
                throw CandidateRejected("candidate root history is not fresh");

            for (;;)
            {
                const CrazyhouseTerminalStatus terminal =
                  position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY);
                if (terminal.ended())
                {
                    trajectory.terminalReason = map_terminal_reason(terminal.reason);
                    if (trajectory.terminalReason < 1 || trajectory.terminalReason > 3)
                        throw CandidateRejected("non-automatic terminal reason reached");
                    trajectory.resultWhite =
                      terminal.winner.has_value() ? (*terminal.winner == WHITE ? 1 : -1) : 0;
                    trajectory.labels.emplace_back(std::nullopt);
                    break;
                }
                const std::size_t effectiveMaxGamePly =
                  options.testCandidateFault == SelfplayTestCandidateFault::SafetyLimit
                    ? 0
                    : options.maxGamePly;
                if (trajectory.moves.size() >= effectiveMaxGamePly)
                    throw CandidateRejected("nonterminal safety limit reached");

                const std::uint32_t ply = static_cast<std::uint32_t>(trajectory.moves.size());
                const std::size_t   multiPv =
                  ply < options.explorationPlies ? options.explorationMultiPv : 1;
                const Search::TrainingSearchRequest request{options.depth, options.nodes, multiPv};
                const PositionSnapshot              beforeSearch = snapshot(position);
                Search::TrainingSearchResult        search =
                  run_training_search(position, request, threads, tt);
                require_restored(position, beforeSearch);
                if (options.nodes == 0 && search.depth != options.depth)
                    throw CandidateRejected(
                      "fixed-depth production teacher search did not complete its target");
                if (options.testCandidateFault == SelfplayTestCandidateFault::MissingPv)
                {
                    search.exact = false;
                    search.pv.clear();
                    search.lines.clear();
                }
                const std::size_t lineIndex =
                  select_production_line(options, search, position, trajectory.trajectoryId, ply);
                const TeacherLabel label = production_teacher_label(search, position, lineIndex);
                const Move         move =
                  options.testCandidateFault == SelfplayTestCandidateFault::IllegalPv
                            ? Move::none()
                            : search.lines[lineIndex].pv[0];
                const std::string token = UCIEngine::move(move, false);
                if (move == Move::none() || UCIEngine::to_move(position, token) != move)
                    throw CandidateRejected(
                      "selected production teacher move is absent or illegal");
                static_cast<void>(move_wire(token, move));

                const PositionSnapshot beforeMove = snapshot(position);
                StateInfo              temporary;
                position.do_move(move, temporary);
                position.undo_move(move);
                require_restored(position, beforeMove);

                trajectory.moves.push_back(token);
                trajectory.labels.emplace_back(label);
                states.emplace_back();
                position.do_move(move, states.back());
            }

            require(trajectory.labels.size() == trajectory.moves.size() + 1
                      && !trajectory.labels.back().has_value(),
                    "production live trajectory label framing drifted");
            const std::size_t records = trajectory.moves.size() + 1;
            if (records > options.expectedRecords)
                throw CandidateRejected("complete trajectory record count "
                                        + std::to_string(records)
                                        + " exceeds the exact record quota");

            const std::size_t completeIndex = complete.size();
            complete.push_back({std::move(trajectory), records});
            ++output.roleEligibleCompleteCandidates;
            for (std::size_t sum = options.expectedRecords - records + 1; sum-- > 0;)
            {
                if (predecessorTrajectory[sum] == -1)
                    continue;
                const std::size_t next = sum + records;
                if (predecessorTrajectory[next] == -1)
                {
                    predecessorTrajectory[next] = static_cast<std::int32_t>(completeIndex);
                    predecessorSum[next]        = static_cast<std::int32_t>(sum);
                }
            }
            quotaReachable = predecessorTrajectory[options.expectedRecords] != -1;
        } catch (const CandidateRejected& error)
        { output.rejected.push_back({candidateIndex, root.id, error.what()}); }
    }

    if (!quotaReachable)
    {
        std::ostringstream error;
        error
          << "production candidate budget could not make the exact complete-trajectory quota reachable"
          << "; examined=" << output.candidatesExamined
          << "; role_ineligible=" << output.roleIneligibleCandidates
          << "; complete=" << output.roleEligibleCompleteCandidates;
        for (const CandidateRejection& rejected : output.rejected)
            error << "; candidate=" << rejected.candidateIndex << " root=" << rejected.rootId
                  << " reason=" << rejected.reason;
        throw DatagenError(error.str());
    }

    std::vector<std::size_t> selected;
    for (std::size_t sum = options.expectedRecords; sum != 0;)
    {
        const std::int32_t completeIndex = predecessorTrajectory[sum];
        const std::int32_t previous      = predecessorSum[sum];
        require(completeIndex >= 0 && previous >= 0 && static_cast<std::size_t>(previous) < sum,
                "production exact-subset predecessor chain is invalid");
        selected.push_back(static_cast<std::size_t>(completeIndex));
        sum = static_cast<std::size_t>(previous);
    }
    std::reverse(selected.begin(), selected.end());
    output.corpus.trajectories.reserve(selected.size());
    for (const std::size_t index : selected)
    {
        output.corpus.recordCount += complete[index].records;
        output.corpus.trajectories.push_back(std::move(complete[index].trajectory));
    }
    output.subsetCandidatesOmitted = complete.size() - selected.size();
    require(output.corpus.recordCount == options.expectedRecords
              && !output.corpus.trajectories.empty(),
            "production exact-subset reconstruction drifted");
    return output;
}

Digest selfplay_search_settings_digest(const SelfplayOptions& options) {
    std::ostringstream settings;
    settings << "Crazyhouse-Stockfish selfplay search settings v1\n"
             << "depth=" << options.depth << "\n"
             << "hash_mib=" << options.hashMb << "\n"
             << "history_reset=every-root-search\n"
             << "multipv=1\n"
             << "nodes=" << options.nodes << "\n"
             << "tablebases=disabled\n"
             << "threads=" << options.threads << "\n"
             << "tt_reset=every-root-search\n"
             << "wall_time_encoded=false\n";
    return sha256(settings.str());
}

std::string build_selfplay_provenance(const SelfplayOptions& options,
                                      const ArtifactIdentity& artifact,
                                      const Digest&           capabilityDigest,
                                      std::size_t             capabilityBytes,
                                      const Digest&           bookDigest,
                                      std::size_t             bookBytes,
                                      const LiveCorpus&       live,
                                      std::string_view        routeIdentity,
                                      std::string_view        evaluatorMode) {
    const Digest settingsDigest = selfplay_search_settings_digest(options);
    std::ostringstream output;
    output << "{\"adjudication\":{\"claim_policy\":\"automatic-only\""
           << ",\"fivefold_automatic\":true,\"insufficient_material\":false"
           << ",\"resignation\":false,\"rule50\":false,\"threefold_claim\":false}"
           << ",\"campaign_id\":" << json_string(options.campaignText)
           << ",\"chunk_id\":" << json_string(options.chunkText)
           << ",\"chunk_index\":" << options.chunkIndex
           << ",\"generation_settings\":{\"accepted_trajectories\":"
           << live.corpus.trajectories.size()
           << ",\"base_seed\":" << options.baseSeed
           << ",\"candidate_games_examined\":" << live.candidatesExamined
           << ",\"complete_trajectory_only\":true"
           << ",\"depth\":" << options.depth
           << ",\"exploration\":false,\"hash_mib\":" << options.hashMb
           << ",\"max_candidate_games\":" << options.maxCandidateGames
           << ",\"max_game_ply\":" << options.maxGamePly
           << ",\"multipv\":1,\"nodes\":" << options.nodes
           << ",\"nonstandard_root_policy\":\"g0-fixture-only\""
           << ",\"record_count\":" << options.expectedRecords
           << ",\"threads\":" << options.threads
           << ",\"training_admissible\":false,\"wall_time_encoded\":false}"
           << ",\"invalid_game_policy\":{\"bound_or_missing_pv\":\"quarantine-game\""
           << ",\"complete_trajectory_oversize\":\"quarantine-game\""
           << ",\"crash\":\"abort-chunk\",\"illegal_move\":\"quarantine-game\""
           << ",\"observed_rejections\":[";
    for (std::size_t index = 0; index < live.rejected.size(); ++index)
    {
        if (index != 0)
            output << ',';
        const CandidateRejection& rejected = live.rejected[index];
        output << "{\"candidate_index\":" << rejected.candidateIndex
               << ",\"reason\":" << json_string(rejected.reason)
               << ",\"root_id\":" << json_string(rejected.rootId) << '}';
    }
    output << "]"
           << ",\"safety_limit\":\"quarantine-game\""
           << ",\"unreachable_exact_quota\":\"abort-chunk\"}"
           << ",\"network\":{\"bytes\":" << RegisteredLegacyNetworkBytes
           << ",\"compatibility\":\"qualified-positive-and-negative-load\""
           << ",\"format\":\"legacy-halfkav2variants-v1\""
           << ",\"license\":\"CC0-1.0\",\"path\":"
           << json_string(options.networkRepoPath)
           << ",\"sha256\":" << json_string(options.networkSha256)
           << ",\"used\":true}"
           << ",\"opening_source\":{\"artifact\":{\"bytes\":" << bookBytes
           << ",\"kind\":\"crazyhouse-epd-physical-roots-v1\""
           << ",\"license\":\"GPL-3.0-or-later\",\"path\":"
           << json_string(options.bookRepoPath)
           << ",\"sha256\":" << json_string(hex(bookDigest)) << '}'
           << ",\"engine_selected\":false"
           << ",\"kind\":\"deterministic-authenticated-book-order\""
           << ",\"match_result_selected\":false,\"selection_policy_sha256\":"
           << json_string(options.selectionPolicySha256) << '}'
           << ",\"producer_artifact\":{\"bytes\":" << artifact.bytes
           << ",\"kind\":\"crazyhouse-physical-datagen-selfplay-v1\""
           << ",\"path\":" << json_string(options.artifactRepoPath)
           << ",\"sha256\":" << json_string(hex(artifact.digest)) << '}'
           << ",\"producer_capability\":{\"bytes\":" << capabilityBytes
           << ",\"challenge\":" << json_string(options.challenge)
           << ",\"schema\":\"crazyhouse-datagen-selfplay-capability-response/v1\""
           << ",\"sha256\":" << json_string(hex(capabilityDigest)) << '}'
           << ",\"project\":\"Crazyhouse-Stockfish\""
           << ",\"rule_profile\":{\"id\":" << json_string(CrazyhouseProfile::Id)
           << ",\"sha256\":" << json_string(CrazyhouseProfile::Sha256) << '}'
           << ",\"schema\":\"crazyhouse-datagen-provenance/v1\""
           << ",\"seed\":" << json_string(options.seedText)
           << ",\"source_commit\":" << json_string(DATAGEN_SOURCE_COMMIT)
           << ",\"source_dirty\":false"
           << ",\"source_tree\":" << json_string(DATAGEN_SOURCE_TREE)
           << ",\"src_tree\":" << json_string(DATAGEN_SRC_TREE)
           << ",\"teacher\":{\"artifact\":{\"bytes\":" << artifact.bytes
           << ",\"path\":" << json_string(options.artifactRepoPath)
           << ",\"sha256\":" << json_string(hex(artifact.digest)) << '}'
           << ",\"bound_policy\":\"exact-only-for-ongoing-records\""
           << ",\"evaluator_mode\":" << json_string(evaluatorMode)
           << ",\"kind\":\"legacy-network-product-search\""
           << ",\"network_used\":true,\"route_backend_identity\":"
           << json_string(routeIdentity)
           << ",\"score_perspective\":\"side-to-move\""
           << ",\"search_settings_sha256\":" << json_string(hex(settingsDigest))
           << ",\"synthetic\":false}"
           << ",\"toolchain\":{\"build_recipe_sha256\":"
           << json_string(DATAGEN_BUILD_RECIPE_SHA256)
           << ",\"identity\":" << json_string(DATAGEN_TOOLCHAIN_IDENTITY)
           << ",\"sha256\":" << json_string(DATAGEN_TOOLCHAIN_SHA256) << '}'
           << ",\"variant\":\"crazyhouse\"}\n";
    return output.str();
}

Digest production_search_settings_digest(const SelfplayOptions& options) {
    std::ostringstream settings;
    settings << "Crazyhouse-Stockfish production search settings v1\n"
             << "depth_cap=" << options.depth << "\n"
             << "exploration_max_score_diff=" << options.explorationMaxScoreDiff << "\n"
             << "exploration_multipv=" << options.explorationMultiPv << "\n"
             << "exploration_plies=" << options.explorationPlies << "\n"
             << "hash_mib=" << options.hashMb << "\n"
             << "history_reset=every-position-search\n"
             << "nodes=" << options.nodes << "\n"
             << "tablebases=disabled\n"
             << "threads=" << options.threads << "\n"
             << "tt_reset=every-position-search\n"
             << "wall_time_encoded=false\n";
    return sha256(settings.str());
}

std::string build_production_provenance(const SelfplayOptions&  options,
                                        const ArtifactIdentity& artifact,
                                        const Digest&           capabilityDigest,
                                        std::size_t             capabilityBytes,
                                        const Digest&           bookDigest,
                                        std::size_t             bookBytes,
                                        const LiveCorpus&       live,
                                        std::string_view        routeIdentity,
                                        std::string_view        evaluatorMode) {
    const Digest       settingsDigest = production_search_settings_digest(options);
    std::ostringstream output;
    output << "{\"adjudication\":{\"claim_policy\":\"automatic-only\""
           << ",\"fivefold_automatic\":true,\"insufficient_material\":false"
           << ",\"resignation\":false,\"rule50\":false,\"threefold_claim\":false}"
           << ",\"campaign_id\":" << json_string(options.campaignText)
           << ",\"chunk_id\":" << json_string(options.chunkText)
           << ",\"chunk_index\":" << options.chunkIndex
           << ",\"cohort\":" << json_string(options.cohort)
           << ",\"external_workload_id\":" << json_string(options.externalWorkloadId)
           << ",\"generation_settings\":{\"accepted_trajectories\":"
           << live.corpus.trajectories.size() << ",\"base_seed\":" << options.baseSeed
           << ",\"candidate_games_examined\":" << live.candidatesExamined
           << ",\"complete_trajectory_only\":true"
           << ",\"depth_cap\":" << options.depth << ",\"exact_count\":true"
           << ",\"exact_quota_algorithm\":\"deterministic-first-reachable-exact-subset-v1\""
           << ",\"exploration_max_score_diff_internal\":" << options.explorationMaxScoreDiff
           << ",\"exploration_multipv\":" << options.explorationMultiPv
           << ",\"exploration_plies\":" << options.explorationPlies
           << ",\"fixture_only\":false,\"hash_mib\":" << options.hashMb
           << ",\"max_candidate_games\":" << options.maxCandidateGames
           << ",\"max_game_ply\":" << options.maxGamePly
           << ",\"nodes_per_position\":" << options.nodes
           << ",\"production_generation_authorized\":true"
           << ",\"record_count\":" << options.expectedRecords
           << ",\"role_eligible_complete_candidates\":" << live.roleEligibleCompleteCandidates
           << ",\"role_ineligible_candidates\":" << live.roleIneligibleCandidates
           << ",\"subset_candidates_omitted\":" << live.subsetCandidatesOmitted
           << ",\"threads\":" << options.threads
           << ",\"training_admissible\":true,\"wall_time_encoded\":false}"
           << ",\"invalid_game_policy\":{\"bound_or_missing_pv\":\"quarantine-game\""
           << ",\"complete_trajectory_oversize\":\"quarantine-game\""
           << ",\"crash\":\"abort-chunk\",\"illegal_move\":\"quarantine-game\""
           << ",\"observed_rejections\":[";
    for (std::size_t index = 0; index < live.rejected.size(); ++index)
    {
        if (index != 0)
            output << ',';
        const CandidateRejection& rejected = live.rejected[index];
        output << "{\"candidate_index\":" << rejected.candidateIndex
               << ",\"reason\":" << json_string(rejected.reason)
               << ",\"root_id\":" << json_string(rejected.rootId) << '}';
    }
    output << "]"
           << ",\"safety_limit\":\"quarantine-game\""
           << ",\"unreachable_exact_quota\":\"abort-chunk\"}"
           << ",\"network\":{\"bytes\":" << RegisteredLegacyNetworkBytes
           << ",\"compatibility\":\"qualified-positive-and-negative-load\""
           << ",\"format\":\"legacy-halfkav2variants-v1\""
           << ",\"license\":\"CC0-1.0\",\"path\":" << json_string(options.networkRepoPath)
           << ",\"sha256\":" << json_string(options.networkSha256) << ",\"used\":true}"
           << ",\"official_openbench_origin\":\"https://belzedar.duckdns.org\""
           << ",\"openbench_publication_protocol\":" << options.openbenchProtocol
           << ",\"opening_source\":{\"artifact\":{\"bytes\":" << bookBytes
           << ",\"kind\":\"official-crazyhouse-epd-physical-roots-v1\""
           << ",\"license\":\"GPL-3.0-or-later\",\"path\":" << json_string(options.bookRepoPath)
           << ",\"roots\":" << ProductionBookRoots << ",\"sha256\":" << json_string(hex(bookDigest))
           << '}' << ",\"engine_selected\":false"
           << ",\"kind\":\"deterministic-authenticated-book-order\""
           << ",\"match_result_selected\":false,\"selection_policy_sha256\":"
           << json_string(options.selectionPolicySha256) << '}'
           << ",\"partition\":{\"campaign_set_sha256\":" << json_string(options.campaignSetSha256)
           << ",\"domain\":\"Crazyhouse-Stockfish physical trajectory split v1\\\\0\""
           << ",\"label_free\":true,\"method\":\"content-hash-complete-trajectory-v1\""
           << ",\"partition_sha256\":" << json_string(options.partitionSha256)
           << ",\"posthoc_rebalance\":false,\"role\":" << json_string(options.role)
           << ",\"split_seed_u64\":" << options.splitSeed
           << ",\"validation_threshold_u64\":" << options.validationThreshold << '}'
           << ",\"producer_artifact\":{\"bytes\":" << artifact.bytes
           << ",\"kind\":\"crazyhouse-physical-datagen-production-v1\""
           << ",\"path\":" << json_string(options.artifactRepoPath)
           << ",\"sha256\":" << json_string(hex(artifact.digest)) << '}'
           << ",\"producer_capability\":{\"bytes\":" << capabilityBytes
           << ",\"challenge\":" << json_string(options.challenge)
           << ",\"schema\":\"crazyhouse-datagen-production-capability-response/v1\""
           << ",\"sha256\":" << json_string(hex(capabilityDigest)) << '}'
           << ",\"project\":\"Crazyhouse-Stockfish\""
           << ",\"rule_profile\":{\"id\":" << json_string(CrazyhouseProfile::Id)
           << ",\"sha256\":" << json_string(CrazyhouseProfile::Sha256) << '}'
           << ",\"schema\":\"crazyhouse-datagen-provenance/v1\""
           << ",\"seed\":" << json_string(options.seedText)
           << ",\"source_commit\":" << json_string(DATAGEN_SOURCE_COMMIT)
           << ",\"source_dirty\":false"
           << ",\"source_tree\":" << json_string(DATAGEN_SOURCE_TREE)
           << ",\"src_tree\":" << json_string(DATAGEN_SRC_TREE)
           << ",\"teacher\":{\"artifact\":{\"bytes\":" << artifact.bytes
           << ",\"path\":" << json_string(options.artifactRepoPath)
           << ",\"sha256\":" << json_string(hex(artifact.digest)) << '}'
           << ",\"bound_policy\":\"selected-line-exact-only\""
           << ",\"evaluator_mode\":" << json_string(evaluatorMode)
           << ",\"kind\":\"legacy-network-product-search\""
           << ",\"network_used\":true,\"route_backend_identity\":" << json_string(routeIdentity)
           << ",\"score_perspective\":\"side-to-move\""
           << ",\"search_settings_sha256\":" << json_string(hex(settingsDigest))
           << ",\"selected_line_owns_score_and_pv\":true,\"synthetic\":false}"
           << ",\"toolchain\":{\"build_recipe_sha256\":" << json_string(DATAGEN_BUILD_RECIPE_SHA256)
           << ",\"identity\":" << json_string(DATAGEN_TOOLCHAIN_IDENTITY)
           << ",\"sha256\":" << json_string(DATAGEN_TOOLCHAIN_SHA256) << '}'
           << ",\"variant\":\"crazyhouse\"}\n";
    return output.str();
}

struct GeneratedRecords {
    std::vector<Record> records;
};

GeneratedRecords replay_and_encode(const TrajectoryCorpus& corpus, const Digest& provenanceDigest) {
    GeneratedRecords output;
    output.records.reserve(corpus.recordCount);
    std::uint64_t sequence = 0;
    for (const Trajectory& trajectory : corpus.trajectories)
    {
        require(trajectory.labels.size() == trajectory.moves.size() + 1
                  && !trajectory.labels.back().has_value(),
                "trajectory teacher-label framing is invalid");
        for (std::size_t index = 0; index < trajectory.moves.size(); ++index)
        {
            require(trajectory.labels[index].has_value(),
                    "ongoing trajectory record has no teacher label");
            const TeacherLabel& label = *trajectory.labels[index];
            require((label.kind == 1 || label.kind == 2) && label.nodes > 0
                      && label.depth > 0 && label.selDepth > 0,
                    "ongoing teacher label is incomplete");
        }
        std::deque<StateInfo> states;
        states.emplace_back();
        Position position(Ruleset::CRAZYHOUSE);
        if (const auto error = position.set(trajectory.rootFen, false, Ruleset::CRAZYHOUSE,
                                            &states.back()))
            throw DatagenError("root FEN rejected: " + std::string(error->what()));

        Byte rawEp = raw_ep_from_fen(trajectory.rootFen);
        std::map<std::string, unsigned> occurrences;
        Digest previousHistory = history_initial(trajectory.trajectoryId, provenanceDigest);
        for (std::size_t plyIndex = 0; plyIndex <= trajectory.moves.size(); ++plyIndex)
        {
            require(plyIndex <= std::numeric_limits<std::uint32_t>::max(), "trajectory ply overflow");
            const bool finalRecord = plyIndex == trajectory.moves.size();
            validate_terminal(position, trajectory, finalRecord);

            Move     engineMove = Move::none();
            MoveWire wire{};
            if (!finalRecord)
            {
                engineMove = UCIEngine::to_move(position, trajectory.moves[plyIndex]);
                require(engineMove != Move::none(), "illegal move: " + trajectory.moves[plyIndex]);
                wire = move_wire(trajectory.moves[plyIndex], engineMove);
            }

            const Digest positionDigest = position_identity(position);
            const unsigned occurrence = ++occurrences[hex(positionDigest)];
            require(occurrence <= 255, "repetition occurrence exceeds wire range");
            require(position.repetition_occurrences() == static_cast<int>(occurrence),
                    "production repetition count disagrees with physical history");
            const Digest historyDigest =
              history_step(previousHistory, static_cast<std::uint32_t>(plyIndex), positionDigest, wire);

            Record record{};
            record[0] = 'C';
            record[1] = 'H';
            record[2] = 'R';
            record[3] = '1';
            put_le<std::uint16_t>(record.data() + 4, 1);
            put_le<std::uint16_t>(record.data() + 6, 256);
            put_le<std::uint64_t>(record.data() + 8, sequence);
            std::copy(trajectory.gameId.begin(), trajectory.gameId.end(), record.begin() + 16);
            std::copy(trajectory.trajectoryId.begin(), trajectory.trajectoryId.end(), record.begin() + 32);
            put_le<std::uint32_t>(record.data() + 48, static_cast<std::uint32_t>(plyIndex));
            std::uint32_t flags = finalRecord ? 2U : (1U | 4U);
            if (!finalRecord && trajectory.teacherUsedNetwork)
                flags |= 8U;
            if (plyIndex == 0)
                flags |= 32U;
            if (trajectory.nonstandardRoot)
                flags |= 64U;
            put_le<std::uint32_t>(record.data() + 52, flags);
            const auto board = packed_board(position);
            std::copy(board.begin(), board.end(), record.begin() + 56);
            put_le<std::uint64_t>(record.data() + 88, position.promoted_pieces());
            const auto pocket = pockets(position);
            std::copy(pocket.begin(), pocket.end(), record.begin() + 96);
            record[106] = Byte(position.side_to_move());
            record[107] = castling_rights(position);
            record[108] = rawEp;
            record[109] = Byte(occurrence);
            record[110] = trajectory.claimPolicy;
            record[111] = finalRecord ? trajectory.terminalReason : 0;
            require(position.rule50_count() >= 0, "negative halfmove clock");
            put_le<std::uint32_t>(record.data() + 112,
                                  static_cast<std::uint32_t>(position.rule50_count()));
            const int fullmove = 1 + (position.game_ply() - (position.side_to_move() == BLACK)) / 2;
            require(fullmove >= 1, "invalid fullmove number");
            put_le<std::uint32_t>(record.data() + 116, static_cast<std::uint32_t>(fullmove));
            record[120] = wire.kind;
            record[121] = wire.from;
            record[122] = wire.to;
            record[123] = wire.aux;
            record[124] = static_cast<Byte>(trajectory.resultWhite);
            const std::int8_t resultStm = position.side_to_move() == WHITE
                                          ? trajectory.resultWhite
                                          : std::int8_t(-trajectory.resultWhite);
            record[125] = static_cast<Byte>(resultStm);
            const TeacherLabel* label = finalRecord ? nullptr : &*trajectory.labels[plyIndex];
            record[126] = label == nullptr ? 0 : label->kind;
            record[127] = finalRecord ? 0 : 1;
            put_le<std::int32_t>(record.data() + 128, label == nullptr ? 0 : label->value);
            put_le<std::uint64_t>(record.data() + 132, label == nullptr ? 0 : label->nodes);
            put_le<std::uint16_t>(record.data() + 140, label == nullptr ? 0 : label->depth);
            put_le<std::uint16_t>(record.data() + 142, label == nullptr ? 0 : label->selDepth);
            put_le<std::uint32_t>(record.data() + 144, label == nullptr ? 0 : label->moveTimeMs);
            std::copy(positionDigest.begin(), positionDigest.end(), record.begin() + 148);
            std::copy(historyDigest.begin(), historyDigest.end(), record.begin() + 180);
            std::copy(provenanceDigest.begin(), provenanceDigest.end(), record.begin() + 212);
            record[244] = position.ep_square() == SQ_NONE ? NoSquare : Byte(position.ep_square());
            put_le<std::uint32_t>(record.data() + 252, crc32c(record.data(), 252));
            output.records.push_back(record);
            previousHistory = historyDigest;
            ++sequence;

            if (!finalRecord)
            {
                const PositionSnapshot before = snapshot(position);
                const Piece moved = position.moved_piece(engineMove);
                Byte nextRawEp = NoSquare;
                if (type_of(moved) == PAWN && engineMove.kind() == MoveKind::NORMAL
                    && wire.from != NoSquare && wire.to != NoSquare
                    && std::abs(int(wire.to) - int(wire.from)) == 16)
                    nextRawEp = Byte((unsigned(wire.from) + unsigned(wire.to)) / 2U);

                StateInfo temporary;
                position.do_move(engineMove, temporary);
                position.undo_move(engineMove);
                require_restored(position, before);

                states.emplace_back();
                position.do_move(engineMove, states.back());
                rawEp = nextRawEp;
            }
        }
    }
    require(output.records.size() == corpus.recordCount, "encoded record count drifted");
    return output;
}

Digest digest_from_hex(std::string_view value) {
    require(lowercase_hex(value, 64), "invalid digest text");
    Digest output{};
    auto nibble = [](char ch) -> Byte { return Byte(ch <= '9' ? ch - '0' : ch - 'a' + 10); };
    for (std::size_t index = 0; index < output.size(); ++index)
        output[index] = Byte((nibble(value[index * 2]) << 4) | nibble(value[index * 2 + 1]));
    return output;
}

struct ChunkBytes {
    ByteList bytes;
    Digest   digest{};
};

ChunkBytes build_chunk(const std::vector<Record>& records,
                       const GenerationOptions& options,
                       const Digest& provenanceDigest,
                       const Digest& capabilityDigest) {
    require(!records.empty(), "cannot build an empty chunk");
    ByteList payload;
    payload.reserve(records.size() * RecordBytes);
    for (const Record& record : records)
        append(payload, record);
    const Digest payloadDigest = sha256(payload);

    std::array<Byte, HeaderBytes> header{};
    const std::string_view headerMagic = "CHPHYSV1";
    std::copy(headerMagic.begin(), headerMagic.end(), header.begin());
    put_le<std::uint32_t>(header.data() + 16, 0x01020304U);
    put_le<std::uint16_t>(header.data() + 20, 256);
    put_le<std::uint16_t>(header.data() + 22, 256);
    put_le<std::uint16_t>(header.data() + 24, 128);
    put_le<std::uint16_t>(header.data() + 26, 1);
    put_le<std::uint16_t>(header.data() + 28, 0);
    put_le<std::uint32_t>(header.data() + 32, 1);
    put_le<std::uint64_t>(header.data() + 40, records.size());
    std::copy(options.chunkId.begin(), options.chunkId.end(), header.begin() + 48);
    std::copy(options.campaignId.begin(), options.campaignId.end(), header.begin() + 64);
    const Digest profileDigest = digest_from_hex(CrazyhouseProfile::Sha256);
    const Digest schemaDigest  = digest_from_hex(PhysicalSchemaSha256);
    std::copy(profileDigest.begin(), profileDigest.end(), header.begin() + 80);
    std::copy(schemaDigest.begin(), schemaDigest.end(), header.begin() + 112);
    std::copy(provenanceDigest.begin(), provenanceDigest.end(), header.begin() + 144);
    std::copy(payloadDigest.begin(), payloadDigest.end(), header.begin() + 176);
    std::copy(capabilityDigest.begin(), capabilityDigest.end(), header.begin() + 208);
    put_le<std::uint32_t>(header.data() + 252, crc32c(header.data(), 252));
    const Digest headerDigest = sha256(header.data(), header.size());

    std::array<Byte, FooterBytes> footer{};
    const std::string_view footerMagic = "CHPHYSENDV1";
    std::copy(footerMagic.begin(), footerMagic.end(), footer.begin());
    put_le<std::uint16_t>(footer.data() + 16, 128);
    put_le<std::uint16_t>(footer.data() + 18, 1);
    put_le<std::uint32_t>(footer.data() + 20, 1);
    put_le<std::uint64_t>(footer.data() + 24, records.size());
    put_le<std::uint64_t>(footer.data() + 32, records.size() * RecordBytes);
    std::copy(payloadDigest.begin(), payloadDigest.end(), footer.begin() + 40);
    std::copy(headerDigest.begin(), headerDigest.end(), footer.begin() + 72);
    std::copy(options.chunkId.begin(), options.chunkId.end(), footer.begin() + 104);
    put_le<std::uint32_t>(footer.data() + 124, crc32c(footer.data(), 124));

    ChunkBytes output;
    output.bytes.reserve(header.size() + payload.size() + footer.size());
    append(output.bytes, header);
    append(output.bytes, payload.data(), payload.size());
    append(output.bytes, footer);
    output.digest = sha256(output.bytes);
    return output;
}

void verify_chunk(const ByteList& bytes,
                  const GenerationOptions& options,
                  const Digest& provenanceDigest,
                  const Digest& capabilityDigest,
                  std::size_t expectedRecords) {
    require(bytes.size() == HeaderBytes + expectedRecords * RecordBytes + FooterBytes,
            "chunk exact framing mismatch");
    const Byte* header = bytes.data();
    const Byte* footer = bytes.data() + bytes.size() - FooterBytes;
    require(std::memcmp(header, "CHPHYSV1", 8) == 0
              && std::all_of(header + 8, header + 16, [](Byte value) { return value == 0; }),
            "header magic mismatch");
    require(std::memcmp(footer, "CHPHYSENDV1", 11) == 0
              && std::all_of(footer + 11, footer + 16, [](Byte value) { return value == 0; }),
            "footer magic mismatch");
    require(get_le<std::uint32_t>(header + 252) == crc32c(header, 252), "header CRC32C mismatch");
    require(get_le<std::uint32_t>(footer + 124) == crc32c(footer, 124), "footer CRC32C mismatch");
    require(get_le<std::uint32_t>(header + 16) == 0x01020304U
              && get_le<std::uint16_t>(header + 20) == 256
              && get_le<std::uint16_t>(header + 22) == 256
              && get_le<std::uint16_t>(header + 24) == 128
              && get_le<std::uint16_t>(header + 26) == 1
              && get_le<std::uint16_t>(header + 28) == 0
              && get_le<std::uint32_t>(header + 32) == 1,
            "header layout/version mismatch");
    require(get_le<std::uint64_t>(header + 40) == expectedRecords
              && get_le<std::uint64_t>(footer + 24) == expectedRecords
              && get_le<std::uint64_t>(footer + 32) == expectedRecords * RecordBytes,
            "chunk count mismatch");
    require(std::equal(options.chunkId.begin(), options.chunkId.end(), header + 48)
              && std::equal(options.chunkId.begin(), options.chunkId.end(), footer + 104)
              && std::equal(options.campaignId.begin(), options.campaignId.end(), header + 64),
            "chunk/campaign identity mismatch");
    require(std::equal(provenanceDigest.begin(), provenanceDigest.end(), header + 144)
              && std::equal(capabilityDigest.begin(), capabilityDigest.end(), header + 208),
            "chunk provenance/capability mismatch");
    const Byte* payload = header + HeaderBytes;
    const std::size_t payloadBytes = expectedRecords * RecordBytes;
    const Digest payloadDigest = sha256(payload, payloadBytes);
    require(std::equal(payloadDigest.begin(), payloadDigest.end(), header + 176)
              && std::equal(payloadDigest.begin(), payloadDigest.end(), footer + 40),
            "payload SHA-256 mismatch");
    const Digest headerDigest = sha256(header, HeaderBytes);
    require(std::equal(headerDigest.begin(), headerDigest.end(), footer + 72),
            "footer/header SHA-256 mismatch");
    for (std::size_t index = 0; index < expectedRecords; ++index)
    {
        const Byte* record = payload + index * RecordBytes;
        require(std::memcmp(record, "CHR1", 4) == 0, "record magic mismatch");
        require(get_le<std::uint16_t>(record + 4) == 1
                  && get_le<std::uint16_t>(record + 6) == 256
                  && get_le<std::uint64_t>(record + 8) == index,
                "record version/sequence mismatch");
        require(get_le<std::uint32_t>(record + 252) == crc32c(record, 252),
                "record CRC32C mismatch");
        require(std::equal(provenanceDigest.begin(), provenanceDigest.end(), record + 212),
                "record provenance mismatch");
        require(std::all_of(record + 245, record + 252, [](Byte value) { return value == 0; }),
                "record reserved bytes are nonzero");
    }
}

struct BundleBytes {
    ByteList bytes;
    Digest   digest{};
};

BundleBytes build_bundle(const ByteList& capability,
                         const ByteList& provenance,
                         const ChunkBytes& chunk) {
    require(capability.size() >= 2 && capability.size() <= 65536,
            "bundle capability section length is invalid");
    require(provenance.size() >= 2 && provenance.size() <= 1048576,
            "bundle provenance section length is invalid");
    require(chunk.bytes.size() >= HeaderBytes + RecordBytes + FooterBytes,
            "bundle physical section is too short");
    const Digest capabilityDigest = sha256(capability);
    const Digest provenanceDigest = sha256(provenance);
    const Digest chunkDigest      = sha256(chunk.bytes);
    require(chunkDigest == chunk.digest, "physical chunk digest drifted before bundling");

    ByteList payload;
    payload.reserve(capability.size() + provenance.size() + chunk.bytes.size());
    append(payload, capability.data(), capability.size());
    append(payload, provenance.data(), provenance.size());
    append(payload, chunk.bytes.data(), chunk.bytes.size());
    const Digest payloadDigest = sha256(payload);
    const std::uint64_t totalBytes = HeaderBytes + payload.size() + FooterBytes;

    std::array<Byte, HeaderBytes> header{};
    std::copy_n("CHBNDLV1", 8, header.begin());
    put_le<std::uint32_t>(header.data() + 16, 0x01020304U);
    put_le<std::uint16_t>(header.data() + 20, 256);
    put_le<std::uint16_t>(header.data() + 22, 128);
    put_le<std::uint16_t>(header.data() + 24, 1);
    put_le<std::uint16_t>(header.data() + 26, 0);
    put_le<std::uint32_t>(header.data() + 28, 3);
    put_le<std::uint64_t>(header.data() + 32, totalBytes);
    put_le<std::uint64_t>(header.data() + 40, capability.size());
    put_le<std::uint64_t>(header.data() + 48, provenance.size());
    put_le<std::uint64_t>(header.data() + 56, chunk.bytes.size());
    std::copy(capabilityDigest.begin(), capabilityDigest.end(), header.begin() + 64);
    std::copy(provenanceDigest.begin(), provenanceDigest.end(), header.begin() + 96);
    std::copy(chunkDigest.begin(), chunkDigest.end(), header.begin() + 128);
    std::copy(payloadDigest.begin(), payloadDigest.end(), header.begin() + 160);
    const Digest schemaDigest = digest_from_hex(DatagenBundleSchemaSha256);
    std::copy(schemaDigest.begin(), schemaDigest.end(), header.begin() + 192);
    put_le<std::uint32_t>(header.data() + 252, crc32c(header.data(), 252));
    const Digest headerDigest = sha256(header.data(), header.size());

    std::array<Byte, FooterBytes> footer{};
    std::copy_n("CHBNDENDV1", 10, footer.begin());
    put_le<std::uint16_t>(footer.data() + 16, 128);
    put_le<std::uint16_t>(footer.data() + 18, 1);
    put_le<std::uint32_t>(footer.data() + 20, 3);
    put_le<std::uint64_t>(footer.data() + 24, totalBytes);
    put_le<std::uint64_t>(footer.data() + 32, payload.size());
    std::copy(payloadDigest.begin(), payloadDigest.end(), footer.begin() + 40);
    std::copy(headerDigest.begin(), headerDigest.end(), footer.begin() + 72);
    put_le<std::uint32_t>(footer.data() + 124, crc32c(footer.data(), 124));

    BundleBytes output;
    output.bytes.reserve(static_cast<std::size_t>(totalBytes));
    append(output.bytes, header);
    append(output.bytes, payload.data(), payload.size());
    append(output.bytes, footer);
    output.digest = sha256(output.bytes);
    return output;
}

void verify_bundle(const ByteList&           bytes,
                   const ByteList&           expectedCapability,
                   const ByteList&           expectedProvenance,
                   const ChunkBytes&         expectedChunk,
                   const GenerationOptions& physicalOptions,
                   const Digest&             provenanceDigest,
                   const Digest&             capabilityDigest,
                   std::size_t               expectedRecords) {
    require(bytes.size() >= HeaderBytes + 2 + 2 + HeaderBytes + RecordBytes
                             + FooterBytes + FooterBytes,
            "bundle is too short");
    const Byte* header = bytes.data();
    const Byte* footer = bytes.data() + bytes.size() - FooterBytes;
    require(std::memcmp(header, "CHBNDLV1", 8) == 0
              && std::all_of(header + 8, header + 16, [](Byte value) { return value == 0; }),
            "bundle header magic mismatch");
    require(std::memcmp(footer, "CHBNDENDV1", 10) == 0
              && std::all_of(footer + 10, footer + 16, [](Byte value) { return value == 0; }),
            "bundle footer magic mismatch");
    require(get_le<std::uint32_t>(header + 252) == crc32c(header, 252),
            "bundle header CRC32C mismatch");
    require(get_le<std::uint32_t>(footer + 124) == crc32c(footer, 124),
            "bundle footer CRC32C mismatch");
    require(get_le<std::uint32_t>(header + 16) == 0x01020304U
              && get_le<std::uint16_t>(header + 20) == 256
              && get_le<std::uint16_t>(header + 22) == 128
              && get_le<std::uint16_t>(header + 24) == 1
              && get_le<std::uint16_t>(header + 26) == 0
              && get_le<std::uint32_t>(header + 28) == 3
              && get_le<std::uint16_t>(footer + 16) == 128
              && get_le<std::uint16_t>(footer + 18) == 1
              && get_le<std::uint32_t>(footer + 20) == 3,
            "bundle layout/version mismatch");
    const std::uint64_t capabilityBytes = get_le<std::uint64_t>(header + 40);
    const std::uint64_t provenanceBytes = get_le<std::uint64_t>(header + 48);
    const std::uint64_t chunkBytes      = get_le<std::uint64_t>(header + 56);
    require(capabilityBytes == expectedCapability.size()
              && provenanceBytes == expectedProvenance.size()
              && chunkBytes == expectedChunk.bytes.size(),
            "bundle section length mismatch");
    const std::uint64_t payloadBytes = capabilityBytes + provenanceBytes + chunkBytes;
    require(payloadBytes >= capabilityBytes && payloadBytes >= provenanceBytes
              && payloadBytes >= chunkBytes,
            "bundle section length overflow");
    const std::uint64_t totalBytes = HeaderBytes + payloadBytes + FooterBytes;
    require(totalBytes == bytes.size() && get_le<std::uint64_t>(header + 32) == totalBytes
              && get_le<std::uint64_t>(footer + 24) == totalBytes
              && get_le<std::uint64_t>(footer + 32) == payloadBytes,
            "bundle total length mismatch");
    require(std::all_of(header + 224, header + 252, [](Byte value) { return value == 0; })
              && std::all_of(footer + 104, footer + 124,
                             [](Byte value) { return value == 0; }),
            "bundle reserved bytes are nonzero");

    const Digest schemaDigest = digest_from_hex(DatagenBundleSchemaSha256);
    require(std::equal(schemaDigest.begin(), schemaDigest.end(), header + 192),
            "bundle schema binding mismatch");
    const Byte* capability = header + HeaderBytes;
    const Byte* provenance = capability + capabilityBytes;
    const Byte* chunk      = provenance + provenanceBytes;
    require(std::equal(expectedCapability.begin(), expectedCapability.end(), capability),
            "bundle capability bytes drifted");
    require(std::equal(expectedProvenance.begin(), expectedProvenance.end(), provenance),
            "bundle provenance bytes drifted");
    require(std::equal(expectedChunk.bytes.begin(), expectedChunk.bytes.end(), chunk),
            "bundle physical chunk bytes drifted");
    require(std::equal(capabilityDigest.begin(), capabilityDigest.end(), header + 64)
              && std::equal(provenanceDigest.begin(), provenanceDigest.end(), header + 96)
              && std::equal(expectedChunk.digest.begin(), expectedChunk.digest.end(), header + 128),
            "bundle section SHA-256 mismatch");
    const Digest payloadDigest = sha256(header + HeaderBytes,
                                        static_cast<std::size_t>(payloadBytes));
    require(std::equal(payloadDigest.begin(), payloadDigest.end(), header + 160)
              && std::equal(payloadDigest.begin(), payloadDigest.end(), footer + 40),
            "bundle payload SHA-256 mismatch");
    const Digest headerDigest = sha256(header, HeaderBytes);
    require(std::equal(headerDigest.begin(), headerDigest.end(), footer + 72),
            "bundle footer/header SHA-256 mismatch");
    const ByteList nestedChunk(chunk, chunk + chunkBytes);
    verify_chunk(nestedChunk, physicalOptions, provenanceDigest, capabilityDigest,
                 expectedRecords);
}

void write_all_exclusive(const std::filesystem::path& path, const ByteList& bytes) {
#ifdef _WIN32
    const int descriptor = _wopen(path.wstring().c_str(), _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY,
                                  _S_IREAD | _S_IWRITE);
#else
    const int descriptor = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
#endif
    require(descriptor >= 0, "exclusive partial create failed: " + path.string());
    std::size_t offset = 0;
    try
    {
        while (offset < bytes.size())
        {
            const std::size_t remaining = bytes.size() - offset;
#ifdef _WIN32
            const unsigned request = static_cast<unsigned>(
              std::min<std::size_t>(remaining, std::numeric_limits<unsigned>::max()));
            const int written = _write(descriptor, bytes.data() + offset, request);
#else
            const ssize_t written = ::write(descriptor, bytes.data() + offset, remaining);
#endif
            require(written > 0, "partial write failed: " + path.string());
            offset += static_cast<std::size_t>(written);
        }
#ifdef _WIN32
        require(_commit(descriptor) == 0, "partial fsync failed: " + path.string());
        require(_close(descriptor) == 0, "partial close failed: " + path.string());
#else
        require(::fsync(descriptor) == 0, "partial fsync failed: " + path.string());
        require(::close(descriptor) == 0, "partial close failed: " + path.string());
#endif
    }
    catch (...)
    {
#ifdef _WIN32
        _close(descriptor);
#else
        ::close(descriptor);
#endif
        throw;
    }
}

void fsync_parent(const std::filesystem::path& path) {
#ifndef _WIN32
    const int descriptor = ::open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY);
    require(descriptor >= 0, "cannot open output directory for fsync");
    const int synced = ::fsync(descriptor);
    const int closed = ::close(descriptor);
    require(synced == 0 && closed == 0, "output directory fsync failed");
#else
    static_cast<void>(path);
#endif
}

void publish_no_replace(const std::filesystem::path& partial, const std::filesystem::path& final) {
    require(!std::filesystem::exists(final), "final output already exists: " + final.string());
#ifdef _WIN32
    require(MoveFileExW(partial.wstring().c_str(), final.wstring().c_str(), MOVEFILE_WRITE_THROUGH)
              != 0,
            "atomic publish failed: " + final.string());
#else
    require(::link(partial.c_str(), final.c_str()) == 0,
            "atomic no-replace publish failed: " + final.string());
    require(::unlink(partial.c_str()) == 0, "cannot unlink published partial: " + partial.string());
    fsync_parent(final);
#endif
}

ByteList bytes_of(std::string_view text) {
    return ByteList(reinterpret_cast<const Byte*>(text.data()),
                    reinterpret_cast<const Byte*>(text.data()) + text.size());
}

void validate_text_binding(const ByteList& bytes, std::string_view expectedHash, std::string_view label) {
    require(hex(sha256(bytes)) == expectedHash, std::string(label) + " byte identity mismatch");
    require(!bytes.empty() && bytes.back() == '\n', std::string(label) + " must end in LF");
    require(std::find(bytes.begin(), bytes.end(), Byte{'\r'}) == bytes.end(),
            std::string(label) + " contains CR");
}

int generate(int argc, char* argv[], const ArtifactIdentity& artifact) {
    const GenerationOptions options = parse_generation_options(argc, argv);
    validate_compiled_identity();
    require(DATAGEN_SOURCE_DIRTY == 0, "dirty source build is not admitted for generation");

    const ByteList schemaBytes   = read_file(options.schemaPath);
    const ByteList contractBytes = read_file(options.contractPath);
    validate_text_binding(schemaBytes, PhysicalSchemaSha256, "physical schema");
    validate_text_binding(contractBytes, CapabilityContractSha256, "capability contract");
    const ByteList inputBytes = read_file(options.inputPath);
    const TrajectoryCorpus corpus = parse_trajectory_corpus(
      inputBytes, options.expectedTrajectories, options.expectedRecords);

    const std::string capability = capability_response(artifact, options.challenge);
    const ByteList capabilityBytes = bytes_of(capability);
    const Digest capabilityDigest = sha256(capabilityBytes);
    const Digest inputDigest      = sha256(inputBytes);
    const std::string provenance = build_provenance(options, artifact, capabilityDigest,
                                                     capabilityBytes.size(), inputDigest,
                                                     inputBytes.size());
    const ByteList provenanceBytes = bytes_of(provenance);
    const Digest provenanceDigest = sha256(provenanceBytes);

    Attacks::init();
    Position::init();
    const GeneratedRecords generated = replay_and_encode(corpus, provenanceDigest);
    const ChunkBytes chunk = build_chunk(generated.records, options, provenanceDigest,
                                         capabilityDigest);
    verify_chunk(chunk.bytes, options, provenanceDigest, capabilityDigest, options.expectedRecords);

    const auto parent = options.outputPath.parent_path();
    require(!parent.empty() && std::filesystem::is_directory(parent),
            "output parent must already exist");
    const std::filesystem::path capabilityFinal = options.outputPath.string() + ".capability.json";
    const std::filesystem::path provenanceFinal = options.outputPath.string() + ".provenance.json";
    const std::string partialSuffix = ".partial." + options.chunkText;
    const std::filesystem::path chunkPartial = options.outputPath.string() + partialSuffix;
    const std::filesystem::path capabilityPartial = capabilityFinal.string() + partialSuffix;
    const std::filesystem::path provenancePartial = provenanceFinal.string() + partialSuffix;
    for (const auto& path : {options.outputPath, capabilityFinal, provenanceFinal,
                             chunkPartial, capabilityPartial, provenancePartial})
        require(!std::filesystem::exists(path), "output namespace is not fresh: " + path.string());

    write_all_exclusive(capabilityPartial, capabilityBytes);
    write_all_exclusive(provenancePartial, provenanceBytes);
    write_all_exclusive(chunkPartial, chunk.bytes);

    if (options.pauseAfterPartialMs != 0)
        std::this_thread::sleep_for(std::chrono::milliseconds(options.pauseAfterPartialMs));

    const ByteList rereadCapability = read_file(capabilityPartial);
    const ByteList rereadProvenance = read_file(provenancePartial);
    const ByteList rereadChunk      = read_file(chunkPartial);
    require(rereadCapability == capabilityBytes && sha256(rereadCapability) == capabilityDigest,
            "capability partial verification failed");
    require(rereadProvenance == provenanceBytes && sha256(rereadProvenance) == provenanceDigest,
            "provenance partial verification failed");
    verify_chunk(rereadChunk, options, provenanceDigest, capabilityDigest, options.expectedRecords);
    require(sha256(rereadChunk) == chunk.digest, "chunk partial digest drifted");

    publish_no_replace(capabilityPartial, capabilityFinal);
    publish_no_replace(provenancePartial, provenanceFinal);
    publish_no_replace(chunkPartial, options.outputPath);
    fsync_parent(options.outputPath);

    std::ostringstream result;
    result << "{\"artifact_sha256\":" << json_string(hex(artifact.digest))
           << ",\"capability_sha256\":" << json_string(hex(capabilityDigest))
           << ",\"chunk_bytes\":" << chunk.bytes.size()
           << ",\"chunk_id\":" << json_string(options.chunkText)
           << ",\"chunk_sha256\":" << json_string(hex(chunk.digest))
           << ",\"output\":" << json_string(options.outputPath.filename().string())
           << ",\"provenance_sha256\":" << json_string(hex(provenanceDigest))
           << ",\"records\":" << options.expectedRecords
           << ",\"schema\":\"crazyhouse-datagen-generation-result/v1\""
           << ",\"status\":\"committed\""
           << ",\"trajectories\":" << options.expectedTrajectories << "}\n";
    std::cout << result.str();
    return EXIT_SUCCESS;
}

int generate_selfplay(SelfplayOptions         options,
                      const ArtifactIdentity& artifact,
                      Engine&                 engine,
                      ThreadPool&             threads,
                      TranspositionTable&     tt) {
    validate_compiled_identity();
    require(DATAGEN_SOURCE_DIRTY == 0,
            "dirty source build is not admitted for self-play generation");
    require(options.producerSha256 == hex(artifact.digest),
            "rendered producer SHA-256 does not match the running executable");
    options.challenge = derive_selfplay_challenge(options.campaignId, options.chunkId,
                                                  options.assignedSeed, artifact.digest);
    require(lowercase_hex(options.challenge, 32), "derived self-play challenge is invalid");

    std::error_code ec;
    require(std::filesystem::is_regular_file(options.bookPath, ec) && !ec,
            "self-play book is not a regular file");
    ec.clear();
    require(std::filesystem::is_regular_file(options.networkPath, ec) && !ec,
            "self-play network is not a regular file");
    const ByteList    bookBytes     = read_file(options.bookPath);
    const ByteList    networkBytes  = read_file(options.networkPath);
    const Digest      bookDigest    = sha256(bookBytes);
    const Digest      networkDigest = sha256(networkBytes);
    const std::size_t expectedBookBytes =
      options.mode == SelfplayMode::ProductionV1 ? ProductionBookBytes : SelfplayG0BookBytes;
    require(bookBytes.size() == expectedBookBytes && hex(bookDigest) == options.bookSha256,
            "self-play book byte identity mismatch");
    require(networkBytes.size() == RegisteredLegacyNetworkBytes
              && hex(networkDigest) == options.networkSha256,
            "self-play network byte identity mismatch");

    const auto parent = options.outputPath.parent_path();
    require(!parent.empty() && std::filesystem::is_directory(parent),
            "self-play output parent must already exist");
    const std::filesystem::path partial =
      options.outputPath.string() + ".partial." + options.chunkText;
    require(!std::filesystem::exists(options.outputPath) && !std::filesystem::exists(partial),
            "self-play output namespace is not fresh");

    const std::vector<BookRoot> roots = options.mode == SelfplayMode::ProductionV1
                                        ? parse_production_book(bookBytes)
                                        : parse_selfplay_book(bookBytes);
    if (options.mode == SelfplayMode::ProductionV1)
        require(roots.size() == ProductionBookRoots,
                "production book root count does not match the frozen contract");
    require(threads.num_threads() == 1 && options.threads == 1,
            "self-play runtime is not single-threaded");
    engine.set_tt_size(options.hashMb);
    require(engine.stage_ruleset("crazyhouse"), "cannot stage Crazyhouse self-play route");
    engine.stage_chess960(false);
    engine.stage_crazyhouse_profile(std::string(CrazyhouseProfile::Token));
    engine.stage_crazyhouse_eval_file(options.networkPath.string());
    const EngineRouting::ApplyResult apply = engine.apply_pending_route();
    require(apply.ready && apply.error == EngineRouting::ErrorCode::None,
            "self-play legacy route load failed: "
              + std::string(EngineRouting::error_code_name(apply.error)));
    const EngineRouting::Snapshot& route = engine.routing_snapshot();
    require(route.active.has_value() && route.active->ruleset == Ruleset::CRAZYHOUSE
              && !route.active->chess960
              && route.active->crazyhouseProfile == CrazyhouseProfile::Token
              && route.backend.kind == EngineRouting::BackendKind::LegacyCrazyhouseV1
              && route.backend.readiness == EngineRouting::BackendReadiness::Ready
              && route.backend.identity == RegisteredLegacyNetworkSha256
              && engine.has_routed_legacy_network(),
            "self-play route identity/readiness mismatch");

    const LiveCorpus live = options.mode == SelfplayMode::ProductionV1
                            ? generate_live_corpus_production(options, roots, threads, tt)
                            : generate_live_corpus(options, roots, threads, tt);

    // Close the time-of-check/time-of-use gap before any output is opened.
    require(read_file(options.bookPath) == bookBytes, "self-play book changed during generation");
    require(read_file(options.networkPath) == networkBytes,
            "self-play network changed during generation");
    const ByteList stableArtifact = read_file(artifact.path);
    require(stableArtifact.size() == artifact.bytes && sha256(stableArtifact) == artifact.digest,
            "self-play producer changed during generation");

    const std::string capability       = options.mode == SelfplayMode::ProductionV1
                                         ? production_capability_response(artifact, options.challenge)
                                         : selfplay_capability_response(artifact, options.challenge);
    const ByteList    capabilityBytes  = bytes_of(capability);
    const Digest      capabilityDigest = sha256(capabilityBytes);
    const std::string provenance =
      options.mode == SelfplayMode::ProductionV1
        ? build_production_provenance(options, artifact, capabilityDigest, capabilityBytes.size(),
                                      bookDigest, bookBytes.size(), live, route.backend.identity,
                                      engine.routed_legacy_evaluator_mode())
        : build_selfplay_provenance(options, artifact, capabilityDigest, capabilityBytes.size(),
                                    bookDigest, bookBytes.size(), live, route.backend.identity,
                                    engine.routed_legacy_evaluator_mode());
    const ByteList provenanceBytes  = bytes_of(provenance);
    const Digest   provenanceDigest = sha256(provenanceBytes);

    const GeneratedRecords generated = replay_and_encode(live.corpus, provenanceDigest);
    GenerationOptions      physicalOptions;
    physicalOptions.campaignText    = options.campaignText;
    physicalOptions.chunkText       = options.chunkText;
    physicalOptions.campaignId      = options.campaignId;
    physicalOptions.chunkId         = options.chunkId;
    physicalOptions.chunkIndex      = options.chunkIndex;
    physicalOptions.expectedRecords = options.expectedRecords;
    const ChunkBytes chunk =
      build_chunk(generated.records, physicalOptions, provenanceDigest, capabilityDigest);
    verify_chunk(chunk.bytes, physicalOptions, provenanceDigest, capabilityDigest,
                 options.expectedRecords);
    const BundleBytes bundle = build_bundle(capabilityBytes, provenanceBytes, chunk);
    verify_bundle(bundle.bytes, capabilityBytes, provenanceBytes, chunk, physicalOptions,
                  provenanceDigest, capabilityDigest, options.expectedRecords);

    require(!std::filesystem::exists(options.outputPath) && !std::filesystem::exists(partial),
            "self-play output namespace changed before commit");
    write_all_exclusive(partial, bundle.bytes);
    if (options.pauseAfterPartialMs != 0)
        std::this_thread::sleep_for(std::chrono::milliseconds(options.pauseAfterPartialMs));
    const ByteList reread = read_file(partial);
    verify_bundle(reread, capabilityBytes, provenanceBytes, chunk, physicalOptions,
                  provenanceDigest, capabilityDigest, options.expectedRecords);
    require(sha256(reread) == bundle.digest, "self-play bundle partial digest drifted");
    publish_no_replace(partial, options.outputPath);
    fsync_parent(options.outputPath);

    std::ostringstream result;
    result << "{\"artifact_sha256\":" << json_string(hex(artifact.digest))
           << ",\"bundle_bytes\":" << bundle.bytes.size()
           << ",\"bundle_sha256\":" << json_string(hex(bundle.digest))
           << ",\"capability_sha256\":" << json_string(hex(capabilityDigest))
           << ",\"chunk_id\":" << json_string(options.chunkText)
           << ",\"chunk_sha256\":" << json_string(hex(chunk.digest))
           << ",\"output\":" << json_string(options.outputPath.filename().string())
           << ",\"provenance_sha256\":" << json_string(hex(provenanceDigest))
           << ",\"records\":" << options.expectedRecords << ",\"schema\":"
           << json_string(options.mode == SelfplayMode::ProductionV1
                            ? "crazyhouse-datagen-production-result/v1"
                            : "crazyhouse-datagen-selfplay-result/v1")
           << ",\"status\":\"committed\""
           << ",\"trajectories\":" << live.corpus.trajectories.size() << "}\n";
    std::cout << result.str();
    return EXIT_SUCCESS;
}

void crypto_self_test() {
    require(hex(sha256(std::string_view{}))
              == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "SHA-256 self-test failed");
    constexpr std::string_view Vector = "123456789";
    require(crc32c(reinterpret_cast<const Byte*>(Vector.data()), Vector.size()) == 0xE3069283U,
            "CRC32C self-test failed");
}

}  // namespace

int run(int argc, char* argv[]) {
    try
    {
#ifdef _WIN32
        require(_setmode(_fileno(stdin), _O_BINARY) != -1,
                "cannot set canonical binary stdin mode");
        require(_setmode(_fileno(stdout), _O_BINARY) != -1,
                "cannot set canonical binary stdout mode");
        require(_setmode(_fileno(stderr), _O_BINARY) != -1,
                "cannot set canonical binary stderr mode");
#endif
        crypto_self_test();
        require(argc >= 1 && argv != nullptr && argv[0] != nullptr, "producer argv is unavailable");
        const ArtifactIdentity artifact = identify_artifact(argv[0]);
        if (argc == 4 && std::string_view(argv[1]) == "--datagen-capabilities-v1"
            && std::string_view(argv[2]) == "--challenge")
        {
            std::cout << capability_response(artifact, argv[3]);
            return EXIT_SUCCESS;
        }
        if (argc == 4 && std::string_view(argv[1]) == "--datagen-selfplay-capabilities-v1"
            && std::string_view(argv[2]) == "--challenge")
        {
            std::cout << selfplay_capability_response(artifact, argv[3]);
            return EXIT_SUCCESS;
        }
        if (argc == 4 && std::string_view(argv[1]) == "--datagen-production-capabilities-v1"
            && std::string_view(argv[2]) == "--challenge")
        {
            std::cout << production_capability_response(artifact, argv[3]);
            return EXIT_SUCCESS;
        }
        if (argc >= 2 && std::string_view(argv[1]) == "--generate-trajectories-v1")
            return generate(argc, argv, artifact);
        if (argc == 1)
        {
            SelfplayOptions options = parse_selfplay_options(read_selfplay_stdin());
            Attacks::init();
            Position::init();
            Engine engine(std::optional<std::filesystem::path>{artifact.path},
                          Engine::LegacyExecutionBackend::Scalar);
            return generate_selfplay(std::move(options), artifact, engine, engine.threads,
                                     engine.tt);
        }
        throw DatagenError("unsupported invocation");
    } catch (const std::exception& error)
    {
        std::cerr << "ERROR crazyhouse-datagen-v1: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}

}  // namespace Stockfish::CrazyhouseDatagen
