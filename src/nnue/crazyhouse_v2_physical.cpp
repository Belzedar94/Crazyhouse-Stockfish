/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_physical.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <limits>
#include <type_traits>

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {
namespace {

constexpr std::uint32_t KnownFlags = 0x7FU;
constexpr std::uint32_t FlagMove = 1U << 0;
constexpr std::uint32_t FlagTerminal = 1U << 1;
constexpr std::uint32_t FlagTeacher = 1U << 2;
constexpr std::uint32_t FlagTeacherNetwork = 1U << 3;
constexpr std::uint32_t FlagTrajectoryStart = 1U << 5;
constexpr std::uint32_t FlagNonstandardRoot = 1U << 6;

constexpr Byte PieceNone   = 0;
constexpr Byte PiecePawn   = 1;
constexpr Byte PieceKnight = 2;
constexpr Byte PieceBishop = 3;
constexpr Byte PieceRook   = 4;
constexpr Byte PieceQueen  = 5;
constexpr Byte PieceKing   = 6;

constexpr Byte MoveNone      = 0;
constexpr Byte MovePromotion = 2;
constexpr Byte MoveEnPassant = 3;
constexpr Byte MoveCastling  = 4;
constexpr Byte MoveDrop      = 5;

constexpr Byte TerminalOngoing       = 0;
constexpr Byte TerminalCheckmate     = 1;
constexpr Byte TerminalStalemate     = 2;
constexpr Byte TerminalFivefold      = 3;
constexpr Byte TerminalThreefold     = 4;
constexpr Byte TerminalAdjudication  = 6;

constexpr Byte TeacherNone       = 0;
constexpr Byte TeacherCentipawn  = 1;
constexpr Byte TeacherMatePlies  = 2;
constexpr Byte BoundNone         = 0;
constexpr Byte BoundExact        = 1;

constexpr char PositionDomain[] =
  "Crazyhouse-Stockfish physical repetition identity v1\0";

constexpr std::array<Byte, 10> PocketMaximums = {16, 4, 4, 4, 2, 16, 4, 4, 4, 2};

constexpr bool valid_piece_code(Byte code) noexcept {
    return code == PieceNone || (code >= 1 && code <= 6) || (code >= 9 && code <= 14);
}

constexpr Byte piece_type(Byte code) noexcept { return Byte(code & 7U); }
constexpr Byte piece_owner(Byte code) noexcept { return Byte(code >> 3U); }
constexpr std::uint64_t square_bit(unsigned square) noexcept {
    return std::uint64_t{1} << square;
}

template<typename UInt>
UInt get_le(const Byte* bytes) noexcept {
    static_assert(std::is_unsigned_v<UInt>);
    UInt output = 0;
    for (std::size_t index = 0; index < sizeof(UInt); ++index)
        output |= UInt(bytes[index]) << (8U * index);
    return output;
}

std::int32_t get_i32_le(const Byte* bytes) noexcept {
    const std::uint32_t raw = get_le<std::uint32_t>(bytes);
    if (raw <= std::uint32_t(std::numeric_limits<std::int32_t>::max()))
        return static_cast<std::int32_t>(raw);
    return static_cast<std::int32_t>(std::int64_t(raw) - (std::int64_t{1} << 32));
}

std::int8_t get_i8(Byte value) noexcept {
    return static_cast<std::int8_t>(value <= 127 ? int(value) : int(value) - 256);
}

template<std::size_t Size>
bool all_zero(const std::array<Byte, Size>& bytes) noexcept {
    return std::all_of(bytes.begin(), bytes.end(), [](Byte value) { return value == 0; });
}

bool range_all_zero(const Byte* first, const Byte* last) noexcept {
    return std::all_of(first, last, [](Byte value) { return value == 0; });
}

constexpr std::uint32_t rotate_right(std::uint32_t value, unsigned shift) noexcept {
    return (value >> shift) | (value << (32U - shift));
}

class Sha256 {
   public:
    void update(const Byte* data, std::size_t size) noexcept {
        totalBytes += size;
        while (size != 0)
        {
            const std::size_t amount = std::min(size, block.size() - buffered);
            std::copy_n(data, amount, block.begin() + static_cast<std::ptrdiff_t>(buffered));
            buffered += amount;
            data += amount;
            size -= amount;
            if (buffered == block.size())
            {
                transform(block.data());
                buffered = 0;
            }
        }
    }

    Digest final() const noexcept {
        Sha256 copy = *this;
        const std::uint64_t bitLength = copy.totalBytes * 8U;
        copy.block[copy.buffered++] = 0x80;
        if (copy.buffered > 56)
        {
            std::fill(copy.block.begin() + static_cast<std::ptrdiff_t>(copy.buffered),
                      copy.block.end(), Byte{0});
            copy.transform(copy.block.data());
            copy.buffered = 0;
        }
        std::fill(copy.block.begin() + static_cast<std::ptrdiff_t>(copy.buffered),
                  copy.block.begin() + 56, Byte{0});
        for (unsigned index = 0; index < 8; ++index)
            copy.block[63 - index] = Byte(bitLength >> (8U * index));
        copy.transform(copy.block.data());

        Digest output{};
        for (std::size_t word = 0; word < copy.state.size(); ++word)
            for (unsigned index = 0; index < 4; ++index)
                output[word * 4 + index] = Byte(copy.state[word] >> (24U - 8U * index));
        return output;
    }

   private:
    void transform(const Byte* data) noexcept {
        static constexpr std::array<std::uint32_t, 64> Constants = {
          0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U,
          0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
          0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U,
          0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
          0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
          0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
          0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
          0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
          0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU,
          0x5b9cca4fU, 0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
          0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U};

        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16; ++index)
            words[index] = (std::uint32_t(data[index * 4]) << 24U)
                         | (std::uint32_t(data[index * 4 + 1]) << 16U)
                         | (std::uint32_t(data[index * 4 + 2]) << 8U)
                         | std::uint32_t(data[index * 4 + 3]);
        for (std::size_t index = 16; index < words.size(); ++index)
        {
            const std::uint32_t s0 = rotate_right(words[index - 15], 7)
                                   ^ rotate_right(words[index - 15], 18)
                                   ^ (words[index - 15] >> 3U);
            const std::uint32_t s1 = rotate_right(words[index - 2], 17)
                                   ^ rotate_right(words[index - 2], 19)
                                   ^ (words[index - 2] >> 10U);
            words[index] = words[index - 16] + s0 + words[index - 7] + s1;
        }

        std::uint32_t a = state[0];
        std::uint32_t b = state[1];
        std::uint32_t c = state[2];
        std::uint32_t d = state[3];
        std::uint32_t e = state[4];
        std::uint32_t f = state[5];
        std::uint32_t g = state[6];
        std::uint32_t h = state[7];
        for (std::size_t index = 0; index < words.size(); ++index)
        {
            const std::uint32_t sum1 = rotate_right(e, 6) ^ rotate_right(e, 11)
                                     ^ rotate_right(e, 25);
            const std::uint32_t choose = (e & f) ^ (~e & g);
            const std::uint32_t temp1 = h + sum1 + choose + Constants[index] + words[index];
            const std::uint32_t sum0 = rotate_right(a, 2) ^ rotate_right(a, 13)
                                     ^ rotate_right(a, 22);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t temp2 = sum0 + majority;
            h = g;
            g = f;
            f = e;
            e = d + temp1;
            d = c;
            c = b;
            b = a;
            a = temp1 + temp2;
        }
        state[0] += a;
        state[1] += b;
        state[2] += c;
        state[3] += d;
        state[4] += e;
        state[5] += f;
        state[6] += g;
        state[7] += h;
    }

    std::array<std::uint32_t, 8> state = {0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U,
                                          0xa54ff53aU, 0x510e527fU, 0x9b05688cU,
                                          0x1f83d9abU, 0x5be0cd19U};
    std::array<Byte, 64> block{};
    std::size_t buffered = 0;
    std::uint64_t totalBytes = 0;
};

std::uint32_t crc32c(const Byte* data, std::size_t size) noexcept {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < size; ++index)
    {
        crc ^= data[index];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1U) ^ ((crc & 1U) ? 0x82F63B78U : 0U);
    }
    return crc ^ 0xFFFFFFFFU;
}

unsigned popcount64(std::uint64_t value) noexcept {
    unsigned count = 0;
    while (value)
    {
        value &= value - 1;
        ++count;
    }
    return count;
}

PhysicalDecodeResult failure(PhysicalDecodeError error) noexcept {
    PhysicalDecodeResult result;
    result.error = error;
    return result;
}

bool validate_move_wire(const PhysicalMoveV1& move) noexcept {
    if (move.kind > MoveDrop)
        return false;
    if (move.kind == MoveNone)
        return move.fromSquare == NoSquare && move.toSquare == NoSquare
            && move.auxPiece == PieceNone;
    if (move.kind == MoveDrop)
    {
        if (move.fromSquare != NoSquare || move.toSquare >= 64
            || move.auxPiece < PiecePawn || move.auxPiece > PieceQueen)
            return false;
        return move.auxPiece != PiecePawn
            || (move.toSquare / 8 != 0 && move.toSquare / 8 != 7);
    }
    if (move.fromSquare >= 64 || move.toSquare >= 64 || move.fromSquare == move.toSquare)
        return false;
    if (move.kind == MovePromotion)
        return move.auxPiece >= PieceKnight && move.auxPiece <= PieceQueen;
    return move.auxPiece == PieceNone;
}

bool validate_castling(const PhysicalStateV1& state) noexcept {
    struct Requirement {
        Byte bit;
        Byte kingSquare;
        Byte kingCode;
        Byte rookSquare;
        Byte rookCode;
    };
    constexpr std::array<Requirement, 4> Requirements = {
      Requirement{0, 4, 6, 7, 4}, Requirement{1, 4, 6, 0, 4},
      Requirement{2, 60, 14, 63, 12}, Requirement{3, 60, 14, 56, 12}};
    for (const Requirement& item : Requirements)
        if (state.castlingRights & (1U << item.bit))
        {
            if (state.board[item.kingSquare] != item.kingCode
                || state.board[item.rookSquare] != item.rookCode)
                return false;
            if (state.promotedMask
                & (square_bit(item.kingSquare) | square_bit(item.rookSquare)))
                return false;
        }
    return true;
}

bool validate_en_passant(const PhysicalStateV1& state) noexcept {
    if (state.rawEnPassantSquare != NoSquare && state.rawEnPassantSquare >= 64)
        return false;
    if (state.effectiveEnPassantSquare != NoSquare
        && state.effectiveEnPassantSquare != state.rawEnPassantSquare)
        return false;
    if (state.rawEnPassantSquare == NoSquare)
        return true;

    const int ep = state.rawEnPassantSquare;
    const int expectedRank = state.sideToMove == 0 ? 5 : 2;
    if (ep / 8 != expectedRank || state.board[ep] != PieceNone)
        return false;
    const int previousMover = state.sideToMove ^ 1;
    const int pawnPush = previousMover == 0 ? 8 : -8;
    const Byte pawnCode = previousMover == 0 ? PiecePawn : Byte(PiecePawn ^ 8U);
    if (ep + pawnPush < 0 || ep + pawnPush >= 64 || ep - pawnPush < 0
        || ep - pawnPush >= 64 || state.board[ep + pawnPush] != pawnCode
        || state.board[ep - pawnPush] != PieceNone)
        return false;

    if (state.effectiveEnPassantSquare != NoSquare)
    {
        const Byte attacker = state.sideToMove == 0 ? PiecePawn : Byte(PiecePawn ^ 8U);
        const int attackerRank = expectedRank + (state.sideToMove == 0 ? -1 : 1);
        const int epFile = ep % 8;
        bool found = false;
        for (int file : {epFile - 1, epFile + 1})
            if (file >= 0 && file < 8 && state.board[attackerRank * 8 + file] == attacker)
                found = true;
        if (!found)
            return false;
    }
    return true;
}

bool validate_move_state(const PhysicalRecordV1& record) noexcept {
    const PhysicalMoveV1& move = record.move;
    const PhysicalStateV1& state = record.state;
    if (move.kind == MoveNone)
        return true;
    if (move.kind == MoveDrop)
    {
        if (state.board[move.toSquare] != PieceNone)
            return false;
        const std::size_t index = std::size_t(state.sideToMove) * 5
                                + std::size_t(move.auxPiece - 1);
        return state.pockets[index] > 0;
    }

    const Byte moving = state.board[move.fromSquare];
    const Byte target = state.board[move.toSquare];
    if (moving == PieceNone || piece_owner(moving) != state.sideToMove)
        return false;
    if (target != PieceNone && piece_owner(target) == state.sideToMove)
        return false;
    const Byte type = piece_type(moving);

    if (move.kind == MovePromotion)
    {
        const int fromRank = state.sideToMove == 0 ? 6 : 1;
        const int toRank = state.sideToMove == 0 ? 7 : 0;
        if (type != PiecePawn || move.fromSquare / 8 != fromRank
            || move.toSquare / 8 != toRank)
            return false;
    }
    else if (type == PiecePawn && (move.toSquare / 8 == 0 || move.toSquare / 8 == 7))
        return false;

    if (move.kind == MoveEnPassant
        && (type != PiecePawn || move.toSquare != state.effectiveEnPassantSquare
            || target != PieceNone))
        return false;

    if (move.kind == MoveCastling)
    {
        const Byte expectedFrom = state.sideToMove == 0 ? 4 : 60;
        Byte requiredBit = 255;
        if (state.sideToMove == 0 && move.toSquare == 6)
            requiredBit = 0;
        else if (state.sideToMove == 0 && move.toSquare == 2)
            requiredBit = 1;
        else if (state.sideToMove == 1 && move.toSquare == 62)
            requiredBit = 2;
        else if (state.sideToMove == 1 && move.toSquare == 58)
            requiredBit = 3;
        if (type != PieceKing || move.fromSquare != expectedFrom || requiredBit == 255
            || !(state.castlingRights & (1U << requiredBit)))
            return false;
    }
    return true;
}

bool validate_standard_material(const PhysicalStateV1& state) noexcept {
    const unsigned promoted = popcount64(state.promotedMask);
    auto board_type_count = [&](Byte type, bool excludePromoted) {
        unsigned count = 0;
        for (unsigned square = 0; square < 64; ++square)
            if (state.board[square] != PieceNone && piece_type(state.board[square]) == type
                && (!excludePromoted || !(state.promotedMask & square_bit(square))))
                ++count;
        return count;
    };
    if (board_type_count(PiecePawn, false) + state.pockets[0] + state.pockets[5]
          + promoted != 16)
        return false;
    constexpr std::array<Byte, 4> Types = {PieceKnight, PieceBishop, PieceRook, PieceQueen};
    constexpr std::array<unsigned, 4> Expected = {4, 4, 4, 2};
    for (std::size_t index = 0; index < Types.size(); ++index)
        if (board_type_count(Types[index], true) + state.pockets[index + 1]
              + state.pockets[index + 6] != Expected[index])
            return false;
    return true;
}

}  // namespace

Digest physical_position_identity_v1(const PhysicalStateV1& state) noexcept {
    std::array<Byte, 32> packedBoard{};
    for (unsigned square = 0; square < 64; ++square)
        packedBoard[square / 2] |= Byte(state.board[square] << (4U * (square & 1U)));
    std::array<Byte, 8> promoted{};
    for (unsigned index = 0; index < promoted.size(); ++index)
        promoted[index] = Byte(state.promotedMask >> (8U * index));

    Sha256 hash;
    hash.update(reinterpret_cast<const Byte*>(PositionDomain), sizeof(PositionDomain) - 1);
    hash.update(packedBoard.data(), packedBoard.size());
    const std::array<Byte, 3> scalar = {
      state.sideToMove, state.castlingRights, state.effectiveEnPassantSquare};
    hash.update(scalar.data(), scalar.size());
    hash.update(state.pockets.data(), state.pockets.size());
    hash.update(promoted.data(), promoted.size());
    return hash.final();
}

PhysicalDecodeResult decode_physical_record_v1(const Byte* bytes, std::size_t size) noexcept {
    if (bytes == nullptr || size != PhysicalRecordBytes)
        return failure(PhysicalDecodeError::WRONG_SIZE);
    if (std::memcmp(bytes, "CHR1", 4) != 0)
        return failure(PhysicalDecodeError::MAGIC);
    if (get_le<std::uint16_t>(bytes + 4) != 1
        || get_le<std::uint16_t>(bytes + 6) != PhysicalRecordBytes)
        return failure(PhysicalDecodeError::VERSION);
    if (!range_all_zero(bytes + 245, bytes + 252))
        return failure(PhysicalDecodeError::RESERVED_BYTES);
    if (get_le<std::uint32_t>(bytes + 252) != crc32c(bytes, 252))
        return failure(PhysicalDecodeError::CRC32C);

    PhysicalRecordV1 candidate;
    candidate.sequence = get_le<std::uint64_t>(bytes + 8);
    std::copy_n(bytes + 16, candidate.gameId.size(), candidate.gameId.begin());
    std::copy_n(bytes + 32, candidate.trajectoryId.size(), candidate.trajectoryId.begin());
    if (all_zero(candidate.gameId) || all_zero(candidate.trajectoryId))
        return failure(PhysicalDecodeError::ZERO_IDENTITY);
    candidate.ply = get_le<std::uint32_t>(bytes + 48);
    candidate.flags = get_le<std::uint32_t>(bytes + 52);
    if ((candidate.flags & ~KnownFlags) != 0
        || bool(candidate.flags & FlagTrajectoryStart) != (candidate.ply == 0))
        return failure(PhysicalDecodeError::FLAGS);

    unsigned whiteKings = 0;
    unsigned blackKings = 0;
    std::uint64_t occupied = 0;
    std::uint64_t forbiddenPromoted = 0;
    for (unsigned square = 0; square < 64; ++square)
    {
        const Byte packed = bytes[56 + square / 2];
        const Byte code = Byte((packed >> (4U * (square & 1U))) & 0xFU);
        if (!valid_piece_code(code))
            return failure(PhysicalDecodeError::BOARD_PIECE_CODE);
        candidate.state.board[square] = code;
        if (code == PieceNone)
            continue;
        occupied |= square_bit(square);
        const Byte type = piece_type(code);
        if (type == PieceKing)
            piece_owner(code) == 0 ? ++whiteKings : ++blackKings;
        if (type == PiecePawn && (square / 8 == 0 || square / 8 == 7))
            return failure(PhysicalDecodeError::PAWN_PROMOTION_RANK);
        if (type == PiecePawn || type == PieceKing)
            forbiddenPromoted |= square_bit(square);
    }
    if (whiteKings != 1 || blackKings != 1)
        return failure(PhysicalDecodeError::KING_COUNT);

    candidate.state.promotedMask = get_le<std::uint64_t>(bytes + 88);
    if ((candidate.state.promotedMask & ~occupied) != 0
        || (candidate.state.promotedMask & forbiddenPromoted) != 0)
        return failure(PhysicalDecodeError::PROMOTED_MASK);

    std::copy_n(bytes + 96, candidate.state.pockets.size(), candidate.state.pockets.begin());
    for (std::size_t index = 0; index < candidate.state.pockets.size(); ++index)
        if (candidate.state.pockets[index] > PocketMaximums[index])
            return failure(PhysicalDecodeError::POCKET_BOUNDS);

    candidate.state.sideToMove = bytes[106];
    if (candidate.state.sideToMove > 1)
        return failure(PhysicalDecodeError::SIDE_TO_MOVE);
    candidate.state.castlingRights = bytes[107];
    if (candidate.state.castlingRights > 15)
        return failure(PhysicalDecodeError::CASTLING_RIGHTS);
    candidate.state.rawEnPassantSquare = bytes[108];
    candidate.state.repetitionOccurrences = bytes[109];
    candidate.state.claimPolicy = bytes[110];
    candidate.state.terminalReason = bytes[111];
    candidate.state.halfmoveClock = get_le<std::uint32_t>(bytes + 112);
    candidate.state.fullmoveNumber = get_le<std::uint32_t>(bytes + 116);
    candidate.state.effectiveEnPassantSquare = bytes[244];

    if (!validate_castling(candidate.state))
        return failure(PhysicalDecodeError::CASTLING_RIGHTS);
    if (!validate_en_passant(candidate.state))
        return failure(PhysicalDecodeError::EN_PASSANT);
    if (candidate.state.repetitionOccurrences < 1
        || candidate.state.repetitionOccurrences > 5)
        return failure(PhysicalDecodeError::REPETITION);
    if (candidate.state.claimPolicy > 1)
        return failure(PhysicalDecodeError::CLAIM_POLICY);
    if (candidate.state.terminalReason > TerminalAdjudication)
        return failure(PhysicalDecodeError::TERMINAL_REASON);
    if (candidate.state.fullmoveNumber == 0)
        return failure(PhysicalDecodeError::CLOCKS);

    candidate.move = PhysicalMoveV1{bytes[120], bytes[121], bytes[122], bytes[123]};
    if (!validate_move_wire(candidate.move))
        return failure(PhysicalDecodeError::MOVE_WIRE);
    const bool terminal = bool(candidate.flags & FlagTerminal);
    const bool movePresent = bool(candidate.flags & FlagMove);
    if (terminal != (candidate.state.terminalReason != TerminalOngoing)
        || movePresent != (candidate.move.kind != MoveNone) || terminal == movePresent)
        return failure(PhysicalDecodeError::MOVE_STATE);
    if (!validate_move_state(candidate))
        return failure(PhysicalDecodeError::MOVE_STATE);

    candidate.gameResultWhite = get_i8(bytes[124]);
    candidate.resultSideToMove = get_i8(bytes[125]);
    if (candidate.gameResultWhite < -1 || candidate.gameResultWhite > 1)
        return failure(PhysicalDecodeError::RESULT_PERSPECTIVE);
    const int expectedStm = candidate.state.sideToMove == 0
                          ? candidate.gameResultWhite
                          : -candidate.gameResultWhite;
    if (candidate.resultSideToMove != expectedStm)
        return failure(PhysicalDecodeError::RESULT_PERSPECTIVE);

    candidate.teacherScoreKind = bytes[126];
    candidate.teacherBound = bytes[127];
    candidate.teacherScoreValue = get_i32_le(bytes + 128);
    candidate.searchNodes = get_le<std::uint64_t>(bytes + 132);
    candidate.searchDepth = get_le<std::uint16_t>(bytes + 140);
    candidate.searchSelDepth = get_le<std::uint16_t>(bytes + 142);
    candidate.moveTimeMs = get_le<std::uint32_t>(bytes + 144);
    const bool teacherPresent = bool(candidate.flags & FlagTeacher);
    const bool teacherNetwork = bool(candidate.flags & FlagTeacherNetwork);
    if ((teacherNetwork && !teacherPresent) || teacherPresent != !terminal)
        return failure(PhysicalDecodeError::TEACHER_FRAMING);
    if (teacherPresent)
    {
        if ((candidate.teacherScoreKind != TeacherCentipawn
             && candidate.teacherScoreKind != TeacherMatePlies)
            || candidate.teacherBound != BoundExact || candidate.searchNodes == 0)
            return failure(PhysicalDecodeError::TEACHER_FRAMING);
    }
    else if (candidate.teacherScoreKind != TeacherNone || candidate.teacherBound != BoundNone
             || candidate.teacherScoreValue != 0 || candidate.searchNodes != 0
             || candidate.searchDepth != 0 || candidate.searchSelDepth != 0
             || candidate.moveTimeMs != 0)
        return failure(PhysicalDecodeError::TEACHER_FRAMING);

    std::copy_n(bytes + 148, candidate.positionIdentity.size(), candidate.positionIdentity.begin());
    std::copy_n(bytes + 180, candidate.historyPrefix.size(), candidate.historyPrefix.begin());
    std::copy_n(bytes + 212, candidate.provenance.size(), candidate.provenance.begin());
    if (all_zero(candidate.positionIdentity) || all_zero(candidate.historyPrefix)
        || all_zero(candidate.provenance))
        return failure(PhysicalDecodeError::ZERO_DIGEST);
    if (candidate.positionIdentity != physical_position_identity_v1(candidate.state))
        return failure(PhysicalDecodeError::POSITION_IDENTITY);

    if (candidate.state.terminalReason == TerminalFivefold
        && candidate.state.repetitionOccurrences != 5)
        return failure(PhysicalDecodeError::REPETITION);
    if (candidate.state.terminalReason == TerminalThreefold
        && (candidate.state.claimPolicy != 1 || candidate.state.repetitionOccurrences < 3))
        return failure(PhysicalDecodeError::REPETITION);
    if (candidate.state.terminalReason == TerminalCheckmate
        && candidate.resultSideToMove != -1)
        return failure(PhysicalDecodeError::RESULT_PERSPECTIVE);
    if ((candidate.state.terminalReason == TerminalStalemate
         || candidate.state.terminalReason == TerminalFivefold
         || candidate.state.terminalReason == TerminalThreefold
         || candidate.state.terminalReason == TerminalAdjudication)
        && candidate.resultSideToMove != 0)
        return failure(PhysicalDecodeError::RESULT_PERSPECTIVE);

    if (!(candidate.flags & FlagNonstandardRoot) && !validate_standard_material(candidate.state))
        return failure(PhysicalDecodeError::MATERIAL_CONSERVATION);

    PhysicalDecodeResult result;
    result.error = PhysicalDecodeError::NONE;
    result.record = candidate;
    return result;
}

std::string_view physical_decode_error_name(PhysicalDecodeError error) noexcept {
    switch (error)
    {
    case PhysicalDecodeError::NONE : return "NONE";
    case PhysicalDecodeError::WRONG_SIZE : return "WRONG_SIZE";
    case PhysicalDecodeError::MAGIC : return "MAGIC";
    case PhysicalDecodeError::VERSION : return "VERSION";
    case PhysicalDecodeError::RESERVED_BYTES : return "RESERVED_BYTES";
    case PhysicalDecodeError::CRC32C : return "CRC32C";
    case PhysicalDecodeError::ZERO_IDENTITY : return "ZERO_IDENTITY";
    case PhysicalDecodeError::FLAGS : return "FLAGS";
    case PhysicalDecodeError::BOARD_PIECE_CODE : return "BOARD_PIECE_CODE";
    case PhysicalDecodeError::KING_COUNT : return "KING_COUNT";
    case PhysicalDecodeError::PAWN_PROMOTION_RANK : return "PAWN_PROMOTION_RANK";
    case PhysicalDecodeError::PROMOTED_MASK : return "PROMOTED_MASK";
    case PhysicalDecodeError::POCKET_BOUNDS : return "POCKET_BOUNDS";
    case PhysicalDecodeError::SIDE_TO_MOVE : return "SIDE_TO_MOVE";
    case PhysicalDecodeError::CASTLING_RIGHTS : return "CASTLING_RIGHTS";
    case PhysicalDecodeError::EN_PASSANT : return "EN_PASSANT";
    case PhysicalDecodeError::REPETITION : return "REPETITION";
    case PhysicalDecodeError::CLAIM_POLICY : return "CLAIM_POLICY";
    case PhysicalDecodeError::TERMINAL_REASON : return "TERMINAL_REASON";
    case PhysicalDecodeError::CLOCKS : return "CLOCKS";
    case PhysicalDecodeError::MOVE_WIRE : return "MOVE_WIRE";
    case PhysicalDecodeError::MOVE_STATE : return "MOVE_STATE";
    case PhysicalDecodeError::RESULT_PERSPECTIVE : return "RESULT_PERSPECTIVE";
    case PhysicalDecodeError::TEACHER_FRAMING : return "TEACHER_FRAMING";
    case PhysicalDecodeError::ZERO_DIGEST : return "ZERO_DIGEST";
    case PhysicalDecodeError::POSITION_IDENTITY : return "POSITION_IDENTITY";
    case PhysicalDecodeError::MATERIAL_CONSERVATION : return "MATERIAL_CONSERVATION";
    }
    return "UNKNOWN";
}

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2
