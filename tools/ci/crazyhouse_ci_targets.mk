# CI-only test targets. This file is loaded after src/Makefile and does not
# change the product Makefile or any source identity frozen for strength gates.

CRAZYHOUSE_LEGACY_PARSER_CI_EXE = crazyhouse-legacy-parser-ci$(suffix $(EXE))
CRAZYHOUSE_LEGACY_PARSER_CI_OBJS = crazyhouse_legacy_parser_ci.o \
	$(filter-out main.o,$(OBJS))

.PHONY: crazyhouse-legacy-parser-ci

crazyhouse-legacy-parser-ci: $(CRAZYHOUSE_LEGACY_PARSER_CI_EXE)

$(CRAZYHOUSE_LEGACY_PARSER_CI_EXE): $(CRAZYHOUSE_LEGACY_PARSER_CI_OBJS)
	+$(CXX) -o $@ $(CRAZYHOUSE_LEGACY_PARSER_CI_OBJS) $(LDFLAGS)

crazyhouse_legacy_parser_ci.o: ../tests/crazyhouse_legacy_parser.cpp FORCE
	$(strip $(CXX) $(CPPFLAGS) $(CXXFLAGS)) -I. -c -o $@ $<
