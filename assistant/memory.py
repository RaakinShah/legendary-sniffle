"""Persistent memory: markdown files the assistant reads at startup and edits over time.

Layout under ~/.assistant/memory/:
    profile.md      — who you are (name, job, family, recurring context)
    preferences.md  — how you like things done
    projects.md     — what you're working on right now
    inbox.md        — loose facts the assistant captured and hasn't filed yet
    journal/        — dated daily logs
"""

from __future__ import annotations

import datetime as dt

from . import config

SEED_FILES = {
    "profile.md": "# Profile\n\n(The assistant fills this in as it learns about you.)\n",
    "preferences.md": "# Preferences\n\n(How you like things done — tone, schedules, formats.)\n",
    "projects.md": "# Current Projects\n\n(Active projects and their status.)\n",
    "inbox.md": "# Memory Inbox\n\n(New facts land here before being filed.)\n",
}

# Keep startup context bounded; the agent can always Read the full files.
MAX_CHARS_PER_FILE = 8000


def seed() -> None:
    """Create the memory directory and starter files if they don't exist."""
    config.ensure_dirs()
    for name, content in SEED_FILES.items():
        path = config.MEMORY_DIR / name
        if not path.exists():
            path.write_text(content)


def load() -> str:
    """Concatenate memory files (and today's journal) for the system prompt."""
    seed()
    parts: list[str] = []
    for name in SEED_FILES:
        path = config.MEMORY_DIR / name
        text = path.read_text().strip()
        if len(text) > MAX_CHARS_PER_FILE:
            text = text[:MAX_CHARS_PER_FILE] + "\n...(truncated — Read the file for the rest)"
        parts.append(f"<file path=\"{path}\">\n{text}\n</file>")
    today = _journal_path(dt.date.today())
    if today.exists():
        parts.append(f"<file path=\"{today}\">\n{today.read_text().strip()}\n</file>")
    return "\n\n".join(parts)


def remember(fact: str, category: str = "inbox") -> str:
    """Append a fact to a memory file. Returns the path written."""
    seed()
    name = {
        "profile": "profile.md",
        "preferences": "preferences.md",
        "projects": "projects.md",
        "inbox": "inbox.md",
    }.get(category, "inbox.md")
    path = config.MEMORY_DIR / name
    with path.open("a") as f:
        f.write(f"- {fact}  ({dt.date.today().isoformat()})\n")
    return str(path)


def journal(entry: str) -> str:
    """Append a timestamped entry to today's journal. Returns the path written."""
    config.ensure_dirs()
    path = _journal_path(dt.date.today())
    if not path.exists():
        path.write_text(f"# Journal — {dt.date.today().isoformat()}\n\n")
    with path.open("a") as f:
        f.write(f"- {dt.datetime.now().strftime('%H:%M')} — {entry}\n")
    return str(path)


def _journal_path(day: dt.date) -> "config.Path":
    return config.JOURNAL_DIR / f"{day.isoformat()}.md"
