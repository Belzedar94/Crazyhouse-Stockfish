/*
  Crazyhouse-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the Free
  Software Foundation, either version 3 of the License, or (at your option) any
  later version.
*/

#ifndef CRAZYHOUSE_MOVE_CODEC_H_INCLUDED
#define CRAZYHOUSE_MOVE_CODEC_H_INCLUDED

#include <cassert>
#include <optional>
#include <string>
#include <string_view>

#include "types.h"

namespace Stockfish {

constexpr char drop_uci_role(PieceType pt) noexcept {
    switch (pt)
    {
    case PAWN :
        return 'P';
    case KNIGHT :
        return 'N';
    case BISHOP :
        return 'B';
    case ROOK :
        return 'R';
    case QUEEN :
        return 'Q';
    default :
        return '\0';
    }
}

constexpr PieceType drop_uci_piece_type(char role) noexcept {
    switch (role)
    {
    case 'P' :
    case 'p' :
        return PAWN;
    case 'N' :
    case 'n' :
        return KNIGHT;
    case 'B' :
    case 'b' :
        return BISHOP;
    case 'R' :
    case 'r' :
        return ROOK;
    case 'Q' :
    case 'q' :
        return QUEEN;
    default :
        return NO_PIECE_TYPE;
    }
}

inline std::string format_drop_uci(Move move) {
    assert(move.is_drop());

    const Square    to   = move.to_sq();
    const PieceType type = move.drop_piece_type();
    const char      role = drop_uci_role(type);
    assert(role != '\0');

    return {role, '@', char('a' + file_of(to)), char('1' + rank_of(to))};
}

inline std::optional<Move> parse_drop_uci(std::string_view text) noexcept {
    if (text.size() != 4 || text[1] != '@' || text[2] < 'a' || text[2] > 'h' || text[3] < '1'
        || text[3] > '8')
        return std::nullopt;

    const PieceType type = drop_uci_piece_type(text[0]);
    if (type == NO_PIECE_TYPE)
        return std::nullopt;

    const Square to = make_square(File(text[2] - 'a'), Rank(text[3] - '1'));
    return Move::make_drop(type, to);
}

}  // namespace Stockfish

#endif  // CRAZYHOUSE_MOVE_CODEC_H_INCLUDED
