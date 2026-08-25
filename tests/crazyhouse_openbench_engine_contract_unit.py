#!/usr/bin/env python3

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.json"
LIMIT_ADDENDUM_PATH = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.001.json"
)
CORPUS_ADDENDUM_PATH = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.002.json"
)
TARGET_ADDENDUM_PATH = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.003.json"
)
HARNESS_ADDENDUM_PATH = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.004.json"
)
SIGNATURE_ADDENDUM_PATH = (
    ROOT / "tests" / "crazyhouse" / "p10-openbench-onboarding-v1.addendum.005.json"
)
BENCHMARK_CPP = ROOT / "src" / "benchmark.cpp"
LEGACY_HEADER = ROOT / "src" / "nnue" / "crazyhouse_legacy_network.h"
MAKEFILE = ROOT / "src" / "Makefile"


class CrazyhouseOpenBenchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.limit_addendum = json.loads(LIMIT_ADDENDUM_PATH.read_text(encoding="utf-8"))
        cls.corpus_addendum = json.loads(CORPUS_ADDENDUM_PATH.read_text(encoding="utf-8"))
        cls.target_addendum = json.loads(TARGET_ADDENDUM_PATH.read_text(encoding="utf-8"))
        cls.harness_addendum = json.loads(HARNESS_ADDENDUM_PATH.read_text(encoding="utf-8"))
        cls.signature_addendum = json.loads(SIGNATURE_ADDENDUM_PATH.read_text(encoding="utf-8"))
        cls.benchmark_cpp = BENCHMARK_CPP.read_text(encoding="utf-8")
        cls.legacy_header = LEGACY_HEADER.read_text(encoding="utf-8")
        cls.makefile = MAKEFILE.read_text(encoding="utf-8")

    def test_preregistration_is_still_pre_result(self) -> None:
        benchmark = self.contract["benchmark"]
        self.assertEqual(benchmark["position_count"], 12)
        self.assertEqual(len(benchmark["positions"]), 12)
        self.assertIsNone(benchmark["expected_nodes"])
        self.assertEqual(benchmark["limit"], 6)
        self.assertEqual(self.limit_addendum["correction"]["before"], 6)
        self.assertEqual(self.limit_addendum["correction"]["after"], 4)
        self.assertTrue(
            self.limit_addendum["correction"]["frozen_before_complete_depth4_signature"]
        )
        self.assertEqual(
            self.corpus_addendum["correction"]["single_changed_scientific_field"],
            "benchmark.positions[10]",
        )
        self.assertTrue(
            self.corpus_addendum["correction"][
                "frozen_before_complete_revised_corpus_signature"
            ]
        )
        target = self.target_addendum["decision"]["after"]
        self.assertEqual(target["arch"], "x86-64")
        self.assertEqual(target["legacy_evaluator"], "scalar")
        self.assertTrue(
            self.target_addendum["decision"]["frozen_before_clean_export_bench_signature"]
        )
        self.assertEqual(
            self.harness_addendum["correction"]["after"],
            "rehash every tracked archive path exactly while inventorying generated build outputs separately",
        )
        self.assertEqual(self.harness_addendum["rejected_lease"]["lease"], 285)
        self.assertFalse(self.harness_addendum["rejected_lease"]["gate_credit"])
        self.assertEqual(self.signature_addendum["benchmark"]["expected_nodes"], 113485)
        self.assertEqual(
            self.signature_addendum["status"],
            "PASS_ENGINE_BUILD_SIGNATURE_FROZEN_AFTER_TWO_CLEAN_EXPORTS",
        )
        self.assertFalse(self.contract["claims"]["openbench_onboarded"])
        self.assertFalse(self.contract["claims"]["official_canary"])

    def test_correction_chain_authenticates_immutable_parents(self) -> None:
        contract_bytes = CONTRACT_PATH.read_bytes()
        limit_bytes = LIMIT_ADDENDUM_PATH.read_bytes()
        corpus_bytes = CORPUS_ADDENDUM_PATH.read_bytes()
        target_bytes = TARGET_ADDENDUM_PATH.read_bytes()
        harness_bytes = HARNESS_ADDENDUM_PATH.read_bytes()
        self.assertEqual(
            self.limit_addendum["parent"]["sha256"], hashlib.sha256(contract_bytes).hexdigest()
        )
        self.assertEqual(self.limit_addendum["parent"]["bytes"], len(contract_bytes))
        self.assertEqual(
            self.corpus_addendum["parent"]["sha256"], hashlib.sha256(limit_bytes).hexdigest()
        )
        self.assertEqual(self.corpus_addendum["parent"]["bytes"], len(limit_bytes))
        self.assertEqual(
            self.target_addendum["parent"]["sha256"], hashlib.sha256(corpus_bytes).hexdigest()
        )
        self.assertEqual(self.target_addendum["parent"]["bytes"], len(corpus_bytes))
        self.assertEqual(
            self.harness_addendum["parent"]["sha256"], hashlib.sha256(target_bytes).hexdigest()
        )
        self.assertEqual(self.harness_addendum["parent"]["bytes"], len(target_bytes))
        self.assertEqual(
            self.signature_addendum["parent"]["sha256"],
            hashlib.sha256(harness_bytes).hexdigest(),
        )
        self.assertEqual(self.signature_addendum["parent"]["bytes"], len(harness_bytes))

    def test_crazyhouse_benchmark_positions_match_frozen_order(self) -> None:
        match = re.search(
            r"const std::vector<std::string> CrazyhouseDefaults = \{(?P<body>.*?)\n\};",
            self.benchmark_cpp,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        literals = re.findall(r'^\s*("(?:[^"\\]|\\.)*"),?\s*$', match.group("body"), re.MULTILINE)
        observed = [json.loads(literal) for literal in literals]
        expected = [row["fen"] for row in self.contract["benchmark"]["positions"]]
        correction = self.corpus_addendum["correction"]
        self.assertEqual(expected[10], correction["before"]["fen"])
        expected[10] = correction["after"]["fen"]
        self.assertEqual(observed, expected)

    def test_crazyhouse_benchmark_defaults_are_frozen(self) -> None:
        benchmark = self.contract["benchmark"]
        self.assertEqual(benchmark["threads"], 1)
        self.assertEqual(benchmark["hash_mib"], 16)
        self.assertEqual(self.limit_addendum["correction"]["after"], 4)
        self.assertIn('std::string ttSize    = (is >> token) ? token : "16";', self.benchmark_cpp)
        self.assertIn('std::string threads   = (is >> token) ? token : "1";', self.benchmark_cpp)
        self.assertIn('(crazyhouse ? "4" : "13")', self.benchmark_cpp)
        self.assertIn('fens = crazyhouse ? CrazyhouseDefaults : Defaults;', self.benchmark_cpp)

    def test_registered_network_identity_is_single_exact_value(self) -> None:
        network = self.contract["legacy_network"]
        self.assertIn(network["sha256"], self.legacy_header)
        self.assertIn('"embedded:crazyhouse-8ebf84784ad2.nnue"', self.legacy_header)
        self.assertIn(f")\" = {network['bytes']}", self.makefile)
        self.assertIn(f")\" = {network['sha256']}", self.makefile)
        self.assertEqual(self.makefile.count(network["sha256"]), 1)

    def test_openbench_default_and_roles_are_fail_closed(self) -> None:
        self.assertIn('.DEFAULT_GOAL := openbench', self.makefile)
        self.assertIn('openbench: openbench-identity-sanity', self.makefile)
        self.assertIn('OPENBENCH_ARCH = x86-64', self.makefile)
        self.assertRegex(
            self.makefile,
            r"ifeq \(\$\(OS\),Windows_NT\)\s+OPENBENCH_COMP = mingw\s+else\s+"
            r"OPENBENCH_COMP = gcc\s+endif",
        )
        self.assertIn('ifeq ($(OPENBENCH_DATAGEN),1)', self.makefile)
        self.assertIn("CRAZYHOUSE_DATAGEN_EXE='$(EXE)' '$(EXE)'", self.makefile)
        self.assertIn(
            'ARCH=$(OPENBENCH_ARCH) COMP=$(OPENBENCH_COMP) \\\n\t\tCRAZYHOUSE_LEGACY_BACKEND=scalar OPENBENCH_PLAY_BUILD=1 all',
            self.makefile,
        )
        self.assertIn(
            "'-DCRAZYHOUSE_LEGACY_EMBED_FILE=\"$(EVALFILE)\"'", self.makefile
        )
        self.assertIn(
            'CRAZYHOUSE_LEGACY_EMBED_DEP := $(subst $(space),\\$(space),$(EVALFILE))',
            self.makefile,
        )
        self.assertIn(
            'crazyhouse_legacy_network.o: $(CRAZYHOUSE_LEGACY_EMBED_DEP)', self.makefile
        )

    def test_publication_and_referee_gaps_remain_explicit(self) -> None:
        boundaries = self.contract["owner_boundaries"]
        self.assertFalse(boundaries["public_repository_creation_or_publication_authorized"])
        self.assertFalse(boundaries["official_openbench_canary_authorized"])
        self.assertFalse(self.contract["referee"]["linux"]["qualified"])
        self.assertFalse(self.contract["referee"]["shared_horde_cutechess_allowed"])


if __name__ == "__main__":
    unittest.main()
