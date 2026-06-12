import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point all ASSISTANT_HOME-derived paths at a temp dir for every test."""
    import assistant.config as config

    monkeypatch.setattr(config, "ASSISTANT_HOME", tmp_path)
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(config, "JOURNAL_DIR", tmp_path / "memory" / "journal")
    monkeypatch.setattr(config, "BRIEFINGS_DIR", tmp_path / "briefings")
    monkeypatch.setattr(config, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "assistant.db")
    # Hermetic by default: never let auto-rescue / ask_advisor / think_harder /
    # auto-escalation reach the real Claude API during tests (this machine has
    # stored Claude creds, which would otherwise make advisor_available() and
    # escalation_available() true). Tests that need them opt back in.
    monkeypatch.setattr(config, "ADVISOR", False)
    monkeypatch.setattr(config, "ESCALATE", False)
    # Pin the backend so a developer's .env (e.g. ASSISTANT_BACKEND=apple) can't
    # change test outcomes. Tests that exercise another backend set it themselves.
    monkeypatch.setattr(config, "BACKEND", "claude")
    yield
