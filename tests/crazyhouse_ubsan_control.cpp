/*
  Positive and negative controls for the local undefined-behavior sanitizer
  gate. This translation unit is never linked into the engine.
*/

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--signed-overflow") == 0)
    {
        volatile int left   = std::numeric_limits<int>::max();
        volatile int right  = 1;
        volatile int result = left + right;
        std::cout << "FAIL UBSan positive control returned: " << result << '\n';
        return EXIT_FAILURE;
    }

    if (argc != 1)
    {
        std::cerr << "FAIL crazyhouse_ubsan_control: unknown argument\n";
        return EXIT_FAILURE;
    }

    std::cout << "PASS crazyhouse_ubsan_control safe_path=PASS positive_control=SEPARATE\n";
    return EXIT_SUCCESS;
}
