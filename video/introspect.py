"""Read-only views of what the SSE stream and the trace don't carry (decision 0043).

Runs with the backend's venv and imports the backend's own functions, so what the video
shows is what the code computes, not a copy:

    backend/.venv/Scripts/python video/introspect.py guard "DELETE FROM pedidos"

Prints one JSON object. Writes nothing, opens no file and no network connection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.agent.tools.sql_guard import UnsafeQueryError, check_select


def guard(query: str) -> dict:
    """`sql_select`'s guard on `query`: the statement it would run, or why it refuses."""
    try:
        return {"query": query, "allowed": True, "runs": check_select(query)}
    except UnsafeQueryError as exc:
        return {"query": query, "allowed": False, "refused": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("guard", help="the SQL guard's verdict on one statement")
    g.add_argument("query")
    args = parser.parse_args()
    print(json.dumps(guard(args.query), ensure_ascii=False))


if __name__ == "__main__":
    main()
