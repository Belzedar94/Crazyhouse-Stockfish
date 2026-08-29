/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_LARGE_NETWORK_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_LARGE_NETWORK_H_INCLUDED

#include <array>
#include <bitset>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string_view>

#include "crazyhouse_v2_features.h"
#include "crazyhouse_v2_large_transform.h"

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {

inline constexpr std::size_t LargeNetworkHeaderBytes  = 1024;
inline constexpr std::size_t LargeNetworkPayloadBytes = 126405664;
inline constexpr std::size_t LargeNetworkFileBytes    = 126406688;
inline constexpr std::size_t LargeLayerStacks         = 8;
inline constexpr std::size_t LargeFc0Inputs           = 1024;
inline constexpr std::size_t LargeFc0Outputs          = 32;
inline constexpr std::size_t LargeFc1Inputs           = 64;
inline constexpr std::size_t LargeFc1Outputs          = 32;
inline constexpr std::size_t LargeFc2Inputs           = 128;
inline constexpr std::size_t LargeFc2Outputs          = 1;

inline constexpr std::size_t LargeKWeightElements =
  LargeFeatureInventoryV1::KDimensions * LargeKTransformerLanes;
inline constexpr std::size_t LargeGWeightElements =
  LargeFeatureInventoryV1::GDimensions * LargeGTransformerLanes;
inline constexpr std::size_t LargeFc0WeightElements =
  LargeLayerStacks * LargeFc0Outputs * LargeFc0Inputs;
inline constexpr std::size_t LargeFc1WeightElements =
  LargeLayerStacks * LargeFc1Outputs * LargeFc1Inputs;
inline constexpr std::size_t LargeFc2WeightElements = LargeLayerStacks * LargeFc2Inputs;

struct LargeExpectedProvenanceV1 {
    Digest datasetManifest{};
    Digest splitManifest{};
    Digest trainingConfig{};
    Digest trainerCode{};
    Digest trainingRuntime{};
    Digest resumeLineage{};
};

enum class LargeNetworkLoadError {
    NONE,
    WRONG_SIZE,
    EXPECTED_PROVENANCE,
    MAGIC,
    BYTE_ORDER_MARKER,
    HEADER_SIZE,
    VERSION,
    FLAGS,
    FILE_SIZE,
    PAYLOAD_SIZE,
    TENSOR_COUNT,
    LAYER_STACKS,
    K_DIMENSIONS,
    G_DIMENSIONS,
    MAXIMUM_ACTIVE,
    K_LANES,
    G_LANES,
    PERSPECTIVE_COUNT,
    PERSPECTIVE_OUTPUTS,
    DENSE_INPUTS,
    FC0_OUTPUTS,
    FC1_INPUTS,
    FC1_OUTPUTS,
    FC2_INPUTS,
    FC2_OUTPUTS,
    BUCKET_DIVISOR,
    BUCKET_MAXIMUM,
    TENSOR_TYPES,
    TRANSFORM_CONSTANTS,
    ACTIVATION_CONSTANTS,
    OUTPUT_CONSTANTS,
    INPUT_SEMANTICS,
    PERSPECTIVE_ORDER,
    DIRECTORY_LAYOUT,
    RULE_PROFILE_IDENTITY,
    PHYSICAL_SCHEMA_IDENTITY,
    FEATURE_CONTRACT_IDENTITY,
    ARCHITECTURE_IDENTITY,
    QUANTIZATION_IDENTITY,
    DATASET_IDENTITY_ZERO,
    DATASET_IDENTITY,
    SPLIT_IDENTITY_ZERO,
    SPLIT_IDENTITY,
    TRAINING_CONFIG_IDENTITY_ZERO,
    TRAINING_CONFIG_IDENTITY,
    TRAINER_CODE_IDENTITY_ZERO,
    TRAINER_CODE_IDENTITY,
    TRAINING_RUNTIME_IDENTITY_ZERO,
    TRAINING_RUNTIME_IDENTITY,
    RESUME_LINEAGE_IDENTITY_ZERO,
    RESUME_LINEAGE_IDENTITY,
    RESERVED_BYTES,
    TENSOR_DIRECTORY,
    HEADER_CRC32C,
    PAYLOAD_SHA256,
    ALLOCATION,
    FC0_INTERVAL,
    FC1_INTERVAL,
    FC2_INTERVAL,
};

enum class LargeNetworkEvaluateError {
    NONE,
    NETWORK_NOT_READY,
    ACCUMULATOR_NOT_READY,
    ACCUMULATOR_INVENTORY_MISMATCH,
    FEATURE_STATUS,
    SIDE_TO_MOVE,
    ACTIVE_OVERFLOW,
    FEATURE_INDEX,
    DUPLICATE_FEATURE,
    POCKET_UNITS,
    POCKET_ROUTING_MISMATCH,
    TRANSFORMER_RUNTIME_RANGE,
    FC0_RUNTIME_RANGE,
    FC1_RUNTIME_RANGE,
    FC2_RUNTIME_RANGE,
    SIMD_UNAVAILABLE,
};

enum class LargeNetworkSimdBackend {
    UNAVAILABLE,
    SSE2_X8_INT16_TO_INT32,
};

struct LargeNetworkTraceV1 {
    std::size_t                                  bucket{};
    std::array<LargeKAccumulator, COLOR_NB>      kAccumulator{};
    std::array<LargeGAccumulator, COLOR_NB>      gAccumulator{};
    std::array<LargePerspectiveOutput, COLOR_NB> perspectiveOutput{};
    std::array<Byte, LargeDenseInputBytes>       denseInput{};
    std::array<std::int32_t, LargeFc0Outputs>    fc0{};
    std::array<Byte, LargeFc0Outputs>            fc0Squared{};
    std::array<Byte, LargeFc0Outputs>            fc0Clipped{};
    std::array<std::int32_t, LargeFc1Outputs>    fc1{};
    std::array<Byte, LargeFc1Outputs>            fc1Squared{};
    std::array<Byte, LargeFc1Outputs>            fc1Clipped{};
    std::int32_t                                 fc2{};
    std::int32_t                                 fwdRaw{};
    std::int32_t                                 outputValue{};
};

struct LargeNetworkEvaluationResultV1 {
    LargeNetworkEvaluateError error = LargeNetworkEvaluateError::NETWORK_NOT_READY;
    LargeNetworkTraceV1       trace{};

    constexpr bool ok() const noexcept { return error == LargeNetworkEvaluateError::NONE; }
};

struct LargeNetworkLoadResultV1;
class LargeNetworkAccumulatorV1;

class LargeNetworkV1 {
   public:
    constexpr bool                   ready() const noexcept { return ready_; }
    const LargeExpectedProvenanceV1& provenance() const noexcept { return provenance_; }

    LargeNetworkEvaluationResultV1 evaluate(const LargeFeatureInventoryV1::Result& features,
                                            Color sideToMove) const noexcept;
    LargeNetworkEvaluationResultV1 evaluate_simd(const LargeFeatureInventoryV1::Result& features,
                                                 Color sideToMove) const noexcept;

   private:
    friend class LargeNetworkAccumulatorV1;
    friend LargeNetworkLoadResultV1
    load_large_network_v1(const Byte*, std::size_t, const LargeExpectedProvenanceV1&) noexcept;

    LargeNetworkLoadError          validate_dense_intervals() const noexcept;
    LargeNetworkEvaluationResultV1 evaluate_from_accumulators(
      const LargeFeatureInventoryV1::Result&         features,
      Color                                          sideToMove,
      const std::array<LargeKAccumulator, COLOR_NB>& kAccumulator,
      const std::array<LargeGAccumulator, COLOR_NB>& gAccumulator) const noexcept;

    bool                                                         ready_ = false;
    LargeExpectedProvenanceV1                                    provenance_{};
    std::unique_ptr<std::int16_t[]>                              kWeights_{};
    std::array<std::int16_t, LargeKTransformerLanes>             kBiases_{};
    std::unique_ptr<std::int16_t[]>                              gWeights_{};
    std::array<std::int16_t, LargeGTransformerLanes>             gBiases_{};
    std::array<std::int32_t, LargeLayerStacks * LargeFc0Outputs> fc0Biases_{};
    std::array<std::int8_t, LargeFc0WeightElements>              fc0Weights_{};
    std::array<std::int32_t, LargeLayerStacks * LargeFc1Outputs> fc1Biases_{};
    std::array<std::int8_t, LargeFc1WeightElements>              fc1Weights_{};
    std::array<std::int32_t, LargeLayerStacks>                   fc2Biases_{};
    std::array<std::int8_t, LargeFc2WeightElements>              fc2Weights_{};
};

enum class LargeNetworkAccumulatorError {
    NONE,
    NETWORK_NOT_READY,
    NETWORK_MISMATCH,
    SOURCE_NOT_READY,
    SOURCE_INVENTORY_MISMATCH,
    FEATURE_STATUS,
    ACTIVE_OVERFLOW,
    FEATURE_INDEX,
    DUPLICATE_FEATURE,
    POCKET_UNITS,
    POCKET_ROUTING_MISMATCH,
    TRANSFORMER_RUNTIME_RANGE,
};

struct LargeNetworkAccumulatorResultV1 {
    LargeNetworkAccumulatorError error = LargeNetworkAccumulatorError::SOURCE_NOT_READY;

    constexpr bool ok() const noexcept { return error == LargeNetworkAccumulatorError::NONE; }
};

class LargeNetworkAccumulatorV1 {
   public:
    constexpr bool ready() const noexcept { return ready_; }
    bool           bound_to(const LargeNetworkV1& network) const noexcept {
        return ready_ && network_ == &network;
    }

    LargeNetworkAccumulatorResultV1
                                    refresh(const LargeNetworkV1&                  network,
                                            const LargeFeatureInventoryV1::Result& features) noexcept;
    LargeNetworkAccumulatorResultV1 update(const LargeNetworkV1&                  network,
                                           const LargeFeatureInventoryV1::Result& source,
                                           const LargeFeatureInventoryV1::Result& target) noexcept;
    LargeNetworkEvaluationResultV1  evaluate(const LargeFeatureInventoryV1::Result& features,
                                             Color sideToMove) const noexcept;
    bool matches(const LargeFeatureInventoryV1::Result& features) const noexcept;

   private:
    using KMembership = std::array<std::bitset<LargeFeatureInventoryV1::KDimensions>, COLOR_NB>;
    using GMembership = std::array<std::bitset<LargeFeatureInventoryV1::GDimensions>, COLOR_NB>;

    bool                                    ready_   = false;
    const LargeNetworkV1*                   network_ = nullptr;
    KMembership                             kMembership_{};
    GMembership                             gMembership_{};
    std::array<std::size_t, COLOR_NB>       kSizes_{};
    std::array<std::size_t, COLOR_NB>       gSizes_{};
    std::size_t                             totalPocketUnits_{};
    std::array<LargeKAccumulator, COLOR_NB> kAccumulator_{};
    std::array<LargeGAccumulator, COLOR_NB> gAccumulator_{};
};

struct LargeNetworkLoadResultV1 {
    LargeNetworkLoadError           error = LargeNetworkLoadError::WRONG_SIZE;
    std::unique_ptr<LargeNetworkV1> network{};

    bool ok() const noexcept {
        return error == LargeNetworkLoadError::NONE && network && network->ready();
    }
};

LargeNetworkLoadResultV1 load_large_network_v1(const Byte*                      bytes,
                                               std::size_t                      size,
                                               const LargeExpectedProvenanceV1& expected) noexcept;
Digest                   large_network_sha256(const Byte* bytes, std::size_t size) noexcept;
LargeNetworkSimdBackend  large_network_simd_backend() noexcept;
std::string_view         large_network_load_error_name(LargeNetworkLoadError error) noexcept;
std::string_view large_network_evaluate_error_name(LargeNetworkEvaluateError error) noexcept;
std::string_view large_network_accumulator_error_name(LargeNetworkAccumulatorError error) noexcept;

static_assert(LargeKWeightElements == 62717952);
static_assert(LargeGWeightElements == 343040);
static_assert(LargeFc0WeightElements == 262144);
static_assert(LargeFc1WeightElements == 16384);
static_assert(LargeFc2WeightElements == 1024);
static_assert(LargeNetworkHeaderBytes + LargeNetworkPayloadBytes == LargeNetworkFileBytes);

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2

#endif  // NNUE_CRAZYHOUSE_V2_LARGE_NETWORK_H_INCLUDED
