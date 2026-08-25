/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_probe.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <limits>
#include <type_traits>

#if defined(USE_SSE2)
    #include <emmintrin.h>
#endif

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {
namespace {

constexpr std::uint32_t ByteOrderMarker   = 0x01020304U;
constexpr std::uint16_t HeaderSize        = 256;
constexpr std::uint16_t VersionMajor      = 1;
constexpr std::uint16_t VersionMinor      = 0;
constexpr std::uint16_t CommittedFlag     = 1;
constexpr std::uint32_t FileSize          = 30992;
constexpr std::uint32_t FeatureDimensions = 902;
constexpr std::uint32_t MaximumActive     = 138;
constexpr std::uint32_t OutputLanes       = 17;
constexpr std::uint16_t SparseBinaryInput = 1;
constexpr std::uint16_t SignedInt16       = 1;
constexpr std::uint16_t SignedInt32       = 2;
constexpr std::uint32_t WeightsOffset     = 256;
constexpr std::uint32_t WeightsBytes      = 30668;
constexpr std::uint32_t BiasesOffset      = 30924;
constexpr std::uint32_t BiasesBytes       = 68;
constexpr std::uint32_t PayloadBytes      = 30736;

constexpr std::array<Byte, 16> Magic = {'C', 'H', 'N', 'N', 'U', 'E', 'V', '2',
                                        'R', 'E', 'F', '1', 0,   0,   0,   0};

constexpr Byte hex_nibble(char value) noexcept {
    return value >= '0' && value <= '9' ? Byte(value - '0')
         : value >= 'a' && value <= 'f' ? Byte(value - 'a' + 10)
                                        : Byte{0};
}

template<std::size_t Size>
constexpr Digest digest_from_hex(const char (&text)[Size]) noexcept {
    static_assert(Size == 65);
    Digest output{};
    for (std::size_t index = 0; index < output.size(); ++index)
        output[index] = Byte((hex_nibble(text[index * 2]) << 4U) | hex_nibble(text[index * 2 + 1]));
    return output;
}

constexpr Digest RuleProfileIdentity =
  digest_from_hex("d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68");
constexpr Digest PhysicalSchemaIdentity =
  digest_from_hex("c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55");
constexpr Digest FeatureContractIdentity =
  digest_from_hex("1e2b9afc2be77d2df66e3cdfe22bffafa7f2d926b224d2b01ab244f354c889c6");
constexpr Digest ArchitectureIdentity =
  digest_from_hex("e71d819a1d568979ec4fe99b6a004359768c31f618c91da7a309386f3bf732bb");

template<typename UInt>
UInt get_le(const Byte* bytes) noexcept {
    static_assert(std::is_unsigned_v<UInt>);
    UInt output = 0;
    for (std::size_t index = 0; index < sizeof(UInt); ++index)
        output |= UInt(bytes[index]) << (8U * index);
    return output;
}

std::int16_t get_i16_le(const Byte* bytes) noexcept {
    const std::uint16_t raw = get_le<std::uint16_t>(bytes);
    if (raw <= std::uint16_t(std::numeric_limits<std::int16_t>::max()))
        return static_cast<std::int16_t>(raw);
    return static_cast<std::int16_t>(std::int32_t(raw) - (std::int32_t{1} << 16));
}

std::int32_t get_i32_le(const Byte* bytes) noexcept {
    const std::uint32_t raw = get_le<std::uint32_t>(bytes);
    if (raw <= std::uint32_t(std::numeric_limits<std::int32_t>::max()))
        return static_cast<std::int32_t>(raw);
    return static_cast<std::int32_t>(std::int64_t(raw) - (std::int64_t{1} << 32));
}

bool range_all_zero(const Byte* first, const Byte* last) noexcept {
    return std::all_of(first, last, [](Byte value) { return value == 0; });
}

bool matches_digest(const Byte* bytes, const Digest& expected) noexcept {
    return std::equal(expected.begin(), expected.end(), bytes);
}

constexpr std::uint32_t rotate_right(std::uint32_t value, unsigned shift) noexcept {
    return (value >> shift) | (value << (32U - shift));
}

class Sha256 {
   public:
    void update(const Byte* data, std::size_t size) noexcept {
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

    Digest final() const noexcept {
        Sha256              copy      = *this;
        const std::uint64_t bitLength = copy.totalBytes * 8U;
        copy.block[copy.buffered++]   = 0x80;
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
        for (std::size_t word = 0; word < copy.state.size(); ++word)
            for (unsigned index = 0; index < 4; ++index)
                output[word * 4 + index] = Byte(copy.state[word] >> (24U - 8U * index));
        return output;
    }

   private:
    void transform(const Byte* data) noexcept {
        static constexpr std::array<std::uint32_t, 64> Constants = {
          0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U, 0x923f82a4U,
          0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU,
          0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU,
          0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
          0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
          0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
          0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U,
          0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
          0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U, 0x90befffaU, 0xa4506cebU, 0xbef9a3f7U,
          0xc67178f2U};

        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16; ++index)
            words[index] =
              (std::uint32_t(data[index * 4]) << 24U) | (std::uint32_t(data[index * 4 + 1]) << 16U)
              | (std::uint32_t(data[index * 4 + 2]) << 8U) | std::uint32_t(data[index * 4 + 3]);
        for (std::size_t index = 16; index < words.size(); ++index)
        {
            const std::uint32_t s0 = rotate_right(words[index - 15], 7)
                                   ^ rotate_right(words[index - 15], 18)
                                   ^ (words[index - 15] >> 3U);
            const std::uint32_t s1 = rotate_right(words[index - 2], 17)
                                   ^ rotate_right(words[index - 2], 19) ^ (words[index - 2] >> 10U);
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
            const std::uint32_t sum1 =
              rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
            const std::uint32_t choose = (e & f) ^ (~e & g);
            const std::uint32_t temp1  = h + sum1 + choose + Constants[index] + words[index];
            const std::uint32_t sum0 =
              rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t temp2    = sum0 + majority;
            h                            = g;
            g                            = f;
            f                            = e;
            e                            = d + temp1;
            d                            = c;
            c                            = b;
            b                            = a;
            a                            = temp1 + temp2;
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

    std::array<std::uint32_t, 8> state = {0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
                                          0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U};
    std::array<Byte, 64>         block{};
    std::size_t                  buffered   = 0;
    std::uint64_t                totalBytes = 0;
};

std::uint32_t crc32c(const Byte* data, std::size_t size) noexcept {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < size; ++index)
    {
        crc ^= data[index];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1U) ^ ((crc & 1U) ? 0x82F63B78U : 0U);
    }
    return crc ^ 0xFFFFFFFFU;
}

ScalarProbeLoadResult failure(ScalarProbeLoadError error) noexcept {
    ScalarProbeLoadResult result;
    result.error = error;
    return result;
}

ScalarProbeEvaluateError validate_feature_side(const ScalarFeatureInventoryV1::Result& features,
                                               unsigned side) noexcept {
    if (!features.ok())
        return ScalarProbeEvaluateError::FEATURE_STATUS;
    if (side >= COLOR_NB)
        return ScalarProbeEvaluateError::PERSPECTIVE;
    if (features.size[side] > ScalarFeatureInventoryV1::MaximumActive)
        return ScalarProbeEvaluateError::ACTIVE_OVERFLOW;
    for (std::size_t index = 0; index < features.size[side]; ++index)
    {
        const auto row = features.active[side][index];
        if (row >= ScalarFeatureInventoryV1::Dimensions)
            return ScalarProbeEvaluateError::FEATURE_INDEX;
        for (std::size_t prior = 0; prior < index; ++prior)
            if (features.active[side][prior] == row)
                return ScalarProbeEvaluateError::DUPLICATE_FEATURE;
    }
    return ScalarProbeEvaluateError::NONE;
}

ScalarProbeAccumulatorError accumulator_error(ScalarProbeEvaluateError error) noexcept {
    switch (error)
    {
    case ScalarProbeEvaluateError::NONE :
        return ScalarProbeAccumulatorError::NONE;
    case ScalarProbeEvaluateError::NETWORK_NOT_READY :
        return ScalarProbeAccumulatorError::NETWORK_NOT_READY;
    case ScalarProbeEvaluateError::FEATURE_STATUS :
        return ScalarProbeAccumulatorError::FEATURE_STATUS;
    case ScalarProbeEvaluateError::PERSPECTIVE :
        return ScalarProbeAccumulatorError::PERSPECTIVE;
    case ScalarProbeEvaluateError::ACTIVE_OVERFLOW :
        return ScalarProbeAccumulatorError::ACTIVE_OVERFLOW;
    case ScalarProbeEvaluateError::FEATURE_INDEX :
        return ScalarProbeAccumulatorError::FEATURE_INDEX;
    case ScalarProbeEvaluateError::DUPLICATE_FEATURE :
        return ScalarProbeAccumulatorError::DUPLICATE_FEATURE;
    case ScalarProbeEvaluateError::ACCUMULATOR_OVERFLOW :
        return ScalarProbeAccumulatorError::ACCUMULATOR_OVERFLOW;
    case ScalarProbeEvaluateError::SIMD_UNAVAILABLE :
        return ScalarProbeAccumulatorError::SIMD_UNAVAILABLE;
    }
    return ScalarProbeAccumulatorError::FEATURE_STATUS;
}

using FeatureMembership =
  std::array<std::array<bool, ScalarFeatureInventoryV1::Dimensions>, COLOR_NB>;

FeatureMembership make_membership(const ScalarFeatureInventoryV1::Result& features) noexcept {
    FeatureMembership membership{};
    for (unsigned side = 0; side < COLOR_NB; ++side)
        for (std::size_t index = 0; index < features.size[side]; ++index)
            membership[side][features.active[side][index]] = true;
    return membership;
}

}  // namespace

ScalarProbeLoadResult load_scalar_probe_v1(const Byte* bytes, std::size_t size) noexcept {
    if (bytes == nullptr || size != ScalarProbeFileBytes)
        return failure(ScalarProbeLoadError::WRONG_SIZE);
    if (!std::equal(Magic.begin(), Magic.end(), bytes))
        return failure(ScalarProbeLoadError::MAGIC);
    if (get_le<std::uint32_t>(bytes + 16) != ByteOrderMarker)
        return failure(ScalarProbeLoadError::BYTE_ORDER);
    if (get_le<std::uint16_t>(bytes + 20) != HeaderSize)
        return failure(ScalarProbeLoadError::HEADER_SIZE);
    if (get_le<std::uint16_t>(bytes + 22) != VersionMajor
        || get_le<std::uint16_t>(bytes + 24) != VersionMinor)
        return failure(ScalarProbeLoadError::VERSION);
    if (get_le<std::uint16_t>(bytes + 26) != CommittedFlag)
        return failure(ScalarProbeLoadError::FLAGS);
    if (get_le<std::uint32_t>(bytes + 28) != FileSize)
        return failure(ScalarProbeLoadError::FILE_SIZE);
    if (get_le<std::uint32_t>(bytes + 32) != FeatureDimensions)
        return failure(ScalarProbeLoadError::FEATURE_DIMENSIONS);
    if (get_le<std::uint32_t>(bytes + 36) != MaximumActive)
        return failure(ScalarProbeLoadError::MAXIMUM_ACTIVE);
    if (get_le<std::uint32_t>(bytes + 40) != OutputLanes)
        return failure(ScalarProbeLoadError::OUTPUT_LANES);
    if (get_le<std::uint16_t>(bytes + 44) != SparseBinaryInput)
        return failure(ScalarProbeLoadError::INPUT_SEMANTICS);
    if (get_le<std::uint16_t>(bytes + 46) != SignedInt16)
        return failure(ScalarProbeLoadError::WEIGHT_TYPE);
    if (get_le<std::uint16_t>(bytes + 48) != SignedInt32)
        return failure(ScalarProbeLoadError::BIAS_TYPE);
    if (get_le<std::uint16_t>(bytes + 50) != SignedInt32)
        return failure(ScalarProbeLoadError::ACCUMULATOR_TYPE);
    if (get_le<std::uint32_t>(bytes + 52) != WeightsOffset)
        return failure(ScalarProbeLoadError::WEIGHTS_OFFSET);
    if (get_le<std::uint32_t>(bytes + 56) != WeightsBytes)
        return failure(ScalarProbeLoadError::WEIGHTS_BYTES);
    if (get_le<std::uint32_t>(bytes + 60) != BiasesOffset)
        return failure(ScalarProbeLoadError::BIASES_OFFSET);
    if (get_le<std::uint32_t>(bytes + 64) != BiasesBytes)
        return failure(ScalarProbeLoadError::BIASES_BYTES);
    if (get_le<std::uint32_t>(bytes + 68) != PayloadBytes)
        return failure(ScalarProbeLoadError::PAYLOAD_BYTES);
    if (!range_all_zero(bytes + 72, bytes + 80) || !range_all_zero(bytes + 240, bytes + 252))
        return failure(ScalarProbeLoadError::RESERVED_BYTES);
    if (!matches_digest(bytes + 80, RuleProfileIdentity))
        return failure(ScalarProbeLoadError::RULE_PROFILE_IDENTITY);
    if (!matches_digest(bytes + 112, PhysicalSchemaIdentity))
        return failure(ScalarProbeLoadError::PHYSICAL_SCHEMA_IDENTITY);
    if (!matches_digest(bytes + 144, FeatureContractIdentity))
        return failure(ScalarProbeLoadError::FEATURE_CONTRACT_IDENTITY);
    if (!matches_digest(bytes + 176, ArchitectureIdentity))
        return failure(ScalarProbeLoadError::ARCHITECTURE_IDENTITY);
    if (get_le<std::uint32_t>(bytes + 252) != crc32c(bytes, 252))
        return failure(ScalarProbeLoadError::HEADER_CRC32C);

    Sha256 payloadHash;
    payloadHash.update(bytes + ScalarProbeHeaderBytes, PayloadBytes);
    if (!matches_digest(bytes + 208, payloadHash.final()))
        return failure(ScalarProbeLoadError::PAYLOAD_SHA256);

    ScalarProbeNetworkV1 candidate;
    for (std::size_t index = 0; index < candidate.weights_.size(); ++index)
        candidate.weights_[index] = get_i16_le(bytes + WeightsOffset + index * 2);
    for (std::size_t index = 0; index < candidate.biases_.size(); ++index)
        candidate.biases_[index] = get_i32_le(bytes + BiasesOffset + index * 4);
    candidate.ready_ = true;

    ScalarProbeLoadResult result;
    result.error   = ScalarProbeLoadError::NONE;
    result.network = candidate;
    return result;
}

ScalarProbeEvaluationResult
ScalarProbeNetworkV1::evaluate(const ScalarFeatureInventoryV1::Result& features,
                               Color                                   perspective) const noexcept {
    ScalarProbeEvaluationResult result;
    if (!ready_)
    {
        result.error = ScalarProbeEvaluateError::NETWORK_NOT_READY;
        return result;
    }
    const unsigned side = static_cast<unsigned>(perspective);
    if (const auto error = validate_feature_side(features, side);
        error != ScalarProbeEvaluateError::NONE)
    {
        result.error = error;
        return result;
    }

    std::array<std::int64_t, ScalarProbeOutputLanes> accumulator{};
    for (std::size_t lane = 0; lane < accumulator.size(); ++lane)
        accumulator[lane] = biases_[lane];
    for (std::size_t index = 0; index < features.size[side]; ++index)
    {
        const std::size_t row = features.active[side][index];
        for (std::size_t lane = 0; lane < accumulator.size(); ++lane)
            accumulator[lane] += weights_[row * ScalarProbeOutputLanes + lane];
    }
    for (std::size_t lane = 0; lane < accumulator.size(); ++lane)
    {
        if (accumulator[lane] < std::numeric_limits<std::int32_t>::min()
            || accumulator[lane] > std::numeric_limits<std::int32_t>::max())
        {
            result.error = ScalarProbeEvaluateError::ACCUMULATOR_OVERFLOW;
            return result;
        }
    }
    for (std::size_t lane = 0; lane < accumulator.size(); ++lane)
        result.lanes[lane] = static_cast<std::int32_t>(accumulator[lane]);
    result.error = ScalarProbeEvaluateError::NONE;
    return result;
}

ScalarProbeEvaluationResult
ScalarProbeNetworkV1::evaluate_simd(const ScalarFeatureInventoryV1::Result& features,
                                    Color perspective) const noexcept {
    ScalarProbeEvaluationResult result;
    if (!ready_)
    {
        result.error = ScalarProbeEvaluateError::NETWORK_NOT_READY;
        return result;
    }
    const unsigned side = static_cast<unsigned>(perspective);
    if (const auto error = validate_feature_side(features, side);
        error != ScalarProbeEvaluateError::NONE)
    {
        result.error = error;
        return result;
    }

#if defined(USE_SSE2)
    __m128i accumulator[8];
    for (std::size_t pair = 0; pair < 8; ++pair)
        accumulator[pair] = _mm_set_epi64x(static_cast<long long>(biases_[pair * 2 + 1]),
                                           static_cast<long long>(biases_[pair * 2]));

    const __m128i zero      = _mm_setzero_si128();
    auto          add_eight = [&](const std::int16_t* values, std::size_t accumulatorOffset) {
        const __m128i packed =
          _mm_loadu_si128(reinterpret_cast<const __m128i*>(static_cast<const void*>(values)));
        const __m128i sign16     = _mm_cmpgt_epi16(zero, packed);
        const __m128i low32      = _mm_unpacklo_epi16(packed, sign16);
        const __m128i high32     = _mm_unpackhi_epi16(packed, sign16);
        const __m128i lowSign32  = _mm_cmpgt_epi32(zero, low32);
        const __m128i highSign32 = _mm_cmpgt_epi32(zero, high32);
        accumulator[accumulatorOffset] =
          _mm_add_epi64(accumulator[accumulatorOffset], _mm_unpacklo_epi32(low32, lowSign32));
        accumulator[accumulatorOffset + 1] =
          _mm_add_epi64(accumulator[accumulatorOffset + 1], _mm_unpackhi_epi32(low32, lowSign32));
        accumulator[accumulatorOffset + 2] =
          _mm_add_epi64(accumulator[accumulatorOffset + 2], _mm_unpacklo_epi32(high32, highSign32));
        accumulator[accumulatorOffset + 3] =
          _mm_add_epi64(accumulator[accumulatorOffset + 3], _mm_unpackhi_epi32(high32, highSign32));
    };

    std::int64_t tail = biases_[16];
    for (std::size_t index = 0; index < features.size[side]; ++index)
    {
        const std::size_t row    = features.active[side][index];
        const auto*       values = weights_.data() + row * ScalarProbeOutputLanes;
        add_eight(values, 0);
        add_eight(values + 8, 4);
        tail += values[16];
    }

    std::array<std::int64_t, ScalarProbeOutputLanes> widened{};
    for (std::size_t pair = 0; pair < 8; ++pair)
    {
        alignas(16) std::int64_t values[2];
        _mm_store_si128(reinterpret_cast<__m128i*>(static_cast<void*>(values)), accumulator[pair]);
        widened[pair * 2]     = values[0];
        widened[pair * 2 + 1] = values[1];
    }
    widened[16] = tail;
    for (std::size_t lane = 0; lane < widened.size(); ++lane)
    {
        if (widened[lane] < std::numeric_limits<std::int32_t>::min()
            || widened[lane] > std::numeric_limits<std::int32_t>::max())
        {
            result.error = ScalarProbeEvaluateError::ACCUMULATOR_OVERFLOW;
            return result;
        }
        result.lanes[lane] = static_cast<std::int32_t>(widened[lane]);
    }
    result.error = ScalarProbeEvaluateError::NONE;
#else
    result.error = ScalarProbeEvaluateError::SIMD_UNAVAILABLE;
#endif
    return result;
}

ScalarProbeSimdBackend scalar_probe_simd_backend() noexcept {
#if defined(USE_SSE2)
    return ScalarProbeSimdBackend::SSE2_X16_SCALAR_TAIL1;
#else
    return ScalarProbeSimdBackend::UNAVAILABLE;
#endif
}

ScalarProbeAccumulatorResult
ScalarProbeAccumulatorV1::refresh(const ScalarProbeNetworkV1&             network,
                                  const ScalarFeatureInventoryV1::Result& features) noexcept {
    ScalarProbeAccumulatorResult result;
    if (!network.ready())
    {
        result.error = ScalarProbeAccumulatorError::NETWORK_NOT_READY;
        return result;
    }

    ScalarProbeAccumulatorV1 candidate;
    candidate.network_ = &network;
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        const auto evaluated = network.evaluate(features, Color(side));
        if (!evaluated.ok())
        {
            result.error = accumulator_error(evaluated.error);
            return result;
        }
        candidate.lanes_[side] = evaluated.lanes;
    }
    candidate.membership_ = make_membership(features);
    candidate.sizes_      = features.size;
    candidate.ready_      = true;
    *this                 = candidate;
    result.error          = ScalarProbeAccumulatorError::NONE;
    return result;
}

bool ScalarProbeAccumulatorV1::matches(
  const ScalarFeatureInventoryV1::Result& features) const noexcept {
    if (!ready_ || !features.ok())
        return false;
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        if (validate_feature_side(features, side) != ScalarProbeEvaluateError::NONE
            || sizes_[side] != features.size[side])
            return false;
    }
    return membership_ == make_membership(features);
}

ScalarProbeAccumulatorResult
ScalarProbeAccumulatorV1::update(const ScalarProbeNetworkV1&             network,
                                 const ScalarFeatureInventoryV1::Result& source,
                                 const ScalarFeatureInventoryV1::Result& target) noexcept {
    ScalarProbeAccumulatorResult result;
    if (!network.ready())
    {
        result.error = ScalarProbeAccumulatorError::NETWORK_NOT_READY;
        return result;
    }
    if (!ready_)
    {
        result.error = ScalarProbeAccumulatorError::SOURCE_NOT_READY;
        return result;
    }
    if (network_ != &network)
    {
        result.error = ScalarProbeAccumulatorError::NETWORK_MISMATCH;
        return result;
    }
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        if (const auto error = validate_feature_side(source, side);
            error != ScalarProbeEvaluateError::NONE)
        {
            result.error = accumulator_error(error);
            return result;
        }
        if (const auto error = validate_feature_side(target, side);
            error != ScalarProbeEvaluateError::NONE)
        {
            result.error = accumulator_error(error);
            return result;
        }
    }
    if (!matches(source))
    {
        result.error = ScalarProbeAccumulatorError::SOURCE_INVENTORY_MISMATCH;
        return result;
    }

    const FeatureMembership  targetMembership = make_membership(target);
    ScalarProbeAccumulatorV1 candidate        = *this;
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        std::array<std::int64_t, ScalarProbeOutputLanes> widened{};
        for (std::size_t lane = 0; lane < widened.size(); ++lane)
            widened[lane] = lanes_[side][lane];
        for (std::size_t row = 0; row < ScalarFeatureInventoryV1::Dimensions; ++row)
        {
            const int direction = targetMembership[side][row] == membership_[side][row] ? 0
                                : targetMembership[side][row]                           ? 1
                                                                                        : -1;
            if (direction == 0)
                continue;
            for (std::size_t lane = 0; lane < widened.size(); ++lane)
                widened[lane] +=
                  direction * std::int64_t(network.weights_[row * ScalarProbeOutputLanes + lane]);
        }
        for (std::size_t lane = 0; lane < widened.size(); ++lane)
        {
            if (widened[lane] < std::numeric_limits<std::int32_t>::min()
                || widened[lane] > std::numeric_limits<std::int32_t>::max())
            {
                result.error = ScalarProbeAccumulatorError::ACCUMULATOR_OVERFLOW;
                return result;
            }
            candidate.lanes_[side][lane] = static_cast<std::int32_t>(widened[lane]);
        }
    }
    candidate.membership_ = targetMembership;
    candidate.sizes_      = target.size;
    *this                 = candidate;
    result.error          = ScalarProbeAccumulatorError::NONE;
    return result;
}

ScalarProbeEvaluationResult ScalarProbeAccumulatorV1::evaluate(Color perspective) const noexcept {
    ScalarProbeEvaluationResult result;
    if (!ready_)
    {
        result.error = ScalarProbeEvaluateError::NETWORK_NOT_READY;
        return result;
    }
    const unsigned side = static_cast<unsigned>(perspective);
    if (side >= COLOR_NB)
    {
        result.error = ScalarProbeEvaluateError::PERSPECTIVE;
        return result;
    }
    result.lanes = lanes_[side];
    result.error = ScalarProbeEvaluateError::NONE;
    return result;
}

std::string_view scalar_probe_load_error_name(ScalarProbeLoadError error) noexcept {
    switch (error)
    {
    case ScalarProbeLoadError::NONE :
        return "NONE";
    case ScalarProbeLoadError::WRONG_SIZE :
        return "WRONG_SIZE";
    case ScalarProbeLoadError::MAGIC :
        return "MAGIC";
    case ScalarProbeLoadError::BYTE_ORDER :
        return "BYTE_ORDER";
    case ScalarProbeLoadError::HEADER_SIZE :
        return "HEADER_SIZE";
    case ScalarProbeLoadError::VERSION :
        return "VERSION";
    case ScalarProbeLoadError::FLAGS :
        return "FLAGS";
    case ScalarProbeLoadError::FILE_SIZE :
        return "FILE_SIZE";
    case ScalarProbeLoadError::FEATURE_DIMENSIONS :
        return "FEATURE_DIMENSIONS";
    case ScalarProbeLoadError::MAXIMUM_ACTIVE :
        return "MAXIMUM_ACTIVE";
    case ScalarProbeLoadError::OUTPUT_LANES :
        return "OUTPUT_LANES";
    case ScalarProbeLoadError::INPUT_SEMANTICS :
        return "INPUT_SEMANTICS";
    case ScalarProbeLoadError::WEIGHT_TYPE :
        return "WEIGHT_TYPE";
    case ScalarProbeLoadError::BIAS_TYPE :
        return "BIAS_TYPE";
    case ScalarProbeLoadError::ACCUMULATOR_TYPE :
        return "ACCUMULATOR_TYPE";
    case ScalarProbeLoadError::WEIGHTS_OFFSET :
        return "WEIGHTS_OFFSET";
    case ScalarProbeLoadError::WEIGHTS_BYTES :
        return "WEIGHTS_BYTES";
    case ScalarProbeLoadError::BIASES_OFFSET :
        return "BIASES_OFFSET";
    case ScalarProbeLoadError::BIASES_BYTES :
        return "BIASES_BYTES";
    case ScalarProbeLoadError::PAYLOAD_BYTES :
        return "PAYLOAD_BYTES";
    case ScalarProbeLoadError::RESERVED_BYTES :
        return "RESERVED_BYTES";
    case ScalarProbeLoadError::RULE_PROFILE_IDENTITY :
        return "RULE_PROFILE_IDENTITY";
    case ScalarProbeLoadError::PHYSICAL_SCHEMA_IDENTITY :
        return "PHYSICAL_SCHEMA_IDENTITY";
    case ScalarProbeLoadError::FEATURE_CONTRACT_IDENTITY :
        return "FEATURE_CONTRACT_IDENTITY";
    case ScalarProbeLoadError::ARCHITECTURE_IDENTITY :
        return "ARCHITECTURE_IDENTITY";
    case ScalarProbeLoadError::HEADER_CRC32C :
        return "HEADER_CRC32C";
    case ScalarProbeLoadError::PAYLOAD_SHA256 :
        return "PAYLOAD_SHA256";
    }
    return "UNKNOWN";
}

std::string_view scalar_probe_evaluate_error_name(ScalarProbeEvaluateError error) noexcept {
    switch (error)
    {
    case ScalarProbeEvaluateError::NONE :
        return "NONE";
    case ScalarProbeEvaluateError::NETWORK_NOT_READY :
        return "NETWORK_NOT_READY";
    case ScalarProbeEvaluateError::FEATURE_STATUS :
        return "FEATURE_STATUS";
    case ScalarProbeEvaluateError::PERSPECTIVE :
        return "PERSPECTIVE";
    case ScalarProbeEvaluateError::ACTIVE_OVERFLOW :
        return "ACTIVE_OVERFLOW";
    case ScalarProbeEvaluateError::FEATURE_INDEX :
        return "FEATURE_INDEX";
    case ScalarProbeEvaluateError::DUPLICATE_FEATURE :
        return "DUPLICATE_FEATURE";
    case ScalarProbeEvaluateError::ACCUMULATOR_OVERFLOW :
        return "ACCUMULATOR_OVERFLOW";
    case ScalarProbeEvaluateError::SIMD_UNAVAILABLE :
        return "SIMD_UNAVAILABLE";
    }
    return "UNKNOWN";
}

std::string_view scalar_probe_accumulator_error_name(ScalarProbeAccumulatorError error) noexcept {
    switch (error)
    {
    case ScalarProbeAccumulatorError::NONE :
        return "NONE";
    case ScalarProbeAccumulatorError::NETWORK_NOT_READY :
        return "NETWORK_NOT_READY";
    case ScalarProbeAccumulatorError::NETWORK_MISMATCH :
        return "NETWORK_MISMATCH";
    case ScalarProbeAccumulatorError::SOURCE_NOT_READY :
        return "SOURCE_NOT_READY";
    case ScalarProbeAccumulatorError::SOURCE_INVENTORY_MISMATCH :
        return "SOURCE_INVENTORY_MISMATCH";
    case ScalarProbeAccumulatorError::FEATURE_STATUS :
        return "FEATURE_STATUS";
    case ScalarProbeAccumulatorError::ACTIVE_OVERFLOW :
        return "ACTIVE_OVERFLOW";
    case ScalarProbeAccumulatorError::FEATURE_INDEX :
        return "FEATURE_INDEX";
    case ScalarProbeAccumulatorError::DUPLICATE_FEATURE :
        return "DUPLICATE_FEATURE";
    case ScalarProbeAccumulatorError::ACCUMULATOR_OVERFLOW :
        return "ACCUMULATOR_OVERFLOW";
    case ScalarProbeAccumulatorError::PERSPECTIVE :
        return "PERSPECTIVE";
    case ScalarProbeAccumulatorError::SIMD_UNAVAILABLE :
        return "SIMD_UNAVAILABLE";
    }
    return "UNKNOWN";
}

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2
