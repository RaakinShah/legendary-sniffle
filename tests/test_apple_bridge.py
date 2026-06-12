"""The native Apple bridge engine.

The protocol handling is tested against a mock bridge (a tiny script speaking the
line-delimited JSON), so it runs anywhere with no Swift/SDK. The real end-to-end
path against the compiled binary is opt-in (ASSISTANT_TEST_APPLE=1).
"""

import asyncio
import os
import sys

import pytest

_MOCK = """#!/usr/bin/env python3
import sys, json
def send(o): print(json.dumps(o), flush=True)
for line in sys.stdin:
    m = json.loads(line); op = m.get("op")
    if op == "init":
        send({"type": "ready"})
    elif op == "turn":
        send({"type": "tool_call", "id": 1, "name": "add_task",
              "args": json.dumps({"title": "pay rent"})})
    elif op == "tool_result":
        send({"type": "delta", "text": "Added "})
        send({"type": "delta", "text": "the task."})
        send({"type": "final", "text": "Added the task."})
        send({"type": "done"})
    elif op == "shutdown":
        break
"""


def _mock_bridge(tmp_path):
    p = tmp_path / "mock-bridge"
    p.write_text(_MOCK)
    p.chmod(0o755)
    return p


def test_binary_path_points_at_bridge_dir():
    from assistant import apple_bridge
    assert apple_bridge.binary_path().name == "aide-fm"
    assert apple_bridge.binary_path().parent.name == "bridge"


def test_bridge_engine_tool_roundtrip(tmp_path, monkeypatch):
    from assistant import apple_bridge, engine, tasks

    mock = _mock_bridge(tmp_path)
    monkeypatch.setattr(apple_bridge, "binary_path", lambda: mock)

    eng = apple_bridge.AppleBridgeEngine(system="you are a task assistant", cloud=True)

    async def go():
        kinds, tools, text = [], [], ""
        async for ev in eng.run("remind me to pay rent"):
            kinds.append(type(ev).__name__)
            if isinstance(ev, engine.ToolCall):
                tools.append(ev.name)
            if isinstance(ev, engine.Text):
                text += ev.text
        await eng.aclose()
        return kinds, tools, text

    kinds, tools, text = asyncio.run(go())
    assert "ToolCall" in kinds and "Done" in kinds
    assert "add_task" in tools
    assert "Added the task." in text
    # The real handler ran: the task actually landed in the (isolated) store.
    assert any("pay rent" in t.title for t in tasks.list_tasks(status="all"))


def test_bridge_engine_curates_tools():
    from assistant import apple_bridge
    from assistant.engine import _APPLE_CORE_TOOLS

    eng = apple_bridge.AppleBridgeEngine(system="s", cloud=True)
    names = {s["function"]["name"] for s in eng.specs}
    assert names <= _APPLE_CORE_TOOLS                    # only the curated core
    assert "add_task" in names and "delete_task" not in names


_run_live = (os.environ.get("ASSISTANT_TEST_APPLE") == "1" and sys.platform == "darwin")


@pytest.mark.skipif(not _run_live, reason="set ASSISTANT_TEST_APPLE=1 + build bridge/aide-fm")
def test_bridge_live_on_device(monkeypatch):
    from assistant import apple_bridge, engine, tasks

    if not apple_bridge.available():
        pytest.skip("bridge/aide-fm not built")

    eng = apple_bridge.AppleBridgeEngine(
        system="You are a task assistant. When the user mentions a to-do, call add_task.",
        cloud=False)

    async def go():
        kinds = []
        async for ev in eng.run("Add a task titled 'pay rent'."):
            kinds.append(type(ev).__name__)
        await eng.aclose()
        return kinds

    kinds = asyncio.run(go())
    assert "Done" in kinds
    assert any("rent" in t.title.lower() for t in tasks.list_tasks(status="all")), kinds
