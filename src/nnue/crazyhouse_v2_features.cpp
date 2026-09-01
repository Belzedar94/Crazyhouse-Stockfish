/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_features.h"

#include <algorithm>
#include <array>

#include "../position.h"
#include "../ruleset.h"

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {
namespace {

using Inventory      = ScalarFeatureInventoryV1;
using LargeInventory = LargeFeatureInventoryV1;

constexpr std::array<std::size_t, 5> PocketTypeBase = {0, 34, 44, 54, 64};
constexpr std::array<std::size_t, 5> PocketWidths   = {17, 5, 5, 5, 3};
constexpr std::array<Byte, 10>       PocketMaximums = {16, 4, 4, 4, 2, 16, 4, 4, 4, 2};

constexpr bool valid_piece_code(Byte code) noexcept {
    return code == 0 || (code >= 1 && code <= 6) || (code >= 9 && code <= 14);
}

constexpr Byte          piece_type(Byte code) noexcept { return Byte(code & 7U); }
constexpr Byte          piece_owner(Byte code) noexcept { return Byte(code >> 3U); }
constexpr std::uint64_t square_bit(unsigned square) noexcept { return std::uint64_t{1} << square; }
constexpr unsigned      orient_square(unsigned perspective, unsigned square) noexcept {
    return perspective == 0 ? square : square ^ 56U;
}

Inventory::Result failure(Inventory::Status status) noexcept {
    Inventory::Result result;
    result.status = status;
    return result;
}

Inventory::Result extract_state(const PhysicalStateV1& state) noexcept {
    unsigned      whiteKings        = 0;
    unsigned      blackKings        = 0;
    std::uint64_t occupied          = 0;
    std::uint64_t forbiddenPromoted = 0;
    for (unsigned square = 0; square < 64; ++square)
    {
        const Byte code = state.board[square];
        if (!valid_piece_code(code))
            return failure(Inventory::Status::INVALID_PIECE);
        if (code == 0)
            continue;
        occupied |= square_bit(square);
        const Byte type = piece_type(code);
        if (type == 6)
            piece_owner(code) == 0 ? ++whiteKings : ++blackKings;
        if (type == 1 && (square / 8 == 0 || square / 8 == 7))
            return failure(Inventory::Status::PAWN_PROMOTION_RANK);
        if (type == 1 || type == 6)
            forbiddenPromoted |= square_bit(square);
    }
    if (whiteKings != 1 || blackKings != 1)
        return failure(Inventory::Status::INVALID_KING_STATE);
    if ((state.promotedMask & ~occupied) != 0 || (state.promotedMask & forbiddenPromoted) != 0)
        return failure(Inventory::Status::PROMOTED_MASK);
    for (std::size_t index = 0; index < state.pockets.size(); ++index)
        if (state.pockets[index] > PocketMaximums[index])
            return failure(Inventory::Status::POCKET_BOUNDS);

    Inventory::Result candidate;
    candidate.status = Inventory::Status::SUCCESS;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        auto append = [&](std::size_t rawIndex) -> Inventory::Status {
            if (rawIndex >= Inventory::Dimensions)
                return Inventory::Status::INDEX_OUT_OF_RANGE;
            if (candidate.size[perspective] >= Inventory::MaximumActive)
                return Inventory::Status::ACTIVE_OVERFLOW;
            const Inventory::Index index = static_cast<Inventory::Index>(rawIndex);
            const auto             first = candidate.active[perspective].begin();
            if (std::find(first, first + static_cast<std::ptrdiff_t>(candidate.size[perspective]),
                          index)
                != first + static_cast<std::ptrdiff_t>(candidate.size[perspective]))
                return Inventory::Status::DUPLICATE_INDEX;
            candidate.active[perspective][candidate.size[perspective]++] = index;
            return Inventory::Status::SUCCESS;
        };

        for (unsigned square = 0; square < 64; ++square)
        {
            const Byte code = state.board[square];
            if (code == 0)
                continue;
            const std::size_t plane =
              2 * std::size_t(piece_type(code) - 1) + std::size_t(piece_owner(code) != perspective);
            const Inventory::Status status =
              append(plane * 64 + orient_square(perspective, square));
            if (status != Inventory::Status::SUCCESS)
                return failure(status);
        }

        for (std::size_t type = 0; type < 5; ++type)
            for (unsigned relativeOwner = 0; relativeOwner < 2; ++relativeOwner)
            {
                const unsigned    absoluteOwner = perspective ^ relativeOwner;
                const Byte        count         = state.pockets[absoluteOwner * 5 + type];
                const std::size_t row           = Inventory::PocketOffset + PocketTypeBase[type]
                                      + relativeOwner * PocketWidths[type] + count;
                const Inventory::Status status = append(row);
                if (status != Inventory::Status::SUCCESS)
                    return failure(status);
            }

        for (unsigned square = 0; square < 64; ++square)
            if (state.promotedMask & square_bit(square))
            {
                const Inventory::Status status =
                  append(Inventory::PromotedOffset + orient_square(perspective, square));
                if (status != Inventory::Status::SUCCESS)
                    return failure(status);
            }
    }
    return candidate;
}

unsigned population(std::uint64_t value) noexcept {
    unsigned count = 0;
    while (value)
    {
        value &= value - 1;
        ++count;
    }
    return count;
}

LargeInventory::Result large_failure(LargeInventory::Status status) noexcept {
    LargeInventory::Result result;
    result.status = status;
    return result;
}

LargeInventory::Result extract_large_state(const PhysicalStateV1& state) noexcept {
    unsigned                whiteKings = 0;
    unsigned                blackKings = 0;
    std::array<unsigned, 2> kingSquares{};
    std::uint64_t           occupied          = 0;
    std::uint64_t           forbiddenPromoted = 0;
    std::array<unsigned, 6> unpromotedUnits{};

    for (unsigned square = 0; square < 64; ++square)
    {
        const Byte code = state.board[square];
        if (!valid_piece_code(code))
            return large_failure(LargeInventory::Status::INVALID_PIECE);
        if (code == 0)
            continue;

        occupied |= square_bit(square);
        const Byte type  = piece_type(code);
        const Byte owner = piece_owner(code);
        if (type == 6)
        {
            owner == 0 ? ++whiteKings : ++blackKings;
            kingSquares[owner] = square;
        }
        if (type == 1 && (square / 8 == 0 || square / 8 == 7))
            return large_failure(LargeInventory::Status::PAWN_PROMOTION_RANK);
        if (type == 1 || type == 6)
            forbiddenPromoted |= square_bit(square);
        if (type <= 5 && !(state.promotedMask & square_bit(square)))
            ++unpromotedUnits[type];
    }

    if (whiteKings != 1 || blackKings != 1)
        return large_failure(LargeInventory::Status::INVALID_KING_STATE);
    if ((state.promotedMask & ~occupied) != 0 || (state.promotedMask & forbiddenPromoted) != 0)
        return large_failure(LargeInventory::Status::PROMOTED_MASK);

    for (std::size_t index = 0; index < state.pockets.size(); ++index)
        if (state.pockets[index] > PocketMaximums[index])
            return large_failure(LargeInventory::Status::POCKET_BOUNDS);

    unsigned pawnUnits =
      unpromotedUnits[1] + population(state.promotedMask) + state.pockets[0] + state.pockets[5];
    if (pawnUnits > 16)
        return large_failure(LargeInventory::Status::PHYSICAL_UNIT_BOUNDS);

    constexpr std::array<unsigned, 4> OtherMaximums = {4, 4, 4, 2};
    unsigned                          physicalUnits = 2 + pawnUnits;
    for (unsigned type = 2; type <= 5; ++type)
    {
        const unsigned units =
          unpromotedUnits[type] + state.pockets[type - 1] + state.pockets[5 + type - 1];
        if (units > OtherMaximums[type - 2])
            return large_failure(LargeInventory::Status::PHYSICAL_UNIT_BOUNDS);
        physicalUnits += units;
    }
    if (physicalUnits > 32)
        return large_failure(LargeInventory::Status::PHYSICAL_UNIT_BOUNDS);

    LargeInventory::Result candidate;
    candidate.status = LargeInventory::Status::SUCCESS;
    for (const Byte count : state.pockets)
        candidate.totalPocketUnits += count;
    for (unsigned perspective = 0; perspective < COLOR_NB; ++perspective)
    {
        const unsigned kingBucket = orient_square(perspective, kingSquares[perspective]);

        auto append = [&](LargeInventory::DomainResult& domain, std::size_t dimensions,
                          std::size_t rawIndex) -> LargeInventory::Status {
            if (rawIndex >= dimensions)
                return LargeInventory::Status::INDEX_OUT_OF_RANGE;
            if (domain.size >= LargeInventory::MaximumActivePerDomain)
                return LargeInventory::Status::ACTIVE_OVERFLOW;
            const auto index = static_cast<LargeInventory::Index>(rawIndex);
            const auto first = domain.active.begin();
            const auto last  = first + static_cast<std::ptrdiff_t>(domain.size);
            if (std::find(first, last, index) != last)
                return LargeInventory::Status::DUPLICATE_INDEX;
            domain.active[domain.size++] = index;
            return LargeInventory::Status::SUCCESS;
        };

        auto append_both = [&](std::size_t kIndex, std::size_t gIndex) -> LargeInventory::Status {
            LargeInventory::Status status =
              append(candidate.perspective[perspective].k64, LargeInventory::KDimensions, kIndex);
            if (status != LargeInventory::Status::SUCCESS)
                return status;
            return append(candidate.perspective[perspective].g1, LargeInventory::GDimensions,
                          gIndex);
        };

        for (unsigned square = 0; square < 64; ++square)
        {
            const Byte code = state.board[square];
            if (code == 0)
                continue;
            const unsigned    type          = piece_type(code);
            const unsigned    relativeOwner = unsigned(piece_owner(code) != perspective);
            const unsigned    oriented      = orient_square(perspective, square);
            const std::size_t kPlane = type == 6 ? 10 : 2 * std::size_t(type - 1) + relativeOwner;
            const std::size_t gPlane = 2 * std::size_t(type - 1) + relativeOwner;
            const LargeInventory::Status status =
              append_both((kingBucket * 11 + kPlane) * 64 + oriented, gPlane * 64 + oriented);
            if (status != LargeInventory::Status::SUCCESS)
                return large_failure(status);
        }

        constexpr std::array<std::size_t, 5> SlotPrefixes = {0, 16, 20, 24, 28};
        for (std::size_t type = 0; type < 5; ++type)
            for (unsigned relativeOwner = 0; relativeOwner < 2; ++relativeOwner)
            {
                const unsigned absoluteOwner = perspective ^ relativeOwner;
                const Byte     count         = state.pockets[absoluteOwner * 5 + type];
                for (unsigned slot = 0; slot < count; ++slot)
                {
                    const std::size_t pocketPlane = relativeOwner * 30 + SlotPrefixes[type] + slot;
                    const LargeInventory::Status status =
                      append_both(LargeInventory::KPocketOffset + kingBucket * 60 + pocketPlane,
                                  LargeInventory::GPocketOffset + pocketPlane);
                    if (status != LargeInventory::Status::SUCCESS)
                        return large_failure(status);
                }
            }

        for (unsigned square = 0; square < 64; ++square)
            if (state.promotedMask & square_bit(square))
            {
                const Byte     code          = state.board[square];
                const unsigned type          = piece_type(code);
                const unsigned relativeOwner = unsigned(piece_owner(code) != perspective);
                const unsigned promotedPlane = relativeOwner * 4 + type - 2;
                const unsigned oriented      = orient_square(perspective, square);
                const LargeInventory::Status status =
                  append_both(LargeInventory::KPromotedOffset
                                + ((kingBucket * 8 + promotedPlane) * 64) + oriented,
                              LargeInventory::GPromotedOffset + promotedPlane * 64 + oriented);
                if (status != LargeInventory::Status::SUCCESS)
                    return large_failure(status);
            }
    }
    return candidate;
}

}  // namespace

ScalarFeatureInventoryV1::Result
ScalarFeatureInventoryV1::extract(const PhysicalStateV1& state) noexcept {
    return extract_state(state);
}

ScalarFeatureInventoryV1::Result
ScalarFeatureInventoryV1::extract(const Position& position) noexcept {
    if (position.ruleset() != Ruleset::CRAZYHOUSE)
        return failure(Status::WRONG_RULESET);

    PhysicalStateV1 state;
    for (unsigned square = 0; square < 64; ++square)
        state.board[square] = static_cast<Byte>(position.piece_on(Square(square)));
    state.promotedMask                       = position.promoted_pieces();
    constexpr std::array<PieceType, 5> Types = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    for (Color owner : {WHITE, BLACK})
        for (std::size_t type = 0; type < Types.size(); ++type)
        {
            const int count = position.pocket_count(owner, Types[type]);
            if (count < 0 || count > 255)
                return failure(Status::POCKET_BOUNDS);
            state.pockets[std::size_t(owner) * 5 + type] = static_cast<Byte>(count);
        }
    return extract_state(state);
}

std::string_view ScalarFeatureInventoryV1::status_name(Status status) noexcept {
    switch (status)
    {
    case Status::SUCCESS :
        return "SUCCESS";
    case Status::WRONG_RULESET :
        return "WRONG_RULESET";
    case Status::INVALID_PIECE :
        return "INVALID_PIECE";
    case Status::INVALID_KING_STATE :
        return "INVALID_KING_STATE";
    case Status::PAWN_PROMOTION_RANK :
        return "PAWN_PROMOTION_RANK";
    case Status::POCKET_BOUNDS :
        return "POCKET_BOUNDS";
    case Status::PROMOTED_MASK :
        return "PROMOTED_MASK";
    case Status::INDEX_OUT_OF_RANGE :
        return "INDEX_OUT_OF_RANGE";
    case Status::DUPLICATE_INDEX :
        return "DUPLICATE_INDEX";
    case Status::ACTIVE_OVERFLOW :
        return "ACTIVE_OVERFLOW";
    }
    return "UNKNOWN";
}

LargeFeatureInventoryV1::Result
LargeFeatureInventoryV1::extract(const PhysicalStateV1& state) noexcept {
    return extract_large_state(state);
}

LargeFeatureInventoryV1::Result
LargeFeatureInventoryV1::extract(const Position& position) noexcept {
    if (position.ruleset() != Ruleset::CRAZYHOUSE)
        return large_failure(Status::WRONG_RULESET);

    PhysicalStateV1 state;
    for (unsigned square = 0; square < 64; ++square)
        state.board[square] = static_cast<Byte>(position.piece_on(Square(square)));
    state.promotedMask                       = position.promoted_pieces();
    constexpr std::array<PieceType, 5> Types = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    for (Color owner : {WHITE, BLACK})
        for (std::size_t type = 0; type < Types.size(); ++type)
        {
            const int count = position.pocket_count(owner, Types[type]);
            if (count < 0 || count > 255)
                return large_failure(Status::POCKET_BOUNDS);
            state.pockets[std::size_t(owner) * 5 + type] = static_cast<Byte>(count);
        }
    return extract_large_state(state);
}

std::string_view LargeFeatureInventoryV1::status_name(Status status) noexcept {
    switch (status)
    {
    case Status::SUCCESS :
        return "SUCCESS";
    case Status::WRONG_RULESET :
        return "WRONG_RULESET";
    case Status::INVALID_PIECE :
        return "INVALID_PIECE";
    case Status::INVALID_KING_STATE :
        return "INVALID_KING_STATE";
    case Status::PAWN_PROMOTION_RANK :
        return "PAWN_PROMOTION_RANK";
    case Status::POCKET_BOUNDS :
        return "POCKET_BOUNDS";
    case Status::PROMOTED_MASK :
        return "PROMOTED_MASK";
    case Status::PHYSICAL_UNIT_BOUNDS :
        return "PHYSICAL_UNIT_BOUNDS";
    case Status::INDEX_OUT_OF_RANGE :
        return "INDEX_OUT_OF_RANGE";
    case Status::DUPLICATE_INDEX :
        return "DUPLICATE_INDEX";
    case Status::ACTIVE_OVERFLOW :
        return "ACTIVE_OVERFLOW";
    }
    return "UNKNOWN";
}

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2
