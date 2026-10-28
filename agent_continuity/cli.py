"""Command-line entry point for the synthetic offline example."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

from .store import ContinuityStore, Rejected


def read_json(path: str) -> dict:
    data = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise Rejected("input must be a JSON object")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Offline source-backed work continuity example")
    p.add_argument("--db", required=True, help="Path to a local SQLite database; parent must exist")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("record", "review"):
        command = sub.add_parser(name)
        command.add_argument("--input", required=True, help="JSON object file, or - for stdin")
    status = sub.add_parser("status")
    status.add_argument("item_id")
    sub.add_parser("recover")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        store = ContinuityStore(args.db)
        try:
            if args.command in {"record", "review"}:
                output = store.apply(args.command, read_json(args.input))
            elif args.command == "status":
                output = store.status(args.item_id)
            else:
                output = store.recover()
        finally:
            store.close()
    except (OSError, json.JSONDecodeError, Rejected, sqlite3.Error) as error:  # type: ignore[name-defined]
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
