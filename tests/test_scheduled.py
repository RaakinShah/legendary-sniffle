"""run_job with a stub engine: prompt rendering, kwargs, notify gating, backend gate."""

import asyncio

import pytest


class StubEngine:
    """Engine double: yields Text then Done("success"); optionally writes a file
    (standing in for a write_file tool call) so the notify gate can fire."""

    def __init__(self, reply="job output", create=None):
        self.reply = reply
        self.create = create
        self.prompts = []
        self.closed = False

    async def warm(self):
        pass

    async def run(self, prompt):
        from assistant import engine

        self.prompts.append(prompt)
        if self.create is not None:
            self.create.parent.mkdir(parents=True, exist_ok=True)
            self.create.write_text(self.reply)
        yield engine.Text(self.reply)
        yield engine.Done("success")

    async def aclose(self):
        self.closed = True


def _wire(monkeypatch, stub):
    """Patch make_engine / backend_ready / notify; return (captured kwargs, notes)."""
    from assistant import scheduled

    captured = {}

    def fake_make_engine(**kwargs):
        captured.update(kwargs)
        return stub

    monkeypatch.setattr(scheduled.engine, "make_engine", fake_make_engine)
    monkeypatch.setattr(scheduled.config, "backend_ready", lambda: (True, "ok"))
    notes = []
    monkeypatch.setattr(scheduled.notify, "notify",
                        lambda title, message: notes.append((title, message)))
    return captured, notes


def test_prompt_placeholders_substituted(tmp_path, monkeypatch):
    import datetime as dt

    from assistant import config, scheduled

    stub = StubEngine()
    _wire(monkeypatch, stub)
    out_dir = tmp_path / "briefings"
    asyncio.run(scheduled.run_job(
        label="briefing", out_dir=out_dir,
        prompt_template="On {date} write to {path}; memory lives in {memory_dir}.",
        max_turns=5, notify_title="t", notify_message="m {path}",
    ))

    today = dt.date.today().isoformat()
    prompt = stub.prompts[0]
    assert today in prompt
    assert str(out_dir / f"{today}.md") in prompt
    assert str(config.MEMORY_DIR) in prompt
    assert out_dir.is_dir()                # created before the engine runs


def test_model_and_effort_kwargs_reach_make_engine(tmp_path, monkeypatch):
    from assistant import scheduled

    stub = StubEngine()
    captured, _ = _wire(monkeypatch, stub)
    asyncio.run(scheduled.run_job(
        label="consolidation", out_dir=tmp_path / "out",
        prompt_template="x", max_turns=3,
        notify_title="t", notify_message="m",
        model="claude-opus-4-6", effort="high",
    ))

    assert captured["model"] == "claude-opus-4-6"
    assert captured["effort"] == "high"
    assert captured["unattended"] is True
    assert captured["max_turns"] == 3
    assert stub.closed                     # engine torn down even on success


def test_notify_fires_when_output_file_written(tmp_path, monkeypatch):
    import datetime as dt

    from assistant import scheduled

    out_dir = tmp_path / "out"
    out_path = out_dir / f"{dt.date.today().isoformat()}.md"
    stub = StubEngine(reply="briefing text", create=out_path)
    _, notes = _wire(monkeypatch, stub)
    asyncio.run(scheduled.run_job(
        label="briefing", out_dir=out_dir, prompt_template="x",
        max_turns=5, notify_title="Morning briefing",
        notify_message="Saved to {path}",
    ))

    assert notes == [("Morning briefing", f"Saved to {out_path}")]


def test_no_notify_when_nothing_saved(tmp_path, monkeypatch):
    from assistant import scheduled

    stub = StubEngine()                    # never writes the out file
    _, notes = _wire(monkeypatch, stub)
    asyncio.run(scheduled.run_job(
        label="briefing", out_dir=tmp_path / "out", prompt_template="x",
        max_turns=5, notify_title="t", notify_message="m {path}",
    ))

    assert notes == []


def test_backend_not_ready_aborts_before_engine(tmp_path, monkeypatch):
    from assistant import scheduled

    stub = StubEngine()
    captured, notes = _wire(monkeypatch, stub)
    monkeypatch.setattr(scheduled.config, "backend_ready",
                        lambda: (False, "Ollama is not running"))

    with pytest.raises(SystemExit):
        asyncio.run(scheduled.run_job(
            label="briefing", out_dir=tmp_path / "out", prompt_template="x",
            max_turns=5, notify_title="t", notify_message="m",
        ))

    assert not captured                    # make_engine never called
    assert notes == []
