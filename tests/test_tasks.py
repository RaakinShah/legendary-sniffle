import datetime as dt

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point ASSISTANT_HOME at a temp dir before importing modules under test."""
    import assistant.config as config

    monkeypatch.setattr(config, "ASSISTANT_HOME", tmp_path)
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(config, "JOURNAL_DIR", tmp_path / "memory" / "journal")
    monkeypatch.setattr(config, "BRIEFINGS_DIR", tmp_path / "briefings")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "assistant.db")
    yield


def test_add_and_list():
    from assistant import tasks

    t = tasks.add("email Sam", due="2026-06-12", priority="high")
    assert t.id == 1
    assert t.status == "open"

    items = tasks.list_tasks()
    assert len(items) == 1
    assert items[0].title == "email Sam"
    assert "due 2026-06-12" in items[0].render()


def test_complete_and_filter():
    from assistant import tasks

    t = tasks.add("water plants")
    done = tasks.complete(t.id)
    assert done.status == "done"
    assert done.completed_at is not None
    assert tasks.list_tasks(status="open") == []
    assert len(tasks.list_tasks(status="done")) == 1
    assert len(tasks.list_tasks(status="all")) == 1


def test_complete_missing_raises():
    from assistant import tasks

    with pytest.raises(KeyError):
        tasks.complete(999)


def test_delete():
    from assistant import tasks

    t = tasks.add("temp")
    tasks.delete(t.id)
    assert tasks.list_tasks(status="all") == []
    with pytest.raises(KeyError):
        tasks.delete(t.id)


def test_due_soon_includes_overdue_and_window():
    from assistant import tasks

    now = dt.datetime.now()
    tasks.add("overdue", due=(now - dt.timedelta(days=1)).isoformat(timespec="seconds"))
    tasks.add("soon", due=(now + dt.timedelta(hours=2)).isoformat(timespec="seconds"))
    tasks.add("far", due=(now + dt.timedelta(days=10)).isoformat(timespec="seconds"))
    tasks.add("no due date")

    titles = [t.title for t in tasks.due_soon(within_hours=24)]
    assert titles == ["overdue", "soon"]
