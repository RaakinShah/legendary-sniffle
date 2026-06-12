"""Tiny shared helpers used across the assistant package."""

from __future__ import annotations

import re
from typing import Any


def text_result(message: str, is_error: bool = False) -> dict[str, Any]:
    """Build an MCP tool text response (optionally an error)."""
    result: dict[str, Any] = {"content": [{"type": "text", "text": message}]}
    if is_error:
        result["is_error"] = True
    return result


def fts_rows(con, sql: str, query: str, limit: int, *, first: str | None = None) -> list:
    """Run an FTS5 MATCH query, falling back to a quoted phrase when the raw
    query is invalid FTS syntax (apostrophes, stray operators). One home for
    the pattern history and observer both need.

    `first` overrides the initial attempt (e.g. a prefix query like q+"*");
    the fallback always strips quotes and retries the plain query as a phrase.
    """
    import sqlite3
    try:
        return con.execute(sql, (first if first is not None else query, limit)).fetchall()
    except sqlite3.OperationalError:
        safe = '"' + query.replace('"', "") + '"'
        return con.execute(sql, (safe, limit)).fetchall()


_SECRET = re.compile(
    # Anthropic keys/tokens, Google OAuth client secrets, and generic long
    # bearer-ish tokens that sometimes surface in SDK/HTTP error text.
    r"sk-ant-[A-Za-z0-9_-]{8,}"
    r"|GOCSPX-[A-Za-z0-9_-]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._-]{16,}"
)


def redact(text: str) -> str:
    """Mask credential-shaped substrings before an error string reaches the
    model, the chat, or a log line."""
    return _SECRET.sub("[redacted]", text or "")
