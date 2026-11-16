"""The text check in front of `sql_select`: one SELECT or WITH statement, nothing else.

This is the first of four layers (agent invariant 9). Even if a query slips past
it, the connection it runs on is read-only three times over (`business_db.py`).
The check works on the query with its string literals, quoted identifiers and
comments blanked out, so `';'` inside a string or `-- DROP` in a comment can't
fool it, and a keyword can't hide inside a comment either.
"""

import re

from app.agent.registry import ToolError

MAX_QUERY_CHARS = 5000

# Statements that change something, or reach outside the one database. `REPLACE`
# is only forbidden as `REPLACE INTO`: `replace()` is also an ordinary function.
# `END` (a synonym of COMMIT) isn't listed because `CASE ... END` needs it; as a
# statement of its own it fails the SELECT/WITH check.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|"
    r"ANALYZE|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|LOAD_EXTENSION|REPLACE\s+INTO)\b"
)
_FIRST_WORD = re.compile(r"\s*([A-Z_]+)")


class UnsafeQueryError(ToolError):
    """The query isn't a single read-only SELECT/WITH statement."""


def check_select(query: str) -> str:
    """Returns `query` ready to run (one trailing `;` removed), or raises `UnsafeQueryError`."""
    if len(query) > MAX_QUERY_CHARS:
        raise UnsafeQueryError(f"The query is too long (over {MAX_QUERY_CHARS} characters)")
    masked = mask_literals_and_comments(query)
    statement = masked.rstrip()
    trimmed = len(statement)
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
        trimmed = len(statement)
    if not statement.strip():
        raise UnsafeQueryError("The query is empty")
    if ";" in statement:
        raise UnsafeQueryError("Only one statement is allowed: remove the ';' between statements")

    upper = statement.upper()
    first = _FIRST_WORD.match(upper)
    if first is None or first.group(1) not in {"SELECT", "WITH"}:
        raise UnsafeQueryError("Only SELECT (or WITH ... SELECT) queries are allowed")
    forbidden = _FORBIDDEN.search(upper)
    if forbidden:
        word = " ".join(forbidden.group(1).split())
        raise UnsafeQueryError(f"{word} is not allowed: the business database is read-only")
    # `masked` has the same length as `query`, so the cut maps back to the original.
    return query[:trimmed].strip()


def mask_literals_and_comments(sql: str) -> str:
    """`sql` with the inside of every literal, quoted identifier and comment replaced by
    spaces, same length. Raises `UnsafeQueryError` if one of them never ends."""
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`":
            end = _closing_quote(sql, i, ch)
            _blank(out, i + 1, end)
            i = end + 1
        elif ch == "[":
            end = sql.find("]", i + 1)
            if end < 0:
                raise UnsafeQueryError("A [quoted identifier] is never closed")
            _blank(out, i + 1, end)
            i = end + 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end < 0 else end
            _blank(out, i, end)
            i = end
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise UnsafeQueryError("A /* comment */ is never closed")
            _blank(out, i, end + 2)
            i = end + 2
        else:
            i += 1
    return "".join(out)


def _closing_quote(sql: str, start: int, quote: str) -> int:
    """Index of the quote that closes the one at `start`; a doubled quote is an escape."""
    i = start + 1
    while True:
        end = sql.find(quote, i)
        if end < 0:
            kind = "string" if quote == "'" else "quoted identifier"
            raise UnsafeQueryError(f"A {kind} is never closed")
        if sql.startswith(quote * 2, end):
            i = end + 2
            continue
        return end


def _blank(chars: list[str], start: int, end: int) -> None:
    for k in range(start, end):
        if chars[k] != "\n":
            chars[k] = " "
