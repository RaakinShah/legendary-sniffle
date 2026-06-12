"""notify.notify routing: terminal-notifier (attributed to Aide) when present,
osascript fallback otherwise. The subprocess is always stubbed, so nothing is
posted and the suite stays hermetic."""

from assistant import notify


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.subprocess, "run",
                        lambda args, **kw: calls.append(args))
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    return calls


def test_uses_terminal_notifier_with_sender_and_strips_prefix(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(notify, "_terminal_notifier", lambda: "/opt/homebrew/bin/terminal-notifier")
    monkeypatch.setattr(notify, "_aide_installed", lambda: True)

    notify.notify("Aide: Possible phishing email", "from a stranger")
    assert len(calls) == 1
    args = calls[0]
    assert args[0].endswith("terminal-notifier")
    assert "-sender" in args and notify._SENDER_BUNDLE_ID in args
    # The redundant "Aide: " prefix is dropped; the banner shows Aide already.
    title = args[args.index("-title") + 1]
    assert title == "Possible phishing email"
    assert args[args.index("-message") + 1] == "from a stranger"


def test_no_sender_when_bundle_absent(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(notify, "_terminal_notifier", lambda: "/usr/local/bin/terminal-notifier")
    monkeypatch.setattr(notify, "_aide_installed", lambda: False)

    notify.notify("Aide: hi", "body")
    assert "-sender" not in calls[0]


def test_preserves_title_without_separator(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(notify, "_terminal_notifier", lambda: "terminal-notifier")
    monkeypatch.setattr(notify, "_aide_installed", lambda: True)

    notify.notify("Aide needs attention", "fix setup")   # no colon -> left intact
    assert calls[0][calls[0].index("-title") + 1] == "Aide needs attention"


def test_falls_back_to_osascript(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(notify, "_terminal_notifier", lambda: None)

    notify.notify("Aide: hello", "world")
    assert calls[0][0] == "osascript"
    assert any("display notification" in a for a in calls[0])


def test_noop_off_darwin(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(notify.sys, "platform", "linux")

    notify.notify("Aide: x", "y")
    assert calls == []
