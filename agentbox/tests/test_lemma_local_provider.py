from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentbox.apps import sandbox_app
from agentbox.config import settings
from agentbox.providers.lemma_local import LemmaLocalSandboxProvider
from agentbox.providers.registry import build_provider, provider_names
from agentbox.schemas import SandboxEnsureRequest


def _status(sandbox_id: str = "box-1") -> dict:
    return {
        "provider_id": f"container-{sandbox_id}",
        "metadata": {"engine": "containerd"},
        "status": {
            "id": sandbox_id,
            "ready": True,
            "status": "RUNNING",
            "runtime_url": "http://192.168.64.2:49152",
            "pod_ip": "192.168.64.2",
            "apps": {
                "runtime": {
                    "name": "runtime",
                    "public_slug": "runtime",
                    "port": 8080,
                    "ready": True,
                    "private_url": "http://192.168.64.2:49152",
                }
            },
        },
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, "agentbox_local_runtime_cli", "fake-runtime")
    monkeypatch.setattr(settings, "agentbox_require_callback", False)
    monkeypatch.setattr(
        "agentbox.providers.lemma_local.shutil.which",
        lambda value: f"/signed/{value}",
    )
    return LemmaLocalSandboxProvider()


def _completed(response: dict, *, returncode: int = 0):
    return SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(response),
        stderr="",
    )


def test_provider_is_builtin_and_requires_bridge(monkeypatch):
    assert "lemma_local" in provider_names()
    monkeypatch.setattr(settings, "agentbox_local_runtime_cli", "missing-runtime")
    monkeypatch.setattr(
        "agentbox.providers.lemma_local.shutil.which", lambda _value: None
    )
    with pytest.raises(RuntimeError, match="bundled runtime bridge"):
        build_provider("lemma_local")


@pytest.mark.asyncio
async def test_ensure_sends_secrets_over_stdin_and_returns_status(
    provider, monkeypatch
):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["request"] = json.loads(kwargs["input"])
        return _completed({"ok": True, "result": _status()})

    monkeypatch.setattr("agentbox.providers.lemma_local.subprocess.run", run)
    status = await provider.create(
        "box-1",
        SandboxEnsureRequest(
            env={
                "LEMMA_BASE_URL": "http://host.lemma.internal:8711",
                "LEMMA_TOKEN": "secret-token",
            }
        ),
    )

    assert captured["command"] == ["/signed/fake-runtime", "request"]
    assert "secret-token" not in " ".join(captured["command"])
    assert captured["request"]["operation"] == "sandbox.ensure"
    assert captured["request"]["parameters"]["env"]["LEMMA_TOKEN"] == "secret-token"
    assert status.ready


@pytest.mark.asyncio
async def test_endpoint_carries_exact_guest_generation(provider, monkeypatch):
    monkeypatch.setattr(
        "agentbox.providers.lemma_local.subprocess.run",
        lambda *_args, **_kwargs: _completed({"ok": True, "result": _status()}),
    )

    endpoint = await provider.resolve_endpoint("box-1", sandbox_app("runtime"))

    assert endpoint.base_url == "http://192.168.64.2:49152"
    assert endpoint.provider_id == "container-box-1"
    assert endpoint.instance_id == "container-box-1"


@pytest.mark.asyncio
async def test_inventory_and_adoption_are_exact(provider, monkeypatch):
    responses = iter(
        [
            _completed(
                {
                    "ok": True,
                    "result": {"sandboxes": [_status()]},
                }
            ),
            _completed({"ok": True, "result": _status()}),
            _completed({"ok": True, "result": _status()}),
        ]
    )
    monkeypatch.setattr(
        "agentbox.providers.lemma_local.subprocess.run",
        lambda *_args, **_kwargs: next(responses),
    )

    managed = await provider.list_managed()

    assert managed[0].ref.provider_id == "container-box-1"
    assert managed[0].metadata == {"engine": "containerd"}
    assert await provider.adopt("box-1", "container-box-1")
    assert not await provider.adopt("box-1", "different-generation")


@pytest.mark.asyncio
async def test_not_found_and_invalid_payload_fail_closed(provider, monkeypatch):
    responses = iter(
        [
            _completed(
                {
                    "ok": False,
                    "error": {"code": "not_found", "message": "missing"},
                },
                returncode=1,
            ),
            _completed({"ok": True, "result": {"provider_id": "x"}}),
        ]
    )
    monkeypatch.setattr(
        "agentbox.providers.lemma_local.subprocess.run",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(HTTPException) as not_found:
        await provider.get_status("box-1")
    assert not_found.value.status_code == 404
    with pytest.raises(Exception, match="sandbox status was invalid"):
        await provider.get_status("box-1")


@pytest.mark.asyncio
async def test_callback_configuration_is_explicit(provider, monkeypatch):
    monkeypatch.setattr(settings, "agentbox_require_callback", True)

    with pytest.raises(HTTPException, match="LEMMA_BASE_URL"):
        await provider.create("box-1", SandboxEnsureRequest(env={}))
