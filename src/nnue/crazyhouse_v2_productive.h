/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_PRODUCTIVE_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_PRODUCTIVE_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string_view>

#include "crazyhouse_v2_features.h"

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {

inline constexpr std::size_t ProductiveHeaderBytes      = 512;
inline constexpr std::size_t ProductiveTransformerLanes = 512;
inline constexpr std::size_t ProductiveDense0Inputs     = 1024;
inline constexpr std::size_t ProductiveDense0Outputs    = 32;
inline constexpr std::size_t ProductiveDense1Inputs     = 32;
inline constexpr std::size_t ProductiveDense1Outputs    = 32;
inline constexpr std::size_t ProductiveOutputInputs     = 32;
inline constexpr std::size_t ProductiveTransformerWeightElements =
  ScalarFeatureInventoryV1::Dimensions * ProductiveTransformerLanes;
inline constexpr std::size_t ProductiveDense0WeightElements =
  ProductiveDense0Inputs * ProductiveDense0Outputs;
inline constexpr std::size_t ProductiveDense1WeightElements =
  ProductiveDense1Inputs * ProductiveDense1Outputs;
inline constexpr std::size_t ProductivePayloadBytes = 959812;
inline constexpr std::size_t ProductiveFileBytes    = 960324;

static_assert(ProductiveTransformerWeightElements == 461824);
static_assert(ProductiveDense0WeightElements == 32768);
static_assert(ProductiveDense1WeightElements == 1024);

struct ProductiveExpectedProvenanceV1 {
    Digest datasetManifest{};
    Digest trainingConfig{};
};

enum class ProductiveLoadError {
    NONE,
    WRONG_SIZE,
    EXPECTED_PROVENANCE,
    MAGIC,
    BYTE_ORDER,
    HEADER_SIZE,
    VERSION,
    FLAGS,
    FILE_SIZE,
    FEATURE_DIMENSIONS,
    MAXIMUM_ACTIVE,
    TRANSFORMER_LANES,
    PERSPECTIVE_COUNT,
    DENSE0_INPUTS,
    DENSE0_OUTPUTS,
    DENSE1_INPUTS,
    DENSE1_OUTPUTS,
    OUTPUT_INPUTS,
    OUTPUT_OUTPUTS,
    TRANSFORMER_WEIGHT_TYPE,
    TRANSFORMER_BIAS_TYPE,
    DENSE_WEIGHT_TYPE,
    DENSE_BIAS_TYPE,
    OUTPUT_WEIGHT_TYPE,
    OUTPUT_BIAS_TYPE,
    ACCUMULATOR_TYPE,
    ACTIVATION_TYPE,
    TRANSFORMER_SCALE,
    DENSE_WEIGHT_SCALE,
    OUTPUT_WEIGHT_SCALE,
    OUTPUT_VALUE_SCALE,
    DENSE_ACTIVATION_DIVISOR,
    OUTPUT_DIVISOR,
    TENSOR_DIRECTORY,
    INPUT_SEMANTICS,
    PERSPECTIVE_ORDER,
    ACTIVATION_SEMANTICS,
    OUTPUT_UNITS,
    RULE_PROFILE_IDENTITY,
    PHYSICAL_SCHEMA_IDENTITY,
    FEATURE_CONTRACT_IDENTITY,
    ARCHITECTURE_IDENTITY,
    QUANTIZATION_IDENTITY,
    DATASET_IDENTITY_ZERO,
    DATASET_IDENTITY,
    TRAINING_CONFIG_IDENTITY_ZERO,
    TRAINING_CONFIG_IDENTITY,
    RESERVED_BYTES,
    HEADER_CRC32C,
    PAYLOAD_SHA256,
    ALLOCATION,
    TRANSFORMER_INTERVAL,
    DENSE0_INTERVAL,
    DENSE1_INTERVAL,
    OUTPUT_INTERVAL,
};

enum class ProductiveEvaluateError {
    NONE,
    NETWORK_NOT_READY,
    ACCUMULATOR_NOT_READY,
    ACCUMULATOR_INVENTORY_MISMATCH,
    FEATURE_STATUS,
    SIDE_TO_MOVE,
    ACTIVE_OVERFLOW,
    FEATURE_INDEX,
    DUPLICATE_FEATURE,
    TRANSFORMER_RUNTIME_RANGE,
    DENSE0_RUNTIME_RANGE,
    DENSE1_RUNTIME_RANGE,
    OUTPUT_RUNTIME_RANGE,
    SIMD_UNAVAILABLE,
};

enum class ProductiveSimdBackend {
    UNAVAILABLE,
    SSE2_X8_INT16_TO_INT32,
};

struct ProductiveTraceV1 {
    std::array<std::int32_t, ProductiveTransformerLanes> transformerStm{};
    std::array<std::int32_t, ProductiveTransformerLanes> transformerOpponent{};
    std::array<Byte, ProductiveTransformerLanes>         transformerStmActivation{};
    std::array<Byte, ProductiveTransformerLanes>         transformerOpponentActivation{};
    std::array<std::int32_t, ProductiveDense0Outputs>    dense0{};
    std::array<Byte, ProductiveDense0Outputs>            dense0Activation{};
    std::array<std::int32_t, ProductiveDense1Outputs>    dense1{};
    std::array<Byte, ProductiveDense1Outputs>            dense1Activation{};
    std::int32_t                                         outputRaw        = 0;
    std::int32_t                                         outputCentipawns = 0;
};

struct ProductiveEvaluationResultV1 {
    ProductiveEvaluateError error = ProductiveEvaluateError::NETWORK_NOT_READY;
    ProductiveTraceV1       trace{};

    constexpr bool ok() const noexcept { return error == ProductiveEvaluateError::NONE; }
};

struct ProductiveLoadResultV1;
class ProductiveAccumulatorV1;

class ProductiveNetworkV1 {
   public:
    constexpr bool                        ready() const noexcept { return ready_; }
    const ProductiveExpectedProvenanceV1& provenance() const noexcept { return provenance_; }

    ProductiveEvaluationResultV1 evaluate(const ScalarFeatureInventoryV1::Result& features,
                                          Color sideToMove) const noexcept;
    ProductiveEvaluationResultV1 evaluate_simd(const ScalarFeatureInventoryV1::Result& features,
                                               Color sideToMove) const noexcept;

   private:
    friend class ProductiveAccumulatorV1;
    friend ProductiveLoadResultV1
    load_productive_v1(const Byte*, std::size_t, const ProductiveExpectedProvenanceV1&) noexcept;

    ProductiveLoadError          validate_static_intervals() const noexcept;
    ProductiveEvaluationResultV1 evaluate_from_transformers(
      const std::array<std::int32_t, ProductiveTransformerLanes>& stm,
      const std::array<std::int32_t, ProductiveTransformerLanes>& opponent) const noexcept;

    bool                                                          ready_ = false;
    ProductiveExpectedProvenanceV1                                provenance_{};
    std::array<std::int16_t, ProductiveTransformerWeightElements> transformerWeights_{};
    std::array<std::int32_t, ProductiveTransformerLanes>          transformerBiases_{};
    std::array<std::int8_t, ProductiveDense0WeightElements>       dense0Weights_{};
    std::array<std::int32_t, ProductiveDense0Outputs>             dense0Biases_{};
    std::array<std::int8_t, ProductiveDense1WeightElements>       dense1Weights_{};
    std::array<std::int32_t, ProductiveDense1Outputs>             dense1Biases_{};
    std::array<std::int16_t, ProductiveOutputInputs>              outputWeights_{};
    std::int32_t                                                  outputBias_ = 0;
};

enum class ProductiveAccumulatorError {
    NONE,
    NETWORK_NOT_READY,
    NETWORK_MISMATCH,
    SOURCE_NOT_READY,
    SOURCE_INVENTORY_MISMATCH,
    FEATURE_STATUS,
    ACTIVE_OVERFLOW,
    FEATURE_INDEX,
    DUPLICATE_FEATURE,
    TRANSFORMER_RUNTIME_RANGE,
};

struct ProductiveAccumulatorResultV1 {
    ProductiveAccumulatorError error = ProductiveAccumulatorError::SOURCE_NOT_READY;

    constexpr bool ok() const noexcept { return error == ProductiveAccumulatorError::NONE; }
};

class ProductiveAccumulatorV1 {
   public:
    constexpr bool ready() const noexcept { return ready_; }
    bool           bound_to(const ProductiveNetworkV1& network) const noexcept {
        return ready_ && network_ == &network;
    }

    ProductiveAccumulatorResultV1
                                  refresh(const ProductiveNetworkV1&              network,
                                          const ScalarFeatureInventoryV1::Result& features) noexcept;
    ProductiveAccumulatorResultV1 update(const ProductiveNetworkV1&              network,
                                         const ScalarFeatureInventoryV1::Result& source,
                                         const ScalarFeatureInventoryV1::Result& target) noexcept;
    ProductiveEvaluationResultV1  evaluate(const ScalarFeatureInventoryV1::Result& features,
                                           Color sideToMove) const noexcept;
    bool matches(const ScalarFeatureInventoryV1::Result& features) const noexcept;

   private:
    using Membership = std::array<std::array<bool, ScalarFeatureInventoryV1::Dimensions>, COLOR_NB>;

    bool                                                                       ready_   = false;
    const ProductiveNetworkV1*                                                 network_ = nullptr;
    Membership                                                                 membership_{};
    std::array<std::size_t, COLOR_NB>                                          sizes_{};
    std::array<std::array<std::int32_t, ProductiveTransformerLanes>, COLOR_NB> transformers_{};
};

struct ProductiveLoadResultV1 {
    ProductiveLoadError                  error = ProductiveLoadError::WRONG_SIZE;
    std::unique_ptr<ProductiveNetworkV1> network{};

    bool ok() const noexcept {
        return error == ProductiveLoadError::NONE && network && network->ready();
    }
};

ProductiveLoadResultV1 load_productive_v1(const Byte*                           bytes,
                                          std::size_t                           size,
                                          const ProductiveExpectedProvenanceV1& expected) noexcept;
ProductiveSimdBackend  productive_simd_backend() noexcept;
std::string_view       productive_load_error_name(ProductiveLoadError error) noexcept;
std::string_view       productive_evaluate_error_name(ProductiveEvaluateError error) noexcept;
std::string_view       productive_accumulator_error_name(ProductiveAccumulatorError error) noexcept;

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2

#endif  // NNUE_CRAZYHOUSE_V2_PRODUCTIVE_H_INCLUDED
