#!/usr/bin/env python3
"""Fail-closed one-shot publisher for an explicitly authorized Crazyhouse release."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Optional, Sequence


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = "crazyhouse-g15-publisher/v1"
ADDENDUM_SCHEMA = "crazyhouse-g15-publisher-preimplementation-addendum/v1"
CORRECTION_SCHEMA = "crazyhouse-g15-publisher-download-authority-addendum/v1"
CORRECTION_RELATIVE = Path("tests/crazyhouse/p15-g15-publisher-v1.addendum.003.json")
CORRECTION_BYTES = 4660
CORRECTION_SHA256 = "70ff2396a5a93f76253f941d39eb8ad12c59558a8b678b4e8c9d5e9fa5fed117"
DECISION_SCHEMA = "crazyhouse-g15-owner-decision/v1"
DRAFT_SCHEMA = "crazyhouse-g15-draft-verification/v1"
JOURNAL_SCHEMA = "crazyhouse-g15-publisher-journal-entry/v1"
RESULT_SCHEMA = "crazyhouse-g15-publisher-result/v1"
OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$")
ZERO_OID = "0" * 40
ZERO_DIGEST = "0" * 64


class PublisherError(RuntimeError):
    """The authorization, state or transaction violates the frozen contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PublisherError(message)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PublisherError("duplicate JSON key: " + key)
        value[key] = item
    return value


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_unlinked_file(path: Path, label: str) -> Path:
    require(not path.is_symlink(), label + " must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
        metadata = os.stat(resolved, follow_symlinks=False)
    except OSError as error:
        raise PublisherError(label + " is missing or unreadable") from error
    require(stat.S_ISREG(metadata.st_mode), label + " must be a regular file")
    require(metadata.st_nlink == 1, label + " must have exactly one hard link")
    return resolved


def strict_json_file(path: Path, label: str) -> tuple[Path, dict[str, Any]]:
    resolved = regular_unlinked_file(path, label)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublisherError(label + " is not strict UTF-8 JSON") from error
    require(isinstance(value, dict), label + " root must be one object")
    return resolved, value


def exact_keys(value: dict[str, Any], keys: Sequence[str], label: str) -> None:
    expected = set(keys)
    actual = set(value)
    require(actual == expected, f"{label} keys differ (missing={sorted(expected - actual)} extra={sorted(actual - expected)})")


def require_object_id(value: Any, label: str) -> str:
    require(isinstance(value, str) and OBJECT_ID.fullmatch(value) is not None, label + " must be one full lowercase Git object ID")
    require(value != ZERO_OID, label + " must not be the zero object ID")
    return value


def require_digest(value: Any, label: str) -> str:
    require(isinstance(value, str) and DIGEST.fullmatch(value) is not None, label + " must be one lowercase SHA-256")
    return value


def parse_utc(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and UTC.fullmatch(value) is not None, label + " must be strict UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PublisherError(label + " is not a valid UTC timestamp") from error
    require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed), label + " must be UTC")
    return parsed


def pin_identity(path: Path) -> dict[str, Any]:
    return {"path": path.as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def validate_file_pin(value: Any, label: str, keys: Sequence[str]) -> Path:
    require(isinstance(value, dict), label + " must be an object")
    exact_keys(value, keys, label)
    raw_path = value.get("path")
    require(isinstance(raw_path, str) and raw_path and Path(raw_path).is_absolute(), label + ".path must be absolute")
    path = regular_unlinked_file(Path(raw_path), label)
    size = value.get("bytes")
    require(isinstance(size, int) and not isinstance(size, bool) and size >= 0, label + ".bytes")
    require(path.stat().st_size == size, label + " byte count mismatch")
    expected = require_digest(value.get("sha256"), label + ".sha256")
    require(sha256_file(path) == expected, label + " SHA-256 mismatch")
    return path


def real_parent(path: Path, label: str) -> Path:
    require(not path.parent.is_symlink(), label + " parent must not be a symbolic link")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise PublisherError(label + " parent is missing or unreadable") from error
    require(parent.is_dir() and not parent.is_symlink(), label + " parent must be a real directory")
    return parent


def write_new(path: Path, value: Any) -> None:
    real_parent(path, "output")
    require(not path.exists() and not path.is_symlink(), "output path already exists: " + str(path))
    payload = canonical(value)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def load_contracts(
    contract_path: Path,
    addendum_path: Path,
    correction_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    contract_file, contract = strict_json_file(contract_path, "publisher contract")
    addendum_file, addendum = strict_json_file(addendum_path, "publisher addendum")
    correction_file, correction = strict_json_file(correction_path, "publisher correction")
    canonical_correction = regular_unlinked_file(ROOT / CORRECTION_RELATIVE, "canonical publisher correction")
    require(correction_file == canonical_correction, "publisher correction must be the canonical repository file")
    require(correction_file.stat().st_size == CORRECTION_BYTES, "publisher correction byte pin")
    require(sha256_file(correction_file) == CORRECTION_SHA256, "publisher correction digest pin")
    require(contract.get("schema") == CONTRACT_SCHEMA, "publisher contract schema")
    require(contract.get("project") == "Crazyhouse-Stockfish", "publisher contract project")
    require(contract.get("phase") == "P15", "publisher contract phase")
    require(addendum.get("schema") == ADDENDUM_SCHEMA, "publisher addendum schema")
    require(addendum.get("project") == "Crazyhouse-Stockfish", "publisher addendum project")
    require(correction.get("schema") == CORRECTION_SCHEMA, "publisher correction schema")
    require(correction.get("project") == "Crazyhouse-Stockfish", "publisher correction project")
    require(correction.get("phase") == "P15", "publisher correction phase")
    parent = addendum.get("parents", {}).get("contract", {})
    require(parent.get("bytes") == contract_file.stat().st_size, "addendum contract byte pin")
    require(parent.get("sha256") == sha256_file(contract_file), "addendum contract digest pin")
    verifier = addendum.get("independent_download_verifier", {})
    verifier_path = ROOT / str(verifier.get("path", ""))
    require(verifier_path.stat().st_size == verifier.get("bytes"), "download verifier byte pin")
    require(sha256_file(verifier_path) == verifier.get("sha256"), "download verifier digest pin")
    correction_parent = correction.get("parent", {})
    require(correction_parent.get("path") == "tests/crazyhouse/p15-g15-publisher-v1.addendum.002.json", "correction parent path")
    require(correction_parent.get("bytes") == addendum_file.stat().st_size, "correction addendum byte pin")
    require(correction_parent.get("sha256") == sha256_file(addendum_file), "correction addendum digest pin")
    query_correction = correction.get("github_query_correction", {})
    require(query_correction.get("required_asset_fields_append") == ["id"], "correction asset field append")
    require(query_correction.get("download_command_prefix") == ["gh", "api", "-H", "Accept: application/octet-stream"], "correction download command prefix")
    require(len({contract_file, addendum_file, correction_file}) == 3, "contract, addendum and correction paths must differ")
    return contract, addendum, correction


def _load_download_verifier(addendum: dict[str, Any]) -> Callable[..., int]:
    relative = addendum["independent_download_verifier"]["path"]
    module_path = ROOT / relative
    spec = importlib.util.spec_from_file_location("crazyhouse_release_download_verifier", module_path)
    if spec is None or spec.loader is None:
        raise PublisherError("cannot load the independent release-download verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, addendum["independent_download_verifier"]["direct_function"], None)
    require(callable(function), "independent release-download verifier function is absent")
    return function


@dataclass(frozen=True)
class DecisionContext:
    decision_path: Path
    decision: dict[str, Any]
    draft_receipt_path: Path
    draft_receipt: dict[str, Any]
    notes_path: Path
    asset_root: Path
    assets: tuple[dict[str, Any], ...]
    independent_verifier_path: Path
    source_date_epoch: int


def validate_decision(path: Path, contract: dict[str, Any], addendum: dict[str, Any]) -> DecisionContext:
    decision_path, value = strict_json_file(path, "owner decision")
    decision_contract = contract["decision_record"]
    exact_keys(value, decision_contract["exact_keys"], "owner decision")
    constants = {
        "schema": decision_contract["schema"],
        "project": decision_contract["project"],
        "decision": decision_contract["decision"],
        "repository": decision_contract["repository"],
        "version": decision_contract["version"],
        "tag": decision_contract["tag"],
        "release_title": decision_contract["release_title"],
        "tag_message": decision_contract["tag_message"],
    }
    for key, expected in constants.items():
        require(value.get(key) == expected, "owner decision mismatch: " + key)
    decided = parse_utc(value.get("decided_utc"), "decided_utc")
    for key in ("candidate_commit", "candidate_tree", "tag_target_commit", "tag_target_tree", "origin_main_commit"):
        require_object_id(value.get(key), key)
    require(value["origin_main_commit"] == value["tag_target_commit"], "origin_main_commit must equal tag_target_commit")
    for key in ("owner", "monitor_owner"):
        item = value.get(key)
        require(isinstance(item, str) and item.strip() == item and 1 <= len(item) <= 128, key + " must be a bounded non-empty string")
        require(all(32 <= ord(character) < 127 for character in item), key + " must be printable ASCII")

    nested = addendum["decision_nested_schemas"]
    tagger = value.get("tagger")
    require(isinstance(tagger, dict), "tagger must be an object")
    exact_keys(tagger, nested["tagger_exact_keys"], "tagger")
    name, email, tagged_at = tagger.get("name"), tagger.get("email"), tagger.get("date")
    require(isinstance(name, str) and name.strip() == name and 1 <= len(name) <= 128, "tagger.name")
    require(not any(character in name for character in "<>\r\n"), "tagger.name contains forbidden characters")
    require(isinstance(email, str) and EMAIL.fullmatch(email) is not None and ".." not in email, "tagger.email")
    require(parse_utc(tagged_at, "tagger.date") == decided, "tagger.date must equal decided_utc")

    pin_keys = nested["file_pin_exact_keys"]
    receipt_path = validate_file_pin(value.get("draft_verification_receipt"), "draft_verification_receipt", pin_keys)
    notes_path = validate_file_pin(value.get("release_notes"), "release_notes", pin_keys)
    _, receipt = strict_json_file(receipt_path, "draft verification receipt")
    receipt_contract = addendum["draft_verification_receipt"]
    exact_keys(receipt, receipt_contract["exact_keys"], "draft verification receipt")
    for key, expected in {
        "schema": receipt_contract["schema"],
        "project": "Crazyhouse-Stockfish",
        "status": receipt_contract["status"],
        "version": value["version"],
        "tag": value["tag"],
        "candidate_commit": value["candidate_commit"],
        "candidate_tree": value["candidate_tree"],
        "tag_target_commit": value["tag_target_commit"],
        "tag_target_tree": value["tag_target_tree"],
    }.items():
        require(receipt.get(key) == expected, "draft verification receipt mismatch: " + key)
    source_date_epoch = receipt.get("source_date_epoch")
    require(isinstance(source_date_epoch, int) and not isinstance(source_date_epoch, bool) and source_date_epoch >= 0, "source_date_epoch")

    receipt_notes = receipt.get("release_notes")
    require(isinstance(receipt_notes, dict), "draft receipt release_notes")
    exact_keys(receipt_notes, receipt_contract["release_notes_exact_keys"], "draft receipt release_notes")
    require(receipt_notes == {"bytes": notes_path.stat().st_size, "sha256": sha256_file(notes_path)}, "release notes differ from the verified draft")

    asset_values = value.get("assets")
    require(isinstance(asset_values, list), "owner decision assets must be a list")
    expected_names = contract["release"]["assets"]
    require([item.get("name") if isinstance(item, dict) else None for item in asset_values] == expected_names, "owner decision asset order differs")
    assets: list[dict[str, Any]] = []
    asset_paths: list[Path] = []
    for index, item in enumerate(asset_values):
        label = f"asset[{index}]"
        require(isinstance(item, dict), label + " must be an object")
        exact_keys(item, nested["asset_pin_exact_keys"], label)
        require(item["name"] == expected_names[index], label + " name differs")
        asset_path = validate_file_pin(item, label, nested["asset_pin_exact_keys"])
        require(asset_path.name == item["name"], label + " basename differs")
        assets.append(dict(item))
        asset_paths.append(asset_path)
    roots = {item.parent for item in asset_paths}
    require(len(roots) == 1, "all release assets must share one directory")
    asset_root = next(iter(roots))
    require(notes_path.parent != asset_root and receipt_path.parent != asset_root, "notes and draft receipt must remain outside the asset directory")
    observed_entries = list(asset_root.iterdir())
    require(all(item.is_file() and not item.is_symlink() for item in observed_entries), "asset directory contains a non-file entry")
    require({item.name for item in observed_entries} == set(expected_names), "asset directory inventory differs from the frozen set")
    require(len({item.name.casefold() for item in observed_entries}) == len(expected_names), "asset directory has a case collision")

    receipt_assets = receipt.get("assets")
    require(isinstance(receipt_assets, list) and len(receipt_assets) == len(assets), "draft receipt asset count")
    expected_receipt_assets = [{"name": item["name"], "bytes": item["bytes"], "sha256": item["sha256"]} for item in assets]
    for item in receipt_assets:
        require(isinstance(item, dict), "draft receipt asset entry")
        exact_keys(item, receipt_contract["asset_entry_exact_keys"], "draft receipt asset entry")
    require(receipt_assets == expected_receipt_assets, "draft receipt assets differ from owner decision")

    verifier = receipt.get("independent_verifier")
    require(isinstance(verifier, dict), "draft receipt independent_verifier")
    exact_keys(verifier, receipt_contract["independent_verifier_exact_keys"], "draft receipt independent_verifier")
    require(verifier.get("result") == receipt_contract["independent_verifier_result"], "draft independent verifier result")
    verifier_path = validate_file_pin({key: verifier[key] for key in nested["file_pin_exact_keys"]}, "draft independent verifier", pin_keys)
    protected = {decision_path, receipt_path, notes_path, *asset_paths}
    require(verifier_path not in protected and verifier_path.parent != asset_root, "draft independent verifier aliases another input")
    require(all(item != decision_path for item in (receipt_path, notes_path, verifier_path, *asset_paths)), "owner decision self-reference")

    return DecisionContext(decision_path, value, receipt_path, receipt, notes_path, asset_root, tuple(assets), verifier_path, source_date_epoch)


def tag_payload(context: DecisionContext) -> bytes:
    tagger = context.decision["tagger"]
    timestamp = int(parse_utc(tagger["date"], "tagger.date").timestamp())
    lines = [
        "object " + context.decision["tag_target_commit"],
        "type commit",
        "tag " + context.decision["tag"],
        f"tagger {tagger['name']} <{tagger['email']}> {timestamp} +0000",
        "",
        context.decision["tag_message"],
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def git_object_id(kind: str, payload: bytes) -> str:
    header = f"{kind} {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class SubprocessRunner:
    def run(self, argv: Sequence[str], cwd: Path, stdin: bytes | None = None, timeout: int = 900) -> CommandResult:
        completed = subprocess.run(
            list(argv), cwd=cwd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class Journal:
    def __init__(self, path: Path):
        self.path = path
        real_parent(path, "journal")
        require(not path.exists() and not path.is_symlink(), "journal path already exists")
        self.previous = ZERO_DIGEST
        self.index = 0

    def append(self, stage: str, details: dict[str, Any]) -> str:
        base = {
            "schema": JOURNAL_SCHEMA,
            "index": self.index,
            "captured_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "stage": stage,
            "previous_sha256": self.previous,
            "details": details,
        }
        digest = sha256_bytes(canonical(base))
        entry = dict(base)
        entry["entry_sha256"] = digest
        mode = "xb" if self.index == 0 else "ab"
        with self.path.open(mode) as stream:
            stream.write(canonical(entry))
            stream.flush()
            os.fsync(stream.fileno())
        self.previous = digest
        self.index += 1
        return digest


class Publisher:
    def __init__(
        self,
        contract: dict[str, Any],
        addendum: dict[str, Any],
        correction: dict[str, Any],
        context: DecisionContext,
        git_root: Path,
        runner: Any | None = None,
        download_verifier: Callable[..., int] | None = None,
    ):
        self.contract = contract
        self.addendum = addendum
        self.correction = correction
        self.context = context
        self.git_root = git_root.resolve(strict=True)
        require(self.git_root.is_dir() and not self.git_root.is_symlink(), "git root must be a real directory")
        self.runner = runner if runner is not None else SubprocessRunner()
        self.download_verifier = download_verifier if download_verifier is not None else _load_download_verifier(addendum)
        self.mutation_started = False

    def command(self, argv: Sequence[str], *, stdin: bytes | None = None, allowed: tuple[int, ...] = (0,), timeout: int = 900) -> CommandResult:
        require(all(isinstance(item, str) and item for item in argv), "command contains an empty argument")
        forbidden = set(self.contract["command_plan"]["forbidden_tokens"])
        require(not forbidden.intersection(argv), "command contains a forbidden token")
        result = self.runner.run(tuple(argv), self.git_root, stdin=stdin, timeout=timeout)
        require(result.returncode in allowed, "command failed: " + argv[0] + " " + " ".join(argv[1:3]))
        return result

    @staticmethod
    def text(result: CommandResult, label: str, *, stderr_empty: bool = True) -> str:
        if stderr_empty:
            require(result.stderr == b"", label + " emitted stderr")
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise PublisherError(label + " stdout is not UTF-8") from error

    def _releases(self) -> list[dict[str, Any]]:
        endpoint = self.addendum["github_query"]["endpoint"]
        result = self.command(("gh", "api", "--paginate", "--slurp", endpoint))
        raw = self.text(result, "GitHub release query")
        try:
            pages = json.loads(raw, object_pairs_hook=strict_object)
        except json.JSONDecodeError as error:
            raise PublisherError("GitHub release query returned invalid JSON") from error
        require(isinstance(pages, list), "GitHub release query must return pages")
        releases: list[dict[str, Any]] = []
        for page in pages:
            require(isinstance(page, list), "GitHub release page must be a list")
            for release in page:
                require(isinstance(release, dict), "GitHub release entry must be an object")
                releases.append(release)
        return releases

    def _matching_releases(self) -> list[dict[str, Any]]:
        return [item for item in self._releases() if item.get("tag_name") == self.context.decision["tag"]]

    def _verify_release(self, value: dict[str, Any], *, draft: bool) -> dict[str, Any]:
        required = set(self.addendum["github_query"]["required_release_fields"])
        require(required.issubset(value), "GitHub release fields are incomplete")
        expected = {
            "tag_name": self.context.decision["tag"],
            "name": self.context.decision["release_title"],
            "draft": draft,
            "prerelease": False,
        }
        require(isinstance(value.get("draft"), bool), "GitHub release draft field must be boolean")
        require(isinstance(value.get("prerelease"), bool), "GitHub release prerelease field must be boolean")
        for key, item in expected.items():
            require(value.get(key) == item, "GitHub release mismatch: " + key)
        release_id = value.get("id")
        require(isinstance(release_id, int) and not isinstance(release_id, bool) and release_id > 0, "GitHub release ID")
        published = value.get("published_at")
        if draft:
            require(published is None, "draft release unexpectedly has published_at")
        else:
            parse_utc(published, "published_at")
        remote_assets = value.get("assets")
        require(isinstance(remote_assets, list), "GitHub assets must be a list")
        required_asset = set(self.addendum["github_query"]["required_asset_fields"])
        required_asset.update(self.correction["github_query_correction"]["required_asset_fields_append"])
        mapped: dict[str, dict[str, Any]] = {}
        asset_ids: set[int] = set()
        for asset in remote_assets:
            require(isinstance(asset, dict) and required_asset.issubset(asset), "GitHub asset fields are incomplete")
            name = asset.get("name")
            require(isinstance(name, str) and name not in mapped, "duplicate or invalid GitHub asset")
            asset_id = asset.get("id")
            require(isinstance(asset_id, int) and not isinstance(asset_id, bool) and asset_id > 0, "GitHub asset ID")
            require(asset_id not in asset_ids, "duplicate GitHub asset ID")
            asset_ids.add(asset_id)
            mapped[name] = asset
        expected_names = [item["name"] for item in self.context.assets]
        require(set(mapped) == set(expected_names), "GitHub asset inventory differs")
        verified_assets: list[dict[str, Any]] = []
        for expected_asset in self.context.assets:
            remote = mapped[expected_asset["name"]]
            remote_size = remote.get("size")
            require(isinstance(remote_size, int) and not isinstance(remote_size, bool) and remote_size >= 0, "GitHub asset size")
            require(remote.get("size") == expected_asset["bytes"], "GitHub asset size mismatch")
            require(remote.get("digest") == "sha256:" + expected_asset["sha256"], "GitHub asset digest mismatch")
            require(remote.get("state") == self.addendum["github_query"]["asset_state"], "GitHub asset state mismatch")
            verified_assets.append({"id": remote["id"], "name": expected_asset["name"], "bytes": expected_asset["bytes"], "sha256": expected_asset["sha256"]})
        return {"id": release_id, "published_at": published, "assets": verified_assets}

    def _remote_main(self) -> str:
        branch = self.contract["release"]["admitted_branch"]
        result = self.command(("git", "ls-remote", self.contract["release"]["remote"], "refs/heads/" + branch))
        text = self.text(result, "origin/main lookup")
        rows = [row.split("\t") for row in text.splitlines() if row]
        require(rows == [[self.context.decision["origin_main_commit"], "refs/heads/" + branch]], "origin/main identity drifted")
        return rows[0][0]

    def _remote_tag_rows(self) -> list[list[str]]:
        ref = "refs/tags/" + self.context.decision["tag"]
        result = self.command(("git", "ls-remote", "--tags", self.contract["release"]["remote"], ref, ref + "^{}"))
        text = self.text(result, "remote tag lookup")
        return [row.split("\t") for row in text.splitlines() if row]

    def preflight(self) -> dict[str, Any]:
        require(self.text(self.command(("git", "status", "--porcelain=v1")), "git status") == "", "repository is dirty")
        top = Path(self.text(self.command(("git", "rev-parse", "--show-toplevel")), "git top-level")).resolve(strict=True)
        require(top == self.git_root, "git root differs from --show-toplevel")
        require(self.text(self.command(("git", "rev-parse", "--show-object-format")), "Git object format") == self.addendum["git"]["object_format"], "Git object format differs")
        decision = self.context.decision
        require(self.text(self.command(("git", "rev-parse", "HEAD")), "HEAD") == decision["tag_target_commit"], "HEAD differs from tag target")
        require(self.text(self.command(("git", "rev-parse", "HEAD^{tree}")), "HEAD tree") == decision["tag_target_tree"], "HEAD tree differs")
        require(self.text(self.command(("git", "rev-parse", decision["candidate_commit"] + "^{commit}")), "candidate commit") == decision["candidate_commit"], "candidate commit does not resolve")
        require(self.text(self.command(("git", "rev-parse", decision["candidate_commit"] + "^{tree}")), "candidate tree") == decision["candidate_tree"], "candidate tree differs")
        ancestry = self.command(("git", "merge-base", "--is-ancestor", decision["candidate_commit"], decision["tag_target_commit"]), allowed=(0, 1))
        require(ancestry.returncode == 0 and ancestry.stdout == b"" and ancestry.stderr == b"", "candidate is not an ancestor of tag target")
        origin = self.text(self.command(("git", "remote", "get-url", self.contract["release"]["remote"])), "origin URL")
        require(origin == self.addendum["git"]["accepted_origin_url"], "origin URL differs from publisher authority")
        self._remote_main()
        ref = "refs/tags/" + decision["tag"]
        local = self.command(("git", "show-ref", "--verify", "--quiet", ref), allowed=(0, 1))
        require(local.returncode == 1 and local.stdout == b"" and local.stderr == b"", "local stable tag already exists")
        require(self._remote_tag_rows() == [], "remote stable tag already exists")
        require(self._matching_releases() == [], "GitHub release already exists for the stable tag")
        try:
            count = self.download_verifier(
                self.context.asset_root,
                self.context.asset_root,
                decision["version"],
                decision["candidate_commit"],
                decision["candidate_tree"],
                self.context.source_date_epoch,
            )
        except Exception as error:
            raise PublisherError("local draft failed independent release verification") from error
        require(count == self.addendum["independent_download_verifier"]["expected_file_count"], "independent local draft file count")
        payload = tag_payload(self.context)
        tag_oid = git_object_id("tag", payload)
        return {
            "status": self.contract["preflight"]["result_before_mutation"],
            "repository": decision["repository"],
            "candidate_commit": decision["candidate_commit"],
            "tag_target_commit": decision["tag_target_commit"],
            "origin_main_commit": decision["origin_main_commit"],
            "tag": decision["tag"],
            "prospective_tag_object": tag_oid,
            "assets": [{"name": item["name"], "bytes": item["bytes"], "sha256": item["sha256"]} for item in self.context.assets],
            "network_calls_in_qualification": 0,
            "public_writes_in_qualification": 0,
        }

    def _verify_local_tag(self, expected_tag_oid: str) -> None:
        ref = "refs/tags/" + self.context.decision["tag"]
        actual = self.text(self.command(("git", "rev-parse", ref)), "local tag object")
        peeled = self.text(self.command(("git", "rev-parse", ref + "^{}")), "local tag peeled commit")
        kind = self.text(self.command(("git", "cat-file", "-t", actual)), "local tag type")
        require(actual == expected_tag_oid and peeled == self.context.decision["tag_target_commit"] and kind == "tag", "local annotated tag identity differs")

    def _verify_remote_tag(self, expected_tag_oid: str) -> None:
        ref = "refs/tags/" + self.context.decision["tag"]
        require(self._remote_tag_rows() == [[expected_tag_oid, ref], [self.context.decision["tag_target_commit"], ref + "^{}"]], "remote annotated tag identity differs")

    def _draft_command(self) -> tuple[str, ...]:
        decision = self.context.decision
        return (
            "gh", "release", "create", decision["tag"],
            *(str(Path(item["path"]).resolve(strict=True)) for item in self.context.assets),
            "--draft", "--verify-tag", "--latest=false", "--repo", decision["repository"],
            "--title", decision["release_title"], "--notes-file", str(self.context.notes_path),
        )

    def _download_assets(self, release: dict[str, Any], directory: Path) -> int:
        real_parent(directory, "release download directory")
        require(not directory.exists() and not directory.is_symlink(), "release download directory already exists")
        directory.mkdir(parents=False, exist_ok=False)
        require(directory.is_dir() and not directory.is_symlink(), "release download directory was not created safely")
        prefix = tuple(self.correction["github_query_correction"]["download_command_prefix"])
        template = self.correction["github_query_correction"]["download_endpoint_template"]
        expected = {item["name"]: item for item in self.context.assets}
        for remote in release["assets"]:
            item = expected[remote["name"]]
            endpoint = template.replace("<asset_id>", str(remote["id"]))
            response = self.command((*prefix, endpoint), timeout=3600)
            require(response.stderr == b"", "GitHub release asset download emitted stderr")
            destination = directory / item["name"]
            require(destination.parent == directory, "release asset destination escaped download directory")
            require(not destination.exists() and not destination.is_symlink(), "release asset destination already exists")
            with destination.open("xb") as stream:
                stream.write(response.stdout)
                stream.flush()
                os.fsync(stream.fileno())
            require(destination.stat().st_size == item["bytes"], "downloaded release asset size mismatch")
            require(sha256_file(destination) == item["sha256"], "downloaded release asset SHA-256 mismatch")
        return len(release["assets"])

    def _publish_command(self) -> tuple[str, ...]:
        decision = self.context.decision
        return ("gh", "release", "edit", decision["tag"], "--repo", decision["repository"], "--draft=false", "--latest", "--verify-tag")

    def execute(self, journal_path: Path, result_path: Path, download_dir: Path) -> dict[str, Any]:
        for path, label in ((journal_path, "journal"), (result_path, "result"), (download_dir, "download directory")):
            require(not path.exists() and not path.is_symlink(), label + " already exists")
            real_parent(path, label)
        journal: Journal | None = None
        try:
            plan = self.preflight()
            journal = Journal(journal_path)
            journal.append("PREFLIGHT_AUTHENTICATED", plan)
            payload = tag_payload(self.context)
            expected_tag_oid = git_object_id("tag", payload)
            self.mutation_started = True
            mktag = self.command(("git", "mktag"), stdin=payload)
            tag_oid = self.text(mktag, "git mktag")
            require(tag_oid == expected_tag_oid, "git mktag object ID differs")
            ref = "refs/tags/" + self.context.decision["tag"]
            self.command(("git", "update-ref", ref, tag_oid, ZERO_OID))
            self._verify_local_tag(tag_oid)
            journal.append("LOCAL_ANNOTATED_TAG_CREATED", {"tag": self.context.decision["tag"], "tag_object": tag_oid, "peeled_commit": self.context.decision["tag_target_commit"]})

            self.command(tuple(self.contract["command_plan"]["push"]))
            self._verify_remote_tag(tag_oid)
            journal.append("REMOTE_TAG_PUSHED_AND_REAUTHENTICATED", {"tag_object": tag_oid, "peeled_commit": self.context.decision["tag_target_commit"]})

            self.command(self._draft_command(), timeout=1800)
            matches = self._matching_releases()
            require(len(matches) == 1, "created draft release count differs")
            draft = self._verify_release(matches[0], draft=True)
            journal.append("REMOTE_DRAFT_CREATED", {"release_id": draft["id"], "assets": draft["assets"]})

            downloaded = self._download_assets(draft, download_dir)
            require(downloaded == self.addendum["independent_download_verifier"]["expected_file_count"], "downloaded draft asset count")
            try:
                count = self.download_verifier(
                    self.context.asset_root,
                    download_dir,
                    self.context.decision["version"],
                    self.context.decision["candidate_commit"],
                    self.context.decision["candidate_tree"],
                    self.context.source_date_epoch,
                )
            except Exception as error:
                raise PublisherError("downloaded draft failed independent verification") from error
            require(count == self.addendum["independent_download_verifier"]["expected_file_count"], "downloaded draft file count")
            matches = self._matching_releases()
            require(len(matches) == 1, "draft release disappeared after download")
            draft_after = self._verify_release(matches[0], draft=True)
            require(draft_after == draft, "draft release changed during reauthentication")
            journal.append("REMOTE_DRAFT_DOWNLOADED_AND_REAUTHENTICATED", {"release_id": draft["id"], "downloaded_files": count})

            self.command(self._publish_command())
            matches = self._matching_releases()
            require(len(matches) == 1, "published release count differs")
            published = self._verify_release(matches[0], draft=False)
            require(published["id"] == draft["id"], "published release ID differs from draft")
            require(published["assets"] == draft["assets"], "published release assets differ from draft")
            journal.append("STABLE_RELEASE_PUBLISHED", {"release_id": published["id"], "published_at": published["published_at"]})
            monitor = {
                "release_id": published["id"],
                "published_at": published["published_at"],
                "tag": self.context.decision["tag"],
                "tag_object": tag_oid,
                "peeled_commit": self.context.decision["tag_target_commit"],
                "assets": published["assets"],
                "monitor_owner": self.context.decision["monitor_owner"],
            }
            journal.append("T0_MONITOR_HANDOFF_EMITTED", monitor)
            result = {
                "schema": RESULT_SCHEMA,
                "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "project": "Crazyhouse-Stockfish",
                "phase": "P15",
                "status": self.addendum["result"]["success"],
                "decision": pin_identity(self.context.decision_path),
                "release": monitor,
                "journal": {"path": journal.path.as_posix(), "entries": journal.index, "tip_sha256": journal.previous},
                "stable_publication_authorized": True,
                "openbench_used": False,
            }
            write_new(result_path, result)
            return result
        except Exception as error:
            status = self.addendum["result"]["partial_failure"] if self.mutation_started else self.addendum["result"]["preflight_failure"]
            if journal is not None:
                try:
                    journal.append("TRANSACTION_FAILED", {"status": status, "error_type": type(error).__name__})
                except Exception:
                    pass
            if not result_path.exists() and result_path.parent.exists():
                write_new(result_path, {
                    "schema": RESULT_SCHEMA,
                    "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "project": "Crazyhouse-Stockfish",
                    "phase": "P15",
                    "status": status,
                    "error_type": type(error).__name__,
                    "journal": None if journal is None else {"path": journal.path.as_posix(), "entries": journal.index, "tip_sha256": journal.previous},
                    "automatic_retry": False,
                    "automatic_rollback": False,
                })
            raise


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--addendum", type=Path, required=True)
    parser.add_argument("--correction", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--git-root", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        contract, addendum, correction = load_contracts(args.contract, args.addendum, args.correction)
        context = validate_decision(args.decision, contract, addendum)
        publisher = Publisher(contract, addendum, correction, context, args.git_root)
        if args.execute:
            result = publisher.execute(args.journal, args.result, args.download_dir)
        else:
            require(not args.journal.exists() and not args.result.exists() and not args.download_dir.exists(), "read-only plan output paths must be absent")
            result = publisher.preflight()
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (PublisherError, OSError, subprocess.SubprocessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
