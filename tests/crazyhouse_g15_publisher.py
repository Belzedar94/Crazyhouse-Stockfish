#!/usr/bin/env python3
"""Mutation qualification for the offline post-G15 Crazyhouse publisher."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Any, Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "release" / "crazyhouse_g15_publisher.py"
CONTRACT_PATH = ROOT / "tests" / "crazyhouse" / "p15-g15-publisher-v1.json"
ADDENDUM_PATH = ROOT / "tests" / "crazyhouse" / "p15-g15-publisher-v1.addendum.002.json"
CORRECTION_PATH = ROOT / "tests" / "crazyhouse" / "p15-g15-publisher-v1.addendum.003.json"
SPEC = importlib.util.spec_from_file_location("crazyhouse_g15_publisher", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load post-G15 publisher")
PUBLISHER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PUBLISHER
SPEC.loader.exec_module(PUBLISHER)

CANDIDATE = "1" * 40
TAG_TARGET = "2" * 40
CANDIDATE_TREE = "3" * 40
TAG_TARGET_TREE = "4" * 40
DECIDED = "2026-08-25T02:00:00Z"
PUBLISHED = "2026-08-25T02:05:00Z"
QUALIFICATION_COUNTS = {
    "positive_cases_per_profile": 3,
    "negative_cases_per_profile": 89,
    "unit_tests_per_profile": 11,
}


def payload(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def pin(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def set_nested(value: dict[str, Any], path: Sequence[Any], replacement: Any) -> None:
    current: Any = value
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = replacement


def delete_nested(value: dict[str, Any], path: Sequence[Any]) -> None:
    current: Any = value
    for part in path[:-1]:
        current = current[part]
    del current[path[-1]]


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.git_root = root / "git"
        self.inputs = root / "inputs"
        self.assets_root = self.inputs / "assets"
        self.outputs = root / "outputs"
        self.git_root.mkdir(parents=True)
        self.assets_root.mkdir(parents=True)
        self.outputs.mkdir(parents=True)
        self.contract, self.addendum, self.correction = PUBLISHER.load_contracts(CONTRACT_PATH, ADDENDUM_PATH, CORRECTION_PATH)
        self.asset_names = list(self.contract["release"]["assets"])
        self.asset_paths: list[Path] = []
        for index, name in enumerate(self.asset_names):
            path = self.assets_root / name
            path.write_bytes((name + "\n" + str(index) + "\n").encode("ascii"))
            self.asset_paths.append(path)
        self.notes = self.inputs / "RELEASE_NOTES.md"
        self.notes.write_text("# Crazyhouse-Stockfish 1.0.0\n\nVerified fixture notes.\n", encoding="utf-8", newline="\n")
        self.independent = self.inputs / "independent-draft-verification.json"
        self.independent.write_bytes(payload({"result": "PASS_LOCAL_DRAFT_INDEPENDENT"}))
        self.receipt = self.inputs / "draft-verification.json"
        self.decision_path = self.inputs / "owner-decision.json"
        self.receipt_value = self.make_receipt()
        self.write_receipt()
        self.decision = self.make_decision()
        self.write_decision()

    def make_receipt(self) -> dict[str, Any]:
        return {
            "schema": "crazyhouse-g15-draft-verification/v1",
            "project": "Crazyhouse-Stockfish",
            "status": "PASS_COMPLETE_LOCAL_DRAFT_INDEPENDENTLY_VERIFIED",
            "version": "1.0.0",
            "tag": "v1.0.0",
            "candidate_commit": CANDIDATE,
            "candidate_tree": CANDIDATE_TREE,
            "tag_target_commit": TAG_TARGET,
            "tag_target_tree": TAG_TARGET_TREE,
            "source_date_epoch": 1_756_080_000,
            "release_notes": {"bytes": self.notes.stat().st_size, "sha256": hashlib.sha256(self.notes.read_bytes()).hexdigest()},
            "assets": [{"name": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in self.asset_paths],
            "independent_verifier": {**pin(self.independent), "result": "PASS_LOCAL_DRAFT_INDEPENDENT"},
        }

    def make_decision(self) -> dict[str, Any]:
        return {
            "schema": "crazyhouse-g15-owner-decision/v1",
            "project": "Crazyhouse-Stockfish",
            "decision": "AUTHORIZE_STABLE_PUBLICATION",
            "decided_utc": DECIDED,
            "owner": "Fixture Owner",
            "repository": "Belzedar94/Crazyhouse-Stockfish",
            "version": "1.0.0",
            "tag": "v1.0.0",
            "candidate_commit": CANDIDATE,
            "candidate_tree": CANDIDATE_TREE,
            "tag_target_commit": TAG_TARGET,
            "tag_target_tree": TAG_TARGET_TREE,
            "origin_main_commit": TAG_TARGET,
            "draft_verification_receipt": pin(self.receipt),
            "release_notes": pin(self.notes),
            "assets": [{"name": path.name, **pin(path)} for path in self.asset_paths],
            "tagger": {"name": "Fixture Owner", "email": "fixture@example.com", "date": DECIDED},
            "tag_message": "Crazyhouse-Stockfish 1.0.0",
            "release_title": "Crazyhouse-Stockfish 1.0.0",
            "monitor_owner": "Fixture Monitor",
        }

    def write_receipt(self) -> None:
        self.receipt.write_bytes(payload(self.receipt_value))

    def write_decision(self, value: dict[str, Any] | None = None) -> None:
        if value is not None:
            self.decision = value
        self.decision_path.write_bytes(payload(self.decision))

    def refresh_receipt_pin(self) -> None:
        self.decision["draft_verification_receipt"] = pin(self.receipt)
        self.write_decision()

    def context(self) -> Any:
        return PUBLISHER.validate_decision(self.decision_path, self.contract, self.addendum)

    def verifier(self, fail: bool = False) -> Callable[..., int]:
        def verify(local: Path, downloaded: Path, version: str, commit: str, tree: str, epoch: int) -> int:
            if fail:
                raise RuntimeError("injected verifier failure")
            if (version, commit, tree, epoch) != ("1.0.0", CANDIDATE, CANDIDATE_TREE, 1_756_080_000):
                raise RuntimeError("identity drift")
            def inventory(root: Path) -> dict[str, tuple[int, str]]:
                return {item.name: (item.stat().st_size, hashlib.sha256(item.read_bytes()).hexdigest()) for item in root.iterdir() if item.is_file()}
            if inventory(local) != inventory(downloaded):
                raise RuntimeError("download drift")
            return len(inventory(downloaded))
        return verify


class FakeRunner:
    def __init__(self, fixture: Fixture):
        self.fixture = fixture
        self.head = TAG_TARGET
        self.head_tree = TAG_TARGET_TREE
        self.candidate_tree = CANDIDATE_TREE
        self.object_format = "sha1"
        self.origin_url = "https://github.com/Belzedar94/Crazyhouse-Stockfish.git"
        self.remote_main = TAG_TARGET
        self.ancestry = True
        self.dirty = False
        self.local_tag_oid: str | None = None
        self.remote_tag_oid: str | None = None
        self.remote_tag_peeled = TAG_TARGET
        self.releases: list[dict[str, Any]] = []
        self.mutations: list[str] = []
        self.commands: list[tuple[str, ...]] = []
        self.fail_mode: str | None = None

    def result(self, code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> Any:
        return PUBLISHER.CommandResult(code, stdout, stderr)

    def run(self, argv: Sequence[str], cwd: Path, stdin: bytes | None = None, timeout: int = 900) -> Any:
        command = tuple(argv)
        self.commands.append(command)
        if command == ("git", "status", "--porcelain=v1"):
            return self.result(stdout=b" M dirty\n" if self.dirty else b"")
        if command == ("git", "rev-parse", "--show-toplevel"):
            return self.result(stdout=(str(self.fixture.git_root.resolve()) + "\n").encode())
        if command == ("git", "rev-parse", "--show-object-format"):
            return self.result(stdout=(self.object_format + "\n").encode())
        if command == ("git", "rev-parse", "HEAD"):
            return self.result(stdout=(self.head + "\n").encode())
        if command == ("git", "rev-parse", "HEAD^{tree}"):
            return self.result(stdout=(self.head_tree + "\n").encode())
        if command == ("git", "rev-parse", CANDIDATE + "^{commit}"):
            return self.result(stdout=(CANDIDATE + "\n").encode())
        if command == ("git", "rev-parse", CANDIDATE + "^{tree}"):
            return self.result(stdout=(self.candidate_tree + "\n").encode())
        if command == ("git", "merge-base", "--is-ancestor", CANDIDATE, TAG_TARGET):
            return self.result(code=0 if self.ancestry else 1)
        if command == ("git", "remote", "get-url", "origin"):
            return self.result(stdout=(self.origin_url + "\n").encode())
        if command == ("git", "ls-remote", "origin", "refs/heads/main"):
            return self.result(stdout=(self.remote_main + "\trefs/heads/main\n").encode())
        if command == ("git", "show-ref", "--verify", "--quiet", "refs/tags/v1.0.0"):
            return self.result(code=0 if self.local_tag_oid else 1)
        if command[:4] == ("git", "ls-remote", "--tags", "origin"):
            if self.remote_tag_oid is None:
                return self.result()
            ref = "refs/tags/v1.0.0"
            output = f"{self.remote_tag_oid}\t{ref}\n{self.remote_tag_peeled}\t{ref}^{{}}\n".encode()
            return self.result(stdout=output)
        if command == ("gh", "api", "--paginate", "--slurp", "repos/Belzedar94/Crazyhouse-Stockfish/releases?per_page=100"):
            return self.result(stdout=json.dumps([self.releases], sort_keys=True).encode() + b"\n")
        if command == ("git", "mktag"):
            self.mutations.append("mktag")
            if self.fail_mode == "fail_mktag":
                return self.result(code=2, stderr=b"injected\n")
            if stdin is None:
                raise AssertionError("git mktag fixture requires stdin")
            oid = PUBLISHER.git_object_id("tag", stdin)
            if self.fail_mode == "wrong_mktag":
                oid = "f" * 40
            return self.result(stdout=(oid + "\n").encode())
        if command[:3] == ("git", "update-ref", "refs/tags/v1.0.0"):
            self.mutations.append("update-ref")
            if self.fail_mode == "fail_update_ref":
                return self.result(code=2, stderr=b"injected\n")
            self.local_tag_oid = command[3]
            return self.result()
        if command == ("git", "rev-parse", "refs/tags/v1.0.0"):
            return self.result(stdout=((self.local_tag_oid or "") + "\n").encode())
        if command == ("git", "rev-parse", "refs/tags/v1.0.0^{}"):
            return self.result(stdout=(TAG_TARGET + "\n").encode())
        if len(command) == 4 and command[:3] == ("git", "cat-file", "-t"):
            return self.result(stdout=b"tag\n")
        if command == ("git", "push", "origin", "refs/tags/v1.0.0:refs/tags/v1.0.0"):
            self.mutations.append("push")
            if self.fail_mode == "fail_push":
                return self.result(code=2, stderr=b"injected\n")
            self.remote_tag_oid = self.local_tag_oid
            if self.fail_mode == "wrong_remote_tag":
                self.remote_tag_oid = "e" * 40
            return self.result()
        if command[:4] == ("gh", "release", "create", "v1.0.0"):
            self.mutations.append("create-draft")
            if self.fail_mode == "fail_create_draft":
                return self.result(code=2, stderr=b"injected\n")
            assets = [{"id": 201 + index, "name": path.name, "size": path.stat().st_size, "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), "state": "uploaded"} for index, path in enumerate(self.fixture.asset_paths)]
            name = "Wrong title" if self.fail_mode == "draft_title_drift" else "Crazyhouse-Stockfish 1.0.0"
            if self.fail_mode == "draft_asset_drift":
                assets[0]["digest"] = "sha256:" + "0" * 64
            if self.fail_mode == "draft_asset_missing_id":
                assets[0].pop("id")
            if self.fail_mode == "draft_asset_zero_id":
                assets[0]["id"] = 0
            if self.fail_mode == "draft_asset_bool_id":
                assets[0]["id"] = True
            if self.fail_mode == "draft_asset_duplicate_id":
                assets[1]["id"] = assets[0]["id"]
            self.releases = [{"id": 101, "tag_name": "v1.0.0", "name": name, "draft": True, "prerelease": False, "published_at": None, "assets": assets}]
            return self.result(stdout=b"https://example.invalid/draft\n")
        asset_prefix = ("gh", "api", "-H", "Accept: application/octet-stream")
        if command[:4] == asset_prefix:
            endpoint = command[4]
            prefix = "repos/Belzedar94/Crazyhouse-Stockfish/releases/assets/"
            if not endpoint.startswith(prefix):
                raise AssertionError("unexpected release-asset endpoint: " + endpoint)
            asset_id = int(endpoint.removeprefix(prefix))
            index = asset_id - 201
            if not 0 <= index < len(self.fixture.asset_paths):
                raise AssertionError("unexpected release-asset ID: " + str(asset_id))
            if self.fail_mode == "fail_download" and index == 0:
                return self.result(code=2, stderr=b"injected\n")
            data = self.fixture.asset_paths[index].read_bytes()
            if self.fail_mode == "corrupt_download" and index == 0:
                data += b"drift"
            return self.result(stdout=data, stderr=b"warning\n" if self.fail_mode == "download_stderr" and index == 0 else b"")
        if command == ("gh", "release", "edit", "v1.0.0", "--repo", "Belzedar94/Crazyhouse-Stockfish", "--draft=false", "--latest", "--verify-tag"):
            self.mutations.append("publish")
            if self.fail_mode == "fail_publish":
                return self.result(code=2, stderr=b"injected\n")
            if self.fail_mode != "publish_state_drift":
                self.releases[0]["draft"] = False
                self.releases[0]["published_at"] = PUBLISHED
            if self.fail_mode == "publish_asset_id_drift":
                self.releases[0]["assets"][0]["id"] = 999
            return self.result()
        raise AssertionError("unexpected command: " + repr(command))


class PublisherTests(unittest.TestCase):
    def new_fixture(self, root: Path) -> tuple[Fixture, Any, FakeRunner]:
        fixture = Fixture(root)
        context = fixture.context()
        runner = FakeRunner(fixture)
        publisher = PUBLISHER.Publisher(fixture.contract, fixture.addendum, fixture.correction, context, fixture.git_root, runner=runner, download_verifier=fixture.verifier())
        return fixture, publisher, runner

    def test_read_only_plan_passes_without_outputs_or_mutations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-g15-plan-") as temporary:
            fixture, publisher, runner = self.new_fixture(Path(temporary))
            before = sorted(path.relative_to(fixture.root).as_posix() for path in fixture.root.rglob("*"))
            result = publisher.preflight()
            after = sorted(path.relative_to(fixture.root).as_posix() for path in fixture.root.rglob("*"))
            self.assertEqual(result["status"], "PASS_G15_PUBLISHER_READ_ONLY_PLAN")
            self.assertEqual(runner.mutations, [])
            self.assertEqual(before, after)

    def test_simulated_transaction_passes_in_exact_order(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-g15-green-") as temporary:
            fixture, publisher, runner = self.new_fixture(Path(temporary))
            journal = fixture.outputs / "journal.jsonl"
            result_path = fixture.outputs / "result.json"
            download = fixture.outputs / "download"
            result = publisher.execute(journal, result_path, download)
            self.assertEqual(result["status"], "PASS_STABLE_PUBLICATION_TRANSACTION_MONITOR_T0_PENDING")
            self.assertEqual(runner.mutations, ["mktag", "update-ref", "push", "create-draft", "publish"])
            create = next(command for command in runner.commands if command[:4] == ("gh", "release", "create", "v1.0.0"))
            self.assertIn("--draft", create)
            self.assertIn("--verify-tag", create)
            self.assertNotIn("--clobber", create)
            self.assertEqual([Path(item).name for item in create[4:create.index("--draft")]], fixture.asset_names)
            downloads = [command for command in runner.commands if command[:4] == ("gh", "api", "-H", "Accept: application/octet-stream")]
            self.assertEqual(downloads, [
                ("gh", "api", "-H", "Accept: application/octet-stream", f"repos/Belzedar94/Crazyhouse-Stockfish/releases/assets/{201 + index}")
                for index in range(len(fixture.asset_names))
            ])
            self.assertFalse(any(command[:3] == ("gh", "release", "download") for command in runner.commands))
            self.assertTrue(result_path.is_file())
            self.assertEqual(result["release"]["published_at"], PUBLISHED)

    def test_journal_chain_and_success_stages_are_exact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-g15-chain-") as temporary:
            fixture, publisher, _ = self.new_fixture(Path(temporary))
            journal = fixture.outputs / "journal.jsonl"
            publisher.execute(journal, fixture.outputs / "result.json", fixture.outputs / "download")
            entries = [json.loads(line, object_pairs_hook=PUBLISHER.strict_object) for line in journal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["stage"] for item in entries], fixture.contract["transaction"]["stages"])
            previous = "0" * 64
            for index, entry in enumerate(entries):
                self.assertEqual(entry["index"], index)
                self.assertEqual(entry["previous_sha256"], previous)
                base = dict(entry)
                observed = base.pop("entry_sha256")
                self.assertEqual(observed, hashlib.sha256(PUBLISHER.canonical(base)).hexdigest())
                previous = observed
                self.assertNotIn("GITHUB_TOKEN", json.dumps(entry))

    def test_owner_decision_mutation_matrix_is_rejected(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            ("missing-schema", lambda d: d.pop("schema")),
            ("extra-key", lambda d: d.__setitem__("extra", 1)),
            ("wrong-schema", lambda d: d.__setitem__("schema", "wrong")),
            ("wrong-project", lambda d: d.__setitem__("project", "Atomic-Stockfish")),
            ("wrong-decision", lambda d: d.__setitem__("decision", "NO")),
            ("wrong-repository", lambda d: d.__setitem__("repository", "x/y")),
            ("wrong-version", lambda d: d.__setitem__("version", "1.0.1")),
            ("wrong-tag", lambda d: d.__setitem__("tag", "v1.0.1")),
            ("wrong-title", lambda d: d.__setitem__("release_title", "Wrong")),
            ("wrong-message", lambda d: d.__setitem__("tag_message", "Wrong")),
            ("timestamp-no-z", lambda d: d.__setitem__("decided_utc", "2026-08-25T02:00:00")),
            ("timestamp-invalid", lambda d: d.__setitem__("decided_utc", "2026-02-30T02:00:00Z")),
            ("owner-empty", lambda d: d.__setitem__("owner", "")),
            ("owner-whitespace", lambda d: d.__setitem__("owner", " owner ")),
            ("owner-nonascii", lambda d: d.__setitem__("owner", "Owñer")),
            ("monitor-empty", lambda d: d.__setitem__("monitor_owner", "")),
            ("candidate-short", lambda d: d.__setitem__("candidate_commit", "1" * 39)),
            ("candidate-upper", lambda d: d.__setitem__("candidate_commit", "A" * 40)),
            ("candidate-zero", lambda d: d.__setitem__("candidate_commit", "0" * 40)),
            ("candidate-tree-short", lambda d: d.__setitem__("candidate_tree", "3" * 39)),
            ("target-short", lambda d: d.__setitem__("tag_target_commit", "2" * 39)),
            ("target-tree-short", lambda d: d.__setitem__("tag_target_tree", "4" * 39)),
            ("origin-target-mismatch", lambda d: d.__setitem__("origin_main_commit", "5" * 40)),
            ("tagger-missing", lambda d: d["tagger"].pop("email")),
            ("tagger-extra", lambda d: d["tagger"].__setitem__("extra", 1)),
            ("tagger-name-empty", lambda d: d["tagger"].__setitem__("name", "")),
            ("tagger-name-angle", lambda d: d["tagger"].__setitem__("name", "Bad <name>")),
            ("tagger-email", lambda d: d["tagger"].__setitem__("email", "bad")),
            ("tagger-date", lambda d: d["tagger"].__setitem__("date", "2026-08-25T02:00:01Z")),
            ("assets-reversed", lambda d: d["assets"].reverse()),
            ("asset-name", lambda d: d["assets"][0].__setitem__("name", "wrong.zip")),
            ("asset-relative", lambda d: d["assets"][0].__setitem__("path", "relative.zip")),
            ("asset-bytes", lambda d: d["assets"][0].__setitem__("bytes", -1)),
            ("asset-digest", lambda d: d["assets"][0].__setitem__("sha256", "A" * 64)),
            ("receipt-pin", lambda d: d["draft_verification_receipt"].__setitem__("bytes", 0)),
            ("notes-pin", lambda d: d["release_notes"].__setitem__("sha256", "0" * 64)),
            ("extra-asset", lambda d: d["assets"].append(copy.deepcopy(d["assets"][0]))),
        ]
        self.assertEqual(len(cases), 37)
        for name, mutate in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(prefix="crazyhouse-g15-decision-") as temporary:
                fixture = Fixture(Path(temporary))
                decision = copy.deepcopy(fixture.decision)
                mutate(decision)
                fixture.write_decision(decision)
                with self.assertRaises(PUBLISHER.PublisherError):
                    fixture.context()

    def test_duplicate_decision_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-g15-duplicate-") as temporary:
            fixture = Fixture(Path(temporary))
            text = fixture.decision_path.read_text(encoding="utf-8")
            duplicate = text.replace('  "project": "Crazyhouse-Stockfish",\n', '  "project": "Crazyhouse-Stockfish",\n  "project": "Crazyhouse-Stockfish",\n', 1)
            fixture.decision_path.write_text(duplicate, encoding="utf-8", newline="\n")
            with self.assertRaises(PUBLISHER.PublisherError):
                fixture.context()

    def test_correction_must_be_the_hash_pinned_canonical_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-g15-correction-") as temporary:
            copied = Path(temporary) / "correction.json"
            copied.write_bytes(CORRECTION_PATH.read_bytes())
            with self.assertRaises(PUBLISHER.PublisherError):
                PUBLISHER.load_contracts(CONTRACT_PATH, ADDENDUM_PATH, copied)

    def test_draft_download_has_no_tag_browser_or_shell_fallback(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"gh", "release", "download"', source)
        self.assertNotIn("browser_download_url", source)
        self.assertNotIn("shell=True", source.replace(" ", ""))
        self.assertEqual(QUALIFICATION_COUNTS, {
            "positive_cases_per_profile": 3,
            "negative_cases_per_profile": 37 + 18 + 12 + 18 + 4,
            "unit_tests_per_profile": 11,
        })
        if os.environ.get("CRAZYHOUSE_FORMAL_AUDIT_REQUIRED") == "1":
            self.assertEqual(os.environ.get("CRAZYHOUSE_FORMAL_AUDIT_ACTIVE"), "1")

    def test_draft_receipt_mutation_matrix_is_rejected(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            ("missing-key", lambda d: d.pop("schema")),
            ("extra-key", lambda d: d.__setitem__("extra", 1)),
            ("schema", lambda d: d.__setitem__("schema", "wrong")),
            ("project", lambda d: d.__setitem__("project", "Horde-Stockfish")),
            ("status", lambda d: d.__setitem__("status", "PASS")),
            ("version", lambda d: d.__setitem__("version", "1.0.1")),
            ("tag", lambda d: d.__setitem__("tag", "v1.0.1")),
            ("candidate", lambda d: d.__setitem__("candidate_commit", "6" * 40)),
            ("candidate-tree", lambda d: d.__setitem__("candidate_tree", "6" * 40)),
            ("target", lambda d: d.__setitem__("tag_target_commit", "6" * 40)),
            ("target-tree", lambda d: d.__setitem__("tag_target_tree", "6" * 40)),
            ("epoch-bool", lambda d: d.__setitem__("source_date_epoch", True)),
            ("epoch-negative", lambda d: d.__setitem__("source_date_epoch", -1)),
            ("notes", lambda d: d["release_notes"].__setitem__("sha256", "0" * 64)),
            ("assets-order", lambda d: d["assets"].reverse()),
            ("asset-extra", lambda d: d["assets"][0].__setitem__("extra", 1)),
            ("verifier-result", lambda d: d["independent_verifier"].__setitem__("result", "PASS")),
            ("verifier-pin", lambda d: d["independent_verifier"].__setitem__("sha256", "0" * 64)),
        ]
        self.assertEqual(len(cases), 18)
        for name, mutate in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(prefix="crazyhouse-g15-receipt-") as temporary:
                fixture = Fixture(Path(temporary))
                receipt = copy.deepcopy(fixture.receipt_value)
                mutate(receipt)
                fixture.receipt_value = receipt
                fixture.write_receipt()
                fixture.refresh_receipt_pin()
                with self.assertRaises(PUBLISHER.PublisherError):
                    fixture.context()

    def test_preflight_state_mutation_matrix_is_rejected(self) -> None:
        runner_cases: list[tuple[str, Callable[[FakeRunner], None]]] = [
            ("dirty", lambda r: setattr(r, "dirty", True)),
            ("object-format", lambda r: setattr(r, "object_format", "sha256")),
            ("head", lambda r: setattr(r, "head", "6" * 40)),
            ("head-tree", lambda r: setattr(r, "head_tree", "6" * 40)),
            ("candidate-tree", lambda r: setattr(r, "candidate_tree", "6" * 40)),
            ("ancestry", lambda r: setattr(r, "ancestry", False)),
            ("origin-url", lambda r: setattr(r, "origin_url", "git@github.com:Belzedar94/Crazyhouse-Stockfish.git")),
            ("origin-main", lambda r: setattr(r, "remote_main", "6" * 40)),
            ("local-tag", lambda r: setattr(r, "local_tag_oid", "7" * 40)),
            ("remote-tag", lambda r: setattr(r, "remote_tag_oid", "7" * 40)),
            ("existing-release", lambda r: r.releases.append({"tag_name": "v1.0.0"})),
        ]
        self.assertEqual(len(runner_cases), 11)
        for name, mutate in runner_cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(prefix="crazyhouse-g15-state-") as temporary:
                fixture = Fixture(Path(temporary))
                context = fixture.context()
                runner = FakeRunner(fixture)
                mutate(runner)
                publisher = PUBLISHER.Publisher(fixture.contract, fixture.addendum, fixture.correction, context, fixture.git_root, runner=runner, download_verifier=fixture.verifier())
                with self.assertRaises(PUBLISHER.PublisherError):
                    publisher.preflight()
        with tempfile.TemporaryDirectory(prefix="crazyhouse-g15-verifier-") as temporary:
            fixture = Fixture(Path(temporary))
            publisher = PUBLISHER.Publisher(fixture.contract, fixture.addendum, fixture.correction, fixture.context(), fixture.git_root, runner=FakeRunner(fixture), download_verifier=fixture.verifier(fail=True))
            with self.assertRaises(PUBLISHER.PublisherError):
                publisher.preflight()

    def test_transaction_failure_matrix_is_fail_closed_without_rollback(self) -> None:
        modes = ["fail_mktag", "wrong_mktag", "fail_update_ref", "fail_push", "wrong_remote_tag", "fail_create_draft", "draft_title_drift", "draft_asset_drift", "draft_asset_missing_id", "draft_asset_zero_id", "draft_asset_bool_id", "draft_asset_duplicate_id", "fail_download", "corrupt_download", "download_stderr", "fail_publish", "publish_state_drift", "publish_asset_id_drift"]
        self.assertEqual(len(modes), 18)
        for mode in modes:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="crazyhouse-g15-failure-") as temporary:
                fixture, publisher, runner = self.new_fixture(Path(temporary))
                runner.fail_mode = mode
                result_path = fixture.outputs / "result.json"
                with self.assertRaises(PUBLISHER.PublisherError):
                    publisher.execute(fixture.outputs / "journal.jsonl", result_path, fixture.outputs / "download")
                result = json.loads(result_path.read_text(encoding="utf-8"), object_pairs_hook=PUBLISHER.strict_object)
                self.assertEqual(result["status"], "PARTIAL_PUBLICATION_REQUIRES_ADDITIVE_RECOVERY")
                self.assertFalse(result["automatic_retry"])
                self.assertFalse(result["automatic_rollback"])
                self.assertFalse(any("delete" in token or "--force" in token or "--clobber" in token for command in runner.commands for token in command))

    def test_output_reuse_is_rejected_before_any_transport_call(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crazyhouse-g15-reuse-") as temporary:
            fixture, publisher, runner = self.new_fixture(Path(temporary))
            journal = fixture.outputs / "journal.jsonl"
            journal.write_text("occupied\n", encoding="utf-8")
            with self.assertRaises(PUBLISHER.PublisherError):
                publisher.execute(journal, fixture.outputs / "result.json", fixture.outputs / "download")
            self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
