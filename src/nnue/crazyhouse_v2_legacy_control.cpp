/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_legacy_control.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <fstream>
#include <limits>
#include <system_error>
#include <utility>
#include <vector>

namespace Stockfish::Eval::NNUE {

namespace {

using Network = LegacyControlNetworkV2;
using u8      = std::uint8_t;
using u16     = std::uint16_t;
using u32     = std::uint32_t;
using u64     = std::uint64_t;

constexpr u32 EndianTag           = 0x01020304U;
constexpr u16 VersionMajor        = 1;
constexpr u16 VersionMinor        = 0;
constexpr u8  CommittedFlag       = 1;
constexpr u8  PurposeLegacy       = 1;
constexpr u8  OriginLegacy        = 1;
constexpr u8  ArithmeticId        = 1;
constexpr u32 DirectoryOffset     = 384;
constexpr u16 DirectoryEntryBytes = 64;
constexpr u32 HeaderCrcOffset     = 1'020;

struct SectionSpec {
    u16                id;
    u8                 dtype;
    u8                 rank;
    u64                offset;
    u64                bytes;
    std::array<u32, 3> shape;
};

constexpr std::array<SectionSpec, 9> Sections = {{
  {1, 2, 1, 1'024, 1'024, {512, 0, 0}},
  {2, 2, 2, 2'048, 56'623'104, {55'296, 512, 0}},
  {3, 3, 2, 56'625'152, 1'769'472, {55'296, 8, 0}},
  {4, 3, 2, 58'394'624, 512, {8, 16, 0}},
  {5, 1, 3, 58'395'136, 131'072, {8, 16, 1'024}},
  {6, 3, 2, 58'526'208, 1'024, {8, 32, 0}},
  {7, 1, 3, 58'527'232, 8'192, {8, 32, 32}},
  {8, 3, 1, 58'535'424, 32, {8, 0, 0}},
  {9, 1, 2, 58'535'456, 256, {8, 32, 0}},
}};

static_assert(Sections.back().offset + Sections.back().bytes == Network::FileBytes);

u16 read_u16(const u8* bytes) noexcept { return u16(bytes[0]) | (u16(bytes[1]) << 8); }

u32 read_u32(const u8* bytes) noexcept {
    return u32(bytes[0]) | (u32(bytes[1]) << 8) | (u32(bytes[2]) << 16) | (u32(bytes[3]) << 24);
}

u64 read_u64(const u8* bytes) noexcept {
    u64 value = 0;
    for (std::size_t index = 0; index < 8; ++index)
        value |= u64(bytes[index]) << (8 * index);
    return value;
}

bool zero(const u8* bytes, std::size_t count) noexcept {
    for (std::size_t index = 0; index < count; ++index)
        if (bytes[index] != 0)
            return false;
    return true;
}

std::string hex(const u8* bytes, std::size_t count) {
    constexpr char Digits[] = "0123456789abcdef";
    std::string    output;
    output.reserve(count * 2);
    for (std::size_t index = 0; index < count; ++index)
    {
        output.push_back(Digits[bytes[index] >> 4]);
        output.push_back(Digits[bytes[index] & 0x0F]);
    }
    return output;
}

bool canonical_hex(std::string_view text, std::size_t count) noexcept {
    if (text.size() != count)
        return false;
    for (const char value : text)
        if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f')))
            return false;
    return text.find_first_not_of('0') != std::string_view::npos;
}

constexpr std::array<u32, 64> Sha256Constants = {
  0x428A2F98U, 0x71374491U, 0xB5C0FBCFU, 0xE9B5DBA5U, 0x3956C25BU, 0x59F111F1U, 0x923F82A4U,
  0xAB1C5ED5U, 0xD807AA98U, 0x12835B01U, 0x243185BEU, 0x550C7DC3U, 0x72BE5D74U, 0x80DEB1FEU,
  0x9BDC06A7U, 0xC19BF174U, 0xE49B69C1U, 0xEFBE4786U, 0x0FC19DC6U, 0x240CA1CCU, 0x2DE92C6FU,
  0x4A7484AAU, 0x5CB0A9DCU, 0x76F988DAU, 0x983E5152U, 0xA831C66DU, 0xB00327C8U, 0xBF597FC7U,
  0xC6E00BF3U, 0xD5A79147U, 0x06CA6351U, 0x14292967U, 0x27B70A85U, 0x2E1B2138U, 0x4D2C6DFCU,
  0x53380D13U, 0x650A7354U, 0x766A0ABBU, 0x81C2C92EU, 0x92722C85U, 0xA2BFE8A1U, 0xA81A664BU,
  0xC24B8B70U, 0xC76C51A3U, 0xD192E819U, 0xD6990624U, 0xF40E3585U, 0x106AA070U, 0x19A4C116U,
  0x1E376C08U, 0x2748774CU, 0x34B0BCB5U, 0x391C0CB3U, 0x4ED8AA4AU, 0x5B9CCA4FU, 0x682E6FF3U,
  0x748F82EEU, 0x78A5636FU, 0x84C87814U, 0x8CC70208U, 0x90BEFFFAU, 0xA4506CEBU, 0xBEF9A3F7U,
  0xC67178F2U};

constexpr u32 rotate_right(u32 value, int bits) noexcept {
    return (value >> bits) | (value << (32 - bits));
}

void sha256_block(std::array<u32, 8>& state, const u8* block) noexcept {
    std::array<u32, 64> words{};
    for (std::size_t index = 0; index < 16; ++index)
        words[index] = (u32(block[4 * index]) << 24) | (u32(block[4 * index + 1]) << 16)
                     | (u32(block[4 * index + 2]) << 8) | u32(block[4 * index + 3]);
    for (std::size_t index = 16; index < words.size(); ++index)
    {
        const u32 s0 = rotate_right(words[index - 15], 7) ^ rotate_right(words[index - 15], 18)
                     ^ (words[index - 15] >> 3);
        const u32 s1 = rotate_right(words[index - 2], 17) ^ rotate_right(words[index - 2], 19)
                     ^ (words[index - 2] >> 10);
        words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }

    u32 a = state[0];
    u32 b = state[1];
    u32 c = state[2];
    u32 d = state[3];
    u32 e = state[4];
    u32 f = state[5];
    u32 g = state[6];
    u32 h = state[7];
    for (std::size_t index = 0; index < words.size(); ++index)
    {
        const u32 sum1       = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
        const u32 choose     = (e & f) ^ ((~e) & g);
        const u32 temporary1 = h + sum1 + choose + Sha256Constants[index] + words[index];
        const u32 sum0       = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
        const u32 majority   = (a & b) ^ (a & c) ^ (b & c);
        const u32 temporary2 = sum0 + majority;
        h                    = g;
        g                    = f;
        f                    = e;
        e                    = d + temporary1;
        d                    = c;
        c                    = b;
        b                    = a;
        a                    = temporary1 + temporary2;
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

std::array<u8, 32> sha256(const u8* data, std::size_t size) noexcept {
    std::array<u32, 8> state  = {0x6A09E667U, 0xBB67AE85U, 0x3C6EF372U, 0xA54FF53AU,
                                 0x510E527FU, 0x9B05688CU, 0x1F83D9ABU, 0x5BE0CD19U};
    std::size_t        offset = 0;
    while (size - offset >= 64)
    {
        sha256_block(state, data + offset);
        offset += 64;
    }
    std::array<u8, 128> tail{};
    const std::size_t   remainder = size - offset;
    if (remainder != 0)
        std::memcpy(tail.data(), data + offset, remainder);
    tail[remainder]              = 0x80;
    const std::size_t finalBytes = remainder + 1 + 8 <= 64 ? 64 : 128;
    const u64         bitCount   = u64(size) * 8;
    for (int index = 0; index < 8; ++index)
        tail[finalBytes - 1 - std::size_t(index)] = static_cast<u8>(bitCount >> (8 * index));
    sha256_block(state, tail.data());
    if (finalBytes == 128)
        sha256_block(state, tail.data() + 64);

    std::array<u8, 32> digest{};
    for (std::size_t index = 0; index < state.size(); ++index)
        for (std::size_t byte = 0; byte < 4; ++byte)
            digest[4 * index + byte] = static_cast<u8>(state[index] >> (24 - 8 * byte));
    return digest;
}

std::string digest_text(const std::array<u8, 32>& digest) {
    return hex(digest.data(), digest.size());
}

u32 crc32c(const u8* data, std::size_t size) noexcept {
    u32 value = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < size; ++index)
    {
        value ^= data[index];
        for (int bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1U) != 0 ? 0x82F63B78U : 0U);
    }
    return value ^ 0xFFFFFFFFU;
}

Network::LoadResult failure(Network::LoadStatus status, std::string message) {
    return {status, std::move(message)};
}

std::int16_t signed16(u16 bits) noexcept {
    std::int16_t value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

std::int32_t signed32(u32 bits) noexcept {
    std::int32_t value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

u16 bits16(std::int16_t value) noexcept {
    u16 bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

u32 bits32(std::int32_t value) noexcept {
    u32 bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

void decode_i16(const u8* bytes, std::int16_t* output, std::size_t count) noexcept {
    for (std::size_t index = 0; index < count; ++index)
    {
        const u16 bits = read_u16(bytes + 2 * index);
        std::memcpy(output + index, &bits, sizeof(bits));
    }
}

void decode_i32(const u8* bytes, std::int32_t* output, std::size_t count) noexcept {
    for (std::size_t index = 0; index < count; ++index)
    {
        const u32 bits = read_u32(bytes + 4 * index);
        std::memcpy(output + index, &bits, sizeof(bits));
    }
}

void decode_i8(const u8* bytes, std::int8_t* output, std::size_t count) noexcept {
    std::memcpy(output, bytes, count);
}

u8 activate(std::int32_t input) noexcept {
    if (input <= 0)
        return 0;
    if (input >= 127 * 64)
        return 127;
    return static_cast<u8>(input / 64);
}

void append_u8(std::vector<u8>& output, u8 value) { output.push_back(value); }

void append_u16(std::vector<u8>& output, u16 value) {
    output.push_back(static_cast<u8>(value));
    output.push_back(static_cast<u8>(value >> 8));
}

void append_u32(std::vector<u8>& output, u32 value) {
    for (int byte = 0; byte < 4; ++byte)
        output.push_back(static_cast<u8>(value >> (8 * byte)));
}

}  // namespace

struct LegacyControlNetworkV2::Parameters {
    std::array<std::int16_t, TransformerDimensions>                     transformerBias{};
    std::vector<std::int16_t>                                           transformerWeights;
    std::vector<std::int32_t>                                           psqtWeights;
    std::array<std::int32_t, LayerStacks * Dense0Outputs>               dense0Bias{};
    std::vector<std::int8_t>                                            dense0Weights;
    std::array<std::int32_t, LayerStacks * Dense1Outputs>               dense1Bias{};
    std::array<std::int8_t, LayerStacks * Dense1Outputs * Dense1Inputs> dense1Weights{};
    std::array<std::int32_t, LayerStacks>                               outputBias{};
    std::array<std::int8_t, LayerStacks * OutputInputs>                 outputWeights{};

    Parameters() :
        transformerWeights(FeatureDimensions * TransformerDimensions),
        psqtWeights(FeatureDimensions * PsqtBuckets),
        dense0Weights(LayerStacks * Dense0Outputs * Dense0Inputs) {}
};

LegacyControlNetworkV2::LegacyControlNetworkV2()  = default;
LegacyControlNetworkV2::~LegacyControlNetworkV2() = default;

void LegacyControlNetworkV2::reset() noexcept {
    parameters_.reset();
    fileSha256_.clear();
    converterSha256_.clear();
    sourceCommit_.clear();
    sourceTree_.clear();
}

LegacyControlNetworkV2::LoadResult
LegacyControlNetworkV2::load_file(const std::filesystem::path& path,
                                  const Requirements&          requirements) {
    reset();
    std::error_code error;
    const auto      status = std::filesystem::status(path, error);
    if (error)
        return failure(LoadStatus::FileReadFailure, "control container path status failed");
    if (!std::filesystem::exists(status))
        return failure(LoadStatus::MissingFile, "control container does not exist");
    if (!std::filesystem::is_regular_file(status))
        return failure(LoadStatus::FileReadFailure, "control container path is not a regular file");
    const std::uintmax_t fileSize = std::filesystem::file_size(path, error);
    if (error)
        return failure(LoadStatus::FileReadFailure, "control container size failed");
    if (fileSize < FileBytes)
        return failure(LoadStatus::TruncatedFile, "control container is truncated");
    if (fileSize > FileBytes)
        return failure(LoadStatus::OversizedFile, "control container is oversized");

    std::ifstream stream(path, std::ios::binary);
    if (!stream)
        return failure(LoadStatus::FileReadFailure, "control container open failed");
    std::vector<u8> bytes(FileBytes);
    stream.read(reinterpret_cast<char*>(bytes.data()), std::streamsize(bytes.size()));
    if (stream.gcount() != std::streamsize(bytes.size()) || !stream)
        return failure(LoadStatus::FileReadFailure, "control container read was incomplete");
    if (stream.peek() != std::char_traits<char>::eof())
        return failure(LoadStatus::OversizedFile, "control container grew while reading");
    return load_bytes(bytes.data(), bytes.size(), requirements);
}

LegacyControlNetworkV2::LoadResult LegacyControlNetworkV2::load_bytes(
  const unsigned char* data, std::size_t size, const Requirements& requirements) {
    reset();
    if (size < FileBytes)
        return failure(LoadStatus::TruncatedFile, "control container is truncated");
    if (size > FileBytes)
        return failure(LoadStatus::OversizedFile, "control container is oversized");
    if (data == nullptr)
        return failure(LoadStatus::NullInput, "control container byte pointer is null");
    if (!canonical_hex(requirements.converterSha256, 64)
        || !canonical_hex(requirements.sourceCommit, 40)
        || !canonical_hex(requirements.sourceTree, 40))
        return failure(LoadStatus::ProvenanceMismatch,
                       "control load requirements are not canonical full identities");

    if (read_u32(data + HeaderCrcOffset) != crc32c(data, HeaderCrcOffset))
        return failure(LoadStatus::HeaderCrcMismatch, "control header CRC32C mismatch");
    if (std::memcmp(data, Magic.data(), Magic.size()) != 0
        || !zero(data + Magic.size(), 16 - Magic.size()))
        return failure(LoadStatus::MagicMismatch, "control magic mismatch");

    if (read_u32(data + 16) != EndianTag || read_u16(data + 20) != VersionMajor
        || read_u16(data + 22) != VersionMinor || read_u32(data + 24) != HeaderBytes
        || read_u64(data + 28) != PayloadBytes || read_u64(data + 36) != FileBytes
        || data[44] != CommittedFlag || data[45] != PurposeLegacy || data[46] != OriginLegacy
        || data[47] != 0)
        return failure(LoadStatus::FixedFieldMismatch, "control framing field mismatch");

    constexpr std::array<u32, 13> Architecture = {FeatureDimensions,
                                                  MaximumActive,
                                                  TransformerDimensions,
                                                  COLOR_NB,
                                                  PsqtBuckets,
                                                  LayerStacks,
                                                  Dense0Inputs,
                                                  Dense0Outputs,
                                                  Dense0PaddedOutputs,
                                                  Dense1Inputs,
                                                  Dense1Outputs,
                                                  OutputInputs,
                                                  1};
    for (std::size_t index = 0; index < Architecture.size(); ++index)
        if (read_u32(data + 48 + 4 * index) != Architecture[index])
            return failure(LoadStatus::FixedFieldMismatch, "control architecture field mismatch");
    if (read_u16(data + 100) != Sections.size() || read_u16(data + 102) != DirectoryEntryBytes
        || read_u32(data + 104) != DirectoryOffset || read_u32(data + 108) != HeaderBytes
        || data[112] != ArithmeticId || data[113] != ArithmeticId || data[114] != ArithmeticId
        || data[115] != ArithmeticId)
        return failure(LoadStatus::FixedFieldMismatch,
                       "control directory or arithmetic field mismatch");
    if (!zero(data + 116, 28) || !zero(data + 376, 8) || !zero(data + 960, 60))
        return failure(LoadStatus::ReservedBytesNonzero, "control reserved bytes are nonzero");

    if (hex(data + 176, 32) != RuleProfileSha256
        || hex(data + 208, 32) != LegacyFeatureContractSha256
        || hex(data + 240, 32) != ContainerContractSha256
        || hex(data + 272, 32) != OriginArtifactSha256)
        return failure(LoadStatus::IdentityMismatch, "control fixed SHA-256 identity mismatch");
    if (hex(data + 304, 32) != requirements.converterSha256
        || hex(data + 336, 20) != requirements.sourceCommit
        || hex(data + 356, 20) != requirements.sourceTree)
        return failure(LoadStatus::ProvenanceMismatch, "control producer provenance mismatch");

    for (std::size_t index = 0; index < Sections.size(); ++index)
    {
        const SectionSpec& spec  = Sections[index];
        const u8*          entry = data + DirectoryOffset + index * DirectoryEntryBytes;
        if (read_u16(entry) != spec.id || entry[2] != spec.dtype || entry[3] != spec.rank
            || read_u64(entry + 4) != spec.offset || read_u64(entry + 12) != spec.bytes
            || read_u32(entry + 20) != spec.shape[0] || read_u32(entry + 24) != spec.shape[1]
            || read_u32(entry + 28) != spec.shape[2])
            return failure(LoadStatus::DirectoryMismatch, "control section directory mismatch");
        if (zero(entry + 32, 32))
            return failure(LoadStatus::DirectoryMismatch, "control section digest is zero");
    }

    const std::array<u8, 32> payloadDigest = sha256(data + HeaderBytes, PayloadBytes);
    if (!std::equal(payloadDigest.begin(), payloadDigest.end(), data + 144))
        return failure(LoadStatus::PayloadDigestMismatch, "control payload SHA-256 mismatch");
    for (std::size_t index = 0; index < Sections.size(); ++index)
    {
        const SectionSpec& spec     = Sections[index];
        const auto         digest   = sha256(data + spec.offset, std::size_t(spec.bytes));
        const u8*          expected = data + DirectoryOffset + index * DirectoryEntryBytes + 32;
        if (!std::equal(digest.begin(), digest.end(), expected))
            return failure(LoadStatus::SectionDigestMismatch, "control section SHA-256 mismatch");
    }

    auto candidate = std::make_unique<Parameters>();
    decode_i16(data + Sections[0].offset, candidate->transformerBias.data(),
               candidate->transformerBias.size());
    decode_i16(data + Sections[1].offset, candidate->transformerWeights.data(),
               candidate->transformerWeights.size());
    decode_i32(data + Sections[2].offset, candidate->psqtWeights.data(),
               candidate->psqtWeights.size());
    decode_i32(data + Sections[3].offset, candidate->dense0Bias.data(),
               candidate->dense0Bias.size());
    decode_i8(data + Sections[4].offset, candidate->dense0Weights.data(),
              candidate->dense0Weights.size());
    decode_i32(data + Sections[5].offset, candidate->dense1Bias.data(),
               candidate->dense1Bias.size());
    decode_i8(data + Sections[6].offset, candidate->dense1Weights.data(),
              candidate->dense1Weights.size());
    decode_i32(data + Sections[7].offset, candidate->outputBias.data(),
               candidate->outputBias.size());
    decode_i8(data + Sections[8].offset, candidate->outputWeights.data(),
              candidate->outputWeights.size());

    parameters_      = std::move(candidate);
    fileSha256_      = digest_text(sha256(data, size));
    converterSha256_ = requirements.converterSha256;
    sourceCommit_    = requirements.sourceCommit;
    sourceTree_      = requirements.sourceTree;
    return {LoadStatus::Success, "authenticated legacy-control container loaded"};
}

LegacyControlNetworkV2::EvalResult
LegacyControlNetworkV2::evaluate(const LegacyCrazyhouseFeaturesV1::Result& features,
                                 Color                                     sideToMove) const {
    if (!parameters_)
        return {EvalStatus::NetworkNotLoaded, std::nullopt, std::nullopt,
                "legacy-control network is not loaded"};
    if (!features.ok())
        return {EvalStatus::FeatureRejected, features.status, std::nullopt,
                "legacy-control feature input is not certified"};
    if (sideToMove != WHITE && sideToMove != BLACK)
        return {EvalStatus::ContractViolation, features.status, std::nullopt,
                "legacy-control side to move is invalid"};
    if (features.boardPieceCount < 2
        || features.boardPieceCount > LegacyCrazyhouseFeaturesV1::LegacyMaxPieces)
        return {EvalStatus::ContractViolation, features.status, std::nullopt,
                "legacy-control board-piece count is invalid"};
    const std::size_t expectedBucket =
      (features.boardPieceCount - 1) * LayerStacks / LegacyCrazyhouseFeaturesV1::LegacyMaxPieces;
    if (expectedBucket >= LayerStacks || features.layerBucket != expectedBucket)
        return {EvalStatus::ContractViolation, features.status, std::nullopt,
                "legacy-control material bucket is invalid"};
    for (Color perspective : {WHITE, BLACK})
    {
        const auto& active = features.active[perspective];
        if (active.size() > MaximumActive)
            return {EvalStatus::ContractViolation, features.status, std::nullopt,
                    "legacy-control active feature count is invalid"};
        for (std::size_t index = 0; index < active.size(); ++index)
        {
            if (active[index] >= FeatureDimensions)
                return {EvalStatus::ContractViolation, features.status, std::nullopt,
                        "legacy-control feature index is invalid"};
            for (std::size_t prior = 0; prior < index; ++prior)
                if (active[prior] == active[index])
                    return {EvalStatus::ContractViolation, features.status, std::nullopt,
                            "legacy-control feature input has a duplicate"};
        }
    }

    Trace trace;
    for (Color perspective : {WHITE, BLACK})
    {
        for (std::size_t lane = 0; lane < TransformerDimensions; ++lane)
            trace.transformerBits[perspective][lane] = bits16(parameters_->transformerBias[lane]);
        for (const LegacyCrazyhouseFeaturesV1::Index feature : features.active[perspective])
        {
            const std::size_t transformerRow = std::size_t(feature) * TransformerDimensions;
            for (std::size_t lane = 0; lane < TransformerDimensions; ++lane)
                trace.transformerBits[perspective][lane] = static_cast<u16>(
                  u32(trace.transformerBits[perspective][lane])
                  + bits16(parameters_->transformerWeights[transformerRow + lane]));
            const std::size_t psqtRow = std::size_t(feature) * PsqtBuckets;
            for (std::size_t bucket = 0; bucket < PsqtBuckets; ++bucket)
                trace.psqtBits[perspective][bucket] +=
                  bits32(parameters_->psqtWeights[psqtRow + bucket]);
        }
    }

    const std::array<Color, 2> perspectives = {sideToMove, ~sideToMove};
    for (std::size_t half = 0; half < perspectives.size(); ++half)
        for (std::size_t lane = 0; lane < TransformerDimensions; ++lane)
        {
            const std::int16_t value = signed16(trace.transformerBits[perspectives[half]][lane]);
            trace.transformed[half * TransformerDimensions + lane] = value <= 0 ? 0
                                                                   : value >= 127
                                                                     ? 127
                                                                     : static_cast<u8>(value);
        }

    trace.selectedBucket = static_cast<u8>(expectedBucket);
    for (std::size_t bucket = 0; bucket < LayerStacks; ++bucket)
    {
        BucketTrace& bucketTrace = trace.buckets[bucket];
        const u32    difference =
          trace.psqtBits[sideToMove][bucket] - trace.psqtBits[~sideToMove][bucket];
        bucketTrace.psqt = signed32(difference) / 2;

        for (std::size_t output = 0; output < Dense0Outputs; ++output)
        {
            u32 sum = bits32(parameters_->dense0Bias[bucket * Dense0Outputs + output]);
            const std::size_t row = (bucket * Dense0Outputs + output) * Dense0Inputs;
            for (std::size_t input = 0; input < Dense0Inputs; ++input)
            {
                const std::int32_t product = std::int32_t(trace.transformed[input])
                                           * std::int32_t(parameters_->dense0Weights[row + input]);
                sum += static_cast<u32>(product);
            }
            bucketTrace.dense0Affine[output]     = signed32(sum);
            bucketTrace.dense0Activation[output] = activate(bucketTrace.dense0Affine[output]);
        }

        for (std::size_t output = 0; output < Dense1Outputs; ++output)
        {
            u32 sum = bits32(parameters_->dense1Bias[bucket * Dense1Outputs + output]);
            const std::size_t row = (bucket * Dense1Outputs + output) * Dense1Inputs;
            for (std::size_t input = 0; input < Dense1Inputs; ++input)
            {
                const u8 value = input < Dense0Outputs ? bucketTrace.dense0Activation[input] : 0;
                const std::int32_t product =
                  std::int32_t(value) * std::int32_t(parameters_->dense1Weights[row + input]);
                sum += static_cast<u32>(product);
            }
            bucketTrace.dense1Affine[output]     = signed32(sum);
            bucketTrace.dense1Activation[output] = activate(bucketTrace.dense1Affine[output]);
        }

        u32               output = bits32(parameters_->outputBias[bucket]);
        const std::size_t row    = bucket * OutputInputs;
        for (std::size_t input = 0; input < OutputInputs; ++input)
        {
            const std::int32_t product = std::int32_t(bucketTrace.dense1Activation[input])
                                       * std::int32_t(parameters_->outputWeights[row + input]);
            output += static_cast<u32>(product);
        }
        bucketTrace.outputAffine = signed32(output);
    }
    return {EvalStatus::Success, features.status, std::move(trace),
            "authenticated legacy-control scalar evaluation completed"};
}

bool             LegacyControlNetworkV2::loaded() const noexcept { return bool(parameters_); }
std::string_view LegacyControlNetworkV2::file_sha256() const noexcept { return fileSha256_; }
std::string_view LegacyControlNetworkV2::converter_sha256() const noexcept {
    return converterSha256_;
}
std::string_view LegacyControlNetworkV2::source_commit() const noexcept { return sourceCommit_; }
std::string_view LegacyControlNetworkV2::source_tree() const noexcept { return sourceTree_; }

std::string LegacyControlNetworkV2::trace_sha256(const LegacyCrazyhouseFeaturesV1::Result& features,
                                                 Color        sideToMove,
                                                 const Trace& trace) {
    std::vector<u8> bytes;
    bytes.reserve(8'192);
    constexpr std::array<u8, 16> TraceMagic = {'C', 'H', 'L', 'C', '_', 'T', 'R', 'A',
                                               'C', 'E', '_', 'V', '1', 0,   0,   0};
    bytes.insert(bytes.end(), TraceMagic.begin(), TraceMagic.end());
    append_u32(bytes, static_cast<u32>(features.boardPieceCount));
    append_u8(bytes, sideToMove == WHITE ? 0 : 1);
    append_u8(bytes, trace.selectedBucket);
    append_u16(bytes, 0);
    for (Color perspective : {WHITE, BLACK})
    {
        append_u32(bytes, static_cast<u32>(features.active[perspective].size()));
        for (const LegacyCrazyhouseFeaturesV1::Index feature : features.active[perspective])
            append_u32(bytes, feature);
        for (const u16 value : trace.transformerBits[perspective])
            append_u16(bytes, value);
        for (const u32 value : trace.psqtBits[perspective])
            append_u32(bytes, value);
    }
    bytes.insert(bytes.end(), trace.transformed.begin(), trace.transformed.end());
    for (const BucketTrace& bucket : trace.buckets)
    {
        for (const std::int32_t value : bucket.dense0Affine)
            append_u32(bytes, bits32(value));
        bytes.insert(bytes.end(), bucket.dense0Activation.begin(), bucket.dense0Activation.end());
        for (const std::int32_t value : bucket.dense1Affine)
            append_u32(bytes, bits32(value));
        bytes.insert(bytes.end(), bucket.dense1Activation.begin(), bucket.dense1Activation.end());
        append_u32(bytes, bits32(bucket.outputAffine));
        append_u32(bytes, bits32(bucket.psqt));
    }
    return digest_text(sha256(bytes.data(), bytes.size()));
}

std::string_view LegacyControlNetworkV2::status_name(LoadStatus status) noexcept {
    switch (status)
    {
    case LoadStatus::Success :
        return "Success";
    case LoadStatus::MissingFile :
        return "MissingFile";
    case LoadStatus::FileReadFailure :
        return "FileReadFailure";
    case LoadStatus::TruncatedFile :
        return "TruncatedFile";
    case LoadStatus::OversizedFile :
        return "OversizedFile";
    case LoadStatus::NullInput :
        return "NullInput";
    case LoadStatus::HeaderCrcMismatch :
        return "HeaderCrcMismatch";
    case LoadStatus::MagicMismatch :
        return "MagicMismatch";
    case LoadStatus::FixedFieldMismatch :
        return "FixedFieldMismatch";
    case LoadStatus::ReservedBytesNonzero :
        return "ReservedBytesNonzero";
    case LoadStatus::IdentityMismatch :
        return "IdentityMismatch";
    case LoadStatus::ProvenanceMismatch :
        return "ProvenanceMismatch";
    case LoadStatus::DirectoryMismatch :
        return "DirectoryMismatch";
    case LoadStatus::PayloadDigestMismatch :
        return "PayloadDigestMismatch";
    case LoadStatus::SectionDigestMismatch :
        return "SectionDigestMismatch";
    case LoadStatus::TensorLayoutMismatch :
        return "TensorLayoutMismatch";
    }
    return "Unknown";
}

std::string_view LegacyControlNetworkV2::eval_status_name(EvalStatus status) noexcept {
    switch (status)
    {
    case EvalStatus::Success :
        return "Success";
    case EvalStatus::NetworkNotLoaded :
        return "NetworkNotLoaded";
    case EvalStatus::FeatureRejected :
        return "FeatureRejected";
    case EvalStatus::ContractViolation :
        return "ContractViolation";
    }
    return "Unknown";
}

}  // namespace Stockfish::Eval::NNUE
