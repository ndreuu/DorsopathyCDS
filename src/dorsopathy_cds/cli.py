"""Dependency-free local command line."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .service import PayloadError, evaluate_case, load_catalog


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON numbers are not allowed")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are not allowed")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research-only dorsopathy clinical rules")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate_cmd = commands.add_parser("evaluate", help="Evaluate a structured case JSON")
    evaluate_cmd.add_argument("case", type=Path)
    evaluate_cmd.add_argument("--output", type=Path)
    commands.add_parser("rules", help="Show rules and their exact source fragments")
    server_cmd = commands.add_parser("serve", help="Start a loopback HTTP API")
    server_cmd.add_argument("--host", default="127.0.0.1")
    server_cmd.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .http import serve
            serve(args.host, args.port)
            return 0
        if args.command == "rules":
            rules, sources = load_catalog()
            result = {"rules": rules, "sources": sources}
        else:
            if args.case.stat().st_size > 65536:
                raise PayloadError("Case exceeds the 64 KiB limit")
            payload = json.loads(args.case.read_text(encoding="utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
            result = evaluate_case(payload)
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.command == "evaluate" and args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(str(args.output))
        else:
            sys.stdout.write(output)
        return 0
    except (OSError, ValueError) as exc:
        # Do not print input values or clinical case bodies on error.
        print("Unable to process request: " + type(exc).__name__, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
