"""Native macOS chat window (pywebview / WKWebView) over the same agent core."""

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
3. Quit and reopen me (⌥Space)"""


class Bridge:
    """JS <-> agent bridge. JS calls send(); we push output back via evaluate_js."""

    def __init__(self) -> None:
        self.window = None
        self.visible = True
        self.client: "ClaudeSDKClient | None" = None
        self.conv_id: int | None = None
        self.session_id: str | None = None
        self._buf = ""  # accumulates the current assistant reply for persistence
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def prewarm(self) -> None:
        """Start the agent connection in the background so the first reply is instant."""
        asyncio.run_coroutine_threadsafe(self._client_ready(), self.loop)

    def _js(self, fn: str, payload: str) -> None:
        if self.window:
            self.window.evaluate_js(f"{fn}({json.dumps(payload)})")

    async def _client_ready(self) -> "ClaudeSDKClient":
        if self.client is None:
            # Imported here, not at module top: the SDK (+ mcp) costs ~0.5s,
            # which would otherwise delay the window appearing at launch.
            from claude_agent_sdk import ClaudeSDKClient
            from .agent import build_options
            self.client = ClaudeSDKClient(
                options=build_options(partial_messages=True, resume=self.session_id)
            )
            await self.client.connect()
        return self.client

    async def _drop_client(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def _with_context(self, text: str) -> str:
        """Littlebird-style: every message carries what the user is doing right now."""
        if sys.platform != "darwin" or not config.RECALL:
            return text
        ctx = observer.current_context()
        if not ctx:
            return text
        return f"{text}\n\n[Ambient context, auto-attached — not typed by the user: {ctx}]"

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
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            self._js("streamText", delta["text"])
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
        except Exception as exc:  # surface the error, reset so the next message recovers
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
        # Ephemeral — not saved to history, no conversation created yet.
        asyncio.run_coroutine_threadsafe(
            self._run(
                "Session started. Greet me briefly; if anything is overdue or due today, "
                "surface it in one or two lines. If the ambient context shows I'm in the "
                "middle of something, acknowledge it naturally and offer to help with it. "
                "Then wait for my input.",
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
        asyncio.run_coroutine_threadsafe(self._drop_client(), self.loop)  # next send resumes
        return data

    def list_conversations(self) -> dict:
        return {"recents": history.recents(), "favorites": history.favorites()}

    def search_conversations(self, q: str) -> list:
        return history.search(q)

    def favorite_conversation(self, conv_id: int, favorite: bool) -> bool:
        return history.set_favorite(int(conv_id), bool(favorite))

    def rename_conversation(self, conv_id: int, title: str) -> str:
        history.set_title(int(conv_id), title)
        return "ok"

    def delete_conversation(self, conv_id: int) -> str:
        history.delete(int(conv_id))
        if self.conv_id == int(conv_id):
            self.new_chat()
        return "ok"

    def resize(self, width: int, height: int) -> str:
        width, height = int(width), int(height)
        win = getattr(self, "_ns_window", None)
        if win is not None:
            try:
                import AppKit

                def apply():
                    try:
                        f = win.frame()
                        # AppKit origin is bottom-left; keep the TOP edge fixed
                        nf = AppKit.NSMakeRect(
                            f.origin.x, f.origin.y + f.size.height - height, width, height
                        )
                        win.setFrame_display_animate_(nf, True, True)  # smooth native morph
                    except Exception:
                        pass
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
                return "ok"
            except Exception:
                pass
        if self.window:
            self.window.resize(width, height)
        return "ok"

    def toggle_recall(self) -> bool:
        """Flip the ambient observer from the UI. Returns True if now paused."""
        return observer.set_paused(not observer.paused)

    def hide_window(self) -> str:
        if self.window:
            self.visible = False
            self.window.hide()
        return "ok"

    def toggle_window(self) -> None:
        if not self.window:
            return
        if self.visible:
            self.visible = False
            self.window.hide()
        else:
            self.visible = True
            self.window.show()
            self.window.evaluate_js("window.summon ? summon() : (window.focusInput && focusInput())")


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
        easy_drag=True,            # drag anywhere; controls stop propagation in JS
        on_top=True,               # floats above other windows, like Siri
        transparent=True,          # window itself is invisible; CSS draws the glass
        min_size=(380, 60),
    )
    try:
        bridge.window = webview.create_window(config.ASSISTANT_NAME, **kwargs)
    except TypeError:  # older pywebview without some kwargs
        for k in ("transparent", "frameless", "easy_drag", "on_top", "x", "y"):
            kwargs.pop(k, None)
        bridge.window = webview.create_window(config.ASSISTANT_NAME, **kwargs)
    webview.start(_install_hotkey, bridge)


def _find_ns_window(bridge: Bridge) -> None:
    """Grab the NSWindow so resize() can animate frame changes. No layer tricks —
    the page is transparent and CSS draws the entire glass (shape, blur, shadow)."""
    try:
        import AppKit

        def apply():
            try:
                for w in AppKit.NSApp().windows():
                    if w.title() == config.ASSISTANT_NAME:
                        bridge._ns_window = w
                        break
            except Exception:
                pass
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception:
        pass


def _install_hotkey(bridge: Bridge) -> None:
    """Post-start setup: capture hooks, recall, client pre-warm, ⌥Space summon."""
    bridge.prewarm()   # connect the agent while the page is still loading
    if sys.platform == "darwin":
        from . import mac_tools
        mac_tools.before_capture = bridge.window.hide
        mac_tools.after_capture = bridge.window.show
        observer.start()   # ambient recall (ASSISTANT_RECALL=0 to disable)
        _find_ns_window(bridge)
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
