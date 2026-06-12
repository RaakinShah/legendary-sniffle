"""Conversation history store: CRUD, ordering, favorites, and full-text search."""


def test_create_append_and_get():
    from assistant import history

    cid = history.create("First chat")
    history.append(cid, "user", "what's the weather")
    history.append(cid, "assistant", "sunny and 72")

    data = history.get(cid)
    assert data["id"] == cid
    assert data["title"] == "First chat"
    assert data["favorite"] is False
    assert [(m["role"], m["text"]) for m in data["messages"]] == [
        ("user", "what's the weather"),
        ("assistant", "sunny and 72"),
    ]


def test_get_missing_returns_empty():
    from assistant import history

    assert history.get(999) == {}


def test_append_ignores_empty_text():
    from assistant import history

    cid = history.create()
    history.append(cid, "user", "")
    assert history.get(cid)["messages"] == []


def test_recents_excludes_empty_and_favorites():
    from assistant import history

    spoken = history.create("Has messages")
    history.append(spoken, "user", "hi")
    history.create("Never used")  # no messages -> not a recent
    fav = history.create("Favorited")
    history.append(fav, "user", "hi")
    history.set_favorite(fav, True)

    titles = [c["title"] for c in history.recents()]
    assert "Has messages" in titles
    assert "Never used" not in titles
    assert "Favorited" not in titles  # favorites are surfaced separately


def test_favorites_and_toggle():
    from assistant import history

    cid = history.create("Star me")
    history.append(cid, "user", "hi")
    assert history.favorites() == []

    history.set_favorite(cid, True)
    assert [c["id"] for c in history.favorites()] == [cid]
    assert history.get(cid)["favorite"] is True

    history.set_favorite(cid, False)
    assert history.favorites() == []


def test_set_title_and_session():
    from assistant import history

    cid = history.create()
    history.set_title(cid, "  Renamed conversation  ")
    history.set_session(cid, "sess-abc")
    data = history.get(cid)
    assert data["title"] == "Renamed conversation"
    assert data["session_id"] == "sess-abc"


def test_search_matches_message_text():
    from assistant import history

    a = history.create("Trip")
    history.append(a, "user", "book a flight to Tokyo")
    b = history.create("Dinner")
    history.append(b, "assistant", "the pasta recipe needs basil")

    ids = [c["id"] for c in history.search("flight")]
    assert ids == [a]
    # prefix search still finds partial tokens
    assert a in [c["id"] for c in history.search("Tok")]
    # special characters must not raise (FTS syntax fallback)
    assert isinstance(history.search('"weird (query'), list)


def test_search_empty_query_returns_recents():
    from assistant import history

    cid = history.create("Something")
    history.append(cid, "user", "hi")
    assert [c["id"] for c in history.search("   ")] == [c["id"] for c in history.recents()]


def test_delete_removes_conversation_messages_and_index():
    from assistant import history

    cid = history.create("Doomed")
    history.append(cid, "user", "secret keyword zebra")
    history.delete(cid)

    assert history.get(cid) == {}
    assert cid not in [c["id"] for c in history.recents()]
    assert history.search("zebra") == []  # FTS index row also removed


def test_messages_conv_id_index_exists():
    from contextlib import closing

    from assistant import history

    with closing(history._conn()) as con:
        names = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
    assert "idx_messages_conv_id" in names


def test_user_version_is_one_after_connect():
    from contextlib import closing

    from assistant import history

    with closing(history._conn()) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1


def test_search_messages_renders_hit_with_context():
    import re

    from assistant import history

    trip = history.create("Tokyo trip")
    history.append(trip, "user", "remind me to book the ryokan near Kyoto station")
    history.set_title(trip, "Tokyo trip")
    dinner = history.create("Dinner plans")
    history.append(dinner, "assistant", "the pasta recipe needs fresh basil")

    out = history.search_messages("ryokan")
    assert "[Tokyo trip]" in out
    assert "(user)" in out
    assert ">>ryokan<<" in out  # snippet markers around the match
    assert "Dinner plans" not in out
    # each line opens with a YYYY-MM-DD HH:MM timestamp
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}  \[", out)


def test_search_messages_newest_first_and_limit():
    from assistant import history

    cid = history.create("Bird log")
    for i in range(5):
        history.append(cid, "user", f"falcon sighting number {i}")

    out = history.search_messages("falcon", limit=3)
    lines = out.splitlines()
    assert len(lines) == 3
    assert "number 4" in lines[0]  # newest append surfaces first


def test_search_messages_tolerates_fts_syntax():
    from assistant import history

    cid = history.create("Notes")
    history.append(cid, "user", "don't forget the house keys")

    out = history.search_messages("don't")  # apostrophe is invalid FTS syntax
    assert "[Notes]" in out
    assert ">>" in out and "<<" in out


def test_search_messages_no_db_and_no_match():
    from assistant import history

    # nothing has touched the db yet in this isolated home
    assert history.search_messages("anything") == "No conversation history yet."

    cid = history.create("Small talk")
    history.append(cid, "user", "hello there")
    assert history.search_messages("xylophone").startswith(
        "Nothing in past conversations matches"
    )


def test_search_messages_no_duplicate_hits_for_repeated_text():
    from assistant import history

    # Identical text twice in one conversation must yield exactly two hits
    # (one per message), not a cartesian product.
    cid = history.create()
    history.set_title(cid, "dup test")
    history.append(cid, "user", "the same exact text")
    history.append(cid, "user", "the same exact text")
    out = history.search_messages("exact")
    hits = [l for l in out.splitlines() if ">>" in l]
    assert len(hits) == 2
