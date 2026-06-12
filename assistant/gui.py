"""Native macOS desktop app (pywebview / WKWebView) over the agent core.

A normal resizable window — sidebar + chat, edge to edge, like a real app.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, engine, history, observer
from .log import get_logger

log = get_logger(__name__)

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
        self.eng = None            # backend-agnostic Engine, created per conversation
        self.conv_id: int | None = None
        self.session_id: str | None = None
        self._buf = ""              # current assistant reply, for persistence
        self._turn_fut = None       # in-flight turn future, for the Stop button
        self._connect_lock: asyncio.Lock | None = None
        self.eng_stale = False      # set by new_chat/open_conversation; see _drop_stale
        self._last_active = time.monotonic()   # for the idle-engine release (B1)
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        # Release the warm engine (and its ~150 MB bundled subprocess) after an
        # idle stretch; it re-warms lazily on the next message. 0 disables.
        if config.IDLE_RELEASE_MINUTES:
            asyncio.run_coroutine_threadsafe(self._idle_watcher(), self.loop)

    async def _idle_watcher(self) -> None:
        """Drop the engine after IDLE_RELEASE_MINUTES with no activity, so an
        idle Aide gives back the bundled `claude` subprocess instead of pinning
        it for the whole session."""
        threshold = config.IDLE_RELEASE_MINUTES * 60
        while True:
            await asyncio.sleep(60)
            if (self.eng is not None and not self._turn_busy()
                    and time.monotonic() - self._last_active > threshold):
                if self._connect_lock is None:
                    self._connect_lock = asyncio.Lock()
                async with self._connect_lock:
                    if self.eng is not None and not self._turn_busy():
                        log.info("releasing idle engine after %d min",
                                 config.IDLE_RELEASE_MINUTES)
                        await self._drop_engine()

    def _js(self, fn: str, payload: str) -> None:
        # The window can close between the check and the call (worker thread vs
        # UI thread), and evaluate_js raises on a dead window. Never let that
        # propagate into the streaming coroutine.
        if self.window:
            try:
                self.window.evaluate_js(f"{fn}({json.dumps(payload)})")
            except Exception:  # noqa: BLE001 - window likely closed mid-call
                log.debug("evaluate_js failed (window gone?)", exc_info=True)

    def _resume_messages(self) -> list[dict] | None:
        """Prior turns of the current conversation, shaped for the Ollama backend
        to resume context: [{"role": "user"|"assistant", "content": str}, ...].

        Best-effort and defensive: returns None if there's no current conv_id or
        nothing usable. The Claude backend resumes via session_id instead, so it
        doesn't need this.
        """
        if self.conv_id is None:
            return None
        try:
            data = history.get(int(self.conv_id))
            msgs = (data or {}).get("messages") or []
            out: list[dict] = []
            for m in msgs:
                role = m.get("role")
                text = m.get("text")
                if role in ("user", "assistant") and text:
                    out.append({"role": role, "content": text})
            return out or None
        except Exception:  # noqa: BLE001 - resume is best-effort; start fresh on failure
            log.warning("could not load resume messages for conv %s", self.conv_id,
                        exc_info=True)
            return None

    async def _engine_ready(self):
        # One build at a time: prewarm and the first message must not race
        # (the loser would otherwise see a not-yet-ready engine).
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            # A new_chat/open_conversation may have invalidated the engine but
            # its scheduled teardown may not have run yet. Settle it here,
            # under the lock, so we never build on top of (or reuse) the old one.
            if self.eng_stale:
                await self._drop_engine()
                self.eng_stale = False
            if self.eng is None:
                # Only the local backend resumes from stored messages; the Claude
                # backend resumes via session_id, so skip the DB fetch there.
                resume_msgs = (self._resume_messages()
                               if config.BACKEND != "claude" else None)
                eng = engine.make_engine(
                    resume_session=self.session_id,
                    resume_messages=resume_msgs,
                    partial=True,
                )
                await eng.warm()
                self.eng = eng        # publish only once warmed
        return self.eng

    async def _drop_engine(self) -> None:
        if self.eng is not None:
            try:
                await self.eng.aclose()
            except Exception:  # noqa: BLE001 - teardown failure shouldn't block a new turn
                log.warning("engine aclose failed", exc_info=True)
            self.eng = None

    async def _drop_stale(self) -> None:
        """Tear down an engine invalidated by new_chat/open_conversation.

        Serialized with _engine_ready via the connect lock, and gated on
        eng_stale: if a fast follow-up send() already dropped the stale engine
        and built a fresh one, this becomes a no-op instead of closing the new
        engine out from under it.
        """
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.eng_stale:
                await self._drop_engine()
                self.eng_stale = False

    def prewarm(self) -> None:
        asyncio.run_coroutine_threadsafe(self._engine_ready(), self.loop)

    def _with_context(self, text: str) -> str:
        """Littlebird-style: every message carries what the user is doing right now."""
        if sys.platform != "darwin" or not config.RECALL:
            return text
        ctx = observer.current_context()
        return f"{text}\n\n[Ambient context, auto-attached — not typed by the user: {ctx}]" if ctx else text

    async def _run(self, text: str, persist: bool = True) -> None:
        self._buf = ""
        self._last_active = time.monotonic()   # a turn counts as activity (B1)
        pending: list[str] = []      # coalesce streamed tokens into ~24-char batches

        def flush() -> None:
            if pending:
                self._js("streamText", "".join(pending))
                pending.clear()

        try:
            eng = await self._engine_ready()
            async for ev in eng.run(self._with_context(text)):
                if isinstance(ev, engine.Delta):
                    pending.append(ev.text)
                    if sum(map(len, pending)) >= 24:
                        flush()
                elif isinstance(ev, engine.ToolCall):
                    flush()
                    self._js("appendTool", ev.name)
                elif isinstance(ev, engine.Text):
                    flush()
                    self._js("appendText", ev.text)
                    self._buf += ("\n\n" if self._buf else "") + ev.text
                    if "credit balance is too low" in ev.text.lower():
                        self._js("appendText", LOW_CREDIT_TIP)
                elif isinstance(ev, engine.Done):
                    flush()
                    if ev.session_id:
                        self.session_id = ev.session_id
                        if self.conv_id:
                            history.set_session(self.conv_id, ev.session_id)
                    self._js("done", "" if ev.status == "success" else ev.status)
        except asyncio.CancelledError:
            # The user hit Stop. Keep what already streamed; the mid-stream
            # engine state is unknown, so drop it and rebuild on the next turn.
            flush()
            log.info("turn stopped by user")
            await self._drop_engine()
            self._js("done", "stopped")
        except Exception as exc:
            flush()
            log.exception("turn failed")
            await self._drop_engine()
            self._js(
                "appendText",
                "⚠️ Something went wrong with that turn. The details are in the "
                "log (~/.assistant/logs/aide.log). Try again, or start a new "
                "chat if it keeps happening.",
            )
            if "credit balance is too low" in str(exc).lower():
                self._js("appendText", LOW_CREDIT_TIP)
            self._js("done", "error")
        self._last_active = time.monotonic()   # reset the idle clock after the turn
        if persist and self.conv_id and self._buf:
            try:
                history.append(self.conv_id, "assistant", self._buf)
            except Exception:  # noqa: BLE001 - a DB hiccup must not kill the coroutine
                log.exception("could not persist reply for conv %s", self.conv_id)
                self._js("appendText",
                         "⚠️ This reply could not be saved to history.")

    # --- methods callable from JS ---
    def _turn_busy(self) -> bool:
        fut = self._turn_fut
        return fut is not None and not fut.done()

    def send(self, text: str) -> str:
        # One turn at a time: overlapping _run coroutines would share one SDK
        # client (overlapping query() is undefined behavior) and overwrite
        # _turn_fut, making the first turn uncancellable. The UI disables Send
        # while busy, but the bridge is the real gate.
        if self._turn_busy():
            return "busy"
        if self.conv_id is None:
            self.conv_id = history.create()
            history.set_title(self.conv_id, text)
        history.append(self.conv_id, "user", text)
        self._turn_fut = asyncio.run_coroutine_threadsafe(self._run(text), self.loop)
        return "ok"

    def greet(self) -> str:
        if self._turn_busy():
            return "busy"
        from . import agent
        self._turn_fut = asyncio.run_coroutine_threadsafe(
            self._run(agent.greeting_prompt(), persist=False),
            self.loop,
        )
        return "ok"

    def welcome_suggestions(self) -> list:
        """Context-aware quick actions for the hero, cheap and local (no model call)."""
        try:
            out: list[dict] = []

            # 1) Due tasks lead: the most actionable thing on the plate.
            try:
                from . import tasks
                due = tasks.due_soon(within_hours=24)
            except Exception:  # noqa: BLE001 - suggestions must never break the hero
                log.warning("welcome_suggestions: due_soon failed", exc_info=True)
                due = []
            if due:
                out.append({
                    "icon": "clock",
                    "label": f"What's due ({len(due)})",
                    "prompt": "What's due in the next 24 hours? Walk me through it briefly.",
                })

            # 2) Ambient continuity, when recall has a digest of recent work.
            try:
                digest = observer.latest_digest()
            except Exception:  # noqa: BLE001 - recall DB may be absent or locked
                log.warning("welcome_suggestions: latest_digest failed", exc_info=True)
                digest = None
            if digest:
                out.append({
                    "icon": "spark",
                    "label": "Pick up where I left off",
                    "prompt": "Based on your ambient recall of what I've been working "
                              "on, help me pick up where I left off. Use recall tools "
                              "if you need detail.",
                })

            # Connectors shape both the morning prompt and the chips below.
            try:
                servers = config.load_external_mcp_servers()
            except Exception:  # noqa: BLE001 - a bad mcp_servers.json is not fatal here
                log.warning("welcome_suggestions: load_external_mcp_servers failed",
                            exc_info=True)
                servers = {}

            # 3) Time of day.
            hour = datetime.now().hour
            if hour < 12:
                prompt = "Plan my day: check my tasks"
                if "gcal" in servers:
                    prompt += " and calendar"
                prompt += " and lay out a plan."
                out.append({"icon": "sun", "label": "Plan my day", "prompt": prompt})
            elif hour < 17:
                out.append({
                    "icon": "spark",
                    "label": "Summarize my afternoon",
                    "prompt": "Summarize what I've done so far today from recall, "
                              "then suggest the single best next action.",
                })
            else:
                out.append({
                    "icon": "moon",
                    "label": "Recap my day",
                    "prompt": "Recap my day from recall and tasks. What did I get "
                              "done, and what are tomorrow's top 3?",
                })

            # 4) Connector-backed chips.
            if "gmail" in servers:
                out.append({
                    "icon": "mail",
                    "label": "Check my email",
                    "prompt": "Check my recent unread email and surface anything "
                              "important.",
                })
            if "gcal" in servers:
                out.append({
                    "icon": "calendar",
                    "label": "What's on my calendar",
                    "prompt": "What's on my calendar for the rest of today and "
                              "tomorrow morning?",
                })

            # 5) The screen chip always closes the row (same prompt as askScreen),
            #    so cap the priority list at 3 and append it last.
            out = out[:3]
            out.append({
                "icon": "screen",
                "label": "What's on my screen",
                "prompt": "Capture my screen and help me with what I am currently "
                          "looking at. If it is obvious what I need, just do it; "
                          "otherwise summarize and offer next steps.",
            })
            return out
        except Exception:  # noqa: BLE001 - the hero falls back to its static chips
            log.exception("welcome_suggestions failed")
            return []

    def stop(self) -> str:
        """Cancel the in-flight turn (the Stop button). Whatever already
        streamed stays in the chat and is persisted."""
        fut = getattr(self, "_turn_fut", None)
        if fut is not None and not fut.done():
            fut.cancel()
            return "ok"
        return "idle"

    def rename_conversation(self, conv_id: int, title: str) -> str:
        title = (title or "").strip()
        if not title:
            return "empty"
        history.set_title(int(conv_id), title)
        return "ok"

    def export_conversation(self, conv_id: int) -> str:
        """Write the conversation as markdown under ~/.assistant/exports and
        reveal it in Finder. Returns the path (or '' if the conv is gone)."""
        data = history.get(int(conv_id))
        if not data:
            return ""
        out_dir = config.ASSISTANT_HOME / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() or c in "-_ " else "" for c in
                       (data["title"] or "chat")).strip().replace(" ", "-")[:40]
        path = out_dir / f"{slug or 'chat'}-{data['id']}.md"
        lines = [f"# {data['title'] or 'Conversation'}", ""]
        for m in data["messages"]:
            who = "You" if m["role"] == "user" else config.ASSISTANT_NAME
            lines += [f"**{who}:**", "", m["text"] or "", ""]
        path.write_text("\n".join(lines))
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        return str(path)

    # --- proactive feed (Routines panel) ---
    def proactive_feed(self) -> list:
        from .proactive import store
        return store.feed()

    def proactive_unread(self) -> int:
        from .proactive import store
        return store.unread_count()

    def proactive_mark_seen(self) -> str:
        from .proactive import store
        store.mark_seen()
        return "ok"

    def proactive_act(self, item_id: int) -> str:
        """Mark the insight done and hand its suggested action back to JS, which
        runs it through the normal send() path (so the user bubble renders)."""
        from .proactive import store
        item = store.get(int(item_id))
        if not item:
            return ""
        store.mark(int(item_id), "done")
        return item.get("action_prompt") or item.get("title") or ""

    def proactive_dismiss(self, item_id: int) -> str:
        from .proactive import store
        store.mark(int(item_id), "dismissed")
        return "ok"

    def proactive_snooze(self, item_id: int, hours: float = 3) -> str:
        from .proactive import store
        store.snooze(int(item_id), float(hours))
        return "ok"

    def proactive_feedback(self, item_id: int, good: bool) -> str:
        from .proactive import store
        store.set_feedback(int(item_id), bool(good))
        return "ok"

    def new_chat(self) -> str:
        self.conv_id = None
        self.session_id = None
        # Mark stale, then tear down on the loop. Whoever wins (this drop or
        # the next _engine_ready) does the close under the connect lock.
        self.eng_stale = True
        asyncio.run_coroutine_threadsafe(self._drop_stale(), self.loop)
        return "ok"

    def open_conversation(self, conv_id: int) -> dict:
        data = history.get(int(conv_id))
        if not data:
            return {}
        self.conv_id = data["id"]
        self.session_id = data["session_id"]
        # Same stale-flag handoff as new_chat; see _drop_stale.
        self.eng_stale = True
        asyncio.run_coroutine_threadsafe(self._drop_stale(), self.loop)
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

    def context_snapshot(self) -> dict:
        """What Aide is working from right now — rendered by the Context panel (⌘I)."""
        from . import agent
        return agent.context_snapshot()

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


def _set_app_name(name: str = None) -> None:
    """Make the macOS menu bar / Dock read the app name instead of "Python".

    When launched from the .app wrapper the real executable is the framework
    Python, so AppKit derives the app name from Python.app's bundle. Patching the
    main bundle's info dict before NSApplication starts overrides that. No-op off
    macOS / if AppKit is unavailable."""
    try:
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name or config.ASSISTANT_NAME
    except Exception:
        pass


def _aide_icon_path() -> "object":
    """Path to the installed Aide.app's icon, or None. The bundle ships an
    AppIcon.icns (built by scripts/build_app.py); we read it straight from
    /Applications or ~/Applications rather than depend on being launched from
    inside the bundle."""
    from pathlib import Path
    for base in (Path("/Applications"), Path.home() / "Applications"):
        icon = base / "Aide.app" / "Contents" / "Resources" / "AppIcon.icns"
        if icon.is_file():
            return icon
    return None


def _set_dock_icon() -> None:
    """Show Aide's icon in the Dock instead of the generic Python rocket.

    The .app wrapper execs the framework Python, so the running process re-binds
    to Python.app and the Dock would otherwise show Python's icon (the app NAME
    is already fixed by _set_app_name, but the icon is a separate binding).
    Setting the icon image on NSApplication overrides the Dock tile for every
    launch path. Best-effort: no-op off macOS or if the bundle isn't installed.
    Must run after NSApp exists, so it's called from _post_start."""
    icon = _aide_icon_path()
    if icon is None:
        return
    try:
        import AppKit
    except Exception:
        return

    def apply() -> None:
        try:
            app = AppKit.NSApp()
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))
            if app is not None and img is not None:
                app.setApplicationIconImage_(img)
        except Exception:
            return

    try:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception:
        try:
            apply()
        except Exception:
            return


def run() -> None:
    _set_app_name()
    ok, detail = config.backend_ready()
    if not ok:
        print(detail, file=sys.stderr)
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
        width=1080,
        height=760,
        min_size=(840, 560),
        background_color=_appearance_bg(),
    )
    webview.start(_post_start, bridge)
    _cleanup(bridge)


def _cleanup(bridge: Bridge) -> None:
    """After the window closes: drop the engine, stop the bridge loop, release
    the single-instance lock. Every step is guarded; never raises out of run()."""
    try:
        fut = asyncio.run_coroutine_threadsafe(bridge._drop_engine(), bridge.loop)
        fut.result(timeout=5)  # bounded wait so a hung teardown can't block exit
    except Exception:  # noqa: BLE001
        log.warning("engine teardown on exit failed or timed out", exc_info=True)
    try:
        bridge.loop.call_soon_threadsafe(bridge.loop.stop)
    except Exception:  # noqa: BLE001
        log.warning("could not stop bridge loop", exc_info=True)
    global _lock_file
    if _lock_file is not None:
        try:
            import fcntl
            fcntl.flock(_lock_file, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass  # no fcntl on this platform, or lock already gone
        try:
            _lock_file.close()
        except Exception:  # noqa: BLE001
            log.warning("could not close lock file", exc_info=True)
        _lock_file = None


def _appearance_bg() -> str:
    """Window background matched to the current macOS appearance, so there's no
    white flash before the page paints (and a sane base under vibrancy)."""
    dark = True
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2)
            dark = out.returncode == 0 and "Dark" in out.stdout
        except Exception:
            dark = True
    return "#1a1a1c" if dark else "#ffffff"


def _style_native_window() -> None:
    """Make the pywebview/WKWebView window read as a native Mac app.

    Applies a unified, transparent titlebar with the OS title hidden and a
    full-size content view, so the traffic-light buttons float over the
    top-left of the HTML sidebar (which reserves a ~30px top inset for them).
    Movable-by-background lets the chrome-less window be dragged from anywhere.

    A solid, flat sidebar is used by design: behind-window NSVisualEffectView
    vibrancy is intentionally NOT installed, because it only renders if the
    WKWebView is made non-opaque (setOpaque_(False)/drawsBackground=NO), which
    pywebview does not guarantee — every reviewer flagged that path as likely
    to render a flat/occluded sidebar. A correct flat sidebar beats broken
    vibrancy.

    Safe to call exactly once after webview.start(). The actual Cocoa mutation
    runs on the AppKit main thread (required); the whole thing is nested in
    try/except so it degrades silently if AppKit/pyobjc is unavailable or any
    individual call fails (e.g. off macOS, headless).
    """
    try:
        import AppKit
    except Exception:
        return

    NSWindowStyleMaskFullSizeContentView = 1 << 15
    NSWindowTitleHidden = 1  # NSWindowTitleVisibility.hidden

    def apply() -> None:
        try:
            app = AppKit.NSApp()
            if app is None:
                return
            for win in app.windows():
                try:
                    win.setTitlebarAppearsTransparent_(True)
                    win.setTitleVisibility_(NSWindowTitleHidden)
                    win.setStyleMask_(
                        win.styleMask() | NSWindowStyleMaskFullSizeContentView
                    )
                    win.setMovableByWindowBackground_(True)

                    # Keep the standard traffic-light buttons visible and let
                    # them repaint cleanly inside the now-transparent titlebar.
                    try:
                        for kind in (
                            AppKit.NSWindowCloseButton,
                            AppKit.NSWindowMiniaturizeButton,
                            AppKit.NSWindowZoomButton,
                        ):
                            b = win.standardWindowButton_(kind)
                            if b is not None and b.superview() is not None:
                                b.setHidden_(False)
                                b.superview().setNeedsDisplay_(True)
                    except Exception:
                        pass
                except Exception:
                    # one bad window shouldn't stop the rest
                    continue
        except Exception:
            return

    try:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception:
        # already on the main thread / no queue available: run directly
        try:
            apply()
        except Exception:
            return


def _inject_user_name(bridge) -> None:
    """Tell the page the user's display name so the serif hero can greet them
    by name ("Good evening, Raakin."). Resolves a name from $ASSISTANT_USER,
    else the macOS full-name / login account; the HTML degrades to a plain
    greeting if this is empty, so any failure here is harmless. Wrapped so it
    can never raise.
    """
    try:
        import json
        import os

        name = (os.environ.get("ASSISTANT_USER") or "").strip()
        if not name:
            try:
                # macOS full name (e.g. "Raakin Shah"); take the first token.
                import pwd

                gecos = pwd.getpwuid(os.getuid()).pw_gecos or ""
                name = gecos.split(",")[0].strip()
            except Exception:
                name = ""
        if not name:
            name = (os.environ.get("USER") or os.environ.get("LOGNAME") or "").strip()
        first = name.split()[0] if name else ""
        if first and getattr(bridge, "window", None) is not None:
            # refreshHeroName re-renders an already-shown hero greeting: the
            # launch hero races this injection, so the name may arrive after it.
            bridge.window.evaluate_js(
                f"window.AIDE_USER = {json.dumps(first)}; "
                "window.refreshHeroName && window.refreshHeroName()"
            )
    except Exception:
        return


def _apply_vibrancy(bridge) -> None:
    """Give the window a translucent macOS sidebar: install an NSVisualEffectView
    behind a non-opaque WKWebView, then tell the page (via the .vibrancy class) to
    let the sidebar go transparent. Everything is wrapped so it fails soft to the
    flat sidebar — no class is added unless the native side actually took.

    Reparents the existing WKWebView under the effect view (same instance, so the
    JS bridge and config are preserved); runs on the AppKit main thread.
    """
    if not config.VIBRANCY:
        return
    try:
        import AppKit
    except Exception:
        return
    win = getattr(bridge.window, "native", None)
    if win is None:
        return

    def apply() -> None:
        try:
            webview = win.contentView()
            if webview is None:
                return
            mask = AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
            effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(webview.frame())
            effect.setMaterial_(AppKit.NSVisualEffectMaterialSidebar)
            effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(AppKit.NSVisualEffectStateActive)
            effect.setAutoresizingMask_(mask)
            # Effect view becomes the content view; the webview rides on top of it,
            # non-opaque, so its transparent (sidebar) regions show the blur.
            win.setContentView_(effect)
            webview.setFrame_(effect.bounds())
            webview.setAutoresizingMask_(mask)
            effect.addSubview_(webview)
            webview.setOpaque_(False)
            try:
                webview.setValue_forKey_(False, "drawsBackground")
            except Exception:
                pass
            try:
                bridge.window.evaluate_js(
                    "document.documentElement.classList.add('vibrancy')"
                )
            except Exception:
                pass
        except Exception:
            return

    try:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception:
        try:
            apply()
        except Exception:
            return


def _post_start(bridge: Bridge) -> None:
    """After the window is up: connect the agent, wire recall + the ⌥Space hotkey."""
    # B1: do NOT pre-warm at launch. The engine (and its ~150 MB bundled
    # subprocess) is built lazily on the first message, and released again after
    # an idle stretch, so an idle Aide does not pin it. JS may call prewarm() on
    # input focus for a snappier first reply.

    # Give the window the unified native titlebar (traffic lights float over
    # the sidebar's reserved top inset). Safe no-op off macOS / without AppKit.
    if sys.platform == "darwin":
        _set_dock_icon()
        _style_native_window()
        _apply_vibrancy(bridge)

    # Set the user's first name BEFORE the page greets, so the serif hero can
    # personalize it. Best-effort; the page degrades gracefully if it's unset.
    _inject_user_name(bridge)

    if sys.platform == "darwin":
        # Hide Aide's own window during a screen capture, for BOTH tool paths:
        # mac_tools (Claude/MCP backend) and toolkit (local Ollama backend).
        from . import mac_tools, toolkit
        mac_tools.before_capture = toolkit.before_capture = bridge.window.hide
        mac_tools.after_capture = toolkit.after_capture = bridge.window.show
        observer.start()

    # The ⌥Space global hotkey is opt-in: pynput's listener can hard-crash the
    # process on modern macOS (Text Services Manager off the main thread). Off by
    # default so the app is stable; enable with ASSISTANT_HOTKEY=1.
    if not config.HOTKEY:
        return
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
