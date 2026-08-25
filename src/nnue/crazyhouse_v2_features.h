/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_FEATURES_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_FEATURES_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

#include "crazyhouse_v2_physical.h"
#include "../types.h"

namespace Stockfish {

class Position;

namespace Eval::NNUE::CrazyhouseV2 {

class ScalarFeatureInventoryV1 {
   public:
    using Index = std::uint32_t;

    static constexpr std::size_t BoardRoleCount = 12;
    static constexpr std::size_t BoardRows = BoardRoleCount * 64;
    static constexpr std::size_t PocketRows = 70;
    static constexpr std::size_t PromotedRows = 64;
    static constexpr std::size_t PocketOffset = BoardRows;
    static constexpr std::size_t PromotedOffset = PocketOffset + PocketRows;
    static constexpr std::size_t Dimensions = PromotedOffset + PromotedRows;
    static constexpr std::size_t MaximumActive = 64 + 10 + 64;

    enum class Status {
        SUCCESS,
        WRONG_RULESET,
        INVALID_PIECE,
        INVALID_KING_STATE,
        PAWN_PROMOTION_RANK,
        POCKET_BOUNDS,
        PROMOTED_MASK,
        INDEX_OUT_OF_RANGE,
        DUPLICATE_INDEX,
        ACTIVE_OVERFLOW,
    };

    struct Result {
        Status                                        status = Status::INVALID_PIECE;
        std::array<std::array<Index, MaximumActive>, COLOR_NB> active{};
        std::array<std::size_t, COLOR_NB>                       size{};

        constexpr bool ok() const noexcept { return status == Status::SUCCESS; }
    };

    static Result extract(const PhysicalStateV1& state) noexcept;
    static Result extract(const Position& position) noexcept;
    static std::string_view status_name(Status status) noexcept;
};

static_assert(ScalarFeatureInventoryV1::BoardRows == 768);
static_assert(ScalarFeatureInventoryV1::PocketRows == 70);
static_assert(ScalarFeatureInventoryV1::PromotedOffset == 838);
static_assert(ScalarFeatureInventoryV1::Dimensions == 902);
static_assert(ScalarFeatureInventoryV1::MaximumActive == 138);

}  // namespace Eval::NNUE::CrazyhouseV2
}  // namespace Stockfish

#endif  // NNUE_CRAZYHOUSE_V2_FEATURES_H_INCLUDED
