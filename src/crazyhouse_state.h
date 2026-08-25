/*
  Crazyhouse-Stockfish, a focused Crazyhouse engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.

  Crazyhouse-Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
  or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
  more details.

  You should have received a copy of the GNU General Public License along with
  Crazyhouse-Stockfish. If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef CRAZYHOUSE_STATE_H_INCLUDED
#define CRAZYHOUSE_STATE_H_INCLUDED

#include <array>
#include <cstdint>

#include "types.h"

namespace Stockfish {

namespace Crazyhouse {

constexpr usize POCKET_TYPE_NB = 5;

inline constexpr std::array<PieceType, POCKET_TYPE_NB> PocketPieceTypes = {
  PAWN, KNIGHT, BISHOP, ROOK, QUEEN};

constexpr int pocket_index(PieceType pt) {
    switch (pt)
    {
    case PAWN : return 0;
    case KNIGHT : return 1;
    case BISHOP : return 2;
    case ROOK : return 3;
    case QUEEN : return 4;
    default : return -1;
    }
}

constexpr int max_pocket_count(PieceType pt) {
    switch (pt)
    {
    case PAWN : return 16;
    case KNIGHT :
    case BISHOP :
    case ROOK : return 4;
    case QUEEN : return 2;
    default : return -1;
    }
}

}  // namespace Crazyhouse

struct PocketCounts {
    std::uint8_t count[COLOR_NB][Crazyhouse::POCKET_TYPE_NB];
};

struct CrazyhouseState {
    PocketCounts pockets;
    Bitboard     promoted;
    Key          pocketKey;
    Key          promotedKey;
};

}  // namespace Stockfish

#endif  // #ifndef CRAZYHOUSE_STATE_H_INCLUDED
