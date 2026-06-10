"""macOS-only tools: on-screen awareness via screenshots.

Registered as MCP server "mac" (tools named mcp__mac__<name>) on darwin only.
Requires Screen Recording permission (System Settings > Privacy & Security).
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

MAX_WIDTH = 1440  # downscale captures so they stay cheap in context

# Hooks the GUI installs so captures see past Aide's own window.
before_capture = None  # callable: hide the window
after_capture = None   # callable: show it again


@tool(
    name="capture_screen",
    description=(
        "Capture the user's current screen and look at it. Call this whenever the user "
        "refers to what they're looking at, seeing, reading, or working on right now "
        "('what's on my screen', 'help me with this', 'summarize this page', 'reply to "
        "this message'). Returns a screenshot image you can analyze."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "display": {
                "type": "integer",
                "description": "Display number for multi-monitor setups (1 = main). Default 1.",
            }
        },
    },
)
async def capture_screen(args: dict[str, Any]) -> dict[str, Any]:
    display = int(args.get("display", 1))
    if before_capture:
        try:
            before_capture()
            await asyncio.sleep(0.35)  # let the window actually disappear
        except Exception:
            pass
    try:
        return await _capture(display)
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Screen capture failed: {exc}"}],
                "is_error": True}
    finally:
        if after_capture:
            try:
                after_capture()
            except Exception:
                pass


async def _capture(display: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        shot = Path(td) / "screen.png"
        # -x: no sound, -D: display number
        cap = subprocess.run(
            ["screencapture", "-x", "-D", str(display), str(shot)],
            capture_output=True, text=True,
        )
        if cap.returncode != 0 or not shot.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Screen capture failed. The app likely needs Screen Recording "
                        "permission: System Settings > Privacy & Security > Screen Recording "
                        f"— enable it for this app, then retry. ({cap.stderr.strip()})"
                    ),
                }],
                "is_error": True,
            }
        # Downscale + convert to JPEG to keep token cost low
        jpg = Path(td) / "screen.jpg"
        subprocess.run(
            ["sips", "--resampleWidth", str(MAX_WIDTH), "-s", "format", "jpeg",
             "-s", "formatOptions", "80", str(shot), "--out", str(jpg)],
            capture_output=True,
        )
        img = jpg if jpg.exists() else shot
        mime = "image/jpeg" if img == jpg else "image/png"
        data = base64.standard_b64encode(img.read_bytes()).decode()
    return {
        "content": [
            {"type": "image", "data": data, "mimeType": mime},
            {"type": "text", "text": "Screenshot captured — this is what the user sees right now."},
        ]
    }


def build_server():
    return create_sdk_mcp_server(name="mac", version="1.0.0", tools=[capture_screen])
