#!/usr/bin/env python3
"""Unit and adversarial checks for the Crazyhouse physical V1 format."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "datagen" / "crazyhouse_physical_v1.py"
SPEC = importlib.util.spec_from_file_location("crazyhouse_physical_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
codec = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = codec
SPEC.loader.exec_module(codec)


CAMPAIGN_ID = codec.uuid_bytes("10000000-0000-4000-8000-000000000001")
CHUNK_ID = codec.uuid_bytes("20000000-0000-4000-8000-000000000001")
DATA_ROOT = ROOT / "tests" / "crazyhouse" / "data"
PROVENANCE_PATH = DATA_ROOT / "crazyhouse-physical-v1-golden-provenance.json"
CAPABILITY_RESPONSE_PATH = DATA_ROOT / "crazyhouse-physical-v1-golden-capability-response.json"
GOLDEN_MANIFEST_PATH = DATA_ROOT / "crazyhouse-physical-v1-goldens.json"
CAPABILITY_SHA = hashlib.sha256(CAPABILITY_RESPONSE_PATH.read_bytes()).digest()


def provenance_document() -> dict[str, object]:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


def provenance_bytes() -> bytes:
    return PROVENANCE_PATH.read_bytes()


def record(
    sequence: int,
    suffix: int,
    ply: int,
    fen: str,
    move: codec.MoveWire,
    *,
    white_result: int,
    previous_history: bytes | None,
    terminal_reason: int = codec.TERMINAL_ONGOING,
    repetition: int = 1,
    claim_policy: int = codec.CLAIM_CORE_ONLY,
    nonstandard: bool = True,
    effective_ep: int = codec.NO_SQUARE,
    teacher_score: int = 0,
) -> codec.PhysicalRecord:
    terminal = terminal_reason != codec.TERMINAL_ONGOING
    return codec.build_record(
        sequence=sequence,
        game_id=codec.uuid_bytes(f"30000000-0000-4000-8000-{suffix:012d}"),
        trajectory_id=codec.uuid_bytes(f"40000000-0000-4000-8000-{suffix:012d}"),
        ply=ply,
        fen=fen,
        effective_en_passant_square=effective_ep,
        repetition_occurrences=repetition,
        claim_policy=claim_policy,
        terminal_reason=terminal_reason,
        move=move,
        game_result_white=white_result,
        provenance_sha256=hashlib.sha256(provenance_bytes()).digest(),
        previous_history_sha256=previous_history,
        teacher_score_kind=codec.TEACHER_NONE if terminal else codec.TEACHER_CENTIPAWN,
        teacher_score_value=0 if terminal else teacher_score,
        teacher_bound=codec.BOUND_NONE if terminal else codec.BOUND_EXACT,
        search_nodes=0 if terminal else 1024,
        search_depth=0 if terminal else 8,
        search_seldepth=0 if terminal else 10,
        move_time_ms=0 if terminal else 5,
        teacher_used_network=False,
        nonstandard_root=nonstandard,
    )


def golden_records() -> list[codec.PhysicalRecord]:
    records: list[codec.PhysicalRecord] = []

    def add(
        suffix: int,
        ply: int,
        fen: str,
        move: codec.MoveWire,
        *,
        white_result: int,
        terminal_reason: int = codec.TERMINAL_ONGOING,
        repetition: int = 1,
        claim_policy: int = codec.CLAIM_CORE_ONLY,
        nonstandard: bool = True,
        effective_ep: int = codec.NO_SQUARE,
        teacher_score: int = 0,
    ) -> codec.PhysicalRecord:
        previous = None
        if ply:
            self_records = [item for item in records if item.trajectory_id == codec.uuid_bytes(f"40000000-0000-4000-8000-{suffix:012d}")]
            if not self_records:
                raise AssertionError("golden trajectory predecessor is missing")
            previous = self_records[-1].history_prefix_sha256
        built = record(
            len(records),
            suffix,
            ply,
            fen,
            move,
            white_result=white_result,
            previous_history=previous,
            terminal_reason=terminal_reason,
            repetition=repetition,
            claim_policy=claim_policy,
            nonstandard=nonstandard,
            effective_ep=effective_ep,
            teacher_score=teacher_score,
        )
        records.append(built)
        return built

    add(
        1,
        0,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR[] w KQkq - 0 1",
        codec.parse_move("e2e4"),
        white_result=1,
        nonstandard=False,
        teacher_score=24,
    )
    add(
        1,
        1,
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR[] b KQkq e3 0 1",
        codec.MoveWire.none(),
        white_result=1,
        terminal_reason=codec.TERMINAL_RESIGNATION,
        nonstandard=False,
    )
    add(2, 0, "k7/2Q5/2K5/8/8/8/8/8[n] b - - 0 1", codec.parse_move("N@a1"), white_result=1, teacher_score=-24)
    add(2, 1, "k7/2Q5/2K5/8/8/8/8/n7[] w - - 1 2", codec.MoveWire.none(), white_result=1, terminal_reason=codec.TERMINAL_RESIGNATION)
    add(3, 0, "7k/8/8/8/8/8/Q~7/K7[] w - - 0 1", codec.parse_move("a2b2"), white_result=1, teacher_score=24)
    add(3, 1, "7k/8/8/8/8/8/1Q~6/K7[] b - - 1 1", codec.MoveWire.none(), white_result=1, terminal_reason=codec.TERMINAL_RESIGNATION)
    add(4, 0, "k7/1Q6/2K5/8/8/8/8/8[] b - - 0 1", codec.MoveWire.none(), white_result=1, terminal_reason=codec.TERMINAL_CHECKMATE)
    add(5, 0, "k7/2Q5/2K5/8/8/8/8/8[] b - - 0 1", codec.MoveWire.none(), white_result=0, terminal_reason=codec.TERMINAL_STALEMATE)
    add(6, 0, "4k3/8/8/3pP3/8/8/8/4K3[] w - d6 0 2", codec.parse_move("e5d6", kind="en_passant"), white_result=1, effective_ep=codec.parse_square("d6"), teacher_score=24)
    add(6, 1, "4k3/8/3P4/8/8/8/8/4K3[P] b - - 0 2", codec.MoveWire.none(), white_result=1, terminal_reason=codec.TERMINAL_RESIGNATION)
    add(7, 0, "r3k2r/8/8/8/8/8/8/R3K2R[] w KQkq - 7 12", codec.parse_move("e1g1", kind="castling"), white_result=1, teacher_score=24)
    add(7, 1, "r3k2r/8/8/8/8/8/8/R4RK1[] b kq - 8 12", codec.MoveWire.none(), white_result=1, terminal_reason=codec.TERMINAL_RESIGNATION)
    add(8, 0, "7k/P7/8/8/8/8/8/4K3[] w - - 0 12", codec.parse_move("a7a8n"), white_result=1, teacher_score=24)
    add(8, 1, "N~6k/8/8/8/8/8/8/4K3[] b - - 0 12", codec.MoveWire.none(), white_result=1, terminal_reason=codec.TERMINAL_RESIGNATION)
    add(9, 0, "r6k/8/8/8/8/8/Q~7/K7[] b - - 0 1", codec.parse_move("a8a2"), white_result=-1, teacher_score=24)
    add(9, 1, "7k/8/8/8/8/8/r7/K7[p] w - - 0 2", codec.MoveWire.none(), white_result=-1, terminal_reason=codec.TERMINAL_RESIGNATION)

    repetition_fens = (
        "7k/8/8/8/8/8/8/K7[] w - - {halfmove} {fullmove}",
        "7k/8/8/8/8/8/8/1K6[] b - - {halfmove} {fullmove}",
        "6k1/8/8/8/8/8/8/1K6[] w - - {halfmove} {fullmove}",
        "6k1/8/8/8/8/8/8/K7[] b - - {halfmove} {fullmove}",
    )
    repetition_moves = (
        codec.parse_move("a1b1"),
        codec.parse_move("h8g8"),
        codec.parse_move("b1a1"),
        codec.parse_move("g8h8"),
    )

    def add_repetition_trajectory(suffix: int, cycles: int, terminal_reason: int, claim_policy: int) -> None:
        final_ply = cycles * 4
        for ply in range(final_ply + 1):
            state_index = ply % 4
            terminal = ply == final_ply
            add(
                suffix,
                ply,
                repetition_fens[state_index].format(halfmove=ply, fullmove=ply // 2 + 1),
                codec.MoveWire.none() if terminal else repetition_moves[state_index],
                white_result=0,
                terminal_reason=terminal_reason if terminal else codec.TERMINAL_ONGOING,
                repetition=ply // 4 + 1,
                claim_policy=claim_policy,
                teacher_score=0,
            )

    add_repetition_trajectory(10, 4, codec.TERMINAL_FIVEFOLD, codec.CLAIM_CORE_ONLY)
    add_repetition_trajectory(11, 2, codec.TERMINAL_THREEFOLD_PROXY, codec.CLAIM_IMMEDIATE_THREEFOLD)
    return records


class CrazyhousePhysicalV1Tests(unittest.TestCase):
    def test_schema_is_canonical_and_layout_is_exact(self) -> None:
        payload, document = codec.load_schema()
        self.assertEqual(document["schema_id"], codec.SCHEMA_ID)
        self.assertEqual(document["file_layout"]["record_size"], 256)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), "c72a1fac41e311ed09a2167c56887d64b18293149291f6505f4021f348c1ef55")

    def test_crc32c_known_vector(self) -> None:
        self.assertEqual(codec.crc32c(b"123456789"), 0xE3069283)

    def test_capability_response_and_manifest_bind_exact_bytes(self) -> None:
        contract_path = ROOT / "tests" / "crazyhouse" / "datagen-capability-v1.json"
        response = codec.validate_capability_response_bytes(
            CAPABILITY_RESPONSE_PATH.read_bytes(),
            contract_bytes=contract_path.read_bytes(),
            expected_challenge="0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(response["artifact_role"], "schema-golden-reference-codec")
        self.assertFalse(response["production_generation_authorized"])
        manifest = json.loads(GOLDEN_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["chunk"]["record_count"], 42)
        for pin in manifest["inputs"].values():
            path = ROOT / pin["path"]
            payload = path.read_bytes()
            self.assertEqual(len(payload), pin["bytes"], pin["path"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), pin["sha256"], pin["path"])
        schema_bytes, _ = codec.load_schema()
        records = golden_records()
        chunk = codec.build_chunk(
            records,
            schema_bytes=schema_bytes,
            provenance_bytes=provenance_bytes(),
            producer_capability_sha256=CAPABILITY_SHA,
            chunk_id=CHUNK_ID,
            campaign_id=CAMPAIGN_ID,
        )
        expected = manifest["expected"]
        self.assertEqual(
            [hashlib.sha256(codec.encode_record(item)).hexdigest() for item in records],
            expected["record_sha256"],
        )
        self.assertEqual(hashlib.sha256(chunk[: codec.HEADER_SIZE]).hexdigest(), expected["header_sha256"])
        self.assertEqual(hashlib.sha256(chunk[codec.HEADER_SIZE : -codec.FOOTER_SIZE]).hexdigest(), expected["payload_sha256"])
        self.assertEqual(hashlib.sha256(chunk[-codec.FOOTER_SIZE :]).hexdigest(), expected["footer_sha256"])
        self.assertEqual(hashlib.sha256(chunk).hexdigest(), expected["chunk_sha256"])

    def test_fen_keeps_pockets_promoted_and_raw_ep_separate(self) -> None:
        state = codec.parse_fen("7k/8/8/8/8/8/Q~7/K7[Pnr] b - e3 17 42")
        self.assertEqual(state.promoted_mask, 1 << codec.parse_square("a2"))
        self.assertEqual(state.pockets, (1, 0, 0, 0, 0, 0, 1, 0, 1, 0))
        self.assertEqual(state.raw_en_passant_square, codec.parse_square("e3"))
        self.assertEqual((state.halfmove_clock, state.fullmove_number), (17, 42))

    def test_move_wire_is_not_engine_abi(self) -> None:
        self.assertEqual(codec.parse_move("N@e4"), codec.MoveWire(codec.MOVE_DROP, 255, codec.parse_square("e4"), codec.PIECE_KNIGHT))
        self.assertEqual(codec.parse_move("a7a8q").kind, codec.MOVE_PROMOTION)
        self.assertEqual(codec.parse_move("e1g1", kind="castling").kind, codec.MOVE_CASTLING)
        self.assertEqual(codec.parse_move("e5d6", kind="en_passant").kind, codec.MOVE_EN_PASSANT)

    def test_golden_records_round_trip_byte_exact(self) -> None:
        for original in golden_records():
            encoded = codec.encode_record(original)
            self.assertEqual(len(encoded), 256)
            self.assertEqual(codec.encode_record(codec.decode_record(encoded)), encoded)

    def test_label_perspective_examples(self) -> None:
        records = golden_records()
        self.assertEqual((records[0].game_result_white, records[0].result_side_to_move), (1, 1))
        self.assertEqual((records[1].game_result_white, records[1].result_side_to_move), (1, -1))
        self.assertEqual((records[7].game_result_white, records[7].result_side_to_move), (0, 0))

    def test_promoted_provenance_is_physical(self) -> None:
        promoted = golden_records()[4]
        self.assertTrue(promoted.promoted_mask & (1 << codec.parse_square("a2")))
        identity_without_promoted_flag = codec.position_identity(
            promoted.board,
            promoted.side_to_move,
            promoted.castling_rights,
            promoted.effective_en_passant_square,
            promoted.pockets,
            0,
        )
        self.assertNotEqual(promoted.position_identity_sha256, identity_without_promoted_flag)
        with self.assertRaisesRegex(codec.FormatError, "position identity"):
            codec.validate_record(replace(promoted, promoted_mask=0))

    def test_effective_ep_can_be_none_while_raw_target_is_present(self) -> None:
        ep = record(
            0,
            8,
            0,
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR[] b KQkq e3 0 1",
            codec.parse_move("c7c5"),
            white_result=0,
            previous_history=None,
            nonstandard=False,
            effective_ep=codec.NO_SQUARE,
        )
        decoded = codec.decode_record(codec.encode_record(ep))
        self.assertEqual(decoded.raw_en_passant_square, codec.parse_square("e3"))
        self.assertEqual(decoded.effective_en_passant_square, codec.NO_SQUARE)

    def test_rank_color_symmetry_is_involutive_and_label_safe(self) -> None:
        original = golden_records()[2]
        reflected = codec.reflect_rank_color_swap(original)
        restored = codec.reflect_rank_color_swap(reflected)
        self.assertEqual(restored, original)
        self.assertEqual(reflected.result_side_to_move, original.result_side_to_move)
        self.assertEqual(reflected.game_result_white, -original.game_result_white)
        self.assertEqual(reflected.pockets[:5], original.pockets[5:])

    def test_complete_chunk_round_trip_and_header_offsets(self) -> None:
        schema_bytes, _ = codec.load_schema()
        records = golden_records()
        chunk = codec.build_chunk(
            records,
            schema_bytes=schema_bytes,
            provenance_bytes=provenance_bytes(),
            producer_capability_sha256=CAPABILITY_SHA,
            chunk_id=CHUNK_ID,
            campaign_id=CAMPAIGN_ID,
        )
        self.assertEqual(len(chunk), 256 + len(records) * 256 + 128)
        self.assertEqual(struct.unpack_from("<I", chunk, 32)[0], codec.COMMITTED)
        self.assertEqual(chunk[36:40], bytes(4))
        self.assertEqual(struct.unpack_from("<Q", chunk, 40)[0], len(records))
        parsed = codec.parse_chunk(chunk, schema_bytes=schema_bytes, provenance_bytes=provenance_bytes())
        self.assertEqual(parsed.records, tuple(records))

    def test_partial_truncated_or_appended_chunk_fails(self) -> None:
        schema_bytes, _ = codec.load_schema()
        chunk = codec.build_chunk(
            golden_records(),
            schema_bytes=schema_bytes,
            provenance_bytes=provenance_bytes(),
            producer_capability_sha256=CAPABILITY_SHA,
            chunk_id=CHUNK_ID,
            campaign_id=CAMPAIGN_ID,
        )
        for broken in (chunk[:-1], chunk + b"x", chunk[: codec.HEADER_SIZE + 17]):
            with self.assertRaises(codec.FormatError):
                codec.parse_chunk(broken, schema_bytes=schema_bytes, provenance_bytes=provenance_bytes())

    def test_record_header_and_footer_corruption_fail_closed(self) -> None:
        schema_bytes, _ = codec.load_schema()
        chunk = bytearray(
            codec.build_chunk(
                golden_records(),
                schema_bytes=schema_bytes,
                provenance_bytes=provenance_bytes(),
                producer_capability_sha256=CAPABILITY_SHA,
                chunk_id=CHUNK_ID,
                campaign_id=CAMPAIGN_ID,
            )
        )
        for offset in (0, codec.HEADER_SIZE + 60, len(chunk) - 1):
            broken = bytearray(chunk)
            broken[offset] ^= 1
            with self.assertRaises(codec.FormatError):
                codec.parse_chunk(bytes(broken), schema_bytes=schema_bytes, provenance_bytes=provenance_bytes())

    def test_unknown_flag_and_reserved_piece_fail(self) -> None:
        original = golden_records()[0]
        with self.assertRaisesRegex(codec.FormatError, "unknown record flag"):
            codec.encode_record(replace(original, flags=original.flags | (1 << 31)))
        board = list(original.board)
        board[0] = 7
        with self.assertRaisesRegex(codec.FormatError, "reserved|invalid piece"):
            codec.encode_record(replace(original, board=tuple(board)))

    def test_physical_move_and_special_state_contradictions_fail(self) -> None:
        original = golden_records()[0]
        board = list(original.board)
        board[codec.parse_square("a2")] = codec.PIECE_NONE
        board[codec.parse_square("a1")] = codec.PIECE_PAWN
        with self.assertRaisesRegex(codec.FormatError, "promotion rank"):
            codec.encode_record(replace(original, board=tuple(board)))
        board = list(original.board)
        board[codec.parse_square("h1")] = codec.PIECE_NONE
        with self.assertRaisesRegex(codec.FormatError, "eligible rook"):
            codec.encode_record(replace(original, board=tuple(board)))
        with self.assertRaisesRegex(codec.FormatError, "forbidden rank"):
            codec.parse_move("P@a1")
        drop = golden_records()[2]
        with self.assertRaisesRegex(codec.FormatError, "absent from side-to-move pocket"):
            codec.encode_record(replace(drop, pockets=(0,) * 10))
        ep = golden_records()[8]
        with self.assertRaisesRegex(codec.FormatError, "wrong rank"):
            codec.encode_record(
                replace(
                    ep,
                    raw_en_passant_square=codec.parse_square("d3"),
                    effective_en_passant_square=codec.NO_SQUARE,
                )
            )

    def test_chunk_history_and_teacher_provenance_contradictions_fail(self) -> None:
        schema_bytes, _ = codec.load_schema()
        records = golden_records()
        self.assertEqual(records[16].repetition_occurrences, 1)
        self.assertEqual(records[32].repetition_occurrences, 5)
        self.assertEqual(records[41].repetition_occurrences, 3)
        hidden_history = list(records)
        hidden_history[16] = replace(hidden_history[16], repetition_occurrences=2)
        with self.assertRaisesRegex(codec.FormatError, "hidden repetition history"):
            codec.build_chunk(
                hidden_history,
                schema_bytes=schema_bytes,
                provenance_bytes=provenance_bytes(),
                producer_capability_sha256=CAPABILITY_SHA,
                chunk_id=CHUNK_ID,
                campaign_id=CAMPAIGN_ID,
            )
        wrong_teacher = list(records)
        wrong_teacher[0] = replace(wrong_teacher[0], flags=wrong_teacher[0].flags | codec.FLAG_TEACHER_NETWORK)
        with self.assertRaisesRegex(codec.FormatError, "teacher/network provenance"):
            codec.build_chunk(
                wrong_teacher,
                schema_bytes=schema_bytes,
                provenance_bytes=provenance_bytes(),
                producer_capability_sha256=CAPABILITY_SHA,
                chunk_id=CHUNK_ID,
                campaign_id=CAMPAIGN_ID,
            )
        with self.assertRaisesRegex(codec.FormatError, "without a terminal record"):
            codec.build_chunk(
                records[:1],
                schema_bytes=schema_bytes,
                provenance_bytes=provenance_bytes(),
                producer_capability_sha256=CAPABILITY_SHA,
                chunk_id=CHUNK_ID,
                campaign_id=CAMPAIGN_ID,
            )

    def test_label_terminal_and_teacher_contradictions_fail(self) -> None:
        ongoing = golden_records()[0]
        mate = golden_records()[6]
        with self.assertRaisesRegex(codec.FormatError, "perspective"):
            codec.encode_record(replace(ongoing, result_side_to_move=-1))
        with self.assertRaisesRegex(codec.FormatError, "teacher"):
            codec.encode_record(replace(mate, flags=mate.flags | codec.FLAG_TEACHER_PRESENT))
        with self.assertRaisesRegex(codec.FormatError, "checkmate"):
            codec.encode_record(replace(mate, game_result_white=-1, result_side_to_move=1))

    def test_standard_start_conservation_is_enforced(self) -> None:
        original = golden_records()[0]
        board = list(original.board)
        board[codec.parse_square("a2")] = 0
        mutated = replace(
            original,
            board=tuple(board),
            position_identity_sha256=codec.position_identity(
                board,
                original.side_to_move,
                original.castling_rights,
                original.effective_en_passant_square,
                original.pockets,
                original.promoted_mask,
            ),
        )
        with self.assertRaisesRegex(codec.FormatError, "pawn-origin conservation"):
            codec.encode_record(mutated)

    def test_provenance_is_canonical_and_dirty_source_fails(self) -> None:
        payload = provenance_bytes()
        document = codec.validate_provenance_bytes(payload, chunk_id=CHUNK_ID, campaign_id=CAMPAIGN_ID)
        self.assertFalse(document["source_dirty"])
        noncanonical = json.dumps(provenance_document(), indent=2, sort_keys=True).encode() + b"\n"
        with self.assertRaisesRegex(codec.FormatError, "not canonical"):
            codec.validate_provenance_bytes(noncanonical, chunk_id=CHUNK_ID, campaign_id=CAMPAIGN_ID)
        dirty = provenance_document()
        dirty["source_dirty"] = True
        with self.assertRaisesRegex(codec.FormatError, "dirty source"):
            codec.validate_provenance_bytes(codec.canonical_json_bytes(dirty), chunk_id=CHUNK_ID, campaign_id=CAMPAIGN_ID)

    def test_schema_rejects_duplicate_keys_and_crlf(self) -> None:
        payload = codec.DEFAULT_SCHEMA.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_bytes(payload.replace(b'"schema_id": "crazyhouse-physical-v1"', b'"schema_id": "crazyhouse-physical-v1",\n  "schema_id": "crazyhouse-physical-v1"', 1))
            with self.assertRaisesRegex(codec.FormatError, "duplicate JSON key"):
                codec.load_schema(duplicate)
            crlf = Path(directory) / "crlf.json"
            crlf.write_bytes(payload.replace(b"\n", b"\r\n"))
            with self.assertRaisesRegex(codec.FormatError, "LF line endings"):
                codec.load_schema(crlf)


if __name__ == "__main__":
    unittest.main()
