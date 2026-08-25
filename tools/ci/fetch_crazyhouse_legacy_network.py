#!/usr/bin/env python3
"""Materialize the registered Crazyhouse V1 network with fail-closed identity checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import BinaryIO, Sequence
import urllib.error
import urllib.parse
import urllib.request


REGISTERED_BASENAME = "Crazyhouse_v1.nnue"
REGISTERED_BYTES = 58_534_811
REGISTERED_SHA256 = "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43"
READ_SIZE = 1024 * 1024


class NetworkInputError(RuntimeError):
    """A typed, user-facing failure that does not expose URL query material."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self,
        request: urllib.request.Request,
        file_pointer: BinaryIO,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request | None:
        parsed = urllib.parse.urlsplit(new_url)
        if parsed.scheme.lower() != "https":
            raise NetworkInputError(
                "REDIRECT_SCHEME_REJECTED", "network download redirected outside HTTPS"
            )
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(READ_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_network_identity(
    path: Path,
    *,
    expected_bytes: int = REGISTERED_BYTES,
    expected_sha256: str = REGISTERED_SHA256,
) -> tuple[int, str]:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise NetworkInputError("NETWORK_MISSING", "registered network is missing") from error
    if not resolved.is_file():
        raise NetworkInputError("NETWORK_NOT_FILE", "registered network is not a regular file")
    size = resolved.stat().st_size
    if size != expected_bytes:
        raise NetworkInputError(
            "NETWORK_SIZE_MISMATCH",
            f"network byte count mismatch: expected {expected_bytes}, observed {size}",
        )
    digest = sha256(resolved)
    if digest.lower() != expected_sha256.lower():
        raise NetworkInputError(
            "NETWORK_DIGEST_MISMATCH", "network SHA-256 does not match the registered artifact"
        )
    return size, digest.lower()


def validate_public_https_url(value: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as error:
        raise NetworkInputError("URL_PARSE_REJECTED", "network URL is malformed") from error
    if parsed.scheme.lower() != "https":
        raise NetworkInputError("URL_SCHEME_REJECTED", "network URL must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise NetworkInputError(
            "URL_AUTHORITY_REJECTED", "network URL must have a host and no embedded credentials"
        )
    if parsed.fragment:
        raise NetworkInputError("URL_FRAGMENT_REJECTED", "network URL must not contain a fragment")
    if parsed.query:
        raise NetworkInputError(
            "URL_QUERY_REJECTED", "network URL must be a stable public URL without a query"
        )
    safe_origin = f"{parsed.scheme.lower()}://{parsed.hostname.lower()}"
    try:
        port = parsed.port
    except ValueError as error:
        raise NetworkInputError("URL_PORT_REJECTED", "network URL has an invalid port") from error
    if port is not None:
        safe_origin += f":{port}"
    safe_path = parsed.path or "/"
    return safe_origin, safe_path


def _copy_and_hash(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    expected_bytes: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        block = source.read(READ_SIZE)
        if not block:
            break
        total += len(block)
        if total > expected_bytes:
            raise NetworkInputError(
                "NETWORK_OVERSIZED", "network stream exceeded the registered byte count"
            )
        destination.write(block)
        digest.update(block)
    return total, digest.hexdigest()


def materialize(
    source: BinaryIO,
    output: Path,
    *,
    expected_bytes: int = REGISTERED_BYTES,
    expected_sha256: str = REGISTERED_SHA256,
) -> tuple[int, str]:
    output = output.resolve()
    if output.exists():
        raise NetworkInputError("OUTPUT_EXISTS", "refusing to replace an existing network output")
    if not output.parent.is_dir():
        raise NetworkInputError("OUTPUT_PARENT_MISSING", "network output parent does not exist")

    partial = output.with_name(f".{output.name}.partial-{os.getpid()}")
    if partial.exists():
        raise NetworkInputError("PARTIAL_EXISTS", "refusing to replace an existing partial output")

    try:
        with partial.open("xb") as destination:
            size, digest = _copy_and_hash(
                source, destination, expected_bytes=expected_bytes
            )
            destination.flush()
            os.fsync(destination.fileno())
        if size != expected_bytes:
            raise NetworkInputError(
                "NETWORK_TRUNCATED",
                f"network byte count mismatch: expected {expected_bytes}, observed {size}",
            )
        if digest.lower() != expected_sha256.lower():
            raise NetworkInputError(
                "NETWORK_DIGEST_MISMATCH", "network SHA-256 does not match the registered artifact"
            )
        try:
            os.link(partial, output)
        except FileExistsError as error:
            raise NetworkInputError(
                "OUTPUT_RACE", "network output appeared during materialization"
            ) from error
        partial.unlink()
        return size, digest.lower()
    finally:
        if partial.exists():
            partial.unlink()


def _open_url(url: str, timeout: float) -> BinaryIO:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Crazyhouse-Stockfish-correctness-ci/1"},
            method="GET",
        )
    except ValueError as error:
        raise NetworkInputError(
            "DOWNLOAD_REQUEST_REJECTED", "network download request is malformed"
        ) from error
    opener = urllib.request.build_opener(HttpsOnlyRedirectHandler())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise NetworkInputError(
            "DOWNLOAD_HTTP_ERROR", f"network download returned HTTP {error.code}"
        ) from error
    except urllib.error.URLError as error:
        reason = type(error.reason).__name__
        raise NetworkInputError(
            "DOWNLOAD_TRANSPORT_ERROR", f"network download transport failed ({reason})"
        ) from error
    except TimeoutError as error:
        raise NetworkInputError("DOWNLOAD_TIMEOUT", "network download timed out") from error

    try:
        final = urllib.parse.urlsplit(response.geturl())
    except ValueError as error:
        response.close()
        raise NetworkInputError(
            "FINAL_URL_REJECTED", "network download resolved to a malformed URL"
        ) from error
    if final.scheme.lower() != "https":
        response.close()
        raise NetworkInputError(
            "FINAL_SCHEME_REJECTED", "network download resolved outside HTTPS"
        )
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            observed_length = int(content_length)
        except ValueError as error:
            response.close()
            raise NetworkInputError(
                "CONTENT_LENGTH_INVALID", "network response has an invalid Content-Length"
            ) from error
        if observed_length != REGISTERED_BYTES:
            response.close()
            raise NetworkInputError(
                "CONTENT_LENGTH_MISMATCH",
                "network response Content-Length does not match the registered artifact",
            )
    return response  # type: ignore[return-value]


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    path = path.resolve()
    if path.exists():
        raise NetworkInputError("MANIFEST_EXISTS", "refusing to replace an existing manifest")
    if not path.parent.is_dir():
        raise NetworkInputError("MANIFEST_PARENT_MISSING", "manifest parent does not exist")
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as destination:
            created = True
            destination.write(serialized)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        if created and path.exists():
            path.unlink()
        raise


def validate_destinations(output: Path, manifest: Path) -> tuple[Path, Path]:
    output = output.resolve()
    manifest = manifest.resolve()
    if output.name != REGISTERED_BASENAME:
        raise NetworkInputError(
            "OUTPUT_BASENAME_MISMATCH", f"network output must be named {REGISTERED_BASENAME}"
        )
    if output == manifest:
        raise NetworkInputError(
            "OUTPUT_MANIFEST_ALIAS", "network output and manifest must differ"
        )
    if not output.parent.is_dir():
        raise NetworkInputError("OUTPUT_PARENT_MISSING", "network output parent does not exist")
    if not manifest.parent.is_dir():
        raise NetworkInputError("MANIFEST_PARENT_MISSING", "manifest parent does not exist")
    if output.exists():
        raise NetworkInputError("OUTPUT_EXISTS", "refusing to replace an existing network output")
    if manifest.exists():
        raise NetworkInputError("MANIFEST_EXISTS", "refusing to replace an existing manifest")
    return output, manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--input", type=Path)
    source.add_argument("--verify-existing", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    if not (0.0 < args.timeout_seconds <= 600.0):
        raise NetworkInputError(
            "TIMEOUT_INVALID", "timeout must be greater than zero and at most 600 seconds"
        )

    if args.verify_existing is not None:
        if args.output is not None or args.manifest is not None:
            raise NetworkInputError(
                "ARGUMENT_COMBINATION_REJECTED",
                "verification mode does not accept output or manifest destinations",
            )
        size, digest = verify_network_identity(args.verify_existing)
        print(
            json.dumps(
                {
                    "status": "PASS_EXACT_REGISTERED_LEGACY_NETWORK",
                    "bytes": size,
                    "sha256": digest,
                    "path_recorded": False,
                }
            )
        )
        return 0

    if args.output is None or args.manifest is None:
        raise NetworkInputError(
            "DESTINATION_REQUIRED",
            "materialization mode requires both output and manifest destinations",
        )
    output, manifest_path = validate_destinations(args.output, args.manifest)

    source_record: dict[str, object]
    if args.url is not None:
        safe_origin, safe_path = validate_public_https_url(args.url)
        source_record = {
            "kind": "public_https",
            "origin": safe_origin,
            "path": safe_path,
            "query_recorded": False,
            "credentials_recorded": False,
        }
        source = _open_url(args.url, args.timeout_seconds)
    else:
        input_path = args.input.resolve(strict=True)
        if input_path == output:
            raise NetworkInputError("SOURCE_OUTPUT_ALIAS", "network input and output must differ")
        source_record = {
            "kind": "local_validation",
            "basename": input_path.name,
            "path_recorded": False,
        }
        source = input_path.open("rb")

    try:
        size, digest = materialize(source, output)
    finally:
        source.close()

    output = output.resolve(strict=True)
    manifest = {
        "schema": "crazyhouse-ci-network-materialization/v1",
        "status": "PASS_EXACT_REGISTERED_LEGACY_NETWORK",
        "source": source_record,
        "artifact": {
            "basename": output.name,
            "bytes": size,
            "sha256": digest,
            "registered_basename": REGISTERED_BASENAME,
            "byte_identical_alias_required": output.name == REGISTERED_BASENAME,
        },
        "fallback_allowed": False,
        "fallback_observed": False,
        "strength_claim": False,
        "openbench_evidence": False,
        "release_evidence": False,
    }
    created_identity = (output.stat().st_dev, output.stat().st_ino)
    try:
        _write_manifest(manifest_path, manifest)
    except BaseException as error:
        try:
            current = output.stat()
            if (current.st_dev, current.st_ino) != created_identity:
                raise NetworkInputError(
                    "ROLLBACK_OWNERSHIP_LOST",
                    "network output changed before manifest rollback",
                ) from error
            output.unlink()
        except FileNotFoundError:
            pass
        raise
    print(json.dumps({"status": manifest["status"], "bytes": size, "sha256": digest}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except NetworkInputError as error:
        print(
            json.dumps({"status": "FAIL_CLOSED", "code": error.code, "message": str(error)}),
            file=sys.stderr,
        )
        return 2
    except (FileNotFoundError, PermissionError, OSError) as error:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED",
                    "code": "LOCAL_IO_ERROR",
                    "message": type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
