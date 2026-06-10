"""Task and reminder store backed by SQLite at ~/.assistant/assistant.db."""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    notes TEXT DEFAULT '',
    due TEXT,                          -- ISO 8601 datetime or date, nullable
    priority TEXT DEFAULT 'normal',    -- low | normal | high
    status TEXT DEFAULT 'open',        -- open | done
    created_at TEXT NOT NULL,
    completed_at TEXT
);
"""


@dataclass
class Task:
    id: int
    title: str
    notes: str
    due: str | None
    priority: str
    status: str
    created_at: str
    completed_at: str | None

    def render(self) -> str:
        bits = [f"#{self.id}", self.title]
        if self.due:
            bits.append(f"(due {self.due})")
        if self.priority != "normal":
            bits.append(f"[{self.priority}]")
        if self.status == "done":
            bits.append("✓ done")
        if self.notes:
            bits.append(f"— {self.notes}")
        return " ".join(bits)


def _conn() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def add(title: str, due: str | None = None, notes: str = "", priority: str = "normal") -> Task:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, notes, due, priority, created_at) VALUES (?, ?, ?, ?, ?)",
            (title, notes, due, priority, dt.datetime.now().isoformat(timespec="seconds")),
        )
        return get(cur.lastrowid, conn)


def get(task_id: int, conn: sqlite3.Connection | None = None) -> Task:
    own = conn is None
    conn = conn or _conn()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"No task with id {task_id}")
        return Task(**dict(row))
    finally:
        if own:
            conn.close()


def list_tasks(status: str = "open") -> list[Task]:
    """List tasks. status: 'open', 'done', or 'all'."""
    query = "SELECT * FROM tasks"
    params: tuple = ()
    if status != "all":
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY due IS NULL, due, priority = 'high' DESC, id"
    with _conn() as conn:
        return [Task(**dict(r)) for r in conn.execute(query, params).fetchall()]


def complete(task_id: int) -> Task:
    with _conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status != 'done'",
            (dt.datetime.now().isoformat(timespec="seconds"), task_id),
        )
        # get() raises KeyError if the id never existed; already-done tasks return as-is.
        return get(task_id, conn)


def delete(task_id: int) -> None:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if cur.rowcount == 0:
            raise KeyError(f"No task with id {task_id}")


def due_soon(within_hours: int = 24) -> list[Task]:
    """Open tasks that are overdue or due within the window."""
    horizon = (dt.datetime.now() + dt.timedelta(hours=within_hours)).isoformat(timespec="seconds")
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'open' AND due IS NOT NULL AND due <= ? "
            "ORDER BY due",
            (horizon,),
        ).fetchall()
        return [Task(**dict(r)) for r in rows]
