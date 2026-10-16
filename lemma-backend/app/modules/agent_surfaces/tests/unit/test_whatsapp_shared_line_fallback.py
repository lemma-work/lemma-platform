"""The shared-line resolver fails soft, and the ownership check decides
nothing, on a database that refuses the connection.

A refused or timed-out connection can surface from the driver as a bare
``OSError`` rather than a SQLAlchemy error, and catching only the latter let it
escape both functions -- turning "verification works from settings" into an
exception on any deployment whose settings are merely incomplete.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.core.config import settings
from app.core.infrastructure.db.session import close_engine

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.contracts import whatsapp

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def database_refuses(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """The process's real engine, aimed at a port nothing listens on.

    A real refusal rather than a stand-in for the session maker, because what
    is under test is which exception the driver lets out -- a double would
    raise whatever the test chose.
    """
    await close_engine()
    monkeypatch.setattr(
        settings, "database_url", "postgresql+asyncpg://postgres:postgres@127.0.0.1:9/x"
    )
    try:
        yield
    finally:
        await close_engine()


async def test_an_unreachable_database_falls_back_to_settings(
    database_refuses: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Incomplete settings, so the resolver has to try the pool.
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", SecretStr("tok"))
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1555")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", None)

    with caplog.at_level("WARNING"):
        configuration = await whatsapp.global_whatsapp_configuration()

    assert configuration.access_token == "tok"
    assert configuration.phone_number_id == "1555"
    assert (
        "agent_surfaces.whatsapp_contract.shared_number_unreadable.degraded"
        in caplog.text
    )


async def test_an_unreachable_database_decides_nothing_about_a_pooled_number(
    database_refuses: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed, and not final: the outage is raised, so the webhook is answered
    with a retryable 503 and the verification job is retried, instead of a
    code dropped as "not our number"."""
    from app.core.infrastructure.db.transient_errors import is_database_unavailable

    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1555")

    with pytest.raises(Exception) as raised:
        await whatsapp.deployment_owns_whatsapp_number("1999")

    assert is_database_unavailable(raised.value)
