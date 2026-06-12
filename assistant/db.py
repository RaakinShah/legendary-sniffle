"""One place for the SQLite setup every store shares.

history, tasks, recall, and the proactive feed all want the same connection
recipe: WAL journaling so a background writer and the GUI reader never block
each other, NORMAL sync (the safe, fast pairing for WAL), a busy timeout so a
brief lock waits instead of raising, and a user_version anchor for future
migrations. Keeping it here means a change to that recipe happens once, not in
four hand-copied `_conn` helpers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable


def open_db(
    path: Path,
    schema: Callable[[sqlite3.Connection], None] | str | None = None,
    *,
    version: int = 1,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """Open `path` with the project's standard pragmas, apply `schema`, and
    stamp user_version on a fresh database.

    `schema` may be a callable run with the connection (for CREATE statements
    that need Python logic) or a plain SQL script string. `row_factory=True`
    yields `sqlite3.Row` rows; pass False for modules that read rows positionally.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if row_factory:
        con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    if callable(schema):
        schema(con)
    elif schema:
        con.executescript(schema)
    # Migration anchor: a fresh db (user_version 0) is stamped to the current
    # version; the additive CREATE IF NOT EXISTS schema fully describes v1.
    if con.execute("PRAGMA user_version").fetchone()[0] == 0:
        con.execute(f"PRAGMA user_version={int(version)}")
    return con
