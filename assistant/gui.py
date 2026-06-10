"""Native macOS desktop app (pywebview / WKWebView) over the agent core.

A normal resizable window — sidebar + chat, edge to edge, like a real app.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from . import config, history, observer

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

HTML_PATH = Path(__file__).parent / "static" / "chat.html"
_lock_file = None  # held for process lifetime (single-instance guard)

LOW_CREDIT_TIP = """💡 **Your API account is out of credits.** To run me on your \
Claude Pro/Max subscription instead:
1. In Terminal: `claude setup-token` (install Claude Code first: `curl -fsSL https://claude.ai/install.sh | bash`)
2. In the project `.env`: add `CLAUDE_CODE_OAUTH_TOKEN=<that token>` and **delete the ANTHROPIC_API_KEY line**
3. Quit and reopen me"""


class Bridge:
    """JS <-> agent bridge. JS calls send(); we push output back via evaluate_js."""

    def __init__(self) -> None:
        self.window = None
        self.visible = True
        self.client: "ClaudeSDKClient | None" = None
        self.conv_id: int | None = None
        self.session_id: str | None = None
        self._buf = ""              # current assistant reply, for persistence
        self._connect_lock: asyncio.Lock | None = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def _js(self, fn: str, payload: str) -> None:
        if self.window:
            self.window.evaluate_js(f"{fn}({json.dumps(payload)})")

    async def _client_ready(self) -> "ClaudeSDKClient":
        # One connect at a time: prewarm and the first message must not race
        # (the loser would otherwise see a not-yet-connected client → "Not connected").
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.client is None:
                from claude_agent_sdk import ClaudeSDKClient   # lazy: ~0.5s import
                from .agent import build_options
                client = ClaudeSDKClient(
                    options=build_options(partial_messages=True, resume=self.session_id)
                )
                await client.connect()
                self.client = client   # publish only once fully connected
        return self.client

    async def _drop_client(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def prewarm(self) -> None:
        asyncio.run_coroutine_threadsafe(self._client_ready(), self.loop)

    def _with_context(self, text: str) -> str:
        """Littlebird-style: every message carries what the user is doing right now."""
        if sys.platform != "darwin" or not config.RECALL:
            return text
        ctx = observer.current_context()
        return f"{text}\n\n[Ambient context, auto-attached — not typed by the user: {ctx}]" if ctx else text

    async def _run(self, text: str, persist: bool = True) -> None:
        from claude_agent_sdk import (
            AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock,
        )
        self._buf = ""
        try:
            client = await self._client_ready()
            await client.query(self._with_context(text))
            async for m in client.receive_response():
                if isinstance(m, StreamEvent):
                    ev = m.event or {}
                    if ev.get("type") == "content_block_delta":
                        d = ev.get("delta", {})
                        if d.get("type") == "text_delta" and d.get("text"):
                            self._js("streamText", d["text"])
                elif isinstance(m, AssistantMessage):
                    for b in m.content:
                        if isinstance(b, TextBlock):
                            self._js("appendText", b.text)
                            self._buf += ("\n\n" if self._buf else "") + b.text
                            if "credit balance is too low" in b.text.lower():
                                self._js("appendText", LOW_CREDIT_TIP)
                        elif isinstance(b, ToolUseBlock):
                            self._js("appendTool", b.name)
                elif isinstance(m, ResultMessage):
                    if m.session_id:
                        self.session_id = m.session_id
                        if self.conv_id:
                            history.set_session(self.conv_id, m.session_id)
                    self._js("done", "" if m.subtype == "success" else m.subtype)
        except Exception as exc:
            await self._drop_client()
            self._js("appendText", f"⚠️ {exc}")
            self._js("done", "error")
        if persist and self.conv_id and self._buf:
            history.append(self.conv_id, "assistant", self._buf)

    # --- methods callable from JS ---
    def send(self, text: str) -> str:
        if self.conv_id is None:
            self.conv_id = history.create()
            history.set_title(self.conv_id, text)
        history.append(self.conv_id, "user", text)
        asyncio.run_coroutine_threadsafe(self._run(text), self.loop)
        return "ok"

    def greet(self) -> str:
        asyncio.run_coroutine_threadsafe(
            self._run(
                "Session started. Greet me in one short line; if anything is overdue or due "
                "today, surface it. If the ambient context shows I'm mid-task, acknowledge it "
                "and offer to help. Then wait.",
                persist=False,
            ),
            self.loop,
        )
        return "ok"

    def new_chat(self) -> str:
        self.conv_id = None
        self.session_id = None
        asyncio.run_coroutine_threadsafe(self._drop_client(), self.loop)
        return "ok"

    def open_conversation(self, conv_id: int) -> dict:
        data = history.get(int(conv_id))
        if not data:
            return {}
        self.conv_id = data["id"]
        self.session_id = data["session_id"]
        asyncio.run_coroutine_threadsafe(self._drop_client(), self.loop)
        return data

    def list_conversations(self) -> dict:
        return {"recents": history.recents(), "favorites": history.favorites()}

    def search_conversations(self, q: str) -> list:
        return history.search(q)

    def favorite_conversation(self, conv_id: int, favorite: bool) -> bool:
        return history.set_favorite(int(conv_id), bool(favorite))

    def delete_conversation(self, conv_id: int) -> str:
        history.delete(int(conv_id))
        if self.conv_id == int(conv_id):
            self.new_chat()
        return "ok"

    def toggle_recall(self) -> bool:
        return observer.set_paused(not observer.paused)

    def toggle_window(self) -> None:
        if not self.window:
            return
        if self.visible:
            self.visible = False
            self.window.hide()
        else:
            self.visible = True
            self.window.show()
            self.window.evaluate_js("window.focusInput && focusInput()")


def run() -> None:
    if not config.auth_available():
        print(config.AUTH_HELP, file=sys.stderr)
        raise SystemExit(1)
    try:
        import webview
    except ImportError:
        print("pywebview not installed. Run: pip install -e '.[gui]'", file=sys.stderr)
        raise SystemExit(1)

    config.ensure_dirs()
    global _lock_file
    _lock_file = open(config.ASSISTANT_HOME / "gui.lock", "w")
    try:
        import fcntl
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"{config.ASSISTANT_NAME} is already running.", file=sys.stderr)
        raise SystemExit(0)
    except ImportError:
        pass

    bridge = Bridge()
    bridge.window = webview.create_window(
        config.ASSISTANT_NAME,
        html=HTML_PATH.read_text(),
        js_api=bridge,
        width=1040,
        height=720,
        min_size=(820, 540),
    )
    webview.start(_post_start, bridge)


def _post_start(bridge: Bridge) -> None:
    """After the window is up: connect the agent, wire recall + the ⌥Space hotkey."""
    bridge.prewarm()
    if sys.platform == "darwin":
        from . import mac_tools
        mac_tools.before_capture = bridge.window.hide
        mac_tools.after_capture = bridge.window.show
        observer.start()
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
