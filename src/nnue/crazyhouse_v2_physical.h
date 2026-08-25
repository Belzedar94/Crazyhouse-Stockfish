/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#ifndef NNUE_CRAZYHOUSE_V2_PHYSICAL_H_INCLUDED
#define NNUE_CRAZYHOUSE_V2_PHYSICAL_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {

using Byte   = std::uint8_t;
using Digest = std::array<Byte, 32>;
using Id     = std::array<Byte, 16>;

inline constexpr std::size_t PhysicalRecordBytes = 256;
inline constexpr Byte        NoSquare            = 255;

enum class PhysicalDecodeError {
    NONE,
    WRONG_SIZE,
    MAGIC,
    VERSION,
    RESERVED_BYTES,
    CRC32C,
    ZERO_IDENTITY,
    FLAGS,
    BOARD_PIECE_CODE,
    KING_COUNT,
    PAWN_PROMOTION_RANK,
    PROMOTED_MASK,
    POCKET_BOUNDS,
    SIDE_TO_MOVE,
    CASTLING_RIGHTS,
    EN_PASSANT,
    REPETITION,
    CLAIM_POLICY,
    TERMINAL_REASON,
    CLOCKS,
    MOVE_WIRE,
    MOVE_STATE,
    RESULT_PERSPECTIVE,
    TEACHER_FRAMING,
    ZERO_DIGEST,
    POSITION_IDENTITY,
    MATERIAL_CONSERVATION,
};

struct PhysicalMoveV1 {
    Byte kind       = 0;
    Byte fromSquare = NoSquare;
    Byte toSquare   = NoSquare;
    Byte auxPiece   = 0;
};

struct PhysicalStateV1 {
    // Piece codes are the frozen nibble codes: 0, 1..6 and 9..14.
    std::array<Byte, 64> board{};
    std::uint64_t        promotedMask            = 0;
    std::array<Byte, 10> pockets{};
    Byte                 sideToMove              = 0;
    Byte                 castlingRights          = 0;
    Byte                 rawEnPassantSquare      = NoSquare;
    Byte                 effectiveEnPassantSquare = NoSquare;
    Byte                 repetitionOccurrences   = 0;
    Byte                 claimPolicy             = 0;
    Byte                 terminalReason          = 0;
    std::uint32_t        halfmoveClock           = 0;
    std::uint32_t        fullmoveNumber          = 0;
};

struct PhysicalRecordV1 {
    std::uint64_t sequence = 0;
    Id            gameId{};
    Id            trajectoryId{};
    std::uint32_t ply   = 0;
    std::uint32_t flags = 0;

    PhysicalStateV1 state{};
    PhysicalMoveV1  move{};

    std::int8_t  gameResultWhite    = 0;
    std::int8_t  resultSideToMove   = 0;
    Byte         teacherScoreKind   = 0;
    Byte         teacherBound       = 0;
    std::int32_t teacherScoreValue  = 0;
    std::uint64_t searchNodes       = 0;
    std::uint16_t searchDepth       = 0;
    std::uint16_t searchSelDepth    = 0;
    std::uint32_t moveTimeMs        = 0;

    Digest positionIdentity{};
    Digest historyPrefix{};
    Digest provenance{};
};

struct PhysicalDecodeResult {
    PhysicalDecodeError error = PhysicalDecodeError::WRONG_SIZE;
    PhysicalRecordV1    record{};

    constexpr bool ok() const noexcept { return error == PhysicalDecodeError::NONE; }
};

PhysicalDecodeResult decode_physical_record_v1(const Byte* bytes, std::size_t size) noexcept;
Digest               physical_position_identity_v1(const PhysicalStateV1& state) noexcept;
std::string_view     physical_decode_error_name(PhysicalDecodeError error) noexcept;

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2

#endif  // NNUE_CRAZYHOUSE_V2_PHYSICAL_H_INCLUDED
