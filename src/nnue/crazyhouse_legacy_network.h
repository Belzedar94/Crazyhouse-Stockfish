/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_LEGACY_NETWORK_H_INCLUDED
#define NNUE_CRAZYHOUSE_LEGACY_NETWORK_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <string_view>

#include "crazyhouse_legacy_features.h"

namespace Stockfish::Eval::NNUE {

class LegacyCrazyhouseAccumulatorStackV1;

class LegacyCrazyhouseNetworkV1 {
   public:
    enum class ExecutionBackend {
        Scalar,
        Simd
    };

    static constexpr std::uint32_t FileVersion      = 0x7AF32F20U;
    static constexpr std::uint32_t NetworkHash      = 0x3C103E72U;
    static constexpr std::uint32_t TransformerHash  = 0x5F2348B8U;
    static constexpr std::uint32_t ArchitectureHash = 0x633376CAU;

    static constexpr std::size_t FeatureDimensions     = 55'296;
    static constexpr std::size_t TransformerDimensions = 512;
    static constexpr std::size_t PsqtBuckets           = 8;
    static constexpr std::size_t LayerStacks           = 8;

    static constexpr std::size_t HeaderBytes = 4 + 4 + 4 + 75;
    static constexpr std::size_t TransformerSectionBytes =
      4 + TransformerDimensions * 2 + FeatureDimensions * TransformerDimensions * 2
      + FeatureDimensions * PsqtBuckets * 4;
    static constexpr std::size_t LayerParameterBytes =
      (16 * 4 + 1024 * 16) + (32 * 4 + 32 * 32) + (1 * 4 + 32 * 1);
    static constexpr std::size_t LayerStackBytes = 4 + LayerParameterBytes;
    static constexpr std::size_t FileBytes =
      HeaderBytes + TransformerSectionBytes + LayerStacks * LayerStackBytes;

    inline static constexpr std::string_view RegisteredDescription =
      "Network trained with the https://github.com/glinscott/nnue-pytorch trainer.";
    inline static constexpr std::string_view RegisteredSha256 =
      "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43";
    inline static constexpr std::string_view EmbeddedFileToken =
      "embedded:crazyhouse-8ebf84784ad2.nnue";

    enum class LoadStatus {
        Success,
        MissingFile,
        FileReadFailure,
        TruncatedFile,
        OversizedFile,
        VersionMismatch,
        NetworkHashMismatch,
        DescriptionLengthMismatch,
        DescriptionMismatch,
        TransformerHashMismatch,
        ArchitectureHashMismatch,
        TensorLayoutMismatch,
        DigestMismatch
    };

    struct LoadResult {
        LoadStatus  status;
        std::string message;
    };

    enum class EvalStatus {
        Success,
        NetworkNotLoaded,
        FeatureRejected,
        ContractViolation
    };

    struct RawComponents {
        std::int32_t psqt       = 0;
        std::int32_t positional = 0;
    };

    struct RawEvaluation {
        std::array<RawComponents, LayerStacks> buckets{};
        std::uint8_t                           selectedBucket = 0;

        const RawComponents& selected() const noexcept { return buckets[selectedBucket]; }
    };

    enum LegacyNonPawnIndex : std::size_t {
        LegacyKnight,
        LegacyBishop,
        LegacyRook,
        LegacyQueen,
        LegacyNonPawnCount
    };

    static constexpr std::array<std::int32_t, LegacyNonPawnCount> LegacyNonPawnValues = {
      781, 825, 1276, 2538};
    static constexpr std::int32_t LegacyEntertainmentThreshold = 44;
    static constexpr std::int32_t LegacyOuterLowerBound        = -31507;
    static constexpr std::int32_t LegacyOuterUpperBound        = 31507;

    struct LegacyBoardInventory {
        std::size_t                                 boardPawns = 0;
        std::array<std::size_t, LegacyNonPawnCount> whiteNonPawns{};
        std::array<std::size_t, LegacyNonPawnCount> blackNonPawns{};
    };

    enum class AdapterStatus {
        Success,
        InvalidInventory,
        ArithmeticOutOfRange
    };

    struct LegacyAdapterOutput {
        std::size_t  boardPawns           = 0;
        std::int32_t whiteNonPawnMaterial = 0;
        std::int32_t blackNonPawnMaterial = 0;
        bool         entertainmentApplied = false;
        std::int32_t scale                = 0;
        std::int32_t unadjusted           = 0;
        std::int32_t adjusted             = 0;
        std::int32_t outerPreClamp        = 0;
        std::int32_t outer                = 0;
        bool         clamped              = false;
    };

    struct AdapterResult {
        AdapterStatus                      status = AdapterStatus::InvalidInventory;
        std::optional<LegacyAdapterOutput> output;
        std::string                        message;

        bool ok() const noexcept { return status == AdapterStatus::Success && output.has_value(); }
    };

    struct LegacyEvaluation {
        RawEvaluation       raw;
        LegacyAdapterOutput adapter;
    };

    struct LegacyEvalResult {
        EvalStatus                                        status = EvalStatus::ContractViolation;
        std::optional<LegacyCrazyhouseFeaturesV1::Status> featureStatus;
        std::optional<LegacyEvaluation>                   output;
        std::string                                       message;

        bool ok() const noexcept { return status == EvalStatus::Success && output.has_value(); }
    };

    struct EvalResult {
        EvalStatus                                        status = EvalStatus::ContractViolation;
        std::optional<LegacyCrazyhouseFeaturesV1::Status> featureStatus;
        std::optional<RawEvaluation>                      output;
        std::string                                       message;

        bool ok() const noexcept { return status == EvalStatus::Success && output.has_value(); }
    };

    explicit LegacyCrazyhouseNetworkV1(ExecutionBackend backend = ExecutionBackend::Scalar);
    ~LegacyCrazyhouseNetworkV1();

    LegacyCrazyhouseNetworkV1(const LegacyCrazyhouseNetworkV1&)            = delete;
    LegacyCrazyhouseNetworkV1& operator=(const LegacyCrazyhouseNetworkV1&) = delete;

    LoadResult       load_file(const std::filesystem::path& path);
    LoadResult       load_embedded();
    LoadResult       load_bytes(const unsigned char* data, std::size_t size);
    EvalResult       evaluate_full_refresh(const Position& position) const;
    EvalResult       evaluate_incremental(const Position&                     position,
                                          LegacyCrazyhouseAccumulatorStackV1& stack) const;
    LegacyEvalResult evaluate_legacy(const Position& position) const;
    LegacyEvalResult evaluate_legacy_incremental(const Position&                     position,
                                                 LegacyCrazyhouseAccumulatorStackV1& stack) const;

    static AdapterResult adapt_legacy_components(RawComponents               raw,
                                                 const LegacyBoardInventory& inventory);

    bool                    loaded() const noexcept;
    static bool             embedded_available() noexcept;
    ExecutionBackend        execution_backend() const noexcept { return backend_; }
    std::string_view        description() const noexcept;
    std::string_view        artifact_sha256() const noexcept;
    static std::string_view compiled_simd_backend() noexcept;

    static std::string_view status_name(LoadStatus status) noexcept;
    static std::string_view eval_status_name(EvalStatus status) noexcept;
    static std::string_view adapter_status_name(AdapterStatus status) noexcept;

   private:
    struct Parameters;
    std::unique_ptr<Parameters> parameters_;
    std::string                 description_;
    ExecutionBackend            backend_ = ExecutionBackend::Scalar;

    void       reset() noexcept;
    EvalResult propagate_accumulators(
      const Position&                                                               position,
      std::size_t                                                                   boardPieceCount,
      const std::array<std::array<std::uint16_t, TransformerDimensions>, COLOR_NB>& transformerBits,
      const std::array<std::array<std::uint32_t, PsqtBuckets>, COLOR_NB>&           psqtBits) const;

    friend class LegacyCrazyhouseAccumulatorStackV1;
};

class LegacyCrazyhouseAccumulatorStackV1 {
   public:
    static constexpr std::size_t MaxSize = MAX_PLY + 1;

    struct Counters {
        std::uint64_t evaluations              = 0;
        std::uint64_t fullRefreshes            = 0;
        std::uint64_t deltaUpdates             = 0;
        std::uint64_t sameFrameReuses          = 0;
        std::uint64_t kingPerspectiveRefreshes = 0;
        std::uint64_t removedFeatures          = 0;
        std::uint64_t addedFeatures            = 0;
        std::uint64_t maxSourceDistance        = 0;
    };

    LegacyCrazyhouseAccumulatorStackV1();
    ~LegacyCrazyhouseAccumulatorStackV1();

    LegacyCrazyhouseAccumulatorStackV1(const LegacyCrazyhouseAccumulatorStackV1&) = delete;
    LegacyCrazyhouseAccumulatorStackV1&
    operator=(const LegacyCrazyhouseAccumulatorStackV1&) = delete;

    void               reset() noexcept;
    [[nodiscard]] bool push() noexcept;
    [[nodiscard]] bool pop() noexcept;

    [[nodiscard]] std::size_t     size() const noexcept { return size_; }
    [[nodiscard]] const Counters& counters() const noexcept { return counters_; }

   private:
    struct ActiveRows {
        std::array<LegacyCrazyhouseFeaturesV1::Index,
                   LegacyCrazyhouseFeaturesV1::MaxActiveDimensions>
                    values{};
        std::size_t size = 0;
    };

    struct Frame {
        std::array<std::array<std::uint16_t, LegacyCrazyhouseNetworkV1::TransformerDimensions>,
                   COLOR_NB>
          transformerBits{};
        std::array<std::array<std::uint32_t, LegacyCrazyhouseNetworkV1::PsqtBuckets>, COLOR_NB>
                                         psqtBits{};
        std::array<ActiveRows, COLOR_NB> active{};
        std::array<Square, COLOR_NB>     kingSquares{SQ_NONE, SQ_NONE};
        std::size_t                      boardPieceCount = 0;
        bool                             computed        = false;
    };

    const LegacyCrazyhouseNetworkV1* network_ = nullptr;
    std::unique_ptr<Frame[]>         frames_;
    std::size_t                      size_ = 1;
    Counters                         counters_{};

    friend class LegacyCrazyhouseNetworkV1;
};

static_assert(LegacyCrazyhouseNetworkV1::RegisteredDescription.size() == 75);
static_assert(LegacyCrazyhouseNetworkV1::HeaderBytes == 87);
static_assert(LegacyCrazyhouseNetworkV1::TransformerSectionBytes == 58'393'604);
static_assert(LegacyCrazyhouseNetworkV1::LayerParameterBytes == 17'636);
static_assert(LegacyCrazyhouseNetworkV1::LayerStackBytes == 17'640);
static_assert(LegacyCrazyhouseNetworkV1::FileBytes == 58'534'811);
static_assert(LegacyCrazyhouseNetworkV1::FeatureDimensions
              == LegacyCrazyhouseFeaturesV1::FeatureDimensions);
static_assert(LegacyCrazyhouseNetworkV1::LayerStacks == LegacyCrazyhouseFeaturesV1::LayerStacks);
static_assert(LegacyCrazyhouseNetworkV1::PsqtBuckets == LegacyCrazyhouseNetworkV1::LayerStacks);
static_assert(LegacyCrazyhouseAccumulatorStackV1::MaxSize == MAX_PLY + 1);

}  // namespace Stockfish::Eval::NNUE

#endif  // NNUE_CRAZYHOUSE_LEGACY_NETWORK_H_INCLUDED
