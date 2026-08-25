/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_productive.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <new>
#include <type_traits>

#if defined(USE_SSE2)
    #include <emmintrin.h>
#endif

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {
namespace {

constexpr std::uint32_t ByteOrderMarker   = 0x01020304U;
constexpr std::uint16_t HeaderSize        = 512;
constexpr std::uint16_t VersionMajor      = 1;
constexpr std::uint16_t VersionMinor      = 0;
constexpr std::uint16_t CommittedFlag     = 1;
constexpr std::uint32_t FileSize          = 960324;
constexpr std::uint32_t FeatureDimensions = 902;
constexpr std::uint32_t MaximumActive     = 138;
constexpr std::uint32_t TransformerLanes  = 512;
constexpr std::uint32_t PerspectiveCount  = 2;
constexpr std::uint32_t Dense0Inputs      = 1024;
constexpr std::uint32_t Dense0Outputs     = 32;
constexpr std::uint32_t Dense1Inputs      = 32;
constexpr std::uint32_t Dense1Outputs     = 32;
constexpr std::uint32_t OutputInputs      = 32;
constexpr std::uint32_t OutputOutputs     = 1;

constexpr std::uint16_t SignedInt16 = 1;
constexpr std::uint16_t SignedInt32 = 2;
constexpr std::uint16_t SignedInt8  = 3;
constexpr std::uint16_t ClippedRelu = 1;

constexpr std::uint32_t TransformerScale       = 127;
constexpr std::uint32_t DenseWeightScale       = 64;
constexpr std::uint32_t OutputWeightScale      = 64;
constexpr std::uint32_t OutputValueScale       = 600;
constexpr std::uint32_t DenseActivationDivisor = 64;
constexpr std::uint32_t OutputDivisor          = 8128;

constexpr std::array<Byte, 16> Magic = {'C', 'H', 'N', 'N', 'U', 'E', 'V', '2',
                                        'P', 'R', 'O', 'D', '1', 0,   0,   0};

struct TensorEntry {
    std::uint32_t offset;
    std::uint32_t bytes;
};

constexpr std::array<TensorEntry, 8> TensorDirectory = {{{512, 923648},
                                                         {924160, 2048},
                                                         {926208, 32768},
                                                         {958976, 128},
                                                         {959104, 1024},
                                                         {960128, 128},
                                                         {960256, 64},
                                                         {960320, 4}}};

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
  digest_from_hex("76ebf73988d21fdd3dbf3c34420be0abe6a587419c9f170c16fa3acde4c112b6");
constexpr Digest QuantizationIdentity =
  digest_from_hex("0a9d811ce76509ab58c1eec02fd87cef9df3804d76eb2fe2ae156183b23311a3");

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

std::int8_t get_i8(Byte value) noexcept {
    if (value <= Byte(std::numeric_limits<std::int8_t>::max()))
        return static_cast<std::int8_t>(value);
    return static_cast<std::int8_t>(int(value) - 256);
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

ProductiveLoadResultV1 failure(ProductiveLoadError error) noexcept {
    ProductiveLoadResultV1 result;
    result.error = error;
    return result;
}

bool fits_int32(std::int64_t value) noexcept {
    return value >= std::numeric_limits<std::int32_t>::min()
        && value <= std::numeric_limits<std::int32_t>::max();
}

template<typename Weight, std::size_t WeightCount, std::size_t BiasCount>
ProductiveLoadError validate_dense_interval(const std::array<Weight, WeightCount>&     weights,
                                            const std::array<std::int32_t, BiasCount>& biases,
                                            std::size_t                                inputs,
                                            ProductiveLoadError error) noexcept {
    for (std::size_t output = 0; output < BiasCount; ++output)
    {
        std::int64_t lower = biases[output];
        std::int64_t upper = biases[output];
        for (std::size_t input = 0; input < inputs; ++input)
        {
            const std::int64_t value = weights[output * inputs + input];
            if (value < 0)
                lower += 127 * value;
            else if (value > 0)
                upper += 127 * value;
        }
        if (!fits_int32(lower) || !fits_int32(upper))
            return error;
    }
    return ProductiveLoadError::NONE;
}

ProductiveEvaluateError validate_feature_side(const ScalarFeatureInventoryV1::Result& features,
                                              unsigned side) noexcept {
    if (!features.ok())
        return ProductiveEvaluateError::FEATURE_STATUS;
    if (side >= COLOR_NB)
        return ProductiveEvaluateError::SIDE_TO_MOVE;
    if (features.size[side] > ScalarFeatureInventoryV1::MaximumActive)
        return ProductiveEvaluateError::ACTIVE_OVERFLOW;
    for (std::size_t index = 0; index < features.size[side]; ++index)
    {
        const auto row = features.active[side][index];
        if (row >= ScalarFeatureInventoryV1::Dimensions)
            return ProductiveEvaluateError::FEATURE_INDEX;
        for (std::size_t prior = 0; prior < index; ++prior)
            if (features.active[side][prior] == row)
                return ProductiveEvaluateError::DUPLICATE_FEATURE;
    }
    return ProductiveEvaluateError::NONE;
}

using ProductiveFeatureMembership =
  std::array<std::array<bool, ScalarFeatureInventoryV1::Dimensions>, COLOR_NB>;

ProductiveFeatureMembership
make_productive_membership(const ScalarFeatureInventoryV1::Result& features) noexcept {
    ProductiveFeatureMembership membership{};
    for (unsigned side = 0; side < COLOR_NB; ++side)
        for (std::size_t index = 0; index < features.size[side]; ++index)
            membership[side][features.active[side][index]] = true;
    return membership;
}

ProductiveAccumulatorError accumulator_error(ProductiveEvaluateError error) noexcept {
    switch (error)
    {
    case ProductiveEvaluateError::NONE :
        return ProductiveAccumulatorError::NONE;
    case ProductiveEvaluateError::NETWORK_NOT_READY :
        return ProductiveAccumulatorError::NETWORK_NOT_READY;
    case ProductiveEvaluateError::FEATURE_STATUS :
        return ProductiveAccumulatorError::FEATURE_STATUS;
    case ProductiveEvaluateError::ACTIVE_OVERFLOW :
        return ProductiveAccumulatorError::ACTIVE_OVERFLOW;
    case ProductiveEvaluateError::FEATURE_INDEX :
        return ProductiveAccumulatorError::FEATURE_INDEX;
    case ProductiveEvaluateError::DUPLICATE_FEATURE :
        return ProductiveAccumulatorError::DUPLICATE_FEATURE;
    case ProductiveEvaluateError::TRANSFORMER_RUNTIME_RANGE :
        return ProductiveAccumulatorError::TRANSFORMER_RUNTIME_RANGE;
    case ProductiveEvaluateError::ACCUMULATOR_NOT_READY :
    case ProductiveEvaluateError::ACCUMULATOR_INVENTORY_MISMATCH :
    case ProductiveEvaluateError::SIDE_TO_MOVE :
    case ProductiveEvaluateError::DENSE0_RUNTIME_RANGE :
    case ProductiveEvaluateError::DENSE1_RUNTIME_RANGE :
    case ProductiveEvaluateError::OUTPUT_RUNTIME_RANGE :
    case ProductiveEvaluateError::SIMD_UNAVAILABLE :
        return ProductiveAccumulatorError::FEATURE_STATUS;
    }
    return ProductiveAccumulatorError::FEATURE_STATUS;
}

ProductiveEvaluationResultV1 evaluation_failure(ProductiveEvaluateError error) noexcept {
    ProductiveEvaluationResultV1 result;
    result.error = error;
    return result;
}

Byte transformer_activation(std::int32_t value) noexcept {
    return value <= 0 ? Byte{0} : value >= 127 ? Byte{127} : static_cast<Byte>(value);
}

Byte dense_activation(std::int32_t value) noexcept {
    return value <= 0                           ? Byte{0}
         : value >= std::int32_t(OutputDivisor) ? Byte{127}
                                                : static_cast<Byte>(value / DenseActivationDivisor);
}

}  // namespace

ProductiveLoadError ProductiveNetworkV1::validate_static_intervals() const noexcept {
    std::array<std::int16_t, ScalarFeatureInventoryV1::Dimensions> column{};
    for (std::size_t lane = 0; lane < ProductiveTransformerLanes; ++lane)
    {
        for (std::size_t row = 0; row < ScalarFeatureInventoryV1::Dimensions; ++row)
            column[row] = transformerWeights_[row * ProductiveTransformerLanes + lane];
        std::sort(column.begin(), column.end());
        std::int64_t lower = transformerBiases_[lane];
        std::int64_t upper = transformerBiases_[lane];
        for (std::size_t index = 0;
             index < ScalarFeatureInventoryV1::MaximumActive && column[index] < 0; ++index)
            lower += column[index];
        for (std::size_t count = 0; count < ScalarFeatureInventoryV1::MaximumActive; ++count)
        {
            const std::int16_t value = column[column.size() - 1 - count];
            if (value <= 0)
                break;
            upper += value;
        }
        if (!fits_int32(lower) || !fits_int32(upper))
            return ProductiveLoadError::TRANSFORMER_INTERVAL;
    }
    ProductiveLoadError interval = validate_dense_interval(
      dense0Weights_, dense0Biases_, ProductiveDense0Inputs, ProductiveLoadError::DENSE0_INTERVAL);
    if (interval != ProductiveLoadError::NONE)
        return interval;
    interval = validate_dense_interval(dense1Weights_, dense1Biases_, ProductiveDense1Inputs,
                                       ProductiveLoadError::DENSE1_INTERVAL);
    if (interval != ProductiveLoadError::NONE)
        return interval;
    const std::array<std::int32_t, 1> outputBias = {outputBias_};
    return validate_dense_interval(outputWeights_, outputBias, ProductiveOutputInputs,
                                   ProductiveLoadError::OUTPUT_INTERVAL);
}

ProductiveLoadResultV1 load_productive_v1(const Byte*                           bytes,
                                          std::size_t                           size,
                                          const ProductiveExpectedProvenanceV1& expected) noexcept {
    if (digest_is_zero(expected.datasetManifest) || digest_is_zero(expected.trainingConfig))
        return failure(ProductiveLoadError::EXPECTED_PROVENANCE);
    if (bytes == nullptr || size != ProductiveFileBytes)
        return failure(ProductiveLoadError::WRONG_SIZE);
    if (!std::equal(Magic.begin(), Magic.end(), bytes))
        return failure(ProductiveLoadError::MAGIC);
    if (get_le<std::uint32_t>(bytes + 16) != ByteOrderMarker)
        return failure(ProductiveLoadError::BYTE_ORDER);
    if (get_le<std::uint16_t>(bytes + 20) != HeaderSize)
        return failure(ProductiveLoadError::HEADER_SIZE);
    if (get_le<std::uint16_t>(bytes + 22) != VersionMajor
        || get_le<std::uint16_t>(bytes + 24) != VersionMinor)
        return failure(ProductiveLoadError::VERSION);
    if (get_le<std::uint16_t>(bytes + 26) != CommittedFlag)
        return failure(ProductiveLoadError::FLAGS);
    if (get_le<std::uint32_t>(bytes + 28) != FileSize)
        return failure(ProductiveLoadError::FILE_SIZE);

    constexpr std::array<std::uint32_t, 10> Dimensions = {
      FeatureDimensions, MaximumActive, TransformerLanes, PerspectiveCount, Dense0Inputs,
      Dense0Outputs,     Dense1Inputs,  Dense1Outputs,    OutputInputs,     OutputOutputs};
    constexpr std::array<ProductiveLoadError, 10> DimensionErrors = {
      ProductiveLoadError::FEATURE_DIMENSIONS, ProductiveLoadError::MAXIMUM_ACTIVE,
      ProductiveLoadError::TRANSFORMER_LANES,  ProductiveLoadError::PERSPECTIVE_COUNT,
      ProductiveLoadError::DENSE0_INPUTS,      ProductiveLoadError::DENSE0_OUTPUTS,
      ProductiveLoadError::DENSE1_INPUTS,      ProductiveLoadError::DENSE1_OUTPUTS,
      ProductiveLoadError::OUTPUT_INPUTS,      ProductiveLoadError::OUTPUT_OUTPUTS};
    for (std::size_t index = 0; index < Dimensions.size(); ++index)
        if (get_le<std::uint32_t>(bytes + 32 + 4 * index) != Dimensions[index])
            return failure(DimensionErrors[index]);

    constexpr std::array<std::uint16_t, 8>       Types      = {SignedInt16, SignedInt32, SignedInt8,
                                                               SignedInt32, SignedInt16, SignedInt32,
                                                               SignedInt32, ClippedRelu};
    constexpr std::array<ProductiveLoadError, 8> TypeErrors = {
      ProductiveLoadError::TRANSFORMER_WEIGHT_TYPE, ProductiveLoadError::TRANSFORMER_BIAS_TYPE,
      ProductiveLoadError::DENSE_WEIGHT_TYPE,       ProductiveLoadError::DENSE_BIAS_TYPE,
      ProductiveLoadError::OUTPUT_WEIGHT_TYPE,      ProductiveLoadError::OUTPUT_BIAS_TYPE,
      ProductiveLoadError::ACCUMULATOR_TYPE,        ProductiveLoadError::ACTIVATION_TYPE};
    for (std::size_t index = 0; index < Types.size(); ++index)
        if (get_le<std::uint16_t>(bytes + 72 + 2 * index) != Types[index])
            return failure(TypeErrors[index]);

    constexpr std::array<std::uint32_t, 6>       Scales = {TransformerScale,       DenseWeightScale,
                                                           OutputWeightScale,      OutputValueScale,
                                                           DenseActivationDivisor, OutputDivisor};
    constexpr std::array<ProductiveLoadError, 6> ScaleErrors = {
      ProductiveLoadError::TRANSFORMER_SCALE,        ProductiveLoadError::DENSE_WEIGHT_SCALE,
      ProductiveLoadError::OUTPUT_WEIGHT_SCALE,      ProductiveLoadError::OUTPUT_VALUE_SCALE,
      ProductiveLoadError::DENSE_ACTIVATION_DIVISOR, ProductiveLoadError::OUTPUT_DIVISOR};
    for (std::size_t index = 0; index < Scales.size(); ++index)
        if (get_le<std::uint32_t>(bytes + 88 + 4 * index) != Scales[index])
            return failure(ScaleErrors[index]);
    for (std::size_t index = 0; index < TensorDirectory.size(); ++index)
        if (get_le<std::uint32_t>(bytes + 112 + 8 * index) != TensorDirectory[index].offset
            || get_le<std::uint32_t>(bytes + 116 + 8 * index) != TensorDirectory[index].bytes)
            return failure(ProductiveLoadError::TENSOR_DIRECTORY);

    constexpr std::array<ProductiveLoadError, 4> SemanticErrors = {
      ProductiveLoadError::INPUT_SEMANTICS, ProductiveLoadError::PERSPECTIVE_ORDER,
      ProductiveLoadError::ACTIVATION_SEMANTICS, ProductiveLoadError::OUTPUT_UNITS};
    for (std::size_t index = 0; index < SemanticErrors.size(); ++index)
        if (get_le<std::uint32_t>(bytes + 176 + 4 * index) != 1)
            return failure(SemanticErrors[index]);

    if (!matches_digest(bytes + 192, RuleProfileIdentity))
        return failure(ProductiveLoadError::RULE_PROFILE_IDENTITY);
    if (!matches_digest(bytes + 224, PhysicalSchemaIdentity))
        return failure(ProductiveLoadError::PHYSICAL_SCHEMA_IDENTITY);
    if (!matches_digest(bytes + 256, FeatureContractIdentity))
        return failure(ProductiveLoadError::FEATURE_CONTRACT_IDENTITY);
    if (!matches_digest(bytes + 288, ArchitectureIdentity))
        return failure(ProductiveLoadError::ARCHITECTURE_IDENTITY);
    if (!matches_digest(bytes + 320, QuantizationIdentity))
        return failure(ProductiveLoadError::QUANTIZATION_IDENTITY);
    if (range_all_zero(bytes + 352, bytes + 384))
        return failure(ProductiveLoadError::DATASET_IDENTITY_ZERO);
    if (!matches_digest(bytes + 352, expected.datasetManifest))
        return failure(ProductiveLoadError::DATASET_IDENTITY);
    if (range_all_zero(bytes + 384, bytes + 416))
        return failure(ProductiveLoadError::TRAINING_CONFIG_IDENTITY_ZERO);
    if (!matches_digest(bytes + 384, expected.trainingConfig))
        return failure(ProductiveLoadError::TRAINING_CONFIG_IDENTITY);
    if (!range_all_zero(bytes + 448, bytes + 508))
        return failure(ProductiveLoadError::RESERVED_BYTES);
    if (get_le<std::uint32_t>(bytes + 508) != crc32c(bytes, 508))
        return failure(ProductiveLoadError::HEADER_CRC32C);
    Sha256 payloadHash;
    payloadHash.update(bytes + ProductiveHeaderBytes, ProductivePayloadBytes);
    if (!matches_digest(bytes + 416, payloadHash.final()))
        return failure(ProductiveLoadError::PAYLOAD_SHA256);

    std::unique_ptr<ProductiveNetworkV1> network(new (std::nothrow) ProductiveNetworkV1);
    if (!network)
        return failure(ProductiveLoadError::ALLOCATION);
    for (std::size_t index = 0; index < network->transformerWeights_.size(); ++index)
        network->transformerWeights_[index] = get_i16_le(bytes + 512 + 2 * index);
    for (std::size_t index = 0; index < network->transformerBiases_.size(); ++index)
        network->transformerBiases_[index] = get_i32_le(bytes + 924160 + 4 * index);
    for (std::size_t index = 0; index < network->dense0Weights_.size(); ++index)
        network->dense0Weights_[index] = get_i8(bytes[926208 + index]);
    for (std::size_t index = 0; index < network->dense0Biases_.size(); ++index)
        network->dense0Biases_[index] = get_i32_le(bytes + 958976 + 4 * index);
    for (std::size_t index = 0; index < network->dense1Weights_.size(); ++index)
        network->dense1Weights_[index] = get_i8(bytes[959104 + index]);
    for (std::size_t index = 0; index < network->dense1Biases_.size(); ++index)
        network->dense1Biases_[index] = get_i32_le(bytes + 960128 + 4 * index);
    for (std::size_t index = 0; index < network->outputWeights_.size(); ++index)
        network->outputWeights_[index] = get_i16_le(bytes + 960256 + 2 * index);
    network->outputBias_ = get_i32_le(bytes + 960320);

    const ProductiveLoadError interval = network->validate_static_intervals();
    if (interval != ProductiveLoadError::NONE)
        return failure(interval);

    network->provenance_ = expected;
    network->ready_      = true;
    ProductiveLoadResultV1 result;
    result.error   = ProductiveLoadError::NONE;
    result.network = std::move(network);
    return result;
}

ProductiveEvaluationResultV1 ProductiveNetworkV1::evaluate_from_transformers(
  const std::array<std::int32_t, ProductiveTransformerLanes>& stm,
  const std::array<std::int32_t, ProductiveTransformerLanes>& opponent) const noexcept {
    ProductiveEvaluationResultV1 result;
    ProductiveTraceV1&           trace = result.trace;
    trace.transformerStm               = stm;
    trace.transformerOpponent          = opponent;
    for (std::size_t lane = 0; lane < ProductiveTransformerLanes; ++lane)
    {
        trace.transformerStmActivation[lane]      = transformer_activation(stm[lane]);
        trace.transformerOpponentActivation[lane] = transformer_activation(opponent[lane]);
    }

    for (std::size_t output = 0; output < ProductiveDense0Outputs; ++output)
    {
        std::int64_t value = dense0Biases_[output];
        for (std::size_t input = 0; input < ProductiveDense0Inputs; ++input)
        {
            const Byte activation =
              input < ProductiveTransformerLanes
                ? trace.transformerStmActivation[input]
                : trace.transformerOpponentActivation[input - ProductiveTransformerLanes];
            value +=
              std::int64_t(activation) * dense0Weights_[output * ProductiveDense0Inputs + input];
        }
        if (!fits_int32(value))
            return evaluation_failure(ProductiveEvaluateError::DENSE0_RUNTIME_RANGE);
        trace.dense0[output]           = static_cast<std::int32_t>(value);
        trace.dense0Activation[output] = dense_activation(trace.dense0[output]);
    }
    for (std::size_t output = 0; output < ProductiveDense1Outputs; ++output)
    {
        std::int64_t value = dense1Biases_[output];
        for (std::size_t input = 0; input < ProductiveDense1Inputs; ++input)
            value += std::int64_t(trace.dense0Activation[input])
                   * dense1Weights_[output * ProductiveDense1Inputs + input];
        if (!fits_int32(value))
            return evaluation_failure(ProductiveEvaluateError::DENSE1_RUNTIME_RANGE);
        trace.dense1[output]           = static_cast<std::int32_t>(value);
        trace.dense1Activation[output] = dense_activation(trace.dense1[output]);
    }
    std::int64_t output = outputBias_;
    for (std::size_t input = 0; input < ProductiveOutputInputs; ++input)
        output += std::int64_t(trace.dense1Activation[input]) * outputWeights_[input];
    if (!fits_int32(output))
        return evaluation_failure(ProductiveEvaluateError::OUTPUT_RUNTIME_RANGE);
    trace.outputRaw               = static_cast<std::int32_t>(output);
    const std::int64_t centipawns = output * OutputValueScale / OutputDivisor;
    if (!fits_int32(centipawns))
        return evaluation_failure(ProductiveEvaluateError::OUTPUT_RUNTIME_RANGE);
    trace.outputCentipawns = static_cast<std::int32_t>(centipawns);
    result.error           = ProductiveEvaluateError::NONE;
    return result;
}

ProductiveEvaluationResultV1
ProductiveNetworkV1::evaluate(const ScalarFeatureInventoryV1::Result& features,
                              Color                                   sideToMove) const noexcept {
    if (!ready_)
        return evaluation_failure(ProductiveEvaluateError::NETWORK_NOT_READY);
    if (sideToMove != WHITE && sideToMove != BLACK)
        return evaluation_failure(ProductiveEvaluateError::SIDE_TO_MOVE);
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        const ProductiveEvaluateError error = validate_feature_side(features, side);
        if (error != ProductiveEvaluateError::NONE)
            return evaluation_failure(error);
    }

    const unsigned stmSide      = static_cast<unsigned>(sideToMove);
    const unsigned opponentSide = stmSide ^ 1U;
    auto           transform    = [&](unsigned side, auto& raw) -> bool {
        for (std::size_t lane = 0; lane < ProductiveTransformerLanes; ++lane)
        {
            std::int64_t value = transformerBiases_[lane];
            for (std::size_t index = 0; index < features.size[side]; ++index)
                value +=
                  transformerWeights_[features.active[side][index] * ProductiveTransformerLanes
                                      + lane];
            if (!fits_int32(value))
                return false;
            raw[lane] = static_cast<std::int32_t>(value);
        }
        return true;
    };
    std::array<std::int32_t, ProductiveTransformerLanes> stm{};
    std::array<std::int32_t, ProductiveTransformerLanes> opponent{};
    if (!transform(stmSide, stm) || !transform(opponentSide, opponent))
        return evaluation_failure(ProductiveEvaluateError::TRANSFORMER_RUNTIME_RANGE);
    return evaluate_from_transformers(stm, opponent);
}

ProductiveEvaluationResultV1
ProductiveNetworkV1::evaluate_simd(const ScalarFeatureInventoryV1::Result& features,
                                   Color sideToMove) const noexcept {
    if (!ready_)
        return evaluation_failure(ProductiveEvaluateError::NETWORK_NOT_READY);
    if (sideToMove != WHITE && sideToMove != BLACK)
        return evaluation_failure(ProductiveEvaluateError::SIDE_TO_MOVE);
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        const ProductiveEvaluateError error = validate_feature_side(features, side);
        if (error != ProductiveEvaluateError::NONE)
            return evaluation_failure(error);
    }

#if defined(USE_SSE2)
    static_assert(ProductiveTransformerLanes % 8 == 0);
    auto transform = [&](unsigned side, auto& raw) {
        raw                = transformerBiases_;
        const __m128i zero = _mm_setzero_si128();
        for (std::size_t index = 0; index < features.size[side]; ++index)
        {
            const std::int16_t* row = transformerWeights_.data()
                                    + features.active[side][index] * ProductiveTransformerLanes;
            for (std::size_t lane = 0; lane < ProductiveTransformerLanes; lane += 8)
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
    const unsigned stmSide      = static_cast<unsigned>(sideToMove);
    const unsigned opponentSide = stmSide ^ 1U;
    std::array<std::int32_t, ProductiveTransformerLanes> stm{};
    std::array<std::int32_t, ProductiveTransformerLanes> opponent{};
    transform(stmSide, stm);
    transform(opponentSide, opponent);
    return evaluate_from_transformers(stm, opponent);
#else
    return evaluation_failure(ProductiveEvaluateError::SIMD_UNAVAILABLE);
#endif
}

ProductiveAccumulatorResultV1
ProductiveAccumulatorV1::refresh(const ProductiveNetworkV1&              network,
                                 const ScalarFeatureInventoryV1::Result& features) noexcept {
    ProductiveAccumulatorResultV1 result;
    if (!network.ready())
    {
        result.error = ProductiveAccumulatorError::NETWORK_NOT_READY;
        return result;
    }
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        const ProductiveEvaluateError error = validate_feature_side(features, side);
        if (error != ProductiveEvaluateError::NONE)
        {
            result.error = accumulator_error(error);
            return result;
        }
    }

    ProductiveAccumulatorV1 candidate;
    candidate.network_ = &network;
    for (unsigned side = 0; side < COLOR_NB; ++side)
        for (std::size_t lane = 0; lane < ProductiveTransformerLanes; ++lane)
        {
            std::int64_t value = network.transformerBiases_[lane];
            for (std::size_t index = 0; index < features.size[side]; ++index)
                value +=
                  network
                    .transformerWeights_[features.active[side][index] * ProductiveTransformerLanes
                                         + lane];
            if (!fits_int32(value))
            {
                result.error = ProductiveAccumulatorError::TRANSFORMER_RUNTIME_RANGE;
                return result;
            }
            candidate.transformers_[side][lane] = static_cast<std::int32_t>(value);
        }
    candidate.membership_ = make_productive_membership(features);
    candidate.sizes_      = features.size;
    candidate.ready_      = true;
    *this                 = candidate;
    result.error          = ProductiveAccumulatorError::NONE;
    return result;
}

bool ProductiveAccumulatorV1::matches(
  const ScalarFeatureInventoryV1::Result& features) const noexcept {
    if (!ready_ || !features.ok())
        return false;
    for (unsigned side = 0; side < COLOR_NB; ++side)
        if (validate_feature_side(features, side) != ProductiveEvaluateError::NONE
            || sizes_[side] != features.size[side])
            return false;
    return membership_ == make_productive_membership(features);
}

ProductiveAccumulatorResultV1
ProductiveAccumulatorV1::update(const ProductiveNetworkV1&              network,
                                const ScalarFeatureInventoryV1::Result& source,
                                const ScalarFeatureInventoryV1::Result& target) noexcept {
    ProductiveAccumulatorResultV1 result;
    if (!network.ready())
    {
        result.error = ProductiveAccumulatorError::NETWORK_NOT_READY;
        return result;
    }
    if (!ready_)
    {
        result.error = ProductiveAccumulatorError::SOURCE_NOT_READY;
        return result;
    }
    if (network_ != &network)
    {
        result.error = ProductiveAccumulatorError::NETWORK_MISMATCH;
        return result;
    }
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        if (const ProductiveEvaluateError error = validate_feature_side(source, side);
            error != ProductiveEvaluateError::NONE)
        {
            result.error = accumulator_error(error);
            return result;
        }
        if (const ProductiveEvaluateError error = validate_feature_side(target, side);
            error != ProductiveEvaluateError::NONE)
        {
            result.error = accumulator_error(error);
            return result;
        }
    }
    if (!matches(source))
    {
        result.error = ProductiveAccumulatorError::SOURCE_INVENTORY_MISMATCH;
        return result;
    }

    const ProductiveFeatureMembership targetMembership = make_productive_membership(target);
    ProductiveAccumulatorV1           candidate        = *this;
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        std::array<std::int64_t, ProductiveTransformerLanes> widened{};
        for (std::size_t lane = 0; lane < ProductiveTransformerLanes; ++lane)
            widened[lane] = transformers_[side][lane];
        for (std::size_t row = 0; row < ScalarFeatureInventoryV1::Dimensions; ++row)
        {
            const int direction = targetMembership[side][row] == membership_[side][row] ? 0
                                : targetMembership[side][row]                           ? 1
                                                                                        : -1;
            if (direction == 0)
                continue;
            for (std::size_t lane = 0; lane < ProductiveTransformerLanes; ++lane)
                widened[lane] +=
                  direction
                  * std::int64_t(
                    network.transformerWeights_[row * ProductiveTransformerLanes + lane]);
        }
        for (std::size_t lane = 0; lane < ProductiveTransformerLanes; ++lane)
        {
            if (!fits_int32(widened[lane]))
            {
                result.error = ProductiveAccumulatorError::TRANSFORMER_RUNTIME_RANGE;
                return result;
            }
            candidate.transformers_[side][lane] = static_cast<std::int32_t>(widened[lane]);
        }
    }
    candidate.membership_ = targetMembership;
    candidate.sizes_      = target.size;
    *this                 = candidate;
    result.error          = ProductiveAccumulatorError::NONE;
    return result;
}

ProductiveEvaluationResultV1
ProductiveAccumulatorV1::evaluate(const ScalarFeatureInventoryV1::Result& features,
                                  Color sideToMove) const noexcept {
    if (!ready_ || network_ == nullptr || !network_->ready())
        return evaluation_failure(ProductiveEvaluateError::ACCUMULATOR_NOT_READY);
    if (sideToMove != WHITE && sideToMove != BLACK)
        return evaluation_failure(ProductiveEvaluateError::SIDE_TO_MOVE);
    for (unsigned side = 0; side < COLOR_NB; ++side)
    {
        const ProductiveEvaluateError error = validate_feature_side(features, side);
        if (error != ProductiveEvaluateError::NONE)
            return evaluation_failure(error);
    }
    if (!matches(features))
        return evaluation_failure(ProductiveEvaluateError::ACCUMULATOR_INVENTORY_MISMATCH);
    const unsigned stmSide      = static_cast<unsigned>(sideToMove);
    const unsigned opponentSide = stmSide ^ 1U;
    return network_->evaluate_from_transformers(transformers_[stmSide],
                                                transformers_[opponentSide]);
}

ProductiveSimdBackend productive_simd_backend() noexcept {
#if defined(USE_SSE2)
    return ProductiveSimdBackend::SSE2_X8_INT16_TO_INT32;
#else
    return ProductiveSimdBackend::UNAVAILABLE;
#endif
}

std::string_view productive_load_error_name(ProductiveLoadError error) noexcept {
    switch (error)
    {
    case ProductiveLoadError::NONE :
        return "NONE";
    case ProductiveLoadError::WRONG_SIZE :
        return "WRONG_SIZE";
    case ProductiveLoadError::EXPECTED_PROVENANCE :
        return "EXPECTED_PROVENANCE";
    case ProductiveLoadError::MAGIC :
        return "MAGIC";
    case ProductiveLoadError::BYTE_ORDER :
        return "BYTE_ORDER";
    case ProductiveLoadError::HEADER_SIZE :
        return "HEADER_SIZE";
    case ProductiveLoadError::VERSION :
        return "VERSION";
    case ProductiveLoadError::FLAGS :
        return "FLAGS";
    case ProductiveLoadError::FILE_SIZE :
        return "FILE_SIZE";
    case ProductiveLoadError::FEATURE_DIMENSIONS :
        return "FEATURE_DIMENSIONS";
    case ProductiveLoadError::MAXIMUM_ACTIVE :
        return "MAXIMUM_ACTIVE";
    case ProductiveLoadError::TRANSFORMER_LANES :
        return "TRANSFORMER_LANES";
    case ProductiveLoadError::PERSPECTIVE_COUNT :
        return "PERSPECTIVE_COUNT";
    case ProductiveLoadError::DENSE0_INPUTS :
        return "DENSE0_INPUTS";
    case ProductiveLoadError::DENSE0_OUTPUTS :
        return "DENSE0_OUTPUTS";
    case ProductiveLoadError::DENSE1_INPUTS :
        return "DENSE1_INPUTS";
    case ProductiveLoadError::DENSE1_OUTPUTS :
        return "DENSE1_OUTPUTS";
    case ProductiveLoadError::OUTPUT_INPUTS :
        return "OUTPUT_INPUTS";
    case ProductiveLoadError::OUTPUT_OUTPUTS :
        return "OUTPUT_OUTPUTS";
    case ProductiveLoadError::TRANSFORMER_WEIGHT_TYPE :
        return "TRANSFORMER_WEIGHT_TYPE";
    case ProductiveLoadError::TRANSFORMER_BIAS_TYPE :
        return "TRANSFORMER_BIAS_TYPE";
    case ProductiveLoadError::DENSE_WEIGHT_TYPE :
        return "DENSE_WEIGHT_TYPE";
    case ProductiveLoadError::DENSE_BIAS_TYPE :
        return "DENSE_BIAS_TYPE";
    case ProductiveLoadError::OUTPUT_WEIGHT_TYPE :
        return "OUTPUT_WEIGHT_TYPE";
    case ProductiveLoadError::OUTPUT_BIAS_TYPE :
        return "OUTPUT_BIAS_TYPE";
    case ProductiveLoadError::ACCUMULATOR_TYPE :
        return "ACCUMULATOR_TYPE";
    case ProductiveLoadError::ACTIVATION_TYPE :
        return "ACTIVATION_TYPE";
    case ProductiveLoadError::TRANSFORMER_SCALE :
        return "TRANSFORMER_SCALE";
    case ProductiveLoadError::DENSE_WEIGHT_SCALE :
        return "DENSE_WEIGHT_SCALE";
    case ProductiveLoadError::OUTPUT_WEIGHT_SCALE :
        return "OUTPUT_WEIGHT_SCALE";
    case ProductiveLoadError::OUTPUT_VALUE_SCALE :
        return "OUTPUT_VALUE_SCALE";
    case ProductiveLoadError::DENSE_ACTIVATION_DIVISOR :
        return "DENSE_ACTIVATION_DIVISOR";
    case ProductiveLoadError::OUTPUT_DIVISOR :
        return "OUTPUT_DIVISOR";
    case ProductiveLoadError::TENSOR_DIRECTORY :
        return "TENSOR_DIRECTORY";
    case ProductiveLoadError::INPUT_SEMANTICS :
        return "INPUT_SEMANTICS";
    case ProductiveLoadError::PERSPECTIVE_ORDER :
        return "PERSPECTIVE_ORDER";
    case ProductiveLoadError::ACTIVATION_SEMANTICS :
        return "ACTIVATION_SEMANTICS";
    case ProductiveLoadError::OUTPUT_UNITS :
        return "OUTPUT_UNITS";
    case ProductiveLoadError::RULE_PROFILE_IDENTITY :
        return "RULE_PROFILE_IDENTITY";
    case ProductiveLoadError::PHYSICAL_SCHEMA_IDENTITY :
        return "PHYSICAL_SCHEMA_IDENTITY";
    case ProductiveLoadError::FEATURE_CONTRACT_IDENTITY :
        return "FEATURE_CONTRACT_IDENTITY";
    case ProductiveLoadError::ARCHITECTURE_IDENTITY :
        return "ARCHITECTURE_IDENTITY";
    case ProductiveLoadError::QUANTIZATION_IDENTITY :
        return "QUANTIZATION_IDENTITY";
    case ProductiveLoadError::DATASET_IDENTITY_ZERO :
        return "DATASET_IDENTITY_ZERO";
    case ProductiveLoadError::DATASET_IDENTITY :
        return "DATASET_IDENTITY";
    case ProductiveLoadError::TRAINING_CONFIG_IDENTITY_ZERO :
        return "TRAINING_CONFIG_IDENTITY_ZERO";
    case ProductiveLoadError::TRAINING_CONFIG_IDENTITY :
        return "TRAINING_CONFIG_IDENTITY";
    case ProductiveLoadError::RESERVED_BYTES :
        return "RESERVED_BYTES";
    case ProductiveLoadError::HEADER_CRC32C :
        return "HEADER_CRC32C";
    case ProductiveLoadError::PAYLOAD_SHA256 :
        return "PAYLOAD_SHA256";
    case ProductiveLoadError::ALLOCATION :
        return "ALLOCATION";
    case ProductiveLoadError::TRANSFORMER_INTERVAL :
        return "TRANSFORMER_INTERVAL";
    case ProductiveLoadError::DENSE0_INTERVAL :
        return "DENSE0_INTERVAL";
    case ProductiveLoadError::DENSE1_INTERVAL :
        return "DENSE1_INTERVAL";
    case ProductiveLoadError::OUTPUT_INTERVAL :
        return "OUTPUT_INTERVAL";
    }
    return "UNKNOWN";
}

std::string_view productive_evaluate_error_name(ProductiveEvaluateError error) noexcept {
    switch (error)
    {
    case ProductiveEvaluateError::NONE :
        return "NONE";
    case ProductiveEvaluateError::NETWORK_NOT_READY :
        return "NETWORK_NOT_READY";
    case ProductiveEvaluateError::ACCUMULATOR_NOT_READY :
        return "ACCUMULATOR_NOT_READY";
    case ProductiveEvaluateError::ACCUMULATOR_INVENTORY_MISMATCH :
        return "ACCUMULATOR_INVENTORY_MISMATCH";
    case ProductiveEvaluateError::FEATURE_STATUS :
        return "FEATURE_STATUS";
    case ProductiveEvaluateError::SIDE_TO_MOVE :
        return "SIDE_TO_MOVE";
    case ProductiveEvaluateError::ACTIVE_OVERFLOW :
        return "ACTIVE_OVERFLOW";
    case ProductiveEvaluateError::FEATURE_INDEX :
        return "FEATURE_INDEX";
    case ProductiveEvaluateError::DUPLICATE_FEATURE :
        return "DUPLICATE_FEATURE";
    case ProductiveEvaluateError::TRANSFORMER_RUNTIME_RANGE :
        return "TRANSFORMER_RUNTIME_RANGE";
    case ProductiveEvaluateError::DENSE0_RUNTIME_RANGE :
        return "DENSE0_RUNTIME_RANGE";
    case ProductiveEvaluateError::DENSE1_RUNTIME_RANGE :
        return "DENSE1_RUNTIME_RANGE";
    case ProductiveEvaluateError::OUTPUT_RUNTIME_RANGE :
        return "OUTPUT_RUNTIME_RANGE";
    case ProductiveEvaluateError::SIMD_UNAVAILABLE :
        return "SIMD_UNAVAILABLE";
    }
    return "UNKNOWN";
}

std::string_view productive_accumulator_error_name(ProductiveAccumulatorError error) noexcept {
    switch (error)
    {
    case ProductiveAccumulatorError::NONE :
        return "NONE";
    case ProductiveAccumulatorError::NETWORK_NOT_READY :
        return "NETWORK_NOT_READY";
    case ProductiveAccumulatorError::NETWORK_MISMATCH :
        return "NETWORK_MISMATCH";
    case ProductiveAccumulatorError::SOURCE_NOT_READY :
        return "SOURCE_NOT_READY";
    case ProductiveAccumulatorError::SOURCE_INVENTORY_MISMATCH :
        return "SOURCE_INVENTORY_MISMATCH";
    case ProductiveAccumulatorError::FEATURE_STATUS :
        return "FEATURE_STATUS";
    case ProductiveAccumulatorError::ACTIVE_OVERFLOW :
        return "ACTIVE_OVERFLOW";
    case ProductiveAccumulatorError::FEATURE_INDEX :
        return "FEATURE_INDEX";
    case ProductiveAccumulatorError::DUPLICATE_FEATURE :
        return "DUPLICATE_FEATURE";
    case ProductiveAccumulatorError::TRANSFORMER_RUNTIME_RANGE :
        return "TRANSFORMER_RUNTIME_RANGE";
    }
    return "UNKNOWN";
}

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2
