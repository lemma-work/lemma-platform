from __future__ import annotations

import json

import pytest

from lemma_stack.config import store
from lemma_stack.host_pack import build_manifest, write_manifest
from lemma_stack.output import AdminError
from lemma_stack.release.manifest import parse


def _release():
    return parse(
        {
            "schema_version": 1,
            "version": "1.2.3",
            "min_admin_version": "0",
            "images": {
                "backend": "backend:test",
                "frontend": "frontend:test",
                "workspace": "workspace:test",
                "function": "function:test",
            },
        }
    )


def _pack(tmp_path):
    root = tmp_path / "pack"
    for relative in (
        "backend/python/bin/python3",
        "frontend/node/bin/node",
        "frontend/frontend-launcher.mjs",
        "frontend/app/server.js",
        "backend/assets/browser-sdk/lemma-client.js",
        "backend/assets/browser-sdk/lemma-ui.js",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    (root / "backend/assets/lemma-skills").mkdir(parents=True)
    return root


def test_builds_exact_backend_frontend_native_contract(paths, tmp_path):
    config = store.new_document()
    manifest = build_manifest(_pack(tmp_path), paths, config, _release(), provider="docker")

    assert [service["id"] for service in manifest["services"]] == [
        "backend",
        "frontend",
    ]
    # One chain: the sandbox runtime's own database and alembic history are gone.
    assert [setup["id"] for setup in manifest["setup"]] == ["migrations"]
    assert manifest["setup"][0]["max_attempts"] == 3
    assert manifest["setup"][0]["retry_backoff_seconds"] == 2
    assert manifest["setup"][0]["command"][1:] == [
        "-m",
        "alembic",
        "-c",
        "alembic.ini",
        "upgrade",
        "head",
    ]
    assert manifest["setup"][0]["env"]["DATABASE_URL"].endswith(":55432/lemma")
    backend, frontend = manifest["services"]
    assert backend["command"][1:4] == ["-m", "uvicorn", "local_app:app"]
    assert "--no-access-log" not in backend["command"]
    assert "FUNCTION_RUNTIME_SECRET" not in backend["env"]
    assert backend["env"]["WORKSPACE_CALLBACK_API_URL"] == ("http://host.lemma.internal:8711")
    assert backend["env"]["FUNCTION_RUNTIME_GATEWAY_URL"] == ("http://host.lemma.internal:8711")
    assert backend["env"]["WORKSPACE_IMAGE"] == "workspace:test"
    assert backend["env"]["FUNCTION_IMAGE"] == "function:test"
    assert backend["env"]["BROWSER_SDK_PATH"].endswith("lemma-client.js")
    assert backend["env"]["SESSION_COOKIE_DOMAIN"] == ""
    assert frontend["dependencies"] == ["backend"]
    assert frontend["env"]["HOSTNAME"] == "127.0.0.1"
    assert frontend["env"]["PORT"] == "3711"
    assert frontend["env"]["NEXT_PUBLIC_SESSION_TOKEN_DOMAIN"] == ""
    assert frontend["health"]["url"] == "http://127.0.0.1:3711/runtime-config.js"


def test_user_environment_remains_last_wins(paths, tmp_path):
    config = store.new_document()
    store.set_value(config, "backend.env.REDIS_URL", "redis://custom:9999")
    store.set_value(config, "frontend.env.PORT", "4700")

    manifest = build_manifest(_pack(tmp_path), paths, config, _release())

    assert manifest["services"][0]["env"]["REDIS_URL"] == "redis://custom:9999"
    assert manifest["services"][1]["env"]["PORT"] == "4700"


def test_managed_runtime_contract_is_explicit(paths, tmp_path, monkeypatch):
    release = parse(
        {
            "schema_version": 1,
            "version": "1.2.3",
            "min_admin_version": "0",
            "images": {
                "backend": "backend:test",
                "frontend": "frontend:test",
                "workspace": "workspace@sha256:sandbox",
                "function": "function@sha256:sandbox",
            },
            "infra": {
                "postgres": "postgres@sha256:postgres",
                "redis": "redis@sha256:redis",
                "supertokens": "supertokens@sha256:supertokens",
            },
        }
    )
    monkeypatch.setenv("LEMMA_MANAGED_POSTGRES_PASSWORD", "a" * 64)
    monkeypatch.setenv("LEMMA_MANAGED_REDIS_PASSWORD", "b" * 64)
    monkeypatch.setenv("LEMMA_MANAGED_RUNTIME_CLI", "/signed/lemma-runtime")

    manifest = build_manifest(
        _pack(tmp_path), paths, store.new_document(), release, provider="lemma_local"
    )

    runtime = manifest["managed_runtime"]
    assert runtime["images"]["postgres"] == "postgres@sha256:postgres"
    assert runtime["ports"] == {
        "postgres": 55432,
        "redis": 56379,
        "supertokens": 53567,
        "backend": 8711,
        "frontend": 3711,
    }
    backend = manifest["services"][0]
    assert backend["env"]["WORKSPACE_PROVIDER"] == "lemma_local"
    assert backend["env"]["WORKSPACE_LOCAL_RUNTIME_CLI"] == "/signed/lemma-runtime"
    assert backend["env"]["WORKSPACE_LOCAL_CALLBACK_REQUIRED"] == "true"
    assert backend["env"]["WORKSPACE_LOCAL_CALLBACK_URL"] == ("http://host.lemma.internal:8711")
    assert backend["env"]["WORKSPACE_IMAGE"] == ("workspace@sha256:sandbox")
    assert backend["env"]["FUNCTION_IMAGE"] == ("function@sha256:sandbox")
    assert backend["env"]["WORKSPACE_ADD_HOST_GATEWAY"] == "false"
    assert backend["env"]["DATABASE_URL"].startswith("postgresql+asyncpg://postgres:" + "a" * 64)
    assert backend["env"]["REDIS_URL"].startswith("redis://:" + "b" * 64)
    assert backend["env"]["WORKSPACE_CALLBACK_API_URL"] == ("http://host.lemma.internal:8711")
    assert backend["env"]["FUNCTION_RUNTIME_GATEWAY_URL"] == ("http://host.lemma.internal:8711")


def test_missing_pack_file_is_actionable(paths, tmp_path):
    with pytest.raises(AdminError, match="backend Python"):
        build_manifest(tmp_path, paths, store.new_document(), _release())


def test_manifest_is_private_and_atomic(paths, tmp_path):
    destination = tmp_path / "run/host-pack.json"
    write_manifest(destination, {"secret": "value"})

    assert json.loads(destination.read_text()) == {"secret": "value"}
    assert destination.stat().st_mode & 0o777 == 0o600
    assert not list(destination.parent.glob("*.tmp-*"))
