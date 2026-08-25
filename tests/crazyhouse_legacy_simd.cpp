/*
  Fixture executable for exact scalar/SIMD parity of the registered
  Crazyhouse legacy V1 evaluator. The frozen scalar fixture is included as a
  single translation-unit reference so its case parser and counter checks
  cannot drift independently.
*/

int crazyhouse_scalar_incremental_reference_main(int argc, char* argv[]);
#define main crazyhouse_scalar_incremental_reference_main
#include "crazyhouse_legacy_incremental.cpp"
#undef main

namespace {

bool same_totals(const Totals& left, const Totals& right) {
    return left.cases == right.cases && left.transitions == right.transitions
        && left.undos == right.undos && left.nulls == right.nulls
        && left.rejections == right.rejections && left.digest == right.digest;
}

}  // namespace

int main(int argc, char* argv[]) {
    require(argc == 3,
            "usage: crazyhouse_legacy_simd <registered-network> <expected-simd-backend>");
    const std::filesystem::path artifact(argv[1]);
    const std::string           expectedBackend(argv[2]);

    Attacks::init();
    Position::init();

    require(Network::compiled_simd_backend() == expectedBackend,
            "compiled SIMD backend mismatch: expected " + expectedBackend + " got "
              + std::string(Network::compiled_simd_backend()));
    require(expectedBackend != "none", "fixture cannot grant SIMD credit to a scalar fallback");

    Network scalar(Network::ExecutionBackend::Scalar);
    Network simd(Network::ExecutionBackend::Simd);
    require(scalar.execution_backend() == Network::ExecutionBackend::Scalar,
            "scalar network backend identity mismatch");
    require(simd.execution_backend() == Network::ExecutionBackend::Simd,
            "SIMD network backend identity mismatch");

    const auto scalarLoaded = scalar.load_file(artifact);
    const auto simdLoaded   = simd.load_file(artifact);
    require(scalarLoaded.status == Network::LoadStatus::Success && scalar.loaded(),
            "scalar registered network failed to load: " + scalarLoaded.message);
    require(simdLoaded.status == Network::LoadStatus::Success && simd.loaded(),
            "SIMD registered network failed to load: " + simdLoaded.message);
    require(scalar.artifact_sha256() == simd.artifact_sha256(),
            "scalar/SIMD artifact identities differ");

    Totals      scalarTotals;
    Totals      simdTotals;
    std::string line;
    while (std::getline(std::cin, line))
    {
        require(!line.empty(), "fixture stream contains an empty line");
        const auto fields = split(line, '\t');
        execute_case(scalar, fields, scalarTotals);
        execute_case(simd, fields, simdTotals);
        require(same_totals(scalarTotals, simdTotals),
                fields.front() + " scalar/SIMD cumulative protocol mismatch");
    }
    require(std::cin.eof(), "fixture stream read failed");
    require(scalarTotals.cases > 0, "fixture stream contains no cases");

    verify_boundaries(scalar, artifact, scalarTotals);
    verify_boundaries(simd, artifact, simdTotals);
    require(same_totals(scalarTotals, simdTotals),
            "scalar/SIMD boundary protocol mismatch");

    std::cout << "PASS crazyhouse_legacy_simd backend=" << expectedBackend
              << " cases=" << scalarTotals.cases
              << " transitions=" << scalarTotals.transitions
              << " undos=" << scalarTotals.undos << " nulls=" << scalarTotals.nulls
              << " rejections=" << scalarTotals.rejections
              << " trace_digest=" << std::hex << scalarTotals.digest << std::dec << '\n';
    return EXIT_SUCCESS;
}
