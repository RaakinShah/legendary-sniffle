"""The proactive engine: store dedupe/lifecycle, deterministic checks, line
parsing, quiet hours, and the runner wiring (with a stub engine, no network)."""

import asyncio
import datetime as dt


# --- store -------------------------------------------------------------------

def _ins(key="k1", urgency="feed", **kw):
    from assistant.proactive.core import Insight
    return Insight(key=key, category="test", title=kw.get("title", "T"),
                   body=kw.get("body", ""), urgency=urgency)


def test_store_add_dedupes_within_window():
    from assistant.proactive import store

    assert store.add(_ins(key="dup")) is True
    assert store.add(_ins(key="dup")) is False     # same key, deduped
    assert store.add(_ins(key="other")) is True


def test_store_feed_lifecycle_and_unread():
    from assistant.proactive import store

    store.add(_ins(key="a", title="Alpha"))
    store.add(_ins(key="b", title="Beta"))
    assert store.unread_count() == 2
    feed = store.feed()
    assert {f["title"] for f in feed} == {"Alpha", "Beta"}

    store.mark(feed[0]["id"], "dismissed")
    assert len(store.feed()) == 1                   # dismissed hidden
    store.mark_seen()
    assert store.unread_count() == 0               # opening the feed clears unread


def test_store_snooze_hides_then_returns():
    from assistant.proactive import store

    store.add(_ins(key="s", title="Snoozy"))
    fid = store.feed()[0]["id"]
    store.snooze(fid, hours=5)
    assert store.feed() == []                        # hidden while snoozed
    assert any(f["title"] == "Snoozy" for f in store.feed(include_resolved=True))


def test_store_notify_priority_ordering():
    from assistant.proactive import store

    store.add(_ins(key="f", title="Feed", urgency="feed"))
    store.add(_ins(key="n", title="Ping", urgency="notify"))
    titles = [f["title"] for f in store.feed()]
    assert titles[0] == "Ping"                       # notify sorts first


def test_store_pending_pings_excludes_feed_seen_and_notified():
    from assistant.proactive import store

    store.add(_ins(key="n1", title="Notify one", urgency="notify"))
    store.add(_ins(key="n2", title="Notify two", urgency="notify"))
    store.add(_ins(key="f1", title="Feed only", urgency="feed"))

    pending = store.pending_pings(10)
    assert {p["title"] for p in pending} == {"Notify one", "Notify two"}  # feed excluded

    store.mark_notified([pending[0]["id"]])
    titles = {p["title"] for p in store.pending_pings(10)}
    assert titles == {"Notify two"}                  # a pinged item drops out

    store.mark_seen()                                # user opens the feed
    assert store.pending_pings(10) == []             # seen items never ping after


# --- deterministic checks ----------------------------------------------------

def test_stale_task_gardener_flags_old_undated_tasks(monkeypatch):
    from assistant import tasks
    from assistant.proactive import checks
    from assistant.proactive.core import Context

    old = (dt.datetime.now() - dt.timedelta(days=30)).isoformat(timespec="seconds")
    t = tasks.add(title="ancient todo")
    # Backdate created_at directly (tasks.add stamps "now").
    with tasks._conn() as con:
        con.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (old, t.id))
    out = checks.StaleTaskGardener().run(Context(now=dt.datetime.now()))
    assert len(out) == 1
    assert "ancient todo" in out[0].body
    assert out[0].urgency == "feed"


def test_due_tasks_overdue_pings(monkeypatch):
    from assistant import tasks
    from assistant.proactive import checks
    from assistant.proactive.core import Context

    past = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    tasks.add(title="overdue thing", due=past)
    out = checks.DueTasks().run(Context(now=dt.datetime.now()))
    assert any(i.urgency == "notify" and "Overdue" in i.title for i in out)


def test_context_resume_gated_on_wake(monkeypatch):
    from assistant import config, observer
    from assistant.proactive import checks
    from assistant.proactive.core import Context

    monkeypatch.setattr(config, "RECALL", True)
    con = observer._conn()
    con.execute("INSERT INTO digest VALUES (?,?)",
                (dt.datetime.now().isoformat(timespec="seconds"), "writing the thesis intro"))
    con.commit(); con.close()
    chk = checks.ContextResume()
    assert chk.gate(Context(returned_from_away=False)) is False   # not just back
    ctx = Context(returned_from_away=True)
    assert chk.gate(ctx) is True
    out = chk.run(ctx)
    assert out and "thesis" in out[0].body


def test_connector_health_flags_unresolved_env(monkeypatch):
    from assistant import config
    from assistant.proactive import checks
    from assistant.proactive.core import Context

    monkeypatch.setattr(config, "auth_available", lambda: True)
    monkeypatch.setattr(config, "RECALL", False)
    monkeypatch.setattr(config, "load_external_mcp_servers",
                        lambda: {"gcal": {"env": {"GOOGLE_CLIENT_ID": "${GOOGLE_CLIENT_ID}"}}})
    out = checks.ConnectorHealth().run(Context(now=dt.datetime.now()))
    assert out and out[0].urgency == "notify"
    assert "gcal" in out[0].body


# --- line parsing ------------------------------------------------------------

def test_parse_insight_lines():
    from assistant.proactive.checks import _parse_insight_lines

    text = ("notify | Meeting in 30 min | Standup with the team\n"
            "feed | Renew domain | example.com renews Friday\n"
            "garbage line with no pipe\n"
            "ALL-CLEAR")
    out = _parse_insight_lines(text, "comms", "feed", "sweep", "2026-06-11")
    assert len(out) == 2
    assert out[0].urgency == "notify" and "Meeting" in out[0].title
    assert out[1].urgency == "feed"


def test_parse_all_clear_is_empty():
    from assistant.proactive.checks import _parse_insight_lines

    assert _parse_insight_lines("ALL-CLEAR", "comms", "feed", "s", "d") == []
    assert _parse_insight_lines("", "comms", "feed", "s", "d") == []


# --- runner ------------------------------------------------------------------

def test_in_quiet_hours_wraps_midnight(monkeypatch):
    from assistant.proactive import run

    monkeypatch.setattr(run, "QUIET_START", 23)
    monkeypatch.setattr(run, "QUIET_END", 8)
    at = lambda h: dt.datetime(2026, 6, 11, h, 0)
    assert run._in_quiet_hours(at(2)) is True       # 2am inside the wrap
    assert run._in_quiet_hours(at(23)) is True
    assert run._in_quiet_hours(at(14)) is False     # 2pm is fine


def test_runner_routes_notify_and_feed(monkeypatch):
    from assistant.proactive import run, store
    from assistant.proactive.core import Insight

    pings = []
    monkeypatch.setattr(run.notify, "notify", lambda title, msg: pings.append((title, msg)))
    monkeypatch.setattr(run, "_in_quiet_hours", lambda now: False)
    monkeypatch.setattr(run, "_focus_app", lambda: "")       # not in a focus app
    monkeypatch.setattr(run.config, "backend_ready", lambda: (True, "ok"))

    class FakeCheck:
        name, category, cadence = "fake", "test", "cycle"
        def gate(self, ctx): return True
        def run(self, ctx):
            return [Insight(key="p1", category="test", title="Urgent thing",
                            body="now", urgency="notify"),
                    Insight(key="f1", category="test", title="Quiet thing",
                            urgency="feed")]

    monkeypatch.setattr(run.checks, "active_checks", lambda: [FakeCheck()])
    asyncio.run(run.main())

    titles = {f["title"] for f in store.feed()}
    assert {"Urgent thing", "Quiet thing"} <= titles    # both in the feed
    assert pings and pings[0][0] == "Aide: Urgent thing"  # only the urgent one pinged


def test_runner_suppresses_pings_in_quiet_hours(monkeypatch):
    from assistant.proactive import run, store
    from assistant.proactive.core import Insight

    pings = []
    monkeypatch.setattr(run.notify, "notify", lambda title, msg: pings.append(title))
    monkeypatch.setattr(run, "_in_quiet_hours", lambda now: True)   # quiet
    monkeypatch.setattr(run.config, "backend_ready", lambda: (True, "ok"))

    class FakeCheck:
        name, category, cadence = "fake", "test", "cycle"
        def gate(self, ctx): return True
        def run(self, ctx):
            return [Insight(key="q1", category="test", title="Urgent",
                            urgency="notify")]

    monkeypatch.setattr(run.checks, "active_checks", lambda: [FakeCheck()])
    asyncio.run(run.main())
    assert pings == []                                  # no ping during quiet hours
    assert any(f["title"] == "Urgent" for f in store.feed())  # still in the feed


def test_focus_app_matches_substring_case_insensitively(monkeypatch):
    from assistant import config
    from assistant.proactive import run

    monkeypatch.setattr(config, "FOCUS_APPS", frozenset({"zoom.us", "xcode"}))
    monkeypatch.setattr(run.sys, "platform", "darwin")
    monkeypatch.setattr("assistant.observer._frontmost_app", lambda: "zoom.us")
    assert run._focus_app() == "zoom.us"
    monkeypatch.setattr("assistant.observer._frontmost_app", lambda: "Mail")
    assert run._focus_app() == ""                       # not a focus app
    monkeypatch.setattr(config, "FOCUS_APPS", frozenset())
    monkeypatch.setattr("assistant.observer._frontmost_app", lambda: "zoom.us")
    assert run._focus_app() == ""                       # empty list disables silence


def test_runner_focus_holds_ping_then_releases(monkeypatch):
    """A notify item raised while a focus app is frontmost waits in the feed and
    pings on the next cycle once the user has left the focus app."""
    from assistant.proactive import run, store
    from assistant.proactive.core import Insight

    pings = []
    monkeypatch.setattr(run.notify, "notify", lambda title, msg: pings.append(title))
    monkeypatch.setattr(run, "_in_quiet_hours", lambda now: False)
    monkeypatch.setattr(run.config, "backend_ready", lambda: (True, "ok"))

    class FakeCheck:
        name, category, cadence = "fake", "test", "cycle"
        def gate(self, ctx): return True
        def run(self, ctx):
            return [Insight(key="m1", category="test", title="Meeting prep",
                            body="standup", urgency="notify")]

    monkeypatch.setattr(run.checks, "active_checks", lambda: [FakeCheck()])

    # Cycle 1: in a focus app (e.g. Zoom) -> the ping is held, item still feeds.
    monkeypatch.setattr(run, "_focus_app", lambda: "zoom.us")
    asyncio.run(run.main())
    assert pings == []
    assert any(f["title"] == "Meeting prep" for f in store.feed())
    assert [p["title"] for p in store.pending_pings(10)] == ["Meeting prep"]

    # Cycle 2: left the focus app -> the held ping fires (deduped from the feed,
    # so the check adds nothing new, but the pending ping still drains).
    monkeypatch.setattr(run, "_focus_app", lambda: "")
    asyncio.run(run.main())
    assert pings == ["Aide: Meeting prep"]
    assert store.pending_pings(10) == []                # drained, won't ping twice
