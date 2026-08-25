/*
  Direct projection participant for the frozen Crazyhouse G4 corpus.

  This executable is a test adapter over the production Position, MoveList,
  make/history, repetition, FEN, terminal and perft code. It owns no rules and
  is not part of the UCI product surface. The production executable remains the
  separate UCI participant.
*/

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "crazyhouse_profile.h"
#include "movegen.h"
#include "perft.h"
#include "position.h"
#include "uci.h"

namespace {

using namespace Stockfish;

constexpr std::string_view Protocol = "crazyhouse-engine-direct-projection/v1";

std::string json_string(std::string_view value) {
    constexpr char Hex[] = "0123456789abcdef";
    std::string    result;
    result.reserve(value.size() + 2);
    result.push_back('"');
    for (unsigned char ch : value)
    {
        switch (ch)
        {
        case '"': result += "\\\""; break;
        case '\\': result += "\\\\"; break;
        case '\b': result += "\\b"; break;
        case '\f': result += "\\f"; break;
        case '\n': result += "\\n"; break;
        case '\r': result += "\\r"; break;
        case '\t': result += "\\t"; break;
        default:
            if (ch < 0x20)
            {
                result += "\\u00";
                result.push_back(Hex[ch >> 4]);
                result.push_back(Hex[ch & 0x0F]);
            }
            else
                result.push_back(char(ch));
        }
    }
    result.push_back('"');
    return result;
}

std::vector<std::string> split(std::string_view value, char delimiter) {
    std::vector<std::string> fields;
    usize                    begin = 0;
    for (;;)
    {
        const usize end = value.find(delimiter, begin);
        fields.emplace_back(value.substr(begin, end == std::string_view::npos
                                                  ? value.size() - begin
                                                  : end - begin));
        if (end == std::string_view::npos)
            break;
        begin = end + 1;
    }
    return fields;
}

bool parse_nonnegative_int(std::string_view text, int& value) {
    value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    return result.ec == std::errc() && result.ptr == text.data() + text.size() && value >= 0;
}

std::string square_name(Square square) {
    return std::string{char('a' + file_of(square)), char('1' + rank_of(square))};
}

std::string reason_name(CrazyhouseTerminalReason reason) {
    switch (reason)
    {
    case CrazyhouseTerminalReason::ONGOING: return "ongoing";
    case CrazyhouseTerminalReason::CHECKMATE: return "checkmate";
    case CrazyhouseTerminalReason::STALEMATE: return "stalemate";
    case CrazyhouseTerminalReason::FIVEFOLD_REPETITION: return "fivefold_repetition";
    case CrazyhouseTerminalReason::THREEFOLD_REPETITION_CLAIM:
        return "threefold_repetition_claim";
    }
    std::abort();
}

std::string result_name(const CrazyhouseTerminalStatus& status) {
    if (!status.ended())
        return "*";
    if (!status.winner.has_value())
        return "1/2-1/2";
    return *status.winner == WHITE ? "1-0" : "0-1";
}

std::string winner_json(const CrazyhouseTerminalStatus& status) {
    if (!status.winner.has_value())
        return "null";
    return json_string(*status.winner == WHITE ? "white" : "black");
}

std::string string_array(const std::vector<std::string>& values) {
    std::ostringstream output;
    output << '[';
    for (usize i = 0; i < values.size(); ++i)
    {
        if (i)
            output << ',';
        output << json_string(values[i]);
    }
    output << ']';
    return output.str();
}

std::string pockets_json(const Position& position) {
    constexpr std::array<PieceType, 5> Types = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    constexpr std::array<std::string_view, 5> Names = {"pawn", "knight", "bishop", "rook",
                                                        "queen"};
    std::ostringstream output;
    output << '{';
    for (Color color : {WHITE, BLACK})
    {
        if (color == BLACK)
            output << ',';
        output << json_string(color == WHITE ? "white" : "black") << ":{";
        for (usize i = 0; i < Types.size(); ++i)
        {
            if (i)
                output << ',';
            output << json_string(Names[i]) << ':' << position.pocket_count(color, Types[i]);
        }
        output << '}';
    }
    output << '}';
    return output.str();
}

struct FenFields {
    std::string board;
    std::string active;
    std::string castling;
    std::string ep;
    int         halfmove = 0;
    int         fullmove = 0;
};

bool parse_fen_fields(const std::string& fen, FenFields& fields) {
    std::string halfmove;
    std::string fullmove;
    std::string extra;
    std::istringstream input(fen);
    if (!(input >> fields.board >> fields.active >> fields.castling >> fields.ep >> halfmove
          >> fullmove)
        || (input >> extra))
        return false;
    return parse_nonnegative_int(halfmove, fields.halfmove)
        && parse_nonnegative_int(fullmove, fields.fullmove) && fields.fullmove > 0
        && (fields.active == "w" || fields.active == "b");
}

void emit_failure(std::string_view id, std::string_view error) {
    std::cout << "{\"schema\":" << json_string(Protocol) << ",\"id\":" << json_string(id)
              << ",\"ok\":false,\"error\":" << json_string(error) << "}\n";
}

bool process_case(const std::vector<std::string>& fields) {
    if (fields.size() != 6 || fields[0] != "CASE")
    {
        emit_failure("<protocol>", "malformed six-field CASE record");
        return false;
    }

    const std::string& id      = fields[1];
    const std::string& op      = fields[2];
    const std::string& fen     = fields[4];
    const std::string& moveSet = fields[5];
    int                depth   = 0;
    if (id.empty() || (op != "inspect" && op != "transition" && op != "perft"
                       && op != "capacity")
        || !parse_nonnegative_int(fields[3], depth) || (op == "perft" && depth == 0))
    {
        emit_failure(id, "invalid CASE identity, operation or depth");
        return false;
    }

    std::deque<StateInfo> states;
    states.emplace_back();
    Position position(Ruleset::CRAZYHOUSE);
    if (const auto error = position.set(fen, false, Ruleset::CRAZYHOUSE, &states.back()))
    {
        emit_failure(id, std::string("FEN rejected: ") + error->what());
        return false;
    }

    int moveCount = 0;
    if (!moveSet.empty())
        for (const std::string& token : split(moveSet, ' '))
        {
            if (token.empty())
            {
                emit_failure(id, "empty move token");
                return false;
            }
            const Move move = UCIEngine::to_move(position, token);
            if (move == Move::none())
            {
                emit_failure(id, std::string("illegal move: ") + token);
                return false;
            }
            states.emplace_back();
            position.do_move(move, states.back());
            ++moveCount;
        }

    const std::string canonicalFen = position.fen();
    const Key         canonicalKey = position.key();
    FenFields         fenFields;
    if (!parse_fen_fields(canonicalFen, fenFields))
    {
        emit_failure(id, "production canonical FEN did not contain six valid fields");
        return false;
    }

    std::vector<std::string> legalMoves;
    usize                    dropCount = 0;
    for (Move move : MoveList<LEGAL>(position))
    {
        legalMoves.push_back(UCIEngine::move(move, position.is_chess960()));
        dropCount += move.is_drop();
    }
    std::sort(legalMoves.begin(), legalMoves.end());
    if (std::adjacent_find(legalMoves.begin(), legalMoves.end()) != legalMoves.end())
    {
        emit_failure(id, "duplicate legal move");
        return false;
    }

    std::vector<std::string> promotedSquares;
    for (Bitboard promoted = position.promoted_pieces(); promoted;)
        promotedSquares.push_back(square_name(pop_lsb(promoted)));
    std::sort(promotedSquares.begin(), promotedSquares.end());

    const CrazyhouseTerminalStatus terminal =
      position.crazyhouse_terminal_status(CrazyhouseClaimPolicy::AUTOMATIC_ONLY);

    bool perftObserved = false;
    u64  perftNodes    = 0;
    if (op == "perft")
    {
        std::ostringstream discardedRootBreakdown;
        std::streambuf*     original = std::cout.rdbuf(discardedRootBreakdown.rdbuf());
        perftNodes                   = Benchmark::perft<true>(position, Depth(depth));
        std::cout.rdbuf(original);
        perftObserved = true;
        if (position.fen() != canonicalFen || position.key() != canonicalKey)
        {
            emit_failure(id, "production perft did not restore the root");
            return false;
        }
    }

    std::ostringstream output;
    output << "{\"schema\":" << json_string(Protocol) << ",\"id\":" << json_string(id)
           << ",\"ok\":true,\"profile_id\":"
           << json_string(Stockfish::CrazyhouseProfile::Id) << ",\"profile_sha256\":"
           << json_string(Stockfish::CrazyhouseProfile::Sha256) << ",\"op\":" << json_string(op)
           << ",\"move_count\":" << moveCount << ",\"state\":{\"canonical_fen\":"
           << json_string(canonicalFen) << ",\"turn\":"
           << json_string(fenFields.active == "w" ? "white" : "black")
           << ",\"castling_rights\":" << json_string(fenFields.castling)
           << ",\"ep_square\":" << (fenFields.ep == "-" ? "null" : json_string(fenFields.ep))
           << ",\"halfmove_clock\":" << fenFields.halfmove << ",\"fullmove_number\":"
           << fenFields.fullmove << ",\"pockets\":" << pockets_json(position)
           << ",\"promoted_squares\":" << string_array(promotedSquares)
           << ",\"in_check\":" << (position.checkers() ? "true" : "false")
           << ",\"legal_moves\":" << string_array(legalMoves) << ",\"terminal\":{\"ended\":"
           << (terminal.ended() ? "true" : "false") << ",\"reason\":"
           << json_string(reason_name(terminal.reason)) << ",\"winner\":" << winner_json(terminal)
           << ",\"result\":" << json_string(result_name(terminal)) << "}},\"direct\":{"
           << "\"repetition_occurrences\":" << position.repetition_occurrences()
           << ",\"is_draw_at_ply_1\":" << (position.is_draw(1) ? "true" : "false")
           << ",\"automatic_terminal_reason\":" << json_string(reason_name(terminal.reason))
           << ",\"automatic_terminal_winner\":" << winner_json(terminal)
           << ",\"automatic_terminal_result\":" << json_string(result_name(terminal))
           << "},\"legal_move_count\":" << legalMoves.size() << ",\"drop_move_count\":"
           << dropCount << ",\"non_drop_move_count\":" << legalMoves.size() - dropCount
           << ",\"perft_nodes\":";
    if (perftObserved)
        output << perftNodes;
    else
        output << "null";
    output << "}\n";
    std::cout << output.str();
    return true;
}

}  // namespace

int main() {
    Stockfish::Attacks::init();
    Stockfish::Position::init();

    std::cout << "{\"schema\":" << json_string(Protocol) << ",\"kind\":\"capabilities\""
              << ",\"profile_id\":" << json_string(Stockfish::CrazyhouseProfile::Id)
              << ",\"profile_sha256\":" << json_string(Stockfish::CrazyhouseProfile::Sha256)
              << ",\"operations\":[\"inspect\",\"transition\",\"perft\",\"capacity\"]}\n";

    std::string line;
    while (std::getline(std::cin, line))
    {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (line == "QUIT")
            return EXIT_SUCCESS;
        if (!process_case(split(line, '\t')))
            return EXIT_FAILURE;
    }

    emit_failure("<protocol>", "early EOF before QUIT");
    return EXIT_FAILURE;
}
