/*
  Crazyhouse-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef CRAZYHOUSE_SEARCH_CAPACITY_H_INCLUDED
#define CRAZYHOUSE_SEARCH_CAPACITY_H_INCLUDED

#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>

#include "types.h"

namespace Stockfish::Search {

// Preserve Stockfish's hot fixed lookup while providing the same frozen
// formula for Crazyhouse move ordinals beyond the orthodox move capacity.
// MAX_MOVES remains an inline optimization, never a Crazyhouse legal-list cap.
class MoveCountReductionTable {
   public:
    void initialize() noexcept {
        for (usize index = 1; index < values.size(); ++index)
            values[index] = formula(int(index));
    }

    int value(int index) const noexcept {
        if (index <= 0)
            fail("nonpositive index");

        return usize(index) < values.size() ? values[usize(index)] : formula(index);
    }

    static int formula(int index) noexcept {
        if (index <= 0)
            fail("nonpositive formula index");

        return int(2872 / 128.0 * std::log(index));
    }

   private:
    [[noreturn]] static void fail(const char* reason) noexcept {
        std::fputs("FATAL MoveCountReductionTable: ", stderr);
        std::fputs(reason, stderr);
        std::fputc('\n', stderr);
        std::abort();
    }

    std::array<int, MAX_MOVES> values{};
};

}  // namespace Stockfish::Search

#endif  // CRAZYHOUSE_SEARCH_CAPACITY_H_INCLUDED
