"""Persistent conversation history: saved chats the sidebar lists and reopens.

Local SQLite at ASSISTANT_HOME/chats.db. Each conversation stores its transcript
plus the agent session_id, so reopening can resume the model's context.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing

from . import config


def _db_path():
    # Read from config at call time so ASSISTANT_HOME overrides (and tests) apply.
    return config.ASSISTANT_HOME / "chats.db"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    # The recall observer thread and the main thread write concurrently; wait
    # out brief lock collisions instead of raising "database is locked".
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, "
        "created_at TEXT, updated_at TEXT, favorite INTEGER DEFAULT 0, session_id TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "conv_id INTEGER, role TEXT, text TEXT, ts TEXT)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conv_id)")
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
        "USING fts5(text, conv_id UNINDEXED)"
    )
    # Migration anchor for future schema changes. v1 is fully described by the
    # additive CREATE IF NOT EXISTS statements above.
    if con.execute("PRAGMA user_version").fetchone()[0] == 0:
        con.execute("PRAGMA user_version=1")
    return con


def create(title: str = "New chat") -> int:
    with closing(_conn()) as con, con:
        cur = con.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?,?,?)",
            (title, _now(), _now()),
        )
        return cur.lastrowid


def append(conv_id: int, role: str, text: str) -> None:
    if not text:
        return
    with closing(_conn()) as con, con:
        cur = con.execute(
            "INSERT INTO messages (conv_id, role, text, ts) VALUES (?,?,?,?)",
            (conv_id, role, text, _now()),
        )
        # Pin the FTS rowid to the message rowid so search can join exactly one
        # message per hit (a text-equality join would go cartesian on repeated
        # text). Historical rows already match: both tables were always inserted
        # in lockstep, so their auto rowids line up.
        con.execute("INSERT INTO messages_fts (rowid, text, conv_id) VALUES (?,?,?)",
                    (cur.lastrowid, text, conv_id))
        con.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id))


def set_session(conv_id: int, session_id: str) -> None:
    if not session_id:
        return
    with closing(_conn()) as con, con:
        con.execute("UPDATE conversations SET session_id = ? WHERE id = ?", (session_id, conv_id))


def set_title(conv_id: int, title: str) -> None:
    with closing(_conn()) as con, con:
        con.execute("UPDATE conversations SET title = ? WHERE id = ?", (title.strip()[:80], conv_id))


def set_favorite(conv_id: int, favorite: bool) -> bool:
    with closing(_conn()) as con, con:
        con.execute("UPDATE conversations SET favorite = ? WHERE id = ?", (1 if favorite else 0, conv_id))
    return favorite


def delete(conv_id: int) -> None:
    with closing(_conn()) as con, con:
        con.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        con.execute("DELETE FROM messages WHERE conv_id = ?", (conv_id,))
        con.execute("DELETE FROM messages_fts WHERE conv_id = ?", (conv_id,))


def get(conv_id: int) -> dict:
    with closing(_conn()) as con, con:
        row = con.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            return {}
        msgs = con.execute(
            "SELECT role, text FROM messages WHERE conv_id = ? ORDER BY rowid", (conv_id,)
        ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "favorite": bool(row["favorite"]),
        "session_id": row["session_id"],
        "messages": [{"role": m["role"], "text": m["text"]} for m in msgs],
    }


def _summaries(where: str = "", params: tuple = (), limit: int = 30) -> list[dict]:
    with closing(_conn()) as con, con:
        rows = con.execute(
            "SELECT id, title, favorite FROM conversations "
            + (f"WHERE {where} " if where else "")
            + "ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [{"id": r["id"], "title": r["title"] or "New chat", "favorite": bool(r["favorite"])}
            for r in rows]


def recents(limit: int = 25) -> list[dict]:
    """Recent conversations that actually have messages."""
    return _summaries(
        "id IN (SELECT DISTINCT conv_id FROM messages) AND favorite = 0", limit=limit
    )


def favorites() -> list[dict]:
    return _summaries("favorite = 1", limit=50)


def search_messages(query: str, limit: int = 12) -> str:
    """Full-text search over past conversation messages. Returns rendered hits,
    one per line with timestamp, conversation title, role, and a snippet."""
    if not _db_path().exists():
        return "No conversation history yet."
    query = query.strip()
    if not query:
        return f"Nothing in past conversations matches {query!r}."
    # The FTS rowid is pinned to the message rowid on insert (see append), so
    # each hit joins exactly one message. A text-equality join would multiply
    # hits whenever the same text appears more than once in a conversation.
    sql = (
        "SELECT m.ts, c.title, m.role, snippet(messages_fts, 0, '>>', '<<', ' … ', 14) AS snip "
        "FROM messages_fts f "
        "JOIN messages m ON m.rowid = f.rowid "
        "JOIN conversations c ON c.id = f.conv_id "
        "WHERE messages_fts MATCH ? ORDER BY m.ts DESC, m.rowid DESC LIMIT ?"
    )
    from .util import fts_rows
    with closing(_conn()) as con, con:
        rows = fts_rows(con, sql, query, limit)
    if not rows:
        return f"Nothing in past conversations matches {query!r}."
    return "\n".join(
        f"{r['ts'][:16].replace('T', ' ')}  [{r['title'] or 'New chat'}]  ({r['role']})  {r['snip']}"
        for r in rows
    )


def search(q: str, limit: int = 30) -> list[dict]:
    q = q.strip()
    if not q:
        return recents(limit)
    from .util import fts_rows
    sql = (
        "SELECT DISTINCT c.id, c.title, c.favorite FROM messages_fts f "
        "JOIN conversations c ON c.id = f.conv_id "
        "WHERE messages_fts MATCH ? ORDER BY c.updated_at DESC LIMIT ?"
    )
    with closing(_conn()) as con, con:
        rows = fts_rows(con, sql, q, limit, first=q + "*")
    return [{"id": r["id"], "title": r["title"] or "New chat", "favorite": bool(r["favorite"])}
            for r in rows]
