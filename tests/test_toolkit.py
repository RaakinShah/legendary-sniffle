"""Local toolset: schema/gating, dispatch into the real stores, and the safety gate."""

import asyncio

from assistant import toolkit


def _names(specs):
    return {s["function"]["name"] for s in specs}


def test_full_access_exposes_shell_and_files(monkeypatch):
    from assistant import config

    monkeypatch.setattr(config, "FULL_ACCESS", True)
    specs, dispatch = toolkit.build_toolset(mac=False)
    assert {"bash", "read_file", "write_file"} <= _names(specs)
    assert "bash" in dispatch


def test_sandbox_hides_shell_and_files(monkeypatch):
    from assistant import config

    monkeypatch.setattr(config, "FULL_ACCESS", False)
    specs, _ = toolkit.build_toolset(mac=False)
    assert not ({"bash", "read_file", "write_file"} & _names(specs))
    # but tasks/memory/web stay available
    assert {"add_task", "remember", "web_fetch", "web_search"} <= _names(specs)


def test_mac_flag_controls_screen_tools():
    assert "capture_screen" in _names(toolkit.build_toolset(mac=True)[0])
    assert "capture_screen" not in _names(toolkit.build_toolset(mac=False)[0])


def test_ask_advisor_tool_gated_by_availability(monkeypatch):
    from assistant import config

    # Present only when the advisor is enabled, local, and has Claude auth.
    monkeypatch.setattr(config, "ADVISOR", True)
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(config, "auth_available", lambda: True)
    specs, dispatch = toolkit.build_toolset(mac=False)
    assert "ask_advisor" in _names(specs) and "ask_advisor" in dispatch

    # Gone without credentials...
    monkeypatch.setattr(config, "auth_available", lambda: False)
    assert "ask_advisor" not in _names(toolkit.build_toolset(mac=False)[0])

    # ...and gone on the Claude backend (advisor-on-local makes no sense there).
    monkeypatch.setattr(config, "auth_available", lambda: True)
    monkeypatch.setattr(config, "BACKEND", "claude")
    assert "ask_advisor" not in _names(toolkit.build_toolset(mac=False)[0])


def test_every_spec_has_a_handler():
    specs, dispatch = toolkit.build_toolset(mac=True)
    assert _names(specs) == set(dispatch)


def test_add_task_and_remember_dispatch_to_stores():
    from assistant import memory, tasks

    out = asyncio.run(toolkit._add_task({"title": "Pay rent", "due": "2026-07-01"}))
    assert "Pay rent" in out
    assert "Pay rent" in [t.title for t in tasks.list_tasks(status="all")]

    asyncio.run(toolkit._remember({"fact": "Prefers tea", "category": "preferences"}))
    assert "Prefers tea" in (memory.config.MEMORY_DIR / "preferences.md").read_text()


def test_bash_runs_safe_command():
    out = asyncio.run(toolkit._bash({"command": "echo hello-from-aide"}))
    assert "hello-from-aide" in out


def test_bash_blocks_destructive_without_confirm():
    out = asyncio.run(toolkit._bash({"command": "rm -rf /tmp/should-not-run"}))
    assert "BLOCKED" in out
    # a benign echo with confirm runs normally (confirm only matters when gated)
    assert "ok" in asyncio.run(toolkit._bash({"command": "echo ok", "confirm": True}))


def test_write_file_blocks_outside_home_then_allows_with_confirm(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"   # tmp_path is outside the real home dir
    blocked = asyncio.run(toolkit._write_file({"path": str(target), "content": "x"}))
    assert "BLOCKED" in blocked
    assert not target.exists()

    ok = asyncio.run(toolkit._write_file({"path": str(target), "content": "hi", "confirm": True}))
    assert "Wrote" in ok and target.read_text() == "hi"


def test_write_file_inside_home_needs_no_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(toolkit.Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "sub" / "note.txt"
    out = asyncio.run(toolkit._write_file({"path": str(target), "content": "hey"}))
    assert "Wrote" in out and target.read_text() == "hey"


def test_read_file_roundtrip(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("readable content")
    assert "readable content" in asyncio.run(toolkit._read_file({"path": str(f)}))
