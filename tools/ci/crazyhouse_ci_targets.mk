# CI-only test targets. This file is loaded after src/Makefile and does not
# change the product Makefile or any source identity frozen for strength gates.

CRAZYHOUSE_LEGACY_PARSER_CI_EXE = crazyhouse-legacy-parser-ci$(suffix $(EXE))
CRAZYHOUSE_LEGACY_PARSER_CI_OBJS = crazyhouse_legacy_parser_ci.o \
	$(filter-out main.o,$(OBJS))

CRAZYHOUSE_CORE_TEST_NAMES = move_abi move_buffer move_codec ruleset_boundary \
	state_layout fen zobrist transitions drop_generation repetition_terminal \
	search_capacity search_primitives
CRAZYHOUSE_CORE_TEST_EXES = $(foreach test,$(CRAZYHOUSE_CORE_TEST_NAMES),\
	crazyhouse-core-$(subst _,-,$(test))-tests$(suffix $(EXE)))

.PHONY: crazyhouse-legacy-parser-ci crazyhouse-core-tests

crazyhouse-legacy-parser-ci: $(CRAZYHOUSE_LEGACY_PARSER_CI_EXE)

$(CRAZYHOUSE_LEGACY_PARSER_CI_EXE): $(CRAZYHOUSE_LEGACY_PARSER_CI_OBJS)
	+$(CXX) -o $@ $(CRAZYHOUSE_LEGACY_PARSER_CI_OBJS) $(LDFLAGS)

crazyhouse_legacy_parser_ci.o: ../tests/crazyhouse_legacy_parser.cpp FORCE
	$(strip $(CXX) $(CPPFLAGS) $(CXXFLAGS)) -I. -c -o $@ $<

crazyhouse-core-tests: $(CRAZYHOUSE_CORE_TEST_EXES)

define CRAZYHOUSE_CORE_TEST_template
crazyhouse-core-$(subst _,-,$(1))-tests$(suffix $(EXE)): crazyhouse_core_$(1)_ci.o $(filter-out main.o,$(OBJS))
	+$$(CXX) -o $$@ $$^ $$(LDFLAGS)

crazyhouse_core_$(1)_ci.o: ../tests/crazyhouse_$(1).cpp FORCE
	$$(strip $$(CXX) $$(CPPFLAGS) $$(CXXFLAGS)) -I. -c -o $$@ $$<
endef

$(foreach test,$(CRAZYHOUSE_CORE_TEST_NAMES),$(eval $(call CRAZYHOUSE_CORE_TEST_template,$(test))))
