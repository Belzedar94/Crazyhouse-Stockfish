/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
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

#ifndef MOVEGEN_H_INCLUDED
#define MOVEGEN_H_INCLUDED

#include <algorithm>  // IWYU pragma: keep
#include <new>

#include "crazyhouse_move_buffer.h"
#include "misc.h"
#include "types.h"

namespace Stockfish {

class Position;

bool uses_growable_move_list_storage(const Position& pos) noexcept;
bool uses_growable_move_picker_storage(const Position& pos) noexcept;

enum GenType {
    CAPTURES,
    QUIETS,
    EVASIONS,
    NON_EVASIONS,
    LEGAL
};

struct ExtMove: public Move {
    int value;

    void operator=(Move m) { data = m.raw(); }

    // Inhibit unwanted implicit conversions to Move
    // with an ambiguity that yields to a compile error.
    operator float() const = delete;
};

inline bool operator<(const ExtMove& f, const ExtMove& s) { return f.value < s.value; }

template<GenType>
Move* generate(const Position& pos, Move* moveList);

using GrowableMoveBuffer = CrazyhouseMoveBuffer<Move, MAX_MOVES>;

template<GenType>
void generate(const Position& pos, GrowableMoveBuffer& moveList);

// FixedMoveList preserves the official Stockfish storage and generation path.
template<GenType T>
struct FixedMoveList {

    explicit FixedMoveList(const Position& pos) :
        last(generate<T>(pos, moveList)) {}
    const Move* begin() const { return moveList; }
    const Move* end() const { return last; }
    usize       size() const { return last - moveList; }
    bool        contains(Move move) const { return std::find(begin(), end(), move) != end(); }

   private:
    Move moveList[MAX_MOVES], *last;
};

// GrowableMoveList exercises the checked append path without asserting any
// numeric Crazyhouse ceiling.
template<GenType T>
struct GrowableMoveList {

    explicit GrowableMoveList(const Position& pos) { generate<T>(pos, moveList); }
    const Move* begin() const { return moveList.begin(); }
    const Move* end() const { return moveList.end(); }
    usize       size() const { return moveList.size(); }
    bool        contains(Move move) const { return std::find(begin(), end(), move) != end(); }

   private:
    GrowableMoveBuffer moveList;
};

// RulesetMoveList owns exactly one live implementation. Orthodox chess keeps
// Stockfish's fixed pointer path; Crazyhouse selects checked growable storage.
template<GenType T>
class RulesetMoveList {

    using Fixed    = FixedMoveList<T>;
    using Growable = GrowableMoveList<T>;

    union Storage {
        Storage() noexcept {}
        ~Storage() {}

        Fixed    fixed;
        Growable growable;
    } storage;

   public:
    explicit RulesetMoveList(const Position& pos) :
        growable_(uses_growable_move_list_storage(pos)) {
        if (growable_)
            ::new (static_cast<void*>(&storage.growable)) Growable(pos);
        else
            ::new (static_cast<void*>(&storage.fixed)) Fixed(pos);
    }

    RulesetMoveList(const RulesetMoveList&)            = delete;
    RulesetMoveList& operator=(const RulesetMoveList&) = delete;
    RulesetMoveList(RulesetMoveList&&)                 = delete;
    RulesetMoveList& operator=(RulesetMoveList&&)      = delete;

    ~RulesetMoveList() {
        if (growable_)
            storage.growable.~Growable();
        else
            storage.fixed.~Fixed();
    }

    const Move* begin() const {
        return growable_ ? storage.growable.begin() : storage.fixed.begin();
    }
    const Move* end() const { return growable_ ? storage.growable.end() : storage.fixed.end(); }
    usize       size() const { return growable_ ? storage.growable.size() : storage.fixed.size(); }
    bool        contains(Move move) const {
        return growable_ ? storage.growable.contains(move) : storage.fixed.contains(move);
    }
    bool is_growable() const noexcept { return growable_; }

   private:
    bool growable_;
};

template<GenType T>
using MoveList = RulesetMoveList<T>;

}  // namespace Stockfish

#endif  // #ifndef MOVEGEN_H_INCLUDED
