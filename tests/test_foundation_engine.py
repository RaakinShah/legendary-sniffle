"""Apple Foundation Models backend.

The pure pieces (cumulative-stream delta, JSON-schema -> GenerationSchema) are
tested unconditionally where the SDK is present. The end-to-end inference test
is opt-in (ASSISTANT_TEST_APPLE=1) so the normal suite stays fast and offline.
"""

import asyncio
import os
import sys

import pytest

fm = pytest.importorskip("apple_fm_sdk", reason="apple-fm-sdk not installed")


def test_fm_delta_diffs_cumulative_snapshots():
    from assistant.engine import _fm_delta

    assert _fm_delta("", "Red") == "Red"
    assert _fm_delta("Red", "Red, yellow") == ", yellow"   # only the appended tail
    assert _fm_delta("Red, yellow", "Red, yellow, blue") == ", blue"
    # A snapshot that doesn't extend the previous one is emitted whole, not sliced.
    assert _fm_delta("abcdef", "xyz") == "xyz"


def test_fm_schema_maps_types_required_and_enum():
    from assistant.engine import _fm_schema

    params = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "the title"},
            "within_hours": {"type": "integer", "description": "hours"},
            "status": {"type": "string", "enum": ["open", "done", "all"]},
        },
        "required": ["title"],
    }
    schema = _fm_schema(fm, "add a task", params).to_dict()
    props = schema["properties"]
    assert props["title"]["type"] == "string"
    assert props["within_hours"]["type"] == "integer"
    # Enum collapses to a string whose description lists the choices.
    assert props["status"]["type"] == "string"
    assert "open, done, all" in props["status"]["description"]
    # Only the declared field is required; the rest are optional.
    assert schema["required"] == ["title"]


def test_make_engine_selects_foundation(monkeypatch):
    from assistant import config, engine

    monkeypatch.setattr(config, "BACKEND", "apple")
    eng = engine.make_engine()
    assert type(eng).__name__ == "FoundationModelsEngine"
    assert eng.dispatch and eng.specs        # shares the normal toolset
    assert any(t.name == "add_task" for t in eng._tools)


def test_fm_loop_guard_stops_repeated_calls():
    from assistant.engine import _fm_loop_message

    counts: dict = {}
    # Within the limit: keep running (None).
    assert _fm_loop_message(counts, "due_tasks", "{}", 3) is None      # 1
    assert _fm_loop_message(counts, "due_tasks", "{}", 3) is None      # 2
    assert _fm_loop_message(counts, "due_tasks", "{}", 3) is None      # 3
    msg = _fm_loop_message(counts, "due_tasks", "{}", 3)               # 4 -> stop
    assert msg is not None and "already called" in msg.lower()
    # Different args reset the count (tracked per signature).
    assert _fm_loop_message(counts, "due_tasks", '{"within_hours":48}', 3) is None
    # A different tool is independent.
    assert _fm_loop_message(counts, "list_tasks", "{}", 3) is None


def test_model_selection_prefers_pcc_when_present_and_opted_in(monkeypatch):
    """The Private Cloud Compute hook stays dormant until both the opt-in flag is
    set and the SDK exposes the class; otherwise it falls back to on-device."""
    from assistant import config, engine

    monkeypatch.setattr(config, "BACKEND", "apple")
    eng = engine.make_engine()

    class _PCC: pass
    class _Sys: pass

    class _FakeFM:
        PrivateCloudComputeLanguageModel = _PCC
        SystemLanguageModel = _Sys
    monkeypatch.setattr(eng, "_fm", _FakeFM)

    monkeypatch.setattr(config, "APPLE_CLOUD", True)
    assert isinstance(eng._make_model(), _PCC)        # opted in + present -> PCC

    monkeypatch.setattr(config, "APPLE_CLOUD", False)
    assert isinstance(eng._make_model(), _Sys)        # not opted in -> on-device

    del _FakeFM.PrivateCloudComputeLanguageModel
    monkeypatch.setattr(config, "APPLE_CLOUD", True)
    assert isinstance(eng._make_model(), _Sys)        # opted in but absent -> on-device


_run_live = os.environ.get("ASSISTANT_TEST_APPLE") == "1" and sys.platform == "darwin"


@pytest.mark.skipif(not _run_live, reason="set ASSISTANT_TEST_APPLE=1 to run on-device inference")
def test_foundation_engine_streams_and_calls_tool(monkeypatch):
    from assistant import config, engine, tasks

    if not fm.SystemLanguageModel().is_available()[0]:
        pytest.skip("Apple Intelligence not enabled")

    monkeypatch.setattr(config, "BACKEND", "apple")
    eng = engine.make_engine()

    async def go():
        kinds = []
        text = ""
        async for ev in eng.run("Add a task titled 'pay rent'."):
            kinds.append(type(ev).__name__)
            if isinstance(ev, engine.Text):
                text += ev.text
        await eng.aclose()
        return kinds, text

    kinds, _ = asyncio.run(go())
    assert "Done" in kinds
    # The model should have invoked add_task; the task store proves it landed.
    titles = [t.title.lower() for t in tasks.list_tasks(status="all")]
    assert any("rent" in t for t in titles), f"no task added; events={kinds}"
