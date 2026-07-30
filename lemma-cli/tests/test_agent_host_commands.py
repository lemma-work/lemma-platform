from __future__ import annotations

import hashlib
import subprocess
import sys

from typer.testing import CliRunner

from lemma_cli.agent_host import commands
from lemma_cli.agent_host import bootstrap


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
    monkeypatch.setattr(
        commands,
        "install_agent_host",
        lambda: (_ for _ in ()).throw(
            RuntimeError("set LEMMA_AGENT_HOST_BIN for this platform")
        ),
    )

    try:
        commands._binary()
    except RuntimeError as exc:
        assert "LEMMA_AGENT_HOST_BIN" in str(exc)
    else:
        raise AssertionError("expected missing Agent Host binary to fail")


def test_install_downloads_versioned_checksum_verified_binary(
    monkeypatch,
    tmp_path,
) -> None:
    release = tmp_path / "release"
    install_root = tmp_path / "install"
    release.mkdir()
    extension = ".exe" if sys.platform == "win32" else ""
    asset_name = f"lemma-agent-host-test-target{extension}"
    binary = b"native-agent-host-test-binary"
    (release / asset_name).write_bytes(binary)
    (release / f"{asset_name}.sha256").write_text(
        f"{hashlib.sha256(binary).hexdigest()}  {asset_name}\n",
        encoding="ascii",
    )
    monkeypatch.setenv("LEMMA_AGENT_HOST_INSTALL_DIR", str(install_root))
    monkeypatch.setenv(
        "LEMMA_AGENT_HOST_RELEASE_BASE_URL",
        release.as_uri(),
    )
    monkeypatch.setattr(bootstrap, "_target_triple", lambda: "test-target")

    installed = bootstrap.install_agent_host()

    assert installed.read_bytes() == binary
    assert installed.parent.parent == install_root
    if sys.platform != "win32":
        assert installed.stat().st_mode & 0o111


def test_install_rejects_tampered_binary(monkeypatch, tmp_path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    extension = ".exe" if sys.platform == "win32" else ""
    asset_name = f"lemma-agent-host-test-target{extension}"
    (release / asset_name).write_bytes(b"tampered")
    (release / f"{asset_name}.sha256").write_text(
        f"{hashlib.sha256(b'expected').hexdigest()}  {asset_name}\n",
        encoding="ascii",
    )
    monkeypatch.setenv("LEMMA_AGENT_HOST_INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setenv(
        "LEMMA_AGENT_HOST_RELEASE_BASE_URL",
        release.as_uri(),
    )
    monkeypatch.setattr(bootstrap, "_target_triple", lambda: "test-target")

    try:
        bootstrap.install_agent_host()
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("expected a tampered Agent Host binary to fail")
    assert not bootstrap.managed_binary_path().exists()


def test_install_command_reports_managed_path(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "lemma-agent-host"
    monkeypatch.setattr(
        commands,
        "install_agent_host",
        lambda *, force: binary,
    )

    result = runner.invoke(commands.app, ["install"])

    assert result.exit_code == 0, result.output
    assert str(binary) in result.output


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
