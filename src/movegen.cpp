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

#include "movegen.h"

#include <cassert>
#include <initializer_list>

#include "attacks.h"
#include "bitboard.h"
#include "position.h"

#if defined(USE_AVX512ICL)
    #include <array>
    #include <algorithm>
    #include <immintrin.h>
#endif

namespace Stockfish {

bool uses_growable_move_list_storage(const Position& pos) noexcept {
#if defined(CRAZYHOUSE_GROWABLE_MOVE_LIST_CONTROL)
    static_cast<void>(pos);
    return true;
#else
    return uses_growable_move_storage(pos.ruleset());
#endif
}

bool uses_growable_move_picker_storage(const Position& pos) noexcept {
#if defined(CRAZYHOUSE_GROWABLE_MOVE_PICKER_CONTROL)
    static_cast<void>(pos);
    return true;
#else
    return uses_growable_move_storage(pos.ruleset());
#endif
}

namespace {

class FixedMoveSink {
   public:
    explicit FixedMoveSink(Move* initial) noexcept :
        first(initial),
        current(initial) {}

    void push(Move move) noexcept { *current++ = move; }
    void advance(usize count) noexcept { current += count; }

    Move* end() const noexcept { return current; }
    usize size() const noexcept { return current - first; }

    Move& operator[](usize index) noexcept { return first[index]; }
    Move& back() noexcept { return current[-1]; }

    void pop_back() noexcept {
        assert(current != first);
        --current;
    }

   private:
    Move* first;
    Move* current;
};

class GrowableMoveSink {
   public:
    explicit GrowableMoveSink(GrowableMoveBuffer& targetStorage) noexcept :
        storage(targetStorage) {}

    void push(Move move) noexcept { storage.push_back(move); }

    usize size() const noexcept { return storage.size(); }

    Move& operator[](usize index) noexcept { return storage[index]; }
    Move& back() noexcept { return storage.back(); }

    void pop_back() noexcept { storage.pop_back(); }

   private:
    GrowableMoveBuffer& storage;
};

template<Direction offset, typename Sink>
inline void splat_pawn_moves(Sink& sink, Bitboard to_bb) {
    while (to_bb)
    {
        Square to = pop_lsb(to_bb);
        sink.push(Move(to - offset, to));
    }
}

template<typename Sink>
inline void splat_moves(Sink& sink, Square from, Bitboard to_bb) {
    while (to_bb)
        sink.push(Move(from, pop_lsb(to_bb)));
}

#if defined(USE_AVX512ICL)

template<Direction offset>
inline void splat_pawn_moves(FixedMoveSink& sink, Bitboard to_bb) {
    assert(popcount(to_bb) <= 8);  // <= 8 pawns per side

    const __m128i toSquares =
      _mm_cvtepi8_epi16(_mm512_castsi512_si128(_mm512_maskz_compress_epi8(to_bb, AllSquares)));
    const __m128i fromSquares = _mm_subs_epi16(toSquares, _mm_set1_epi16(offset));
    const __m128i moves       = _mm_or_si128(_mm_slli_epi16(fromSquares, Move::FromSqShift),
                                             _mm_slli_epi16(toSquares, Move::ToSqShift));

    _mm_storeu_si128(reinterpret_cast<__m128i*>(sink.end()), moves);
    sink.advance(popcount(to_bb));
}

inline void splat_moves(FixedMoveSink& sink, Square from, Bitboard to_bb) {
    assert(popcount(to_bb) <= 32);  // Q can attack up to 27 squares

    const __m512i fromVec = _mm512_set1_epi16(Move(from, SQUARE_ZERO).raw());
    const __m512i toSquares =
      _mm512_cvtepi8_epi16(_mm512_castsi512_si256(_mm512_maskz_compress_epi8(to_bb, AllSquares)));
    const __m512i moves = _mm512_or_si512(fromVec, _mm512_slli_epi16(toSquares, Move::ToSqShift));

    _mm512_storeu_si512(sink.end(), moves);
    sink.advance(popcount(to_bb));
}

#endif

template<GenType Type, Direction D, bool Enemy, typename Sink>
void make_promotions(Sink& sink, [[maybe_unused]] Square to) {

    constexpr bool          all  = Type == EVASIONS || Type == NON_EVASIONS;
    [[maybe_unused]] Square from = to - D;

    if constexpr (Type == CAPTURES || all)
        sink.push(Move::make<PROMOTION>(from, to, QUEEN));

    if constexpr ((Type == CAPTURES && Enemy) || (Type == QUIETS && !Enemy) || all)
    {
        sink.push(Move::make<PROMOTION>(from, to, ROOK));
        sink.push(Move::make<PROMOTION>(from, to, BISHOP));
        sink.push(Move::make<PROMOTION>(from, to, KNIGHT));
    }
}


template<Color Us, GenType Type, typename Sink>
void generate_pawn_moves(const Position& pos, Sink& sink, Bitboard target) {

    constexpr Color     Them     = ~Us;
    constexpr Bitboard  TRank7BB = (Us == WHITE ? Rank7BB : Rank2BB);
    constexpr Bitboard  TRank3BB = (Us == WHITE ? Rank3BB : Rank6BB);
    constexpr Direction Up       = pawn_push(Us);
    constexpr Direction UpRight  = (Us == WHITE ? NORTH_EAST : SOUTH_WEST);
    constexpr Direction UpLeft   = (Us == WHITE ? NORTH_WEST : SOUTH_EAST);

    const Bitboard emptySquares = ~pos.pieces();
    const Bitboard enemies      = Type == EVASIONS ? pos.checkers() : pos.pieces(Them);

    Bitboard pawnsOn7    = pos.pieces(Us, PAWN) & TRank7BB;
    Bitboard pawnsNotOn7 = pos.pieces(Us, PAWN) & ~TRank7BB;

    // Single and double pawn pushes, no promotions
    if constexpr (Type != CAPTURES)
    {
        Bitboard b1 = shift(pawnsNotOn7, Up) & emptySquares;
        Bitboard b2 = shift(b1 & TRank3BB, Up) & emptySquares;

        if constexpr (Type == EVASIONS)  // Consider only blocking squares
        {
            b1 &= target;
            b2 &= target;
        }

        splat_pawn_moves<Up>(sink, b1);
        splat_pawn_moves<Up + Up>(sink, b2);
    }

    // Promotions and underpromotions
    if (pawnsOn7)
    {
        Bitboard b1 = shift(pawnsOn7, UpRight) & enemies;
        Bitboard b2 = shift(pawnsOn7, UpLeft) & enemies;
        Bitboard b3 = shift(pawnsOn7, Up) & emptySquares;

        if constexpr (Type == EVASIONS)
            b3 &= target;

        while (b1)
            make_promotions<Type, UpRight, true>(sink, pop_lsb(b1));

        while (b2)
            make_promotions<Type, UpLeft, true>(sink, pop_lsb(b2));

        while (b3)
            make_promotions<Type, Up, false>(sink, pop_lsb(b3));
    }

    // Standard and en passant captures
    if constexpr (Type == CAPTURES || Type == EVASIONS || Type == NON_EVASIONS)
    {
        Bitboard b1 = shift(pawnsNotOn7, UpRight) & enemies;
        Bitboard b2 = shift(pawnsNotOn7, UpLeft) & enemies;

        splat_pawn_moves<UpRight>(sink, b1);
        splat_pawn_moves<UpLeft>(sink, b2);

        if (pos.ep_square() != SQ_NONE)
        {
            assert(rank_of(pos.ep_square()) == relative_rank(Us, RANK_6));

            // An en passant capture cannot resolve a discovered check
            if (Type == EVASIONS && (target & (pos.ep_square() + Up)))
                return;

            b1 = pawnsNotOn7 & Attacks::attacks_bb<PAWN>(pos.ep_square(), Them);

            assert(b1);

            while (b1)
                sink.push(Move::make<EN_PASSANT>(pop_lsb(b1), pos.ep_square()));
        }
    }
}


template<Color Us, PieceType Pt, typename Sink>
void generate_moves(const Position& pos, Sink& sink, Bitboard target) {

    static_assert(Pt != KING && Pt != PAWN, "Unsupported piece type in generate_moves()");

    Bitboard bb = pos.pieces(Us, Pt);

    while (bb)
    {
        Square   from = pop_lsb(bb);
        Bitboard b    = Attacks::attacks_bb<Pt>(from, pos.pieces()) & target;

        splat_moves(sink, from, b);
    }
}


template<Color Us, GenType Type, typename Sink>
void generate_drops(const Position& pos, Sink& sink, Bitboard target) {

    static_assert(Type != LEGAL, "Unsupported type in generate_drops()");

    if constexpr (Type == CAPTURES)
        return;

    if (pos.ruleset() != Ruleset::CRAZYHOUSE)
        return;

    const Bitboard emptyTargets = target & ~pos.pieces();

    for (PieceType pt : Crazyhouse::PocketPieceTypes)
    {
        if (pos.pocket_count(Us, pt) == 0)
            continue;

        Bitboard destinations = emptyTargets;
        if (pt == PAWN)
            destinations &= ~(Rank1BB | Rank8BB);

        while (destinations)
            sink.push(Move::make_drop(pt, pop_lsb(destinations)));
    }
}


template<Color Us, GenType Type, typename Sink>
void generate_all(const Position& pos, Sink& sink) {

    static_assert(Type != LEGAL, "Unsupported type in generate_all()");

    const Square ksq = pos.square<KING>(Us);
    Bitboard     target;

    // Skip generating non-king moves when in double check
    if (Type != EVASIONS || !more_than_one(pos.checkers()))
    {
        target = Type == EVASIONS     ? Attacks::between_bb(ksq, lsb(pos.checkers()))
               : Type == NON_EVASIONS ? ~pos.pieces(Us)
               : Type == CAPTURES     ? pos.pieces(~Us)
                                      : ~pos.pieces();  // QUIETS

        generate_pawn_moves<Us, Type>(pos, sink, target);
        generate_moves<Us, KNIGHT>(pos, sink, target);
        generate_moves<Us, BISHOP>(pos, sink, target);
        generate_moves<Us, ROOK>(pos, sink, target);
        generate_moves<Us, QUEEN>(pos, sink, target);
        generate_drops<Us, Type>(pos, sink, target);
    }

    Bitboard b = Attacks::attacks_bb<KING>(ksq) & (Type == EVASIONS ? ~pos.pieces(Us) : target);

    splat_moves(sink, ksq, b);

    if ((Type == QUIETS || Type == NON_EVASIONS) && pos.can_castle(Us & ANY_CASTLING))
        for (CastlingRights cr : {Us & KING_SIDE, Us & QUEEN_SIDE})
            if (!pos.castling_impeded(cr) && pos.can_castle(cr))
                sink.push(Move::make<CASTLING>(ksq, pos.castling_rook_square(cr)));
}

template<GenType Type, typename Sink>
void generate_pseudo(const Position& pos, Sink& sink) {
    static_assert(Type != LEGAL, "Unsupported type in generate_pseudo()");
    assert((Type == EVASIONS) == bool(pos.checkers()));

    Color us = pos.side_to_move();

    if (us == WHITE)
        generate_all<WHITE, Type>(pos, sink);
    else
        generate_all<BLACK, Type>(pos, sink);
}

template<typename Sink>
void generate_legal(const Position& pos, Sink& sink) {
    Color    us     = pos.side_to_move();
    Bitboard pinned = pos.blockers_for_king(us) & pos.pieces(us);
    Square   ksq    = pos.square<KING>(us);

    if (pos.checkers())
        generate_pseudo<EVASIONS>(pos, sink);
    else
        generate_pseudo<NON_EVASIONS>(pos, sink);

    usize current = 0;
    while (current != sink.size())
    {
        Move move = sink[current];
        if ((move.is_drop()
             || (pinned & move.from_sq()) || move.from_sq() == ksq
             || move.type_of() == EN_PASSANT)
            && !pos.legal(move))
        {
            sink[current] = sink.back();
            sink.pop_back();
        }
        else
            ++current;
    }
}

template<GenType Type, typename Sink>
void generate_into(const Position& pos, Sink& sink) {
    if constexpr (Type == LEGAL)
        generate_legal(pos, sink);
    else
        generate_pseudo<Type>(pos, sink);
}

}  // namespace


// <CAPTURES>     Generates all pseudo-legal captures plus queen promotions
// <QUIETS>       Generates all pseudo-legal non-captures and underpromotions
// <EVASIONS>     Generates all pseudo-legal check evasions
// <NON_EVASIONS> Generates all pseudo-legal captures and non-captures
//
// Returns a pointer to the end of the move list.
template<GenType Type>
Move* generate(const Position& pos, Move* moveList) {
    FixedMoveSink sink(moveList);
    generate_into<Type>(pos, sink);
    return sink.end();
}

template<GenType Type>
void generate(const Position& pos, GrowableMoveBuffer& moveList) {
    moveList.clear();
    GrowableMoveSink sink(moveList);
    generate_into<Type>(pos, sink);
}

// Explicit template instantiations for the fixed official path.
template Move* generate<CAPTURES>(const Position&, Move*);
template Move* generate<QUIETS>(const Position&, Move*);
template Move* generate<EVASIONS>(const Position&, Move*);
template Move* generate<NON_EVASIONS>(const Position&, Move*);
template Move* generate<LEGAL>(const Position&, Move*);

// Explicit template instantiations for the checked growable path.
template void generate<CAPTURES>(const Position&, GrowableMoveBuffer&);
template void generate<QUIETS>(const Position&, GrowableMoveBuffer&);
template void generate<EVASIONS>(const Position&, GrowableMoveBuffer&);
template void generate<NON_EVASIONS>(const Position&, GrowableMoveBuffer&);
template void generate<LEGAL>(const Position&, GrowableMoveBuffer&);

}  // namespace Stockfish
