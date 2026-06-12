"""xattr file tagging: the pure comment-merge logic everywhere, plus the real
setxattr/getxattr round-trip on macOS (skipped elsewhere)."""

import asyncio
import sys

import pytest


def test_without_aide_block_preserves_user_text():
    from assistant.macmeta import _without_aide_block

    assert _without_aide_block("") == ""
    assert _without_aide_block("[Aide] just ours") == ""
    assert _without_aide_block("user note\n\n[Aide] ours") == "user note"
    assert _without_aide_block("only a user note") == "only a user note"


# The round-trip needs the real macOS libc xattr syscalls.
darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS xattr only")


@darwin_only
def test_tag_roundtrip_preserve_and_replace(tmp_path):
    from assistant import macmeta

    f = tmp_path / "doc.txt"
    f.write_text("body with no concept words")
    path = str(f)

    macmeta.tag_file(path, "alpha beta gamma")
    assert macmeta.get_summary(path) == "alpha beta gamma"
    assert "[Aide] alpha beta gamma" in macmeta.get_finder_comment(path)

    # Re-tag replaces the Aide block, not stacks it.
    macmeta.tag_file(path, "delta epsilon")
    comment = macmeta.get_finder_comment(path)
    assert "alpha beta" not in comment and "[Aide] delta epsilon" in comment


@darwin_only
def test_tag_keeps_user_authored_comment(tmp_path):
    import plistlib

    from assistant import macmeta

    f = tmp_path / "doc.txt"
    f.write_text("x")
    path = str(f)
    macmeta._set(path, macmeta._FINDER, plistlib.dumps("hand-written note", fmt=plistlib.FMT_BINARY))

    macmeta.tag_file(path, "aide keywords")
    comment = macmeta.get_finder_comment(path)
    assert comment.startswith("hand-written note")
    assert "[Aide] aide keywords" in comment


def test_toolcore_tag_file_missing_path():
    from assistant import toolcore

    out = asyncio.run(toolcore.tag_file({"path": "/nope/does/not/exist.pdf", "summary": "x"}))
    assert out.startswith("No such file to tag")


@darwin_only
def test_toolcore_tag_file_expands_user_path(tmp_path, monkeypatch):
    from assistant import macmeta, toolcore

    f = tmp_path / "paper.txt"
    f.write_text("dense content")
    out = asyncio.run(toolcore.tag_file({"path": str(f), "summary": "concept words"}))
    assert "Tagged" in out
    assert macmeta.get_summary(str(f)) == "concept words"
