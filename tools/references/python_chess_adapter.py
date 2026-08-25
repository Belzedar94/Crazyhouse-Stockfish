#!/usr/bin/env python3
"""Pinned python-chess Crazyhouse adapter for the shared JSONL protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, TextIO

import chess
import chess.variant


REQUEST_SCHEMA = "crazyhouse-reference-request/v1"
RESPONSE_SCHEMA = "crazyhouse-reference-response/v1"
AUTHORITY_PROFILE = "LICHESS_CRAZYHOUSE_2026_08_12"
EXPECTED_COMMIT = "9c24454dcea4f8a30259d811a2f10b26e911deb4"
EXPECTED_TREE = "33627273cd58c1a5a20c3132548e5df7b85ff9d6"
POCKET_ORDER = "PNBRQpnbrq"


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError("IDENTITY_UNAVAILABLE", f"git identity probe failed: {exc}") from exc
    if completed.returncode:
        raise AdapterError("IDENTITY_UNAVAILABLE", completed.stderr.strip() or "git identity probe failed")
    return completed.stdout.strip()


def authenticate_checkout(root: Path) -> dict[str, Any]:
    root = root.resolve()
    module_path = Path(chess.__file__).resolve()
    try:
        module_path.relative_to(root)
    except ValueError as exc:
        raise AdapterError(
            "WRONG_MODULE_ROOT",
            f"loaded chess module {module_path} is outside required root {root}",
        ) from exc

    commit = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    dirty = git(root, "status", "--porcelain")
    if commit != EXPECTED_COMMIT or tree != EXPECTED_TREE:
        raise AdapterError(
            "WRONG_REFERENCE_IDENTITY",
            f"expected {EXPECTED_COMMIT}/{EXPECTED_TREE}, got {commit}/{tree}",
        )
    if dirty:
        raise AdapterError("DIRTY_REFERENCE", "python-chess checkout is not clean")

    return {
        "name": "python-chess",
        "version": chess.__version__,
        "commit": commit,
        "tree": tree,
        "module_path": str(module_path),
        "module_sha256": sha256(module_path),
        "license": "GPL-3.0-or-later",
        "role": "independent_differential_reference",
        "result_authority": False,
    }


def canonicalize_fen(fen: str) -> str:
    fields = fen.strip().split()
    if len(fields) != 6:
        raise AdapterError("INVALID_FEN", f"expected six FEN fields, got {len(fields)}")

    board_and_pocket = fields[0]
    pocket = ""
    if board_and_pocket.endswith("]") and "[" in board_and_pocket:
        board, pocket = board_and_pocket[:-1].rsplit("[", 1)
    elif board_and_pocket.count("/") == 8:
        ranks = board_and_pocket.split("/")
        board, pocket = "/".join(ranks[:8]), ranks[8]
    elif board_and_pocket.count("/") == 7:
        board = board_and_pocket
    else:
        raise AdapterError("INVALID_FEN", "Crazyhouse board field has neither eight ranks nor a pocket field")

    if board.count("/") != 7:
        raise AdapterError("INVALID_FEN", "Crazyhouse board field does not contain eight ranks")
    if any(char not in POCKET_ORDER for char in pocket):
        raise AdapterError("INVALID_FEN", "pocket contains an unsupported piece symbol")
    ordered_pocket = "".join(symbol * pocket.count(symbol) for symbol in POCKET_ORDER)
    fields[0] = f"{board}[{ordered_pocket}]"
    return " ".join(fields)


def physical_state(canonical_fen: str) -> dict[str, Any]:
    board_and_pocket, turn, castling, ep_square, halfmove, fullmove = canonical_fen.split()
    board, pocket = board_and_pocket[:-1].rsplit("[", 1)
    promoted: list[str] = []
    for rank_index, rank in enumerate(board.split("/")):
        file_index = 0
        previous_square: str | None = None
        for char in rank:
            if char.isdigit():
                file_index += int(char)
                previous_square = None
            elif char == "~":
                if previous_square is None:
                    raise AdapterError("INVALID_FEN", "promoted marker is not attached to a board piece")
                promoted.append(previous_square)
            else:
                if file_index >= 8:
                    raise AdapterError("INVALID_FEN", "rank exceeds eight files")
                previous_square = f"{chr(ord('a') + file_index)}{8 - rank_index}"
                file_index += 1
        if file_index != 8:
            raise AdapterError("INVALID_FEN", "rank does not contain eight files")

    role_symbols = {"pawn": "P", "knight": "N", "bishop": "B", "rook": "R", "queen": "Q"}
    pockets = {
        "white": {role: pocket.count(symbol) for role, symbol in role_symbols.items()},
        "black": {role: pocket.count(symbol.lower()) for role, symbol in role_symbols.items()},
    }
    return {
        "canonical_fen": canonical_fen,
        "turn": "white" if turn == "w" else "black",
        "castling_rights": castling,
        "ep_square": None if ep_square == "-" else ep_square,
        "halfmove_clock": int(halfmove),
        "fullmove_number": int(fullmove),
        "pockets": pockets,
        "promoted_squares": sorted(promoted),
    }


def authority_terminal(board: chess.variant.CrazyhouseBoard) -> dict[str, Any]:
    if board.is_checkmate():
        winner = "black" if board.turn == chess.WHITE else "white"
        return {"ended": True, "reason": "checkmate", "winner": winner, "result": "0-1" if winner == "black" else "1-0"}
    if board.is_stalemate():
        return {"ended": True, "reason": "stalemate", "winner": None, "result": "1/2-1/2"}
    if board.is_fivefold_repetition():
        return {"ended": True, "reason": "fivefold_repetition", "winner": None, "result": "1/2-1/2"}
    return {"ended": False, "reason": "ongoing", "winner": None, "result": "*"}


def native_diagnostics(board: chess.variant.CrazyhouseBoard) -> dict[str, Any]:
    outcome = board.outcome(claim_draw=False)
    return {
        "is_insufficient_material": board.is_insufficient_material(),
        "white_has_insufficient_material": board.has_insufficient_material(chess.WHITE),
        "black_has_insufficient_material": board.has_insufficient_material(chess.BLACK),
        "is_seventyfive_moves": board.is_seventyfive_moves(),
        "can_claim_fifty_moves": board.can_claim_fifty_moves(),
        "is_fivefold_repetition": board.is_fivefold_repetition(),
        "can_claim_threefold_repetition": board.can_claim_threefold_repetition(),
        "outcome_termination": outcome.termination.name.lower() if outcome else None,
        "note": "native insufficient-material outcome is diagnostic only and is not Lichess Crazyhouse authority",
    }


def legal_moves(board: chess.variant.CrazyhouseBoard) -> list[str]:
    return sorted(move.uci() for move in board.legal_moves)


def describe(board: chess.variant.CrazyhouseBoard) -> dict[str, Any]:
    canonical_fen = canonicalize_fen(board.fen(promoted=True, en_passant="legal"))
    state = physical_state(canonical_fen)
    state.update(
        {
            "in_check": board.is_check(),
            "legal_moves": legal_moves(board),
            "terminal": authority_terminal(board),
            "native_diagnostics": native_diagnostics(board),
        }
    )
    return state


def parse_board(fen: str) -> chess.variant.CrazyhouseBoard:
    try:
        board = chess.variant.CrazyhouseBoard(fen)
    except (TypeError, ValueError) as exc:
        raise AdapterError("INVALID_FEN", str(exc)) from exc
    status = board.status()
    if status != chess.STATUS_VALID:
        raise AdapterError("INVALID_POSITION", f"python-chess status bitmask is {status}")
    return board


def play_moves(board: chess.variant.CrazyhouseBoard, moves: list[Any]) -> None:
    for index, raw_move in enumerate(moves):
        if not isinstance(raw_move, str):
            raise AdapterError("INVALID_REQUEST", f"moves[{index}] is not a string")
        try:
            move = chess.Move.from_uci(raw_move)
        except ValueError as exc:
            raise AdapterError("INVALID_UCI", f"moves[{index}] {raw_move!r}: {exc}") from exc
        if move not in board.legal_moves:
            raise AdapterError("ILLEGAL_MOVE", f"moves[{index}] {raw_move!r} is illegal")
        board.push(move)


def perft(board: chess.variant.CrazyhouseBoard, depth: int) -> int:
    if depth == 0:
        return 1
    nodes = 0
    for move in list(board.legal_moves):
        board.push(move)
        nodes += perft(board, depth - 1)
        board.pop()
    return nodes


def require_request(request: Any) -> tuple[str, str]:
    if not isinstance(request, dict):
        raise AdapterError("INVALID_REQUEST", "request must be a JSON object")
    if request.get("schema") != REQUEST_SCHEMA:
        raise AdapterError("INVALID_SCHEMA", f"expected {REQUEST_SCHEMA}")
    if request.get("authority_profile") != AUTHORITY_PROFILE:
        raise AdapterError("INVALID_PROFILE", f"expected {AUTHORITY_PROFILE}")
    request_id = request.get("id")
    op = request.get("op")
    if not isinstance(request_id, str) or not request_id:
        raise AdapterError("INVALID_REQUEST", "id must be a nonempty string")
    if not isinstance(op, str):
        raise AdapterError("INVALID_REQUEST", "op must be a string")
    return request_id, op


def execute(request: Any, identity: dict[str, Any]) -> dict[str, Any]:
    request_id, op = require_request(request)
    base = {"schema": RESPONSE_SCHEMA, "authority_profile": AUTHORITY_PROFILE, "id": request_id, "implementation": identity}
    if op == "capabilities":
        return {
            **base,
            "ok": True,
            "capabilities": {
                "operations": ["capabilities", "inspect", "transition", "perft"],
                "fen_input": ["bracket_pocket", "slash_pocket"],
                "fen_output": "bracket_pocket_PNBRQpnbrq_legal_ep",
                "history": "move_sequence_from_request",
                "native_result_authority": False,
            },
        }

    fen = request.get("fen")
    if not isinstance(fen, str):
        raise AdapterError("INVALID_REQUEST", "fen must be a string")
    board = parse_board(fen)

    if op == "inspect":
        return {**base, "ok": True, "state": describe(board)}
    if op == "transition":
        moves = request.get("moves")
        if not isinstance(moves, list):
            raise AdapterError("INVALID_REQUEST", "transition moves must be an array")
        initial = board.copy(stack=True)
        play_moves(board, moves)
        final_state = describe(board)
        while board.move_stack:
            board.pop()
        undo_restored = (
            canonicalize_fen(board.fen(promoted=True, en_passant="legal"))
            == canonicalize_fen(initial.fen(promoted=True, en_passant="legal"))
            and board._transposition_key() == initial._transposition_key()
        )
        if not undo_restored:
            raise AdapterError("UNDO_MISMATCH", "push/pop did not restore physical state and transposition key")
        return {**base, "ok": True, "move_count": len(moves), "undo_restored": True, "state": final_state}
    if op == "perft":
        depth = request.get("depth")
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0 or depth > 6:
            raise AdapterError("INVALID_REQUEST", "depth must be an integer from 0 through 6")
        before = canonicalize_fen(board.fen(promoted=True, en_passant="legal"))
        nodes = perft(board, depth)
        after = canonicalize_fen(board.fen(promoted=True, en_passant="legal"))
        if before != after or board.move_stack:
            raise AdapterError("UNDO_MISMATCH", "perft did not restore the root position")
        return {**base, "ok": True, "depth": depth, "nodes": nodes, "root": describe(board)}
    raise AdapterError("UNSUPPORTED_OPERATION", f"unsupported operation {op!r}")


def open_stream(path: str, mode: str) -> TextIO:
    if path == "-":
        return sys.stdin if "r" in mode else sys.stdout
    return Path(path).open(mode, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-root", type=Path, required=True)
    parser.add_argument("--input", default="-")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    try:
        identity = authenticate_checkout(args.require_root)
    except AdapterError as exc:
        print(f"FATAL {exc.code}: {exc}", file=sys.stderr)
        return 2

    failed = False
    try:
        input_stream = open_stream(args.input, "r")
        output_stream = open_stream(args.output, "x")
    except OSError as exc:
        print(f"FATAL IO_SETUP_FAILURE: {exc}", file=sys.stderr)
        return 2
    try:
        for line_number, line in enumerate(input_stream, 1):
            if line_number == 1:
                # Windows PowerShell can expose a UTF-8 BOM through a legacy
                # stdin codec as either U+FEFF or the three decoded bytes.
                line = line.lstrip("\ufeff\xef\xbb\xbf")
            if not line.strip():
                continue
            request_id: str | None = None
            try:
                request = json.loads(line)
                if isinstance(request, dict) and isinstance(request.get("id"), str):
                    request_id = request["id"]
                response = execute(request, identity)
            except json.JSONDecodeError as exc:
                failed = True
                response = {
                    "schema": RESPONSE_SCHEMA,
                    "authority_profile": AUTHORITY_PROFILE,
                    "id": request_id,
                    "implementation": identity,
                    "ok": False,
                    "error": {"code": "INVALID_JSON", "message": f"line {line_number}: {exc.msg}"},
                }
            except AdapterError as exc:
                failed = True
                response = {
                    "schema": RESPONSE_SCHEMA,
                    "authority_profile": AUTHORITY_PROFILE,
                    "id": request_id,
                    "implementation": identity,
                    "ok": False,
                    "error": {"code": exc.code, "message": str(exc)},
                }
            output_stream.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
            output_stream.flush()
    finally:
        if input_stream is not sys.stdin:
            input_stream.close()
        if output_stream is not sys.stdout:
            output_stream.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
