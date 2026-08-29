/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_LARGE_RUNTIME_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_LARGE_RUNTIME_H_INCLUDED

#include <cstddef>
#include <filesystem>
#include <memory>
#include <string>
#include <string_view>

#include "crazyhouse_v2_large_network.h"

namespace Stockfish {

class Position;

namespace Eval::NNUE::CrazyhouseV2 {

enum class LargeRuntimeLoadStatus {
    SUCCESS,
    INVALID_SHA256,
    INVALID_PROVENANCE,
    MISSING_FILE,
    NOT_REGULAR_FILE,
    FILE_READ_FAILURE,
    WRONG_FILE_SIZE,
    ARTIFACT_SHA256_MISMATCH,
    SIMD_UNAVAILABLE,
    CONTAINER_REJECTED,
};

struct LargeRuntimeLoadResultV1 {
    LargeRuntimeLoadStatus status         = LargeRuntimeLoadStatus::FILE_READ_FAILURE;
    LargeNetworkLoadError  containerError = LargeNetworkLoadError::NONE;
    std::string            message;

    constexpr bool ok() const noexcept { return status == LargeRuntimeLoadStatus::SUCCESS; }
};

class LargeRuntimeAccumulatorStackV1;

class LargeRuntimeV1 {
   public:
    LargeRuntimeLoadResultV1 load_file(const std::filesystem::path& path,
                                       std::string_view             expectedSha256,
                                       std::string_view             expectedProvenance);
    void                     reset() noexcept;

    bool             loaded() const noexcept { return network_ && network_->ready(); }
    std::string_view artifact_sha256() const noexcept { return artifactSha256_; }

    LargeNetworkEvaluationResultV1 evaluate_full_refresh(const Position& position) const noexcept;
    LargeNetworkEvaluationResultV1
    evaluate_full_refresh_simd(const Position& position) const noexcept;
    LargeNetworkEvaluationResultV1
    evaluate_search_incremental(const Position&                 position,
                                LargeRuntimeAccumulatorStackV1& stack) const noexcept;

    static std::string_view simd_backend_name() noexcept;

   private:
    std::unique_ptr<LargeNetworkV1> network_{};
    std::string                     artifactSha256_;

    friend class LargeRuntimeAccumulatorStackV1;
};

class LargeRuntimeAccumulatorStackV1 {
   public:
    static constexpr std::size_t MaxSize = MAX_PLY + 1;

    LargeRuntimeAccumulatorStackV1();
    ~LargeRuntimeAccumulatorStackV1();

    LargeRuntimeAccumulatorStackV1(const LargeRuntimeAccumulatorStackV1&)            = delete;
    LargeRuntimeAccumulatorStackV1& operator=(const LargeRuntimeAccumulatorStackV1&) = delete;

    [[nodiscard]] bool ensure_allocated() noexcept;
    void               reset() noexcept;
    [[nodiscard]] bool push() noexcept;
    [[nodiscard]] bool pop() noexcept;
    std::size_t        size() const noexcept { return size_; }

   private:
    struct Frame {
        LargeFeatureInventoryV1::Result inventory{};
        LargeNetworkAccumulatorV1       accumulator{};
        bool                            computed = false;
    };

    const LargeNetworkV1*    network_ = nullptr;
    std::unique_ptr<Frame[]> frames_;
    std::size_t              size_ = 1;

    friend class LargeRuntimeV1;
};

std::string_view large_runtime_load_status_name(LargeRuntimeLoadStatus status) noexcept;

static_assert(LargeRuntimeAccumulatorStackV1::MaxSize == MAX_PLY + 1);

}  // namespace Eval::NNUE::CrazyhouseV2
}  // namespace Stockfish

#endif  // NNUE_CRAZYHOUSE_V2_LARGE_RUNTIME_H_INCLUDED
