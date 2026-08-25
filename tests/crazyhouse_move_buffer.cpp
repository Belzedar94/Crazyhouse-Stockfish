/*
  Capacity-only tests for the Crazyhouse move buffer. These tests deliberately
  do not implement or assert Crazyhouse move legality.
*/

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <vector>

#include "crazyhouse_move_buffer.h"
#include "movegen.h"

namespace {

using Stockfish::CrazyhouseMoveBuffer;
using Stockfish::ExtMove;
using Stockfish::Move;
using Stockfish::RulesetMoveBuffer;
using Stockfish::u16;

constexpr std::size_t INLINE_CAPACITY = 256;
using Buffer                          = CrazyhouseMoveBuffer<ExtMove, INLINE_CAPACITY>;
using SelectedBuffer                  = RulesetMoveBuffer<ExtMove, INLINE_CAPACITY>;

[[noreturn]] void fail(const char* message) {
    std::cerr << "FAIL crazyhouse_move_buffer: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const char* message) {
    if (!condition)
        fail(message);
}

ExtMove sentinel(std::size_t index) {
    ExtMove entry;
    entry       = Move(static_cast<u16>(index + 2));
    entry.value = static_cast<int>((index * 7919U) % 65521U) - 32760;
    return entry;
}

std::uint64_t digest(const Buffer& buffer) {
    std::uint64_t value = 1469598103934665603ULL;
    for (const ExtMove& entry : buffer)
    {
        value ^= entry.raw();
        value *= 1099511628211ULL;
        value ^= static_cast<std::uint32_t>(entry.value);
        value *= 1099511628211ULL;
    }
    return value;
}

void fill(Buffer& buffer, std::size_t count) {
    for (std::size_t index = 0; index < count; ++index)
        buffer.push_back(sentinel(index));
}

void verify_count(std::size_t count) {
    Buffer   buffer;
    ExtMove* inlineAddress = buffer.data();

    fill(buffer, count);

    require(buffer.size() == count, "wrong size after append");
    require(static_cast<std::size_t>(buffer.end() - buffer.begin()) == count,
            "storage is not contiguous");
    require(buffer.capacity() >= count, "capacity is below size");
    require((count <= INLINE_CAPACITY) == (buffer.data() == inlineAddress),
            "inline/spill transition is wrong");

    for (std::size_t index = 0; index < count; ++index)
    {
        const ExtMove expected = sentinel(index);
        require(buffer[index].raw() == expected.raw(), "append changed a move sentinel");
        require(buffer[index].value == expected.value, "append changed a score sentinel");
    }

    const std::size_t partitionIndex = static_cast<std::size_t>(
      std::partition(buffer.begin(), buffer.end(),
                     [](const ExtMove& move) { return (move.raw() & 1U) == 0; })
      - buffer.begin());

    for (std::size_t index = 0; index < partitionIndex; ++index)
        require((buffer[index].raw() & 1U) == 0, "even partition boundary is corrupt");
    for (std::size_t index = partitionIndex; index < buffer.size(); ++index)
        require((buffer[index].raw() & 1U) != 0, "odd partition boundary is corrupt");

    std::sort(buffer.begin(), buffer.end(),
              [](const ExtMove& left, const ExtMove& right) { return left.raw() < right.raw(); });

    for (std::size_t index = 1; index < buffer.size(); ++index)
        require(buffer[index - 1].raw() < buffer[index].raw(),
                "sort found a duplicate or overwritten sentinel");
}

void verify_reserve_is_not_a_limit() {
    Buffer buffer;
    buffer.reserve(512);
    require(buffer.capacity() >= 512, "reserve did not grow storage");
    fill(buffer, 1024);
    require(buffer.size() == 1024, "reserve became a hard capacity ceiling");
}

void verify_recursive_ownership() {
    Buffer parent;
    fill(parent, 600);
    const auto parentDigest  = digest(parent);
    const auto parentAddress = parent.data();

    {
        Buffer child;
        fill(child, 1024);
        require(child.data() != parent.data(), "parent and child buffers alias");
        require(digest(child) != 0, "child digest is unexpectedly zero");
    }

    require(parent.data() == parentAddress, "child growth invalidated parent storage");
    require(digest(parent) == parentDigest, "child growth overwrote parent storage");
}

void verify_clear_and_refill() {
    Buffer buffer;
    fill(buffer, 600);
    const auto spilledAddress = buffer.data();
    buffer.clear();
    require(buffer.empty(), "clear did not reset size");
    fill(buffer, 303);
    require(buffer.size() == 303, "refill has wrong size");
    require(buffer.data() == spilledAddress, "clear unexpectedly invalidated storage");
}

void verify_pop_and_replace() {
    Buffer buffer;
    fill(buffer, 303);
    const auto spilledAddress = buffer.data();

    buffer.pop_back();
    require(buffer.size() == 302, "pop_back did not reduce size");
    require(buffer.back().raw() == sentinel(301).raw(), "pop_back exposed the wrong tail");

    buffer.push_back(sentinel(302));
    require(buffer.size() == 303, "replacement append has the wrong size");
    require(buffer.back().raw() == sentinel(302).raw(), "replacement append has the wrong tail");
    require(buffer.data() == spilledAddress, "pop and replacement invalidated spill storage");
}

template<typename T>
std::uint64_t selected_digest(const T& buffer) {
    std::uint64_t value = 1469598103934665603ULL;
    for (const ExtMove& entry : buffer)
    {
        value ^= entry.raw();
        value *= 1099511628211ULL;
        value ^= static_cast<std::uint32_t>(entry.value);
        value *= 1099511628211ULL;
    }
    return value;
}

void verify_fixed_selection() {
    SelectedBuffer buffer(false);
    require(!buffer.is_growable(), "fixed selection reports growable storage");

    for (std::size_t index = 0; index < INLINE_CAPACITY; ++index)
        buffer.push_back(sentinel(index));

    require(buffer.size() == INLINE_CAPACITY, "fixed selection has wrong size");
    require(buffer.capacity() == INLINE_CAPACITY, "fixed selection changed inline capacity");
    require(static_cast<std::size_t>(buffer.end() - buffer.begin()) == INLINE_CAPACITY,
            "fixed selection is not contiguous");
}

void verify_growable_stage_boundaries(std::size_t count) {
    constexpr std::size_t captureCount = 193;
    require(count > INLINE_CAPACITY && count > captureCount,
            "stage-boundary fixture must force growth after captures");

    SelectedBuffer buffer(true);
    require(buffer.is_growable(), "growable selection reports fixed storage");

    for (std::size_t index = 0; index < captureCount; ++index)
        buffer.push_back(sentinel(index));

    const std::size_t captureEnd          = buffer.size();
    const auto        captureDigestBefore = selected_digest(buffer);
    const ExtMove*    inlineAddress       = buffer.data();

    for (std::size_t index = captureCount; index < count; ++index)
        buffer.push_back(sentinel(index));

    require(buffer.size() == count, "growable selection has wrong total size");
    require(buffer.capacity() >= count, "growable selection capacity is below size");
    require(buffer.data() != inlineAddress, "stage-boundary fixture did not spill");
    require(captureEnd == captureCount, "capture boundary changed after growth");

    SelectedBuffer captureControl(true);
    for (std::size_t index = 0; index < captureEnd; ++index)
        captureControl.push_back(buffer[index]);
    require(selected_digest(captureControl) == captureDigestBefore,
            "quiet growth changed the capture prefix");

    const std::size_t badCaptureEnd = static_cast<std::size_t>(
      std::partition(buffer.begin(), buffer.begin() + captureEnd,
                     [](const ExtMove& move) { return (move.raw() & 1U) == 0; })
      - buffer.begin());
    require(badCaptureEnd > 0 && badCaptureEnd < captureEnd,
            "capture partition did not produce both classes");

    std::sort(buffer.begin() + captureEnd, buffer.end(),
              [](const ExtMove& left, const ExtMove& right) { return left.value > right.value; });
    for (std::size_t index = captureEnd + 1; index < buffer.size(); ++index)
        require(buffer[index - 1].value >= buffer[index].value,
                "quiet-stage sort crossed or corrupted its index boundary");

    std::vector<bool> seen(count, false);
    for (const ExtMove& entry : buffer)
    {
        const std::size_t index = static_cast<std::size_t>(entry.raw() - 2);
        require(index < count, "stage operations produced an unknown sentinel");
        require(!seen[index], "stage operations duplicated a sentinel");
        seen[index] = true;
    }
    require(std::all_of(seen.begin(), seen.end(), [](bool present) { return present; }),
            "stage operations lost a sentinel");
}

void verify_selected_recursive_ownership() {
    SelectedBuffer parent(true);
    for (std::size_t index = 0; index < 600; ++index)
        parent.push_back(sentinel(index));

    const auto     parentDigest  = selected_digest(parent);
    const ExtMove* parentAddress = parent.data();
    {
        SelectedBuffer child(true);
        for (std::size_t index = 0; index < 1024; ++index)
            child.push_back(sentinel(index));
        require(child.data() != parent.data(), "selected parent and child buffers alias");
    }

    require(parent.data() == parentAddress, "selected child growth invalidated parent storage");
    require(selected_digest(parent) == parentDigest,
            "selected child growth overwrote parent storage");
}

}  // namespace

int main(int argc, char** argv) {
    static_assert(sizeof(ExtMove) == 8, "official ExtMove layout changed");

    if (argc == 2 && std::strcmp(argv[1], "--overflow-control") == 0)
    {
        Buffer buffer;
        buffer.reserve(Buffer::max_size() + 1);
        fail("overflow control returned instead of aborting");
    }

    if (argc == 2 && std::strcmp(argv[1], "--fixed-overflow-control") == 0)
    {
        SelectedBuffer buffer(false);
        for (std::size_t index = 0; index <= INLINE_CAPACITY; ++index)
            buffer.push_back(sentinel(index));
        fail("fixed overflow control returned instead of aborting");
    }

    require(argc == 1, "unknown command-line argument");
    verify_count(303);
    verify_count(512);
    verify_count(1024);
    verify_reserve_is_not_a_limit();
    verify_recursive_ownership();
    verify_clear_and_refill();
    verify_pop_and_replace();
    verify_fixed_selection();
    verify_growable_stage_boundaries(303);
    verify_growable_stage_boundaries(512);
    verify_growable_stage_boundaries(1024);
    verify_selected_recursive_ownership();

    std::cout << "PASS crazyhouse_move_buffer counts=303,512,1024 recursive_ownership=PASS "
                 "selected_stage_boundaries=PASS fixed_overflow_control=SEPARATE "
                 "overflow_control=SEPARATE\n";
    return EXIT_SUCCESS;
}
