"""Read-only access to the business database (agent invariant 9).

The agent's business tools read `hilda_luna.db` through `open_business_db`
and nothing else. Three independent layers keep the connection read-only:

1. SQLite opens the file with `mode=ro`, so the OS-level handle can't write.
2. `PRAGMA query_only = ON` makes SQLite refuse every statement that changes
   the database, attached ones included.
3. An authorizer allows only reading: SELECT, reading columns, functions and
   recursive CTEs. Writes, DDL, PRAGMA, ATTACH and transactions are denied
   when the statement is prepared, before it runs.

The `sql_select` guard (Phase 2) checks the text of a query on top of this;
the connection stays safe even if that check has a bug.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.config import Settings

_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)


class BusinessDBMissingError(RuntimeError):
    """The business database file doesn't exist yet."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"The business database is missing at {path}. "
            "Create it with: python -m app.cli seed-business-db"
        )
        self.path = path


def _read_only_authorizer(action: int, *_args: object) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def open_business_db(path: Path) -> sqlite3.Connection:
    """Opens the business database read-only. The caller closes it.

    Rows come back as `sqlite3.Row`, so tools can read columns by name.
    """
    if not path.is_file():
        raise BusinessDBMissingError(path)
    cx = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    try:
        cx.execute("PRAGMA query_only = ON")
        cx.set_authorizer(_read_only_authorizer)
    except BaseException:
        cx.close()
        raise
    cx.row_factory = sqlite3.Row
    return cx


@contextmanager
def business_db(settings: Settings) -> Iterator[sqlite3.Connection]:
    """A read-only connection to the configured business database, closed after use."""
    cx = open_business_db(settings.business_db_file)
    try:
        yield cx
    finally:
        cx.close()
