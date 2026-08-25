#!/usr/bin/env python3
"""Generate or verify the deterministic Crazyhouse local-gate opening corpus.

The corpus is rules-only data.  Move choice uses SHA-256 over a frozen seed,
trajectory index, and ply; it never calls an evaluator or chess engine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import chess
import chess.variant


SCHEMA = "crazyhouse-local-gate-opening-corpus/v2"
TRAJECTORY_SCHEMA = "crazyhouse-local-gate-opening-trajectory/v2"
ARTIFACT_ID = "CH-LOCAL-GATE-OPENINGS-V2"
PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
EXPECTED_REFERENCE_COMMIT = "9c24454dcea4f8a30259d811a2f10b26e911deb4"
EXPECTED_REFERENCE_TREE = "33627273cd58c1a5a20c3132548e5df7b85ff9d6"
EXPECTED_REFERENCE_VERSION = "1.11.2"
EXPECTED_REFERENCE_LICENSE_BLOB = "94a9ed024d3859793618152ea559a168bbcbb5e2"
EXPECTED_REFERENCE_LICENSE_BYTES = 35147
EXPECTED_REFERENCE_LICENSE_SHA256 = (
    "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
)
SEED = b"Crazyhouse-Stockfish local same-network opening corpus v1"
TARGET_COUNT = 1024
MIN_DEPTH = 12
DEPTH_COUNT = 8
MAX_CANDIDATE_INDEX = 65535
POCKET_ORDER = "PNBRQpnbrq"
DEFAULT_BOOK_LOGICAL_PATH = (
    "tests/crazyhouse/data/crazyhouse-local-gate-openings-v2.epd"
)
DEFAULT_TRAJECTORY_LOGICAL_PATH = (
    "tests/crazyhouse/data/crazyhouse-local-gate-openings-v2.trajectories.jsonl"
)
DEFAULT_GENERATOR_LOGICAL_PATH = (
    "tools/strength/generate_crazyhouse_opening_corpus.py"
)
COVERAGE_REQUIREMENTS = {
    "accepted_roots": TARGET_COUNT,
    "black_to_move": 512,
    "capture_history_min": 400,
    "depth_count_each": 128,
    "drop_history_min": 250,
    "final_nonempty_pocket_min": 200,
    "in_check_min": 8,
    "raw_ep_target_set_exact": 121,
    "pseudo_legal_en_passant_available_exact": 4,
    "legal_en_passant_available_exact": 4,
    "promoted_state_min": 1,
    "white_to_move": 512,
}


class CorpusFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CorpusFailure(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CorpusFailure(f"git {' '.join(arguments)} failed: {stderr}")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def git_blob(root: Path, revision: str) -> tuple[str, bytes]:
    object_id = git(root, "rev-parse", revision)
    completed = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", object_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CorpusFailure(f"git cat-file blob {object_id} failed: {stderr}")
    return object_id, completed.stdout


def git_config(root: Path, key: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "config", "--get", key],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode == 1 and not completed.stdout and not completed.stderr:
        return "unset"
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CorpusFailure(f"git config --get {key} failed: {stderr}")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def authenticate_reference(root: Path) -> dict[str, Any]:
    root = root.resolve()
    module_path = Path(chess.__file__).resolve()
    try:
        module_relative = module_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CorpusFailure(
            f"loaded chess module {module_path} is outside required checkout {root}"
        ) from exc

    commit = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    dirty = git(root, "status", "--porcelain")
    require(commit == EXPECTED_REFERENCE_COMMIT, f"wrong reference commit: {commit}")
    require(tree == EXPECTED_REFERENCE_TREE, f"wrong reference tree: {tree}")
    require(not dirty, "python-chess reference checkout is dirty")
    require(chess.__version__ == EXPECTED_REFERENCE_VERSION, "wrong python-chess version")

    license_path = root / "LICENSE.txt"
    require(license_path.is_file(), "python-chess LICENSE.txt is missing")
    license_object, license_blob = git_blob(root, "HEAD:LICENSE.txt")
    require(
        license_object == EXPECTED_REFERENCE_LICENSE_BLOB,
        "python-chess license blob identity drifted",
    )
    require(
        len(license_blob) == EXPECTED_REFERENCE_LICENSE_BYTES,
        "python-chess license blob size drifted",
    )
    require(
        sha256_bytes(license_blob) == EXPECTED_REFERENCE_LICENSE_SHA256,
        "python-chess license blob bytes drifted",
    )
    checkout_autocrlf = git_config(root, "core.autocrlf")
    return {
        "repository": "https://github.com/niklasf/python-chess.git",
        "commit": commit,
        "tree": tree,
        "version": chess.__version__,
        "module_relative_path": module_relative,
        "module_bytes": module_path.stat().st_size,
        "module_sha256": sha256_file(module_path),
        "license_spdx": "GPL-3.0-or-later",
        "license_relative_path": "LICENSE.txt",
        "license_blob_object_id": license_object,
        "license_bytes": len(license_blob),
        "license_sha256": sha256_bytes(license_blob),
        "license_worktree_bytes": license_path.stat().st_size,
        "license_worktree_sha256": sha256_file(license_path),
        "checkout_core_autocrlf": checkout_autocrlf,
        "role": "authenticated_rules_only_generator_reference",
        "result_authority": False,
    }


def canonical_fen(board: chess.variant.CrazyhouseBoard) -> str:
    raw = board.fen(promoted=True, en_passant="legal")
    fields = raw.split()
    require(len(fields) == 6, "reference emitted a non-six-field FEN")
    placement = fields[0]
    require(placement.endswith("]") and "[" in placement, "pocket field missing")
    board_part, pocket = placement[:-1].rsplit("[", 1)
    require(all(symbol in POCKET_ORDER for symbol in pocket), "invalid pocket symbol")
    ordered = "".join(symbol * pocket.count(symbol) for symbol in POCKET_ORDER)
    fields[0] = f"{board_part}[{ordered}]"
    return " ".join(fields)


def is_authority_terminal(board: chess.variant.CrazyhouseBoard) -> bool:
    return board.is_checkmate() or board.is_stalemate() or board.is_fivefold_repetition()


def choose_move(
    board: chess.variant.CrazyhouseBoard, candidate_index: int, ply: int
) -> chess.Move:
    legal = sorted(board.legal_moves, key=lambda move: move.uci())
    require(bool(legal), "nonterminal root has no legal moves")
    digest = hashlib.sha256(
        SEED
        + b"\0"
        + candidate_index.to_bytes(8, "big")
        + ply.to_bytes(2, "big")
    ).digest()
    return legal[int.from_bytes(digest, "big") % len(legal)]


def squares(bitboard: int) -> list[str]:
    return [chess.square_name(square) for square in chess.scan_forward(bitboard)]


def pocket_counts(board: chess.variant.CrazyhouseBoard) -> dict[str, dict[str, int]]:
    roles = {
        "pawn": chess.PAWN,
        "knight": chess.KNIGHT,
        "bishop": chess.BISHOP,
        "rook": chess.ROOK,
        "queen": chess.QUEEN,
    }
    return {
        color_name: {
            role: board.pockets[color].count(piece_type)
            for role, piece_type in roles.items()
        }
        for color_name, color in (("white", chess.WHITE), ("black", chess.BLACK))
    }


def trajectory_record(
    accepted_index: int, candidate_index: int, target_depth: int
) -> tuple[dict[str, Any] | None, str]:
    board = chess.variant.CrazyhouseBoard()
    moves: list[str] = []
    captures = 0
    drops = 0
    promotions = 0

    for ply in range(target_depth):
        if is_authority_terminal(board):
            return None, "terminal_before_target_depth"
        move = choose_move(board, candidate_index, ply)
        captures += int(board.is_capture(move))
        drops += int(move.drop is not None)
        promotions += int(move.promotion is not None)
        moves.append(move.uci())
        board.push(move)

    if is_authority_terminal(board):
        return None, "terminal_at_target_depth"

    fen = canonical_fen(board)
    fields = fen.split()
    raw_ep_square = (
        None if board.ep_square is None else chess.square_name(board.ep_square)
    )
    pseudo_legal_en_passant = board.has_pseudo_legal_en_passant()
    legal_en_passant = board.has_legal_en_passant()
    require(
        (fields[3] != "-") == legal_en_passant,
        "canonical en-passant field disagrees with legal availability",
    )
    require(
        not legal_en_passant or pseudo_legal_en_passant,
        "legal en-passant is not pseudo-legal",
    )
    identifier = f"CHLG-{accepted_index + 1:04d}"
    pockets = pocket_counts(board)
    record = {
        "schema": TRAJECTORY_SCHEMA,
        "id": identifier,
        "accepted_index": accepted_index,
        "candidate_index": candidate_index,
        "target_depth": target_depth,
        "moves": moves,
        "moves_sha256": sha256_bytes((" ".join(moves) + "\n").encode("ascii")),
        "canonical_fen": fen,
        "physical_identity": " ".join(fields[:4]),
        "board_and_pocket": fields[0],
        "turn": "white" if board.turn == chess.WHITE else "black",
        "castling_rights": fields[2],
        "raw_ep_square": raw_ep_square,
        "pseudo_legal_en_passant_available": pseudo_legal_en_passant,
        "legal_ep_square": None if fields[3] == "-" else fields[3],
        "legal_en_passant_available": legal_en_passant,
        "halfmove_clock": board.halfmove_clock,
        "fullmove_number": board.fullmove_number,
        "pockets": pockets,
        "promoted_squares": squares(board.promoted),
        "in_check": board.is_check(),
        "legal_move_count": board.legal_moves.count(),
        "history_counts": {
            "captures": captures,
            "drops": drops,
            "promotions": promotions,
        },
        "evaluator_used": False,
        "engine_used": False,
    }
    epd = (
        f"{' '.join(fields[:4])} hmvc {fields[4]}; fmvn {fields[5]}; "
        f'id "{identifier}";'
    )
    return record, epd


def coverage(records: list[dict[str, Any]], rejected: Counter[str]) -> dict[str, Any]:
    depths = Counter(record["target_depth"] for record in records)
    physical = [record["physical_identity"] for record in records]
    board_only = [record["board_and_pocket"] for record in records]
    full_fens = [record["canonical_fen"] for record in records]

    def has_pocket(record: dict[str, Any]) -> bool:
        return any(
            count
            for side in record["pockets"].values()
            for count in side.values()
        )

    result = {
        "accepted_roots": len(records),
        "candidate_indices_consumed": (
            0 if not records else records[-1]["candidate_index"] + 1
        ),
        "rejected": dict(sorted(rejected.items())),
        "depth_counts": {str(depth): depths[depth] for depth in sorted(depths)},
        "white_to_move": sum(record["turn"] == "white" for record in records),
        "black_to_move": sum(record["turn"] == "black" for record in records),
        "capture_history": sum(
            record["history_counts"]["captures"] > 0 for record in records
        ),
        "drop_history": sum(
            record["history_counts"]["drops"] > 0 for record in records
        ),
        "promotion_history": sum(
            record["history_counts"]["promotions"] > 0 for record in records
        ),
        "final_nonempty_pocket": sum(has_pocket(record) for record in records),
        "promoted_state": sum(bool(record["promoted_squares"]) for record in records),
        "in_check": sum(record["in_check"] for record in records),
        "raw_ep_target_set": sum(
            record["raw_ep_square"] is not None for record in records
        ),
        "pseudo_legal_en_passant_available": sum(
            record["pseudo_legal_en_passant_available"] for record in records
        ),
        "legal_en_passant_available": sum(
            record["legal_en_passant_available"] for record in records
        ),
        "castling_rights_present": sum(
            record["castling_rights"] != "-" for record in records
        ),
        "full_fen_unique": len(set(full_fens)),
        "physical_identity_unique": len(set(physical)),
        "board_and_pocket_unique": len(set(board_only)),
    }
    return result


def check_coverage(value: dict[str, Any]) -> None:
    require(value["accepted_roots"] == TARGET_COUNT, "wrong accepted root count")
    require(value["full_fen_unique"] == TARGET_COUNT, "duplicate full FEN")
    require(value["physical_identity_unique"] == TARGET_COUNT, "physical duplicate")
    require(value["board_and_pocket_unique"] == TARGET_COUNT, "board/pocket duplicate")
    require(value["white_to_move"] == 512, "white-to-move balance failed")
    require(value["black_to_move"] == 512, "black-to-move balance failed")
    require(value["capture_history"] >= 400, "capture-history coverage failed")
    require(value["drop_history"] >= 250, "drop-history coverage failed")
    require(value["final_nonempty_pocket"] >= 200, "pocket coverage failed")
    require(value["promoted_state"] >= 1, "promoted-state coverage failed")
    require(value["in_check"] >= 8, "check-state coverage failed")
    require(value["raw_ep_target_set"] == 121, "raw EP-target coverage drifted")
    require(
        value["pseudo_legal_en_passant_available"] == 4,
        "pseudo-legal en-passant coverage drifted",
    )
    require(
        value["legal_en_passant_available"] == 4,
        "legal en-passant coverage drifted",
    )
    expected_depths = {str(depth): 128 for depth in range(MIN_DEPTH, MIN_DEPTH + DEPTH_COUNT)}
    require(value["depth_counts"] == expected_depths, "depth balance failed")


def generate_records() -> tuple[list[dict[str, Any]], list[str], Counter[str]]:
    records: list[dict[str, Any]] = []
    epd_lines: list[str] = []
    rejected: Counter[str] = Counter()
    seen: set[str] = set()
    candidate_index = 0

    while len(records) < TARGET_COUNT:
        require(candidate_index <= MAX_CANDIDATE_INDEX, "candidate index ceiling reached")
        target_depth = MIN_DEPTH + (len(records) % DEPTH_COUNT)
        record, epd = trajectory_record(len(records), candidate_index, target_depth)
        candidate_index += 1
        if record is None:
            rejected[epd] += 1
            continue
        if record["physical_identity"] in seen:
            rejected["duplicate_physical_identity"] += 1
            continue
        seen.add(record["physical_identity"])
        records.append(record)
        epd_lines.append(epd)

    return records, epd_lines, rejected


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )


def jsonl_bytes(records: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("utf-8")
        for record in records
    )


def build_artifacts(
    reference: dict[str, Any],
    book_logical_path: str,
    trajectory_logical_path: str,
    generator_logical_path: str,
) -> tuple[bytes, bytes, bytes]:
    records, epd_lines, rejected = generate_records()
    book = ("\n".join(epd_lines) + "\n").encode("ascii")
    trajectories = jsonl_bytes(records)
    observed_coverage = coverage(records, rejected)
    check_coverage(observed_coverage)

    generator_path = Path(__file__).resolve()
    generator_raw = generator_path.read_bytes()
    manifest = {
        "schema": SCHEMA,
        "artifact_id": ARTIFACT_ID,
        "evidence_class": "E1_ENGINEERING",
        "purpose": "engine-independent paired openings for the local same-network comparator gate",
        "rule_profile": {
            "id": PROFILE_ID,
            "sha256": PROFILE_SHA256,
        },
        "generator": {
            "path": generator_logical_path,
            "bytes": len(generator_raw),
            "sha256": sha256_bytes(generator_raw),
            "algorithm": "sorted legal UCI moves; SHA-256(seed || NUL || uint64be(candidate_index) || uint16be(ply)) modulo legal count",
            "seed_utf8": SEED.decode("ascii"),
            "seed_sha256": sha256_bytes(SEED),
            "target_count": TARGET_COUNT,
            "depth_cycle_inclusive": [MIN_DEPTH, MIN_DEPTH + DEPTH_COUNT - 1],
            "candidate_index_start": 0,
            "candidate_index_ceiling": MAX_CANDIDATE_INDEX,
            "terminal_rejection": ["checkmate", "stalemate", "fivefold_repetition"],
            "duplicate_rejection_key": "placement+pockets+turn+castling+legal_ep",
            "evaluator_used": False,
            "engine_used": False,
        },
        "reference": reference,
        "runtime": {
            "python_implementation": sys.implementation.name,
            "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        },
        "outputs": {
            "book": {
                "path": book_logical_path,
                "bytes": len(book),
                "sha256": sha256_bytes(book),
                "rows": TARGET_COUNT,
                "format": "EPD with bracket pockets, legal EP, hmvc, fmvn and id",
                "encoding": "US-ASCII",
                "line_endings": "LF",
            },
            "trajectories": {
                "path": trajectory_logical_path,
                "bytes": len(trajectories),
                "sha256": sha256_bytes(trajectories),
                "records": TARGET_COUNT,
                "format": "JSONL",
                "encoding": "UTF-8",
                "line_endings": "LF",
            },
        },
        "coverage_requirements": COVERAGE_REQUIREMENTS,
        "coverage_observed": observed_coverage,
        "license": {
            "spdx": "CC0-1.0",
            "dedication": "To the extent possible under law, the Crazyhouse-Stockfish contributors waive all copyright and related or neighboring rights in this generated corpus.",
            "source_code_license_unmodified": True,
        },
        "scientific_boundary": {
            "opening_selection_used_engine_output": False,
            "opening_selection_used_network_output": False,
            "opening_selection_used_match_result": False,
            "strength_claim": False,
            "timing_claim": False,
            "openbench_claim": False,
            "release_claim": False,
        },
    }
    return book, trajectories, json_bytes(manifest)


def require_fresh_output(path: Path) -> None:
    require(not path.exists(), f"refusing to overwrite {path}")
    require(path.parent.is_dir(), f"output parent does not exist: {path.parent}")


def compare_exact(path: Path, expected: bytes) -> None:
    require(path.is_file(), f"missing artifact: {path}")
    actual = path.read_bytes()
    require(actual == expected, f"byte mismatch: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-chess-root", type=Path, required=True)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--trajectories", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--book-logical-path", default=DEFAULT_BOOK_LOGICAL_PATH)
    parser.add_argument(
        "--trajectory-logical-path", default=DEFAULT_TRAJECTORY_LOGICAL_PATH
    )
    parser.add_argument("--generator-logical-path", default=DEFAULT_GENERATOR_LOGICAL_PATH)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--generate", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    reference = authenticate_reference(args.python_chess_root)
    book, trajectories, manifest = build_artifacts(
        reference,
        args.book_logical_path,
        args.trajectory_logical_path,
        args.generator_logical_path,
    )

    if args.generate:
        for output in (args.book, args.trajectories, args.manifest):
            require_fresh_output(output)
        args.book.write_bytes(book)
        args.trajectories.write_bytes(trajectories)
        args.manifest.write_bytes(manifest)
    else:
        compare_exact(args.book, book)
        compare_exact(args.trajectories, trajectories)
        compare_exact(args.manifest, manifest)

    summary = {
        "result": "PASS",
        "mode": "generate" if args.generate else "verify",
        "book": {"bytes": len(book), "sha256": sha256_bytes(book)},
        "trajectories": {
            "bytes": len(trajectories),
            "sha256": sha256_bytes(trajectories),
        },
        "manifest": {"bytes": len(manifest), "sha256": sha256_bytes(manifest)},
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CorpusFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
