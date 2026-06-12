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


def test_update_revises_fact_in_place(tmp_path):
    from assistant import memory

    memory.remember("Drives a Honda Civic", category="profile")
    out = memory.update("profile", "Honda Civic", "Toyota Camry")
    text = (tmp_path / "memory" / "profile.md").read_text()
    assert "Toyota Camry" in text
    assert "Honda Civic" not in text
    assert "replaced 1 occurrence" in out


def test_update_reports_no_match(tmp_path):
    from assistant import memory

    memory.seed()
    out = memory.update("profile", "never written", "anything")
    assert "Nothing matching" in out
    # the file must be untouched on a miss
    assert "anything" not in (tmp_path / "memory" / "profile.md").read_text()


def test_forget_fact_removes_only_matching_bullets(tmp_path):
    from assistant import memory

    memory.remember("Allergic to penicillin", category="profile")
    memory.remember("Lives in Louisville", category="profile")
    out = memory.forget_fact("profile", "penicillin")
    text = (tmp_path / "memory" / "profile.md").read_text()
    assert "penicillin" not in text
    assert "Lives in Louisville" in text          # other bullets survive
    assert text.startswith("# Profile")           # headers survive
    assert "Forgot 1 entry" in out


def test_forget_fact_is_case_insensitive_and_reports_misses(tmp_path):
    from assistant import memory

    memory.remember("Prefers Dark Mode", category="preferences")
    assert "Forgot 1 entry" in memory.forget_fact("preferences", "dark mode")
    assert "Nothing matching" in memory.forget_fact("preferences", "dark mode")


def test_load_includes_recent_journal_window(tmp_path):
    from assistant import config, memory

    # Yesterday's journal must survive midnight: load() carries a 3-day window.
    memory.seed()
    yesterday = dt.date.today() - dt.timedelta(days=1)
    old = dt.date.today() - dt.timedelta(days=5)
    (config.JOURNAL_DIR / f"{yesterday.isoformat()}.md").write_text(
        "# Journal\n- finished the anemia deck\n")
    (config.JOURNAL_DIR / f"{old.isoformat()}.md").write_text(
        "# Journal\n- ancient history entry\n")
    loaded = memory.load()
    assert "finished the anemia deck" in loaded     # yesterday included
    assert "ancient history entry" not in loaded    # outside the window
