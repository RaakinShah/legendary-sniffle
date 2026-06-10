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

LOW_CREDIT_TIP = """💡 **Your API account is out of credits.** To run me on your \
Claude Pro/Max subscription instead:
1. In Terminal: `claude setup-token` (install Claude Code first: `curl -fsSL https://claude.ai/install.sh | bash`)
2. In the project `.env`: add `CLAUDE_CODE_OAUTH_TOKEN=<that token>` and **delete the ANTHROPIC_API_KEY line**
3. Quit and reopen me (⌥Space)"""


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
                            if "credit balance is too low" in b.text.lower():
                                self._js("appendText", LOW_CREDIT_TIP)
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
                        radius = height / 2.0 if height <= 110 else 28.0
                        win.contentView().layer().setCornerRadius_(radius)
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
            self._update_radius(height)
        return "ok"

    def _update_radius(self, height: int) -> None:
        """Keep the native rounded mask in sync: capsule for the pill, 28pt panel."""
        win = getattr(self, "_ns_window", None)
        if win is None:
            return
        try:
            import AppKit

            def apply():
                try:
                    radius = height / 2.0 if height <= 110 else 28.0
                    win.contentView().layer().setCornerRadius_(radius)
                    win.invalidateShadow()
                except Exception:
                    pass
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
        except Exception:
            pass

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
            self.window.evaluate_js("window.summon ? summon() : (window.focusInput && focusInput())")

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
    bridge.visible = True
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
                content.addSubview_positioned_relativeTo_(effect, AppKit.NSWindowBelow, None)
                win.setHasShadow_(True)
                win.invalidateShadow()
                bridge._ns_window = win
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
