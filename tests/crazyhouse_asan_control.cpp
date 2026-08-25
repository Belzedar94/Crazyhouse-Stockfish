/*
  Positive and negative controls for the local AddressSanitizer runtime gate.
  This translation unit is never linked into the engine.
*/

#include <cstdlib>
#include <cstring>
#include <iostream>

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--heap-overflow") == 0)
    {
        int*          buffer  = new int[1];
        volatile int* escaped = buffer;
        escaped[4]             = 42;
        std::cout << "FAIL ASan positive control returned: " << escaped[4] << '\n';
        delete[] buffer;
        return EXIT_FAILURE;
    }

    if (argc != 1)
    {
        std::cerr << "FAIL crazyhouse_asan_control: unknown argument\n";
        return EXIT_FAILURE;
    }

    std::cout << "PASS crazyhouse_asan_control safe_path=PASS positive_control=SEPARATE\n";
    return EXIT_SUCCESS;
}
