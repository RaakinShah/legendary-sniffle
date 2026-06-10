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

# If a subscription token is configured, it wins: the SDK would otherwise prefer
# ANTHROPIC_API_KEY, which surprises users whose API account has no credits.
if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
    os.environ.pop("ANTHROPIC_API_KEY", None)


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


ASSISTANT_HOME = _expand(os.environ.get("ASSISTANT_HOME", "~/.assistant"))
MEMORY_DIR = ASSISTANT_HOME / "memory"
JOURNAL_DIR = MEMORY_DIR / "journal"
BRIEFINGS_DIR = ASSISTANT_HOME / "briefings"
DB_PATH = ASSISTANT_HOME / "assistant.db"

ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Aide")
MODEL = os.environ.get("ASSISTANT_MODEL", "claude-opus-4-8")
# "high" is the quality/token-efficiency sweet spot; adaptive thinking spends
# reasoning tokens only when a task needs them. Crank to "xhigh" for hard work.
EFFORT = os.environ.get("ASSISTANT_EFFORT", "high")

# Full system access: the assistant can read/act anywhere in your home directory
# without per-action approval. Set ASSISTANT_FULL_ACCESS=0 to sandbox it back to
# ASSISTANT_HOME + ASSISTANT_ALLOWED_DIRS with edit confirmation.
FULL_ACCESS = os.environ.get("ASSISTANT_FULL_ACCESS", "1") != "0"

# Ambient recall: background observer remembers what you were doing (local only).
RECALL = os.environ.get("ASSISTANT_RECALL", "1") != "0"


AUTH_HELP = """No Claude credentials found. Two options:

  A) Use your Claude Pro/Max subscription (no API credits needed):
     1. Install Claude Code:  curl -fsSL https://claude.ai/install.sh | bash
     2. Run:  claude setup-token   (logs in via browser)
     3. Put the printed token in .env as CLAUDE_CODE_OAUTH_TOKEN=...
        (or just run `claude` once and log in — a stored login also works)

  B) Use an API key from https://console.anthropic.com:
     Copy .env.example to .env and set ANTHROPIC_API_KEY=...
"""


def auth_available() -> bool:
    """True if any usable Claude credential is present: API key, subscription
    OAuth token, or a stored Claude Code login on this machine."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    home = Path.home()
    if (home / ".claude" / ".credentials.json").is_file():
        return True
    cfg = home / ".claude.json"
    return cfg.is_file() and "oauthAccount" in cfg.read_text()


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
