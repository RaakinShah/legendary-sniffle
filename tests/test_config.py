"""Config hardening: defensive env parsing that can never crash the app at import."""


def test_int_env_uses_default_when_unset(monkeypatch):
    from assistant import config

    monkeypatch.delenv("X_AIDE_TEST_INT", raising=False)
    assert config._int_env("X_AIDE_TEST_INT", 7, 1, 10) == 7


def test_int_env_falls_back_on_garbage(monkeypatch, capsys):
    from assistant import config

    monkeypatch.setenv("X_AIDE_TEST_INT", "abc")
    assert config._int_env("X_AIDE_TEST_INT", 7, 1, 10) == 7
    assert "not an integer" in capsys.readouterr().err


def test_int_env_clamps_out_of_range(monkeypatch, capsys):
    from assistant import config

    monkeypatch.setenv("X_AIDE_TEST_INT", "9999")
    assert config._int_env("X_AIDE_TEST_INT", 7, 1, 10) == 10
    monkeypatch.setenv("X_AIDE_TEST_INT", "-5")
    assert config._int_env("X_AIDE_TEST_INT", 7, 1, 10) == 1
    assert "clamped" in capsys.readouterr().err


def test_int_env_passes_valid_values(monkeypatch):
    from assistant import config

    monkeypatch.setenv("X_AIDE_TEST_INT", "5")
    assert config._int_env("X_AIDE_TEST_INT", 7, 1, 10) == 5


def test_recall_exclude_covers_confidential_defaults():
    from assistant import config

    # Credential managers and finance contexts are filtered out of recall by default.
    for marker in ("1password", "bitwarden", "authy", "bank", "paypal"):
        assert marker in config.RECALL_EXCLUDE
