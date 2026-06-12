"""Preflight check: `assistant-doctor`.

Verifies everything Aide needs before it can actually help — credentials, the
optional GUI/OCR stack, and the macOS privacy permissions that otherwise only
reveal themselves as a failed tool call mid-conversation (Screen Recording,
Automation, Full Disk Access). Read-only and offline: it makes no API calls and
changes nothing. Some macOS checks may surface a one-time permission dialog;
that is the point — grant it and Aide stops hitting that wall.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
_MARK = {
    OK: f"{GREEN}✓{RESET}",
    WARN: f"{YELLOW}⚠{RESET}",
    FAIL: f"{RED}✗{RESET}",
    INFO: f"{BLUE}•{RESET}",
}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, status: str, label: str, detail: str = "", hint: str = "") -> None:
        self.rows.append((status, label, detail, hint))

    def section(self, title: str) -> None:
        self.rows.append(("section", title, "", ""))

    def render(self) -> int:
        fails = warns = 0
        for status, label, detail, hint in self.rows:
            if status == "section":
                print(f"\n{BOLD}{label}{RESET}")
                continue
            fails += status == FAIL
            warns += status == WARN
            line = f"  {_MARK[status]} {label}"
            if detail:
                line += f"  {DIM}{detail}{RESET}"
            print(line)
            if hint and status in (WARN, FAIL):
                print(f"      {DIM}↳ {hint}{RESET}")
        print()
        if fails:
            print(f"{RED}{fails} blocking issue(s){RESET}"
                  + (f", {warns} warning(s)" if warns else "") + ".")
        elif warns:
            print(f"{YELLOW}All clear, {warns} warning(s) — optional features may be limited.{RESET}")
        else:
            print(f"{GREEN}Everything checks out. Aide is ready.{RESET}")
        return 1 if fails else 0


# --- individual checks -------------------------------------------------------

def _check_python(r: Report) -> None:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        r.add(OK, "Python", ver)
    else:
        r.add(FAIL, "Python", ver, "Aide needs Python 3.10+. Upgrade your interpreter.")


def _check_auth(r: Report) -> None:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        r.add(OK, "Credentials", "Claude subscription token (CLAUDE_CODE_OAUTH_TOKEN)")
    elif os.environ.get("ANTHROPIC_API_KEY"):
        r.add(OK, "Credentials", "Anthropic API key (ANTHROPIC_API_KEY)")
    elif (Path.home() / ".claude" / ".credentials.json").is_file():
        r.add(OK, "Credentials", "stored Claude Code login")
    elif (Path.home() / ".claude.json").is_file() and "oauthAccount" in (
        Path.home() / ".claude.json"
    ).read_text():
        r.add(OK, "Credentials", "stored Claude login (~/.claude.json)")
    else:
        r.add(FAIL, "Credentials", "none found",
              "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env "
              "(see README 'Auth'), or run `claude setup-token`.")


def _check_backend(r: Report) -> None:
    if config.BACKEND == "claude":
        _check_auth(r)
        return
    reachable, names, err = config.ollama_tags()   # one probe, reused below
    if not reachable:
        r.add(FAIL, "Backend", f"Ollama · {err}",
              "Start it (`ollama serve` or open the Ollama app) and pull the model "
              f"(`ollama pull {config.OLLAMA_MODEL}`). Or set ASSISTANT_BACKEND=claude.")
        return
    base = config.OLLAMA_MODEL.split(":")[0]
    if config.OLLAMA_MODEL in names or any(n.split(":")[0] == base for n in names):
        r.add(OK, "Backend", f"Ollama · {config.OLLAMA_MODEL} ready")
    else:
        r.add(FAIL, "Backend", f"Ollama · {config.OLLAMA_MODEL} not installed",
              f"Run: ollama pull {config.OLLAMA_MODEL}. Or set ASSISTANT_BACKEND=claude.")
    if names:
        r.add(INFO, "Ollama models", ", ".join(names))


def _check_advisor(r: Report) -> None:
    # The Haiku advisor only applies when a local model is the base brain.
    if config.BACKEND == "claude":
        return
    if not config.ADVISOR:
        r.add(INFO, "Advisor", "off (ASSISTANT_ADVISOR=0)")
        return
    if config.auth_available():
        rescue = "on" if config.ADVISOR_RESCUE else "off"
        r.add(OK, "Advisor",
              f"{config.ADVISOR_MODEL} · ask_advisor tool + auto-rescue {rescue}")
    else:
        r.add(WARN, "Advisor",
              f"{config.ADVISOR_MODEL} — enabled but no Claude credentials",
              "The local model runs alone until Claude auth is present (see Credentials "
              "above); then it can consult Haiku on hard turns. Set ASSISTANT_ADVISOR=0 "
              "to silence this.")


def _check_config(r: Report) -> None:
    if config.BACKEND == "claude":
        r.add(INFO, "Model", f"{config.MODEL}  (effort: {config.EFFORT})")
    else:
        think = "on" if config.wants_thinking(config.OLLAMA_MODEL) else "off"
        r.add(INFO, "Model",
              f"{config.OLLAMA_MODEL}  (ctx {config.OLLAMA_NUM_CTX}, reasoning {think})")
    home = config.ASSISTANT_HOME
    r.add(INFO if home.exists() else WARN, "Data home", str(home),
          "" if home.exists() else "Created on first run.")
    mode = "full access (acts across your home dir)" if config.FULL_ACCESS \
        else "sandboxed to data home + allowed dirs"
    r.add(INFO, "Access mode", mode)


def _check_gui_deps(r: Report) -> None:
    import importlib.util as iu

    def installed(mod: str) -> bool:
        try:
            return iu.find_spec(mod) is not None
        except (ImportError, ValueError):
            return False

    if installed("webview"):
        r.add(OK, "Desktop app", "pywebview installed")
    else:
        r.add(WARN, "Desktop app", "pywebview not installed",
              "Run `pip install -e '.[gui]'` to use the GUI (the CLI works without it).")
    if installed("pynput"):
        if config.HOTKEY:
            r.add(OK, "Global hotkey", "⌥Space enabled (ASSISTANT_HOTKEY=1)")
        else:
            r.add(INFO, "Global hotkey",
                  "off by default — pynput can crash on modern macOS; set ASSISTANT_HOTKEY=1 to try ⌥Space")
    else:
        r.add(WARN, "Global hotkey", "pynput not installed",
              "Part of the [gui] extra; without it ⌥Space won't summon Aide.")
    if sys.platform == "darwin":
        if installed("Vision"):
            r.add(OK, "Screen OCR", "pyobjc Vision available")
        else:
            r.add(WARN, "Screen OCR", "pyobjc-framework-Vision not installed",
                  "Ambient recall can still log apps, but won't OCR screen text. "
                  "Install the [gui] extra.")


def _osa(script: str, timeout: int = 6):
    try:
        return subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - report any failure mode
        return exc


def _check_screen_recording(r: Report) -> None:
    try:
        import Quartz  # type: ignore
        granted = bool(Quartz.CGPreflightScreenCaptureAccess())
        if granted:
            r.add(OK, "Screen Recording", "granted")
        else:
            r.add(WARN, "Screen Recording", "not granted",
                  "System Settings > Privacy & Security > Screen Recording — enable "
                  "your terminal/Aide. Needed for `capture_screen` and ambient recall.")
        return
    except Exception:
        pass
    # Fallback when Quartz isn't importable: a capture to /dev/null tells us enough.
    if not shutil.which("screencapture"):
        r.add(INFO, "Screen Recording", "cannot determine (no screencapture)")
        return
    try:
        out = subprocess.run(["screencapture", "-x", "-t", "jpg", "/tmp/.aide_doctor.jpg"],
                             capture_output=True, text=True, timeout=8)
        ok = Path("/tmp/.aide_doctor.jpg").exists()
        Path("/tmp/.aide_doctor.jpg").unlink(missing_ok=True)
        if ok and out.returncode == 0:
            r.add(OK, "Screen Recording", "capture succeeded")
        else:
            r.add(WARN, "Screen Recording", "capture failed",
                  "Grant Screen Recording in System Settings > Privacy & Security.")
    except Exception as exc:  # noqa: BLE001
        r.add(WARN, "Screen Recording", f"check error: {exc}")


def _check_automation(r: Report) -> None:
    res = _osa('tell application "System Events" to get name of '
               'first process whose frontmost is true')
    if isinstance(res, Exception):
        r.add(WARN, "Automation (AppleScript)", f"check error: {res}")
        return
    if res.returncode == 0 and res.stdout.strip():
        r.add(OK, "Automation (AppleScript)", f"working (front app: {res.stdout.strip()})")
    elif "-1743" in res.stderr or "Not authorized" in res.stderr:
        r.add(WARN, "Automation (AppleScript)", "not authorized",
              "System Settings > Privacy & Security > Automation — allow your terminal/Aide "
              "to control System Events (and Mail/Calendar/etc).")
    else:
        r.add(WARN, "Automation (AppleScript)", (res.stderr.strip() or "no response")[:80])


def _check_full_disk_access(r: Report) -> None:
    # Reading these paths requires Full Disk Access; PermissionError means it's off.
    probes = [
        Path.home() / "Library" / "Messages" / "chat.db",
        Path.home() / "Library" / "Mail",
        Path.home() / "Library" / "Safari" / "History.db",
    ]
    existing = [p for p in probes if p.exists()]
    if not existing:
        r.add(INFO, "Full Disk Access", "inconclusive (no protected data on this Mac)")
        return
    for p in existing:
        try:
            if p.is_dir():
                list(p.iterdir())
            else:
                with open(p, "rb") as fh:
                    fh.read(16)
            r.add(OK, "Full Disk Access", f"can read {p.name}")
            return
        except PermissionError:
            continue
        except Exception:
            continue
    r.add(WARN, "Full Disk Access", "blocked",
          "System Settings > Privacy & Security > Full Disk Access — enable your "
          "terminal/Aide to let it search Mail, Messages, and Safari history.")


def _check_recall(r: Report) -> None:
    if not config.RECALL:
        r.add(INFO, "Ambient recall", "disabled (ASSISTANT_RECALL=0)")
        return
    days = config.RECALL_RETAIN_HOURS // 24
    db = config.ASSISTANT_HOME / "recall.db"
    shots = config.ASSISTANT_HOME / "recall"
    n_shots = len(list(shots.glob("*.jpg"))) if shots.is_dir() else 0
    if db.exists():
        mb = db.stat().st_size / 1_000_000
        r.add(OK, "Ambient recall", f"on · {days}d retention · {mb:.1f} MB · {n_shots} screenshots")
    else:
        r.add(INFO, "Ambient recall", f"on · {days}d retention · no data yet")


def _check_mcp(r: Report) -> None:
    try:
        servers = config.load_external_mcp_servers()
    except Exception as exc:  # noqa: BLE001 - a malformed config shouldn't crash doctor
        r.add(FAIL, "MCP connectors", f"config error: {exc}",
              "Fix the JSON in mcp_servers.json.")
        return
    if servers:
        r.add(OK, "MCP connectors", ", ".join(sorted(servers)))
        # Flag env placeholders that never got filled in.
        missing = []
        for name, spec in servers.items():
            for k, v in (spec.get("env") or {}).items():
                if isinstance(v, str) and (v == "" or v.startswith("${")):
                    missing.append(f"{name}.{k}")
        if missing:
            r.add(WARN, "MCP env", "unresolved: " + ", ".join(missing),
                  "Set these in .env so ${VARS} expand.")
    else:
        r.add(INFO, "MCP connectors", "none configured "
              "(copy mcp_servers.example.json to enable Gmail/Calendar/etc.)")


def _check_connectors(r: Report) -> None:
    """Connector readiness: config file, Node/npx, and whether the OAuth env
    vars a connector references actually resolve.

    This complements ``_check_mcp`` (which lists configured servers) by checking
    the runtime prerequisites those servers need: ``npx`` on PATH to launch them,
    and resolved ``${VAR}`` env references (for example the Google OAuth client
    used by the ``gcal`` connector). When a connector is configured but a
    prerequisite is missing, it surfaces one clear, actionable line so the user
    is not left debugging a silent tool failure mid-conversation.
    """
    cfg_path = config.ASSISTANT_HOME / "mcp_servers.json"
    repo_cfg = config.REPO_ROOT / "mcp_servers.json"
    if cfg_path.is_file():
        r.add(INFO, "Connector config", str(cfg_path))
    elif repo_cfg.is_file():
        r.add(INFO, "Connector config", str(repo_cfg))
    else:
        r.add(INFO, "Connector config", "none "
              "(run `python3 scripts/connect_google.py` to set up Gmail/Calendar)")
        return

    try:
        servers = config.load_external_mcp_servers()
    except Exception as exc:  # noqa: BLE001 - a malformed config shouldn't crash doctor
        # _check_mcp already reports this; stay quiet here to avoid a duplicate.
        return
    if not servers:
        return

    # npx is required to launch any of these community servers.
    npx_servers = sorted(
        name for name, spec in servers.items() if spec.get("command") == "npx"
    )
    npx = shutil.which("npx")
    if not npx_servers:
        pass  # No npx-launched connectors; nothing to check here.
    elif npx:
        node = shutil.which("node")
        detail = f"npx on PATH ({npx})" + ("" if node else ", but `node` not found")
        r.add(OK if node else WARN, "Connector runtime (npx/Node)", detail,
              "" if node else "Install Node.js so the connector servers can run "
              "(e.g. `brew install node`).")
    else:
        r.add(FAIL, "Connector runtime (npx/Node)",
              "npx not found, but these connectors need it: " + ", ".join(npx_servers),
              "Install Node.js (e.g. `brew install node`); the MCP servers launch via npx.")

    # Per-connector env: flag any ${VAR} that never resolved to a real value.
    for name in sorted(servers):
        spec = servers[name]
        unresolved = [
            k for k, v in (spec.get("env") or {}).items()
            if isinstance(v, str) and (v == "" or v.startswith("${"))
        ]
        if unresolved:
            r.add(FAIL, f"Connector '{name}'",
                  "configured but env unresolved: " + ", ".join(unresolved),
                  f"Set {', '.join(unresolved)} in .env, then restart Aide "
                  "(or run `python3 scripts/connect_google.py` for guided setup).")


def main() -> int:
    print(f"{BOLD}{config.ASSISTANT_NAME} doctor{RESET} "
          f"{DIM}— {platform.system()} {platform.release()}{RESET}")
    r = Report()

    r.section("Core")
    _check_python(r)
    _check_backend(r)
    _check_advisor(r)
    _check_config(r)

    r.section("Optional stack")
    _check_gui_deps(r)
    _check_mcp(r)
    _check_connectors(r)
    _check_recall(r)

    if sys.platform == "darwin":
        r.section("macOS permissions")
        print(f"  {DIM}(a one-time approval dialog may appear — that's expected){RESET}")
        _check_screen_recording(r)
        _check_automation(r)
        _check_full_disk_access(r)

    return r.render()


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()
