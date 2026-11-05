"""Slack's app-configuration calls: what Lemma keeps from them, and what it never
prints. Slack is a recorded double at the client; nothing reaches the network."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from slack_sdk.errors import SlackApiError

from app.modules.agent_surfaces.api.controllers.slack_app_creation_controller import (
    SlackConfigurationTokenRequest,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfacePlatformError,
    SlackConfigurationTokenRefusedError,
    SlackRateLimitedError,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (  # noqa: E501
    ConfigurationTokenPair,
)
from app.modules.agent_surfaces.platforms.slack import app_configuration

pytestmark = pytest.mark.asyncio

REFRESH = "xoxe-1-refresh-plaintext"
ACCESS = "xoxe.xoxp-1-access-plaintext"
CLIENT_SECRET = "client-secret-plaintext"
SIGNING_SECRET = "signing-secret-plaintext"


@dataclass
class _Response:
    data: dict[str, object]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class _Client:
    """Slack's client, doing what the real one does with its arguments: the
    token it is built with and the refresh token it is handed are strings."""

    answer: dict[str, object] | None = None
    error: _Response | None = None
    seen: list[object] = field(default_factory=list)

    def _reply(self) -> _Response:
        if self.error is not None:
            raise SlackApiError("refused", self.error)
        return _Response(self.answer or {})

    async def tooling_tokens_rotate(self, *, refresh_token: str) -> _Response:
        self.seen.append(refresh_token.encode())
        return self._reply()

    async def apps_manifest_create(self, *, manifest: dict[str, object]) -> _Response:
        return self._reply()

    async def apps_manifest_delete(self, *, app_id: str) -> _Response:
        return self._reply()


@pytest.fixture
def client(monkeypatch) -> _Client:
    fake = _Client()

    def build(token: SecretStr | None = None) -> _Client:
        if token is not None:
            fake.seen.append(token.get_secret_value().encode())
        return fake

    monkeypatch.setattr(app_configuration, "_client", build)
    return fake


async def test_a_rotation_naming_no_expiry_lasts_slacks_twelve_hours(client):
    client.answer = {"token": ACCESS, "refresh_token": "xoxe-1-next", "team_id": "T1"}

    rotated = await app_configuration.rotate_configuration_token(SecretStr(REFRESH))

    # Read as the epoch, it made every later use rotate again.
    assert rotated.expires_at - datetime.now(UTC) > timedelta(hours=11)
    assert client.seen == [REFRESH.encode()], "revealed only to the SDK"
    assert rotated.access_token.get_secret_value() == ACCESS


async def test_a_refused_refresh_token_is_its_own_error(client):
    client.error = _Response({"ok": False, "error": "invalid_refresh_token"})

    with pytest.raises(SlackConfigurationTokenRefusedError) as raised:
        await app_configuration.rotate_configuration_token(SecretStr(REFRESH))

    assert raised.value.details == {"slack_error": "invalid_refresh_token"}
    # The SDK's error holds the request, and the request holds the token.
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


async def test_slack_rate_limiting_says_when_to_try_again(client):
    client.error = _Response(
        {"ok": False, "error": "ratelimited"}, headers={"Retry-After": "17"}
    )

    with pytest.raises(SlackRateLimitedError) as raised:
        await app_configuration.create_app(SecretStr(ACCESS), {"x": 1})

    assert raised.value.status_code == 429
    assert raised.value.details == {"retry_after_seconds": 17}


async def test_any_other_refusal_to_create_is_a_platform_error(client):
    client.error = _Response({"ok": False, "error": "invalid_manifest"})

    with pytest.raises(AgentSurfacePlatformError, match="invalid_manifest"):
        await app_configuration.create_app(SecretStr(ACCESS), {"x": 1})


async def test_an_app_already_gone_counts_as_deleted(client):
    client.error = _Response({"ok": False, "error": "app_not_found"})

    assert await app_configuration.delete_app(SecretStr(ACCESS), "A1") is True


async def test_nothing_secret_is_printed_by_what_carries_it(client, caplog):
    client.answer = {
        "app_id": "A1",
        "credentials": {
            "client_id": "cid",
            "client_secret": CLIENT_SECRET,
            "signing_secret": SIGNING_SECRET,
        },
    }
    created = await app_configuration.create_app(SecretStr(ACCESS), {"x": 1})
    rotated = app_configuration.RotatedConfigurationToken(
        access_token=SecretStr(ACCESS),
        refresh_token=SecretStr(REFRESH),
        team_id="T1",
        slack_user_id="U1",
        expires_at=datetime.now(UTC),
    )
    pair = ConfigurationTokenPair(SecretStr(ACCESS), SecretStr(REFRESH))
    request = SlackConfigurationTokenRequest(refresh_token=REFRESH)

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("test").warning("carried %r %r %r", created, rotated, pair)
    printed = " ".join(
        [repr(created), repr(rotated), repr(pair), repr(request), str(request)]
        + [record.getMessage() for record in caplog.records]
    )

    for secret in (REFRESH, ACCESS, CLIENT_SECRET, SIGNING_SECRET):
        assert secret not in printed
    assert created.client_secret.get_secret_value() == CLIENT_SECRET
    assert request.refresh_token.get_secret_value() == REFRESH
