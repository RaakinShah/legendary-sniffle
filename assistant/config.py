"""Configuration: paths, environment, and defaults.

All assistant state (memory, tasks, briefings) lives under ASSISTANT_HOME
(default ~/.assistant) so the code in this repo stays stateless and the
data survives reinstalls and machine moves.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    """Read an integer env var defensively: a bad value falls back to the
    default and an out-of-range value is clamped, each with a stderr warning,
    so a typo in .env can never crash the app at import. (Warnings go to
    stderr, not the logger: log.py imports config, so config can't log.)"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        print(f"warning: {name}={raw!r} is not an integer; using {default}",
              file=sys.stderr)
        return default
    if not (lo <= val <= hi):
        clamped = max(lo, min(val, hi))
        print(f"warning: {name}={val} outside [{lo}, {hi}]; clamped to {clamped}",
              file=sys.stderr)
        return clamped
    return val


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
# Say so once, instead of silently dropping the key.
if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("note: both CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are set; "
              "using the subscription token and ignoring the API key.", file=sys.stderr)
    os.environ.pop("ANTHROPIC_API_KEY", None)


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


# .resolve() so a symlinked home can't masquerade as a different location in
# path-containment checks elsewhere.
ASSISTANT_HOME = _expand(os.environ.get("ASSISTANT_HOME", "~/.assistant")).resolve()
MEMORY_DIR = ASSISTANT_HOME / "memory"
JOURNAL_DIR = MEMORY_DIR / "journal"
BRIEFINGS_DIR = ASSISTANT_HOME / "briefings"
INSIGHTS_DIR = ASSISTANT_HOME / "insights"
DB_PATH = ASSISTANT_HOME / "assistant.db"

# Shared "argument not given" sentinel (distinct from an explicit None).
UNSET = object()

ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Aide")
# Haiku is the default brain: strong enough to avoid the fabrication a small local
# model falls into, cheap relative to Opus/Sonnet, and it never touches local RAM.
MODEL = os.environ.get("ASSISTANT_MODEL", "claude-haiku-4-5")
# "high" is the quality/token-efficiency sweet spot; adaptive thinking spends
# reasoning tokens only when a task needs them. Crank to "xhigh" for hard work.
EFFORT = os.environ.get("ASSISTANT_EFFORT", "high")

# Which brain runs the assistant:
#   "claude" (default) — the Claude Agent SDK on MODEL above (Haiku); accurate,
#                        no local RAM, runs on your Claude subscription/API.
#   "ollama"           — a local model via Ollama; free/offline/private, but a
#                        small model hallucinates, so it's opt-in, not the default.
# Both share the same tools, memory, tasks, and recall; only the engine differs.
BACKEND = os.environ.get("ASSISTANT_BACKEND", "claude").strip().lower()
# "apple" = the on-device Apple Foundation model (macOS 26+, Apple Intelligence).
BACKEND = {"foundation": "apple", "fm": "apple", "apple-fm": "apple"}.get(BACKEND, BACKEND)
if BACKEND not in ("claude", "ollama", "apple"):
    print(f"warning: ASSISTANT_BACKEND={BACKEND!r} is not 'claude', 'ollama', or 'apple'; "
          "using 'claude'", file=sys.stderr)
    BACKEND = "claude"

# Opt the apple backend into Apple's Private Cloud Compute model — far more
# capable (Light/Moderate/Deep reasoning, 32K context, broad knowledge), still
# free, private, and key-less. It fronts the same LanguageModelSession API, so
# Aide adopts it with no code change the moment apple-fm-sdk exposes the binding;
# until then this safely falls back to the on-device model.
APPLE_CLOUD = os.environ.get("ASSISTANT_APPLE_CLOUD", "0").strip().lower() in ("1", "true", "on", "yes")

# Local (Ollama) settings. The model must support tool calling.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
# Default to a model that fits comfortably in 16 GB of RAM. llama3.1:8b (~4.9 GB)
# supports tool calling and leaves headroom for the OS and other apps. Bigger
# models like gpt-oss:20b (~13 GB) will thrash/swap on a 16 GB Mac and can freeze
# it — only switch to one if you have the RAM (ASSISTANT_OLLAMA_MODEL=...).
OLLAMA_MODEL = os.environ.get("ASSISTANT_OLLAMA_MODEL", "llama3.1:8b")
# Context window the local model runs with. Bigger = more memory/recall/tool output
# fits, but the KV cache grows with it and eats RAM. 8k is a safe default on 16 GB
# (system prompt + memory are only ~2-3k tokens); raise it if you have the RAM.
OLLAMA_NUM_CTX = _int_env("ASSISTANT_OLLAMA_NUM_CTX", 8192, 1024, 131072)
# How long Ollama keeps the model resident after a turn. Shorter frees RAM sooner
# on memory-tight machines; "0" unloads immediately, "30m"/"-1" keep it warm.
OLLAMA_KEEP_ALIVE = os.environ.get("ASSISTANT_OLLAMA_KEEP_ALIVE", "5m").strip()
# Hard cap on tool-call rounds in one turn, so a confused local model can't loop forever.
OLLAMA_MAX_STEPS = _int_env("ASSISTANT_OLLAMA_MAX_STEPS", 12, 1, 64)
# Reasoning models (gpt-oss, qwen3, deepseek-r1, magistral) think before answering,
# which markedly improves agentic tool use. "auto" enables it for known reasoners.
OLLAMA_THINK = os.environ.get("ASSISTANT_OLLAMA_THINK", "auto").strip().lower()
_THINKERS = ("gpt-oss", "qwen3", "deepseek-r1", "magistral", "phi4-reasoning", "thinking")


def wants_thinking(model: str) -> bool:
    """Whether to ask the model to reason ('think') before answering."""
    if OLLAMA_THINK in ("1", "true", "on", "yes"):
        return True
    if OLLAMA_THINK in ("0", "false", "off", "no"):
        return False
    return any(tag in model.lower() for tag in _THINKERS)


# The Haiku advisor. The local model is the base brain for every turn; a stronger
# cloud model (Claude Haiku, billed to the same Claude credentials as the "claude"
# backend) is pulled in only on the hard moments, two ways:
#   1. on demand — the local model calls the `ask_advisor` tool when it's stuck;
#   2. auto-rescue — if the local loop visibly fails (loops, repeats a failing
#      tool, hits the step cap, or errors), the turn is handed to the advisor to
#      finish with full tool access.
# Both need Claude auth (see auth_available) and the network; without either, the
# advisor silently stays out of the way and the local model answers alone.
ADVISOR = os.environ.get("ASSISTANT_ADVISOR", "1") != "0"
ADVISOR_MODEL = os.environ.get("ASSISTANT_ADVISOR_MODEL", "claude-haiku-4-5").strip()
# Auto-rescue (mechanism 2) on top of the always-available tool (mechanism 1).
ADVISOR_RESCUE = os.environ.get("ASSISTANT_ADVISOR_RESCUE", "1") != "0"
# A local tool call (name+args) repeating this many times counts as a stall and
# trips auto-rescue, even before the hard OLLAMA_MAX_STEPS cap.
ADVISOR_LOOP_LIMIT = _int_env("ASSISTANT_ADVISOR_LOOP_LIMIT", 3, 2, 10)


def advisor_available() -> bool:
    """True when the Haiku advisor can actually be reached: enabled, on the
    local backend (it makes no sense on top of the Claude backend), and with
    usable Claude credentials present."""
    return ADVISOR and BACKEND != "claude" and auth_available()


# On-demand escalation. Haiku runs every turn (fast, cheap, accurate enough), but
# hard turns deserve a stronger brain. The `think_harder` tool lets Haiku consult
# a bigger model mid-turn, and the Claude engine auto-escalates once if a turn
# errors. Both bill to the same Claude credentials. Disable with ASSISTANT_ESCALATE=0.
ESCALATE = os.environ.get("ASSISTANT_ESCALATE", "1") != "0"
# The everyday escalation target (think_harder level="sonnet", and auto-rescue).
ESCALATE_MODEL = os.environ.get("ASSISTANT_ESCALATE_MODEL", "claude-sonnet-4-6").strip()
# The heavyweight target for the hardest problems (think_harder level="opus").
ESCALATE_MODEL_MAX = os.environ.get("ASSISTANT_ESCALATE_MODEL_MAX", "claude-opus-4-8").strip()
# Reasoning effort the escalation model runs with (Sonnet/Opus support effort levels).
ESCALATE_EFFORT = os.environ.get("ASSISTANT_ESCALATE_EFFORT", "high").strip()


# Release the warm GUI engine (and its bundled `claude` subprocess, ~150 MB)
# after this many idle minutes; it re-warms lazily on the next message. 0 keeps
# it resident for the whole session (snappier first reply, more idle RAM).
IDLE_RELEASE_MINUTES = _int_env("ASSISTANT_IDLE_RELEASE_MIN", 10, 0, 240)


# Focus-aware proactive silence: when one of these apps is frontmost, the
# proactive runner holds back notification pings (the finding still lands in the
# feed; it pings once you leave the focus app). Aimed at moments where a banner
# is genuinely disruptive — a meeting or shared screen, presenting, or deep work
# — not at background video (his normal study state). Case-insensitive substring
# match on the frontmost app name. Override the whole list with
# ASSISTANT_FOCUS_APPS (comma-separated); set it empty to disable the silence.
_FOCUS_DEFAULT = (
    "zoom.us,Zoom,Microsoft Teams,Webex,Google Meet,Around,"   # meetings / shared screen
    "Keynote,Microsoft PowerPoint,"                              # presenting
    "Xcode,Terminal,iTerm2"                                      # deep work / long builds
)
FOCUS_APPS = frozenset(
    x.strip().lower()
    for x in os.environ.get("ASSISTANT_FOCUS_APPS", _FOCUS_DEFAULT).split(",")
    if x.strip()
)


def escalation_available() -> bool:
    """True when think_harder / auto-escalation can reach a stronger Claude model:
    enabled and with usable Claude credentials present. Backend-agnostic — the
    Claude backend always has auth, and the Ollama backend can escalate too."""
    return ESCALATE and auth_available()


# Full system access: the assistant can read/act anywhere in your home directory
# without per-action approval. Set ASSISTANT_FULL_ACCESS=0 to sandbox it back to
# ASSISTANT_HOME + ASSISTANT_ALLOWED_DIRS with edit confirmation.
FULL_ACCESS = os.environ.get("ASSISTANT_FULL_ACCESS", "1") != "0"

# Use the Gmail/Calendar connectors already authorized on the user's Claude
# account (read-only) — gives Aide mail/calendar access with zero Google OAuth
# setup, on the Claude backend. Set ASSISTANT_ACCOUNT_CONNECTORS=0 to disable.
ACCOUNT_CONNECTORS = os.environ.get("ASSISTANT_ACCOUNT_CONNECTORS", "1") != "0"

# Global ⌥Space hotkey (pynput). OFF by default: on macOS 14+/26, pynput's
# listener thread calls the Text Services Manager off the main thread, which the
# OS now aborts (SIGTRAP) — it crashes the whole app. Opt in with ASSISTANT_HOTKEY=1
# if your macOS tolerates it; otherwise just click the window to use Aide.
HOTKEY = os.environ.get("ASSISTANT_HOTKEY", "0").strip().lower() in ("1", "true", "on", "yes")

# Translucent macOS vibrancy sidebar (an NSVisualEffectView behind a non-opaque
# WKWebView). On by default; the GUI falls back to a clean flat sidebar if it
# can't be installed. Set ASSISTANT_VIBRANCY=0 to force the flat sidebar.
VIBRANCY = os.environ.get("ASSISTANT_VIBRANCY", "1") != "0"

# Ambient recall: background observer remembers what you were doing (local only).
RECALL = os.environ.get("ASSISTANT_RECALL", "1") != "0"
RECALL_OBSERVE_SECONDS = _int_env("ASSISTANT_OBSERVE_SECONDS", 5, 2, 120)
RECALL_RETAIN_HOURS = 24 * _int_env("ASSISTANT_RECALL_DAYS", 30, 1, 365)
# Disk cap for the recall screenshot directory. Time-based retention alone lets a
# busy month quietly grow into many GB; the prune pass deletes the oldest shots
# once the directory exceeds this.
RECALL_MAX_MB = _int_env("ASSISTANT_RECALL_MAX_MB", 1500, 100, 100_000)
# Capture cadence: an OCR shot at most every SHOT_SECONDS (heartbeat), and at
# least SHOT_MIN_GAP apart when window switches trigger extra shots. The OCR
# dedupe discards unchanged screens, so a fast heartbeat costs CPU, not disk.
# Defaults give near-continuous coverage; raise them on battery-sensitive setups.
RECALL_SHOT_SECONDS = _int_env("ASSISTANT_SHOT_SECONDS", 10, 2, 600)
RECALL_SHOT_MIN_GAP = _int_env("ASSISTANT_SHOT_MIN_GAP", 3, 1, 60)
# Ambient understanding: every N minutes a one-shot Haiku call distills the
# recent timeline + screen text into 1-2 sentences of "what the user is
# actually working on". That digest (not raw window titles) anchors the ambient
# context line and the working-memory panel. 0 disables it. Needs Claude auth.
RECALL_DIGEST_MINUTES = _int_env("ASSISTANT_DIGEST_MINUTES", 30, 0, 720)

# Apps/window-title substrings the observer must never record (case-insensitive),
# in addition to private/incognito browser windows. Credential managers,
# authenticators, and common banking/finance contexts are excluded by default;
# extend via env, comma-separated.
RECALL_EXCLUDE = frozenset(
    x.strip().lower()
    for x in (
        "1Password,Passwords,Keychain Access,Bitwarden,LastPass,Dashlane,"
        "KeePass,Enpass,Proton Pass,NordPass,Authenticator,Authy,"
        "bank,banking,chase.com,wellsfargo,fidelity,vanguard,schwab,"
        "robinhood,venmo,paypal,zelle,"
        + os.environ.get("ASSISTANT_RECALL_EXCLUDE", "")
    ).split(",")
    if x.strip()
)


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


def connectors_available() -> bool:
    """True if read-only calendar/email tools are reachable this run: either an
    external gcal/gmail MCP server is configured, or the Claude account
    connectors are enabled. Either way auth must be present to spend an engine
    turn. Proactive calendar/email checks gate on this so they don't silently
    no-op when the connectors moved from mcp_servers.json to the Claude account."""
    if not auth_available():
        return False
    if ACCOUNT_CONNECTORS:
        return True
    try:
        servers = load_external_mcp_servers()
    except Exception:  # noqa: BLE001 - unreadable config means no external connector
        return False
    return bool({"gmail", "gcal"} & set(servers))


def ollama_tags() -> tuple[bool, list[str], str]:
    """One probe of the Ollama server: (reachable, installed model names, error).
    Shared by ollama_ready() and the doctor so the server is hit once, not twice."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        return False, [], f"server not reachable ({exc})"
    names = sorted(m.get("name", "") for m in data.get("models", []))
    return True, names, ""


def ollama_ready() -> tuple[bool, str]:
    """Return (ok, detail). ok=True when the Ollama server answers and the
    configured model is installed; detail explains any problem."""
    reachable, names, err = ollama_tags()
    if not reachable:
        return False, err
    # Accept an exact match or the same model under the default ":latest" tag.
    base = OLLAMA_MODEL.split(":")[0]
    if OLLAMA_MODEL in names or any(n.split(":")[0] == base for n in names):
        return True, f"{OLLAMA_MODEL} ready"
    return False, f"model {OLLAMA_MODEL} not installed (run: ollama pull {OLLAMA_MODEL})"


def apple_ready() -> tuple[bool, str]:
    """Whether the Apple on-device Foundation model can run here."""
    if sys.platform != "darwin":
        return False, "the Apple backend needs macOS 26+ on Apple silicon"
    try:
        import apple_fm_sdk as fm
    except ImportError:
        return False, "apple-fm-sdk not installed (pip install apple-fm-sdk)"
    try:
        ok, reason = fm.SystemLanguageModel().is_available()
    except Exception as exc:  # noqa: BLE001
        return False, f"Apple Foundation model error: {exc}"
    return (True, "Apple on-device model ready") if ok else \
        (False, f"Apple Intelligence unavailable: {reason or 'enable it in System Settings'}")


def backend_ready() -> tuple[bool, str]:
    """Whether the configured backend can actually run. (ok, human-readable detail)."""
    if BACKEND == "claude":
        return (True, "Claude credentials present") if auth_available() else (False, AUTH_HELP)
    if BACKEND == "apple":
        return apple_ready()
    return ollama_ready()


def allowed_dirs() -> list[str]:
    """Extra directories the assistant may access (ASSISTANT_ALLOWED_DIRS, colon-separated)."""
    raw = os.environ.get("ASSISTANT_ALLOWED_DIRS", "")
    return [str(_expand(p)) for p in raw.split(":") if p.strip()]


def ensure_dirs() -> None:
    for d in (ASSISTANT_HOME, MEMORY_DIR, JOURNAL_DIR, BRIEFINGS_DIR, INSIGHTS_DIR):
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
