"""Persistent conversation history: saved chats the sidebar lists and reopens.

Local SQLite at ASSISTANT_HOME/chats.db. Each conversation stores its transcript
plus the agent session_id, so reopening can resume the model's context.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from . import config

DB = config.ASSISTANT_HOME / "chats.db"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, "
        "created_at TEXT, updated_at TEXT, favorite INTEGER DEFAULT 0, session_id TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "conv_id INTEGER, role TEXT, text TEXT, ts TEXT)"
    )
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
        "USING fts5(text, conv_id UNINDEXED)"
    )
    return con


def create(title: str = "New chat") -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?,?,?)",
            (title, _now(), _now()),
        )
        return cur.lastrowid


def append(conv_id: int, role: str, text: str) -> None:
    if not text:
        return
    with _conn() as con:
        con.execute(
            "INSERT INTO messages (conv_id, role, text, ts) VALUES (?,?,?,?)",
            (conv_id, role, text, _now()),
        )
        con.execute("INSERT INTO messages_fts (text, conv_id) VALUES (?,?)", (text, conv_id))
        con.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id))


def set_session(conv_id: int, session_id: str) -> None:
    if not session_id:
        return
    with _conn() as con:
        con.execute("UPDATE conversations SET session_id = ? WHERE id = ?", (session_id, conv_id))


def set_title(conv_id: int, title: str) -> None:
    with _conn() as con:
        con.execute("UPDATE conversations SET title = ? WHERE id = ?", (title.strip()[:80], conv_id))


def set_favorite(conv_id: int, favorite: bool) -> bool:
    with _conn() as con:
        con.execute("UPDATE conversations SET favorite = ? WHERE id = ?", (1 if favorite else 0, conv_id))
    return favorite


def delete(conv_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        con.execute("DELETE FROM messages WHERE conv_id = ?", (conv_id,))
        con.execute("DELETE FROM messages_fts WHERE conv_id = ?", (conv_id,))


def get(conv_id: int) -> dict:
    with _conn() as con:
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
    with _conn() as con:
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


def search(q: str, limit: int = 30) -> list[dict]:
    q = q.strip()
    if not q:
        return recents(limit)
    with _conn() as con:
        try:
            rows = con.execute(
                "SELECT DISTINCT c.id, c.title, c.favorite FROM messages_fts f "
                "JOIN conversations c ON c.id = f.conv_id "
                "WHERE messages_fts MATCH ? ORDER BY c.updated_at DESC LIMIT ?",
                (q + "*", limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = con.execute(
                "SELECT DISTINCT c.id, c.title, c.favorite FROM messages_fts f "
                "JOIN conversations c ON c.id = f.conv_id "
                "WHERE messages_fts MATCH ? ORDER BY c.updated_at DESC LIMIT ?",
                ('"' + q.replace('"', "") + '"', limit),
            ).fetchall()
    return [{"id": r["id"], "title": r["title"] or "New chat", "favorite": bool(r["favorite"])}
            for r in rows]
