"""Configuration: paths, environment, and defaults.

All assistant state (memory, tasks, briefings) lives under ASSISTANT_HOME
(default ~/.assistant) so the code in this repo stays stateless and the
data survives reinstalls and machine moves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a .env file without overriding real env vars."""
    for candidate in (REPO_ROOT / ".env", Path.cwd() / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
        break


_load_dotenv()


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


ASSISTANT_HOME = _expand(os.environ.get("ASSISTANT_HOME", "~/.assistant"))
MEMORY_DIR = ASSISTANT_HOME / "memory"
JOURNAL_DIR = MEMORY_DIR / "journal"
BRIEFINGS_DIR = ASSISTANT_HOME / "briefings"
DB_PATH = ASSISTANT_HOME / "assistant.db"

ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Aide")
MODEL = os.environ.get("ASSISTANT_MODEL", "claude-opus-4-8")


def allowed_dirs() -> list[str]:
    """Extra directories the assistant may access (ASSISTANT_ALLOWED_DIRS, colon-separated)."""
    raw = os.environ.get("ASSISTANT_ALLOWED_DIRS", "")
    return [str(_expand(p)) for p in raw.split(":") if p.strip()]


def ensure_dirs() -> None:
    for d in (ASSISTANT_HOME, MEMORY_DIR, JOURNAL_DIR, BRIEFINGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_external_mcp_servers() -> dict:
    """Load external MCP server definitions (Gmail, Calendar, ...).

    Looks for mcp_servers.json in ASSISTANT_HOME first, then the repo root.
    The file uses the standard shape: {"mcpServers": {name: {command, args, env}}}.
    """
    for candidate in (ASSISTANT_HOME / "mcp_servers.json", REPO_ROOT / "mcp_servers.json"):
        if candidate.is_file():
            data = json.loads(candidate.read_text())
            servers = data.get("mcpServers", data)
            # Expand ${VAR} references in env blocks so secrets stay in .env
            for spec in servers.values():
                env = spec.get("env")
                if isinstance(env, dict):
                    for k, v in env.items():
                        if isinstance(v, str):
                            env[k] = os.path.expandvars(v)
            return servers
    return {}
