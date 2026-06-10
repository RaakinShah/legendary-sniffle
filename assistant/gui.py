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
    TextBlock,
    ToolUseBlock,
)

from . import config
from .agent import build_options

HTML_PATH = Path(__file__).parent / "static" / "chat.html"


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
            self.client = ClaudeSDKClient(options=build_options())
            await self.client.connect()
        return self.client

    async def _run(self, text: str) -> None:
        try:
            client = await self._client_ready()
            await client.query(text)
            async for m in client.receive_response():
                if isinstance(m, AssistantMessage):
                    for b in m.content:
                        if isinstance(b, TextBlock):
                            self._js("appendText", b.text)
                        elif isinstance(b, ToolUseBlock):
                            self._js("appendTool", b.name)
                elif isinstance(m, ResultMessage):
                    self._js("done", "" if m.subtype == "success" else m.subtype)
        except Exception as exc:  # surface errors in the UI instead of dying silently
            self._js("appendText", f"⚠️ {exc}")
            self._js("done", "error")

    # --- methods callable from JS ---
    def send(self, text: str) -> str:
        asyncio.run_coroutine_threadsafe(self._run(text), self.loop)
        return "ok"

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

    bridge = Bridge()
    kwargs = dict(
        html=HTML_PATH.read_text(),
        js_api=bridge,
        width=520,
        height=720,
        min_size=(380, 480),
    )
    if sys.platform == "darwin":
        # Native NSVisualEffectView blur behind a transparent page background.
        kwargs.update(vibrancy=True, transparent=True)
    try:
        bridge.window = webview.create_window(config.ASSISTANT_NAME, **kwargs)
    except TypeError:  # older pywebview without vibrancy/transparent kwargs
        kwargs.pop("vibrancy", None)
        kwargs.pop("transparent", None)
        bridge.window = webview.create_window(config.ASSISTANT_NAME, **kwargs)
    webview.start()


if __name__ == "__main__":
    run()
