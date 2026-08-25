/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_PROBE_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_PROBE_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

#include "crazyhouse_v2_features.h"

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {

inline constexpr std::size_t ScalarProbeHeaderBytes = 256;
inline constexpr std::size_t ScalarProbeOutputLanes = 17;
inline constexpr std::size_t ScalarProbeWeightElements =
  ScalarFeatureInventoryV1::Dimensions * ScalarProbeOutputLanes;
inline constexpr std::size_t ScalarProbeWeightBytes = ScalarProbeWeightElements * 2;
inline constexpr std::size_t ScalarProbeBiasBytes   = ScalarProbeOutputLanes * 4;
inline constexpr std::size_t ScalarProbeFileBytes =
  ScalarProbeHeaderBytes + ScalarProbeWeightBytes + ScalarProbeBiasBytes;

static_assert(ScalarProbeWeightElements == 15334);
static_assert(ScalarProbeWeightBytes == 30668);
static_assert(ScalarProbeBiasBytes == 68);
static_assert(ScalarProbeFileBytes == 30992);

enum class ScalarProbeLoadError {
    NONE,
    WRONG_SIZE,
    MAGIC,
    BYTE_ORDER,
    HEADER_SIZE,
    VERSION,
    FLAGS,
    FILE_SIZE,
    FEATURE_DIMENSIONS,
    MAXIMUM_ACTIVE,
    OUTPUT_LANES,
    INPUT_SEMANTICS,
    WEIGHT_TYPE,
    BIAS_TYPE,
    ACCUMULATOR_TYPE,
    WEIGHTS_OFFSET,
    WEIGHTS_BYTES,
    BIASES_OFFSET,
    BIASES_BYTES,
    PAYLOAD_BYTES,
    RESERVED_BYTES,
    RULE_PROFILE_IDENTITY,
    PHYSICAL_SCHEMA_IDENTITY,
    FEATURE_CONTRACT_IDENTITY,
    ARCHITECTURE_IDENTITY,
    HEADER_CRC32C,
    PAYLOAD_SHA256,
};

enum class ScalarProbeEvaluateError {
    NONE,
    NETWORK_NOT_READY,
    FEATURE_STATUS,
    PERSPECTIVE,
    ACTIVE_OVERFLOW,
    FEATURE_INDEX,
    DUPLICATE_FEATURE,
    ACCUMULATOR_OVERFLOW,
    SIMD_UNAVAILABLE,
};

enum class ScalarProbeSimdBackend {
    UNAVAILABLE,
    SSE2_X16_SCALAR_TAIL1,
};

struct ScalarProbeEvaluationResult {
    ScalarProbeEvaluateError error = ScalarProbeEvaluateError::NETWORK_NOT_READY;
    std::array<std::int32_t, ScalarProbeOutputLanes> lanes{};

    constexpr bool ok() const noexcept { return error == ScalarProbeEvaluateError::NONE; }
};

struct ScalarProbeLoadResult;
class ScalarProbeAccumulatorV1;

class ScalarProbeNetworkV1 {
   public:
    constexpr bool ready() const noexcept { return ready_; }

    ScalarProbeEvaluationResult evaluate(const ScalarFeatureInventoryV1::Result& features,
                                         Color perspective) const noexcept;
    ScalarProbeEvaluationResult evaluate_simd(const ScalarFeatureInventoryV1::Result& features,
                                              Color perspective) const noexcept;

   private:
    friend struct ScalarProbeLoadResult;
    friend class ScalarProbeAccumulatorV1;
    friend ScalarProbeLoadResult load_scalar_probe_v1(const Byte*, std::size_t) noexcept;

    bool                                                ready_ = false;
    std::array<std::int16_t, ScalarProbeWeightElements> weights_{};
    std::array<std::int32_t, ScalarProbeOutputLanes>    biases_{};
};

enum class ScalarProbeAccumulatorError {
    NONE,
    NETWORK_NOT_READY,
    NETWORK_MISMATCH,
    SOURCE_NOT_READY,
    SOURCE_INVENTORY_MISMATCH,
    FEATURE_STATUS,
    ACTIVE_OVERFLOW,
    FEATURE_INDEX,
    DUPLICATE_FEATURE,
    ACCUMULATOR_OVERFLOW,
    PERSPECTIVE,
    SIMD_UNAVAILABLE,
};

struct ScalarProbeAccumulatorResult {
    ScalarProbeAccumulatorError error = ScalarProbeAccumulatorError::SOURCE_NOT_READY;

    constexpr bool ok() const noexcept { return error == ScalarProbeAccumulatorError::NONE; }
};

class ScalarProbeAccumulatorV1 {
   public:
    constexpr bool ready() const noexcept { return ready_; }

    ScalarProbeAccumulatorResult refresh(const ScalarProbeNetworkV1&             network,
                                         const ScalarFeatureInventoryV1::Result& features) noexcept;
    ScalarProbeAccumulatorResult update(const ScalarProbeNetworkV1&             network,
                                        const ScalarFeatureInventoryV1::Result& source,
                                        const ScalarFeatureInventoryV1::Result& target) noexcept;
    ScalarProbeEvaluationResult  evaluate(Color perspective) const noexcept;
    bool matches(const ScalarFeatureInventoryV1::Result& features) const noexcept;

   private:
    bool                                                                         ready_   = false;
    const ScalarProbeNetworkV1*                                                  network_ = nullptr;
    std::array<std::array<bool, ScalarFeatureInventoryV1::Dimensions>, COLOR_NB> membership_{};
    std::array<std::size_t, COLOR_NB>                                            sizes_{};
    std::array<std::array<std::int32_t, ScalarProbeOutputLanes>, COLOR_NB>       lanes_{};
};

struct ScalarProbeLoadResult {
    ScalarProbeLoadError error = ScalarProbeLoadError::WRONG_SIZE;
    ScalarProbeNetworkV1 network{};

    constexpr bool ok() const noexcept {
        return error == ScalarProbeLoadError::NONE && network.ready();
    }
};

ScalarProbeLoadResult  load_scalar_probe_v1(const Byte* bytes, std::size_t size) noexcept;
ScalarProbeSimdBackend scalar_probe_simd_backend() noexcept;
std::string_view       scalar_probe_load_error_name(ScalarProbeLoadError error) noexcept;
std::string_view       scalar_probe_evaluate_error_name(ScalarProbeEvaluateError error) noexcept;
std::string_view scalar_probe_accumulator_error_name(ScalarProbeAccumulatorError error) noexcept;

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2

#endif  // NNUE_CRAZYHOUSE_V2_PROBE_H_INCLUDED
