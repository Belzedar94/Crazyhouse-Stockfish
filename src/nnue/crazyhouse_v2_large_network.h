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
    BYTE_ORDER,
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
};

struct LargeNetworkTraceV1 {
    std::size_t bucket{};
    std::array<LargeKAccumulator, COLOR_NB> kAccumulator{};
    std::array<LargeGAccumulator, COLOR_NB> gAccumulator{};
    std::array<LargePerspectiveOutput, COLOR_NB> perspectiveOutput{};
    std::array<Byte, LargeDenseInputBytes> denseInput{};
    std::array<std::int32_t, LargeFc0Outputs> fc0{};
    std::array<Byte, LargeFc0Outputs> fc0Squared{};
    std::array<Byte, LargeFc0Outputs> fc0Clipped{};
    std::array<std::int32_t, LargeFc1Outputs> fc1{};
    std::array<Byte, LargeFc1Outputs> fc1Squared{};
    std::array<Byte, LargeFc1Outputs> fc1Clipped{};
    std::int32_t fc2{};
    std::int32_t fwdRaw{};
    std::int32_t outputValue{};
};

struct LargeNetworkEvaluationResultV1 {
    LargeNetworkEvaluateError error = LargeNetworkEvaluateError::NETWORK_NOT_READY;
    LargeNetworkTraceV1       trace{};

    constexpr bool ok() const noexcept { return error == LargeNetworkEvaluateError::NONE; }
};

struct LargeNetworkLoadResultV1;

class LargeNetworkV1 {
   public:
    constexpr bool ready() const noexcept { return ready_; }
    const LargeExpectedProvenanceV1& provenance() const noexcept { return provenance_; }

    LargeNetworkEvaluationResultV1 evaluate(const LargeFeatureInventoryV1::Result& features,
                                             Color sideToMove) const noexcept;

   private:
    friend LargeNetworkLoadResultV1
    load_large_network_v1(const Byte*, std::size_t, const LargeExpectedProvenanceV1&) noexcept;

    LargeNetworkLoadError validate_dense_intervals() const noexcept;

    bool ready_ = false;
    LargeExpectedProvenanceV1 provenance_{};
    std::unique_ptr<std::int16_t[]> kWeights_{};
    std::array<std::int16_t, LargeKTransformerLanes> kBiases_{};
    std::unique_ptr<std::int16_t[]> gWeights_{};
    std::array<std::int16_t, LargeGTransformerLanes> gBiases_{};
    std::array<std::int32_t, LargeLayerStacks * LargeFc0Outputs> fc0Biases_{};
    std::array<std::int8_t, LargeFc0WeightElements> fc0Weights_{};
    std::array<std::int32_t, LargeLayerStacks * LargeFc1Outputs> fc1Biases_{};
    std::array<std::int8_t, LargeFc1WeightElements> fc1Weights_{};
    std::array<std::int32_t, LargeLayerStacks> fc2Biases_{};
    std::array<std::int8_t, LargeFc2WeightElements> fc2Weights_{};
};

struct LargeNetworkLoadResultV1 {
    LargeNetworkLoadError error = LargeNetworkLoadError::WRONG_SIZE;
    std::unique_ptr<LargeNetworkV1> network{};

    bool ok() const noexcept {
        return error == LargeNetworkLoadError::NONE && network && network->ready();
    }
};

LargeNetworkLoadResultV1 load_large_network_v1(const Byte* bytes,
                                               std::size_t size,
                                               const LargeExpectedProvenanceV1& expected) noexcept;
std::string_view large_network_load_error_name(LargeNetworkLoadError error) noexcept;
std::string_view large_network_evaluate_error_name(LargeNetworkEvaluateError error) noexcept;

static_assert(LargeKWeightElements == 62717952);
static_assert(LargeGWeightElements == 343040);
static_assert(LargeFc0WeightElements == 262144);
static_assert(LargeFc1WeightElements == 16384);
static_assert(LargeFc2WeightElements == 1024);
static_assert(LargeNetworkHeaderBytes + LargeNetworkPayloadBytes == LargeNetworkFileBytes);

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2

#endif  // NNUE_CRAZYHOUSE_V2_LARGE_NETWORK_H_INCLUDED
