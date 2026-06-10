import datetime as dt


def test_seed_creates_files(tmp_path):
    from assistant import memory

    memory.seed()
    for name in memory.SEED_FILES:
        assert (tmp_path / "memory" / name).exists()


def test_remember_appends_to_category(tmp_path):
    from assistant import memory

    memory.remember("Likes espresso, no sugar", category="preferences")
    text = (tmp_path / "memory" / "preferences.md").read_text()
    assert "Likes espresso, no sugar" in text


def test_remember_defaults_to_inbox(tmp_path):
    from assistant import memory

    memory.remember("Some loose fact", category="nonsense")
    assert "Some loose fact" in (tmp_path / "memory" / "inbox.md").read_text()


def test_journal_creates_dated_file(tmp_path):
    from assistant import memory

    memory.journal("Shipped the assistant")
    path = tmp_path / "memory" / "journal" / f"{dt.date.today().isoformat()}.md"
    assert path.exists()
    assert "Shipped the assistant" in path.read_text()


def test_load_includes_memory_and_journal(tmp_path):
    from assistant import memory

    memory.remember("Works at Acme", category="profile")
    memory.journal("Day went well")
    loaded = memory.load()
    assert "Works at Acme" in loaded
    assert "Day went well" in loaded
    assert "<file path=" in loaded
