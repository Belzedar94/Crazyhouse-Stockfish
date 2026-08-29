/*
  Scalar engineering fixture for CH-NNUE-V2-LARGE-K64G1-SFNNV16.
  This target is deliberately separate from the normal engine.
*/

#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>

#include "attacks.h"
#include "nnue/crazyhouse_v2_features.h"
#include "nnue/crazyhouse_v2_large_transform.h"
#include "position.h"
#include "ruleset.h"
#include "uci.h"

namespace {

using namespace Stockfish;
using namespace Stockfish::Eval::NNUE::CrazyhouseV2;
using Inventory = LargeFeatureInventoryV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_large_features: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

bool contains(const Inventory::DomainResult& domain, Inventory::Index index) {
    return std::find(domain.active.begin(),
                     domain.active.begin() + static_cast<std::ptrdiff_t>(domain.size), index)
        != domain.active.begin() + static_cast<std::ptrdiff_t>(domain.size);
}

void require_same_set(const Inventory::DomainResult& left,
                      const Inventory::DomainResult& right,
                      const std::string&             message) {
    require(left.size == right.size, message + " size");
    std::array<Inventory::Index, Inventory::MaximumActivePerDomain> lhs = left.active;
    std::array<Inventory::Index, Inventory::MaximumActivePerDomain> rhs = right.active;
    std::sort(lhs.begin(), lhs.begin() + static_cast<std::ptrdiff_t>(left.size));
    std::sort(rhs.begin(), rhs.begin() + static_cast<std::ptrdiff_t>(right.size));
    require(
      std::equal(lhs.begin(), lhs.begin() + static_cast<std::ptrdiff_t>(left.size), rhs.begin()),
      message + " rows");
}

bool same_set(const Inventory::DomainResult& left, const Inventory::DomainResult& right) {
    if (left.size != right.size)
        return false;
    std::array<Inventory::Index, Inventory::MaximumActivePerDomain> lhs = left.active;
    std::array<Inventory::Index, Inventory::MaximumActivePerDomain> rhs = right.active;
    std::sort(lhs.begin(), lhs.begin() + static_cast<std::ptrdiff_t>(left.size));
    std::sort(rhs.begin(), rhs.begin() + static_cast<std::ptrdiff_t>(right.size));
    return std::equal(lhs.begin(), lhs.begin() + static_cast<std::ptrdiff_t>(left.size),
                      rhs.begin());
}

void require_same_inventory(const Inventory::Result& left,
                            const Inventory::Result& right,
                            const std::string&       message) {
    require(left.status == right.status, message + " status");
    require(left.ok(), message + " success");
    require(left.totalPocketUnits == right.totalPocketUnits, message + " total pocket units");
    for (Color perspective : {WHITE, BLACK})
    {
        require_same_set(left.perspective[perspective].k64, right.perspective[perspective].k64,
                         message + " K64");
        require_same_set(left.perspective[perspective].g1, right.perspective[perspective].g1,
                         message + " G1");
    }
}

void set_position(Position& position, StateInfo& state, std::string_view fen) {
    const auto error = position.set(std::string(fen), false, Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), "position setup rejected: " + std::string(fen));
}

Move parse_move(const Position& position, std::string_view token) {
    const Move move = UCIEngine::to_move(position, std::string(token));
    require(move != Move::none(), "move setup rejected: " + std::string(token));
    return move;
}

void require_status(const PhysicalStateV1& state,
                    Inventory::Status      status,
                    const std::string&     message) {
    require(Inventory::extract(state).status == status, message);
}

PhysicalStateV1 physical_from(const Position& position) {
    PhysicalStateV1 state;
    for (unsigned square = 0; square < 64; ++square)
        state.board[square] = static_cast<Byte>(position.piece_on(Square(square)));
    state.promotedMask                       = position.promoted_pieces();
    constexpr std::array<PieceType, 5> Types = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    for (Color owner : {WHITE, BLACK})
        for (std::size_t type = 0; type < Types.size(); ++type)
            state.pockets[std::size_t(owner) * 5 + type] =
              static_cast<Byte>(position.pocket_count(owner, Types[type]));
    return state;
}

PhysicalStateV1 kings_only() {
    PhysicalStateV1 state;
    state.board[4]  = static_cast<Byte>(W_KING);
    state.board[60] = static_cast<Byte>(B_KING);
    return state;
}

void verify_start_and_position_parity() {
    StateInfo  stateInfo;
    Position   position(Ruleset::CRAZYHOUSE);
    const auto error = position.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1",
                                    false, Ruleset::CRAZYHOUSE, &stateInfo);
    require(!error.has_value(), "start-position setup");

    const Inventory::Result fromPosition = Inventory::extract(position);
    const Inventory::Result fromPhysical = Inventory::extract(physical_from(position));
    require(fromPosition.ok() && fromPhysical.ok(), "start-position extraction");
    require(fromPosition.totalPocketUnits == 0, "start-position total pocket units");
    for (Color perspective : {WHITE, BLACK})
    {
        const auto& positionView = fromPosition.perspective[perspective];
        const auto& physicalView = fromPhysical.perspective[perspective];
        require(positionView.k64.size == 32 && positionView.g1.size == 32,
                "start-position active count");
        require_same_set(positionView.k64, physicalView.k64, "start-position K64 parity");
        require_same_set(positionView.g1, physicalView.g1, "start-position G1 parity");
        require(contains(positionView.k64, 3460), "own-king K64 row");
        require(contains(positionView.k64, 3516), "opponent-king K64 row");
        require(contains(positionView.g1, 644), "own-king G1 row");
        require(contains(positionView.g1, 764), "opponent-king G1 row");
    }
}

void verify_asymmetric_position_parity_and_goldens() {
    StateInfo stateInfo;
    Position  position(Ruleset::CRAZYHOUSE);
    set_position(position, stateInfo, "7k/8/8/8/3Q~4/8/2K5/8[PNq] b - - 7 42");

    const Inventory::Result fromPosition = Inventory::extract(position);
    const Inventory::Result fromPhysical = Inventory::extract(physical_from(position));
    require_same_inventory(fromPosition, fromPhysical, "asymmetric Position/physical parity");
    require(fromPosition.totalPocketUnits == 3, "asymmetric total pocket units");
    for (Color perspective : {WHITE, BLACK})
    {
        require(fromPosition.perspective[perspective].k64.size == 7, "asymmetric K64 active count");
        require(fromPosition.perspective[perspective].g1.size == 7, "asymmetric G1 active count");
    }

    const auto& white = fromPosition.perspective[WHITE];
    require(contains(white.k64, 7690), "asymmetric white own-king K64 row");
    require(contains(white.k64, 7743), "asymmetric white opponent-king K64 row");
    require(contains(white.k64, 7579), "asymmetric white queen K64 row");
    require(contains(white.k64, 45656) && contains(white.k64, 45672) && contains(white.k64, 45714),
            "asymmetric white-view pocket rows");
    require(contains(white.k64, 54235), "asymmetric white-view promoted K64 row");
    require(contains(white.g1, 650) && contains(white.g1, 767) && contains(white.g1, 539),
            "asymmetric white-view board G1 rows");
    require(contains(white.g1, 768) && contains(white.g1, 784) && contains(white.g1, 826),
            "asymmetric white-view pocket G1 rows");
    require(contains(white.g1, 1047), "asymmetric white-view promoted G1 row");

    const auto& black = fromPosition.perspective[BLACK];
    require(contains(black.k64, 5575), "asymmetric black own-king K64 row");
    require(contains(black.k64, 5618), "asymmetric black opponent-king K64 row");
    require(contains(black.k64, 5539), "asymmetric black queen K64 row");
    require(contains(black.k64, 45504) && contains(black.k64, 45506) && contains(black.k64, 45522),
            "asymmetric black-view pocket rows");
    require(contains(black.k64, 52963), "asymmetric black-view promoted K64 row");
    require(contains(black.g1, 647) && contains(black.g1, 754) && contains(black.g1, 611),
            "asymmetric black-view board G1 rows");
    require(contains(black.g1, 796) && contains(black.g1, 798) && contains(black.g1, 814),
            "asymmetric black-view pocket G1 rows");
    require(contains(black.g1, 1311), "asymmetric black-view promoted G1 row");
}

void verify_pocket_slots() {
    PhysicalStateV1 state            = kings_only();
    state.pockets[0]                 = 2;
    const Inventory::Result features = Inventory::extract(state);
    require(features.ok(), "pocket-slot extraction");
    require(features.totalPocketUnits == 2, "pocket-slot routing count");
    require(features.perspective[WHITE].k64.size == 4 && features.perspective[WHITE].g1.size == 4,
            "pocket-slot active count");
    require(contains(features.perspective[WHITE].k64, 45296)
              && contains(features.perspective[WHITE].k64, 45297),
            "white K64 cumulative pocket slots");
    require(contains(features.perspective[WHITE].g1, 768)
              && contains(features.perspective[WHITE].g1, 769),
            "white G1 cumulative pocket slots");
    require(contains(features.perspective[BLACK].k64, 45326)
              && contains(features.perspective[BLACK].k64, 45327),
            "black-perspective K64 opponent pocket slots");
    require(contains(features.perspective[BLACK].g1, 798)
              && contains(features.perspective[BLACK].g1, 799),
            "black-perspective G1 opponent pocket slots");

    constexpr std::array<unsigned, 5> Maxima = {16, 4, 4, 4, 2};
    constexpr std::array<unsigned, 5> Prefix = {0, 16, 20, 24, 28};
    for (unsigned owner = 0; owner < 2; ++owner)
        for (unsigned type = 0; type < 5; ++type)
        {
            PhysicalStateV1 extreme           = kings_only();
            extreme.pockets[owner * 5 + type] = static_cast<Byte>(Maxima[type]);
            const Inventory::Result rows      = Inventory::extract(extreme);
            require(rows.ok(), "pocket maximum extraction");
            require(rows.totalPocketUnits == Maxima[type], "pocket maximum routing count");
            for (unsigned perspective = 0; perspective < 2; ++perspective)
            {
                const unsigned relativeOwner = owner != perspective;
                const unsigned firstPlane    = relativeOwner * 30 + Prefix[type];
                const unsigned lastPlane     = firstPlane + Maxima[type] - 1;
                const unsigned bucket        = 4;
                const auto&    view          = rows.perspective[perspective];
                require(
                  contains(view.k64, static_cast<Inventory::Index>(Inventory::KPocketOffset
                                                                   + bucket * 60 + firstPlane))
                    && contains(view.k64, static_cast<Inventory::Index>(Inventory::KPocketOffset
                                                                        + bucket * 60 + lastPlane)),
                  "pocket maximum K64 boundary slots");
                require(contains(view.g1, static_cast<Inventory::Index>(Inventory::GPocketOffset
                                                                        + firstPlane))
                          && contains(view.g1, static_cast<Inventory::Index>(
                                                 Inventory::GPocketOffset + lastPlane)),
                        "pocket maximum G1 boundary slots");
            }
        }

    PhysicalStateV1 finalPocketRow;
    finalPocketRow.board[63]         = static_cast<Byte>(W_KING);
    finalPocketRow.board[0]          = static_cast<Byte>(B_KING);
    finalPocketRow.pockets[9]        = 2;
    const Inventory::Result boundary = Inventory::extract(finalPocketRow);
    require(boundary.ok(), "last-pocket-row extraction");
    require(contains(boundary.perspective[WHITE].k64, 45055), "last K64 board row");
    require(contains(boundary.perspective[WHITE].k64, 48895), "last K64 pocket row");

    PhysicalStateV1 finalGBoardRow;
    finalGBoardRow.board[0]           = static_cast<Byte>(W_KING);
    finalGBoardRow.board[63]          = static_cast<Byte>(B_KING);
    const Inventory::Result gBoundary = Inventory::extract(finalGBoardRow);
    require(gBoundary.ok(), "last-G1-board-row extraction");
    require(contains(gBoundary.perspective[WHITE].g1, 767), "last G1 board row");
}

void verify_promoted_provenance() {
    PhysicalStateV1 state            = kings_only();
    state.board[27]                  = static_cast<Byte>(W_QUEEN);
    state.promotedMask               = std::uint64_t{1} << 27;
    const Inventory::Result features = Inventory::extract(state);
    require(features.ok(), "promoted extraction");
    require(features.perspective[WHITE].k64.size == 4 && features.perspective[WHITE].g1.size == 4,
            "promoted active count");
    require(contains(features.perspective[WHITE].k64, 51163), "white K64 promoted row");
    require(contains(features.perspective[WHITE].g1, 1047), "white G1 promoted row");
    require(contains(features.perspective[BLACK].k64, 51427), "black K64 promoted row");
    require(contains(features.perspective[BLACK].g1, 1311), "black G1 promoted row");

    PhysicalStateV1 unpromoted      = state;
    unpromoted.promotedMask         = 0;
    const Inventory::Result control = Inventory::extract(unpromoted);
    require(control.ok(), "unpromoted control extraction");
    require(control.perspective[WHITE].k64.size + 1 == features.perspective[WHITE].k64.size,
            "promoted K64 separation");
    require(control.perspective[WHITE].g1.size + 1 == features.perspective[WHITE].g1.size,
            "promoted G1 separation");

    struct PromotedGolden {
        Byte             piece;
        unsigned         square;
        Inventory::Index whiteG1;
        Inventory::Index blackG1;
    };
    constexpr std::array<PromotedGolden, 8> Goldens = {{
      {static_cast<Byte>(W_KNIGHT), 17, 845, 1125},
      {static_cast<Byte>(W_BISHOP), 18, 910, 1190},
      {static_cast<Byte>(W_ROOK), 19, 975, 1255},
      {static_cast<Byte>(W_QUEEN), 20, 1040, 1320},
      {static_cast<Byte>(B_KNIGHT), 41, 1125, 845},
      {static_cast<Byte>(B_BISHOP), 42, 1190, 910},
      {static_cast<Byte>(B_ROOK), 43, 1255, 975},
      {static_cast<Byte>(B_QUEEN), 44, 1320, 1040},
    }};
    for (const PromotedGolden golden : Goldens)
    {
        PhysicalStateV1 promoted      = kings_only();
        promoted.board[golden.square] = golden.piece;
        promoted.promotedMask         = std::uint64_t{1} << golden.square;
        const Inventory::Result rows  = Inventory::extract(promoted);
        require(rows.ok(), "promoted type/owner extraction");
        require(contains(rows.perspective[WHITE].g1, golden.whiteG1),
                "promoted type/owner white G1 golden");
        require(contains(rows.perspective[BLACK].g1, golden.blackG1),
                "promoted type/owner black G1 golden");
    }

    PhysicalStateV1 finalPromotedRow;
    finalPromotedRow.board[0]     = static_cast<Byte>(W_KING);
    finalPromotedRow.board[60]    = static_cast<Byte>(B_KING);
    finalPromotedRow.board[7]     = static_cast<Byte>(W_QUEEN);
    finalPromotedRow.promotedMask = std::uint64_t{1} << 7;
    const Inventory::Result last  = Inventory::extract(finalPromotedRow);
    require(last.ok(), "last-promoted-row extraction");
    require(contains(last.perspective[BLACK].g1, 1339), "last G1 promoted row");
}

PhysicalStateV1 maximum_active_state() {
    PhysicalStateV1 state = kings_only();
    for (unsigned index = 0; index < 16; ++index)
    {
        const unsigned square = 8 + index;
        state.board[square]   = static_cast<Byte>(index % 2 ? B_KNIGHT : W_KNIGHT);
        state.promotedMask |= std::uint64_t{1} << square;
    }
    state.pockets[1] = 4;
    state.pockets[2] = 4;
    state.pockets[3] = 4;
    state.pockets[4] = 2;
    return state;
}

PhysicalStateV1 reflected_color_swap(const PhysicalStateV1& source) {
    PhysicalStateV1 target;
    for (unsigned square = 0; square < 64; ++square)
        if (source.board[square])
            target.board[square ^ 56U] = Byte(source.board[square] ^ 8U);
    for (unsigned owner = 0; owner < 2; ++owner)
        for (unsigned type = 0; type < 5; ++type)
            target.pockets[(owner ^ 1U) * 5 + type] = source.pockets[owner * 5 + type];
    for (unsigned square = 0; square < 64; ++square)
        if (source.promotedMask & (std::uint64_t{1} << square))
            target.promotedMask |= std::uint64_t{1} << (square ^ 56U);
    return target;
}

PhysicalStateV1 file_reflected(const PhysicalStateV1& source) {
    PhysicalStateV1 target = source;
    target.board.fill(0);
    target.promotedMask = 0;
    for (unsigned square = 0; square < 64; ++square)
        if (source.board[square])
            target.board[square ^ 7U] = source.board[square];
    for (unsigned square = 0; square < 64; ++square)
        if (source.promotedMask & (std::uint64_t{1} << square))
            target.promotedMask |= std::uint64_t{1} << (square ^ 7U);
    return target;
}

void verify_capacity_and_symmetry() {
    for (unsigned square = 0; square < 64; ++square)
    {
        PhysicalStateV1 bucketState;
        bucketState.board[square]             = static_cast<Byte>(W_KING);
        bucketState.board[(square + 32) % 64] = static_cast<Byte>(B_KING);
        const Inventory::Result bucketRows    = Inventory::extract(bucketState);
        require(bucketRows.ok(), "king-bucket sweep extraction");
        const Inventory::Index ownKingRow =
          static_cast<Inventory::Index>((square * 11 + 10) * 64 + square);
        require(contains(bucketRows.perspective[WHITE].k64, ownKingRow),
                "king-bucket sweep own-king row");
    }

    const PhysicalStateV1   maximum  = maximum_active_state();
    const Inventory::Result features = Inventory::extract(maximum);
    require(features.ok(), "maximum-active extraction");
    require(features.totalPocketUnits == 14, "maximum-active pocket routing count");
    for (Color perspective : {WHITE, BLACK})
    {
        require(features.perspective[perspective].k64.size == 48, "maximum K64 active count");
        require(features.perspective[perspective].g1.size == 48, "maximum G1 active count");
        for (std::size_t index = 0; index < 48; ++index)
        {
            require(features.perspective[perspective].k64.active[index] < Inventory::KDimensions,
                    "K64 index bound");
            require(features.perspective[perspective].g1.active[index] < Inventory::GDimensions,
                    "G1 index bound");
        }
    }

    const Inventory::Result transformed = Inventory::extract(reflected_color_swap(maximum));
    require(transformed.ok(), "symmetry extraction");
    require_same_set(features.perspective[WHITE].k64, transformed.perspective[BLACK].k64,
                     "K64 symmetry");
    require_same_set(features.perspective[WHITE].g1, transformed.perspective[BLACK].g1,
                     "G1 symmetry");
    require_same_set(features.perspective[BLACK].k64, transformed.perspective[WHITE].k64,
                     "reverse K64 symmetry");
    require_same_set(features.perspective[BLACK].g1, transformed.perspective[WHITE].g1,
                     "reverse G1 symmetry");

    PhysicalStateV1 asymmetric;
    asymmetric.board[10]             = static_cast<Byte>(W_KING);
    asymmetric.board[63]             = static_cast<Byte>(B_KING);
    asymmetric.board[27]             = static_cast<Byte>(W_QUEEN);
    asymmetric.promotedMask          = std::uint64_t{1} << 27;
    asymmetric.pockets[0]            = 1;
    const Inventory::Result original = Inventory::extract(asymmetric);
    const Inventory::Result mirrored = Inventory::extract(file_reflected(asymmetric));
    require(original.ok() && mirrored.ok(), "file-reflection control extraction");
    require(!same_set(original.perspective[WHITE].k64, mirrored.perspective[WHITE].k64),
            "file reflection must not be treated as K64 symmetry");
    require(!same_set(original.perspective[WHITE].g1, mirrored.perspective[WHITE].g1),
            "file reflection must not be treated as G1 symmetry");
}

template<typename Verify>
void verify_full_refresh_transition(std::string_view label,
                                    std::string_view fen,
                                    std::string_view moveText,
                                    Verify           verify) {
    StateInfo rootState;
    StateInfo childState;
    Position  position(Ruleset::CRAZYHOUSE);
    set_position(position, rootState, fen);
    const std::string       rootFen = position.fen();
    const Inventory::Result root    = Inventory::extract(position);
    require_same_inventory(root, Inventory::extract(physical_from(position)),
                           std::string(label) + " root projection");

    const Move move = parse_move(position, moveText);
    position.do_move(move, childState);
    const Inventory::Result after = Inventory::extract(position);
    require_same_inventory(after, Inventory::extract(physical_from(position)),
                           std::string(label) + " child projection");
    verify(root, after);

    position.undo_move(move);
    require(position.fen() == rootFen, std::string(label) + " FEN undo");
    require_same_inventory(root, Inventory::extract(position),
                           std::string(label) + " feature undo");
}

void verify_full_refresh_transitions() {
    verify_full_refresh_transition(
      "drop", "7k/8/8/8/8/8/2K5/8[P] w - - 0 1", "P@d4",
      [](const Inventory::Result& root, const Inventory::Result& after) {
          const auto& beforeWhite = root.perspective[WHITE];
          const auto& afterWhite  = after.perspective[WHITE];
          require(contains(beforeWhite.k64, 45656) && contains(beforeWhite.g1, 768),
                  "drop root pocket row");
          require(!contains(afterWhite.k64, 45656) && !contains(afterWhite.g1, 768),
                  "drop removes pocket row");
          require(contains(afterWhite.k64, 7067) && contains(afterWhite.g1, 27),
                  "drop adds board row");
          require(contains(after.perspective[BLACK].k64, 5027)
                    && contains(after.perspective[BLACK].g1, 99),
                  "drop opponent-perspective board row");
      });

    verify_full_refresh_transition(
      "promotion", "7k/P7/8/8/8/8/8/K7[] w - - 0 1", "a7a8q",
      [](const Inventory::Result& root, const Inventory::Result& after) {
          require(root.perspective[WHITE].k64.size + 1 == after.perspective[WHITE].k64.size,
                  "promotion adds provenance incidence");
          require(contains(after.perspective[WHITE].k64, 568)
                    && contains(after.perspective[WHITE].k64, 49144),
                  "promotion white K64 rows");
          require(contains(after.perspective[WHITE].g1, 568)
                    && contains(after.perspective[WHITE].g1, 1076),
                  "promotion white G1 rows");
          require(contains(after.perspective[BLACK].k64, 5504)
                    && contains(after.perspective[BLACK].k64, 52928),
                  "promotion black K64 rows");
      });

    verify_full_refresh_transition(
      "promoted capture", "7k/8/8/8/8/8/Q~6r/K7[] b - - 0 1", "h2a2",
      [](const Inventory::Result& root, const Inventory::Result& after) {
          require(root.perspective[BLACK].k64.size == 5 && after.perspective[BLACK].k64.size == 4,
                  "promoted capture active counts");
          require(contains(after.perspective[BLACK].k64, 5360)
                    && contains(after.perspective[BLACK].k64, 45476),
                  "promoted capture black K64 board/pocket rows");
          require(contains(after.perspective[BLACK].g1, 768),
                  "promoted capture black pawn-pocket row");
          require(after.perspective[BLACK].g1.size == 4, "promoted capture clears provenance row");
      });

    verify_full_refresh_transition(
      "promoted-on-promoted capture", "7k/8/8/8/8/8/Q~6r~/K7[] b - - 0 1", "h2a2",
      [](const Inventory::Result& root, const Inventory::Result& after) {
          require(root.perspective[BLACK].g1.size == 6 && after.perspective[BLACK].g1.size == 5,
                  "promoted-on-promoted active counts");
          require(contains(root.perspective[BLACK].g1, 1011)
                    && contains(root.perspective[BLACK].g1, 1324),
                  "promoted-on-promoted root provenance rows");
          require(contains(after.perspective[BLACK].g1, 1004),
                  "promoted-on-promoted replacement provenance row");
          require(!contains(after.perspective[BLACK].g1, 1011)
                    && !contains(after.perspective[BLACK].g1, 1324),
                  "promoted-on-promoted stale rows removed");
      });

    verify_full_refresh_transition(
      "en-passant", "7k/8/8/3pP3/8/8/8/K7[] w - d6 0 1", "e5d6",
      [](const Inventory::Result& root, const Inventory::Result& after) {
          require(root.perspective[WHITE].g1.size == 4 && after.perspective[WHITE].g1.size == 4,
                  "en-passant active count");
          require(contains(after.perspective[WHITE].k64, 43)
                    && contains(after.perspective[WHITE].k64, 45056),
                  "en-passant white K64 board/pocket rows");
          require(contains(after.perspective[WHITE].g1, 43)
                    && contains(after.perspective[WHITE].g1, 768),
                  "en-passant white G1 board/pocket rows");
      });

    verify_full_refresh_transition(
      "castling", "r3k2r/8/8/8/8/8/8/R3K2R[] w KQkq - 0 1", "e1g1",
      [](const Inventory::Result& root, const Inventory::Result& after) {
          require(!same_set(root.perspective[WHITE].k64, after.perspective[WHITE].k64),
                  "castling refreshes own K64 bucket");
          require(contains(after.perspective[WHITE].k64, 4870)
                    && contains(after.perspective[WHITE].k64, 4613),
                  "castling white K64 king/rook rows");
          require(contains(after.perspective[WHITE].g1, 646)
                    && contains(after.perspective[WHITE].g1, 389),
                  "castling white G1 king/rook rows");
      });

    verify_full_refresh_transition(
      "king move", "7k/8/8/8/8/8/4K3/8[] w - - 0 1", "e2f3",
      [](const Inventory::Result& root, const Inventory::Result& after) {
          require(!same_set(root.perspective[WHITE].k64, after.perspective[WHITE].k64),
                  "king move refreshes own K64 bucket");
          require(contains(after.perspective[WHITE].k64, 15445), "king move own-king K64 row");
      });

    StateInfo rootState;
    StateInfo nullState;
    Position  position(Ruleset::CRAZYHOUSE);
    set_position(position, rootState, "6k1/8/8/8/3Q~4/8/2K5/8[PNq] w - - 7 42");
    const std::string       rootFen = position.fen();
    const Inventory::Result root    = Inventory::extract(position);
    position.do_null_move(nullState);
    require_same_inventory(root, Inventory::extract(position), "null feature invariance");
    position.undo_null_move();
    require(position.fen() == rootFen, "null FEN undo");
    require_same_inventory(root, Inventory::extract(position), "null feature undo");
}

void verify_negative_controls() {
    StateInfo  chessState;
    Position   chess(Ruleset::CHESS);
    const auto chessError = chess.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                                      false, Ruleset::CHESS, &chessState);
    require(!chessError.has_value(), "chess control setup");
    require(Inventory::extract(chess).status == Inventory::Status::WRONG_RULESET,
            "wrong-ruleset rejection");

    PhysicalStateV1 invalidPiece = kings_only();
    invalidPiece.board[0]        = 7;
    require_status(invalidPiece, Inventory::Status::INVALID_PIECE, "invalid piece-code rejection");

    PhysicalStateV1 missingKing = kings_only();
    missingKing.board[60]       = 0;
    require_status(missingKing, Inventory::Status::INVALID_KING_STATE, "king-count rejection");

    PhysicalStateV1 pawnOnBackRank = kings_only();
    pawnOnBackRank.board[0]        = static_cast<Byte>(W_PAWN);
    require_status(pawnOnBackRank, Inventory::Status::PAWN_PROMOTION_RANK,
                   "pawn back-rank rejection");

    constexpr std::array<unsigned, 5> PocketMaxima = {16, 4, 4, 4, 2};
    for (unsigned owner = 0; owner < 2; ++owner)
        for (unsigned type = 0; type < 5; ++type)
        {
            PhysicalStateV1 pocketOverflow           = kings_only();
            pocketOverflow.pockets[owner * 5 + type] = static_cast<Byte>(PocketMaxima[type] + 1);
            require_status(pocketOverflow, Inventory::Status::POCKET_BOUNDS,
                           "pocket bound rejection");
        }

    PhysicalStateV1 promotedEmpty = kings_only();
    promotedEmpty.promotedMask    = std::uint64_t{1} << 1;
    require_status(promotedEmpty, Inventory::Status::PROMOTED_MASK,
                   "empty promoted-mask rejection");

    PhysicalStateV1 promotedPawn = kings_only();
    promotedPawn.board[8]        = static_cast<Byte>(W_PAWN);
    promotedPawn.promotedMask    = std::uint64_t{1} << 8;
    require_status(promotedPawn, Inventory::Status::PROMOTED_MASK, "pawn promoted-mask rejection");

    PhysicalStateV1 promotedKing = kings_only();
    promotedKing.promotedMask    = std::uint64_t{1} << 4;
    require_status(promotedKing, Inventory::Status::PROMOTED_MASK, "king promoted-mask rejection");

    PhysicalStateV1 tooManyPawnOrigins = kings_only();
    for (unsigned index = 0; index < 17; ++index)
    {
        const unsigned square            = 8 + index;
        tooManyPawnOrigins.board[square] = static_cast<Byte>(index % 2 ? B_KNIGHT : W_KNIGHT);
        tooManyPawnOrigins.promotedMask |= std::uint64_t{1} << square;
    }
    require_status(tooManyPawnOrigins, Inventory::Status::PHYSICAL_UNIT_BOUNDS,
                   "isolated pawn-origin bound rejection");

    constexpr std::array<Byte, 4> OtherPieces = {
      static_cast<Byte>(W_KNIGHT), static_cast<Byte>(W_BISHOP), static_cast<Byte>(W_ROOK),
      static_cast<Byte>(W_QUEEN)};
    constexpr std::array<unsigned, 4> OtherMaxima = {4, 4, 4, 2};
    for (unsigned type = 0; type < OtherPieces.size(); ++type)
    {
        PhysicalStateV1 originOverflow = kings_only();
        for (unsigned count = 0; count <= OtherMaxima[type]; ++count)
            originOverflow.board[8 + count] = OtherPieces[type];
        require_status(originOverflow, Inventory::Status::PHYSICAL_UNIT_BOUNDS,
                       "isolated non-pawn origin bound rejection");
    }
}

void verify_pair_product_and_ordering() {
    LargeKAccumulator k64{};
    LargeGAccumulator g1{};
    k64[0]                                   = 255;
    k64[384]                                 = 255;
    k64[1]                                   = 256;
    k64[385]                                 = 512;
    k64[2]                                   = -1;
    k64[386]                                 = 255;
    k64[3]                                   = 128;
    k64[387]                                 = 128;
    g1[0]                                    = 255;
    g1[128]                                  = 254;
    const LargePerspectiveOutput transformed = transform_large_pair_product_v1(k64, g1);
    require(transformed[0] == 127 && transformed[1] == 127 && transformed[2] == 0
              && transformed[3] == 32,
            "K64 pair-product goldens");
    require(transformed[384] == 126, "G1 pair-product golden");
    require(*std::max_element(transformed.begin(), transformed.end()) == 127,
            "pair-product upper range");

    LargePerspectiveOutput white{};
    LargePerspectiveOutput black{};
    for (std::size_t index = 0; index < white.size(); ++index)
    {
        white[index] = static_cast<Byte>(index % 128);
        black[index] = static_cast<Byte>(127 - index % 128);
    }
    const LargeDenseInputResultV1 whiteToMove = order_large_dense_input_v1(white, black, WHITE);
    const LargeDenseInputResultV1 blackToMove = order_large_dense_input_v1(white, black, BLACK);
    require(whiteToMove.ok() && blackToMove.ok(), "dense-input ordering status");
    require(std::equal(white.begin(), white.end(), whiteToMove.bytes.begin())
              && std::equal(black.begin(), black.end(),
                            whiteToMove.bytes.begin() + LargePerspectiveOutputBytes),
            "white side-to-move ordering");
    require(std::equal(black.begin(), black.end(), blackToMove.bytes.begin())
              && std::equal(white.begin(), white.end(),
                            blackToMove.bytes.begin() + LargePerspectiveOutputBytes),
            "black side-to-move ordering");
    require(!order_large_dense_input_v1(white, black, COLOR_NB).ok(),
            "invalid side-to-move rejection");
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();
    verify_start_and_position_parity();
    verify_asymmetric_position_parity_and_goldens();
    verify_pocket_slots();
    verify_promoted_provenance();
    verify_capacity_and_symmetry();
    verify_full_refresh_transitions();
    verify_negative_controls();
    verify_pair_product_and_ordering();
    std::cout << "PASS crazyhouse_v2_large_features"
              << " K=" << Inventory::KDimensions << " G=" << Inventory::GDimensions
              << " max_domain=" << Inventory::MaximumActivePerDomain
              << " max_perspective=" << Inventory::MaximumActivePerPerspective
              << " pair_product=PASS ordering=PASS" << '\n';
    return EXIT_SUCCESS;
}
