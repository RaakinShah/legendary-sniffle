"""The proactive feed store: ~/.assistant/proactive.db.

Holds surfaced insights with a status lifecycle (new -> seen -> snoozed/dismissed/
done) and a dedupe window so the same finding is shown once, not every cycle. The
GUI Routines panel reads `feed()`; the runner writes via `add()`.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing

from .. import config, db
from .core import Insight

# A finding stays deduped for this long: a stale unread email or an open task
# should not re-surface every 15 minutes. After the window it can return if the
# check still produces it.
DEDUPE_DAYS = 7
RETAIN_DAYS = 30


def _db_path():
    return config.ASSISTANT_HOME / "proactive.db"


def _schema(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS feed ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, category TEXT, title TEXT, "
        "body TEXT, urgency TEXT, action_prompt TEXT, source TEXT, "
        "status TEXT DEFAULT 'new', created_at TEXT, snooze_until TEXT, feedback INTEGER)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_feed_key ON feed(key)")


def _conn() -> sqlite3.Connection:
    return db.open_db(_db_path(), _schema)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def add(ins: Insight) -> bool:
    """Insert an insight unless an equivalent one is still within the dedupe
    window. Returns True if it was newly added (so the runner knows to maybe
    notify), False if it was a duplicate."""
    cutoff = (dt.datetime.now() - dt.timedelta(days=DEDUPE_DAYS)).isoformat(timespec="seconds")
    with closing(_conn()) as con, con:
        dup = con.execute(
            "SELECT 1 FROM feed WHERE key = ? AND created_at >= ? LIMIT 1",
            (ins.key, cutoff),
        ).fetchone()
        if dup:
            return False
        con.execute(
            "INSERT INTO feed (key, category, title, body, urgency, action_prompt, "
            "source, status, created_at) VALUES (?,?,?,?,?,?,?,'new',?)",
            (ins.key, ins.category, ins.title, ins.body, ins.urgency,
             ins.action_prompt, ins.source, _now()),
        )
    return True


def feed(limit: int = 50, include_resolved: bool = False) -> list[dict]:
    """Active feed items newest first. Snoozed items reappear once their snooze
    has elapsed; dismissed/done are hidden unless include_resolved."""
    now = _now()
    where = ("status IN ('new','seen') OR (status = 'snoozed' AND snooze_until <= ?)"
             if not include_resolved else "1=1")
    params: tuple = (now,) if not include_resolved else ()
    with closing(_conn()) as con, con:
        rows = con.execute(
            f"SELECT * FROM feed WHERE {where} ORDER BY "
            "CASE urgency WHEN 'notify' THEN 0 ELSE 1 END, created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def unread_count() -> int:
    with closing(_conn()) as con, con:
        return con.execute("SELECT COUNT(*) FROM feed WHERE status = 'new'").fetchone()[0]


def mark(item_id: int, status: str) -> None:
    with closing(_conn()) as con, con:
        con.execute("UPDATE feed SET status = ? WHERE id = ?", (status, int(item_id)))


def mark_seen() -> None:
    """Called when the user opens the feed: new -> seen (clears the unread dot)."""
    with closing(_conn()) as con, con:
        con.execute("UPDATE feed SET status = 'seen' WHERE status = 'new'")


def snooze(item_id: int, hours: float) -> None:
    until = (dt.datetime.now() + dt.timedelta(hours=hours)).isoformat(timespec="seconds")
    with closing(_conn()) as con, con:
        con.execute("UPDATE feed SET status = 'snoozed', snooze_until = ? WHERE id = ?",
                    (until, int(item_id)))


def set_feedback(item_id: int, good: bool) -> None:
    with closing(_conn()) as con, con:
        con.execute("UPDATE feed SET feedback = ? WHERE id = ?",
                    (1 if good else -1, int(item_id)))


def get(item_id: int) -> dict:
    with closing(_conn()) as con, con:
        row = con.execute("SELECT * FROM feed WHERE id = ?", (int(item_id),)).fetchone()
    return dict(row) if row else {}


def prune(days: int = RETAIN_DAYS) -> int:
    """Drop resolved items older than `days`. Returns rows removed."""
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    with closing(_conn()) as con, con:
        cur = con.execute(
            "DELETE FROM feed WHERE status IN ('dismissed','done') AND created_at < ?",
            (cutoff,),
        )
        return cur.rowcount or 0
