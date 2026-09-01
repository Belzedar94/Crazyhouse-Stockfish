/*
  Crazyhouse-Stockfish, a UCI chess playing engine derived from Stockfish
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

#ifndef CRAZYHOUSE_MOVE_BUFFER_H_INCLUDED
#define CRAZYHOUSE_MOVE_BUFFER_H_INCLUDED

#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <new>
#include <type_traits>
#include <utility>

namespace Stockfish {

// FixedMoveBuffer gives index-based MovePicker code an owning fixed-capacity
// path without turning the orthodox inline capacity into a Crazyhouse bound.
// Overflow is fail-closed even in release builds.
template<typename T, std::size_t Capacity>
class FixedMoveBuffer {

    static_assert(Capacity > 0, "Fixed move buffers need nonzero storage");
    static_assert(std::is_nothrow_default_constructible_v<T>,
                  "Fixed move entries must be nothrow default constructible");
    static_assert(std::is_nothrow_move_assignable_v<T>,
                  "Fixed move entries must be nothrow move assignable");

   public:
    using value_type      = T;
    using size_type       = std::size_t;
    using iterator        = T*;
    using const_iterator  = const T*;
    using reference       = T&;
    using const_reference = const T&;

    FixedMoveBuffer() noexcept = default;

    FixedMoveBuffer(const FixedMoveBuffer&)            = delete;
    FixedMoveBuffer& operator=(const FixedMoveBuffer&) = delete;
    FixedMoveBuffer(FixedMoveBuffer&&)                 = delete;
    FixedMoveBuffer& operator=(FixedMoveBuffer&&)      = delete;

    iterator       begin() noexcept { return storage.data(); }
    const_iterator begin() const noexcept { return storage.data(); }
    const_iterator cbegin() const noexcept { return storage.data(); }

    iterator       end() noexcept { return storage.data() + size_; }
    const_iterator end() const noexcept { return storage.data() + size_; }
    const_iterator cend() const noexcept { return storage.data() + size_; }

    T*       data() noexcept { return storage.data(); }
    const T* data() const noexcept { return storage.data(); }

    bool                       empty() const noexcept { return size_ == 0; }
    size_type                  size() const noexcept { return size_; }
    static constexpr size_type capacity() noexcept { return Capacity; }

    reference operator[](size_type index) noexcept {
        assert(index < size_);
        return storage[index];
    }

    const_reference operator[](size_type index) const noexcept {
        assert(index < size_);
        return storage[index];
    }

    reference back() noexcept {
        assert(!empty());
        return storage[size_ - 1];
    }

    const_reference back() const noexcept {
        assert(!empty());
        return storage[size_ - 1];
    }

    void clear() noexcept { size_ = 0; }

    void pop_back() noexcept {
        assert(!empty());
        --size_;
    }

    reference push_back(T value) noexcept {
        if (size_ == Capacity)
            fail("capacity exceeded");

        storage[size_] = std::move(value);
        return storage[size_++];
    }

   private:
    [[noreturn]] static void fail(const char* reason) noexcept {
        std::fputs("FATAL FixedMoveBuffer: ", stderr);
        std::fputs(reason, stderr);
        std::fputc('\n', stderr);
        std::abort();
    }

    std::array<T, Capacity> storage;
    size_type               size_ = 0;
};

// CrazyhouseMoveBuffer keeps the orthodox inline move capacity as a storage
// optimization while making it impossible to truncate a Crazyhouse move list
// at that capacity. Pointers into the buffer may be invalidated by growth;
// callers must retain stage boundaries as indices and reacquire pointers.
template<typename T, std::size_t InlineCapacity>
class CrazyhouseMoveBuffer {

    static_assert(InlineCapacity > 0, "Crazyhouse move buffers need nonzero inline storage");
    static_assert(std::is_nothrow_default_constructible_v<T>,
                  "Crazyhouse move entries must be nothrow default constructible");
    static_assert(std::is_nothrow_move_assignable_v<T>,
                  "Crazyhouse move entries must be nothrow move assignable");

   public:
    using value_type      = T;
    using size_type       = std::size_t;
    using iterator        = T*;
    using const_iterator  = const T*;
    using reference       = T&;
    using const_reference = const T&;

    CrazyhouseMoveBuffer() noexcept :
        storage(inlineStorage.data()),
        size_(0),
        capacity_(InlineCapacity) {}

    CrazyhouseMoveBuffer(const CrazyhouseMoveBuffer&)            = delete;
    CrazyhouseMoveBuffer& operator=(const CrazyhouseMoveBuffer&) = delete;
    CrazyhouseMoveBuffer(CrazyhouseMoveBuffer&&)                 = delete;
    CrazyhouseMoveBuffer& operator=(CrazyhouseMoveBuffer&&)      = delete;

    ~CrazyhouseMoveBuffer() { delete[] heapStorage; }

    iterator       begin() noexcept { return storage; }
    const_iterator begin() const noexcept { return storage; }
    const_iterator cbegin() const noexcept { return storage; }

    iterator       end() noexcept { return storage + size_; }
    const_iterator end() const noexcept { return storage + size_; }
    const_iterator cend() const noexcept { return storage + size_; }

    T*       data() noexcept { return storage; }
    const T* data() const noexcept { return storage; }

    bool      empty() const noexcept { return size_ == 0; }
    size_type size() const noexcept { return size_; }
    size_type capacity() const noexcept { return capacity_; }

    static constexpr size_type inline_capacity() noexcept { return InlineCapacity; }
    static constexpr size_type max_size() noexcept {
        return static_cast<size_type>(std::numeric_limits<std::ptrdiff_t>::max()) / sizeof(T);
    }

    reference operator[](size_type index) noexcept {
        assert(index < size_);
        return storage[index];
    }

    const_reference operator[](size_type index) const noexcept {
        assert(index < size_);
        return storage[index];
    }

    reference front() noexcept {
        assert(!empty());
        return storage[0];
    }

    const_reference front() const noexcept {
        assert(!empty());
        return storage[0];
    }

    reference back() noexcept {
        assert(!empty());
        return storage[size_ - 1];
    }

    const_reference back() const noexcept {
        assert(!empty());
        return storage[size_ - 1];
    }

    void clear() noexcept { size_ = 0; }

    void pop_back() noexcept {
        assert(!empty());
        --size_;
    }

    void reserve(size_type requested) noexcept {
        if (requested > capacity_)
            grow(requested);
    }

    reference push_back(T value) noexcept {
        if (size_ == capacity_)
        {
            if (size_ == max_size())
                fail("size overflow");
            grow(size_ + 1);
        }

        storage[size_] = std::move(value);
        return storage[size_++];
    }

   private:
    [[noreturn]] static void fail(const char* reason) noexcept {
        std::fputs("FATAL CrazyhouseMoveBuffer: ", stderr);
        std::fputs(reason, stderr);
        std::fputc('\n', stderr);
        std::abort();
    }

    void grow(size_type required) noexcept {
        if (required > max_size())
            fail("capacity overflow");

        size_type nextCapacity = capacity_ + capacity_ / 2;
        if (nextCapacity < capacity_ || nextCapacity > max_size())
            nextCapacity = max_size();
        nextCapacity = std::max(nextCapacity, required);

        T* nextStorage = new (std::nothrow) T[nextCapacity];
        if (!nextStorage)
            fail("allocation failure");

        for (size_type index = 0; index < size_; ++index)
            nextStorage[index] = std::move(storage[index]);

        delete[] heapStorage;
        heapStorage = nextStorage;
        storage     = heapStorage;
        capacity_   = nextCapacity;
    }

    std::array<T, InlineCapacity> inlineStorage;
    T*                            heapStorage = nullptr;
    T*                            storage;
    size_type                     size_;
    size_type                     capacity_;
};

// RulesetMoveBuffer owns exactly one fixed or growable alternative. Its union
// keeps per-node stack storage to the size of the larger alternative instead
// of embedding both. Boundaries retained by clients must always be indices.
template<typename T, std::size_t InlineCapacity>
class RulesetMoveBuffer {

    using Fixed    = FixedMoveBuffer<T, InlineCapacity>;
    using Growable = CrazyhouseMoveBuffer<T, InlineCapacity>;

    union Storage {
        Storage() noexcept {}
        ~Storage() {}

        Fixed    fixed;
        Growable growable;
    } storage;

   public:
    using value_type      = T;
    using size_type       = std::size_t;
    using iterator        = T*;
    using const_iterator  = const T*;
    using reference       = T&;
    using const_reference = const T&;

    explicit RulesetMoveBuffer(bool selectGrowable) noexcept :
        growable_(selectGrowable) {
        if (growable_)
            ::new (static_cast<void*>(&storage.growable)) Growable();
        else
            ::new (static_cast<void*>(&storage.fixed)) Fixed();
    }

    RulesetMoveBuffer(const RulesetMoveBuffer&)            = delete;
    RulesetMoveBuffer& operator=(const RulesetMoveBuffer&) = delete;
    RulesetMoveBuffer(RulesetMoveBuffer&&)                 = delete;
    RulesetMoveBuffer& operator=(RulesetMoveBuffer&&)      = delete;

    ~RulesetMoveBuffer() {
        if (growable_)
            storage.growable.~Growable();
        else
            storage.fixed.~Fixed();
    }

    bool is_growable() const noexcept { return growable_; }

    iterator begin() noexcept {
        return growable_ ? storage.growable.begin() : storage.fixed.begin();
    }
    const_iterator begin() const noexcept {
        return growable_ ? storage.growable.begin() : storage.fixed.begin();
    }
    const_iterator cbegin() const noexcept { return begin(); }

    iterator end() noexcept { return growable_ ? storage.growable.end() : storage.fixed.end(); }
    const_iterator end() const noexcept {
        return growable_ ? storage.growable.end() : storage.fixed.end();
    }
    const_iterator cend() const noexcept { return end(); }

    T*       data() noexcept { return growable_ ? storage.growable.data() : storage.fixed.data(); }
    const T* data() const noexcept {
        return growable_ ? storage.growable.data() : storage.fixed.data();
    }

    bool      empty() const noexcept { return size() == 0; }
    size_type size() const noexcept {
        return growable_ ? storage.growable.size() : storage.fixed.size();
    }
    size_type capacity() const noexcept {
        return growable_ ? storage.growable.capacity() : storage.fixed.capacity();
    }

    reference operator[](size_type index) noexcept {
        return growable_ ? storage.growable[index] : storage.fixed[index];
    }
    const_reference operator[](size_type index) const noexcept {
        return growable_ ? storage.growable[index] : storage.fixed[index];
    }

    reference back() noexcept { return growable_ ? storage.growable.back() : storage.fixed.back(); }
    const_reference back() const noexcept {
        return growable_ ? storage.growable.back() : storage.fixed.back();
    }

    void clear() noexcept {
        if (growable_)
            storage.growable.clear();
        else
            storage.fixed.clear();
    }

    void pop_back() noexcept {
        if (growable_)
            storage.growable.pop_back();
        else
            storage.fixed.pop_back();
    }

    reference push_back(T value) noexcept {
        return growable_ ? storage.growable.push_back(std::move(value))
                         : storage.fixed.push_back(std::move(value));
    }

   private:
    bool growable_;
};

}  // namespace Stockfish

#endif  // CRAZYHOUSE_MOVE_BUFFER_H_INCLUDED
