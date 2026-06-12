"""watch: ALL-CLEAR contract, notification wording, state-file dedupe, disable knob."""

import asyncio


class StubEngine:
    """Engine double: yields Text(reply) then Done("success")."""

    def __init__(self, reply="ALL-CLEAR"):
        self.reply = reply
        self.prompts = []
        self.closed = False

    async def warm(self):
        pass

    async def run(self, prompt):
        from assistant import engine

        self.prompts.append(prompt)
        yield engine.Text(self.reply)
        yield engine.Done("success")

    async def aclose(self):
        self.closed = True


def _wire(monkeypatch, stub):
    """Patch make_engine / backend_ready / notify; return (call counts, notes)."""
    from assistant import watch

    monkeypatch.delenv("ASSISTANT_WATCH_MINUTES", raising=False)
    calls = {"make_engine": 0}

    def fake_make_engine(**kwargs):
        calls["make_engine"] += 1
        return stub

    monkeypatch.setattr(watch.engine, "make_engine", fake_make_engine)
    monkeypatch.setattr(watch.config, "backend_ready", lambda: (True, "ok"))
    notes = []
    monkeypatch.setattr(watch.notify, "notify",
                        lambda title, message: notes.append((title, message)))
    return calls, notes


def test_all_clear_no_notification(monkeypatch, capsys):
    from assistant import watch

    stub = StubEngine(reply="ALL-CLEAR")
    _, notes = _wire(monkeypatch, stub)
    asyncio.run(watch.main())

    assert notes == []
    assert stub.closed                       # engine torn down even on success
    assert capsys.readouterr().out == ""     # quiet pass leaves no stdout noise


def test_findings_notify_with_first_line(monkeypatch, capsys):
    from assistant import watch

    stub = StubEngine(reply="You have a meeting in 30 min")
    _, notes = _wire(monkeypatch, stub)
    asyncio.run(watch.main())

    assert notes == [("Aide: needs your attention", "You have a meeting in 30 min")]
    assert "You have a meeting in 30 min" in capsys.readouterr().out


def test_same_reply_twice_dedupes(monkeypatch, capsys):
    from assistant import watch

    reply = "You have a meeting in 30 min"
    _, notes = _wire(monkeypatch, StubEngine(reply=reply))
    asyncio.run(watch.main())
    _, notes2 = _wire(monkeypatch, StubEngine(reply=reply))
    asyncio.run(watch.main())

    assert notes == [("Aide: needs your attention", reply)]
    assert notes2 == []                      # state-file dedupe across runs
    assert reply in capsys.readouterr().out  # still printed both times


def test_disabled_via_env_skips_engine(monkeypatch, capsys):
    from assistant import watch

    stub = StubEngine()
    calls, notes = _wire(monkeypatch, stub)
    monkeypatch.setenv("ASSISTANT_WATCH_MINUTES", "0")
    asyncio.run(watch.main())

    assert calls["make_engine"] == 0         # never builds an engine
    assert notes == []
    assert "watcher disabled" in capsys.readouterr().out
