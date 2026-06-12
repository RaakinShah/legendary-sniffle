#!/usr/bin/env python3
"""Turnkey setup for Aide's Google Calendar and Gmail connectors.

Aide loads external MCP servers from ``~/.assistant/mcp_servers.json`` (see
``assistant.config.load_external_mcp_servers``) and exposes each server's tools
to the agent as ``mcp__<name>__*``. This script makes wiring up the two Google
connectors painless: it writes (or safely merges) the ``gcal`` and ``gmail``
server entries into that file using the exact command/args/env shape from
``mcp_servers.example.json``, checks the prerequisites (Node/npx and the Google
OAuth credentials), and verifies the result by reloading the config the same way
the running app does.

What this script does NOT do: it never runs the browser OAuth flow for you and
it makes no network or model calls. OAuth is your action. The community MCP
servers (``@gongrzhe/server-gmail-autoauth-mcp`` for Gmail and
``mcp-google-calendar-plus`` for Calendar) each walk you through Google sign-in
on their first launch, after Aide restarts. This script only prepares the config
and confirms the prerequisites are in place.

Prerequisites
-------------
* Node.js with ``npx`` on your PATH (the MCP servers run via ``npx``).
* A Google Cloud OAuth client of type "Desktop app", with its client ID and
  secret placed in the project ``.env`` as ``GOOGLE_CLIENT_ID`` and
  ``GOOGLE_CLIENT_SECRET``. (Gmail's server handles its own auth and does not
  strictly need these, but Calendar does. The script explains exactly what is
  missing.)

Usage
-----
    python3 scripts/connect_google.py            # configure both (default)
    python3 scripts/connect_google.py --gcal     # only Google Calendar
    python3 scripts/connect_google.py --gmail    # only Gmail
    python3 scripts/connect_google.py --both      # explicit "both"
    python3 scripts/connect_google.py --print     # preview, write nothing

After it writes the config, restart Aide. The new tools appear as
``mcp__gcal__*`` and ``mcp__gmail__*``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Make the project importable when run directly as ``python3 scripts/...`` so we
# share the single source of truth for paths and config loading with the app.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from assistant import config  # noqa: E402  (path bootstrap must run first)


# --- ANSI styling (matches the doctor's palette) -----------------------------

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _color(text: str, code: str) -> str:
    """Wrap ``text`` in an ANSI color, but only when stdout is a real terminal."""
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{RESET}"


def _ok(text: str) -> str:
    return _color(f"✓ {text}", GREEN)


def _warn(text: str) -> str:
    return _color(f"⚠ {text}", YELLOW)


def _fail(text: str) -> str:
    return _color(f"✗ {text}", RED)


def _info(text: str) -> str:
    return _color(f"• {text}", BLUE)


# --- the canonical server definitions ----------------------------------------
#
# These mirror mcp_servers.example.json exactly. gcal pulls its OAuth client
# credentials from the environment via ${VAR}, which config.load_external_mcp_servers
# expands at load time so the secrets live only in .env. Gmail's community server
# does its own auth and needs no env block here.

GOOGLE_ENV_VARS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")

SERVER_DEFS: dict[str, dict] = {
    "gmail": {
        "command": "npx",
        "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
    },
    "gcal": {
        "command": "npx",
        "args": ["-y", "mcp-google-calendar-plus"],
        "env": {
            "GOOGLE_CLIENT_ID": "${GOOGLE_CLIENT_ID}",
            "GOOGLE_CLIENT_SECRET": "${GOOGLE_CLIENT_SECRET}",
        },
    },
}

# Which connectors actually depend on the Google OAuth client credentials. Gmail
# manages its own auth, so we only hard-require these for gcal.
ENV_DEPENDENT = {"gcal"}

OAUTH_INSTRUCTIONS = """How to create the Google OAuth client (one-time, your action):

  1. Open the Google Cloud console:  https://console.cloud.google.com/
  2. Create a project (or pick one), then enable the APIs you want:
       - Gmail API           (for the gmail connector)
       - Google Calendar API (for the gcal connector)
     APIs & Services > Library, search each name, click Enable.
  3. Configure the OAuth consent screen (APIs & Services > OAuth consent screen):
       - User type "External" is fine for a personal account.
       - Add your own Google address under "Test users" so you can sign in
         while the app is unverified.
  4. Create the credential (APIs & Services > Credentials > Create credentials
     > OAuth client ID):
       - Application type: "Desktop app".
       - Copy the generated Client ID and Client secret.
  5. Put them in this project's .env file:
       GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
       GOOGLE_CLIENT_SECRET=your-client-secret
     (.env is gitignored, so the secret is never committed.)

  The actual browser sign-in happens later: the MCP server opens it on its first
  launch after you restart Aide. This script does not and cannot do that for you.
"""


# --- prerequisite detection --------------------------------------------------

def _node_runtime() -> tuple[bool, str]:
    """Return (available, detail) describing whether ``npx`` is on PATH.

    The MCP servers are launched via ``npx``, so without Node nothing runs.
    """
    npx = shutil.which("npx")
    if npx:
        node = shutil.which("node")
        node_note = f", node at {node}" if node else ", but `node` was not found"
        return True, f"npx found at {npx}{node_note}"
    return False, "npx not found on PATH (install Node.js, e.g. `brew install node`)"


def _present_google_vars() -> dict[str, bool]:
    """Map each Google OAuth env var to whether it currently resolves to a value.

    ``config`` already loaded the project ``.env`` at import time (its module-level
    ``_load_dotenv()`` call), so a value set there shows up in ``os.environ`` here.
    """
    return {var: bool(os.environ.get(var)) for var in GOOGLE_ENV_VARS}


# --- config file read / merge / write ----------------------------------------

def _config_path() -> Path:
    """The mcp_servers.json the running app reads first: inside ASSISTANT_HOME."""
    return config.ASSISTANT_HOME / "mcp_servers.json"


def _read_existing(path: Path) -> tuple[dict, dict]:
    """Read an existing config file.

    Returns ``(document, servers)`` where ``document`` is the full parsed JSON
    object (so we can preserve unrelated top-level keys like ``_readme``) and
    ``servers`` is the ``mcpServers`` block. A missing file yields empty dicts.

    Raises ``ValueError`` with a clear message if the file exists but is not
    valid JSON, so we never silently clobber a file we could not understand.
    """
    if not path.is_file():
        return {}, {}
    raw = path.read_text()
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path} exists but is not valid JSON ({exc}). "
            "Fix or remove it, then re-run this script."
        ) from exc
    if not isinstance(document, dict):
        raise ValueError(
            f"{path} does not contain a JSON object at the top level. "
            "Expected something like {\"mcpServers\": {...}}."
        )
    servers = document.get("mcpServers")
    if servers is None:
        # Tolerate the flat shape that load_external_mcp_servers also accepts.
        servers = {k: v for k, v in document.items() if isinstance(v, dict) and "command" in v}
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: the 'mcpServers' value must be an object.")
    return document, servers


def _build_document(existing_doc: dict, existing_servers: dict, selected: list[str]) -> dict:
    """Merge the selected connector definitions into the existing config.

    Other servers the user added are preserved untouched. Selected connectors are
    set to the canonical definition (a deep copy, so the module-level templates
    are never mutated by later ${VAR} expansion during verification).
    """
    merged_servers = dict(existing_servers)
    for name in selected:
        merged_servers[name] = json.loads(json.dumps(SERVER_DEFS[name]))

    document = dict(existing_doc)
    document["mcpServers"] = merged_servers
    document.setdefault(
        "_readme",
        "Written by scripts/connect_google.py. ${VARS} expand from your "
        "environment/.env at load time. See README 'Connecting your life'.",
    )
    return document


def _would_change(existing_servers: dict, selected: list[str]) -> list[str]:
    """Return the subset of ``selected`` whose definition differs from on disk.

    Lets us tell the user precisely what a write would add or replace, and skip
    prompting when nothing would actually change.
    """
    changed = []
    for name in selected:
        if existing_servers.get(name) != SERVER_DEFS[name]:
            changed.append(name)
    return changed


def _confirm(prompt: str) -> bool:
    """Ask a yes/no question. Defaults to "no" on EOF or a non-interactive stdin."""
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


# --- verification -------------------------------------------------------------

def _verify(selected: list[str]) -> tuple[bool, list[str]]:
    """Reload the config exactly as the app does and confirm the entries landed.

    Returns ``(ok, problems)``. ``ok`` is True when every selected connector is
    present after reload and, for env-dependent connectors, its ``${VAR}``
    references expanded to real values. Makes no network or model calls.
    """
    problems: list[str] = []
    try:
        servers = config.load_external_mcp_servers()
    except Exception as exc:  # noqa: BLE001 - surface any load failure clearly
        return False, [f"could not reload config: {exc}"]

    for name in selected:
        spec = servers.get(name)
        if spec is None:
            problems.append(f"{name}: missing after reload")
            continue
        env = spec.get("env") or {}
        for key, value in env.items():
            # An unexpanded reference means the underlying env var is not set.
            if isinstance(value, str) and (value == "" or value.startswith("${")):
                problems.append(f"{name}: env {key} did not expand (set it in .env)")
    return (not problems), problems


# --- top-level flow -----------------------------------------------------------

def _selected_connectors(args: argparse.Namespace) -> list[str]:
    """Resolve the flags into an ordered, de-duplicated connector list.

    Default (no connector flag) is both. ``--both`` is explicit both. ``--gmail``
    / ``--gcal`` may be combined.
    """
    if args.both or not (args.gmail or args.gcal):
        return ["gmail", "gcal"]
    chosen = []
    if args.gmail:
        chosen.append("gmail")
    if args.gcal:
        chosen.append("gcal")
    return chosen


def _report_prerequisites(selected: list[str]) -> bool:
    """Print the Node and credential checks. Returns True if all hard prereqs pass.

    A failing prerequisite does not abort configuration (the user may be setting
    up before installing Node, or before Gmail's own OAuth), but it is reported
    loudly with actionable next steps.
    """
    all_ok = True

    node_ok, node_detail = _node_runtime()
    print(_ok(f"Node runtime: {node_detail}") if node_ok
          else _fail(f"Node runtime: {node_detail}"))
    if not node_ok:
        all_ok = False

    needs_env = any(name in ENV_DEPENDENT for name in selected)
    if needs_env:
        present = _present_google_vars()
        missing = [var for var, ok in present.items() if not ok]
        if not missing:
            print(_ok("Google OAuth credentials: GOOGLE_CLIENT_ID and "
                      "GOOGLE_CLIENT_SECRET present"))
        else:
            all_ok = False
            print(_fail("Google OAuth credentials missing: " + ", ".join(missing)))
            print()
            print(OAUTH_INSTRUCTIONS)
    else:
        # Only Gmail selected: it manages its own auth, so the Google client
        # vars are not required, but mention how to verify if curious.
        print(_info("Gmail manages its own OAuth on first launch; no "
                    "GOOGLE_CLIENT_ID/SECRET required for it."))

    return all_ok


def _print_preview(document: dict, path: Path) -> None:
    """Show exactly what would be written, without touching the filesystem."""
    print(_info(f"Would write to: {path}"))
    print()
    print(json.dumps(document, indent=2))


def run(args: argparse.Namespace) -> int:
    """Execute the configured action. Returns a process exit code (0 = success)."""
    selected = _selected_connectors(args)
    path = _config_path()

    print(f"{_color('Aide Google connector setup', BOLD)}")
    print(_info(f"Connectors selected: {', '.join(selected)}"))
    print()

    # 1. Read whatever is already there (and fail loudly on a corrupt file).
    try:
        existing_doc, existing_servers = _read_existing(path)
    except ValueError as exc:
        print(_fail(str(exc)))
        return 1

    # 2. Build the merged document we would write.
    document = _build_document(existing_doc, existing_servers, selected)
    preserved = [n for n in existing_servers if n not in selected]
    if preserved:
        print(_info(f"Preserving existing connectors untouched: {', '.join(sorted(preserved))}"))

    # 3. Prerequisite report (informative; does not block configuration).
    prereqs_ok = _report_prerequisites(selected)
    print()

    # 4. Preview mode: show and stop.
    if args.print_only:
        _print_preview(document, path)
        print()
        print(_info("Preview only (--print): nothing was written."))
        return 0

    # 5. Decide whether a write is needed, and confirm before replacing.
    changed = _would_change(existing_servers, selected)
    if not changed:
        print(_ok(f"{path} already has the selected connectors configured; "
                  "nothing to write."))
    else:
        replacing = [n for n in changed if n in existing_servers]
        if path.is_file() and replacing:
            print(_warn("This will replace these existing entries: "
                        + ", ".join(sorted(replacing))))
            if not _confirm(f"Update {path}?"):
                print(_info("Aborted; no changes written."))
                return 1
        try:
            config.ASSISTANT_HOME.mkdir(parents=True, exist_ok=True)
            # Write atomically: a temp file in the same dir, then replace, so a
            # crash mid-write can never leave a half-written config.
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(document, indent=2) + "\n")
            tmp.replace(path)
        except OSError as exc:
            print(_fail(f"Could not write {path}: {exc}"))
            return 1
        print(_ok(f"Wrote {', '.join(changed)} to {path}"))

    # 6. Verify by reloading the config the way the app does.
    print()
    verified, problems = _verify(selected)
    if verified:
        print(_ok("Verified: connectors load and all env references resolve."))
    else:
        print(_warn("Verification found issues:"))
        for problem in problems:
            print(f"    {_color('-', DIM)} {problem}")

    # 7. Success summary and next step.
    print()
    namespaces = ", ".join(f"mcp__{name}__*" for name in selected)
    print(f"{_color('Tools that will become available:', BOLD)} {namespaces}")
    print(_info("Restart Aide so it reloads mcp_servers.json and picks up the "
                "new tools."))
    if selected:
        print(_info("On first use each Google server opens a browser sign-in; "
                    "complete it there. Aide always confirms before sending mail "
                    "or changing events."))

    # Exit non-zero only when something is actually broken: a missing hard
    # prerequisite or a failed verification. A clean preview/no-op stays 0.
    if not prereqs_ok or not verified:
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build and parse the CLI. Kept separate so it is easy to unit test."""
    parser = argparse.ArgumentParser(
        prog="connect_google.py",
        description="Set up Aide's Google Calendar and Gmail MCP connectors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 scripts/connect_google.py            # both connectors\n"
               "  python3 scripts/connect_google.py --gcal     # only Calendar\n"
               "  python3 scripts/connect_google.py --print    # preview only\n",
    )
    parser.add_argument("--gmail", action="store_true",
                        help="configure the Gmail connector")
    parser.add_argument("--gcal", action="store_true",
                        help="configure the Google Calendar connector")
    parser.add_argument("--both", action="store_true",
                        help="configure both connectors (the default)")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="show what would be written without writing it")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print()
        print(_info("Interrupted; no further changes made."))
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort guard with a clear message
        print(_fail(f"Unexpected error: {exc}"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
