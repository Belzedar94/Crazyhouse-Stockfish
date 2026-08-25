/*
  Capacity-only search tests. These checks freeze the move-count reduction
  fallback without implementing or asserting Crazyhouse move legality.
*/

#include <cstdlib>
#include <cstring>
#include <iostream>

#include "crazyhouse_search_capacity.h"

namespace {

using Stockfish::Search::MoveCountReductionTable;

[[noreturn]] void fail(const char* message) {
    std::cerr << "FAIL crazyhouse_search_capacity: " << message << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const char* message) {
    if (!condition)
        fail(message);
}

void verify_golden_values() {
    MoveCountReductionTable table;
    table.initialize();

    struct Golden {
        int index;
        int value;
    };

    constexpr Golden GOLDENS[] = {
      {1, 0},     {2, 15},    {13, 57},   {64, 93},   {128, 108},
      {255, 124}, {256, 124}, {303, 128}, {512, 139}, {1024, 155},
    };

    for (const Golden golden : GOLDENS)
        require(table.value(golden.index) == golden.value,
                "reduction lookup differs from a frozen golden value");
}

void verify_table_boundary_and_reinitialization() {
    MoveCountReductionTable table;
    table.initialize();

    for (int index = 1; index < 256; ++index)
        require(table.value(index) == MoveCountReductionTable::formula(index),
                "inline lookup differs from the frozen formula");

    const int first303  = table.value(303);
    const int first1024 = table.value(1024);
    table.initialize();
    require(table.value(303) == first303, "reinitialization changed the 303 fallback");
    require(table.value(1024) == first1024, "reinitialization changed the 1024 fallback");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--invalid-zero-control") == 0)
    {
        MoveCountReductionTable table;
        table.initialize();
        static_cast<void>(table.value(0));
        fail("zero-index control returned instead of aborting");
    }

    require(argc == 1, "unknown command-line argument");
    verify_golden_values();
    verify_table_boundary_and_reinitialization();

    std::cout << "PASS crazyhouse_search_capacity inline=1..255 fallback=256,303,512,1024 "
                 "invalid_zero_control=SEPARATE\n";
    return EXIT_SUCCESS;
}
