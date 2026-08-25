#!/usr/bin/env python3
"""Unit tests for the fail-closed Crazyhouse CI network materializer."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from tools.ci.fetch_crazyhouse_legacy_network import (
    NetworkInputError,
    materialize,
    validate_destinations,
    validate_public_https_url,
    verify_network_identity,
)


class NetworkMaterializerTests(unittest.TestCase):
    def test_materializes_exact_local_bytes(self) -> None:
        payload = b"crazyhouse-ci-fixture\x00\x01"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.nnue"
            size, digest = materialize(
                io.BytesIO(payload),
                output,
                expected_bytes=len(payload),
                expected_sha256=expected,
            )
            self.assertEqual(size, len(payload))
            self.assertEqual(digest, expected)
            self.assertEqual(output.read_bytes(), payload)

    def test_rejects_truncated_input_without_output(self) -> None:
        payload = b"short"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.nnue"
            with self.assertRaisesRegex(NetworkInputError, "byte count mismatch") as failure:
                materialize(
                    io.BytesIO(payload),
                    output,
                    expected_bytes=len(payload) + 1,
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                )
            self.assertEqual(failure.exception.code, "NETWORK_TRUNCATED")
            self.assertFalse(output.exists())
            self.assertFalse(any(".partial-" in path.name for path in Path(directory).iterdir()))

    def test_rejects_oversized_input_without_output(self) -> None:
        payload = b"oversized"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.nnue"
            with self.assertRaises(NetworkInputError) as failure:
                materialize(
                    io.BytesIO(payload),
                    output,
                    expected_bytes=len(payload) - 1,
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                )
            self.assertEqual(failure.exception.code, "NETWORK_OVERSIZED")
            self.assertFalse(output.exists())

    def test_rejects_digest_mismatch_without_output(self) -> None:
        payload = b"digest"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.nnue"
            with self.assertRaises(NetworkInputError) as failure:
                materialize(
                    io.BytesIO(payload),
                    output,
                    expected_bytes=len(payload),
                    expected_sha256="00" * 32,
                )
            self.assertEqual(failure.exception.code, "NETWORK_DIGEST_MISMATCH")
            self.assertFalse(output.exists())

    def test_refuses_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.nnue"
            output.write_bytes(b"owner-data")
            with self.assertRaises(NetworkInputError) as failure:
                materialize(
                    io.BytesIO(b"replacement"),
                    output,
                    expected_bytes=11,
                    expected_sha256=hashlib.sha256(b"replacement").hexdigest(),
                )
            self.assertEqual(failure.exception.code, "OUTPUT_EXISTS")
            self.assertEqual(output.read_bytes(), b"owner-data")

    def test_requires_stable_public_https_url(self) -> None:
        self.assertEqual(
            validate_public_https_url("https://example.test/assets/Crazyhouse_v1.nnue"),
            ("https://example.test", "/assets/Crazyhouse_v1.nnue"),
        )
        rejected = {
            "http://example.test/net.nnue": "URL_SCHEME_REJECTED",
            "https://user@example.test/net.nnue": "URL_AUTHORITY_REJECTED",
            "https://example.test/net.nnue?token=secret": "URL_QUERY_REJECTED",
            "https://example.test/net.nnue#fragment": "URL_FRAGMENT_REJECTED",
            "https://example.test:invalid/net.nnue": "URL_PORT_REJECTED",
            "https://[::1/net.nnue": "URL_PARSE_REJECTED",
        }
        for value, code in rejected.items():
            with self.subTest(value=value):
                with self.assertRaises(NetworkInputError) as failure:
                    validate_public_https_url(value)
                self.assertEqual(failure.exception.code, code)

    def test_destination_preflight_rejects_wrong_basename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(NetworkInputError) as failure:
                validate_destinations(root / "wrong.nnue", root / "manifest.json")
            self.assertEqual(failure.exception.code, "OUTPUT_BASENAME_MISMATCH")

    def test_destination_preflight_rejects_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Crazyhouse_v1.nnue"
            with self.assertRaises(NetworkInputError) as failure:
                validate_destinations(target, target)
            self.assertEqual(failure.exception.code, "OUTPUT_MANIFEST_ALIAS")

    def test_existing_manifest_prevents_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "Crazyhouse_v1.nnue"
            manifest = root / "manifest.json"
            manifest.write_text("owner-data", encoding="utf-8")
            with self.assertRaises(NetworkInputError) as failure:
                validate_destinations(output, manifest)
            self.assertEqual(failure.exception.code, "MANIFEST_EXISTS")
            self.assertFalse(output.exists())
            self.assertEqual(manifest.read_text(encoding="utf-8"), "owner-data")

    def test_verifies_exact_existing_bytes(self) -> None:
        payload = b"registered-network-fixture"
        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "legacy.nnue"
            network.write_bytes(payload)
            self.assertEqual(
                verify_network_identity(
                    network,
                    expected_bytes=len(payload),
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                ),
                (len(payload), hashlib.sha256(payload).hexdigest()),
            )

    def test_existing_network_size_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "legacy.nnue"
            network.write_bytes(b"short")
            with self.assertRaises(NetworkInputError) as failure:
                verify_network_identity(
                    network,
                    expected_bytes=6,
                    expected_sha256=hashlib.sha256(b"short").hexdigest(),
                )
            self.assertEqual(failure.exception.code, "NETWORK_SIZE_MISMATCH")

    def test_existing_network_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "legacy.nnue"
            network.write_bytes(b"digest")
            with self.assertRaises(NetworkInputError) as failure:
                verify_network_identity(
                    network,
                    expected_bytes=6,
                    expected_sha256="00" * 32,
                )
            self.assertEqual(failure.exception.code, "NETWORK_DIGEST_MISMATCH")


if __name__ == "__main__":
    unittest.main()
