"""Doctor preflight: report aggregation and the platform-agnostic checks.
The macOS permission probes are not exercised here (they depend on TCC state)."""


def test_report_exit_code_and_counts(capsys):
    from assistant.doctor import OK, WARN, FAIL, Report

    r = Report()
    r.section("Core")
    r.add(OK, "fine")
    r.add(WARN, "meh", hint="do a thing")
    code = r.render()
    assert code == 0  # warnings alone don't block
    assert "1 warning" in capsys.readouterr().out

    r2 = Report()
    r2.add(FAIL, "broken", hint="fix it")
    assert r2.render() == 1  # a failure blocks
    assert "fix it" in capsys.readouterr().out


def test_check_python_passes_on_current_interpreter():
    from assistant import doctor

    r = doctor.Report()
    doctor._check_python(r)
    assert r.rows[0][0] == doctor.OK


def test_check_auth_detects_token(monkeypatch):
    from assistant import doctor

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = doctor.Report()
    doctor._check_auth(r)
    status, label, detail, _ = r.rows[0]
    assert status == doctor.OK and "subscription token" in detail


def test_check_auth_fails_when_absent(monkeypatch, tmp_path):
    from assistant import doctor

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(doctor.Path, "home", staticmethod(lambda: tmp_path))
    r = doctor.Report()
    doctor._check_auth(r)
    assert r.rows[0][0] == doctor.FAIL


def test_check_mcp_flags_unresolved_env(monkeypatch):
    from assistant import config, doctor

    monkeypatch.setattr(
        config, "load_external_mcp_servers",
        lambda: {"gcal": {"env": {"GOOGLE_CLIENT_ID": "${GOOGLE_CLIENT_ID}"}}},
    )
    r = doctor.Report()
    doctor._check_mcp(r)
    statuses = {label: status for status, label, *_ in r.rows}
    assert statuses.get("MCP connectors") == doctor.OK
    assert statuses.get("MCP env") == doctor.WARN


def test_check_mcp_reports_config_error(monkeypatch):
    from assistant import config, doctor

    def boom():
        raise ValueError("bad json")

    monkeypatch.setattr(config, "load_external_mcp_servers", boom)
    r = doctor.Report()
    doctor._check_mcp(r)
    assert r.rows[0][0] == doctor.FAIL
