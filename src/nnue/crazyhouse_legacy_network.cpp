/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_legacy_network.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <system_error>
#include <utility>
#include <vector>

#if defined(CRAZYHOUSE_LEGACY_EMBED_FILE) && !defined(_MSC_VER)
    #define INCBIN_SILENCE_BITCODE_WARNING
    #include "../incbin/incbin.h"
INCBIN(CrazyhouseLegacyV1, CRAZYHOUSE_LEGACY_EMBED_FILE);
#endif

#include "../position.h"
#include "layers/affine_transform.h"
#include "simd.h"

namespace Stockfish::Eval::NNUE {

namespace {

using Network = LegacyCrazyhouseNetworkV1;

class Reader {
   public:
    Reader(const unsigned char* data, std::size_t size) :
        current_(data),
        end_(data + size) {}

    bool read_u32(std::uint32_t& value) noexcept {
        if (remaining() < 4)
            return false;
        value = std::uint32_t(current_[0]) | (std::uint32_t(current_[1]) << 8)
              | (std::uint32_t(current_[2]) << 16) | (std::uint32_t(current_[3]) << 24);
        current_ += 4;
        return true;
    }

    bool read_i16(std::int16_t* output, std::size_t count) noexcept {
        if (count > remaining() / 2)
            return false;
        for (std::size_t i = 0; i < count; ++i)
        {
            const std::uint16_t bits =
              std::uint16_t(current_[0]) | (std::uint16_t(current_[1]) << 8);
            std::memcpy(output + i, &bits, sizeof(bits));
            current_ += 2;
        }
        return true;
    }

    bool read_i32(std::int32_t* output, std::size_t count) noexcept {
        if (count > remaining() / 4)
            return false;
        for (std::size_t i = 0; i < count; ++i)
        {
            const std::uint32_t bits =
              std::uint32_t(current_[0]) | (std::uint32_t(current_[1]) << 8)
              | (std::uint32_t(current_[2]) << 16) | (std::uint32_t(current_[3]) << 24);
            std::memcpy(output + i, &bits, sizeof(bits));
            current_ += 4;
        }
        return true;
    }

    bool read_i8(std::int8_t* output, std::size_t count) noexcept {
        if (count > remaining())
            return false;
        std::memcpy(output, current_, count);
        current_ += count;
        return true;
    }

    bool read_string(std::string& output, std::size_t count) {
        if (count > remaining())
            return false;
        output.assign(reinterpret_cast<const char*>(current_), count);
        current_ += count;
        return true;
    }

    std::size_t          remaining() const noexcept { return std::size_t(end_ - current_); }
    const unsigned char* current() const noexcept { return current_; }

   private:
    const unsigned char* current_;
    const unsigned char* end_;
};

constexpr std::array<std::uint32_t, 64> Sha256Constants = {
  0x428A2F98U, 0x71374491U, 0xB5C0FBCFU, 0xE9B5DBA5U, 0x3956C25BU, 0x59F111F1U, 0x923F82A4U,
  0xAB1C5ED5U, 0xD807AA98U, 0x12835B01U, 0x243185BEU, 0x550C7DC3U, 0x72BE5D74U, 0x80DEB1FEU,
  0x9BDC06A7U, 0xC19BF174U, 0xE49B69C1U, 0xEFBE4786U, 0x0FC19DC6U, 0x240CA1CCU, 0x2DE92C6FU,
  0x4A7484AAU, 0x5CB0A9DCU, 0x76F988DAU, 0x983E5152U, 0xA831C66DU, 0xB00327C8U, 0xBF597FC7U,
  0xC6E00BF3U, 0xD5A79147U, 0x06CA6351U, 0x14292967U, 0x27B70A85U, 0x2E1B2138U, 0x4D2C6DFCU,
  0x53380D13U, 0x650A7354U, 0x766A0ABBU, 0x81C2C92EU, 0x92722C85U, 0xA2BFE8A1U, 0xA81A664BU,
  0xC24B8B70U, 0xC76C51A3U, 0xD192E819U, 0xD6990624U, 0xF40E3585U, 0x106AA070U, 0x19A4C116U,
  0x1E376C08U, 0x2748774CU, 0x34B0BCB5U, 0x391C0CB3U, 0x4ED8AA4AU, 0x5B9CCA4FU, 0x682E6FF3U,
  0x748F82EEU, 0x78A5636FU, 0x84C87814U, 0x8CC70208U, 0x90BEFFFAU, 0xA4506CEBU, 0xBEF9A3F7U,
  0xC67178F2U};

constexpr std::uint32_t rotate_right(std::uint32_t value, int bits) noexcept {
    return (value >> bits) | (value << (32 - bits));
}

void sha256_block(std::array<std::uint32_t, 8>& state, const unsigned char* block) noexcept {
    std::array<std::uint32_t, 64> words{};
    for (std::size_t i = 0; i < 16; ++i)
        words[i] = (std::uint32_t(block[4 * i]) << 24) | (std::uint32_t(block[4 * i + 1]) << 16)
                 | (std::uint32_t(block[4 * i + 2]) << 8) | std::uint32_t(block[4 * i + 3]);

    for (std::size_t i = 16; i < words.size(); ++i)
    {
        const std::uint32_t s0 =
          rotate_right(words[i - 15], 7) ^ rotate_right(words[i - 15], 18) ^ (words[i - 15] >> 3);
        const std::uint32_t s1 =
          rotate_right(words[i - 2], 17) ^ rotate_right(words[i - 2], 19) ^ (words[i - 2] >> 10);
        words[i] = words[i - 16] + s0 + words[i - 7] + s1;
    }

    std::uint32_t a = state[0];
    std::uint32_t b = state[1];
    std::uint32_t c = state[2];
    std::uint32_t d = state[3];
    std::uint32_t e = state[4];
    std::uint32_t f = state[5];
    std::uint32_t g = state[6];
    std::uint32_t h = state[7];

    for (std::size_t i = 0; i < words.size(); ++i)
    {
        const std::uint32_t sum1   = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
        const std::uint32_t choose = (e & f) ^ (~e & g);
        const std::uint32_t temp1  = h + sum1 + choose + Sha256Constants[i] + words[i];
        const std::uint32_t sum0   = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
        const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        const std::uint32_t temp2    = sum0 + majority;

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

std::array<unsigned char, 32> sha256(const unsigned char* data, std::size_t size) noexcept {
    std::array<std::uint32_t, 8> state = {0x6A09E667U, 0xBB67AE85U, 0x3C6EF372U, 0xA54FF53AU,
                                          0x510E527FU, 0x9B05688CU, 0x1F83D9ABU, 0x5BE0CD19U};

    std::size_t offset = 0;
    while (size - offset >= 64)
    {
        sha256_block(state, data + offset);
        offset += 64;
    }

    std::array<unsigned char, 128> tail{};
    const std::size_t              remainder = size - offset;
    std::memcpy(tail.data(), data + offset, remainder);
    tail[remainder]                = 0x80;
    const std::size_t   finalBytes = remainder < 56 ? 64 : 128;
    const std::uint64_t bitCount   = std::uint64_t(size) * 8;
    for (int i = 0; i < 8; ++i)
        tail[finalBytes - 1 - std::size_t(i)] = static_cast<unsigned char>(bitCount >> (8 * i));
    sha256_block(state, tail.data());
    if (finalBytes == 128)
        sha256_block(state, tail.data() + 64);

    std::array<unsigned char, 32> digest{};
    for (std::size_t i = 0; i < state.size(); ++i)
        for (std::size_t byte = 0; byte < 4; ++byte)
            digest[4 * i + byte] = static_cast<unsigned char>(state[i] >> (24 - 8 * byte));
    return digest;
}

std::string digest_text(const std::array<unsigned char, 32>& digest) {
    constexpr char Hex[] = "0123456789abcdef";
    std::string    text;
    text.reserve(digest.size() * 2);
    for (const unsigned char byte : digest)
    {
        text.push_back(Hex[byte >> 4]);
        text.push_back(Hex[byte & 0x0F]);
    }
    return text;
}

Network::LoadResult failure(Network::LoadStatus status, std::string message) {
    return {status, std::move(message)};
}

std::int16_t signed16_from_bits(std::uint16_t bits) noexcept {
    std::int16_t value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

std::int32_t signed32_from_bits(std::uint32_t bits) noexcept {
    std::int32_t value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

void copy_transformer_bits(std::uint16_t*      target,
                           const std::int16_t* source,
                           std::size_t         count) noexcept {
    std::memcpy(target, source, count * sizeof(*target));
}

void apply_transformer_bits(std::uint16_t*      target,
                            const std::int16_t* source,
                            std::size_t         count,
                            bool                add,
                            bool                useSimd) noexcept {
#if defined(USE_AVX2)
    if (useSimd)
    {
        constexpr std::size_t Lanes = sizeof(__m256i) / sizeof(std::uint16_t);
        std::size_t           lane  = 0;
        for (; lane + Lanes <= count; lane += Lanes)
        {
            const __m256i left =
              _mm256_loadu_si256(reinterpret_cast<const __m256i*>(target + lane));
            const __m256i right =
              _mm256_loadu_si256(reinterpret_cast<const __m256i*>(source + lane));
            const __m256i value =
              add ? _mm256_add_epi16(left, right) : _mm256_sub_epi16(left, right);
            _mm256_storeu_si256(reinterpret_cast<__m256i*>(target + lane), value);
        }
        for (; lane < count; ++lane)
        {
            const std::uint32_t value  = target[lane];
            const std::uint32_t weight = static_cast<std::uint16_t>(source[lane]);
            target[lane] = static_cast<std::uint16_t>(add ? value + weight : value - weight);
        }
        return;
    }
#elif defined(USE_SSE2)
    if (useSimd)
    {
        constexpr std::size_t Lanes = sizeof(__m128i) / sizeof(std::uint16_t);
        std::size_t           lane  = 0;
        for (; lane + Lanes <= count; lane += Lanes)
        {
            const __m128i left  = _mm_loadu_si128(reinterpret_cast<const __m128i*>(target + lane));
            const __m128i right = _mm_loadu_si128(reinterpret_cast<const __m128i*>(source + lane));
            const __m128i value = add ? _mm_add_epi16(left, right) : _mm_sub_epi16(left, right);
            _mm_storeu_si128(reinterpret_cast<__m128i*>(target + lane), value);
        }
        for (; lane < count; ++lane)
        {
            const std::uint32_t value  = target[lane];
            const std::uint32_t weight = static_cast<std::uint16_t>(source[lane]);
            target[lane] = static_cast<std::uint16_t>(add ? value + weight : value - weight);
        }
        return;
    }
#else
    (void) useSimd;
#endif

    for (std::size_t lane = 0; lane < count; ++lane)
    {
        const std::uint32_t value  = target[lane];
        const std::uint32_t weight = static_cast<std::uint16_t>(source[lane]);
        target[lane] = static_cast<std::uint16_t>(add ? value + weight : value - weight);
    }
}

void apply_psqt_bits(std::uint32_t*      target,
                     const std::int32_t* source,
                     std::size_t         count,
                     bool                add,
                     bool                useSimd) noexcept {
#if defined(USE_AVX2)
    if (useSimd)
    {
        constexpr std::size_t Lanes = sizeof(__m256i) / sizeof(std::uint32_t);
        std::size_t           lane  = 0;
        for (; lane + Lanes <= count; lane += Lanes)
        {
            const __m256i left =
              _mm256_loadu_si256(reinterpret_cast<const __m256i*>(target + lane));
            const __m256i right =
              _mm256_loadu_si256(reinterpret_cast<const __m256i*>(source + lane));
            const __m256i value =
              add ? _mm256_add_epi32(left, right) : _mm256_sub_epi32(left, right);
            _mm256_storeu_si256(reinterpret_cast<__m256i*>(target + lane), value);
        }
        for (; lane < count; ++lane)
        {
            const std::uint32_t weight = static_cast<std::uint32_t>(source[lane]);
            target[lane]               = add ? target[lane] + weight : target[lane] - weight;
        }
        return;
    }
#elif defined(USE_SSE2)
    if (useSimd)
    {
        constexpr std::size_t Lanes = sizeof(__m128i) / sizeof(std::uint32_t);
        std::size_t           lane  = 0;
        for (; lane + Lanes <= count; lane += Lanes)
        {
            const __m128i left  = _mm_loadu_si128(reinterpret_cast<const __m128i*>(target + lane));
            const __m128i right = _mm_loadu_si128(reinterpret_cast<const __m128i*>(source + lane));
            const __m128i value = add ? _mm_add_epi32(left, right) : _mm_sub_epi32(left, right);
            _mm_storeu_si128(reinterpret_cast<__m128i*>(target + lane), value);
        }
        for (; lane < count; ++lane)
        {
            const std::uint32_t weight = static_cast<std::uint32_t>(source[lane]);
            target[lane]               = add ? target[lane] + weight : target[lane] - weight;
        }
        return;
    }
#else
    (void) useSimd;
#endif

    for (std::size_t lane = 0; lane < count; ++lane)
    {
        const std::uint32_t weight = static_cast<std::uint32_t>(source[lane]);
        target[lane]               = add ? target[lane] + weight : target[lane] - weight;
    }
}

void transform_accumulator(const std::uint16_t* input,
                           std::uint8_t*        output,
                           std::size_t          count,
                           bool                 useSimd) noexcept {
#if defined(USE_SSE2)
    if (useSimd)
    {
        constexpr std::size_t Lanes = sizeof(__m128i) / sizeof(std::uint16_t);
        const __m128i         zero  = _mm_setzero_si128();
        const __m128i         upper = _mm_set1_epi16(127);
        std::size_t           lane  = 0;
        for (; lane + Lanes <= count; lane += Lanes)
        {
            __m128i values       = _mm_loadu_si128(reinterpret_cast<const __m128i*>(input + lane));
            values               = _mm_max_epi16(values, zero);
            values               = _mm_min_epi16(values, upper);
            const __m128i packed = _mm_packus_epi16(values, zero);
            _mm_storel_epi64(reinterpret_cast<__m128i*>(output + lane), packed);
        }
        for (; lane < count; ++lane)
        {
            const std::int16_t value = signed16_from_bits(input[lane]);
            output[lane] = value <= 0 ? 0 : value >= 127 ? 127 : static_cast<std::uint8_t>(value);
        }
        return;
    }
#else
    (void) useSimd;
#endif

    for (std::size_t lane = 0; lane < count; ++lane)
    {
        const std::int16_t value = signed16_from_bits(input[lane]);
        output[lane] = value <= 0 ? 0 : value >= 127 ? 127 : static_cast<std::uint8_t>(value);
    }
}

template<std::size_t OutputCount, std::size_t PaddedInputCount, std::size_t InputCount>
std::array<std::int32_t, OutputCount>
propagate_affine(const std::array<std::int32_t, OutputCount>&                   biases,
                 const std::array<std::int8_t, OutputCount * PaddedInputCount>& weights,
                 const std::array<std::uint8_t, InputCount>&                    input) noexcept {
    static_assert(InputCount <= PaddedInputCount);

    std::array<std::int32_t, OutputCount> output{};
    for (std::size_t out = 0; out < OutputCount; ++out)
    {
        std::uint32_t     sumBits = static_cast<std::uint32_t>(biases[out]);
        const std::size_t row     = out * PaddedInputCount;
        for (std::size_t in = 0; in < PaddedInputCount; ++in)
        {
            const std::uint8_t value   = in < InputCount ? input[in] : 0;
            const std::int32_t product = std::int32_t(value) * std::int32_t(weights[row + in]);
            sumBits += static_cast<std::uint32_t>(product);
        }
        output[out] = signed32_from_bits(sumBits);
    }
    return output;
}

template<std::size_t Count>
std::array<std::uint8_t, Count> activate(const std::array<std::int32_t, Count>& input) noexcept {
    std::array<std::uint8_t, Count> output{};
    for (std::size_t i = 0; i < Count; ++i)
    {
        if (input[i] <= 0)
            output[i] = 0;
        else if (input[i] >= 127 * 64)
            output[i] = 127;
        else
            output[i] = static_cast<std::uint8_t>(input[i] / 64);
    }
    return output;
}

std::uint8_t activate_one(std::int32_t input) noexcept {
    if (input <= 0)
        return 0;
    if (input >= 127 * 64)
        return 127;
    return static_cast<std::uint8_t>(input / 64);
}

template<std::size_t Count>
void prepare_affine_input(const std::uint8_t* canonical, std::uint8_t* optimized) noexcept {
    static_assert(Count % 32 == 0);

#if defined(USE_PAIR_ACTIVATIONS) || defined(USE_LASX)
    // AffineTransform pre-permutes its weights for the lane-interleaved output produced by
    // Stockfish's paired activations. The legacy activation is intentionally scalar-exact and
    // canonical, so apply the inverse side of that load-time contract to each dense input.
    for (std::size_t block = 0; block < Count; block += 32)
        for (std::size_t chunk = 0; chunk < 8; ++chunk)
        {
    #if defined(USE_AVX512)
            const std::size_t optimizedChunk = (chunk % 4) * 2 + chunk / 4;
    #else
            const std::size_t optimizedChunk = (chunk % 2) * 4 + chunk / 2;
    #endif
            std::memcpy(optimized + block + optimizedChunk * 4, canonical + block + chunk * 4, 4);
        }
#else
    std::memcpy(optimized, canonical, Count);
#endif
}

Network::LegacyBoardInventory legacy_inventory(const Position& position) {
    Network::LegacyBoardInventory inventory;
    inventory.boardPawns = std::size_t(position.count<PAWN>());
    constexpr std::array<PieceType, Network::LegacyNonPawnCount> PieceTypes = {KNIGHT, BISHOP, ROOK,
                                                                               QUEEN};
    for (std::size_t type = 0; type < PieceTypes.size(); ++type)
    {
        inventory.whiteNonPawns[type] =
          std::size_t(popcount(position.pieces(WHITE, PieceTypes[type])));
        inventory.blackNonPawns[type] =
          std::size_t(popcount(position.pieces(BLACK, PieceTypes[type])));
    }
    return inventory;
}

}  // namespace

struct LegacyCrazyhouseNetworkV1::Parameters {
    struct LayerStack {
        std::array<std::int32_t, 16>       affine0Biases{};
        std::array<std::int8_t, 1024 * 16> affine0Weights{};
        std::array<std::int32_t, 32>       affine1Biases{};
        std::array<std::int8_t, 32 * 32>   affine1Weights{};
        std::array<std::int32_t, 1>        affine2Biases{};
        std::array<std::int8_t, 32>        affine2Weights{};
    };

    struct OptimizedLayerStack {
        using Affine0 = Layers::AffineTransform<TransformerDimensions * 2, 16>;
        using Affine1 = Layers::AffineTransform<32, 32>;
        using Affine2 = Layers::AffineTransform<32, 1>;

        Affine0 affine0{};
        Affine1 affine1{};
        Affine2 affine2{};
    };

    std::array<std::int16_t, TransformerDimensions> transformerBiases{};
    std::vector<std::int16_t>                       transformerWeights;
    std::vector<std::int32_t>                       psqtWeights;
    std::array<LayerStack, LayerStacks>             layers{};
    std::array<OptimizedLayerStack, LayerStacks>    optimizedLayers{};

    Parameters() :
        transformerWeights(FeatureDimensions * TransformerDimensions),
        psqtWeights(FeatureDimensions * PsqtBuckets) {}
};

LegacyCrazyhouseNetworkV1::LegacyCrazyhouseNetworkV1(ExecutionBackend backend) :
    backend_(backend) {}
LegacyCrazyhouseNetworkV1::~LegacyCrazyhouseNetworkV1() = default;

std::string_view LegacyCrazyhouseNetworkV1::compiled_simd_backend() noexcept {
#if defined(USE_AVX512)
    return "avx512";
#elif defined(USE_AVX2)
    return "avx2";
#elif defined(USE_SSE2)
    return "sse2";
#else
    return "none";
#endif
}

LegacyCrazyhouseAccumulatorStackV1::LegacyCrazyhouseAccumulatorStackV1() :
    frames_(std::make_unique<Frame[]>(MaxSize)) {
    reset();
}

LegacyCrazyhouseAccumulatorStackV1::~LegacyCrazyhouseAccumulatorStackV1() = default;

void LegacyCrazyhouseAccumulatorStackV1::reset() noexcept {
    network_                      = nullptr;
    size_                         = 1;
    counters_                     = {};
    frames_[0].computed           = false;
    frames_[0].boardPieceCount    = 0;
    frames_[0].kingSquares        = {SQ_NONE, SQ_NONE};
    frames_[0].boardInventory.fill(0);
    frames_[0].pocketInventory.fill(0);
    frames_[0].active[WHITE].size = 0;
    frames_[0].active[BLACK].size = 0;
}

bool LegacyCrazyhouseAccumulatorStackV1::push() noexcept {
    if (size_ >= MaxSize)
        return false;
    Frame& frame             = frames_[size_++];
    frame.computed           = false;
    frame.boardPieceCount    = 0;
    frame.kingSquares        = {SQ_NONE, SQ_NONE};
    frame.boardInventory.fill(0);
    frame.pocketInventory.fill(0);
    frame.active[WHITE].size = 0;
    frame.active[BLACK].size = 0;
    return true;
}

bool LegacyCrazyhouseAccumulatorStackV1::pop() noexcept {
    if (size_ <= 1)
        return false;
    --size_;
    return true;
}

void LegacyCrazyhouseNetworkV1::reset() noexcept {
    parameters_.reset();
    description_.clear();
}

LegacyCrazyhouseNetworkV1::LoadResult
LegacyCrazyhouseNetworkV1::load_file(const std::filesystem::path& path) {
    reset();

    std::error_code error;
    const auto      fileStatus = std::filesystem::status(path, error);
    if (error)
    {
        if (error == std::errc::no_such_file_or_directory)
            return failure(LoadStatus::MissingFile,
                           "legacy Crazyhouse network file does not exist");
        return failure(LoadStatus::FileReadFailure,
                       "legacy Crazyhouse network path status could not be read");
    }
    if (!std::filesystem::exists(fileStatus))
        return failure(LoadStatus::MissingFile, "legacy Crazyhouse network file does not exist");
    if (!std::filesystem::is_regular_file(fileStatus))
        return failure(LoadStatus::FileReadFailure,
                       "legacy Crazyhouse network path is not a regular file");

    const std::uintmax_t fileSize = std::filesystem::file_size(path, error);
    if (error)
        return failure(LoadStatus::FileReadFailure,
                       "legacy Crazyhouse network size could not be read");
    if (fileSize < FileBytes)
        return failure(LoadStatus::TruncatedFile,
                       "legacy Crazyhouse network is shorter than the registered artifact");
    if (fileSize > FileBytes)
        return failure(LoadStatus::OversizedFile,
                       "legacy Crazyhouse network is longer than the registered artifact");

    std::ifstream stream(path, std::ios::binary);
    if (!stream)
        return failure(LoadStatus::FileReadFailure,
                       "legacy Crazyhouse network could not be opened for reading");

    std::vector<unsigned char> bytes(FileBytes);
    stream.read(reinterpret_cast<char*>(bytes.data()), std::streamsize(bytes.size()));
    if (stream.gcount() != std::streamsize(bytes.size()) || !stream)
        return failure(LoadStatus::FileReadFailure,
                       "legacy Crazyhouse network read was incomplete");
    if (stream.peek() != std::char_traits<char>::eof())
        return failure(
          LoadStatus::OversizedFile,
          "legacy Crazyhouse network grew beyond the registered artifact while reading");

    return load_bytes(bytes.data(), bytes.size());
}

LegacyCrazyhouseNetworkV1::LoadResult LegacyCrazyhouseNetworkV1::load_embedded() {
#if defined(CRAZYHOUSE_LEGACY_EMBED_FILE) && !defined(_MSC_VER)
    return load_bytes(gCrazyhouseLegacyV1Data, gCrazyhouseLegacyV1Size);
#else
    reset();
    return failure(LoadStatus::MissingFile,
                   "legacy Crazyhouse network is not embedded in this executable");
#endif
}

bool LegacyCrazyhouseNetworkV1::embedded_available() noexcept {
#if defined(CRAZYHOUSE_LEGACY_EMBED_FILE) && !defined(_MSC_VER)
    return gCrazyhouseLegacyV1Size == FileBytes;
#else
    return false;
#endif
}

LegacyCrazyhouseNetworkV1::LoadResult
LegacyCrazyhouseNetworkV1::load_bytes(const unsigned char* data, std::size_t size) {
    reset();

    if (size < FileBytes)
        return failure(LoadStatus::TruncatedFile,
                       "legacy Crazyhouse network is shorter than the registered artifact");
    if (size > FileBytes)
        return failure(LoadStatus::OversizedFile,
                       "legacy Crazyhouse network is longer than the registered artifact");
    if (!data)
        return failure(LoadStatus::FileReadFailure,
                       "legacy Crazyhouse network byte pointer is null");

    Reader        reader(data, size);
    std::uint32_t value = 0;
    if (!reader.read_u32(value) || value != FileVersion)
        return failure(LoadStatus::VersionMismatch,
                       "legacy Crazyhouse network file version is incompatible");
    if (!reader.read_u32(value) || value != NetworkHash)
        return failure(LoadStatus::NetworkHashMismatch,
                       "legacy Crazyhouse network architecture hash is incompatible");
    if (!reader.read_u32(value) || value != RegisteredDescription.size())
        return failure(LoadStatus::DescriptionLengthMismatch,
                       "legacy Crazyhouse network description length is incompatible");

    std::string candidateDescription;
    if (!reader.read_string(candidateDescription, RegisteredDescription.size())
        || candidateDescription != RegisteredDescription)
        return failure(LoadStatus::DescriptionMismatch,
                       "legacy Crazyhouse network description is incompatible");
    if (!reader.read_u32(value) || value != TransformerHash)
        return failure(LoadStatus::TransformerHashMismatch,
                       "legacy Crazyhouse network transformer hash is incompatible");

    auto candidate = std::make_unique<Parameters>();
    if (!reader.read_i16(candidate->transformerBiases.data(), candidate->transformerBiases.size())
        || !reader.read_i16(candidate->transformerWeights.data(),
                            candidate->transformerWeights.size())
        || !reader.read_i32(candidate->psqtWeights.data(), candidate->psqtWeights.size()))
        return failure(LoadStatus::TensorLayoutMismatch,
                       "legacy Crazyhouse transformer tensors do not fill the registered layout");

    for (std::size_t stack = 0; stack < candidate->layers.size(); ++stack)
    {
        Parameters::LayerStack& layer = candidate->layers[stack];
        if (!reader.read_u32(value) || value != ArchitectureHash)
            return failure(LoadStatus::ArchitectureHashMismatch,
                           "legacy Crazyhouse layer-stack architecture hash is incompatible");
        const unsigned char* canonicalLayerBytes = reader.current();
        if (!reader.read_i32(layer.affine0Biases.data(), layer.affine0Biases.size())
            || !reader.read_i8(layer.affine0Weights.data(), layer.affine0Weights.size())
            || !reader.read_i32(layer.affine1Biases.data(), layer.affine1Biases.size())
            || !reader.read_i8(layer.affine1Weights.data(), layer.affine1Weights.size())
            || !reader.read_i32(layer.affine2Biases.data(), layer.affine2Biases.size())
            || !reader.read_i8(layer.affine2Weights.data(), layer.affine2Weights.size()))
            return failure(LoadStatus::TensorLayoutMismatch,
                           "legacy Crazyhouse layer tensors do not fill the registered layout");

        std::string        canonicalLayer(reinterpret_cast<const char*>(canonicalLayerBytes),
                                          LayerParameterBytes);
        std::istringstream optimizedStream(std::move(canonicalLayer),
                                           std::ios::in | std::ios::binary);
        Parameters::OptimizedLayerStack& optimized = candidate->optimizedLayers[stack];
        if (!optimized.affine0.read_parameters(optimizedStream)
            || !optimized.affine1.read_parameters(optimizedStream)
            || !optimized.affine2.read_parameters(optimizedStream)
            || optimizedStream.peek() != std::char_traits<char>::eof())
            return failure(LoadStatus::TensorLayoutMismatch,
                           "legacy Crazyhouse optimized layer layout could not be hydrated");
    }

    if (reader.remaining() != 0)
        return failure(LoadStatus::TensorLayoutMismatch,
                       "legacy Crazyhouse network has trailing bytes after the registered layout");
    const std::string digest = digest_text(sha256(data, size));
    if (digest != RegisteredSha256)
        return failure(LoadStatus::DigestMismatch,
                       "legacy Crazyhouse network SHA-256 is not registered (got " + digest + ")");

    parameters_  = std::move(candidate);
    description_ = std::move(candidateDescription);
    return {LoadStatus::Success, "registered legacy Crazyhouse network loaded"};
}

bool LegacyCrazyhouseNetworkV1::loaded() const noexcept { return bool(parameters_); }

LegacyCrazyhouseNetworkV1::EvalResult
LegacyCrazyhouseNetworkV1::evaluate_full_refresh(const Position& position) const {
    if (!parameters_)
        return {EvalStatus::NetworkNotLoaded, std::nullopt, std::nullopt,
                "registered legacy Crazyhouse network is not loaded"};
    if (backend_ == ExecutionBackend::Simd && compiled_simd_backend() == "none")
        return {EvalStatus::ContractViolation, std::nullopt, std::nullopt,
                "registered legacy Crazyhouse SIMD backend is not compiled"};

    const LegacyCrazyhouseFeaturesV1::Result features =
      LegacyCrazyhouseFeaturesV1::extract(position);
    if (!features.ok())
        return {EvalStatus::FeatureRejected, features.status, std::nullopt,
                "legacy Crazyhouse feature extraction failed: "
                  + std::string(LegacyCrazyhouseFeaturesV1::status_name(features.status))
                  + (features.message.empty() ? "" : " :: " + features.message)};

    const auto contract_failure = [&features](std::string message) {
        return EvalResult{EvalStatus::ContractViolation, features.status, std::nullopt,
                          std::move(message)};
    };

    if (features.boardPieceCount < 2
        || features.boardPieceCount > LegacyCrazyhouseFeaturesV1::LegacyMaxPieces)
        return contract_failure("legacy Crazyhouse board-piece count is outside the V1 contract");

    const std::size_t expectedBucket =
      (features.boardPieceCount - 1) * LayerStacks / LegacyCrazyhouseFeaturesV1::LegacyMaxPieces;
    if (expectedBucket >= LayerStacks || features.layerBucket != expectedBucket)
        return contract_failure("legacy Crazyhouse feature bucket violates the V1 contract");

    for (Color perspective : {WHITE, BLACK})
    {
        const auto& active = features.active[perspective];
        if (active.size() > LegacyCrazyhouseFeaturesV1::MaxActiveDimensions)
            return contract_failure(
              "legacy Crazyhouse active-feature count exceeds the V1 contract");
        for (std::size_t i = 0; i < active.size(); ++i)
        {
            if (active[i] >= FeatureDimensions)
                return contract_failure("legacy Crazyhouse feature index is outside the V1 tensor");
            for (std::size_t prior = 0; prior < i; ++prior)
                if (active[prior] == active[i])
                    return contract_failure(
                      "legacy Crazyhouse feature vector contains a duplicate");
        }
    }

    std::array<std::array<std::uint16_t, TransformerDimensions>, COLOR_NB> transformerBits{};
    std::array<std::array<std::uint32_t, PsqtBuckets>, COLOR_NB>           psqtBits{};
    const bool useSimd = backend_ == ExecutionBackend::Simd;

    for (Color perspective : {WHITE, BLACK})
    {
        copy_transformer_bits(transformerBits[perspective].data(),
                              parameters_->transformerBiases.data(), TransformerDimensions);

        for (LegacyCrazyhouseFeaturesV1::Index index : features.active[perspective])
        {
            const std::size_t transformerOffset = std::size_t(index) * TransformerDimensions;
            apply_transformer_bits(transformerBits[perspective].data(),
                                   parameters_->transformerWeights.data() + transformerOffset,
                                   TransformerDimensions, true, useSimd);

            const std::size_t psqtOffset = std::size_t(index) * PsqtBuckets;
            apply_psqt_bits(psqtBits[perspective].data(),
                            parameters_->psqtWeights.data() + psqtOffset, PsqtBuckets, true,
                            useSimd);
        }
    }

    EvalResult result =
      propagate_accumulators(position, features.boardPieceCount, transformerBits, psqtBits);
    if (result.ok())
        result.message = backend_ == ExecutionBackend::Simd
                         ? "registered legacy Crazyhouse SIMD full refresh completed"
                         : "registered legacy Crazyhouse scalar full refresh completed";
    return result;
}

LegacyCrazyhouseNetworkV1::EvalResult LegacyCrazyhouseNetworkV1::propagate_accumulators(
  const Position&                                                               position,
  std::size_t                                                                   boardPieceCount,
  const std::array<std::array<std::uint16_t, TransformerDimensions>, COLOR_NB>& transformerBits,
  const std::array<std::array<std::uint32_t, PsqtBuckets>, COLOR_NB>&           psqtBits) const {
    if (!parameters_)
        return {EvalStatus::NetworkNotLoaded, std::nullopt, std::nullopt,
                "registered legacy Crazyhouse network is not loaded"};
    if (boardPieceCount < 2 || boardPieceCount > LegacyCrazyhouseFeaturesV1::LegacyMaxPieces)
        return {EvalStatus::ContractViolation, LegacyCrazyhouseFeaturesV1::Status::Success,
                std::nullopt,
                "legacy Crazyhouse accumulated board-piece count is outside the V1 contract"};

    const std::size_t expectedBucket =
      (boardPieceCount - 1) * LayerStacks / LegacyCrazyhouseFeaturesV1::LegacyMaxPieces;
    if (expectedBucket >= LayerStacks)
        return {EvalStatus::ContractViolation, LegacyCrazyhouseFeaturesV1::Status::Success,
                std::nullopt,
                "legacy Crazyhouse accumulated feature bucket violates the V1 contract"};

    const Color sideToMove = position.side_to_move();
    if (sideToMove != WHITE && sideToMove != BLACK)
        return {EvalStatus::ContractViolation, LegacyCrazyhouseFeaturesV1::Status::Success,
                std::nullopt, "legacy Crazyhouse side to move is outside the color contract"};
    const Color opposite = ~sideToMove;

    alignas(64) std::array<std::uint8_t, TransformerDimensions * 2> transformed{};
    const std::array<Color, 2> perspectives = {sideToMove, opposite};
    const bool                 useSimd      = backend_ == ExecutionBackend::Simd;
    for (std::size_t half = 0; half < perspectives.size(); ++half)
        transform_accumulator(transformerBits[perspectives[half]].data(),
                              transformed.data() + half * TransformerDimensions,
                              TransformerDimensions, useSimd);

    RawEvaluation evaluation;
    evaluation.selectedBucket = static_cast<std::uint8_t>(expectedBucket);
    for (std::size_t bucket = 0; bucket < LayerStacks; ++bucket)
    {
        const std::uint32_t differenceBits =
          psqtBits[sideToMove][bucket] - psqtBits[opposite][bucket];
        evaluation.buckets[bucket].psqt = signed32_from_bits(differenceBits) / 2;

        if (useSimd)
        {
            const Parameters::OptimizedLayerStack& layer = parameters_->optimizedLayers[bucket];
            alignas(64) std::array<std::uint8_t,
                                   Parameters::OptimizedLayerStack::Affine0::PaddedInputDimensions>
                                                                               affine0Input{};
            alignas(64) Parameters::OptimizedLayerStack::Affine0::OutputBuffer affine0{};
            alignas(64) std::array<std::uint8_t,
                                   Parameters::OptimizedLayerStack::Affine0::PaddedOutputDimensions>
              hidden0{};
            static_assert(Parameters::OptimizedLayerStack::Affine0::PaddedInputDimensions
                          == transformed.size());
            prepare_affine_input<Parameters::OptimizedLayerStack::Affine0::PaddedInputDimensions>(
              transformed.data(), affine0Input.data());
            layer.affine0.propagate(affine0Input.data(), affine0);
            for (std::size_t output = 0;
                 output < Parameters::OptimizedLayerStack::Affine0::OutputDimensions; ++output)
                hidden0[output] = activate_one(affine0[output]);

            alignas(64) std::array<std::uint8_t,
                                   Parameters::OptimizedLayerStack::Affine1::PaddedInputDimensions>
                                                                               affine1Input{};
            alignas(64) Parameters::OptimizedLayerStack::Affine1::OutputBuffer affine1{};
            alignas(64) std::array<std::uint8_t,
                                   Parameters::OptimizedLayerStack::Affine1::PaddedOutputDimensions>
              hidden1{};
            static_assert(Parameters::OptimizedLayerStack::Affine1::PaddedInputDimensions
                          == hidden0.size());
            prepare_affine_input<Parameters::OptimizedLayerStack::Affine1::PaddedInputDimensions>(
              hidden0.data(), affine1Input.data());
            layer.affine1.propagate(affine1Input.data(), affine1);
            for (std::size_t output = 0;
                 output < Parameters::OptimizedLayerStack::Affine1::OutputDimensions; ++output)
                hidden1[output] = activate_one(affine1[output]);

            alignas(64) std::array<std::uint8_t,
                                   Parameters::OptimizedLayerStack::Affine2::PaddedInputDimensions>
                                                                               affine2Input{};
            alignas(64) Parameters::OptimizedLayerStack::Affine2::OutputBuffer affine2{};
            static_assert(Parameters::OptimizedLayerStack::Affine2::PaddedInputDimensions
                          == hidden1.size());
            prepare_affine_input<Parameters::OptimizedLayerStack::Affine2::PaddedInputDimensions>(
              hidden1.data(), affine2Input.data());
            layer.affine2.propagate(affine2Input.data(), affine2);
            evaluation.buckets[bucket].positional = affine2[0];
        }
        else
        {
            const Parameters::LayerStack& layer = parameters_->layers[bucket];
            const auto                    affine0 =
              propagate_affine<16, 1024>(layer.affine0Biases, layer.affine0Weights, transformed);
            const auto hidden0 = activate(affine0);
            const auto affine1 =
              propagate_affine<32, 32>(layer.affine1Biases, layer.affine1Weights, hidden0);
            const auto hidden1 = activate(affine1);
            const auto affine2 =
              propagate_affine<1, 32>(layer.affine2Biases, layer.affine2Weights, hidden1);
            evaluation.buckets[bucket].positional = affine2[0];
        }
    }

    return {EvalStatus::Success, LegacyCrazyhouseFeaturesV1::Status::Success, std::move(evaluation),
            useSimd ? "registered legacy Crazyhouse accumulated SIMD propagation completed"
                    : "registered legacy Crazyhouse accumulated scalar propagation completed"};
}

LegacyCrazyhouseNetworkV1::EvalResult
LegacyCrazyhouseNetworkV1::evaluate_incremental(const Position&                     position,
                                                 LegacyCrazyhouseAccumulatorStackV1& stack) const {
    using Stack = LegacyCrazyhouseAccumulatorStackV1;
    constexpr std::array<PieceType, 6> BoardTypes = {PAWN, KNIGHT, BISHOP,
                                                      ROOK, QUEEN, KING};
    constexpr std::array<PieceType, 5> PocketTypes = {PAWN, KNIGHT, BISHOP, ROOK, QUEEN};

    if (!parameters_)
        return {EvalStatus::NetworkNotLoaded, std::nullopt, std::nullopt,
                "registered legacy Crazyhouse network is not loaded"};
    if (backend_ == ExecutionBackend::Simd && compiled_simd_backend() == "none")
        return {EvalStatus::ContractViolation, std::nullopt, std::nullopt,
                "registered legacy Crazyhouse SIMD backend is not compiled"};
    if (stack.size_ == 0 || stack.size_ > Stack::MaxSize || !stack.frames_)
        return {EvalStatus::ContractViolation, std::nullopt, std::nullopt,
                "legacy Crazyhouse incremental stack is structurally invalid"};
    if (stack.network_ && stack.network_ != this)
        return {EvalStatus::ContractViolation, std::nullopt, std::nullopt,
                "legacy Crazyhouse incremental stack is bound to another network"};

    const std::size_t currentIndex = stack.size_ - 1;
    Stack::Frame&     currentFrame = stack.frames_[currentIndex];
    const auto physical_inventory_matches = [&]() noexcept {
        std::size_t boardIndex = 0;
        for (const Color owner : {WHITE, BLACK})
            for (const PieceType type : BoardTypes)
                if (currentFrame.boardInventory[boardIndex++] != position.pieces(owner, type))
                    return false;

        std::size_t pocketIndex = 0;
        for (const Color owner : {WHITE, BLACK})
            for (const PieceType type : PocketTypes)
                if (currentFrame.pocketInventory[pocketIndex++]
                    != std::uint8_t(position.pocket_count(owner, type)))
                    return false;
        return true;
    };

    if (currentFrame.computed)
    {
        if (!physical_inventory_matches())
            return {EvalStatus::ContractViolation, std::nullopt, std::nullopt,
                    "legacy Crazyhouse physical features changed without an incremental stack push"};

        EvalResult result =
          propagate_accumulators(position, currentFrame.boardPieceCount,
                                 currentFrame.transformerBits, currentFrame.psqtBits);
        if (!result.ok())
            return result;
        ++stack.counters_.evaluations;
        ++stack.counters_.sameFrameReuses;
        result.message = "registered legacy Crazyhouse same-frame incremental reuse completed";
        return result;
    }

    const LegacyCrazyhouseFeaturesV1::Result features =
      LegacyCrazyhouseFeaturesV1::extract(position);
    if (!features.ok())
        return {EvalStatus::FeatureRejected, features.status, std::nullopt,
                "legacy Crazyhouse feature extraction failed: "
                  + std::string(LegacyCrazyhouseFeaturesV1::status_name(features.status))
                  + (features.message.empty() ? "" : " :: " + features.message)};

    const auto contract_failure = [&features](std::string message) {
        return EvalResult{EvalStatus::ContractViolation, features.status, std::nullopt,
                          std::move(message)};
    };

    if (features.boardPieceCount < 2
        || features.boardPieceCount > LegacyCrazyhouseFeaturesV1::LegacyMaxPieces)
        return contract_failure("legacy Crazyhouse board-piece count is outside the V1 contract");
    std::array<Stack::ActiveRows, COLOR_NB> currentRows{};
    for (const Color perspective : {WHITE, BLACK})
    {
        const auto& active = features.active[perspective];
        if (active.size() > LegacyCrazyhouseFeaturesV1::MaxActiveDimensions)
            return contract_failure(
              "legacy Crazyhouse active-feature count exceeds the V1 contract");
        auto& rows = currentRows[perspective];
        rows.size  = active.size();
        std::copy(active.begin(), active.end(), rows.values.begin());
        std::sort(rows.values.begin(), rows.values.begin() + rows.size);
        if (std::adjacent_find(rows.values.begin(), rows.values.begin() + rows.size)
            != rows.values.begin() + rows.size)
            return contract_failure(
              "legacy Crazyhouse canonical active-feature set contains a duplicate");
        if (rows.size && rows.values[rows.size - 1] >= LegacyCrazyhouseNetworkV1::FeatureDimensions)
            return contract_failure(
              "legacy Crazyhouse canonical active-feature set exceeds the V1 tensor");
    }

    const std::array<Square, COLOR_NB> currentKings = {position.square<KING>(WHITE),
                                                       position.square<KING>(BLACK)};

    bool        sourceFound = false;
    std::size_t sourceIndex = currentIndex;
    while (sourceIndex > 0)
    {
        --sourceIndex;
        if (stack.frames_[sourceIndex].computed)
        {
            sourceFound = true;
            break;
        }
    }

    Stack::Frame candidate{};
    const bool   useSimd = backend_ == ExecutionBackend::Simd;
    if (sourceFound)
        candidate = stack.frames_[sourceIndex];
    else
    {
        for (const Color perspective : {WHITE, BLACK})
        {
            copy_transformer_bits(candidate.transformerBits[perspective].data(),
                                  parameters_->transformerBiases.data(), TransformerDimensions);
            candidate.psqtBits[perspective].fill(0);
            candidate.active[perspective].size = 0;
        }
    }
    candidate.computed = false;

    std::uint64_t removedFeatures = 0;
    std::uint64_t addedFeatures   = 0;
    const auto    apply_feature   = [this, &candidate, useSimd](Color perspective,
                                                           LegacyCrazyhouseFeaturesV1::Index index,
                                                           bool                              add) {
        const std::size_t transformerOffset = std::size_t(index) * TransformerDimensions;
        apply_transformer_bits(candidate.transformerBits[perspective].data(),
                                    parameters_->transformerWeights.data() + transformerOffset,
                                    TransformerDimensions, add, useSimd);

        const std::size_t psqtOffset = std::size_t(index) * PsqtBuckets;
        apply_psqt_bits(candidate.psqtBits[perspective].data(),
                             parameters_->psqtWeights.data() + psqtOffset, PsqtBuckets, add, useSimd);
    };

    for (const Color perspective : {WHITE, BLACK})
    {
        const Stack::ActiveRows  sourceRows   = candidate.active[perspective];
        const Stack::ActiveRows& targetRows   = currentRows[perspective];
        std::size_t              sourceCursor = 0;
        std::size_t              targetCursor = 0;
        while (sourceCursor < sourceRows.size || targetCursor < targetRows.size)
        {
            if (targetCursor == targetRows.size
                || (sourceCursor < sourceRows.size
                    && sourceRows.values[sourceCursor] < targetRows.values[targetCursor]))
            {
                apply_feature(perspective, sourceRows.values[sourceCursor++], false);
                ++removedFeatures;
            }
            else if (sourceCursor == sourceRows.size
                     || targetRows.values[targetCursor] < sourceRows.values[sourceCursor])
            {
                apply_feature(perspective, targetRows.values[targetCursor++], true);
                ++addedFeatures;
            }
            else
            {
                ++sourceCursor;
                ++targetCursor;
            }
        }
        candidate.active[perspective] = targetRows;
    }

    std::uint64_t kingRefreshes = 0;
    if (sourceFound)
        for (const Color perspective : {WHITE, BLACK})
            kingRefreshes += candidate.kingSquares[perspective] != currentKings[perspective];
    candidate.kingSquares     = currentKings;
    candidate.boardPieceCount = features.boardPieceCount;
    std::size_t boardIndex = 0;
    for (const Color owner : {WHITE, BLACK})
        for (const PieceType type : BoardTypes)
            candidate.boardInventory[boardIndex++] = position.pieces(owner, type);
    std::size_t pocketIndex = 0;
    for (const Color owner : {WHITE, BLACK})
        for (const PieceType type : PocketTypes)
            candidate.pocketInventory[pocketIndex++] =
              std::uint8_t(position.pocket_count(owner, type));

    EvalResult result = propagate_accumulators(position, candidate.boardPieceCount,
                                               candidate.transformerBits, candidate.psqtBits);
    if (!result.ok())
        return result;

    candidate.computed = true;
    currentFrame       = std::move(candidate);
    if (!stack.network_)
        stack.network_ = this;
    ++stack.counters_.evaluations;
    if (sourceFound)
    {
        const std::uint64_t distance = currentIndex - sourceIndex;
        ++stack.counters_.deltaUpdates;
        stack.counters_.maxSourceDistance = std::max(stack.counters_.maxSourceDistance, distance);
    }
    else
        ++stack.counters_.fullRefreshes;
    stack.counters_.kingPerspectiveRefreshes += kingRefreshes;
    stack.counters_.removedFeatures += removedFeatures;
    stack.counters_.addedFeatures += addedFeatures;
    if (backend_ == ExecutionBackend::Simd)
        result.message =
          sourceFound ? "registered legacy Crazyhouse SIMD sparse incremental update completed"
                      : "registered legacy Crazyhouse SIMD incremental root full refresh completed";
    else
        result.message =
          sourceFound
            ? "registered legacy Crazyhouse scalar sparse incremental update completed"
            : "registered legacy Crazyhouse scalar incremental root full refresh completed";
    return result;
}

LegacyCrazyhouseNetworkV1::AdapterResult
LegacyCrazyhouseNetworkV1::adapt_legacy_components(RawComponents               raw,
                                                   const LegacyBoardInventory& inventory) {
    std::size_t boardNonKingCount = inventory.boardPawns;
    if (boardNonKingCount > LegacyCrazyhouseFeaturesV1::LegacyMaxPieces - 2)
        return {AdapterStatus::InvalidInventory, std::nullopt,
                "legacy Crazyhouse board inventory exceeds the physical V1 bound"};

    std::array<std::int64_t, COLOR_NB> material{};
    for (std::size_t type = 0; type < LegacyNonPawnCount; ++type)
    {
        const std::array<std::size_t, COLOR_NB> counts = {inventory.whiteNonPawns[type],
                                                          inventory.blackNonPawns[type]};
        for (Color color : {WHITE, BLACK})
        {
            if (counts[color] > LegacyCrazyhouseFeaturesV1::LegacyMaxPieces - 2
                || boardNonKingCount
                     > LegacyCrazyhouseFeaturesV1::LegacyMaxPieces - 2 - counts[color])
                return {AdapterStatus::InvalidInventory, std::nullopt,
                        "legacy Crazyhouse board inventory exceeds the physical V1 bound"};
            boardNonKingCount += counts[color];
            material[color] +=
              std::int64_t(counts[color]) * std::int64_t(LegacyNonPawnValues[type]);
        }
    }

    if (material[WHITE] > std::numeric_limits<std::int32_t>::max()
        || material[BLACK] > std::numeric_limits<std::int32_t>::max())
        return {AdapterStatus::ArithmeticOutOfRange, std::nullopt,
                "legacy Crazyhouse material total exceeds the donor integer contract"};

    const auto blend = [raw](std::int32_t entertainment, std::int32_t& output) -> bool {
        const std::int64_t numerator = std::int64_t(128 - entertainment) * raw.psqt
                                     + std::int64_t(128 + entertainment) * raw.positional;
        if (numerator < std::numeric_limits<std::int32_t>::min()
            || numerator > std::numeric_limits<std::int32_t>::max())
            return false;
        const std::int32_t sum = static_cast<std::int32_t>(numerator) / 128;
        output                 = sum / 16;
        return true;
    };

    LegacyAdapterOutput output;
    output.boardPawns                     = inventory.boardPawns;
    output.whiteNonPawnMaterial           = static_cast<std::int32_t>(material[WHITE]);
    output.blackNonPawnMaterial           = static_cast<std::int32_t>(material[BLACK]);
    const std::int64_t difference         = material[WHITE] - material[BLACK];
    const std::int64_t absoluteDifference = difference < 0 ? -difference : difference;
    output.entertainmentApplied           = absoluteDifference <= LegacyEntertainmentThreshold;

    if (!blend(0, output.unadjusted)
        || !blend(output.entertainmentApplied ? 7 : 0, output.adjusted))
        return {AdapterStatus::ArithmeticOutOfRange, std::nullopt,
                "legacy Crazyhouse blend exceeds the donor signed 32-bit intermediate"};

    const std::int64_t totalMaterial = material[WHITE] + material[BLACK];
    const std::int64_t scale =
      903 + 32 * std::int64_t(inventory.boardPawns) + (32 * totalMaterial) / 1024;
    if (scale < std::numeric_limits<std::int32_t>::min()
        || scale > std::numeric_limits<std::int32_t>::max())
        return {AdapterStatus::ArithmeticOutOfRange, std::nullopt,
                "legacy Crazyhouse outer scale exceeds the donor integer contract"};
    output.scale = static_cast<std::int32_t>(scale);

    const std::int64_t outerNumerator = std::int64_t(output.adjusted) * output.scale;
    if (outerNumerator < std::numeric_limits<std::int32_t>::min()
        || outerNumerator > std::numeric_limits<std::int32_t>::max())
        return {AdapterStatus::ArithmeticOutOfRange, std::nullopt,
                "legacy Crazyhouse outer product exceeds the donor signed 32-bit intermediate"};
    output.outerPreClamp = static_cast<std::int32_t>(outerNumerator) / 1024;
    output.outer   = std::clamp(output.outerPreClamp, LegacyOuterLowerBound, LegacyOuterUpperBound);
    output.clamped = output.outer != output.outerPreClamp;

    return {AdapterStatus::Success, std::move(output),
            "legacy Crazyhouse value and outer adapter completed"};
}

LegacyCrazyhouseNetworkV1::LegacyEvalResult
LegacyCrazyhouseNetworkV1::evaluate_legacy(const Position& position) const {
    EvalResult raw = evaluate_full_refresh(position);
    if (!raw.ok())
        return {raw.status, raw.featureStatus, std::nullopt, raw.message};
    if (position.is_chess960())
        return {EvalStatus::ContractViolation, raw.featureStatus, std::nullopt,
                "legacy Crazyhouse outer adapter does not admit Chess960 correction"};

    AdapterResult adapter =
      adapt_legacy_components(raw.output->selected(), legacy_inventory(position));
    if (!adapter.ok())
        return {EvalStatus::ContractViolation, raw.featureStatus, std::nullopt,
                "legacy Crazyhouse adapter failed: "
                  + std::string(adapter_status_name(adapter.status)) + " :: " + adapter.message};

    LegacyEvaluation evaluation{std::move(*raw.output), std::move(*adapter.output)};
    return {EvalStatus::Success, raw.featureStatus, std::move(evaluation),
            "registered legacy Crazyhouse evaluation completed"};
}

LegacyCrazyhouseNetworkV1::LegacyEvalResult LegacyCrazyhouseNetworkV1::evaluate_legacy_incremental(
  const Position& position, LegacyCrazyhouseAccumulatorStackV1& stack) const {
    EvalResult raw = evaluate_incremental(position, stack);
    if (!raw.ok())
        return {raw.status, raw.featureStatus, std::nullopt, raw.message};
    if (position.is_chess960())
        return {EvalStatus::ContractViolation, raw.featureStatus, std::nullopt,
                "legacy Crazyhouse outer adapter does not admit Chess960 correction"};

    AdapterResult adapter =
      adapt_legacy_components(raw.output->selected(), legacy_inventory(position));
    if (!adapter.ok())
        return {EvalStatus::ContractViolation, raw.featureStatus, std::nullopt,
                "legacy Crazyhouse incremental adapter failed: "
                  + std::string(adapter_status_name(adapter.status)) + " :: " + adapter.message};

    LegacyEvaluation evaluation{std::move(*raw.output), std::move(*adapter.output)};
    return {EvalStatus::Success, raw.featureStatus, std::move(evaluation),
            "registered legacy Crazyhouse incremental evaluation completed"};
}

std::string_view LegacyCrazyhouseNetworkV1::description() const noexcept { return description_; }

std::string_view LegacyCrazyhouseNetworkV1::artifact_sha256() const noexcept {
    return loaded() ? RegisteredSha256 : std::string_view{};
}

std::string_view LegacyCrazyhouseNetworkV1::status_name(LoadStatus status) noexcept {
    switch (status)
    {
    case LoadStatus::Success :
        return "Success";
    case LoadStatus::MissingFile :
        return "MissingFile";
    case LoadStatus::FileReadFailure :
        return "FileReadFailure";
    case LoadStatus::TruncatedFile :
        return "TruncatedFile";
    case LoadStatus::OversizedFile :
        return "OversizedFile";
    case LoadStatus::VersionMismatch :
        return "VersionMismatch";
    case LoadStatus::NetworkHashMismatch :
        return "NetworkHashMismatch";
    case LoadStatus::DescriptionLengthMismatch :
        return "DescriptionLengthMismatch";
    case LoadStatus::DescriptionMismatch :
        return "DescriptionMismatch";
    case LoadStatus::TransformerHashMismatch :
        return "TransformerHashMismatch";
    case LoadStatus::ArchitectureHashMismatch :
        return "ArchitectureHashMismatch";
    case LoadStatus::TensorLayoutMismatch :
        return "TensorLayoutMismatch";
    case LoadStatus::DigestMismatch :
        return "DigestMismatch";
    }
    return "Unknown";
}

std::string_view LegacyCrazyhouseNetworkV1::eval_status_name(EvalStatus status) noexcept {
    switch (status)
    {
    case EvalStatus::Success :
        return "Success";
    case EvalStatus::NetworkNotLoaded :
        return "NetworkNotLoaded";
    case EvalStatus::FeatureRejected :
        return "FeatureRejected";
    case EvalStatus::ContractViolation :
        return "ContractViolation";
    }
    return "Unknown";
}

std::string_view LegacyCrazyhouseNetworkV1::adapter_status_name(AdapterStatus status) noexcept {
    switch (status)
    {
    case AdapterStatus::Success :
        return "Success";
    case AdapterStatus::InvalidInventory :
        return "InvalidInventory";
    case AdapterStatus::ArithmeticOutOfRange :
        return "ArithmeticOutOfRange";
    }
    return "Unknown";
}

}  // namespace Stockfish::Eval::NNUE
