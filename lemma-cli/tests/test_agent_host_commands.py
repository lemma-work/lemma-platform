from __future__ import annotations

import subprocess

from typer.testing import CliRunner

from lemma_cli.agent_host import commands


runner = CliRunner()


def test_status_delegates_to_native_binary(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(commands, "_binary", lambda: "/opt/lemma-agent-host")

    def fake_run(arguments, *, check):
        calls.append(arguments)
        assert check is False
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    result = runner.invoke(commands.app, ["status"])

    assert result.exit_code == 0, result.output
    assert calls == [["/opt/lemma-agent-host", "status", "--json"]]


def test_disconnect_preserves_explicit_safety_switch(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(commands, "_binary", lambda: "lemma-agent-host")
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda arguments, *, check: (
            calls.append(arguments)
            or subprocess.CompletedProcess(arguments, 0)
        ),
    )

    result = runner.invoke(
        commands.app,
        ["disconnect", "--target", "work", "--force-local"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "lemma-agent-host",
            "disconnect",
            "--target",
            "work",
            "--force-local",
        ]
    ]


def test_missing_native_binary_has_actionable_error(monkeypatch) -> None:
    monkeypatch.delenv("LEMMA_AGENT_HOST_BIN", raising=False)
    monkeypatch.setattr(commands.shutil, "which", lambda _name: None)
    monkeypatch.setattr(commands.sys, "executable", "/missing/python")
    monkeypatch.setattr(commands.Path, "is_file", lambda _path: False)

    try:
        commands._binary()
    except RuntimeError as exc:
        assert "LEMMA_AGENT_HOST_BIN" in str(exc)
    else:
        raise AssertionError("expected missing Agent Host binary to fail")


def test_start_routes_through_locald_when_desktop_owns_lifecycle(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[list[str]] = []
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    monkeypatch.setattr(commands, "_locald_binary", lambda: "/opt/lemma-locald")
    monkeypatch.setattr(commands, "_locald_token_path", lambda: token)
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda arguments, *, check: (
            calls.append(arguments)
            or subprocess.CompletedProcess(arguments, 0)
        ),
    )

    result = runner.invoke(commands.app, ["start"])

    assert result.exit_code == 0, result.output
    assert calls[0][:2] == ["/opt/lemma-locald", "send"]
    assert '"cmd":"agent-host.start"' in calls[0][2]
