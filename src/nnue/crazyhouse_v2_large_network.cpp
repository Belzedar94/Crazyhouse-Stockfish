/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_large_network.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <new>
#include <tuple>
#include <type_traits>

#if defined(USE_SSE2)
    #include <emmintrin.h>
#endif

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {
namespace {

constexpr std::uint32_t ByteOrderMarker = 0x01020304U;
constexpr std::uint16_t HeaderSize      = 1024;
constexpr std::uint16_t VersionMajor    = 1;
constexpr std::uint16_t VersionMinor    = 0;
constexpr std::uint16_t CommittedFlag   = 1;
constexpr std::uint16_t TensorCount     = 10;

constexpr std::uint16_t SignedInt16 = 1;
constexpr std::uint16_t SignedInt32 = 2;
constexpr std::uint16_t SignedInt8  = 3;

constexpr std::uint32_t PairProductActivation = 2;
constexpr std::uint32_t TransformerClamp      = 255;
constexpr std::uint32_t PairProductDivisor    = 512;
constexpr std::uint32_t WeightScaleBits       = 6;
constexpr std::uint32_t HiddenOne             = 128;
constexpr std::uint32_t OutputScale           = 16;
constexpr std::uint32_t Fc0ActivationShift    = 7;
constexpr std::uint32_t Fc1ActivationShift    = 6;
constexpr std::uint32_t SquaredExtraShift     = 7;
constexpr std::uint32_t OutputMultiplier      = 600 * OutputScale;
constexpr std::uint32_t OutputDenominator     = HiddenOne * (1U << WeightScaleBits) * 2;
constexpr std::uint32_t PocketBucketDivisor   = 4;
constexpr std::uint32_t PocketBucketMaximum   = 7;
constexpr std::uint32_t InputSemantics        = 1;
constexpr std::uint32_t PerspectiveOrder      = 1;
constexpr std::uint32_t TensorDirectoryOffset = 624;
constexpr std::uint32_t TensorEntryBytes      = 40;
constexpr std::uint32_t TensorLayoutVersion   = 1;
constexpr std::size_t   HeaderCrcOffset       = 608;

constexpr std::array<Byte, 16> Magic = {'C', 'H', 'N', 'N', 'U', 'E', 'V', '2',
                                        'L', 'A', 'R', 'G', 'E', 'A', '0', 0};

struct TensorEntry {
    std::uint16_t                id;
    std::uint16_t                type;
    std::uint16_t                rank;
    std::uint16_t                flags;
    std::uint64_t                offset;
    std::uint64_t                bytes;
    std::array<std::uint32_t, 4> dimensions;
};

constexpr std::array<TensorEntry, TensorCount> TensorDirectory = {{
  {1, SignedInt16, 2, 0, 1024, 125435904, {81664, 768, 0, 0}},
  {2, SignedInt16, 1, 0, 125436928, 1536, {768, 0, 0, 0}},
  {3, SignedInt16, 2, 0, 125438464, 686080, {1340, 256, 0, 0}},
  {4, SignedInt16, 1, 0, 126124544, 512, {256, 0, 0, 0}},
  {5, SignedInt32, 2, 0, 126125056, 1024, {8, 32, 0, 0}},
  {6, SignedInt8, 3, 0, 126126080, 262144, {8, 32, 1024, 0}},
  {7, SignedInt32, 2, 0, 126388224, 1024, {8, 32, 0, 0}},
  {8, SignedInt8, 3, 0, 126389248, 16384, {8, 32, 64, 0}},
  {9, SignedInt32, 1, 0, 126405632, 32, {8, 0, 0, 0}},
  {10, SignedInt8, 2, 0, 126405664, 1024, {8, 128, 0, 0}},
}};

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
  digest_from_hex("6e616c2e090b43daa7710ca39aaedc76b43a90db46e8f093466f45b821f44a79");
constexpr Digest ArchitectureIdentity =
  digest_from_hex("2f5efc7cf05f3365bf5e524e636d47a6abdbadcdf5673cc0d260f1e61638341e");
constexpr Digest QuantizationIdentity =
  digest_from_hex("262399c3d1e8f96681f485d8b2d9d6d1c8e783cd1685250317a9c7e244c9386c");

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
    return raw <= std::uint16_t(std::numeric_limits<std::int16_t>::max())
           ? static_cast<std::int16_t>(raw)
           : static_cast<std::int16_t>(std::int32_t(raw) - (std::int32_t{1} << 16));
}

std::int32_t get_i32_le(const Byte* bytes) noexcept {
    const std::uint32_t raw = get_le<std::uint32_t>(bytes);
    return raw <= std::uint32_t(std::numeric_limits<std::int32_t>::max())
           ? static_cast<std::int32_t>(raw)
           : static_cast<std::int32_t>(std::int64_t(raw) - (std::int64_t{1} << 32));
}

std::int8_t get_i8(Byte value) noexcept {
    return value <= Byte(std::numeric_limits<std::int8_t>::max())
           ? static_cast<std::int8_t>(value)
           : static_cast<std::int8_t>(int(value) - 256);
}

bool range_all_zero(const Byte* first, const Byte* last) noexcept {
    return std::all_of(first, last, [](Byte value) { return value == 0; });
}

bool digest_is_zero(const Digest& digest) noexcept {
    return std::all_of(digest.begin(), digest.end(), [](Byte value) { return value == 0; });
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

        std::uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
        std::uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
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
    std::size_t                  buffered{};
    std::uint64_t                totalBytes{};
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

LargeNetworkLoadResultV1 load_failure(LargeNetworkLoadError error) noexcept {
    LargeNetworkLoadResultV1 result;
    result.error = error;
    return result;
}

LargeNetworkEvaluationResultV1 evaluation_failure(LargeNetworkEvaluateError error) noexcept {
    LargeNetworkEvaluationResultV1 result;
    result.error = error;
    return result;
}

bool fits_int32(std::int64_t value) noexcept {
    return value >= std::numeric_limits<std::int32_t>::min()
        && value <= std::numeric_limits<std::int32_t>::max();
}

LargeNetworkEvaluateError validate_domain(const LargeFeatureInventoryV1::DomainResult& domain,
                                          std::size_t dimensions) noexcept {
    if (domain.size > LargeFeatureInventoryV1::MaximumActivePerDomain)
        return LargeNetworkEvaluateError::ACTIVE_OVERFLOW;
    for (std::size_t index = 0; index < domain.size; ++index)
    {
        if (domain.active[index] >= dimensions)
            return LargeNetworkEvaluateError::FEATURE_INDEX;
        for (std::size_t prior = 0; prior < index; ++prior)
            if (domain.active[prior] == domain.active[index])
                return LargeNetworkEvaluateError::DUPLICATE_FEATURE;
    }
    return LargeNetworkEvaluateError::NONE;
}

std::size_t pocket_rows(const LargeFeatureInventoryV1::DomainResult& domain,
                        std::size_t                                  pocketOffset,
                        std::size_t                                  promotedOffset) noexcept {
    return static_cast<std::size_t>(std::count_if(
      domain.active.begin(), domain.active.begin() + static_cast<std::ptrdiff_t>(domain.size),
      [&](LargeFeatureInventoryV1::Index row) {
          return row >= pocketOffset && row < promotedOffset;
      }));
}

LargeNetworkEvaluateError
validate_large_features(const LargeFeatureInventoryV1::Result& features) noexcept {
    if (!features.ok())
        return LargeNetworkEvaluateError::FEATURE_STATUS;
    if (features.totalPocketUnits > 30)
        return LargeNetworkEvaluateError::POCKET_UNITS;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        const auto& featurePerspective = features.perspective[perspective];
        if (const auto error =
              validate_domain(featurePerspective.k64, LargeFeatureInventoryV1::KDimensions);
            error != LargeNetworkEvaluateError::NONE)
            return error;
        if (const auto error =
              validate_domain(featurePerspective.g1, LargeFeatureInventoryV1::GDimensions);
            error != LargeNetworkEvaluateError::NONE)
            return error;
        if (pocket_rows(featurePerspective.k64, LargeFeatureInventoryV1::KPocketOffset,
                        LargeFeatureInventoryV1::KPromotedOffset)
              != features.totalPocketUnits
            || pocket_rows(featurePerspective.g1, LargeFeatureInventoryV1::GPocketOffset,
                           LargeFeatureInventoryV1::GPromotedOffset)
                 != features.totalPocketUnits)
            return LargeNetworkEvaluateError::POCKET_ROUTING_MISMATCH;
    }
    return LargeNetworkEvaluateError::NONE;
}

LargeNetworkAccumulatorError accumulator_error(LargeNetworkEvaluateError error) noexcept {
    switch (error)
    {
    case LargeNetworkEvaluateError::NONE :
        return LargeNetworkAccumulatorError::NONE;
    case LargeNetworkEvaluateError::FEATURE_STATUS :
        return LargeNetworkAccumulatorError::FEATURE_STATUS;
    case LargeNetworkEvaluateError::ACTIVE_OVERFLOW :
        return LargeNetworkAccumulatorError::ACTIVE_OVERFLOW;
    case LargeNetworkEvaluateError::FEATURE_INDEX :
        return LargeNetworkAccumulatorError::FEATURE_INDEX;
    case LargeNetworkEvaluateError::DUPLICATE_FEATURE :
        return LargeNetworkAccumulatorError::DUPLICATE_FEATURE;
    case LargeNetworkEvaluateError::POCKET_UNITS :
        return LargeNetworkAccumulatorError::POCKET_UNITS;
    case LargeNetworkEvaluateError::POCKET_ROUTING_MISMATCH :
        return LargeNetworkAccumulatorError::POCKET_ROUTING_MISMATCH;
    case LargeNetworkEvaluateError::TRANSFORMER_RUNTIME_RANGE :
        return LargeNetworkAccumulatorError::TRANSFORMER_RUNTIME_RANGE;
    default :
        return LargeNetworkAccumulatorError::FEATURE_STATUS;
    }
}

bool contains_row(const LargeFeatureInventoryV1::DomainResult& domain,
                  LargeFeatureInventoryV1::Index               row) noexcept {
    return std::find(domain.active.begin(),
                     domain.active.begin() + static_cast<std::ptrdiff_t>(domain.size), row)
        != domain.active.begin() + static_cast<std::ptrdiff_t>(domain.size);
}

Byte squared_activation(std::int32_t value, unsigned shift) noexcept {
    const std::int64_t squared = std::int64_t(value) * value;
    return static_cast<Byte>(
      std::min<std::int64_t>(127, squared >> static_cast<unsigned>(2 * shift + SquaredExtraShift)));
}

Byte clipped_activation(std::int32_t value, unsigned shift) noexcept {
    if (value <= 0)
        return 0;
    return static_cast<Byte>(std::min<std::int32_t>(127, value >> shift));
}

template<std::size_t Size>
void load_i16_array(std::array<std::int16_t, Size>& output, const Byte* bytes) noexcept {
    for (std::size_t index = 0; index < Size; ++index)
        output[index] = get_i16_le(bytes + index * 2);
}

template<std::size_t Size>
void load_i32_array(std::array<std::int32_t, Size>& output, const Byte* bytes) noexcept {
    for (std::size_t index = 0; index < Size; ++index)
        output[index] = get_i32_le(bytes + index * 4);
}

template<std::size_t Size>
void load_i8_array(std::array<std::int8_t, Size>& output, const Byte* bytes) noexcept {
    for (std::size_t index = 0; index < Size; ++index)
        output[index] = get_i8(bytes[index]);
}

}  // namespace

LargeNetworkLoadError LargeNetworkV1::validate_dense_intervals() const noexcept {
    for (std::size_t bucket = 0; bucket < LargeLayerStacks; ++bucket)
        for (std::size_t output = 0; output < LargeFc0Outputs; ++output)
        {
            std::int64_t      lower = fc0Biases_[bucket * LargeFc0Outputs + output];
            std::int64_t      upper = lower;
            const std::size_t base  = (bucket * LargeFc0Outputs + output) * LargeFc0Inputs;
            for (std::size_t input = 0; input < LargeFc0Inputs; ++input)
            {
                const std::int64_t weight = fc0Weights_[base + input];
                (weight < 0 ? lower : upper) += 127 * weight;
            }
            if (!fits_int32(lower) || !fits_int32(upper))
                return LargeNetworkLoadError::FC0_INTERVAL;
        }

    for (std::size_t bucket = 0; bucket < LargeLayerStacks; ++bucket)
        for (std::size_t output = 0; output < LargeFc1Outputs; ++output)
        {
            std::int64_t      lower = fc1Biases_[bucket * LargeFc1Outputs + output];
            std::int64_t      upper = lower;
            const std::size_t base  = (bucket * LargeFc1Outputs + output) * LargeFc1Inputs;
            for (std::size_t input = 0; input < LargeFc1Inputs; ++input)
            {
                const std::int64_t weight = fc1Weights_[base + input];
                (weight < 0 ? lower : upper) += 127 * weight;
            }
            if (!fits_int32(lower) || !fits_int32(upper))
                return LargeNetworkLoadError::FC1_INTERVAL;
        }

    for (std::size_t bucket = 0; bucket < LargeLayerStacks; ++bucket)
    {
        std::int64_t      lower = fc2Biases_[bucket];
        std::int64_t      upper = lower;
        const std::size_t base  = bucket * LargeFc2Inputs;
        for (std::size_t input = 0; input < LargeFc2Inputs; ++input)
        {
            const std::int64_t weight = fc2Weights_[base + input];
            (weight < 0 ? lower : upper) += 127 * weight;
        }
        if (!fits_int32(lower - 65535) || !fits_int32(upper + 65535))
            return LargeNetworkLoadError::FC2_INTERVAL;
    }
    return LargeNetworkLoadError::NONE;
}

LargeNetworkLoadResultV1 load_large_network_v1(const Byte*                      bytes,
                                               std::size_t                      size,
                                               const LargeExpectedProvenanceV1& expected) noexcept {
    for (const Digest* digest :
         {&expected.datasetManifest, &expected.splitManifest, &expected.trainingConfig,
          &expected.trainerCode, &expected.trainingRuntime, &expected.resumeLineage})
        if (digest_is_zero(*digest))
            return load_failure(LargeNetworkLoadError::EXPECTED_PROVENANCE);
    if (bytes == nullptr || size != LargeNetworkFileBytes)
        return load_failure(LargeNetworkLoadError::WRONG_SIZE);
    if (!std::equal(Magic.begin(), Magic.end(), bytes))
        return load_failure(LargeNetworkLoadError::MAGIC);
    if (get_le<std::uint32_t>(bytes + 16) != ByteOrderMarker)
        return load_failure(LargeNetworkLoadError::BYTE_ORDER_MARKER);
    if (get_le<std::uint16_t>(bytes + 20) != HeaderSize)
        return load_failure(LargeNetworkLoadError::HEADER_SIZE);
    if (get_le<std::uint16_t>(bytes + 22) != VersionMajor
        || get_le<std::uint16_t>(bytes + 24) != VersionMinor)
        return load_failure(LargeNetworkLoadError::VERSION);
    if (get_le<std::uint16_t>(bytes + 26) != CommittedFlag)
        return load_failure(LargeNetworkLoadError::FLAGS);
    if (get_le<std::uint32_t>(bytes + 28) != LargeNetworkFileBytes)
        return load_failure(LargeNetworkLoadError::FILE_SIZE);
    if (get_le<std::uint32_t>(bytes + 32) != LargeNetworkPayloadBytes)
        return load_failure(LargeNetworkLoadError::PAYLOAD_SIZE);
    if (get_le<std::uint16_t>(bytes + 36) != TensorCount)
        return load_failure(LargeNetworkLoadError::TENSOR_COUNT);
    if (get_le<std::uint16_t>(bytes + 38) != LargeLayerStacks)
        return load_failure(LargeNetworkLoadError::LAYER_STACKS);

    constexpr std::array<std::uint32_t, 13> Dimensions = {
      LargeFeatureInventoryV1::KDimensions,
      LargeFeatureInventoryV1::GDimensions,
      LargeFeatureInventoryV1::MaximumActivePerDomain,
      LargeKTransformerLanes,
      LargeGTransformerLanes,
      COLOR_NB,
      LargePerspectiveOutputBytes,
      LargeDenseInputBytes,
      LargeFc0Outputs,
      LargeFc1Inputs,
      LargeFc1Outputs,
      LargeFc2Inputs,
      LargeFc2Outputs,
    };
    constexpr std::array<LargeNetworkLoadError, 13> DimensionErrors = {
      LargeNetworkLoadError::K_DIMENSIONS,
      LargeNetworkLoadError::G_DIMENSIONS,
      LargeNetworkLoadError::MAXIMUM_ACTIVE,
      LargeNetworkLoadError::K_LANES,
      LargeNetworkLoadError::G_LANES,
      LargeNetworkLoadError::PERSPECTIVE_COUNT,
      LargeNetworkLoadError::PERSPECTIVE_OUTPUTS,
      LargeNetworkLoadError::DENSE_INPUTS,
      LargeNetworkLoadError::FC0_OUTPUTS,
      LargeNetworkLoadError::FC1_INPUTS,
      LargeNetworkLoadError::FC1_OUTPUTS,
      LargeNetworkLoadError::FC2_INPUTS,
      LargeNetworkLoadError::FC2_OUTPUTS,
    };
    for (std::size_t index = 0; index < Dimensions.size(); ++index)
        if (get_le<std::uint32_t>(bytes + 40 + index * 4) != Dimensions[index])
            return load_failure(DimensionErrors[index]);

    if (get_le<std::uint32_t>(bytes + 92) != PocketBucketDivisor)
        return load_failure(LargeNetworkLoadError::BUCKET_DIVISOR);
    if (get_le<std::uint32_t>(bytes + 96) != PocketBucketMaximum)
        return load_failure(LargeNetworkLoadError::BUCKET_MAXIMUM);
    constexpr std::array<std::uint16_t, 8> Types = {SignedInt16, SignedInt16,          SignedInt16,
                                                    SignedInt16, SignedInt8,           SignedInt32,
                                                    SignedInt32, PairProductActivation};
    for (std::size_t index = 0; index < Types.size(); ++index)
        if (get_le<std::uint16_t>(bytes + 100 + index * 2) != Types[index])
            return load_failure(LargeNetworkLoadError::TENSOR_TYPES);
    if (get_le<std::uint32_t>(bytes + 116) != TransformerClamp
        || get_le<std::uint32_t>(bytes + 120) != PairProductDivisor)
        return load_failure(LargeNetworkLoadError::TRANSFORM_CONSTANTS);
    if (get_le<std::uint32_t>(bytes + 124) != WeightScaleBits
        || get_le<std::uint32_t>(bytes + 128) != HiddenOne
        || get_le<std::uint32_t>(bytes + 136) != Fc0ActivationShift
        || get_le<std::uint32_t>(bytes + 140) != Fc1ActivationShift
        || get_le<std::uint32_t>(bytes + 144) != SquaredExtraShift)
        return load_failure(LargeNetworkLoadError::ACTIVATION_CONSTANTS);
    if (get_le<std::uint32_t>(bytes + 132) != OutputScale
        || get_le<std::uint32_t>(bytes + 148) != OutputMultiplier
        || get_le<std::uint32_t>(bytes + 152) != OutputDenominator)
        return load_failure(LargeNetworkLoadError::OUTPUT_CONSTANTS);
    if (get_le<std::uint32_t>(bytes + 156) != InputSemantics)
        return load_failure(LargeNetworkLoadError::INPUT_SEMANTICS);
    if (get_le<std::uint32_t>(bytes + 160) != PerspectiveOrder)
        return load_failure(LargeNetworkLoadError::PERSPECTIVE_ORDER);
    if (get_le<std::uint32_t>(bytes + 164) != TensorDirectoryOffset
        || get_le<std::uint32_t>(bytes + 168) != TensorEntryBytes
        || get_le<std::uint32_t>(bytes + 172) != TensorLayoutVersion)
        return load_failure(LargeNetworkLoadError::DIRECTORY_LAYOUT);

    constexpr std::array<std::pair<std::size_t, const Digest*>, 5> FixedDigests      = {{
      {224, &RuleProfileIdentity},
      {256, &PhysicalSchemaIdentity},
      {288, &FeatureContractIdentity},
      {320, &ArchitectureIdentity},
      {352, &QuantizationIdentity},
    }};
    constexpr std::array<LargeNetworkLoadError, 5>                 FixedDigestErrors = {
      LargeNetworkLoadError::RULE_PROFILE_IDENTITY,
      LargeNetworkLoadError::PHYSICAL_SCHEMA_IDENTITY,
      LargeNetworkLoadError::FEATURE_CONTRACT_IDENTITY,
      LargeNetworkLoadError::ARCHITECTURE_IDENTITY,
      LargeNetworkLoadError::QUANTIZATION_IDENTITY,
    };
    for (std::size_t index = 0; index < FixedDigests.size(); ++index)
        if (!matches_digest(bytes + FixedDigests[index].first, *FixedDigests[index].second))
            return load_failure(FixedDigestErrors[index]);

    constexpr std::array<std::size_t, 6> ProvenanceOffsets = {384, 416, 448, 480, 512, 544};
    const std::array<const Digest*, 6>   ExpectedDigests   = {
      &expected.datasetManifest, &expected.splitManifest,   &expected.trainingConfig,
      &expected.trainerCode,     &expected.trainingRuntime, &expected.resumeLineage};
    constexpr std::array<LargeNetworkLoadError, 6> ZeroErrors = {
      LargeNetworkLoadError::DATASET_IDENTITY_ZERO,
      LargeNetworkLoadError::SPLIT_IDENTITY_ZERO,
      LargeNetworkLoadError::TRAINING_CONFIG_IDENTITY_ZERO,
      LargeNetworkLoadError::TRAINER_CODE_IDENTITY_ZERO,
      LargeNetworkLoadError::TRAINING_RUNTIME_IDENTITY_ZERO,
      LargeNetworkLoadError::RESUME_LINEAGE_IDENTITY_ZERO,
    };
    constexpr std::array<LargeNetworkLoadError, 6> MismatchErrors = {
      LargeNetworkLoadError::DATASET_IDENTITY,
      LargeNetworkLoadError::SPLIT_IDENTITY,
      LargeNetworkLoadError::TRAINING_CONFIG_IDENTITY,
      LargeNetworkLoadError::TRAINER_CODE_IDENTITY,
      LargeNetworkLoadError::TRAINING_RUNTIME_IDENTITY,
      LargeNetworkLoadError::RESUME_LINEAGE_IDENTITY,
    };
    for (std::size_t index = 0; index < ProvenanceOffsets.size(); ++index)
    {
        Digest observed{};
        std::copy_n(bytes + ProvenanceOffsets[index], observed.size(), observed.begin());
        if (digest_is_zero(observed))
            return load_failure(ZeroErrors[index]);
        if (observed != *ExpectedDigests[index])
            return load_failure(MismatchErrors[index]);
    }

    if (!range_all_zero(bytes + 176, bytes + 224)
        || !range_all_zero(bytes + 612, bytes + TensorDirectoryOffset))
        return load_failure(LargeNetworkLoadError::RESERVED_BYTES);
    for (std::size_t index = 0; index < TensorDirectory.size(); ++index)
    {
        const Byte*       entry         = bytes + TensorDirectoryOffset + index * TensorEntryBytes;
        const TensorEntry expectedEntry = TensorDirectory[index];
        if (get_le<std::uint16_t>(entry) != expectedEntry.id
            || get_le<std::uint16_t>(entry + 2) != expectedEntry.type
            || get_le<std::uint16_t>(entry + 4) != expectedEntry.rank
            || get_le<std::uint16_t>(entry + 6) != expectedEntry.flags
            || get_le<std::uint64_t>(entry + 8) != expectedEntry.offset
            || get_le<std::uint64_t>(entry + 16) != expectedEntry.bytes)
            return load_failure(LargeNetworkLoadError::TENSOR_DIRECTORY);
        for (std::size_t dimension = 0; dimension < 4; ++dimension)
            if (get_le<std::uint32_t>(entry + 24 + dimension * 4)
                != expectedEntry.dimensions[dimension])
                return load_failure(LargeNetworkLoadError::TENSOR_DIRECTORY);
    }

    std::array<Byte, LargeNetworkHeaderBytes> header{};
    std::copy_n(bytes, header.size(), header.begin());
    std::fill_n(header.begin() + static_cast<std::ptrdiff_t>(HeaderCrcOffset), 4, Byte{0});
    if (crc32c(header.data(), header.size()) != get_le<std::uint32_t>(bytes + HeaderCrcOffset))
        return load_failure(LargeNetworkLoadError::HEADER_CRC32C);
    Sha256 payloadHasher;
    payloadHasher.update(bytes + LargeNetworkHeaderBytes, LargeNetworkPayloadBytes);
    if (!matches_digest(bytes + 576, payloadHasher.final()))
        return load_failure(LargeNetworkLoadError::PAYLOAD_SHA256);

    std::unique_ptr<LargeNetworkV1> network(new (std::nothrow) LargeNetworkV1);
    if (!network)
        return load_failure(LargeNetworkLoadError::ALLOCATION);
    network->kWeights_.reset(new (std::nothrow) std::int16_t[LargeKWeightElements]);
    network->gWeights_.reset(new (std::nothrow) std::int16_t[LargeGWeightElements]);
    if (!network->kWeights_ || !network->gWeights_)
        return load_failure(LargeNetworkLoadError::ALLOCATION);

    const Byte* kWeights = bytes + TensorDirectory[0].offset;
    for (std::size_t index = 0; index < LargeKWeightElements; ++index)
        network->kWeights_[index] = get_i16_le(kWeights + index * 2);
    load_i16_array(network->kBiases_, bytes + TensorDirectory[1].offset);
    const Byte* gWeights = bytes + TensorDirectory[2].offset;
    for (std::size_t index = 0; index < LargeGWeightElements; ++index)
        network->gWeights_[index] = get_i16_le(gWeights + index * 2);
    load_i16_array(network->gBiases_, bytes + TensorDirectory[3].offset);
    load_i32_array(network->fc0Biases_, bytes + TensorDirectory[4].offset);
    load_i8_array(network->fc0Weights_, bytes + TensorDirectory[5].offset);
    load_i32_array(network->fc1Biases_, bytes + TensorDirectory[6].offset);
    load_i8_array(network->fc1Weights_, bytes + TensorDirectory[7].offset);
    load_i32_array(network->fc2Biases_, bytes + TensorDirectory[8].offset);
    load_i8_array(network->fc2Weights_, bytes + TensorDirectory[9].offset);
    network->provenance_ = expected;

    if (const LargeNetworkLoadError interval = network->validate_dense_intervals();
        interval != LargeNetworkLoadError::NONE)
        return load_failure(interval);
    network->ready_ = true;

    LargeNetworkLoadResultV1 result;
    result.error   = LargeNetworkLoadError::NONE;
    result.network = std::move(network);
    return result;
}

LargeNetworkEvaluationResultV1 LargeNetworkV1::evaluate_from_accumulators(
  const LargeFeatureInventoryV1::Result&         features,
  Color                                          sideToMove,
  const std::array<LargeKAccumulator, COLOR_NB>& kAccumulator,
  const std::array<LargeGAccumulator, COLOR_NB>& gAccumulator) const noexcept {
    if (!ready_)
        return evaluation_failure(LargeNetworkEvaluateError::NETWORK_NOT_READY);
    if (sideToMove != WHITE && sideToMove != BLACK)
        return evaluation_failure(LargeNetworkEvaluateError::SIDE_TO_MOVE);

    LargeNetworkEvaluationResultV1 result;
    auto&                          trace = result.trace;
    trace.bucket =
      std::min<std::size_t>(PocketBucketMaximum, features.totalPocketUnits / PocketBucketDivisor);
    trace.kAccumulator = kAccumulator;
    trace.gAccumulator = gAccumulator;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        trace.perspectiveOutput[perspective] = transform_large_pair_product_v1(
          trace.kAccumulator[perspective], trace.gAccumulator[perspective]);
    }

    const LargeDenseInputResultV1 ordered = order_large_dense_input_v1(
      trace.perspectiveOutput[WHITE], trace.perspectiveOutput[BLACK], sideToMove);
    if (!ordered.ok())
        return evaluation_failure(LargeNetworkEvaluateError::SIDE_TO_MOVE);
    trace.denseInput = ordered.bytes;

    for (std::size_t output = 0; output < LargeFc0Outputs; ++output)
    {
        std::int64_t      value = fc0Biases_[trace.bucket * LargeFc0Outputs + output];
        const std::size_t base  = (trace.bucket * LargeFc0Outputs + output) * LargeFc0Inputs;
        for (std::size_t input = 0; input < LargeFc0Inputs; ++input)
            value += std::int64_t(trace.denseInput[input]) * fc0Weights_[base + input];
        if (!fits_int32(value))
            return evaluation_failure(LargeNetworkEvaluateError::FC0_RUNTIME_RANGE);
        trace.fc0[output]        = static_cast<std::int32_t>(value);
        trace.fc0Squared[output] = squared_activation(trace.fc0[output], Fc0ActivationShift);
        trace.fc0Clipped[output] = clipped_activation(trace.fc0[output], Fc0ActivationShift);
    }

    std::array<Byte, LargeFc1Inputs> fc1Input{};
    std::copy(trace.fc0Squared.begin(), trace.fc0Squared.end(), fc1Input.begin());
    std::copy(trace.fc0Clipped.begin(), trace.fc0Clipped.end(),
              fc1Input.begin() + static_cast<std::ptrdiff_t>(LargeFc0Outputs));
    for (std::size_t output = 0; output < LargeFc1Outputs; ++output)
    {
        std::int64_t      value = fc1Biases_[trace.bucket * LargeFc1Outputs + output];
        const std::size_t base  = (trace.bucket * LargeFc1Outputs + output) * LargeFc1Inputs;
        for (std::size_t input = 0; input < LargeFc1Inputs; ++input)
            value += std::int64_t(fc1Input[input]) * fc1Weights_[base + input];
        if (!fits_int32(value))
            return evaluation_failure(LargeNetworkEvaluateError::FC1_RUNTIME_RANGE);
        trace.fc1[output]        = static_cast<std::int32_t>(value);
        trace.fc1Squared[output] = squared_activation(trace.fc1[output], Fc1ActivationShift);
        trace.fc1Clipped[output] = clipped_activation(trace.fc1[output], Fc1ActivationShift);
    }

    std::array<Byte, LargeFc2Inputs> fc2Input{};
    std::copy(trace.fc0Squared.begin(), trace.fc0Squared.end(), fc2Input.begin());
    std::copy(trace.fc0Clipped.begin(), trace.fc0Clipped.end(),
              fc2Input.begin() + static_cast<std::ptrdiff_t>(LargeFc0Outputs));
    std::copy(trace.fc1Squared.begin(), trace.fc1Squared.end(),
              fc2Input.begin() + static_cast<std::ptrdiff_t>(LargeFc0Outputs * 2));
    std::copy(trace.fc1Clipped.begin(), trace.fc1Clipped.end(),
              fc2Input.begin()
                + static_cast<std::ptrdiff_t>(LargeFc0Outputs * 2 + LargeFc1Outputs));
    std::int64_t      fc2     = fc2Biases_[trace.bucket];
    const std::size_t fc2Base = trace.bucket * LargeFc2Inputs;
    for (std::size_t input = 0; input < LargeFc2Inputs; ++input)
        fc2 += std::int64_t(fc2Input[input]) * fc2Weights_[fc2Base + input];
    if (!fits_int32(fc2))
        return evaluation_failure(LargeNetworkEvaluateError::FC2_RUNTIME_RANGE);
    trace.fc2 = static_cast<std::int32_t>(fc2);

    const std::int64_t fwd =
      fc2 + std::int64_t(trace.fc0[LargeFc0Outputs - 2]) - trace.fc0[LargeFc0Outputs - 1];
    if (!fits_int32(fwd))
        return evaluation_failure(LargeNetworkEvaluateError::FC2_RUNTIME_RANGE);
    trace.fwdRaw              = static_cast<std::int32_t>(fwd);
    const std::int64_t scaled = fwd * OutputMultiplier / OutputDenominator;
    if (!fits_int32(scaled))
        return evaluation_failure(LargeNetworkEvaluateError::FC2_RUNTIME_RANGE);
    trace.outputValue = static_cast<std::int32_t>(scaled);
    result.error      = LargeNetworkEvaluateError::NONE;
    return result;
}

LargeNetworkEvaluationResultV1
LargeNetworkV1::evaluate(const LargeFeatureInventoryV1::Result& features,
                         Color                                  sideToMove) const noexcept {
    if (!ready_)
        return evaluation_failure(LargeNetworkEvaluateError::NETWORK_NOT_READY);
    if (const LargeNetworkEvaluateError error = validate_large_features(features);
        error != LargeNetworkEvaluateError::NONE)
        return evaluation_failure(error);
    if (sideToMove != WHITE && sideToMove != BLACK)
        return evaluation_failure(LargeNetworkEvaluateError::SIDE_TO_MOVE);

    std::array<LargeKAccumulator, COLOR_NB> kAccumulator{};
    std::array<LargeGAccumulator, COLOR_NB> gAccumulator{};
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        const auto& rows = features.perspective[perspective];
        for (std::size_t lane = 0; lane < LargeKTransformerLanes; ++lane)
        {
            std::int64_t value = kBiases_[lane];
            for (std::size_t index = 0; index < rows.k64.size; ++index)
                value += kWeights_[rows.k64.active[index] * LargeKTransformerLanes + lane];
            if (!fits_int32(value))
                return evaluation_failure(LargeNetworkEvaluateError::TRANSFORMER_RUNTIME_RANGE);
            kAccumulator[perspective][lane] = static_cast<std::int32_t>(value);
        }
        for (std::size_t lane = 0; lane < LargeGTransformerLanes; ++lane)
        {
            std::int64_t value = gBiases_[lane];
            for (std::size_t index = 0; index < rows.g1.size; ++index)
                value += gWeights_[rows.g1.active[index] * LargeGTransformerLanes + lane];
            if (!fits_int32(value))
                return evaluation_failure(LargeNetworkEvaluateError::TRANSFORMER_RUNTIME_RANGE);
            gAccumulator[perspective][lane] = static_cast<std::int32_t>(value);
        }
    }
    return evaluate_from_accumulators(features, sideToMove, kAccumulator, gAccumulator);
}

LargeNetworkEvaluationResultV1
LargeNetworkV1::evaluate_simd(const LargeFeatureInventoryV1::Result& features,
                              Color                                  sideToMove) const noexcept {
    if (!ready_)
        return evaluation_failure(LargeNetworkEvaluateError::NETWORK_NOT_READY);
    if (const LargeNetworkEvaluateError error = validate_large_features(features);
        error != LargeNetworkEvaluateError::NONE)
        return evaluation_failure(error);
    if (sideToMove != WHITE && sideToMove != BLACK)
        return evaluation_failure(LargeNetworkEvaluateError::SIDE_TO_MOVE);

#if defined(USE_SSE2)
    static_assert(LargeKTransformerLanes % 8 == 0);
    static_assert(LargeGTransformerLanes % 8 == 0);
    std::array<LargeKAccumulator, COLOR_NB> kAccumulator{};
    std::array<LargeGAccumulator, COLOR_NB> gAccumulator{};
    auto transform = [&](const auto& rows, const std::int16_t* weights, const auto& biases,
                         auto& raw) {
        for (std::size_t lane = 0; lane < raw.size(); ++lane)
            raw[lane] = biases[lane];
        const __m128i zero = _mm_setzero_si128();
        for (std::size_t index = 0; index < rows.size; ++index)
        {
            const std::int16_t* row = weights + rows.active[index] * raw.size();
            for (std::size_t lane = 0; lane < raw.size(); lane += 8)
            {
                const __m128i packed = _mm_loadu_si128(
                  reinterpret_cast<const __m128i*>(static_cast<const void*>(row + lane)));
                const __m128i sign        = _mm_cmpgt_epi16(zero, packed);
                const __m128i low         = _mm_unpacklo_epi16(packed, sign);
                const __m128i high        = _mm_unpackhi_epi16(packed, sign);
                __m128i       accumulator = _mm_loadu_si128(
                  reinterpret_cast<const __m128i*>(static_cast<const void*>(raw.data() + lane)));
                accumulator = _mm_add_epi32(accumulator, low);
                _mm_storeu_si128(reinterpret_cast<__m128i*>(static_cast<void*>(raw.data() + lane)),
                                 accumulator);
                accumulator = _mm_loadu_si128(reinterpret_cast<const __m128i*>(
                  static_cast<const void*>(raw.data() + lane + 4)));
                accumulator = _mm_add_epi32(accumulator, high);
                _mm_storeu_si128(
                  reinterpret_cast<__m128i*>(static_cast<void*>(raw.data() + lane + 4)),
                  accumulator);
            }
        }
    };
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        transform(features.perspective[perspective].k64, kWeights_.get(), kBiases_,
                  kAccumulator[perspective]);
        transform(features.perspective[perspective].g1, gWeights_.get(), gBiases_,
                  gAccumulator[perspective]);
    }
    return evaluate_from_accumulators(features, sideToMove, kAccumulator, gAccumulator);
#else
    return evaluation_failure(LargeNetworkEvaluateError::SIMD_UNAVAILABLE);
#endif
}

LargeNetworkAccumulatorResultV1
LargeNetworkAccumulatorV1::refresh(const LargeNetworkV1&                  network,
                                   const LargeFeatureInventoryV1::Result& features) noexcept {
    LargeNetworkAccumulatorResultV1 result;
    if (!network.ready())
    {
        result.error = LargeNetworkAccumulatorError::NETWORK_NOT_READY;
        return result;
    }
    if (const LargeNetworkEvaluateError error = validate_large_features(features);
        error != LargeNetworkEvaluateError::NONE)
    {
        result.error = accumulator_error(error);
        return result;
    }

    LargeNetworkAccumulatorV1 candidate;
    candidate.network_ = &network;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        const auto& rows = features.perspective[perspective];
        for (std::size_t lane = 0; lane < LargeKTransformerLanes; ++lane)
        {
            std::int64_t value = network.kBiases_[lane];
            for (std::size_t index = 0; index < rows.k64.size; ++index)
                value += network.kWeights_[rows.k64.active[index] * LargeKTransformerLanes + lane];
            if (!fits_int32(value))
            {
                result.error = LargeNetworkAccumulatorError::TRANSFORMER_RUNTIME_RANGE;
                return result;
            }
            candidate.kAccumulator_[perspective][lane] = static_cast<std::int32_t>(value);
        }
        for (std::size_t lane = 0; lane < LargeGTransformerLanes; ++lane)
        {
            std::int64_t value = network.gBiases_[lane];
            for (std::size_t index = 0; index < rows.g1.size; ++index)
                value += network.gWeights_[rows.g1.active[index] * LargeGTransformerLanes + lane];
            if (!fits_int32(value))
            {
                result.error = LargeNetworkAccumulatorError::TRANSFORMER_RUNTIME_RANGE;
                return result;
            }
            candidate.gAccumulator_[perspective][lane] = static_cast<std::int32_t>(value);
        }
        for (std::size_t index = 0; index < rows.k64.size; ++index)
            candidate.kMembership_[perspective][rows.k64.active[index]] = true;
        for (std::size_t index = 0; index < rows.g1.size; ++index)
            candidate.gMembership_[perspective][rows.g1.active[index]] = true;
        candidate.kSizes_[perspective] = rows.k64.size;
        candidate.gSizes_[perspective] = rows.g1.size;
    }
    candidate.totalPocketUnits_ = features.totalPocketUnits;
    candidate.ready_            = true;
    *this                       = candidate;
    result.error                = LargeNetworkAccumulatorError::NONE;
    return result;
}

bool LargeNetworkAccumulatorV1::matches(
  const LargeFeatureInventoryV1::Result& features) const noexcept {
    if (!ready_ || validate_large_features(features) != LargeNetworkEvaluateError::NONE
        || totalPocketUnits_ != features.totalPocketUnits)
        return false;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        const auto& rows = features.perspective[perspective];
        if (kSizes_[perspective] != rows.k64.size || gSizes_[perspective] != rows.g1.size)
            return false;
        for (std::size_t index = 0; index < rows.k64.size; ++index)
            if (!kMembership_[perspective][rows.k64.active[index]])
                return false;
        for (std::size_t index = 0; index < rows.g1.size; ++index)
            if (!gMembership_[perspective][rows.g1.active[index]])
                return false;
    }
    return true;
}

LargeNetworkAccumulatorResultV1
LargeNetworkAccumulatorV1::update(const LargeNetworkV1&                  network,
                                  const LargeFeatureInventoryV1::Result& source,
                                  const LargeFeatureInventoryV1::Result& target) noexcept {
    LargeNetworkAccumulatorResultV1 result;
    if (!network.ready())
    {
        result.error = LargeNetworkAccumulatorError::NETWORK_NOT_READY;
        return result;
    }
    if (!ready_)
    {
        result.error = LargeNetworkAccumulatorError::SOURCE_NOT_READY;
        return result;
    }
    if (network_ != &network)
    {
        result.error = LargeNetworkAccumulatorError::NETWORK_MISMATCH;
        return result;
    }
    if (const LargeNetworkEvaluateError error = validate_large_features(source);
        error != LargeNetworkEvaluateError::NONE)
    {
        result.error = accumulator_error(error);
        return result;
    }
    if (const LargeNetworkEvaluateError error = validate_large_features(target);
        error != LargeNetworkEvaluateError::NONE)
    {
        result.error = accumulator_error(error);
        return result;
    }
    if (!matches(source))
    {
        result.error = LargeNetworkAccumulatorError::SOURCE_INVENTORY_MISMATCH;
        return result;
    }

    LargeNetworkAccumulatorV1 candidate = *this;
    auto apply_domain = [&](const LargeFeatureInventoryV1::DomainResult& sourceRows,
                            const LargeFeatureInventoryV1::DomainResult& targetRows,
                            const std::int16_t* weights, const auto& committed, auto& updated,
                            auto& membership, std::size_t& committedSize) -> bool {
        using Accumulator                     = std::decay_t<decltype(committed)>;
        constexpr std::size_t           Lanes = std::tuple_size_v<Accumulator>;
        std::array<std::int64_t, Lanes> widened{};
        for (std::size_t lane = 0; lane < Lanes; ++lane)
            widened[lane] = committed[lane];
        auto add_row = [&](LargeFeatureInventoryV1::Index row, int direction) {
            const std::int16_t* values = weights + row * Lanes;
            for (std::size_t lane = 0; lane < Lanes; ++lane)
                widened[lane] += direction * std::int64_t(values[lane]);
        };
        for (std::size_t index = 0; index < sourceRows.size; ++index)
            if (!contains_row(targetRows, sourceRows.active[index]))
                add_row(sourceRows.active[index], -1);
        for (std::size_t index = 0; index < targetRows.size; ++index)
            if (!contains_row(sourceRows, targetRows.active[index]))
                add_row(targetRows.active[index], 1);
        for (std::size_t lane = 0; lane < Lanes; ++lane)
        {
            if (!fits_int32(widened[lane]))
                return false;
            updated[lane] = static_cast<std::int32_t>(widened[lane]);
        }
        membership.reset();
        for (std::size_t index = 0; index < targetRows.size; ++index)
            membership[targetRows.active[index]] = true;
        committedSize = targetRows.size;
        return true;
    };
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        if (!apply_domain(source.perspective[perspective].k64, target.perspective[perspective].k64,
                          network.kWeights_.get(), kAccumulator_[perspective],
                          candidate.kAccumulator_[perspective], candidate.kMembership_[perspective],
                          candidate.kSizes_[perspective])
            || !apply_domain(source.perspective[perspective].g1, target.perspective[perspective].g1,
                             network.gWeights_.get(), gAccumulator_[perspective],
                             candidate.gAccumulator_[perspective],
                             candidate.gMembership_[perspective], candidate.gSizes_[perspective]))
        {
            result.error = LargeNetworkAccumulatorError::TRANSFORMER_RUNTIME_RANGE;
            return result;
        }
    }
    candidate.totalPocketUnits_ = target.totalPocketUnits;
    *this                       = candidate;
    result.error                = LargeNetworkAccumulatorError::NONE;
    return result;
}

LargeNetworkEvaluationResultV1
LargeNetworkAccumulatorV1::evaluate(const LargeFeatureInventoryV1::Result& features,
                                    Color sideToMove) const noexcept {
    if (!ready_ || network_ == nullptr || !network_->ready())
        return evaluation_failure(LargeNetworkEvaluateError::ACCUMULATOR_NOT_READY);
    if (sideToMove != WHITE && sideToMove != BLACK)
        return evaluation_failure(LargeNetworkEvaluateError::SIDE_TO_MOVE);
    if (const LargeNetworkEvaluateError error = validate_large_features(features);
        error != LargeNetworkEvaluateError::NONE)
        return evaluation_failure(error);
    if (!matches(features))
        return evaluation_failure(LargeNetworkEvaluateError::ACCUMULATOR_INVENTORY_MISMATCH);
    return network_->evaluate_from_accumulators(features, sideToMove, kAccumulator_, gAccumulator_);
}

LargeNetworkSimdBackend large_network_simd_backend() noexcept {
#if defined(USE_SSE2)
    return LargeNetworkSimdBackend::SSE2_X8_INT16_TO_INT32;
#else
    return LargeNetworkSimdBackend::UNAVAILABLE;
#endif
}

Digest large_network_sha256(const Byte* bytes, std::size_t size) noexcept {
    Sha256 hash;
    hash.update(bytes, size);
    return hash.final();
}

std::string_view large_network_load_error_name(LargeNetworkLoadError error) noexcept {
#define LARGE_LOAD_NAME(value) \
    case LargeNetworkLoadError::value : \
        return #value
    switch (error)
    {
        LARGE_LOAD_NAME(NONE);
        LARGE_LOAD_NAME(WRONG_SIZE);
        LARGE_LOAD_NAME(EXPECTED_PROVENANCE);
        LARGE_LOAD_NAME(MAGIC);
    case LargeNetworkLoadError::BYTE_ORDER_MARKER :
        return "BYTE_ORDER";
        LARGE_LOAD_NAME(HEADER_SIZE);
        LARGE_LOAD_NAME(VERSION);
        LARGE_LOAD_NAME(FLAGS);
        LARGE_LOAD_NAME(FILE_SIZE);
        LARGE_LOAD_NAME(PAYLOAD_SIZE);
        LARGE_LOAD_NAME(TENSOR_COUNT);
        LARGE_LOAD_NAME(LAYER_STACKS);
        LARGE_LOAD_NAME(K_DIMENSIONS);
        LARGE_LOAD_NAME(G_DIMENSIONS);
        LARGE_LOAD_NAME(MAXIMUM_ACTIVE);
        LARGE_LOAD_NAME(K_LANES);
        LARGE_LOAD_NAME(G_LANES);
        LARGE_LOAD_NAME(PERSPECTIVE_COUNT);
        LARGE_LOAD_NAME(PERSPECTIVE_OUTPUTS);
        LARGE_LOAD_NAME(DENSE_INPUTS);
        LARGE_LOAD_NAME(FC0_OUTPUTS);
        LARGE_LOAD_NAME(FC1_INPUTS);
        LARGE_LOAD_NAME(FC1_OUTPUTS);
        LARGE_LOAD_NAME(FC2_INPUTS);
        LARGE_LOAD_NAME(FC2_OUTPUTS);
        LARGE_LOAD_NAME(BUCKET_DIVISOR);
        LARGE_LOAD_NAME(BUCKET_MAXIMUM);
        LARGE_LOAD_NAME(TENSOR_TYPES);
        LARGE_LOAD_NAME(TRANSFORM_CONSTANTS);
        LARGE_LOAD_NAME(ACTIVATION_CONSTANTS);
        LARGE_LOAD_NAME(OUTPUT_CONSTANTS);
        LARGE_LOAD_NAME(INPUT_SEMANTICS);
        LARGE_LOAD_NAME(PERSPECTIVE_ORDER);
        LARGE_LOAD_NAME(DIRECTORY_LAYOUT);
        LARGE_LOAD_NAME(RULE_PROFILE_IDENTITY);
        LARGE_LOAD_NAME(PHYSICAL_SCHEMA_IDENTITY);
        LARGE_LOAD_NAME(FEATURE_CONTRACT_IDENTITY);
        LARGE_LOAD_NAME(ARCHITECTURE_IDENTITY);
        LARGE_LOAD_NAME(QUANTIZATION_IDENTITY);
        LARGE_LOAD_NAME(DATASET_IDENTITY_ZERO);
        LARGE_LOAD_NAME(DATASET_IDENTITY);
        LARGE_LOAD_NAME(SPLIT_IDENTITY_ZERO);
        LARGE_LOAD_NAME(SPLIT_IDENTITY);
        LARGE_LOAD_NAME(TRAINING_CONFIG_IDENTITY_ZERO);
        LARGE_LOAD_NAME(TRAINING_CONFIG_IDENTITY);
        LARGE_LOAD_NAME(TRAINER_CODE_IDENTITY_ZERO);
        LARGE_LOAD_NAME(TRAINER_CODE_IDENTITY);
        LARGE_LOAD_NAME(TRAINING_RUNTIME_IDENTITY_ZERO);
        LARGE_LOAD_NAME(TRAINING_RUNTIME_IDENTITY);
        LARGE_LOAD_NAME(RESUME_LINEAGE_IDENTITY_ZERO);
        LARGE_LOAD_NAME(RESUME_LINEAGE_IDENTITY);
        LARGE_LOAD_NAME(RESERVED_BYTES);
        LARGE_LOAD_NAME(TENSOR_DIRECTORY);
        LARGE_LOAD_NAME(HEADER_CRC32C);
        LARGE_LOAD_NAME(PAYLOAD_SHA256);
        LARGE_LOAD_NAME(ALLOCATION);
        LARGE_LOAD_NAME(FC0_INTERVAL);
        LARGE_LOAD_NAME(FC1_INTERVAL);
        LARGE_LOAD_NAME(FC2_INTERVAL);
    }
#undef LARGE_LOAD_NAME
    return "UNKNOWN";
}

std::string_view large_network_evaluate_error_name(LargeNetworkEvaluateError error) noexcept {
#define LARGE_EVALUATE_NAME(value) \
    case LargeNetworkEvaluateError::value : \
        return #value
    switch (error)
    {
        LARGE_EVALUATE_NAME(NONE);
        LARGE_EVALUATE_NAME(NETWORK_NOT_READY);
        LARGE_EVALUATE_NAME(ACCUMULATOR_NOT_READY);
        LARGE_EVALUATE_NAME(ACCUMULATOR_INVENTORY_MISMATCH);
        LARGE_EVALUATE_NAME(FEATURE_STATUS);
        LARGE_EVALUATE_NAME(SIDE_TO_MOVE);
        LARGE_EVALUATE_NAME(ACTIVE_OVERFLOW);
        LARGE_EVALUATE_NAME(FEATURE_INDEX);
        LARGE_EVALUATE_NAME(DUPLICATE_FEATURE);
        LARGE_EVALUATE_NAME(POCKET_UNITS);
        LARGE_EVALUATE_NAME(POCKET_ROUTING_MISMATCH);
        LARGE_EVALUATE_NAME(TRANSFORMER_RUNTIME_RANGE);
        LARGE_EVALUATE_NAME(FC0_RUNTIME_RANGE);
        LARGE_EVALUATE_NAME(FC1_RUNTIME_RANGE);
        LARGE_EVALUATE_NAME(FC2_RUNTIME_RANGE);
        LARGE_EVALUATE_NAME(SIMD_UNAVAILABLE);
    }
#undef LARGE_EVALUATE_NAME
    return "UNKNOWN";
}

std::string_view large_network_accumulator_error_name(LargeNetworkAccumulatorError error) noexcept {
#define LARGE_ACCUMULATOR_NAME(value) \
    case LargeNetworkAccumulatorError::value : \
        return #value
    switch (error)
    {
        LARGE_ACCUMULATOR_NAME(NONE);
        LARGE_ACCUMULATOR_NAME(NETWORK_NOT_READY);
        LARGE_ACCUMULATOR_NAME(NETWORK_MISMATCH);
        LARGE_ACCUMULATOR_NAME(SOURCE_NOT_READY);
        LARGE_ACCUMULATOR_NAME(SOURCE_INVENTORY_MISMATCH);
        LARGE_ACCUMULATOR_NAME(FEATURE_STATUS);
        LARGE_ACCUMULATOR_NAME(ACTIVE_OVERFLOW);
        LARGE_ACCUMULATOR_NAME(FEATURE_INDEX);
        LARGE_ACCUMULATOR_NAME(DUPLICATE_FEATURE);
        LARGE_ACCUMULATOR_NAME(POCKET_UNITS);
        LARGE_ACCUMULATOR_NAME(POCKET_ROUTING_MISMATCH);
        LARGE_ACCUMULATOR_NAME(TRANSFORMER_RUNTIME_RANGE);
    }
#undef LARGE_ACCUMULATOR_NAME
    return "UNKNOWN";
}

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2
