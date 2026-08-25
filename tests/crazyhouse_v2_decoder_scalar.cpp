/*
  Fixture protocol for the Crazyhouse V2 physical decoder and scalar feature
  inventory. This target is deliberately separate from the normal engine.
*/

#include <array>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "nnue/crazyhouse_v2_features.h"
#include "nnue/crazyhouse_v2_physical.h"
#include "position.h"
#include "ruleset.h"

namespace {

using namespace Stockfish;
using namespace Stockfish::Eval::NNUE::CrazyhouseV2;
using Inventory = ScalarFeatureInventoryV1;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_decoder_scalar: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

std::vector<std::string> split_tabs(const std::string& text) {
    std::vector<std::string> output;
    std::size_t start = 0;
    while (true)
    {
        const std::size_t delimiter = text.find('\t', start);
        output.push_back(text.substr(start, delimiter - start));
        if (delimiter == std::string::npos)
            return output;
        start = delimiter + 1;
    }
}

int nibble(char value) {
    if (value >= '0' && value <= '9')
        return value - '0';
    if (value >= 'a' && value <= 'f')
        return value - 'a' + 10;
    return -1;
}

std::vector<Byte> parse_hex(const std::string& text) {
    require(text.size() % 2 == 0, "hex payload has odd width");
    std::vector<Byte> output(text.size() / 2);
    for (std::size_t index = 0; index < output.size(); ++index)
    {
        const int high = nibble(text[index * 2]);
        const int low = nibble(text[index * 2 + 1]);
        require(high >= 0 && low >= 0, "hex payload contains a non-lowercase-hex byte");
        output[index] = Byte((high << 4) | low);
    }
    return output;
}

std::string digest_hex(const Digest& digest) {
    constexpr char Digits[] = "0123456789abcdef";
    std::string output;
    output.reserve(64);
    for (Byte value : digest)
    {
        output.push_back(Digits[value >> 4]);
        output.push_back(Digits[value & 15]);
    }
    return output;
}

std::string join(const Inventory::Result& features, Color perspective) {
    std::ostringstream output;
    for (std::size_t index = 0; index < features.size[perspective]; ++index)
    {
        if (index)
            output << ',';
        output << features.active[perspective][index];
    }
    return output.str();
}

void verify_wrong_ruleset() {
    StateInfo state;
    Position position(Ruleset::CHESS);
    const auto error = position.set(
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false,
      Ruleset::CHESS, &state);
    require(!error.has_value(), "standard control setup failed");
    const Inventory::Result features = Inventory::extract(position);
    require(features.status == Inventory::Status::WRONG_RULESET,
            "standard chess entered the Crazyhouse V2 feature path");
    require(features.size[WHITE] == 0 && features.size[BLACK] == 0,
            "failed feature extraction retained partial rows");
}

void verify_position_state(const PhysicalStateV1& physical, const Position& position) {
    for (unsigned square = 0; square < 64; ++square)
        require(physical.board[square]
                  == static_cast<Byte>(position.piece_on(Square(square))),
                "decoded board differs from Position");
    require(physical.promotedMask == position.promoted_pieces(),
            "decoded promoted mask differs from Position");
    constexpr std::array<PieceType, 5> Types = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    for (Color owner : {WHITE, BLACK})
        for (std::size_t type = 0; type < Types.size(); ++type)
            require(physical.pockets[std::size_t(owner) * 5 + type]
                      == position.pocket_count(owner, Types[type]),
                    "decoded pocket count differs from Position");
    require(physical.sideToMove == static_cast<Byte>(position.side_to_move()),
            "decoded side to move differs from Position");
}

void run_valid(const std::vector<std::string>& fields) {
    require(fields.size() == 4 && !fields[1].empty() && !fields[3].empty(),
            "VALID protocol field count");
    const std::vector<Byte> bytes = parse_hex(fields[2]);
    const PhysicalDecodeResult decoded = decode_physical_record_v1(bytes.data(), bytes.size());
    require(decoded.ok(), fields[1] + " decoder rejected: "
                            + std::string(physical_decode_error_name(decoded.error)));

    StateInfo state;
    Position position(Ruleset::CRAZYHOUSE);
    const auto error = position.set(fields[3], false, Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), fields[1] + " Position rejected FEN"
                                  + (error ? ": " + std::string(error->what()) : ""));
    verify_position_state(decoded.record.state, position);

    const Inventory::Result fromRecord = Inventory::extract(decoded.record.state);
    const Inventory::Result fromPosition = Inventory::extract(position);
    require(fromRecord.ok() && fromPosition.ok(), fields[1] + " feature extraction failed");
    for (Color perspective : {WHITE, BLACK})
    {
        require(fromRecord.size[perspective] == fromPosition.size[perspective],
                fields[1] + " feature size parity failed");
        for (std::size_t index = 0; index < fromRecord.size[perspective]; ++index)
            require(fromRecord.active[perspective][index]
                      == fromPosition.active[perspective][index],
                    fields[1] + " ordered feature parity failed");
    }

    std::cout << "OK\t" << fields[1] << '\t'
              << digest_hex(decoded.record.positionIdentity) << '\t'
              << join(fromRecord, WHITE) << '\t' << join(fromRecord, BLACK) << '\n';
}

void run_invalid(const std::vector<std::string>& fields) {
    require(fields.size() == 4 && !fields[1].empty() && !fields[2].empty(),
            "INVALID protocol field count");
    const std::vector<Byte> bytes = parse_hex(fields[3]);
    const PhysicalDecodeResult decoded = decode_physical_record_v1(bytes.data(), bytes.size());
    require(!decoded.ok(), fields[1] + " adversarial record was accepted");
    const std::string observed(physical_decode_error_name(decoded.error));
    require(observed == fields[2], fields[1] + " expected " + fields[2]
                                    + " but observed " + observed);
    require(decoded.record.sequence == 0 && decoded.record.state.promotedMask == 0
              && decoded.record.positionIdentity == Digest{},
            fields[1] + " failed decode exposed partial state");
    std::cout << "REJECT\t" << fields[1] << '\t' << observed << '\n';
}

}  // namespace

int main() {
    Attacks::init();
    Position::init();
    verify_wrong_ruleset();

    std::string line;
    std::size_t valid = 0;
    std::size_t invalid = 0;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "protocol contains an empty line");
        const std::vector<std::string> fields = split_tabs(line);
        if (fields[0] == "VALID")
        {
            run_valid(fields);
            ++valid;
        }
        else if (fields[0] == "INVALID")
        {
            run_invalid(fields);
            ++invalid;
        }
        else
            fail("unknown protocol verb");
    }
    require(std::cin.eof(), "stdin read failed");
    require(valid > 0 && invalid > 0, "protocol did not exercise both paths");
    std::cout << "SUMMARY\tvalid=" << valid << "\tinvalid=" << invalid
              << "\twrong_ruleset=REJECT\tdimensions=" << Inventory::Dimensions << '\n';
    return EXIT_SUCCESS;
}
