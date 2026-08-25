#!/usr/bin/env python3
"""Verify fail-closed routing when a SIMD capability build has no compiled SIMD backend."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from crazyhouse_uci_routing_verify import (
    PROFILE_TOKEN,
    UciProcess,
    VerificationFailure,
    require,
    setoption,
    sha256_file,
)


EXPECTED_ADDENDUM_SHA256 = "9f6738bdb9a28e731c1e71aebe56072500ed389a894e893cbab05abd18fa1c53"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--legacy-network", required=True, type=Path)
    parser.add_argument("--routing-addendum", required=True, type=Path)
    parser.add_argument("--expected-engine-sha256", required=True)
    parser.add_argument("--transcript-out", type=Path)
    args = parser.parse_args()

    try:
        for path in (args.engine, args.legacy_network, args.routing_addendum):
            require(path.is_file(), f"missing input: {path}")
        require(
            re.fullmatch(r"[0-9a-f]{64}", args.expected_engine_sha256) is not None,
            "expected engine SHA-256 is malformed",
        )
        require(
            sha256_file(args.engine) == args.expected_engine_sha256,
            "engine SHA-256 mismatch",
        )
        require(
            args.legacy_network.stat().st_size == 58_534_811
            and sha256_file(args.legacy_network)
            == "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43",
            "legacy network identity mismatch",
        )
        require(
            args.routing_addendum.stat().st_size == 7114
            and sha256_file(args.routing_addendum) == EXPECTED_ADDENDUM_SHA256,
            "routing addendum identity mismatch",
        )
        addendum = json.loads(args.routing_addendum.read_text(encoding="utf-8"))
        negative = addendum["unavailable_simd_negative_control"]
        require(negative["architecture"] == "general-64", "negative architecture drift")
        require(negative["compiled_simd_backend"] == "none", "negative SIMD lane drift")
        require(
            negative["required_error_code"] == "legacy_simd_unavailable",
            "negative error-code drift",
        )

        proc = UciProcess(args.engine)
        try:
            proc.send("uci")
            handshake = proc.wait_for(lambda line: line == "uciok", "uciok", 30)
            require(
                not any("CrazyhouseLegacyBackend" in line for line in handshake),
                "SIMD selection leaked into the UCI option inventory",
            )
            setoption(proc, "Threads", "1")
            setoption(proc, "Hash", "16")
            setoption(proc, "MultiPV", "1")
            setoption(proc, "UCI_Chess960", "false")
            setoption(proc, "UCI_Variant", "crazyhouse")
            setoption(proc, "CrazyhouseProfile", PROFILE_TOKEN)
            setoption(proc, "CrazyhouseEvalFile", str(args.legacy_network))

            proc.send("isready")
            readiness = proc.wait_for(
                lambda line: "info string READY state=failed" in line,
                "fail-closed SIMD readiness",
                90,
            )
            readiness += proc.drain()
            require(
                any("code=legacy_simd_unavailable" in line for line in readiness),
                f"missing legacy_simd_unavailable refusal: {readiness!r}",
            )
            require("readyok" not in readiness, "failed SIMD route emitted readyok")
            require(
                not any("route_commit status=ok ruleset=crazyhouse" in line for line in readiness),
                "failed SIMD route emitted a successful route commit",
            )
            require(
                not any("evaluator=incremental-scalar" in line for line in readiness),
                "failed SIMD route silently fell back to scalar",
            )

            proc.send("position startpos")
            # Position parsing and perft are rule-only operations.  They remain
            # available on a committed rules route even when its evaluator is
            # unavailable; readiness and every evaluator-backed search stay
            # fail-closed below.
            position = proc.drain(1.0)
            proc.send("go perft 1")
            perft = proc.wait_for(
                lambda line: line == "Nodes searched: 20"
                or "info string ERROR go" in line,
                "rule-only perft after unavailable SIMD",
                30,
            )
            perft += proc.drain()
            proc.send("go depth 1")
            search = proc.wait_for(
                lambda line: "info string ERROR go" in line or line.startswith("bestmove "),
                "search refusal after unavailable SIMD",
                30,
            )
            search += proc.drain()

            observation = {
                "schema": "crazyhouse-simd-unavailable-observation/v2",
                "engine": {
                    "bytes": args.engine.stat().st_size,
                    "sha256": args.expected_engine_sha256,
                },
                "legacy_network": {
                    "bytes": args.legacy_network.stat().st_size,
                    "sha256": sha256_file(args.legacy_network),
                },
                "routing_addendum": {
                    "bytes": args.routing_addendum.stat().st_size,
                    "sha256": EXPECTED_ADDENDUM_SHA256,
                },
                "readiness": readiness,
                "position": position,
                "perft": perft,
                "search": search,
                "readyok_withheld": "readyok" not in readiness,
                "route_commit_withheld": not any(
                    "route_commit status=ok ruleset=crazyhouse" in line for line in readiness
                ),
                "position_refused": any(
                    "info string ERROR position" in line for line in position
                ),
                "rule_only_perft_allowed": any(
                    line == "Nodes searched: 20" for line in perft
                ),
                "bestmove_withheld": not any(
                    line.startswith("bestmove ") for line in search
                ),
                "scalar_fallback": any(
                    "evaluator=incremental-scalar" in line for line in readiness + position + search
                ),
                "strength_claim": False,
            }
            if args.transcript_out is not None:
                require(
                    args.transcript_out.parent.is_dir(),
                    "transcript output directory missing",
                )
                require(not args.transcript_out.exists(), "transcript output already exists")
                args.transcript_out.write_text(
                    json.dumps(observation, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )

            require(
                not any("info string ERROR position" in line for line in position),
                f"rule-only position was refused after unavailable SIMD: {position!r}",
            )
            require(
                any(line == "Nodes searched: 20" for line in perft),
                f"rule-only perft was refused after unavailable SIMD: {perft!r}",
            )
            require(
                any("code=legacy_simd_unavailable" in line for line in search),
                f"search did not preserve unavailable-SIMD error: {search!r}",
            )
            require(
                not any(line.startswith("bestmove ") for line in search),
                "unavailable SIMD route entered search",
            )
            proc.close()
        except Exception:
            if proc.process.poll() is None:
                proc.close(expect_stderr_empty=False)
            raise

        print(
            "PASS crazyhouse_simd_unavailable "
            "code=legacy_simd_unavailable readyok_withheld=1 route_commit_withheld=1 "
            "rule_only_perft=PASS bestmove_withheld=1 scalar_fallback=false strength_claim=false"
        )
        return 0
    except (OSError, KeyError, TypeError, ValueError, VerificationFailure) as exc:
        print(f"FAIL crazyhouse_simd_unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
