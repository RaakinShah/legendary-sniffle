"""Native macOS chat window (pywebview / WKWebView) over the same agent core."""

from __future__ import annotations

import asyncio
import json
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

from . import config, history, observer
from .agent import build_options

HTML_PATH = Path(__file__).parent / "static" / "chat.html"
_lock_file = None  # held for process lifetime (single-instance guard)


def _radius(height: int) -> float:
    """Capsule for the pill, rounded panel otherwise."""
    return height / 2.0 if height <= 110 else 28.0


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
        self.client: ClaudeSDKClient | None = None
        self.conv_id: int | None = None
        self.session_id: str | None = None
        self._buf = ""  # accumulates the current assistant reply for persistence
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def _js(self, fn: str, payload: str) -> None:
        if self.window:
            self.window.evaluate_js(f"{fn}({json.dumps(payload)})")

    async def _client_ready(self) -> ClaudeSDKClient:
        if self.client is None:
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
                        win.contentView().layer().setCornerRadius_(_radius(height))
                        eff = getattr(self, "_ns_effect", None)
                        if eff is not None:
                            eff.layer().setCornerRadius_(_radius(height))
                        win.setFrame_display_animate_(nf, True, True)  # smooth native morph
                        win.invalidateShadow()
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


def _native_glass(bridge: Bridge) -> None:
    """Real macOS material: NSVisualEffectView blur clipped to a rounded mask,
    with the native window shadow. Falls back silently to the CSS look."""
    try:
        import AppKit

        def apply():
            try:
                win = AppKit.NSApp().windows()[0]
                for w in AppKit.NSApp().windows():
                    if w.title() == config.ASSISTANT_NAME:
                        win = w
                        break
                content = win.contentView()
                content.setWantsLayer_(True)
                layer = content.layer()
                layer.setCornerRadius_(win.frame().size.height / 2.0)  # starts as pill
                layer.setMasksToBounds_(True)
                material = getattr(AppKit, "NSVisualEffectMaterialUnderWindowBackground",
                                   getattr(AppKit, "NSVisualEffectMaterialHUDWindow", 13))
                effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(content.bounds())
                effect.setMaterial_(material)
                effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
                effect.setState_(AppKit.NSVisualEffectStateActive)
                effect.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
                effect.setWantsLayer_(True)
                effect.layer().setCornerRadius_(win.frame().size.height / 2.0)
                effect.layer().setMasksToBounds_(True)
                content.addSubview_positioned_relativeTo_(effect, AppKit.NSWindowBelow, None)
                win.setHasShadow_(True)
                win.invalidateShadow()
                bridge._ns_window = win
                bridge._ns_effect = effect
                bridge.window.evaluate_js("window.nativeGlass && nativeGlass(true)")
            except Exception:
                pass
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception:
        pass


def _install_hotkey(bridge: Bridge) -> None:
    """Post-start setup: native glass, capture hooks, global ⌥Space summon/dismiss."""
    if sys.platform == "darwin":
        from . import mac_tools
        mac_tools.before_capture = bridge.window.hide
        mac_tools.after_capture = bridge.window.show
        observer.start()   # ambient recall (ASSISTANT_RECALL=0 to disable)
        _native_glass(bridge)
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
