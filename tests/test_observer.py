"""Ambient recall observer: the platform-agnostic logic (DB timeline/search,
forget, privacy filtering, timestamp parsing, cached context). The macOS-only
capture/OCR/osascript paths are not exercised here."""

import datetime as dt
import time

import pytest


def _add_activity(observer, rows):
    """rows: list of (datetime, app, title)."""
    con = observer._conn()
    con.executemany(
        "INSERT INTO activity VALUES (?,?,?)",
        [(ts.isoformat(timespec="seconds"), app, title) for ts, app, title in rows],
    )
    con.commit()
    con.close()


def _add_screen(observer, rows):
    """rows: list of (datetime, app, title, text)."""
    con = observer._conn()
    con.executemany(
        "INSERT INTO screen_fts VALUES (?,?,?,?)",
        [(ts.isoformat(timespec="seconds"), app, title, text) for ts, app, title, text in rows],
    )
    con.commit()
    con.close()


def test_stamp14_normalizes_any_format():
    from assistant import observer

    assert observer._stamp14("2026-06-10 14:30") == 20260610143000
    assert observer._stamp14("20260610-143055") == 20260610143055
    assert observer._stamp14("") == 0


def test_is_private_catches_incognito_and_password_managers():
    from assistant import observer

    assert observer._is_private("Safari", "Private Browsing")
    assert observer._is_private("Google Chrome", "Incognito Tab")
    assert observer._is_private("1Password", "")          # default exclude list
    assert not observer._is_private("Code", "main.py")


def test_timeline_compresses_consecutive_rows():
    from assistant import observer

    now = dt.datetime.now()
    _add_activity(observer, [
        (now - dt.timedelta(minutes=10), "Safari", "Docs"),
        (now - dt.timedelta(minutes=10, seconds=-5), "Safari", "Docs"),  # same group
        (now - dt.timedelta(minutes=5), "Code", "main.py"),
    ])
    lines = observer.timeline(since_hours=24).splitlines()
    assert len(lines) == 2                      # two distinct app/title groups
    assert "Safari — Docs" in lines[0]
    assert "Code — main.py" in lines[1]


def test_timeline_query_filter():
    from assistant import observer

    now = dt.datetime.now()
    _add_activity(observer, [
        (now - dt.timedelta(minutes=3), "Safari", "Invoice"),
        (now - dt.timedelta(minutes=2), "Mail", "Inbox"),
    ])
    assert "Mail" in observer.timeline(query="mail")
    assert "Safari" not in observer.timeline(query="mail")


def test_timeline_empty_when_no_db():
    from assistant import observer

    assert observer.timeline() == "No activity recorded yet."


def test_search_screen_finds_ocr_text():
    from assistant import observer

    now = dt.datetime.now()
    _add_screen(observer, [
        (now - dt.timedelta(minutes=1), "Mail", "Inbox", "Flight confirmation code ABC123"),
        (now, "Safari", "Bank", "checking balance 4096 dollars"),
    ])
    out = observer.search_screen("flight")
    assert "Mail" in out
    assert ">>" in out and "<<" in out           # snippet highlights the match
    assert "Nothing matching" in observer.search_screen("nonexistentword")


def test_search_screen_tolerates_bad_fts_syntax():
    from assistant import observer

    now = dt.datetime.now()
    _add_screen(observer, [(now, "Notes", "x", "a stray quote test")])
    # An unbalanced quote is invalid FTS5 syntax; must fall back, not raise.
    assert isinstance(observer.search_screen('stray"'), str)


def test_forget_everything_and_window():
    from assistant import observer

    now = dt.datetime.now()
    _add_activity(observer, [
        (now - dt.timedelta(hours=5), "Old", "x"),
        (now - dt.timedelta(minutes=10), "Recent", "y"),
    ])
    # Forget only the last hour: the 5-hour-old row survives.
    msg = observer.forget(1)
    assert "the last 1 hours" in msg
    assert "Old" in observer.timeline(since_hours=24)
    assert "Recent" not in observer.timeline(since_hours=24)

    # Forget everything.
    observer.forget(None)
    assert observer.timeline(since_hours=24) == "No matching activity."


def test_forget_screenshots_on_disk():
    from assistant import observer

    shot_dir = observer._shot_dir()
    shot_dir.mkdir(parents=True, exist_ok=True)
    (shot_dir / "20260610-120000.jpg").write_bytes(b"x")
    (shot_dir / "20260610-130000.jpg").write_bytes(b"x")

    msg = observer.forget(None)
    assert "2 screenshots" in msg
    assert list(shot_dir.glob("*.jpg")) == []


def test_nearest_shot_picks_closest_and_latest():
    from assistant import observer

    shot_dir = observer._shot_dir()
    shot_dir.mkdir(parents=True, exist_ok=True)
    for stamp in ("20260610-120000", "20260610-150000", "20260610-180000"):
        (shot_dir / f"{stamp}.jpg").write_bytes(b"x")

    assert observer.nearest_shot("2026-06-10 14:30").endswith("20260610-150000.jpg")
    assert observer.nearest_shot("").endswith("20260610-180000.jpg")  # latest
    observer.forget(None)
    assert observer.nearest_shot() is None


def test_current_context_uses_warm_cache(monkeypatch):
    from assistant import observer

    monkeypatch.setattr(observer.sys, "platform", "darwin")
    # Poison the live probe so any cache miss would raise — proves the cache is used.
    monkeypatch.setattr(observer, "_frontmost",
                        lambda: pytest.fail("osascript called despite a warm cache"))
    observer._latest = ("Xcode", "App.swift", time.monotonic())
    assert observer.current_context() == "Right now: Xcode — App.swift."


def test_current_context_falls_back_and_hides_private(monkeypatch):
    from assistant import observer

    monkeypatch.setattr(observer.sys, "platform", "darwin")
    observer._latest = ("Stale", "x", time.monotonic() - 10_000)   # forces a live probe
    monkeypatch.setattr(observer, "_frontmost", lambda: ("Safari", "Private Browsing"))
    assert observer.current_context() == ""   # private window is suppressed


def test_current_context_empty_off_darwin(monkeypatch):
    from assistant import observer

    monkeypatch.setattr(observer.sys, "platform", "linux")
    observer._latest = ("Whatever", "x", time.monotonic())
    assert observer.current_context() == ""


def test_set_paused_roundtrip():
    from assistant import observer

    assert observer.set_paused(True) is True
    assert observer.paused is True
    assert observer.set_paused(False) is False


def test_prune_size_cap_drops_oldest_first(tmp_path, monkeypatch):
    from assistant import config, observer

    # Three recent 600KB screenshots (recent, so the time prune keeps them all)
    # against a 1MB cap: the two oldest must go, the newest must survive.
    shot_dir = observer._shot_dir()
    shot_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    names = [(now - dt.timedelta(minutes=m)).strftime("%Y%m%d-%H%M%S-%f") + ".jpg"
             for m in (30, 20, 10)]                      # oldest ... newest
    for name in names:
        (shot_dir / name).write_bytes(b"x" * 600_000)
    monkeypatch.setattr(config, "RECALL_MAX_MB", 1)
    con = observer._conn()
    observer._prune(con, now)
    con.close()
    left = sorted(f.name for f in shot_dir.glob("*.jpg"))
    assert left == [names[-1]]                           # only the newest remains


def test_make_digest_stores_understanding(monkeypatch):
    from assistant import advisor, observer

    now = dt.datetime.now()
    _add_activity(observer, [(now - dt.timedelta(minutes=10), "Terminal", "vim notes.md")])

    async def fake_consult(question, context="", **kw):
        assert "Terminal" in context            # the digest pass feeds real activity
        return "Editing study notes in vim."

    monkeypatch.setattr(advisor, "consult", fake_consult)
    con = observer._conn()
    observer._make_digest(con, now)
    con.close()
    got = observer.latest_digest()
    assert got is not None and got[1] == "Editing study notes in vim."


def test_make_digest_respects_local_only_privacy(monkeypatch):
    from assistant import advisor, config, observer

    # A local-first setup (ollama backend, advisor off) means the user chose to
    # keep data on the machine: the digest must NOT consult the cloud, even
    # with Claude credentials present.
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(config, "ADVISOR", False)
    now = dt.datetime.now()
    _add_activity(observer, [(now - dt.timedelta(minutes=5), "Code", "main.py")])

    async def must_not_run(question, context="", **kw):
        pytest.fail("digest consulted the cloud despite a local-only configuration")

    monkeypatch.setattr(advisor, "consult", must_not_run)
    con = observer._conn()
    observer._make_digest(con, now)
    con.close()
    assert observer.latest_digest() is None


def test_make_digest_skips_when_advisor_unavailable(monkeypatch):
    from assistant import advisor, observer

    now = dt.datetime.now()
    _add_activity(observer, [(now - dt.timedelta(minutes=5), "Safari", "Docs")])

    async def unavailable(question, context="", **kw):
        return "(advisor unavailable: offline)"

    monkeypatch.setattr(advisor, "consult", unavailable)
    con = observer._conn()
    observer._make_digest(con, now)
    con.close()
    assert observer.latest_digest() is None      # nothing junk stored


def test_current_context_includes_digest_and_cleans_titles(monkeypatch):
    from assistant import observer

    monkeypatch.setattr(observer.sys, "platform", "darwin")
    observer._latest = ("Terminal", "rashah04 — ✳ aide-plan — claude ◂ 149×45",
                        time.monotonic())
    observer._recent_cache = (time.monotonic(), [])
    con = observer._conn()
    con.execute("INSERT INTO digest VALUES (?,?)",
                (dt.datetime.now().isoformat(timespec="seconds"),
                 "Improving the Aide app with a Claude Code session."))
    con.commit()
    con.close()
    out = observer.current_context()
    assert "Working on" in out
    assert "Improving the Aide app" in out
    assert "✳" not in out and "◂" not in out     # glyph soup cleaned from titles


def test_prune_size_cap_inactive_under_budget(tmp_path, monkeypatch):
    from assistant import config, observer

    shot_dir = observer._shot_dir()
    shot_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    name = now.strftime("%Y%m%d-%H%M%S-%f") + ".jpg"
    (shot_dir / name).write_bytes(b"x" * 1000)
    monkeypatch.setattr(config, "RECALL_MAX_MB", 1500)
    con = observer._conn()
    observer._prune(con, now)
    con.close()
    assert (shot_dir / name).exists()


def test_should_ocr_skips_identical_frames():
    from assistant import observer

    a = observer._img_hash(b"frame-bytes-A")
    b = observer._img_hash(b"frame-bytes-B")
    # Identical image bytes -> same hash -> skip OCR (the text-hash dedupe would
    # have discarded the result anyway, so no captured data is lost).
    assert observer._should_ocr(a, a) is False
    # Different bytes -> different hash -> OCR must run.
    assert observer._should_ocr(b, a) is True
    # No hash (read failed) -> don't claim a frame is worth OCR'ing.
    assert observer._should_ocr("", a) is False


def test_frontmost_app_falls_back_to_osascript(monkeypatch):
    from assistant import observer

    # NSWorkspace unavailable (no AppKit / headless): _frontmost_app returns "".
    monkeypatch.setattr(observer, "_frontmost_app", lambda: "")

    calls = {"n": 0}

    class _Result:
        stdout = "Code\nmain.py\n"

    def fake_run(cmd, *a, **kw):
        # Only the osascript fallback should reach subprocess here.
        assert cmd[0] == "osascript"
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(observer.subprocess, "run", fake_run)
    app, title = observer._frontmost()
    assert (app, title) == ("Code", "main.py")
    assert calls["n"] == 1                # the osascript path was used


def test_frontmost_app_native_degrades_when_appkit_raises(monkeypatch):
    from assistant import observer

    # Simulate NSWorkspace blowing up (no AppKit / no GUI session): the native
    # helper must swallow it and return "", which is the loop's signal to fall
    # back to the osascript _frontmost path that still yields (app, title).
    import builtins

    real_import = builtins.__import__

    def boom(name, *a, **kw):
        if name == "AppKit":
            raise ImportError("no AppKit in this environment")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", boom)
    assert observer._frontmost_app() == ""
