/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_LARGE_TRANSFORM_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_LARGE_TRANSFORM_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>

#include "crazyhouse_v2_features.h"

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {

inline constexpr std::size_t LargeKTransformerLanes      = 768;
inline constexpr std::size_t LargeGTransformerLanes      = 256;
inline constexpr std::size_t LargePerspectiveOutputBytes = 512;
inline constexpr std::size_t LargeDenseInputBytes        = 1024;

using LargeKAccumulator      = std::array<std::int32_t, LargeKTransformerLanes>;
using LargeGAccumulator      = std::array<std::int32_t, LargeGTransformerLanes>;
using LargePerspectiveOutput = std::array<Byte, LargePerspectiveOutputBytes>;

struct LargeDenseInputResultV1 {
    enum class Status {
        SUCCESS,
        INVALID_SIDE_TO_MOVE
    };

    Status                                 status = Status::INVALID_SIDE_TO_MOVE;
    std::array<Byte, LargeDenseInputBytes> bytes{};

    constexpr bool ok() const noexcept { return status == Status::SUCCESS; }
};

LargePerspectiveOutput transform_large_pair_product_v1(const LargeKAccumulator& k64,
                                                       const LargeGAccumulator& g1) noexcept;

LargeDenseInputResultV1 order_large_dense_input_v1(const LargePerspectiveOutput& white,
                                                   const LargePerspectiveOutput& black,
                                                   Color sideToMove) noexcept;

static_assert(LargeKTransformerLanes / 2 + LargeGTransformerLanes / 2
              == LargePerspectiveOutputBytes);
static_assert(LargePerspectiveOutputBytes * 2 == LargeDenseInputBytes);

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2

#endif  // NNUE_CRAZYHOUSE_V2_LARGE_TRANSFORM_H_INCLUDED
