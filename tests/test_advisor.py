"""The Haiku advisor: availability gating and graceful degradation.

These never touch the network — they assert the fail-soft behavior that lets the
local model carry on when the advisor can't be reached."""

import asyncio

from assistant import advisor, config


def test_available_false_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR", False)
    assert advisor.available() is False


def test_available_false_without_auth(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR", True)
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(config, "auth_available", lambda: False)
    assert advisor.available() is False


def test_available_false_on_claude_backend(monkeypatch):
    # Advisor-on-top-of-local makes no sense when the base IS Claude.
    monkeypatch.setattr(config, "ADVISOR", True)
    monkeypatch.setattr(config, "auth_available", lambda: True)
    monkeypatch.setattr(config, "BACKEND", "claude")
    assert advisor.available() is False


def test_consult_degrades_without_auth(monkeypatch):
    # consult gates on Claude auth (not the Ollama-only advisor toggle), since
    # think_harder uses it on the Claude backend too. Tool-level gating lives at
    # the call sites (ask_advisor / think_harder registration).
    monkeypatch.setattr(config, "auth_available", lambda: False)
    out = asyncio.run(advisor.consult("How do I do X?"))
    assert isinstance(out, str) and "unavailable" in out.lower()


def test_consult_passes_model_and_effort(monkeypatch):
    # think_harder escalation must reach the SDK with the requested stronger
    # model and a real effort level; the default (Haiku) path must omit effort.
    monkeypatch.setattr(config, "auth_available", lambda: True)
    import claude_agent_sdk

    captured = {}

    def fake_query(*, prompt, options):
        captured["model"] = options.model
        captured["effort"] = getattr(options, "effort", None)

        async def gen():
            msg = claude_agent_sdk.AssistantMessage(
                content=[claude_agent_sdk.TextBlock(text="deep answer")], model=options.model)
            yield msg
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    out = asyncio.run(advisor.consult("hard question", model="claude-sonnet-4-6",
                                      effort="high"))
    assert out == "deep answer"
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["effort"] == "high"

    asyncio.run(advisor.consult("easy question"))
    assert captured["model"] == config.ADVISOR_MODEL
    assert captured["effort"] is None


def test_consult_rejects_empty_question(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR", True)
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(config, "auth_available", lambda: True)
    out = asyncio.run(advisor.consult("   "))
    assert "no question" in out.lower()


def test_consult_never_raises_on_sdk_failure(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR", True)
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(config, "auth_available", lambda: True)

    # Simulate the SDK query blowing up (offline / rate limited): consult must
    # return a note, not propagate the exception.
    import assistant.advisor as adv

    async def boom(*a, **k):
        raise RuntimeError("network down")
        yield  # pragma: no cover

    # query is imported lazily inside consult; patch it at the SDK module.
    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "query", boom)
    out = asyncio.run(adv.consult("real question"))
    assert "unavailable" in out.lower() and "network down" in out
