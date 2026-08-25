/*
  Dedicated executable for the authenticated Crazyhouse V2 legacy control.
  It is intentionally absent from the normal engine source graph.
*/

#include <array>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "bitboard.h"
#include "nnue/crazyhouse_legacy_network.h"
#include "nnue/crazyhouse_v2_legacy_control.h"
#include "position.h"

namespace {

using namespace Stockfish;
using Control = Eval::NNUE::LegacyControlNetworkV2;
using Legacy  = Eval::NNUE::LegacyCrazyhouseNetworkV1;
using u8      = std::uint8_t;
using u32     = std::uint32_t;

[[noreturn]] void fail(const std::string& message) {
    std::cerr << "FAIL crazyhouse_v2_legacy_control: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& message) {
    if (!condition)
        fail(message);
}

std::vector<u8> read_file(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    require(bool(stream), "could not open " + path.string());
    stream.seekg(0, std::ios::end);
    const std::streamoff end = stream.tellg();
    require(end >= 0, "could not size " + path.string());
    stream.seekg(0, std::ios::beg);
    std::vector<u8> bytes(static_cast<std::size_t>(end));
    if (!bytes.empty())
        stream.read(reinterpret_cast<char*>(bytes.data()), std::streamsize(bytes.size()));
    require(stream.gcount() == std::streamsize(bytes.size()) && bool(stream),
            "could not read " + path.string());
    return bytes;
}

u32 crc32c(const u8* data, std::size_t size) noexcept {
    u32 value = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < size; ++index)
    {
        value ^= data[index];
        for (int bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1U) != 0 ? 0x82F63B78U : 0U);
    }
    return value ^ 0xFFFFFFFFU;
}

void write_u32(std::vector<u8>& bytes, std::size_t offset, u32 value) {
    require(offset + 4 <= bytes.size(), "test mutation write is out of range");
    for (int index = 0; index < 4; ++index)
        bytes[offset + std::size_t(index)] = static_cast<u8>(value >> (8 * index));
}

void repair_header_crc(std::vector<u8>& bytes) {
    require(bytes.size() >= Control::HeaderBytes, "test mutation header is truncated");
    write_u32(bytes, 1'020, crc32c(bytes.data(), 1'020));
}

std::size_t loader_negative_matrix(const std::filesystem::path& containerPath,
                                   const std::filesystem::path& legacyPath,
                                   const std::filesystem::path& productivePath,
                                   const Control::Requirements& requirements) {
    const std::vector<u8> container  = read_file(containerPath);
    const std::vector<u8> legacy     = read_file(legacyPath);
    const std::vector<u8> productive = read_file(productivePath);
    require(container.size() == Control::FileBytes, "positive container size drift");

    std::size_t negatives = 0;
    const auto  reject    = [&](const std::string& name, const std::vector<u8>& candidate,
                            const Control::Requirements& expected = Control::Requirements{}) {
        const Control::Requirements& applied =
          expected.converterSha256.empty() ? requirements : expected;
        Control    network;
        const auto result = network.load_bytes(candidate.data(), candidate.size(), applied);
        require(!result.ok(), name + " mutation was accepted");
        require(!network.loaded(), name + " mutation retained a loaded network");
        ++negatives;
    };
    const auto reject_header = [&](const std::string& name, std::size_t offset) {
        std::vector<u8> candidate = container;
        candidate[offset] ^= 1;
        repair_header_crc(candidate);
        reject(name, candidate);
    };

    reject_header("magic", 0);
    reject_header("magic-padding", 11);
    for (const std::pair<const char*, std::size_t> field : {std::pair{"endian", std::size_t(16)},
                                                            {"version-major", 20},
                                                            {"version-minor", 22},
                                                            {"header-bytes", 24},
                                                            {"payload-bytes", 28},
                                                            {"file-bytes", 36},
                                                            {"committed", 44},
                                                            {"purpose", 45},
                                                            {"origin", 46},
                                                            {"dirty-source", 47}})
        reject_header(field.first, field.second);
    for (std::size_t offset = 48; offset <= 96; offset += 4)
        reject_header("architecture-" + std::to_string(offset), offset);
    for (const std::pair<const char*, std::size_t> field :
         {std::pair{"section-count", std::size_t(100)},
          {"directory-entry-bytes", 102},
          {"directory-offset", 104},
          {"payload-offset", 108},
          {"transformer-arithmetic", 112},
          {"psqt-arithmetic", 113},
          {"dense-arithmetic", 114},
          {"activation", 115}})
        reject_header(field.first, field.second);
    reject_header("reserved-116", 116);
    reject_header("reserved-376", 376);
    reject_header("reserved-960", 960);
    for (const std::pair<const char*, std::size_t> identity :
         {std::pair{"payload-identity", std::size_t(144)},
          {"rule-identity", 176},
          {"feature-identity", 208},
          {"contract-identity", 240},
          {"origin-identity", 272},
          {"converter-identity", 304},
          {"source-commit-identity", 336},
          {"source-tree-identity", 356}})
        reject_header(identity.first, identity.second);

    for (const std::pair<const char*, std::size_t> field :
         {std::pair{"directory-id", std::size_t(384)},
          {"directory-dtype", 386},
          {"directory-rank", 387},
          {"directory-offset-field", 388},
          {"directory-bytes-field", 396},
          {"directory-shape0", 400},
          {"directory-shape1", 404},
          {"directory-shape2", 408}})
        reject_header(field.first, field.second);
    for (std::size_t section = 0; section < 9; ++section)
        reject_header("section-digest-" + std::to_string(section + 1), 384 + section * 64 + 32);

    {
        std::vector<u8> candidate = container;
        candidate[1'020] ^= 1;
        reject("header-crc", candidate);
    }
    constexpr std::array<std::size_t, 9> SectionOffsets = {1'024,      2'048,      56'625'152,
                                                           58'394'624, 58'395'136, 58'526'208,
                                                           58'527'232, 58'535'424, 58'535'456};
    for (std::size_t section = 0; section < SectionOffsets.size(); ++section)
    {
        std::vector<u8> candidate = container;
        candidate[SectionOffsets[section]] ^= 1;
        reject("payload-section-" + std::to_string(section + 1), candidate);
    }
    {
        Control    network;
        const auto result =
          network.load_bytes(container.data(), container.size() - 1, requirements);
        require(!result.ok() && !network.loaded(), "truncated container was accepted");
        ++negatives;
    }
    {
        std::vector<u8> candidate = container;
        candidate.push_back(0);
        reject("extended-container", candidate);
    }
    reject("registered-legacy-artifact", legacy);
    reject("productive-v2-artifact", productive);
    reject("unrelated-exact-size-bytes", std::vector<u8>(Control::FileBytes));
    {
        Control    network;
        const auto result = network.load_bytes(nullptr, Control::FileBytes, requirements);
        require(!result.ok() && !network.loaded(), "null input was accepted");
        ++negatives;
    }
    {
        std::filesystem::path missing = containerPath;
        missing += ".missing";
        require(!std::filesystem::exists(missing), "missing-path control exists");
        Control network;
        require(!network.load_file(missing, requirements).ok() && !network.loaded(),
                "missing path was accepted");
        ++negatives;
    }
    {
        Control network;
        require(!network.load_file(containerPath.parent_path(), requirements).ok()
                  && !network.loaded(),
                "directory path was accepted");
        ++negatives;
    }
    for (const std::pair<const char*, int> mutation : {std::pair{"converter-requirement", 0},
                                                       {"commit-requirement", 1},
                                                       {"tree-requirement", 2},
                                                       {"noncanonical-requirement", 3}})
    {
        Control::Requirements changed = requirements;
        if (mutation.second == 0)
            changed.converterSha256[0] = changed.converterSha256[0] == '0' ? '1' : '0';
        else if (mutation.second == 1)
            changed.sourceCommit[0] = changed.sourceCommit[0] == '0' ? '1' : '0';
        else if (mutation.second == 2)
            changed.sourceTree[0] = changed.sourceTree[0] == '0' ? '1' : '0';
        else
            changed.sourceCommit[0] = 'A';
        reject(mutation.first, container, changed);
    }
    {
        Control network;
        require(network.load_bytes(container.data(), container.size(), requirements).ok()
                  && network.loaded(),
                "failed-replacement control did not load positive bytes");
        std::vector<u8> candidate = container;
        candidate[0] ^= 1;
        repair_header_crc(candidate);
        require(!network.load_bytes(candidate.data(), candidate.size(), requirements).ok()
                  && !network.loaded(),
                "failed replacement retained the positive network");
        ++negatives;
    }
    return negatives;
}

std::size_t
evaluation_negative_matrix(const Control&                                        network,
                           const Eval::NNUE::LegacyCrazyhouseFeaturesV1::Result& features,
                           Color                                                 sideToMove) {
    std::size_t negatives = 0;
    {
        Control unloaded;
        require(!unloaded.evaluate(features, sideToMove).ok(), "unloaded evaluation passed");
        ++negatives;
    }
    {
        auto invalid   = features;
        invalid.status = Eval::NNUE::LegacyCrazyhouseFeaturesV1::Status::WrongRuleset;
        require(!network.evaluate(invalid, sideToMove).ok(), "uncertified features passed");
        ++negatives;
    }
    require(!network.evaluate(features, static_cast<Color>(COLOR_NB)).ok(), "invalid side passed");
    ++negatives;
    {
        auto invalid            = features;
        invalid.boardPieceCount = 1;
        require(!network.evaluate(invalid, sideToMove).ok(), "invalid board count passed");
        ++negatives;
    }
    {
        auto invalid        = features;
        invalid.layerBucket = (invalid.layerBucket + 1) % Control::LayerStacks;
        require(!network.evaluate(invalid, sideToMove).ok(), "invalid bucket passed");
        ++negatives;
    }
    {
        auto invalid = features;
        invalid.active[WHITE].push_back(Control::FeatureDimensions);
        require(!network.evaluate(invalid, sideToMove).ok(), "invalid feature index passed");
        ++negatives;
    }
    {
        auto invalid = features;
        require(!invalid.active[WHITE].empty(), "duplicate control has no white feature");
        invalid.active[WHITE].push_back(invalid.active[WHITE].front());
        require(!network.evaluate(invalid, sideToMove).ok(), "duplicate feature passed");
        ++negatives;
    }
    {
        auto invalid = features;
        invalid.active[WHITE].assign(Control::MaximumActive + 1, 0);
        require(!network.evaluate(invalid, sideToMove).ok(), "active overflow passed");
        ++negatives;
    }
    return negatives;
}

void set_position(Position& position, const std::string& fen, StateInfo& state) {
    const auto error = position.set(fen, false, Ruleset::CRAZYHOUSE, &state);
    require(!error.has_value(), "position setup rejected: " + fen);
}

template<typename Getter>
std::string join_control(const Control::Trace& trace, Getter getter) {
    std::ostringstream output;
    for (std::size_t bucket = 0; bucket < Control::LayerStacks; ++bucket)
    {
        if (bucket)
            output << ',';
        output << getter(trace.buckets[bucket]);
    }
    return output.str();
}

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 7,
            "usage: crazyhouse-v2-legacy-control-tests <container> <legacy> <productive-v2> "
            "<converter-sha256> <source-commit> <source-tree>");
    const std::filesystem::path containerPath(argv[1]);
    const std::filesystem::path legacyPath(argv[2]);
    const std::filesystem::path productivePath(argv[3]);
    const Control::Requirements requirements{argv[4], argv[5], argv[6]};

    Attacks::init();
    Position::init();

    const std::size_t loaderNegatives =
      loader_negative_matrix(containerPath, legacyPath, productivePath, requirements);
    require(loaderNegatives >= 62, "loader negative matrix is below the frozen minimum");

    Control    control;
    const auto controlLoad = control.load_file(containerPath, requirements);
    require(controlLoad.ok() && control.loaded(),
            "positive control load failed: " + controlLoad.message);
    Legacy     legacy;
    const auto legacyLoad = legacy.load_file(legacyPath);
    require(legacyLoad.status == Legacy::LoadStatus::Success && legacy.loaded(),
            "registered legacy evaluator load failed: " + legacyLoad.message);

    std::vector<std::string> fens;
    std::string              fen;
    while (std::getline(std::cin, fen))
    {
        require(!fen.empty(), "input contains an empty FEN line");
        fens.push_back(fen);
    }
    require(std::cin.eof(), "stdin read failed");
    require(!fens.empty(), "no FEN cases were supplied");

    StateInfo firstState;
    Position  first(Ruleset::CRAZYHOUSE);
    set_position(first, fens.front(), firstState);
    const auto firstFeatures = Eval::NNUE::LegacyCrazyhouseFeaturesV1::extract(first);
    require(firstFeatures.ok(), "first feature extraction failed");
    const std::size_t evalNegatives =
      evaluation_negative_matrix(control, firstFeatures, first.side_to_move());

    std::cout << "META\tloader_negatives=" << loaderNegatives
              << "\teval_negatives=" << evalNegatives
              << "\tcontainer_sha256=" << control.file_sha256()
              << "\tconverter_sha256=" << control.converter_sha256()
              << "\tsource_commit=" << control.source_commit()
              << "\tsource_tree=" << control.source_tree() << '\n';

    for (const std::string& inputFen : fens)
    {
        StateInfo state;
        Position  position(Ruleset::CRAZYHOUSE);
        set_position(position, inputFen, state);
        const auto features = Eval::NNUE::LegacyCrazyhouseFeaturesV1::extract(position);
        require(features.ok(), "certified feature extraction failed: " + inputFen);
        const auto candidate = control.evaluate(features, position.side_to_move());
        require(candidate.ok(), "control evaluation failed: " + candidate.message);
        const auto reference = legacy.evaluate_full_refresh(position);
        require(reference.ok(), "registered legacy evaluation failed: " + reference.message);

        const Control::Trace& trace = *candidate.trace;
        require(trace.selectedBucket == reference.output->selectedBucket,
                "selected bucket differs from registered legacy evaluator");
        for (std::size_t bucket = 0; bucket < Control::LayerStacks; ++bucket)
        {
            require(trace.buckets[bucket].psqt == reference.output->buckets[bucket].psqt,
                    "raw PSQT differs from registered legacy evaluator");
            require(trace.buckets[bucket].outputAffine
                      == reference.output->buckets[bucket].positional,
                    "raw positional differs from registered legacy evaluator");
        }

        std::cout << "OK\t" << position.fen() << '\t' << unsigned(trace.selectedBucket) << '\t'
                  << join_control(trace,
                                  [](const Control::BucketTrace& value) { return value.psqt; })
                  << '\t'
                  << join_control(
                       trace, [](const Control::BucketTrace& value) { return value.outputAffine; })
                  << '\t' << Control::trace_sha256(features, position.side_to_move(), trace)
                  << '\n';
    }
    return EXIT_SUCCESS;
}
