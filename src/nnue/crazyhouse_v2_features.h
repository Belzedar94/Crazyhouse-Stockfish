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
    static constexpr std::size_t BoardRows      = BoardRoleCount * 64;
    static constexpr std::size_t PocketRows     = 70;
    static constexpr std::size_t PromotedRows   = 64;
    static constexpr std::size_t PocketOffset   = BoardRows;
    static constexpr std::size_t PromotedOffset = PocketOffset + PocketRows;
    static constexpr std::size_t Dimensions     = PromotedOffset + PromotedRows;
    static constexpr std::size_t MaximumActive  = 64 + 10 + 64;

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
        Status                                                 status = Status::INVALID_PIECE;
        std::array<std::array<Index, MaximumActive>, COLOR_NB> active{};
        std::array<std::size_t, COLOR_NB>                      size{};

        constexpr bool ok() const noexcept { return status == Status::SUCCESS; }
    };

    static Result           extract(const PhysicalStateV1& state) noexcept;
    static Result           extract(const Position& position) noexcept;
    static std::string_view status_name(Status status) noexcept;
};

class LargeFeatureInventoryV1 {
   public:
    using Index = std::uint32_t;

    static constexpr std::size_t KBoardRows      = 64 * 11 * 64;
    static constexpr std::size_t KPocketRows     = 64 * 60;
    static constexpr std::size_t KPromotedRows   = 64 * 8 * 64;
    static constexpr std::size_t KPocketOffset   = KBoardRows;
    static constexpr std::size_t KPromotedOffset = KPocketOffset + KPocketRows;
    static constexpr std::size_t KDimensions     = KPromotedOffset + KPromotedRows;

    static constexpr std::size_t GBoardRows      = 12 * 64;
    static constexpr std::size_t GPocketRows     = 60;
    static constexpr std::size_t GPromotedRows   = 8 * 64;
    static constexpr std::size_t GPocketOffset   = GBoardRows;
    static constexpr std::size_t GPromotedOffset = GPocketOffset + GPocketRows;
    static constexpr std::size_t GDimensions     = GPromotedOffset + GPromotedRows;

    static constexpr std::size_t MaximumActivePerDomain            = 48;
    static constexpr std::size_t MaximumActivePerPerspective       = 96;
    static constexpr std::size_t MaximumBothPerspectivesIncidences = 192;

    enum class Status {
        SUCCESS,
        WRONG_RULESET,
        INVALID_PIECE,
        INVALID_KING_STATE,
        PAWN_PROMOTION_RANK,
        POCKET_BOUNDS,
        PROMOTED_MASK,
        PHYSICAL_UNIT_BOUNDS,
        INDEX_OUT_OF_RANGE,
        DUPLICATE_INDEX,
        ACTIVE_OVERFLOW,
    };

    struct DomainResult {
        std::array<Index, MaximumActivePerDomain> active{};
        std::size_t                               size{};
    };

    struct PerspectiveResult {
        DomainResult k64{};
        DomainResult g1{};
    };

    struct Result {
        Status                                  status = Status::INVALID_PIECE;
        std::array<PerspectiveResult, COLOR_NB> perspective{};

        constexpr bool ok() const noexcept { return status == Status::SUCCESS; }
    };

    // The PhysicalState overload accepts the evaluator projection domain: it
    // validates piece/pocket/provenance and physical upper bounds, but ignores
    // history-only fields and does not require a full 32-unit game record.
    // Canonical DATAGEN records must pass decode_physical_record_v1() first.
    static Result           extract(const PhysicalStateV1& state) noexcept;
    static Result           extract(const Position& position) noexcept;
    static std::string_view status_name(Status status) noexcept;
};

static_assert(ScalarFeatureInventoryV1::BoardRows == 768);
static_assert(ScalarFeatureInventoryV1::PocketRows == 70);
static_assert(ScalarFeatureInventoryV1::PromotedOffset == 838);
static_assert(ScalarFeatureInventoryV1::Dimensions == 902);
static_assert(ScalarFeatureInventoryV1::MaximumActive == 138);
static_assert(LargeFeatureInventoryV1::KBoardRows == 45056);
static_assert(LargeFeatureInventoryV1::KPocketOffset == 45056);
static_assert(LargeFeatureInventoryV1::KPromotedOffset == 48896);
static_assert(LargeFeatureInventoryV1::KDimensions == 81664);
static_assert(LargeFeatureInventoryV1::GBoardRows == 768);
static_assert(LargeFeatureInventoryV1::GPocketOffset == 768);
static_assert(LargeFeatureInventoryV1::GPromotedOffset == 828);
static_assert(LargeFeatureInventoryV1::GDimensions == 1340);
static_assert(LargeFeatureInventoryV1::MaximumActivePerDomain == 48);
static_assert(LargeFeatureInventoryV1::MaximumActivePerPerspective == 96);
static_assert(LargeFeatureInventoryV1::MaximumBothPerspectivesIncidences == 192);
static_assert(WHITE == 0 && BLACK == 1 && COLOR_NB == 2);
static_assert(W_PAWN == 1 && W_KNIGHT == 2 && W_BISHOP == 3 && W_ROOK == 4 && W_QUEEN == 5
              && W_KING == 6);
static_assert(B_PAWN == 9 && B_KNIGHT == 10 && B_BISHOP == 11 && B_ROOK == 12 && B_QUEEN == 13
              && B_KING == 14);

}  // namespace Eval::NNUE::CrazyhouseV2
}  // namespace Stockfish

#endif  // NNUE_CRAZYHOUSE_V2_FEATURES_H_INCLUDED
