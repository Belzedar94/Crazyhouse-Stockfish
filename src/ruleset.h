/*
  Crazyhouse-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the Free
  Software Foundation, either version 3 of the License, or (at your option) any
  later version.
*/

#ifndef RULESET_H_INCLUDED
#define RULESET_H_INCLUDED

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <optional>
#include <string_view>

namespace Stockfish {

// Frozen internal identity. New variants must never reuse an existing value.
enum class Ruleset : std::uint8_t {
    CHESS      = 0,
    CRAZYHOUSE = 1
};

constexpr bool is_valid_ruleset(Ruleset ruleset) noexcept {
    return ruleset == Ruleset::CHESS || ruleset == Ruleset::CRAZYHOUSE;
}

[[noreturn]] inline void fail_invalid_ruleset(const char* operation) noexcept {
    std::fputs("FATAL Ruleset: invalid value in ", stderr);
    std::fputs(operation, stderr);
    std::fputc('\n', stderr);
    std::abort();
}

inline Ruleset validate_ruleset(Ruleset ruleset, const char* operation) noexcept {
    if (!is_valid_ruleset(ruleset))
        fail_invalid_ruleset(operation);
    return ruleset;
}

inline std::string_view ruleset_name(Ruleset ruleset) noexcept {
    switch (ruleset)
    {
    case Ruleset::CHESS :
        return "chess";
    case Ruleset::CRAZYHOUSE :
        return "crazyhouse";
    }

    fail_invalid_ruleset("ruleset_name");
}

inline std::optional<Ruleset> ruleset_from_uci(std::string_view token) noexcept {
    if (token == "chess")
        return Ruleset::CHESS;
    if (token == "crazyhouse")
        return Ruleset::CRAZYHOUSE;
    return std::nullopt;
}

inline bool uses_growable_move_storage(Ruleset ruleset) noexcept {
    switch (ruleset)
    {
    case Ruleset::CHESS :
        return false;
    case Ruleset::CRAZYHOUSE :
        return true;
    }

    fail_invalid_ruleset("uses_growable_move_storage");
}

}  // namespace Stockfish

#endif  // RULESET_H_INCLUDED
