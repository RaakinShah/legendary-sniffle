"""OllamaEngine agent loop, driven by a mock Ollama client (no server needed)."""

import asyncio


def _tool_chunk(name, arguments):
    return {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": name, "arguments": arguments}}]},
            "done": True}


def _text_chunks(text):
    # stream the text in two pieces, last chunk flips done=True
    mid = len(text) // 2
    return [{"message": {"content": text[:mid]}},
            {"message": {"content": text[mid:]}, "done": True}]


def _fake_client(script):
    """script: list of chunk-lists, returned one per successive chat() call."""
    state = {"n": 0}

    async def chat(messages, tools=None, *, stream=True, think=None):
        i = min(state["n"], len(script) - 1)
        state["n"] += 1
        for chunk in script[i]:
            yield chunk

    return chat


def _run(eng, text):
    async def go():
        return [ev async for ev in eng.run(text)]
    return asyncio.run(go())


def _make_engine(script):
    from assistant import engine
    eng = engine.OllamaEngine(system="You are a test assistant.", mac=False)
    eng.client.chat = _fake_client(script)
    return eng


def test_plain_text_answer_streams_and_finishes():
    from assistant import engine

    eng = _make_engine([_text_chunks("Hi there.")])
    events = _run(eng, "hello")
    assert any(isinstance(e, engine.Delta) for e in events)
    assert any(isinstance(e, engine.Text) and e.text == "Hi there." for e in events)
    assert isinstance(events[-1], engine.Done) and events[-1].status == "success"
    assert not any(isinstance(e, engine.ToolCall) for e in events)


def test_tool_call_dispatches_then_answers():
    from assistant import engine, tasks

    eng = _make_engine([
        [_tool_chunk("add_task", {"title": "Buy milk", "due": "2026-06-12"})],
        _text_chunks("Added it."),
    ])
    events = _run(eng, "remind me to buy milk")
    names = [e.name for e in events if isinstance(e, engine.ToolCall)]
    assert names == ["add_task"]
    assert isinstance(events[-1], engine.Done) and events[-1].status == "success"
    # the tool actually ran against the real task store
    titles = [t.title for t in tasks.list_tasks(status="all")]
    assert "Buy milk" in titles
    # the tool result was fed back into the conversation
    assert any(m.get("role") == "tool" for m in eng.messages)


def test_string_arguments_are_parsed():
    from assistant import tasks

    eng = _make_engine([
        [_tool_chunk("add_task", '{"title": "Stringy"}')],   # arguments as a JSON string
        _text_chunks("ok"),
    ])
    _run(eng, "add a task")
    assert "Stringy" in [t.title for t in tasks.list_tasks(status="all")]


def test_unknown_tool_does_not_crash():
    from assistant import engine

    eng = _make_engine([
        [_tool_chunk("nonexistent_tool", {})],
        _text_chunks("recovered"),
    ])
    events = _run(eng, "go")
    assert isinstance(events[-1], engine.Done) and events[-1].status == "success"
    assert any(m.get("role") == "tool" and "unknown tool" in m["content"] for m in eng.messages)


def test_step_limit_stops_runaway_tool_loop():
    from assistant import engine

    # The model always asks for a tool — with no advisor (disabled in conftest)
    # the loop must give up, not spin forever.
    eng = _make_engine([[_tool_chunk("list_tasks", {})]])
    eng.max_steps = 3
    events = _run(eng, "loop")
    assert isinstance(events[-1], engine.Done) and events[-1].status == "error"
    assert sum(isinstance(e, engine.ToolCall) for e in events) <= 3


def test_ollama_error_surfaces_as_text_and_error_done():
    from assistant import engine
    from assistant.ollama import OllamaError

    async def boom(messages, tools=None, *, stream=True, think=None):
        raise OllamaError("server exploded")
        yield  # pragma: no cover - makes this an async generator

    eng = _make_engine([[]])
    eng.client.chat = boom
    events = _run(eng, "hi")
    assert any(isinstance(e, engine.Text) and "server exploded" in e.text for e in events)
    assert isinstance(events[-1], engine.Done) and events[-1].status == "error"


# --- auto-rescue (Haiku advisor takes over a stuck turn) ---------------------

def _stub_advisor_rescue(monkeypatch, reply="rescued answer"):
    """Enable the advisor and replace the Claude rescue engine with a fake that
    answers without any network. Returns nothing; just wires the monkeypatches."""
    from assistant import advisor, config, engine

    monkeypatch.setattr(config, "ADVISOR", True)
    monkeypatch.setattr(config, "ADVISOR_RESCUE", True)
    monkeypatch.setattr(advisor, "available", lambda: True)

    class FakeClaude:
        def __init__(self, *a, **k):
            pass

        async def run(self, text):
            yield engine.Text(reply)
            yield engine.Done("success")

        async def aclose(self):
            pass

    monkeypatch.setattr(engine, "ClaudeEngine", FakeClaude)


def test_auto_rescue_takes_over_on_step_limit(monkeypatch):
    from assistant import engine

    _stub_advisor_rescue(monkeypatch, reply="finished it for you")
    eng = _make_engine([[_tool_chunk("list_tasks", {})]])   # never stops on its own
    eng.max_steps = 3
    events = _run(eng, "do something hard")
    assert any(isinstance(e, engine.ToolCall) and e.name == "advisor" for e in events)
    assert any(isinstance(e, engine.Text) and "finished it for you" in e.text for e in events)
    assert isinstance(events[-1], engine.Done) and events[-1].status == "success"


def test_auto_rescue_takes_over_on_local_error(monkeypatch):
    from assistant import engine
    from assistant.ollama import OllamaError

    _stub_advisor_rescue(monkeypatch, reply="recovered from the crash")

    async def boom(messages, tools=None, *, stream=True, think=None):
        raise OllamaError("server exploded")
        yield  # pragma: no cover

    eng = _make_engine([[]])
    eng.client.chat = boom
    events = _run(eng, "hi")
    assert any(isinstance(e, engine.Text) and "recovered from the crash" in e.text for e in events)
    assert isinstance(events[-1], engine.Done) and events[-1].status == "success"
    # the local "server exploded" error must NOT also be surfaced once rescued
    assert not any(isinstance(e, engine.Text) and "server exploded" in e.text for e in events)


def test_rescue_reply_recorded_in_local_history(monkeypatch):
    from assistant import engine

    # After a rescue, the advisor's answer must land in the local message history
    # so the next local turn still has conversational continuity.
    _stub_advisor_rescue(monkeypatch, reply="the rescued answer")
    eng = _make_engine([[_tool_chunk("list_tasks", {})]])
    eng.max_steps = 2
    _run(eng, "hard question")
    assert eng.messages[-1] == {"role": "assistant", "content": "the rescued answer"}


def test_trim_keeps_system_and_cuts_on_turn_boundary(monkeypatch):
    from assistant import config, engine

    eng = _make_engine([[]])
    monkeypatch.setattr(config, "OLLAMA_NUM_CTX", 20)   # budget = 60 chars
    eng.messages = [
        {"role": "system", "content": "S" * 50},
        {"role": "user", "content": "old question " + "x" * 50},
        {"role": "assistant", "content": "old answer " + "y" * 50},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "", "tool_calls": [{}]},
        {"role": "tool", "content": "result", "tool_name": "list_tasks"},
    ]
    eng._trim()
    assert eng.messages[0]["role"] == "system"           # system always survives
    assert eng.messages[1]["role"] == "user"             # cut lands on a turn boundary
    assert eng.messages[1]["content"] == "new question"  # newest turn kept intact
    assert len(eng.messages) == 4


# --- Claude auto-escalation (failed turn retried on the stronger model) ------

def _stub_escalation(monkeypatch, reply="stronger answer"):
    """Enable escalation and replace the sub-ClaudeEngine with a fake that answers
    without any network. Returns the dict of kwargs the sub-engine was built with."""
    from assistant import config, engine

    monkeypatch.setattr(config, "ESCALATE", True)
    monkeypatch.setattr(config, "auth_available", lambda: True)
    captured: dict = {}

    class FakeSub:
        def __init__(self, *a, **k):
            captured.update(k)
            self.session_id = None

        async def run(self, text):
            self.session_id = "sub-session-xyz"
            yield engine.Text(reply)
            yield engine.Done("success", self.session_id)

        async def aclose(self):
            pass

    monkeypatch.setattr(engine, "ClaudeEngine", FakeSub)
    return captured


def test_claude_engine_escalates_failed_turn(monkeypatch):
    from assistant import config, engine

    eng = engine.ClaudeEngine()                  # default model -> may escalate
    captured = _stub_escalation(monkeypatch, reply="rescued by sonnet")

    async def boom():
        raise RuntimeError("api down")

    monkeypatch.setattr(eng, "_ready", boom)
    events = _run(eng, "hard question")
    assert any(isinstance(e, engine.Text) and "rescued by sonnet" in e.text for e in events)
    assert isinstance(events[-1], engine.Done) and events[-1].status == "success"
    # the raw error must not surface once the escalation answered
    assert not any(isinstance(e, engine.Text) and "api down" in e.text for e in events)
    assert captured["model"] == config.ESCALATE_MODEL
    assert captured["effort"] == config.ESCALATE_EFFORT
    # The outer engine adopts the sub's session: the next turn must resume the
    # conversation that contains the escalated exchange.
    assert eng.session_id == "sub-session-xyz"


def test_claude_subengine_never_reescalates(monkeypatch):
    from assistant import engine

    # An engine with an explicit model (advisor rescue / escalation sub-engine)
    # must surface its error, not recurse into another escalation.
    eng = engine.ClaudeEngine(model="claude-haiku-4-5")
    _stub_escalation(monkeypatch)

    async def boom():
        raise RuntimeError("api down")

    monkeypatch.setattr(eng, "_ready", boom)
    events = _run(eng, "hard question")
    assert any(isinstance(e, engine.Text) and "api down" in e.text for e in events)
    assert isinstance(events[-1], engine.Done) and events[-1].status == "error"


def test_repeated_tool_call_escalates_before_step_cap(monkeypatch):
    from assistant import config, engine

    _stub_advisor_rescue(monkeypatch, reply="loop broken")
    monkeypatch.setattr(config, "ADVISOR_LOOP_LIMIT", 2)
    eng = _make_engine([[_tool_chunk("list_tasks", {})]])
    eng.max_steps = 12        # high cap; the loop guard (limit 2) must fire first
    events = _run(eng, "loop")
    assert any(isinstance(e, engine.ToolCall) and e.name == "advisor" for e in events)
    assert isinstance(events[-1], engine.Done) and events[-1].status == "success"
    # escalated at exactly the loop limit, not the step cap
    assert sum(isinstance(e, engine.ToolCall) and e.name == "list_tasks" for e in events) == 2
