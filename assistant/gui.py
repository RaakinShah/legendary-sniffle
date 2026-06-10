"""Native macOS chat window (pywebview / WKWebView) over the same agent core."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)

from . import config
from .agent import build_options

HTML_PATH = Path(__file__).parent / "static" / "chat.html"
_lock_file = None  # held for process lifetime (single-instance guard)


class Bridge:
    """JS <-> agent bridge. JS calls send(); we push output back via evaluate_js."""

    def __init__(self) -> None:
        self.window = None
        self.client: ClaudeSDKClient | None = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def _js(self, fn: str, payload: str) -> None:
        if self.window:
            self.window.evaluate_js(f"{fn}({json.dumps(payload)})")

    async def _client_ready(self) -> ClaudeSDKClient:
        if self.client is None:
            self.client = ClaudeSDKClient(options=build_options(partial_messages=True))
            await self.client.connect()
        return self.client

    async def _drop_client(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    async def _run(self, text: str) -> None:
        try:
            client = await self._client_ready()
            await client.query(text)
            async for m in client.receive_response():
                if isinstance(m, StreamEvent):
                    ev = m.event or {}
                    if ev.get("type") == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            self._js("streamText", delta["text"])
                elif isinstance(m, AssistantMessage):
                    for b in m.content:
                        if isinstance(b, TextBlock):
                            self._js("appendText", b.text)
                        elif isinstance(b, ToolUseBlock):
                            self._js("appendTool", b.name)
                elif isinstance(m, ResultMessage):
                    self._js("done", "" if m.subtype == "success" else m.subtype)
        except Exception as exc:  # surface the error, reset so the next message recovers
            await self._drop_client()
            self._js("appendText", f"⚠️ {exc}")
            self._js("done", "error")

    # --- methods callable from JS ---
    def send(self, text: str) -> str:
        asyncio.run_coroutine_threadsafe(self._run(text), self.loop)
        return "ok"

    def resize(self, width: int, height: int) -> str:
        if self.window:
            self.window.resize(int(width), int(height))
        return "ok"

    def new_chat(self) -> str:
        asyncio.run_coroutine_threadsafe(self._drop_client(), self.loop)
        return "ok"

    def hide_window(self) -> str:
        if self.window:
            self.visible = False
            self.window.hide()
        return "ok"

    def toggle_window(self) -> None:
        if not self.window:
            return
        if getattr(self, "visible", True):
            self.visible = False
            self.window.hide()
        else:
            self.visible = True
            self.window.show()
            self.window.evaluate_js("focusInput && focusInput()")

    def greet(self) -> str:
        return self.send(
            "Session started. Greet me briefly; if anything is overdue or due today, "
            "surface it in one or two lines. Then wait for my input."
        )


def run() -> None:
    if not config.auth_available():
        print(config.AUTH_HELP, file=sys.stderr)
        raise SystemExit(1)
    try:
        import webview
    except ImportError:
        print("pywebview not installed. Run: pip install -e '.[gui]'", file=sys.stderr)
        raise SystemExit(1)

    # Single instance: the login agent and a manual launch shouldn't both run.
    config.ensure_dirs()
    global _lock_file
    _lock_file = open(config.ASSISTANT_HOME / "gui.lock", "w")
    try:
        import fcntl
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"{config.ASSISTANT_NAME} is already running (press Option+Space).", file=sys.stderr)
        raise SystemExit(0)
    except ImportError:
        pass

    bridge = Bridge()
    # Siri-style summon pill, centered near the top of the screen; expands to a panel.
    pill_w, pill_h = 660, 92
    try:
        screen = webview.screens[0]
        x, y = (screen.width - pill_w) // 2, int(screen.height * 0.14)
    except Exception:
        x = y = None
    kwargs = dict(
        html=HTML_PATH.read_text(),
        js_api=bridge,
        width=pill_w,
        height=pill_h,
        x=x,
        y=y,
        frameless=True,
        easy_drag=False,           # drag via .pywebview-drag-region elements
        min_size=(380, 60),
    )
    if sys.platform == "darwin":
        # Native NSVisualEffectView blur behind a transparent page background.
        kwargs.update(vibrancy=True, transparent=True)
    try:
        bridge.window = webview.create_window(config.ASSISTANT_NAME, **kwargs)
    except TypeError:  # older pywebview without some kwargs
        for k in ("vibrancy", "transparent", "frameless", "easy_drag", "x", "y"):
            kwargs.pop(k, None)
        bridge.window = webview.create_window(config.ASSISTANT_NAME, **kwargs)
    bridge.visible = True
    webview.start(_install_hotkey, bridge)


def _install_hotkey(bridge: Bridge) -> None:
    """Global ⌥Space summon/dismiss. Needs Accessibility permission on macOS."""
    try:
        from pynput import keyboard
    except ImportError:
        return
    try:
        keyboard.GlobalHotKeys({"<alt>+<space>": bridge.toggle_window}).start()
    except Exception as exc:
        print(f"Hotkey unavailable: {exc}", file=sys.stderr)


if __name__ == "__main__":
    run()
