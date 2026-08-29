/*
  Crazyhouse-Stockfish, a UCI chess engine derived from Stockfish
  Copyright (C) 2026 The Crazyhouse-Stockfish developers

  Crazyhouse-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by the
  Free Software Foundation, either version 3 of the License, or (at your option)
  any later version.
*/

#include "crazyhouse_v2_large_runtime.h"

#include <algorithm>
#include <array>
#include <fstream>
#include <new>
#include <system_error>

#include "../position.h"

namespace Stockfish::Eval::NNUE::CrazyhouseV2 {
namespace {

constexpr bool lowercase_hex(char value) noexcept {
    return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
}

constexpr Byte hex_nibble(char value) noexcept {
    return value >= '0' && value <= '9' ? Byte(value - '0') : Byte(value - 'a' + 10);
}

bool parse_digest(std::string_view text, Digest& output) noexcept {
    if (text.size() != output.size() * 2 || !std::all_of(text.begin(), text.end(), lowercase_hex))
        return false;
    for (std::size_t index = 0; index < output.size(); ++index)
        output[index] = Byte((hex_nibble(text[index * 2]) << 4U) | hex_nibble(text[index * 2 + 1]));
    return true;
}

std::string digest_hex(const Digest& digest) {
    static constexpr std::array<char, 16> Hex = {'0', '1', '2', '3', '4', '5', '6', '7',
                                                 '8', '9', 'a', 'b', 'c', 'd', 'e', 'f'};
    std::string                           output(digest.size() * 2, '0');
    for (std::size_t index = 0; index < digest.size(); ++index)
    {
        output[index * 2]     = Hex[digest[index] >> 4U];
        output[index * 2 + 1] = Hex[digest[index] & 0x0FU];
    }
    return output;
}

bool parse_provenance(std::string_view text, LargeExpectedProvenanceV1& output) noexcept {
    constexpr std::size_t DigestChars = 64;
    constexpr std::size_t DigestCount = 6;
    if (text.size() != DigestCount * DigestChars + DigestCount - 1)
        return false;

    std::array<Digest*, DigestCount> targets = {&output.datasetManifest, &output.splitManifest,
                                                &output.trainingConfig,  &output.trainerCode,
                                                &output.trainingRuntime, &output.resumeLineage};
    for (std::size_t index = 0; index < targets.size(); ++index)
    {
        const std::size_t offset = index * (DigestChars + 1);
        if (!parse_digest(text.substr(offset, DigestChars), *targets[index]))
            return false;
        if (index + 1 < targets.size() && text[offset + DigestChars] != ':')
            return false;
    }
    return true;
}

bool same_domain(const LargeFeatureInventoryV1::DomainResult& left,
                 const LargeFeatureInventoryV1::DomainResult& right) noexcept {
    return left.size == right.size
        && std::equal(left.active.begin(), left.active.begin() + left.size, right.active.begin());
}

bool same_inventory(const LargeFeatureInventoryV1::Result& left,
                    const LargeFeatureInventoryV1::Result& right) noexcept {
    if (left.status != right.status || left.totalPocketUnits != right.totalPocketUnits)
        return false;
    for (Color perspective : {WHITE, BLACK})
        if (!same_domain(left.perspective[perspective].k64, right.perspective[perspective].k64)
            || !same_domain(left.perspective[perspective].g1, right.perspective[perspective].g1))
            return false;
    return true;
}

LargeNetworkEvaluationResultV1 evaluation_failure(LargeNetworkEvaluateError error) noexcept {
    LargeNetworkEvaluationResultV1 result;
    result.error = error;
    return result;
}

LargeRuntimeLoadResultV1
runtime_failure(LargeRuntimeLoadStatus status,
                std::string            message,
                LargeNetworkLoadError  containerError = LargeNetworkLoadError::NONE) {
    return {status, containerError, std::move(message)};
}

}  // namespace

LargeRuntimeLoadResultV1 LargeRuntimeV1::load_file(const std::filesystem::path& path,
                                                   std::string_view             expectedSha256,
                                                   std::string_view expectedProvenance) {
    reset();

    Digest expectedArtifact{};
    if (!parse_digest(expectedSha256, expectedArtifact))
        return runtime_failure(LargeRuntimeLoadStatus::INVALID_SHA256,
                               "large-V2 artifact SHA-256 must be lowercase 64-hex");

    LargeExpectedProvenanceV1 provenance;
    if (!parse_provenance(expectedProvenance, provenance))
        return runtime_failure(LargeRuntimeLoadStatus::INVALID_PROVENANCE,
                               "large-V2 provenance must contain six lowercase SHA-256 digests");

    if (large_network_simd_backend() == LargeNetworkSimdBackend::UNAVAILABLE)
        return runtime_failure(LargeRuntimeLoadStatus::SIMD_UNAVAILABLE,
                               "large-V2 requires the frozen SIMD parity backend");

    std::error_code error;
    const auto      status = std::filesystem::status(path, error);
    if (error)
    {
        if (error == std::errc::no_such_file_or_directory)
            return runtime_failure(LargeRuntimeLoadStatus::MISSING_FILE,
                                   "large-V2 network file does not exist");
        return runtime_failure(LargeRuntimeLoadStatus::FILE_READ_FAILURE,
                               "large-V2 network path status could not be read");
    }
    if (!std::filesystem::exists(status))
        return runtime_failure(LargeRuntimeLoadStatus::MISSING_FILE,
                               "large-V2 network file does not exist");
    if (!std::filesystem::is_regular_file(status))
        return runtime_failure(LargeRuntimeLoadStatus::NOT_REGULAR_FILE,
                               "large-V2 network path is not a regular file");

    const auto fileBytes = std::filesystem::file_size(path, error);
    if (error)
        return runtime_failure(LargeRuntimeLoadStatus::FILE_READ_FAILURE,
                               "large-V2 network size could not be read");
    if (fileBytes != LargeNetworkFileBytes)
        return runtime_failure(LargeRuntimeLoadStatus::WRONG_FILE_SIZE,
                               "large-V2 network has the wrong exact byte length");

    std::unique_ptr<Byte[]> bytes(new (std::nothrow) Byte[LargeNetworkFileBytes]);
    if (!bytes)
        return runtime_failure(LargeRuntimeLoadStatus::FILE_READ_FAILURE,
                               "large-V2 network read buffer allocation failed");

    std::ifstream input(path, std::ios::binary);
    if (!input)
        return runtime_failure(LargeRuntimeLoadStatus::FILE_READ_FAILURE,
                               "large-V2 network file could not be opened");
    input.read(reinterpret_cast<char*>(bytes.get()), std::streamsize(LargeNetworkFileBytes));
    if (input.gcount() != std::streamsize(LargeNetworkFileBytes) || !input)
        return runtime_failure(LargeRuntimeLoadStatus::FILE_READ_FAILURE,
                               "large-V2 network file could not be read exactly");

    const Digest artifactDigest = large_network_sha256(bytes.get(), LargeNetworkFileBytes);
    if (artifactDigest != expectedArtifact)
        return runtime_failure(LargeRuntimeLoadStatus::ARTIFACT_SHA256_MISMATCH,
                               "large-V2 full-file SHA-256 does not match the requested identity");

    LargeNetworkLoadResultV1 loaded =
      load_large_network_v1(bytes.get(), LargeNetworkFileBytes, provenance);
    if (!loaded.ok())
        return runtime_failure(LargeRuntimeLoadStatus::CONTAINER_REJECTED,
                               std::string("large-V2 container rejected: ")
                                 + std::string(large_network_load_error_name(loaded.error)),
                               loaded.error);

    network_        = std::move(loaded.network);
    artifactSha256_ = digest_hex(artifactDigest);
    return {LargeRuntimeLoadStatus::SUCCESS, LargeNetworkLoadError::NONE,
            "large-V2 network authenticated and loaded"};
}

void LargeRuntimeV1::reset() noexcept {
    network_.reset();
    artifactSha256_.clear();
}

LargeNetworkEvaluationResultV1
LargeRuntimeV1::evaluate_full_refresh(const Position& position) const noexcept {
    if (!loaded())
        return evaluation_failure(LargeNetworkEvaluateError::NETWORK_NOT_READY);
    const auto features = LargeFeatureInventoryV1::extract(position);
    if (!features.ok())
        return evaluation_failure(LargeNetworkEvaluateError::FEATURE_STATUS);
    return network_->evaluate(features, position.side_to_move());
}

LargeNetworkEvaluationResultV1
LargeRuntimeV1::evaluate_full_refresh_simd(const Position& position) const noexcept {
    if (!loaded())
        return evaluation_failure(LargeNetworkEvaluateError::NETWORK_NOT_READY);
    const auto features = LargeFeatureInventoryV1::extract(position);
    if (!features.ok())
        return evaluation_failure(LargeNetworkEvaluateError::FEATURE_STATUS);
    return network_->evaluate_simd(features, position.side_to_move());
}

LargeNetworkEvaluationResultV1
LargeRuntimeV1::evaluate_search_incremental(const Position&                 position,
                                            LargeRuntimeAccumulatorStackV1& stack) const noexcept {
    if (!loaded())
        return evaluation_failure(LargeNetworkEvaluateError::NETWORK_NOT_READY);
    if (!stack.frames_ || stack.size_ == 0 || stack.size_ > stack.MaxSize)
        return evaluation_failure(LargeNetworkEvaluateError::ACCUMULATOR_NOT_READY);
    if (stack.network_ && stack.network_ != network_.get())
        return evaluation_failure(LargeNetworkEvaluateError::ACCUMULATOR_NOT_READY);

    const auto features = LargeFeatureInventoryV1::extract(position);
    if (!features.ok())
        return evaluation_failure(LargeNetworkEvaluateError::FEATURE_STATUS);

    const std::size_t currentIndex = stack.size_ - 1;
    auto&             current      = stack.frames_[currentIndex];
    if (current.computed)
    {
        if (!same_inventory(current.inventory, features))
            return evaluation_failure(LargeNetworkEvaluateError::ACCUMULATOR_INVENTORY_MISMATCH);
        return current.accumulator.evaluate(features, position.side_to_move());
    }

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

    LargeNetworkAccumulatorV1       candidate;
    LargeNetworkAccumulatorResultV1 updated;
    if (sourceFound)
    {
        candidate = stack.frames_[sourceIndex].accumulator;
        updated   = candidate.update(*network_, stack.frames_[sourceIndex].inventory, features);
    }
    else
        updated = candidate.refresh(*network_, features);
    if (!updated.ok())
        return evaluation_failure(LargeNetworkEvaluateError::ACCUMULATOR_NOT_READY);

    LargeNetworkEvaluationResultV1 result = candidate.evaluate(features, position.side_to_move());
    if (!result.ok())
        return result;

    current.inventory   = features;
    current.accumulator = std::move(candidate);
    current.computed    = true;
    if (!stack.network_)
        stack.network_ = network_.get();
    return result;
}

std::string_view LargeRuntimeV1::simd_backend_name() noexcept {
    return large_network_simd_backend() == LargeNetworkSimdBackend::SSE2_X8_INT16_TO_INT32
           ? "sse2-x8-int16-to-int32"
           : "none";
}

LargeRuntimeAccumulatorStackV1::LargeRuntimeAccumulatorStackV1() = default;

LargeRuntimeAccumulatorStackV1::~LargeRuntimeAccumulatorStackV1() = default;

bool LargeRuntimeAccumulatorStackV1::ensure_allocated() noexcept {
    if (!frames_)
        frames_.reset(new (std::nothrow) Frame[MaxSize]);
    if (!frames_)
        return false;
    reset();
    return true;
}

void LargeRuntimeAccumulatorStackV1::reset() noexcept {
    network_ = nullptr;
    size_    = 1;
    if (frames_)
        frames_[0].computed = false;
}

bool LargeRuntimeAccumulatorStackV1::push() noexcept {
    if (!frames_ || size_ >= MaxSize)
        return false;
    frames_[size_++].computed = false;
    return true;
}

bool LargeRuntimeAccumulatorStackV1::pop() noexcept {
    if (!frames_ || size_ <= 1)
        return false;
    --size_;
    return true;
}

std::string_view large_runtime_load_status_name(LargeRuntimeLoadStatus status) noexcept {
#define LARGE_RUNTIME_STATUS_NAME(value) \
    case LargeRuntimeLoadStatus::value : \
        return #value
    switch (status)
    {
        LARGE_RUNTIME_STATUS_NAME(SUCCESS);
        LARGE_RUNTIME_STATUS_NAME(INVALID_SHA256);
        LARGE_RUNTIME_STATUS_NAME(INVALID_PROVENANCE);
        LARGE_RUNTIME_STATUS_NAME(MISSING_FILE);
        LARGE_RUNTIME_STATUS_NAME(NOT_REGULAR_FILE);
        LARGE_RUNTIME_STATUS_NAME(FILE_READ_FAILURE);
        LARGE_RUNTIME_STATUS_NAME(WRONG_FILE_SIZE);
        LARGE_RUNTIME_STATUS_NAME(ARTIFACT_SHA256_MISMATCH);
        LARGE_RUNTIME_STATUS_NAME(SIMD_UNAVAILABLE);
        LARGE_RUNTIME_STATUS_NAME(CONTAINER_REJECTED);
    }
#undef LARGE_RUNTIME_STATUS_NAME
    return "UNKNOWN";
}

}  // namespace Stockfish::Eval::NNUE::CrazyhouseV2
