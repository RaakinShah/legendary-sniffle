"""GUI Bridge driven with a stub window and stub engine (no webview, no model)."""

import asyncio
import json


class StubWindow:
    """Records every evaluate_js payload; hide/show are no-ops."""

    def __init__(self):
        self.calls = []

    def evaluate_js(self, s):
        self.calls.append(s)

    def hide(self):
        pass

    def show(self):
        pass


class StubEngine:
    """Engine double: run() replays a scripted event list, or raises."""

    def __init__(self, events=None, error=None):
        self.events = events or []
        self.error = error
        self.prompts = []
        self.closed = False

    async def run(self, text):
        self.prompts.append(text)
        if self.error is not None:
            raise self.error
        for ev in self.events:
            yield ev

    async def warm(self):
        pass

    async def aclose(self):
        self.closed = True


def _decode(call):
    """Split an evaluate_js string 'fn("payload")' into (fn, payload)."""
    fn, _, rest = call.partition("(")
    return fn, json.loads(rest[:-1])


def _make_bridge(monkeypatch, events=None, error=None):
    from assistant import config, gui

    # Keep _with_context inert on macOS: no ambient-context lookups in tests.
    monkeypatch.setattr(config, "RECALL", False)
    bridge = gui.Bridge()
    bridge.window = StubWindow()
    eng = StubEngine(events=events, error=error)
    bridge.eng = eng

    async def ready():
        return eng

    monkeypatch.setattr(bridge, "_engine_ready", ready)
    return bridge, eng


def test_stream_events_drive_js(monkeypatch):
    from assistant import engine

    deltas = ["one ", "two ", "three ", "four ", "five ", "six "]
    events = ([engine.Delta(d) for d in deltas]
              + [engine.ToolCall("list_tasks"),
                 engine.Text("All done."),
                 engine.Done("success")])
    bridge, _ = _make_bridge(monkeypatch, events=events)
    asyncio.run(bridge._run("hello", persist=False))

    calls = [_decode(c) for c in bridge.window.calls]
    streamed = [p for fn, p in calls if fn == "streamText"]
    assert "".join(streamed) == "".join(deltas)       # no token lost
    assert 0 < len(streamed) < len(deltas)            # deltas coalesced into batches
    assert ("appendTool", "list_tasks") in calls
    assert ("appendText", "All done.") in calls
    assert calls[-1] == ("done", "")                  # success maps to empty status


def test_engine_error_shows_friendly_message_and_drops_engine(monkeypatch):
    bridge, eng = _make_bridge(monkeypatch,
                               error=RuntimeError("kaboom-secret-detail"))
    asyncio.run(bridge._run("hello", persist=False))  # must not raise

    calls = [_decode(c) for c in bridge.window.calls]
    texts = [p for fn, p in calls if fn == "appendText"]
    assert any("log" in t for t in texts)             # points the user at the log
    joined = " ".join(p for _, p in calls)
    assert "kaboom-secret-detail" not in joined       # no raw exception in the UI
    assert "Traceback" not in joined
    assert calls[-1] == ("done", "error")
    assert bridge.eng is None and eng.closed          # broken engine torn down


def test_reply_persisted_to_history(monkeypatch):
    from assistant import engine, history

    events = [engine.Text("Saved reply."), engine.Done("success")]
    bridge, _ = _make_bridge(monkeypatch, events=events)
    bridge.conv_id = history.create()
    asyncio.run(bridge._run("hello", persist=True))

    msgs = history.get(bridge.conv_id)["messages"]
    assert {"role": "assistant", "text": "Saved reply."} in msgs


def test_persist_failure_warns_instead_of_raising(monkeypatch):
    from assistant import engine, gui, history

    events = [engine.Text("ephemeral"), engine.Done("success")]
    bridge, _ = _make_bridge(monkeypatch, events=events)
    bridge.conv_id = history.create()

    def boom(conv_id, role, text):
        raise RuntimeError("db locked")

    monkeypatch.setattr(gui.history, "append", boom)
    asyncio.run(bridge._run("hello", persist=True))   # DB hiccup must not escape

    calls = [_decode(c) for c in bridge.window.calls]
    assert any(fn == "appendText" and "could not be saved" in p
               for fn, p in calls)


def test_new_chat_resets_state_and_marks_engine_stale(monkeypatch):
    bridge, _ = _make_bridge(monkeypatch)

    # Stub the scheduled teardown so the stale flag isn't cleared by the
    # background loop between new_chat() and the asserts below.
    async def quiet_drop():
        pass

    monkeypatch.setattr(bridge, "_drop_stale", quiet_drop)
    bridge.conv_id = 7
    bridge.session_id = "sess-1"
    bridge.eng_stale = False

    assert bridge.new_chat() == "ok"
    assert bridge.conv_id is None
    assert bridge.session_id is None
    assert bridge.eng_stale is True   # next _engine_ready must rebuild


def test_stop_with_no_turn_reports_idle(monkeypatch):
    bridge, _ = _make_bridge(monkeypatch)
    assert bridge.stop() == "idle"


def test_export_conversation_writes_markdown(monkeypatch, tmp_path):
    from assistant import gui, history

    bridge, _ = _make_bridge(monkeypatch)
    cid = history.create()
    history.set_title(cid, "Pharm notes")
    history.append(cid, "user", "what blocks beta receptors?")
    history.append(cid, "assistant", "Propranolol is a nonselective beta blocker.")
    # No Finder reveal during tests.
    monkeypatch.setattr(gui.subprocess, "run", lambda *a, **k: None)

    path = bridge.export_conversation(cid)
    assert path.endswith(".md")
    text = open(path).read()
    assert "Pharm notes" in text
    assert "Propranolol" in text
    assert "**You:**" in text


def test_export_missing_conversation_returns_empty(monkeypatch):
    bridge, _ = _make_bridge(monkeypatch)
    assert bridge.export_conversation(99999) == ""


def test_rename_conversation_sets_title(monkeypatch):
    from assistant import history

    bridge, _ = _make_bridge(monkeypatch)
    cid = history.create()
    assert bridge.rename_conversation(cid, "  Cardio block  ") == "ok"
    assert history.get(cid)["title"] == "Cardio block"
    assert bridge.rename_conversation(cid, "   ") == "empty"
    assert history.get(cid)["title"] == "Cardio block"   # unchanged on empty
