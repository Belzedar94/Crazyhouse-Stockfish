/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_large_transform.h"

#include <algorithm>

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {
namespace {

template<std::size_t Lanes>
void transform_domain(const std::array<std::int32_t, Lanes>& accumulator, Byte* output) noexcept {
    static_assert(Lanes % 2 == 0);
    constexpr std::size_t Half = Lanes / 2;
    for (std::size_t index = 0; index < Half; ++index)
    {
        const auto left  = std::clamp<std::int32_t>(accumulator[index], 0, 255);
        const auto right = std::clamp<std::int32_t>(accumulator[index + Half], 0, 255);
        output[index]    = static_cast<Byte>(std::uint32_t(left * right) / 512U);
    }
}

}  // namespace

LargePerspectiveOutput transform_large_pair_product_v1(const LargeKAccumulator& k64,
                                                       const LargeGAccumulator& g1) noexcept {
    LargePerspectiveOutput output{};
    transform_domain(k64, output.data());
    transform_domain(g1, output.data() + LargeKTransformerLanes / 2);
    return output;
}

LargeDenseInputResultV1 order_large_dense_input_v1(const LargePerspectiveOutput& white,
                                                   const LargePerspectiveOutput& black,
                                                   Color sideToMove) noexcept {
    if (sideToMove != WHITE && sideToMove != BLACK)
        return {};

    LargeDenseInputResultV1 result;
    result.status      = LargeDenseInputResultV1::Status::SUCCESS;
    const auto& first  = sideToMove == WHITE ? white : black;
    const auto& second = sideToMove == WHITE ? black : white;
    std::copy(first.begin(), first.end(), result.bytes.begin());
    std::copy(second.begin(), second.end(),
              result.bytes.begin() + static_cast<std::ptrdiff_t>(first.size()));
    return result;
}

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2
