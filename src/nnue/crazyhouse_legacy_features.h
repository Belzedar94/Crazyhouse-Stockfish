/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_LEGACY_FEATURES_H_INCLUDED
#define NNUE_CRAZYHOUSE_LEGACY_FEATURES_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include "../types.h"

namespace Stockfish {

class Position;

namespace Eval::NNUE {

class LegacyCrazyhouseFeaturesV1 {
   public:
    using Index = std::uint32_t;

    static constexpr std::size_t BoardPlaneCount  = 11;
    static constexpr std::size_t BoardSquareCount = 64;
    static constexpr std::size_t BoardFeatures    = BoardPlaneCount * BoardSquareCount;
    static constexpr std::size_t PocketBandCount  = 10;
    static constexpr std::size_t PocketSlots      = 16;
    static constexpr std::size_t PocketFeatures   = PocketBandCount * PocketSlots;
    static constexpr std::size_t KingStride       = BoardFeatures + PocketFeatures;
    static constexpr std::size_t KingBuckets      = 64;
    static constexpr std::size_t FeatureDimensions = KingStride * KingBuckets;
    static constexpr std::size_t LayerStacks       = 8;
    static constexpr std::size_t LegacyMaxPieces   = 32;
    static constexpr std::size_t MaxActiveDimensions = 128;

    enum class Status {
        Success,
        WrongRuleset,
        InvalidKingState,
        BoardPieceCountOutOfRange,
        PocketCountOutOfRange,
        InvalidPiece,
        FeatureIndexOutOfRange,
        DuplicateFeature,
        ActiveFeatureOverflow
    };

    struct Result {
        Status                                  status = Status::WrongRuleset;
        std::array<std::vector<Index>, COLOR_NB> active;
        std::size_t                             boardPieceCount = 0;
        std::size_t                             layerBucket     = 0;
        std::string                             message;

        bool ok() const noexcept { return status == Status::Success; }
    };

    static Result extract(const Position& position);
    static std::string_view status_name(Status status) noexcept;
};

static_assert(LegacyCrazyhouseFeaturesV1::BoardFeatures == 704);
static_assert(LegacyCrazyhouseFeaturesV1::PocketFeatures == 160);
static_assert(LegacyCrazyhouseFeaturesV1::KingStride == 864);
static_assert(LegacyCrazyhouseFeaturesV1::FeatureDimensions == 55'296);

}  // namespace Eval::NNUE
}  // namespace Stockfish

#endif  // NNUE_CRAZYHOUSE_LEGACY_FEATURES_H_INCLUDED
