"""macOS notifications via osascript. No-op on other platforms."""

import json
import subprocess
import sys


def notify(title: str, message: str) -> None:
    if sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
