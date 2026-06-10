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
    yield
