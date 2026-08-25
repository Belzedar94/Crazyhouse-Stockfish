/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef CRAZYHOUSE_PROFILE_H_INCLUDED
#define CRAZYHOUSE_PROFILE_H_INCLUDED

#include <cstdint>
#include <string_view>

namespace Stockfish::CrazyhouseProfile {

inline constexpr std::string_view Id = "LICHESS_CRAZYHOUSE_2026_08_12";
inline constexpr std::string_view Sha256 =
  "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68";
inline constexpr std::string_view Token =
  "LICHESS_CRAZYHOUSE_2026_08_12@"
  "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68";

enum class TokenStatus : std::uint8_t {
    Valid,
    Missing,
    UnknownId,
    HashMismatch
};

constexpr TokenStatus classify(std::string_view token) noexcept {
    if (token.empty())
        return TokenStatus::Missing;
    if (token == Token)
        return TokenStatus::Valid;

    const auto separator = token.find('@');
    const auto id = token.substr(0, separator);
    return id == Id ? TokenStatus::HashMismatch : TokenStatus::UnknownId;
}

}  // namespace Stockfish::CrazyhouseProfile

#endif  // CRAZYHOUSE_PROFILE_H_INCLUDED
