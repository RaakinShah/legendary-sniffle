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

from . import observer
from .util import text_result as _text

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


@tool(
    name="recall_timeline",
    description=(
        "Look back at what the user was doing on this Mac (ambient recall). Returns a "
        "timeline of apps and window titles. Use it when the user asks what they were "
        "working on, where they saw something, what that site/document was, or to "
        "reconstruct their day. Filter with `query` (matches app or window title)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "since_hours": {"type": "number", "description": "How far back to look (default 24, max 72)"},
            "query": {"type": "string", "description": "Case-insensitive filter, e.g. 'safari' or 'invoice'"},
        },
    },
)
async def recall_timeline(args: dict[str, Any]) -> dict[str, Any]:
    hours = min(float(args.get("since_hours", 24)), 72)
    return _text(observer.timeline(hours, str(args.get("query", ""))))


@tool(
    name="recall_search",
    description=(
        "Full-text search EVERYTHING that has appeared on the user's screen (OCR'd "
        "ambient screenshots, last ~30 days). The fastest way to find something the "
        "user saw but lost: an error message, a price, a name, a link, a document. "
        "Returns timestamped snippets; follow up with recall_screenshot to view the moment."
    ),
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Words to find, e.g. 'flight confirmation'"}},
        "required": ["query"],
    },
)
async def recall_search(args: dict[str, Any]) -> dict[str, Any]:
    return _text(observer.search_screen(str(args.get("query", ""))))


@tool(
    name="recall_pause",
    description=(
        "Pause or resume ambient recall (the background observer). Use when the user "
        "says to stop watching / pause tracking / 'don't record this' (pause), or to "
        "start again (resume)."
    ),
    input_schema={
        "type": "object",
        "properties": {"paused": {"type": "boolean", "description": "true = pause, false = resume"}},
        "required": ["paused"],
    },
)
async def recall_pause(args: dict[str, Any]) -> dict[str, Any]:
    state = observer.set_paused(bool(args.get("paused", True)))
    return _text("Ambient recall paused." if state else "Ambient recall resumed.")


@tool(
    name="recall_forget",
    description=(
        "Erase recent ambient recall (timeline, screen text, screenshots). Use when the "
        "user says 'forget what you just saw', 'wipe the last hour', or 'delete "
        "everything you've recorded'. hours=0 erases ALL recall."
    ),
    input_schema={
        "type": "object",
        "properties": {"hours": {"type": "number", "description": "How far back to erase; 0 = everything"}},
        "required": ["hours"],
    },
)
async def recall_forget(args: dict[str, Any]) -> dict[str, Any]:
    return _text(observer.forget(float(args.get("hours", 1))))


@tool(
    name="recall_screenshot",
    description=(
        "Retrieve the ambient screenshot closest to a time, to see what was on the "
        "user's screen back then. Use after recall_timeline narrows down WHEN something "
        "happened. `when` like '2026-06-10 14:30', or empty for the most recent."
    ),
    input_schema={
        "type": "object",
        "properties": {"when": {"type": "string", "description": "Approximate time, e.g. 2026-06-10 14:30"}},
    },
)
async def recall_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    path = observer.nearest_shot(str(args.get("when", "")))
    if not path:
        return _text("No ambient screenshots recorded yet.", is_error=True)
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    stamp = Path(path).stem
    return {
        "content": [
            {"type": "image", "data": data, "mimeType": "image/jpeg"},
            {"type": "text", "text": f"Screen at {stamp[:8]} {stamp[9:11]}:{stamp[11:13]}."},
        ]
    }


def build_server():
    return create_sdk_mcp_server(
        name="mac", version="1.0.0",
        tools=[capture_screen, recall_timeline, recall_search, recall_screenshot,
               recall_pause, recall_forget],
    )
