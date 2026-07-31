from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lemma_stack import locald_client
from lemma_stack.locald_client import LocaldClient, LocaldError


def _fake_binary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("binary", encoding="utf-8")
    path.chmod(0o755)


def test_discovers_desktop_runtime_and_renders_explicit_daemon_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support = tmp_path / "support"
    binary = tmp_path / "Lemma.app/Contents/MacOS/lemma-locald"
    _fake_binary(binary)
    release = support / "runtime/releases/1.2.3"
    (release / "local-runtime").mkdir(parents=True)
    (release / "local-runtime/release.json").write_text('{"version":"1.2.3"}', encoding="utf-8")
    guest = release / "managed-runtime/macos-aarch64"
    guest.mkdir(parents=True)
    (guest / "runtime.json").write_text('{"target":"macos-aarch64"}', encoding="utf-8")
    support.mkdir(exist_ok=True)
    (support / "desktop-config.json").write_text(
        json.dumps({"installedRuntime": {"release": "1.2.3", "root": str(release)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEMMA_DESKTOP_APP_SUPPORT_DIR", str(support))
    monkeypatch.setenv("LEMMA_LOCALD_BIN", str(binary))
    monkeypatch.setattr(locald_client.sys, "platform", "darwin")

    client = LocaldClient.discover()

    assert client is not None
    assert client.environment["LEMMA_LOCALD_HOST_PACK_ROOT"] == str(release / "local-runtime")
    assert client.environment["LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT"] == str(
        release / "managed-runtime"
    )
    assert client.environment["LEMMA_LOCALD_RUNTIME_BRIDGE_BIN"].endswith(
        "/Contents/MacOS/lemma-runtime"
    )
    assert client.environment["LEMMA_LOCALD_VZ_BIN"].endswith("/Contents/MacOS/lemma-vz")


def test_does_not_claim_an_unconfigured_binary_as_managed_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "lemma-locald"
    _fake_binary(binary)
    monkeypatch.setenv("LEMMA_DESKTOP_APP_SUPPORT_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("LEMMA_LOCALD_BIN", str(binary))
    monkeypatch.delenv("LEMMA_LOCALD_ROOT", raising=False)

    assert LocaldClient.discover() is None


def test_request_returns_only_the_matching_terminal_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "lemma-locald"
    _fake_binary(binary)
    client = LocaldClient(binary, tmp_path, {})
    monkeypatch.setattr(LocaldClient, "ensure_running", lambda self: None)

    def invoke(self, *arguments: str, timeout: float):
        del self, timeout
        request = json.loads(arguments[1])
        request_id = request["id"]
        output = "\n".join(
            [
                '{"event":"hello"}',
                '{"event":"status","id":"somebody-else"}',
                json.dumps(
                    {
                        "event": "status",
                        "id": request_id,
                        "release": "1.2.3",
                        "ready": True,
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess([], 0, stdout=output, stderr="")

    monkeypatch.setattr(LocaldClient, "_invoke", invoke)

    event = client.request("status")

    assert event["release"] == "1.2.3"
    assert event["ready"] is True


def test_request_surfaces_structured_daemon_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "lemma-locald"
    _fake_binary(binary)
    client = LocaldClient(binary, tmp_path, {})
    monkeypatch.setattr(LocaldClient, "ensure_running", lambda self: None)

    def invoke(self, *arguments: str, timeout: float):
        del self, timeout
        request_id = json.loads(arguments[1])["id"]
        output = json.dumps(
            {
                "event": "error",
                "id": request_id,
                "code": "busy",
                "message": "another operation is running",
            }
        )
        return subprocess.CompletedProcess([], 0, stdout=output, stderr="")

    monkeypatch.setattr(LocaldClient, "_invoke", invoke)

    with pytest.raises(LocaldError, match="another operation is running"):
        client.request("restart")


def test_runtime_prepare_returns_its_structured_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "lemma-locald"
    _fake_binary(binary)
    client = LocaldClient(binary, tmp_path, {})
    monkeypatch.setattr(LocaldClient, "ensure_running", lambda self: None)

    def invoke(self, *arguments: str, timeout: float):
        del self
        assert timeout == 600
        request_id = json.loads(arguments[1])["id"]
        output = "\n".join(
            [
                json.dumps(
                    {
                        "event": "runtime.prepared",
                        "id": request_id,
                        "ready": False,
                        "reboot_required": True,
                    }
                ),
                json.dumps(
                    {
                        "event": "done",
                        "cmd": "runtime.prepare",
                        "id": request_id,
                        "ok": True,
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess([], 0, stdout=output, stderr="")

    monkeypatch.setattr(LocaldClient, "_invoke", invoke)

    event = client.request("runtime.prepare")

    assert event["reboot_required"] is True
