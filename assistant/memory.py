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

_CATEGORY_FILES = {
    "profile": "profile.md",
    "preferences": "preferences.md",
    "projects": "projects.md",
    "inbox": "inbox.md",
}


def _category_path(category: str) -> "config.Path":
    return config.MEMORY_DIR / _CATEGORY_FILES.get(category, "inbox.md")


def seed() -> None:
    """Create the memory directory and starter files if they don't exist."""
    config.ensure_dirs()
    for name, content in SEED_FILES.items():
        path = config.MEMORY_DIR / name
        if not path.exists():
            path.write_text(content)


def load() -> str:
    """Concatenate memory files (and the last few days of journal) for the
    system prompt. A multi-day journal window means yesterday's context is
    still in working memory this morning, instead of vanishing at midnight
    until the evening insights job re-distills it."""
    seed()
    parts: list[str] = []
    for name in SEED_FILES:
        path = config.MEMORY_DIR / name
        text = path.read_text().strip()
        if len(text) > MAX_CHARS_PER_FILE:
            text = text[:MAX_CHARS_PER_FILE] + "\n...(truncated — Read the file for the rest)"
        parts.append(f"<file path=\"{path}\">\n{text}\n</file>")
    budget = MAX_CHARS_PER_FILE          # shared cap across the journal window
    for days_ago in range(3):            # today, yesterday, the day before
        day = _journal_path(dt.date.today() - dt.timedelta(days=days_ago))
        if not day.exists() or budget <= 0:
            continue
        text = day.read_text().strip()[:budget]
        budget -= len(text)
        parts.append(f"<file path=\"{day}\">\n{text}\n</file>")
    return "\n\n".join(parts)


def remember(fact: str, category: str = "inbox") -> str:
    """Append a fact to a memory file. Returns the path written."""
    seed()
    path = _category_path(category)
    with path.open("a") as f:
        f.write(f"- {fact}  ({dt.date.today().isoformat()})\n")
    return str(path)


def update(category: str, find: str, replace: str) -> str:
    """Replace text in a memory file, so stale facts get revised instead of
    accumulating next to their corrections. Returns a result message."""
    seed()
    path = _category_path(category)
    text = path.read_text()
    n = text.count(find)
    if n == 0:
        return (f"Nothing matching {find!r} in {path.name}. Read the file to see "
                "its current wording, then try again with the exact text.")
    path.write_text(text.replace(find, replace))
    return f"Updated {path.name}: replaced {n} occurrence{'s' if n > 1 else ''}."


def forget_fact(category: str, text: str) -> str:
    """Remove memory bullet lines containing `text` (case-insensitive). Only
    bullet lines are touched, so headers and structure survive. Returns a
    result message."""
    seed()
    path = _category_path(category)
    needle = text.strip().lower()
    if not needle:
        return "Nothing to forget: no text given."
    lines = path.read_text().splitlines()
    kept = [l for l in lines
            if not (l.lstrip().startswith("-") and needle in l.lower())]
    removed = len(lines) - len(kept)
    if not removed:
        return f"Nothing matching {text!r} in {path.name}."
    path.write_text("\n".join(kept) + "\n")
    return f"Forgot {removed} {'entries' if removed > 1 else 'entry'} from {path.name}."


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
