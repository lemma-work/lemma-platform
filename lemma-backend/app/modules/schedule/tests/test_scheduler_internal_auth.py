"""The job API is an internal control plane; nothing may reach it unauthenticated."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.modules.schedule.config import schedule_settings
from app.modules.schedule.scheduler.api.scheduler_controller import (
    require_scheduler_token,
)
from app.modules.schedule.scheduler.internal_auth import (
    ensure_internal_token,
    get_internal_token,
)


@pytest.fixture(autouse=True)
def _restore_token():
    original = schedule_settings.scheduler_internal_token
    yield
    schedule_settings.scheduler_internal_token = original


@pytest.mark.asyncio
async def test_unconfigured_token_denies_instead_of_waving_callers_through():
    """An unset token used to skip the check, leaving full CRUD open."""
    schedule_settings.scheduler_internal_token = None

    with pytest.raises(HTTPException) as excinfo:
        await require_scheduler_token(authorization=None)

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_wrong_and_missing_tokens_are_rejected():
    schedule_settings.scheduler_internal_token = SecretStr("expected-token")

    for header in (None, "", "Bearer wrong-token", "expected-token"):
        with pytest.raises(HTTPException) as excinfo:
            await require_scheduler_token(authorization=header)
        assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_matching_bearer_token_is_accepted():
    schedule_settings.scheduler_internal_token = SecretStr("expected-token")

    assert await require_scheduler_token(authorization="Bearer expected-token") is None


def test_ensure_internal_token_mints_once_and_keeps_a_configured_value():
    schedule_settings.scheduler_internal_token = None
    minted = ensure_internal_token()

    assert minted
    assert get_internal_token() == minted
    assert ensure_internal_token() == minted

    schedule_settings.scheduler_internal_token = SecretStr("operator-supplied")
    assert ensure_internal_token() == "operator-supplied"


def test_standalone_app_keeps_the_job_api_out_of_the_public_schema():
    """`/openapi.json` is served unauthenticated, so it must not advertise this."""
    from fastapi import FastAPI

    from app.standalone import build_standalone_app

    class _Worker:
        handle_signals = True

        async def run_async(self):  # pragma: no cover - never started here
            return None

    app = build_standalone_app(FastAPI(), _Worker())
    schema_paths = set(app.openapi().get("paths", {}))

    assert not any(path.startswith("/scheduler") for path in schema_paths)

    # The routes still exist and are guarded -- a 401 rather than a 404 shows
    # the job API is mounted, and that assembling it configured a token.
    from fastapi.testclient import TestClient

    response = TestClient(app).get("/scheduler/jobs")
    assert response.status_code == 401
