/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_LEGACY_CONTROL_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_LEGACY_CONTROL_H_INCLUDED

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

class LegacyControlNetworkV2 {
   public:
    static constexpr std::size_t HeaderBytes           = 1'024;
    static constexpr std::size_t PayloadBytes          = 58'534'688;
    static constexpr std::size_t FileBytes             = 58'535'712;
    static constexpr std::size_t FeatureDimensions     = 55'296;
    static constexpr std::size_t MaximumActive         = 128;
    static constexpr std::size_t TransformerDimensions = 512;
    static constexpr std::size_t PsqtBuckets           = 8;
    static constexpr std::size_t LayerStacks           = 8;
    static constexpr std::size_t Dense0Inputs          = 1'024;
    static constexpr std::size_t Dense0Outputs         = 16;
    static constexpr std::size_t Dense0PaddedOutputs   = 32;
    static constexpr std::size_t Dense1Inputs          = 32;
    static constexpr std::size_t Dense1Outputs         = 32;
    static constexpr std::size_t OutputInputs          = 32;

    inline static constexpr std::string_view Magic = "CHNNUEV2LC1";
    inline static constexpr std::string_view RuleProfileSha256 =
      "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68";
    inline static constexpr std::string_view LegacyFeatureContractSha256 =
      "82b4b5dafa9e280479ea47057da88625d2bdfa40801e5dcd51ba861a52c30f00";
    inline static constexpr std::string_view ContainerContractSha256 =
      "1d738d8c956c9d15a74f44dcf145d33aa72579da83d8be7421ca29050ad04759";
    inline static constexpr std::string_view OriginArtifactSha256 =
      "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43";

    enum class LoadStatus {
        Success,
        MissingFile,
        FileReadFailure,
        TruncatedFile,
        OversizedFile,
        NullInput,
        HeaderCrcMismatch,
        MagicMismatch,
        FixedFieldMismatch,
        ReservedBytesNonzero,
        IdentityMismatch,
        ProvenanceMismatch,
        DirectoryMismatch,
        PayloadDigestMismatch,
        SectionDigestMismatch,
        TensorLayoutMismatch
    };

    struct Requirements {
        std::string converterSha256;
        std::string sourceCommit;
        std::string sourceTree;
    };

    struct LoadResult {
        LoadStatus  status = LoadStatus::FileReadFailure;
        std::string message;

        bool ok() const noexcept { return status == LoadStatus::Success; }
    };

    struct RawComponents {
        std::int32_t psqt       = 0;
        std::int32_t positional = 0;
    };

    struct BucketTrace {
        std::array<std::int32_t, Dense0Outputs> dense0Affine{};
        std::array<std::uint8_t, Dense0Outputs> dense0Activation{};
        std::array<std::int32_t, Dense1Outputs> dense1Affine{};
        std::array<std::uint8_t, Dense1Outputs> dense1Activation{};
        std::int32_t                            outputAffine = 0;
        std::int32_t                            psqt         = 0;
    };

    struct Trace {
        std::array<std::array<std::uint16_t, TransformerDimensions>, COLOR_NB> transformerBits{};
        std::array<std::array<std::uint32_t, PsqtBuckets>, COLOR_NB>           psqtBits{};
        std::array<std::uint8_t, TransformerDimensions * 2>                    transformed{};
        std::array<BucketTrace, LayerStacks>                                   buckets{};
        std::uint8_t                                                           selectedBucket = 0;

        RawComponents selected() const noexcept {
            const BucketTrace& value = buckets[selectedBucket];
            return {value.psqt, value.outputAffine};
        }
    };

    enum class EvalStatus {
        Success,
        NetworkNotLoaded,
        FeatureRejected,
        ContractViolation
    };

    struct EvalResult {
        EvalStatus                                        status = EvalStatus::ContractViolation;
        std::optional<LegacyCrazyhouseFeaturesV1::Status> featureStatus;
        std::optional<Trace>                              trace;
        std::string                                       message;

        bool ok() const noexcept { return status == EvalStatus::Success && trace.has_value(); }
    };

    LegacyControlNetworkV2();
    ~LegacyControlNetworkV2();

    LegacyControlNetworkV2(const LegacyControlNetworkV2&)            = delete;
    LegacyControlNetworkV2& operator=(const LegacyControlNetworkV2&) = delete;

    LoadResult load_file(const std::filesystem::path& path, const Requirements& requirements);
    LoadResult
    load_bytes(const unsigned char* data, std::size_t size, const Requirements& requirements);
    EvalResult evaluate(const LegacyCrazyhouseFeaturesV1::Result& features, Color sideToMove) const;

    bool             loaded() const noexcept;
    std::string_view file_sha256() const noexcept;
    std::string_view converter_sha256() const noexcept;
    std::string_view source_commit() const noexcept;
    std::string_view source_tree() const noexcept;

    static std::string      trace_sha256(const LegacyCrazyhouseFeaturesV1::Result& features,
                                         Color                                     sideToMove,
                                         const Trace&                              trace);
    static std::string_view status_name(LoadStatus status) noexcept;
    static std::string_view eval_status_name(EvalStatus status) noexcept;

   private:
    struct Parameters;
    std::unique_ptr<Parameters> parameters_;
    std::string                 fileSha256_;
    std::string                 converterSha256_;
    std::string                 sourceCommit_;
    std::string                 sourceTree_;

    void reset() noexcept;
};

static_assert(LegacyControlNetworkV2::FeatureDimensions
              == LegacyCrazyhouseFeaturesV1::FeatureDimensions);
static_assert(LegacyControlNetworkV2::MaximumActive
              == LegacyCrazyhouseFeaturesV1::MaxActiveDimensions);
static_assert(LegacyControlNetworkV2::LayerStacks == LegacyCrazyhouseFeaturesV1::LayerStacks);

}  // namespace Stockfish::Eval::NNUE

#endif  // NNUE_CRAZYHOUSE_V2_LEGACY_CONTROL_H_INCLUDED
