"""macOS notifications.

Prefer ``terminal-notifier`` with ``-sender`` so the banner is attributed to
Aide (its registered icon and name) instead of osascript's generic "Script
Editor" host. Fall back to ``osascript`` when terminal-notifier isn't installed.
No-op on non-macOS platforms.

Install the nicer path with:  brew install terminal-notifier
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Aide.app's bundle identifier. terminal-notifier's -sender borrows this app's
# registered icon and name for the banner, which is what makes it read "Aide".
_SENDER_BUNDLE_ID = "com.rashah04.aide"

# Drop a leading "Aide:" / "Aide -" that callers add for the osascript fallback:
# the terminal-notifier banner already shows "Aide" as the app, so the prefix
# would just repeat it. Only strips when a separator follows, so titles like
# "Aide needs attention" are left intact.
_AIDE_PREFIX = re.compile(r"^\s*Aide\s*[:\-–—]\s*")


def _terminal_notifier() -> "str | None":
    path = shutil.which("terminal-notifier")
    if path:
        return path
    for p in ("/opt/homebrew/bin/terminal-notifier", "/usr/local/bin/terminal-notifier"):
        if os.path.exists(p):
            return p
    return None


def _aide_installed() -> bool:
    return any((base / "Aide.app").is_dir()
               for base in (Path("/Applications"), Path.home() / "Applications"))


def notify(title: str, message: str) -> None:
    if sys.platform != "darwin":
        return
    tn = _terminal_notifier()
    if tn:
        clean = _AIDE_PREFIX.sub("", title).strip() or "Aide"
        args = [tn, "-title", clean, "-message", message]
        # Only claim Aide's identity when the bundle is actually installed;
        # otherwise let terminal-notifier post under its own icon.
        if _aide_installed():
            args += ["-sender", _SENDER_BUNDLE_ID]
        subprocess.run(args, check=False, capture_output=True)
        return
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
