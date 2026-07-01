from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import status

from app.modules.agent_surfaces.tests.e2e.helpers import (
    fake_composio_email,
    fake_gmail,
    fake_outlook,
    fake_resend,
    fake_slack,
    fake_speech_provider,
    fake_teams,
    fake_telegram,
    fake_whatsapp,
    message_store,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import _read_root_env_value

# Re-export shared E2E fixtures so this module can run with --confcutdir.
test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
db_session = e2e_fixtures.db_session
scenario = e2e_fixtures.scenario


def _fireworks_credential() -> str | None:
    return (
        os.getenv("FIREWORKS_API_KEY")
        or _read_root_env_value("FIREWORKS_API_KEY")
        or _read_root_env_value("lemma_openai_api_key")
    )


@pytest_asyncio.fixture(scope="function")
async def fireworks_worker(e2e_settings):
    """Production streaq worker subprocess wired to the Fireworks ``system:lemma``
    runtime, sharing the test's Postgres/Redis.

    This is what makes surface e2e fully real: the in-process app publishes the
    webhook event to Redis, this worker consumes it, runs the agent on Fireworks,
    and the observer delivers the reply — nothing is simulated except the inbound
    webhook payload and the external platform API (a local fake). Skips when no
    Fireworks credential is configured.
    """
    import asyncio

    import redis.asyncio as redis

    from app.core.config import settings

    key = _fireworks_credential()
    if not key:
        pytest.skip(
            "Fireworks credential unavailable; set FIREWORKS_API_KEY to run "
            "fully-real surface agent e2e tests."
        )

    previous_setting = settings.lemma_openai_api_key
    settings.lemma_openai_api_key = key

    redis_client = redis.from_url(e2e_settings.redis_url, decode_responses=False)
    await redis_client.flushdb()
    await redis_client.aclose()

    log_path = f"/tmp/lemma_surface_worker_{uuid4().hex}.log"
    backend_root = Path(__file__).resolve().parents[5]

    def _read_log() -> str:
        try:
            with open(log_path, "r") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    # Write handle for the child; the parent reads via fresh handles so the two
    # never contend on a shared file offset.
    log_writer = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            [str(backend_root / ".venv/bin/streaq"), "run", "app.events:streaq_worker"],
            cwd=str(backend_root),
            env={
                **os.environ,
                "PYTHONPATH": ".",
                "PYTHONUNBUFFERED": "1",
                "DATABASE_URL": e2e_settings.database_url,
                "DATASTORE_DATABASE_URL": e2e_settings.datastore_database_url,
                "REDIS_URL": e2e_settings.redis_url,
                "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
                "ENVIRONMENT": "testing",
                "DEBUG": "true",
                "EMAIL_TRANSPORT": "filesystem",
                "EMAIL_OUTPUT_DIR": e2e_settings.email_output_dir,
                "GCS_STORAGE_BUCKET": "",
                "PUBLIC_BUCKET_NAME": "",
                "STORAGE_BACKEND": "local",
                "EMBEDDING_PROVIDER": "local",
                "LOCAL_OBJECT_STORAGE_ROOT": e2e_settings.local_object_storage_root,
                "LOCAL_FILE_STORAGE_ROOT": e2e_settings.local_file_storage_root,
                "COMPOSIO_CACHE_DIR": "/tmp/composio",
                "LEMMA_OPENAI_API_KEY": key,
                "lemma_openai_api_key": key,
            },
            stdout=log_writer,
            stderr=subprocess.STDOUT,
            text=True,
        )
        readiness_markers = (
            "`HandleAgentRunEvent` waiting for messages",
            "`HandleScheduleEvents` waiting for messages",
            "`HandleSurfaceWebhook` waiting for messages",
        )
        startup_ok = False
        for _ in range(600):
            if proc.poll() is not None:
                pytest.fail(
                    f"surface worker exited before startup (code={proc.returncode}).\n"
                    f"{_read_log()}"
                )
            if all(marker in _read_log() for marker in readiness_markers):
                startup_ok = True
                break
            await asyncio.sleep(0.1)
        if not startup_ok:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            pytest.fail(f"Timed out waiting for surface worker.\n{_read_log()}")

        try:
            yield proc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            settings.lemma_openai_api_key = previous_setting
            redis_client = redis.from_url(e2e_settings.redis_url, decode_responses=False)
            await redis_client.flushdb()
            await redis_client.aclose()
    finally:
        log_writer.close()


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    org_id = fixed_test_org["id"]
    payload = {
        "name": f"Surface Test Pod {uuid4()}",
        "slug": f"surface-test-pod-{uuid4()}",
        "type": "ASSISTANT",
        "organization_id": org_id,
    }
    response = await authenticated_client.post(
        "/pods",
        json=payload,
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


__all__ = [
    "authenticated_client",
    "async_client",
    "db_manager",
    "db_session",
    "e2e_settings",
    "fake_composio_email",
    "fake_gmail",
    "fake_outlook",
    "fake_resend",
    "fake_slack",
    "fake_speech_provider",
    "fake_teams",
    "fake_telegram",
    "fake_whatsapp",
    "fireworks_worker",
    "fixed_test_org",
    "fixed_test_user",
    "message_store",
    "postgres_container",
    "redis_container",
    "scenario",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_pod",
    "test_redis_url",
    "worker",
]
