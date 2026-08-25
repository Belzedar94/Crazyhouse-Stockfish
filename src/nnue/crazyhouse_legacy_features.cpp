/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_legacy_features.h"

#include <algorithm>
#include <array>
#include <utility>

#include "../bitboard.h"
#include "../position.h"
#include "../ruleset.h"

namespace Stockfish::Eval::NNUE {

namespace {

using Features = LegacyCrazyhouseFeaturesV1;

Features::Result failure(Features::Status status, std::string message) {
    Features::Result result;
    result.status  = status;
    result.message = std::move(message);
    return result;
}

constexpr std::array<PieceType, 5> PocketPieceTypes = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};

}  // namespace

LegacyCrazyhouseFeaturesV1::Result
LegacyCrazyhouseFeaturesV1::extract(const Position& position) {
    if (position.ruleset() != Ruleset::CRAZYHOUSE)
        return failure(Status::WrongRuleset,
                       "legacy Crazyhouse features require the Crazyhouse ruleset");
    if (position.count<KING>(WHITE) != 1 || position.count<KING>(BLACK) != 1)
        return failure(Status::InvalidKingState,
                       "legacy Crazyhouse features require exactly one king per side");

    const int boardCount = popcount(position.pieces());
    if (boardCount < 2 || boardCount > int(LegacyMaxPieces))
        return failure(Status::BoardPieceCountOutOfRange,
                       "legacy Crazyhouse board piece count is outside 2..32");

    Result candidate;
    candidate.status          = Status::Success;
    candidate.boardPieceCount = std::size_t(boardCount);
    candidate.layerBucket = std::min((candidate.boardPieceCount - 1) * LayerStacks
                                       / LegacyMaxPieces,
                                     LayerStacks - 1);

    for (Color perspective : {WHITE, BLACK})
    {
        std::vector<Index>& active = candidate.active[perspective];
        active.reserve(LegacyMaxPieces);

        const Square ownKing = position.square<KING>(perspective);
        if (ownKing == SQ_NONE)
            return failure(Status::InvalidKingState,
                           "legacy Crazyhouse own-king square is missing");
        const std::size_t kingBase = std::size_t(relative_square(perspective, ownKing))
                                   * KingStride;

        const auto append = [&](std::size_t rawIndex) -> Status {
            if (rawIndex >= FeatureDimensions)
                return Status::FeatureIndexOutOfRange;
            const Index index = static_cast<Index>(rawIndex);
            if (std::find(active.begin(), active.end(), index) != active.end())
                return Status::DuplicateFeature;
            if (active.size() >= MaxActiveDimensions)
                return Status::ActiveFeatureOverflow;
            active.push_back(index);
            return Status::Success;
        };

        Bitboard occupied = position.pieces();
        while (occupied)
        {
            const Square    square = pop_lsb(occupied);
            const Piece     piece  = position.piece_on(square);
            const PieceType type   = type_of(piece);
            const Color     owner  = color_of(piece);
            if (piece == NO_PIECE || type < PAWN || type > KING
                || (owner != WHITE && owner != BLACK))
                return failure(Status::InvalidPiece,
                               "legacy Crazyhouse board contains an invalid piece");

            const std::size_t ordinal = std::size_t(type - PAWN);
            const std::size_t plane = 2 * ordinal
                                    + std::size_t(type != KING && owner != perspective);
            const std::size_t index = kingBase + plane * BoardSquareCount
                                    + std::size_t(relative_square(perspective, square));
            const Status appendStatus = append(index);
            if (appendStatus != Status::Success)
                return failure(appendStatus,
                               "legacy Crazyhouse board feature validation failed");
        }

        for (Color owner : {WHITE, BLACK})
            for (PieceType type : PocketPieceTypes)
            {
                const int count = position.pocket_count(owner, type);
                if (count < 0 || count > int(PocketSlots))
                    return failure(Status::PocketCountOutOfRange,
                                   "legacy Crazyhouse pocket count is outside 0..16");

                const std::size_t ordinal = std::size_t(type - PAWN);
                const std::size_t band = 2 * ordinal + std::size_t(owner != perspective);
                for (int slot = 0; slot < count; ++slot)
                {
                    const std::size_t index = kingBase + BoardFeatures + band * PocketSlots
                                            + std::size_t(slot);
                    const Status appendStatus = append(index);
                    if (appendStatus != Status::Success)
                        return failure(appendStatus,
                                       "legacy Crazyhouse pocket feature validation failed");
                }
            }
    }

    return candidate;
}

std::string_view LegacyCrazyhouseFeaturesV1::status_name(Status status) noexcept {
    switch (status)
    {
    case Status::Success : return "Success";
    case Status::WrongRuleset : return "WrongRuleset";
    case Status::InvalidKingState : return "InvalidKingState";
    case Status::BoardPieceCountOutOfRange : return "BoardPieceCountOutOfRange";
    case Status::PocketCountOutOfRange : return "PocketCountOutOfRange";
    case Status::InvalidPiece : return "InvalidPiece";
    case Status::FeatureIndexOutOfRange : return "FeatureIndexOutOfRange";
    case Status::DuplicateFeature : return "DuplicateFeature";
    case Status::ActiveFeatureOverflow : return "ActiveFeatureOverflow";
    }
    return "Unknown";
}

}  // namespace Stockfish::Eval::NNUE
