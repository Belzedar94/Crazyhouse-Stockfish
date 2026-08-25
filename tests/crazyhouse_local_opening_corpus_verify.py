#!/usr/bin/env python3
"""Qualify the generated Crazyhouse local-gate opening corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import chess
import chess.variant


CREATE_NO_WINDOW = 0x08000000
PROFILE_ID = "LICHESS_CRAZYHOUSE_2026_08_12"
PROFILE_SHA256 = "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68"
NETWORK_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
CORPUS_SCHEMA = "crazyhouse-local-gate-opening-corpus/v2"
TRAJECTORY_SCHEMA = "crazyhouse-local-gate-opening-trajectory/v2"
REFERENCE_VERSION = "1.11.2"
POCKET_ORDER = "PNBRQpnbrq"
SELECTION_IDENTITY_SHA256 = "7700adca41c6c016da82f1a5a97ca08186d54c6f7953dae4360af0edffe00d2a"
ACK_RE = re.compile(
    r"crazyhouse_capability_ack status=ok profile="
    + PROFILE_ID
    + r" profile_sha256="
    + PROFILE_SHA256
    + r" nonce=([0-9a-f]{32})"
)


class VerificationFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    require(not raw.startswith(b"\xef\xbb\xbf"), f"BOM forbidden: {path}")
    require(b"\r" not in raw, f"CR bytes forbidden: {path}")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        counts = Counter(key for key, _ in pairs)
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        require(not duplicates, f"duplicate JSON keys in {path}: {duplicates}")
        return dict(pairs)

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(f"invalid JSON {path}: {exc}") from exc
    require(isinstance(value, dict), f"top level is not an object: {path}")
    return value


def resolve_pin(repo: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def authenticate_pin(repo: Path, pin: dict[str, Any]) -> Path:
    require(set(("path", "bytes", "sha256")) <= pin.keys(), "malformed file pin")
    path = resolve_pin(repo, str(pin["path"]))
    require(path.is_file(), f"pinned file missing: {path}")
    require(path.stat().st_size == int(pin["bytes"]), f"byte drift: {path}")
    require(sha256_file(path) == pin["sha256"], f"SHA-256 drift: {path}")
    return path


def run_checked(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationFailure(f"command timed out: {command[0]}") from exc
    require(completed.returncode == 0, f"command failed {completed.returncode}: {command[0]}")
    return completed


def verify_generator(
    python: Path,
    generator: Path,
    python_chess_root: Path,
    book: Path,
    trajectories: Path,
    manifest: Path,
    repo: Path,
) -> dict[str, Any]:
    completed = run_checked(
        [
            str(python),
            "-B",
            str(generator),
            "--python-chess-root",
            str(python_chess_root),
            "--book",
            str(book),
            "--trajectories",
            str(trajectories),
            "--manifest",
            str(manifest),
            "--verify",
        ],
        cwd=repo,
        timeout=180,
    )
    require(not completed.stderr, "generator verifier emitted stderr")
    try:
        summary = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure("generator verifier summary is invalid") from exc
    require(summary.get("result") == "PASS", "generator verifier did not pass")
    return summary


def parse_trajectories(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    require(not raw.startswith(b"\xef\xbb\xbf"), "trajectory BOM forbidden")
    require(b"\r" not in raw, "trajectory CR bytes forbidden")
    lines = raw.splitlines()
    require(len(lines) == 1024, "trajectory record count is not 1024")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationFailure(f"invalid trajectory line {index + 1}") from exc
        require(record["accepted_index"] == index, f"accepted index mismatch at {index}")
        require(record["id"] == f"CHLG-{index + 1:04d}", f"ID mismatch at {index}")
        require(len(record["moves"]) == record["target_depth"], f"depth mismatch at {index}")
        records.append(record)
    return records


def canonical_fen(board: chess.variant.CrazyhouseBoard) -> str:
    fields = board.fen(promoted=True, en_passant="legal").split()
    require(len(fields) == 6, "replay emitted a non-six-field FEN")
    placement = fields[0]
    require(placement.endswith("]") and "[" in placement, "replay pocket field missing")
    board_part, pocket = placement[:-1].rsplit("[", 1)
    require(all(symbol in POCKET_ORDER for symbol in pocket), "replay pocket symbol invalid")
    ordered = "".join(symbol * pocket.count(symbol) for symbol in POCKET_ORDER)
    fields[0] = f"{board_part}[{ordered}]"
    return " ".join(fields)


def replay_pockets(board: chess.variant.CrazyhouseBoard) -> dict[str, dict[str, int]]:
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


def replay_trajectories(
    records: list[dict[str, Any]], python_chess_root: Path
) -> dict[str, Any]:
    root = python_chess_root.resolve()
    module = Path(chess.__file__).resolve()
    try:
        module.relative_to(root)
    except ValueError as exc:
        raise VerificationFailure(
            f"loaded chess module {module} is outside required checkout {root}"
        ) from exc
    require(chess.__version__ == REFERENCE_VERSION, "replay python-chess version drift")

    depth_counts: Counter[int] = Counter()
    physical: list[str] = []
    board_only: list[str] = []
    full_fens: list[str] = []
    selection_projection: list[list[Any]] = []

    for index, record in enumerate(records):
        required = {
            "schema",
            "id",
            "accepted_index",
            "candidate_index",
            "target_depth",
            "moves",
            "moves_sha256",
            "canonical_fen",
            "physical_identity",
            "board_and_pocket",
            "turn",
            "castling_rights",
            "raw_ep_square",
            "pseudo_legal_en_passant_available",
            "legal_ep_square",
            "legal_en_passant_available",
            "halfmove_clock",
            "fullmove_number",
            "pockets",
            "promoted_squares",
            "in_check",
            "legal_move_count",
            "history_counts",
            "evaluator_used",
            "engine_used",
        }
        require(required <= record.keys(), f"trajectory fields missing at row {index + 1}")
        require(record["schema"] == TRAJECTORY_SCHEMA, f"trajectory schema drift at row {index + 1}")
        require(record["evaluator_used"] is False, f"evaluator flag set at row {index + 1}")
        require(record["engine_used"] is False, f"engine flag set at row {index + 1}")

        board = chess.variant.CrazyhouseBoard()
        captures = 0
        drops = 0
        promotions = 0
        for ply, move_text in enumerate(record["moves"]):
            try:
                move = board.parse_uci(move_text)
            except (ValueError, chess.IllegalMoveError) as exc:
                raise VerificationFailure(
                    f"unparseable replay move at row {index + 1}, ply {ply + 1}"
                ) from exc
            require(move in board.legal_moves, f"illegal replay move at row {index + 1}, ply {ply + 1}")
            captures += int(board.is_capture(move))
            drops += int(move.drop is not None)
            promotions += int(move.promotion is not None)
            board.push(move)

        fen = canonical_fen(board)
        fields = fen.split()
        raw_ep_square = (
            None if board.ep_square is None else chess.square_name(board.ep_square)
        )
        pseudo_legal_ep = board.has_pseudo_legal_en_passant()
        legal_ep = board.has_legal_en_passant()
        legal_ep_square = None if fields[3] == "-" else fields[3]
        promoted_squares = [
            chess.square_name(square) for square in chess.scan_forward(board.promoted)
        ]
        move_digest = sha256_bytes((" ".join(record["moves"]) + "\n").encode("ascii"))
        history = {"captures": captures, "drops": drops, "promotions": promotions}

        require(record["moves_sha256"] == move_digest, f"move digest drift at row {index + 1}")
        require(record["canonical_fen"] == fen, f"replay FEN drift at row {index + 1}")
        require(record["physical_identity"] == " ".join(fields[:4]), f"physical identity drift at row {index + 1}")
        require(record["board_and_pocket"] == fields[0], f"board/pocket drift at row {index + 1}")
        require(record["turn"] == ("white" if board.turn else "black"), f"turn drift at row {index + 1}")
        require(record["castling_rights"] == fields[2], f"castling drift at row {index + 1}")
        require(record["raw_ep_square"] == raw_ep_square, f"raw EP drift at row {index + 1}")
        require(
            record["pseudo_legal_en_passant_available"] is pseudo_legal_ep,
            f"pseudo-legal EP drift at row {index + 1}",
        )
        require(record["legal_ep_square"] == legal_ep_square, f"legal EP square drift at row {index + 1}")
        require(
            record["legal_en_passant_available"] is legal_ep,
            f"legal EP availability drift at row {index + 1}",
        )
        require(record["halfmove_clock"] == board.halfmove_clock, f"halfmove drift at row {index + 1}")
        require(record["fullmove_number"] == board.fullmove_number, f"fullmove drift at row {index + 1}")
        require(record["pockets"] == replay_pockets(board), f"pocket drift at row {index + 1}")
        require(record["promoted_squares"] == promoted_squares, f"promoted-state drift at row {index + 1}")
        require(record["in_check"] is board.is_check(), f"check-state drift at row {index + 1}")
        require(record["legal_move_count"] == board.legal_moves.count(), f"legal-count drift at row {index + 1}")
        require(record["history_counts"] == history, f"history-count drift at row {index + 1}")
        require(not board.is_checkmate(), f"checkmate root at row {index + 1}")
        require(not board.is_stalemate(), f"stalemate root at row {index + 1}")
        require(not board.is_fivefold_repetition(), f"fivefold root at row {index + 1}")

        depth_counts[int(record["target_depth"])] += 1
        physical.append(record["physical_identity"])
        board_only.append(record["board_and_pocket"])
        full_fens.append(record["canonical_fen"])
        selection_projection.append(
            [
                record["candidate_index"],
                record["target_depth"],
                record["moves"],
                record["canonical_fen"],
            ]
        )

    require(
        [record["candidate_index"] for record in records] == list(range(1024)),
        "candidate-index sequence drift",
    )

    def has_pocket(record: dict[str, Any]) -> bool:
        return any(
            count for side in record["pockets"].values() for count in side.values()
        )

    coverage = {
        "accepted_roots": len(records),
        "candidate_indices_consumed": 1024,
        "rejected": {},
        "depth_counts": {str(depth): depth_counts[depth] for depth in sorted(depth_counts)},
        "white_to_move": sum(record["turn"] == "white" for record in records),
        "black_to_move": sum(record["turn"] == "black" for record in records),
        "capture_history": sum(record["history_counts"]["captures"] > 0 for record in records),
        "drop_history": sum(record["history_counts"]["drops"] > 0 for record in records),
        "promotion_history": sum(record["history_counts"]["promotions"] > 0 for record in records),
        "final_nonempty_pocket": sum(has_pocket(record) for record in records),
        "promoted_state": sum(bool(record["promoted_squares"]) for record in records),
        "in_check": sum(record["in_check"] for record in records),
        "raw_ep_target_set": sum(record["raw_ep_square"] is not None for record in records),
        "pseudo_legal_en_passant_available": sum(
            record["pseudo_legal_en_passant_available"] for record in records
        ),
        "legal_en_passant_available": sum(
            record["legal_en_passant_available"] for record in records
        ),
        "castling_rights_present": sum(record["castling_rights"] != "-" for record in records),
        "full_fen_unique": len(set(full_fens)),
        "physical_identity_unique": len(set(physical)),
        "board_and_pocket_unique": len(set(board_only)),
    }
    expected = {
        "accepted_roots": 1024,
        "candidate_indices_consumed": 1024,
        "rejected": {},
        "depth_counts": {str(depth): 128 for depth in range(12, 20)},
        "white_to_move": 512,
        "black_to_move": 512,
        "capture_history": 468,
        "drop_history": 294,
        "promotion_history": 1,
        "final_nonempty_pocket": 250,
        "promoted_state": 1,
        "in_check": 16,
        "raw_ep_target_set": 121,
        "pseudo_legal_en_passant_available": 4,
        "legal_en_passant_available": 4,
        "castling_rights_present": 970,
        "full_fen_unique": 1024,
        "physical_identity_unique": 1024,
        "board_and_pocket_unique": 1024,
    }
    require(coverage == expected, "independent replay coverage drift")
    projection = json.dumps(
        selection_projection, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    selection_identity = sha256_bytes(projection)
    require(selection_identity == SELECTION_IDENTITY_SHA256, "selection identity drift")
    return {
        "rows": len(records),
        "coverage": coverage,
        "selection_identity_sha256": selection_identity,
        "loaded_module": module.as_posix(),
        "python_chess_version": chess.__version__,
    }


def parse_epd(path: Path) -> list[str]:
    raw = path.read_bytes()
    require(not raw.startswith(b"\xef\xbb\xbf"), "EPD BOM forbidden")
    require(b"\r" not in raw, "EPD CR bytes forbidden")
    require(raw.endswith(b"\n"), "EPD final LF missing")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise VerificationFailure("EPD is not US-ASCII") from exc
    require(len(lines) == 1024, "EPD row count is not 1024")
    return lines


def expected_epd(record: dict[str, Any]) -> str:
    fields = str(record["canonical_fen"]).split()
    return (
        f"{' '.join(fields[:4])} hmvc {fields[4]}; fmvn {fields[5]}; "
        f'id "{record["id"]}";'
    )


def qualify_referee_rows(
    referee: Path,
    records: list[dict[str, Any]],
    artifact_dir: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    output = artifact_dir / "referee-conformance.jsonl"
    responses: list[bytes] = []
    identity: dict[str, Any] | None = None
    started = time.monotonic()
    for index, record in enumerate(records):
        completed = run_checked(
            [
                str(referee),
                "--crazyhouse-conformance-v1",
                "identity-canonical-fen",
                "--profile-id",
                PROFILE_ID,
                "--profile-sha256",
                PROFILE_SHA256,
                "--fen",
                record["canonical_fen"],
            ],
            cwd=artifact_dir,
            timeout=30,
            env=env,
        )
        require(not completed.stderr, f"referee stderr at row {index + 1}")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationFailure(f"invalid referee JSON at row {index + 1}") from exc
        require(response.get("status") == "ok", f"referee rejected row {index + 1}")
        require(response.get("schema") == "crazyhouse-referee-conformance/v1", "wrong referee schema")
        require(response["profile"] == {"id": PROFILE_ID, "sha256": PROFILE_SHA256}, "wrong referee profile")
        require(response["result"]["variant"] == "crazyhouse", "wrong referee variant")
        require(
            response["result"]["canonical_fen"] == record["canonical_fen"],
            f"referee canonical FEN mismatch at row {index + 1}",
        )
        if identity is None:
            identity = response["identity"]
        else:
            require(response["identity"] == identity, "referee identity drift within corpus")
        responses.append(
            (json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        )
    raw = b"".join(responses)
    output.write_bytes(raw)
    return {
        "rows": len(responses),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "identity": identity,
        "output": {
            "path": output.as_posix(),
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
        },
    }


def parse_pgn_games(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    starts = [match.start() for match in re.finditer(r"(?m)^\[Event ", text)]
    require(len(starts) == 2, "dry PGN does not contain exactly two games")
    starts.append(len(text))
    games: list[dict[str, str]] = []
    for index in range(2):
        block = text[starts[index] : starts[index + 1]]
        headers = dict(re.findall(r'(?m)^\[([^ ]+) "([^"]*)"\]$', block))
        games.append(headers)
    return games


def run_dry_pair(
    referee: Path,
    candidate: Path,
    adapter: Path,
    raw_fairy: Path,
    network: Path,
    book: Path,
    first_fen: str,
    artifact_dir: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    pgn = artifact_dir / "dry-pair.pgn"
    candidate_stderr = artifact_dir / "candidate.stderr.log"
    fairy_stderr = artifact_dir / "fairy-adapted.stderr.log"
    stdout_path = artifact_dir / "dry-pair.stdout.log"
    stderr_path = artifact_dir / "dry-pair.stderr.log"
    command = [
        str(referee),
        "-engine",
        "name=candidate",
        f"cmd={candidate}",
        f"dir={candidate.parent}",
        "restart=off",
        "proto=uci",
        f"stderr={candidate_stderr}",
        f"option.CrazyhouseEvalFile={network}",
        "option.Threads=1",
        "option.Hash=16",
        "-engine",
        "name=fairy-adapted",
        f"cmd={adapter}",
        f"dir={adapter.parent}",
        "restart=off",
        "proto=uci",
        f"stderr={fairy_stderr}",
        "arg=--engine",
        f"arg={raw_fairy}",
        "arg=--network",
        f"arg={network}",
        f"option.CrazyhouseEvalFile={network}",
        "option.Threads=1",
        "option.Hash=16",
        "-each",
        "tc=60+0",
        "nodes=1",
        "timemargin=1000",
        "-variant",
        "crazyhouse",
        "-concurrency",
        "1",
        "-games",
        "2",
        "-rounds",
        "1",
        "-repeat",
        "2",
        "-openings",
        f"file={book}",
        "format=epd",
        "order=sequential",
        "start=1",
        "policy=default",
        "-srand",
        "2653117302",
        "-maxmoves",
        "1",
        "-pgnout",
        str(pgn),
        "-event",
        "Crazyhouse local opening corpus dry pair",
        "-site",
        "local-functional-only",
        "-debug",
    ]
    completed = run_checked(command, cwd=artifact_dir, timeout=180, env=env)
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    require(not completed.stderr, "dry referee emitted stderr")
    require(candidate_stderr.is_file() and not candidate_stderr.read_bytes(), "candidate emitted stderr")
    require(fairy_stderr.is_file() and not fairy_stderr.read_bytes(), "adapted comparator emitted stderr")
    require(pgn.is_file(), "dry PGN missing")

    text = completed.stdout.decode("utf-8", errors="strict").replace("\r\n", "\n")
    require(text.count("Started game ") == 2, "dry pair did not start two games")
    require(text.count("Finished game ") == 2, "dry pair did not finish two games")
    require("disconnect" not in text.lower(), "dry pair contains disconnect")
    require("illegal" not in text.lower(), "dry pair contains illegal")
    require("on time" not in text.lower(), "dry pair contains time loss")
    require("crash" not in text.lower(), "dry pair contains crash")
    require("info string ERROR" not in text, "dry pair contains engine ERROR")

    nonces = ACK_RE.findall(text)
    require(len(nonces) == 4 and len(set(nonces)) == 4, "capability nonce audit failed")
    require(
        text.count(
            "backend=legacy-v1 identity=" + NETWORK_SHA256 + " evaluator=incremental-scalar"
        )
        >= 2,
        "candidate route marker audit failed",
    )
    require(
        text.count(
            "backend=fairy-external identity=" + NETWORK_SHA256 + " evaluator=halfkav2variants"
        )
        >= 2,
        "adapted comparator route marker audit failed",
    )
    require(
        text.count(f"NNUE evaluation using {network} enabled") == 2,
        "adapted comparator NNUE marker audit failed",
    )

    pgn_raw = pgn.read_bytes()
    games = parse_pgn_games(pgn_raw)
    expected_colors = [
        ("candidate", "fairy-adapted"),
        ("fairy-adapted", "candidate"),
    ]
    for index, (headers, colors) in enumerate(zip(games, expected_colors, strict=True)):
        require(headers.get("White") == colors[0], f"white schedule mismatch game {index + 1}")
        require(headers.get("Black") == colors[1], f"black schedule mismatch game {index + 1}")
        require(headers.get("Variant") == "crazyhouse", f"variant mismatch game {index + 1}")
        require(headers.get("FEN") == first_fen, f"opening FEN mismatch game {index + 1}")
        require(headers.get("SetUp") == "1", f"SetUp missing game {index + 1}")
        require(headers.get("Result") == "1/2-1/2", f"dry result mismatch game {index + 1}")
        require(headers.get("Termination") == "adjudication", f"termination mismatch game {index + 1}")

    return {
        "games": 2,
        "colors": [list(colors) for colors in expected_colors],
        "opening_fen": first_fen,
        "unique_capability_nonces": len(set(nonces)),
        "candidate_route": "legacy-v1/incremental-scalar",
        "comparator_route": "fairy-external/halfkav2variants",
        "adapted_nnue_markers": 2,
        "command": command,
        "stdout": {"path": stdout_path.as_posix(), "bytes": len(completed.stdout), "sha256": sha256_bytes(completed.stdout)},
        "stderr": {"path": stderr_path.as_posix(), "bytes": len(completed.stderr), "sha256": sha256_bytes(completed.stderr)},
        "pgn": {"path": pgn.as_posix(), "bytes": len(pgn_raw), "sha256": sha256_bytes(pgn_raw)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--python-chess-root", type=Path, required=True)
    parser.add_argument("--book-a", type=Path, required=True)
    parser.add_argument("--trajectories-a", type=Path, required=True)
    parser.add_argument("--manifest-a", type=Path, required=True)
    parser.add_argument("--book-b", type=Path, required=True)
    parser.add_argument("--trajectories-b", type=Path, required=True)
    parser.add_argument("--manifest-b", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    contract = load_json(args.contract)
    require(contract["schema"] == "crazyhouse-p7-local-opening-qualification/v2", "wrong contract schema")
    paths = {
        name: authenticate_pin(repo, pin)
        for name, pin in contract["inputs"].items()
        if isinstance(pin, dict) and set(("path", "bytes", "sha256")) <= pin.keys()
    }
    require(paths["verifier"] == Path(__file__).resolve(), "verifier path mismatch")
    require(paths["generator"] == resolve_pin(repo, contract["inputs"]["generator"]["path"]), "generator path mismatch")
    require(sha256_file(args.python) == contract["runtime"]["python_executable_sha256"], "Python executable drift")
    require(not args.artifact_dir.exists(), "artifact directory must be absent")
    require(args.artifact_dir.parent.is_dir(), "artifact parent missing")
    require(not args.result.exists(), "result must be absent")
    args.artifact_dir.mkdir()

    for first, second, label in (
        (args.book_a, args.book_b, "book"),
        (args.trajectories_a, args.trajectories_b, "trajectories"),
        (args.manifest_a, args.manifest_b, "manifest"),
    ):
        require(first.read_bytes() == second.read_bytes(), f"independent {label} builds differ")

    generator_a = verify_generator(args.python, paths["generator"], args.python_chess_root, args.book_a, args.trajectories_a, args.manifest_a, repo)
    generator_b = verify_generator(args.python, paths["generator"], args.python_chess_root, args.book_b, args.trajectories_b, args.manifest_b, repo)
    require(generator_a == generator_b, "generator summaries differ")

    records = parse_trajectories(args.trajectories_a)
    replay = replay_trajectories(records, args.python_chess_root)
    epd_lines = parse_epd(args.book_a)
    for index, (record, line) in enumerate(zip(records, epd_lines, strict=True)):
        require(line == expected_epd(record), f"EPD/trajectory mismatch at row {index + 1}")
    manifest = load_json(args.manifest_a)
    require(manifest["schema"] == CORPUS_SCHEMA, "manifest corpus schema mismatch")
    require(manifest["artifact_id"] == "CH-LOCAL-GATE-OPENINGS-V2", "manifest artifact ID mismatch")
    require(manifest["outputs"]["book"]["sha256"] == sha256_file(args.book_a), "manifest book pin mismatch")
    require(manifest["outputs"]["trajectories"]["sha256"] == sha256_file(args.trajectories_a), "manifest trajectory pin mismatch")
    require(manifest["coverage_observed"] == replay["coverage"], "manifest/replay coverage mismatch")

    qt_bin = Path(contract["runtime"]["qt_bin"]).resolve()
    qt_plugins = Path(contract["runtime"]["qt_plugins"]).resolve()
    qt_platforms = Path(contract["runtime"]["qt_platforms"]).resolve()
    require(qt_bin.is_dir(), "Qt runtime bin directory missing")
    require(qt_plugins.is_dir(), "Qt plugin directory missing")
    require(qt_platforms.is_dir(), "Qt platform directory missing")
    env = os.environ.copy()
    env["PATH"] = str(qt_bin) + os.pathsep + env.get("PATH", "")
    env["QT_PLUGIN_PATH"] = str(qt_plugins)
    env["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(qt_platforms)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["TZ"] = "UTC"
    referee_rows = qualify_referee_rows(paths["referee"], records, args.artifact_dir, env)
    dry_pair = run_dry_pair(
        paths["referee"],
        paths["candidate"],
        paths["adapter"],
        paths["raw_fairy"],
        paths["network"],
        args.book_a,
        records[0]["canonical_fen"],
        args.artifact_dir,
        env,
    )

    result = {
        "schema": "crazyhouse-p7-local-opening-qualification-result/v1",
        "status": "PASS_LOCAL_OPENING_CORPUS_QUALIFICATION",
        "contract": {
            "path": args.contract.as_posix(),
            "bytes": args.contract.stat().st_size,
            "sha256": sha256_file(args.contract),
        },
        "independent_generation": {
            "byte_identical": True,
            "generator_summary": generator_a,
        },
        "corpus": {
            "book": {"path": args.book_a.as_posix(), "bytes": args.book_a.stat().st_size, "sha256": sha256_file(args.book_a)},
            "trajectories": {"path": args.trajectories_a.as_posix(), "bytes": args.trajectories_a.stat().st_size, "sha256": sha256_file(args.trajectories_a)},
            "manifest": {"path": args.manifest_a.as_posix(), "bytes": args.manifest_a.stat().st_size, "sha256": sha256_file(args.manifest_a)},
            "rows": len(records),
            "epd_trajectory_exact": True,
        },
        "independent_trajectory_replay": replay,
        "referee_conformance": referee_rows,
        "dry_pair": dry_pair,
        "scientific_boundary": {
            "fixed_nodes": 1,
            "maxmoves": 1,
            "timing_claim": False,
            "elo_claim": False,
            "strength_claim": False,
            "openbench_claim": False,
            "release_claim": False,
        },
    }
    args.result.write_bytes((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"result": "PASS", "rows": len(records), "dry_games": 2}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
