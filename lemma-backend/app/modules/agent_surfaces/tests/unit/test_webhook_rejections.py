"""Every way a Telegram or Slack webhook is refused, through the service.

The signature arithmetic is tested in `app/core/webhooks`; what is tested here
is that the service applies it on each path a request can take -- the shared
platform endpoint and a surface's own endpoint -- and refuses with the status
that tells an operator which problem they have. 401 is a request we will not
believe; 503 is a deployment that cannot check one, and must not quietly accept
it instead.

Each refusal sits beside the request it refuses with one thing changed, so a
verifier that refused everything would fail the accepting case and one that
accepted everything would fail the rest.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import time
from collections.abc import Callable
from uuid import uuid4

import pytest

from app.core.config import settings as core_settings
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SlackWebhookVerificationCandidate,
    SurfaceWebhookAuthenticationError,
    SurfaceWebhookSecurityService,
)

pytestmark = pytest.mark.asyncio

_TELEGRAM_SECRET = "telegram-webhook-secret"
_SLACK_SECRET = "slack-signing-secret"
_BODY = b'{"type":"event_callback","event":{"type":"message","text":"hi"}}'


@pytest.fixture(autouse=True)
def _verification_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(core_settings, "environment", "production")
    monkeypatch.setattr(
        surface_settings, "telegram_webhook_secret", "telegram-webhook-secret"
    )
    monkeypatch.setattr(
        surface_settings, "slack_signing_secret", "slack-signing-secret"
    )


def _telegram_surface(secret: str | None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(type="TELEGRAM"),
        webhook_secret=secret,
    )


def _slack_headers(
    body: bytes, *, secret: str = _SLACK_SECRET, signed_at: int | None = None
) -> dict[str, str]:
    timestamp = str(int(time.time()) if signed_at is None else signed_at)
    digest = hmac.new(
        secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256
    ).hexdigest()
    return {"x-slack-request-timestamp": timestamp, "x-slack-signature": f"v0={digest}"}


async def _refusal(call: Callable[[], object]) -> int:
    """The status a refused request is answered with."""
    with pytest.raises(SurfaceWebhookAuthenticationError) as refused:
        result = call()
        if inspect.isawaitable(result):
            await result
    return refused.value.status_code


# ------------------------------------------------------------------ Telegram


def _telegram_platform(service: SurfaceWebhookSecurityService, headers: dict[str, str]):
    return lambda: service.verify_platform_request(
        platform="telegram", headers=headers, raw_body=b"{}"
    )


async def test_telegram_with_the_shared_secret_is_accepted() -> None:
    service = SurfaceWebhookSecurityService()

    accepted = await service.verify_platform_request(
        platform="telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": _TELEGRAM_SECRET},
        raw_body=b"{}",
    )

    assert accepted is None


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-secret-header"),
        pytest.param({"x-telegram-bot-api-secret-token": "a-guess"}, id="wrong-secret"),
        pytest.param({"x-telegram-bot-api-secret-token": ""}, id="empty-secret"),
        pytest.param(
            {"x-telegram-bot-api-secret-token": _TELEGRAM_SECRET + "x"},
            id="secret-with-a-suffix",
        ),
    ],
)
async def test_telegram_without_the_shared_secret_is_refused(
    headers: dict[str, str],
) -> None:
    service = SurfaceWebhookSecurityService()

    assert await _refusal(_telegram_platform(service, headers)) == 401


async def test_telegram_on_a_deployment_with_no_secret_cannot_be_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No configured secret is not a secret everybody knows."""
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", None)
    service = SurfaceWebhookSecurityService()
    headers = {"x-telegram-bot-api-secret-token": ""}

    assert await _refusal(_telegram_platform(service, headers)) == 503


async def test_a_telegram_surface_checks_its_own_secret_not_the_shared_one() -> None:
    """A bot of the person's own is answered by its own secret; the shared
    bot's secret, which every shared-bot update carries, must not open it."""
    service = SurfaceWebhookSecurityService()
    surface = _telegram_surface("this-bot-only")

    accepted = await service.verify_surface_request(
        surface=surface,
        headers={"x-telegram-bot-api-secret-token": "this-bot-only"},
        raw_body=b"{}",
    )
    assert accepted is None
    assert (
        await _refusal(
            lambda: service.verify_surface_request(
                surface=surface,
                headers={"x-telegram-bot-api-secret-token": _TELEGRAM_SECRET},
                raw_body=b"{}",
            )
        )
        == 401
    )


async def test_a_telegram_surface_with_no_secret_cannot_be_checked() -> None:
    service = SurfaceWebhookSecurityService()

    status_code = await _refusal(
        lambda: service.verify_surface_request(
            surface=_telegram_surface(None),
            headers={"x-telegram-bot-api-secret-token": _TELEGRAM_SECRET},
            raw_body=b"{}",
        )
    )

    assert status_code == 503


# --------------------------------------------------------------------- Slack


def _slack_platform(
    service: SurfaceWebhookSecurityService, headers: dict[str, str], body: bytes
):
    return lambda: service.verify_platform_request(
        platform="slack", headers=headers, raw_body=body
    )


async def test_slack_signed_now_with_the_signing_secret_is_accepted() -> None:
    service = SurfaceWebhookSecurityService()

    accepted = await service.verify_platform_request(
        platform="slack", headers=_slack_headers(_BODY), raw_body=_BODY
    )

    assert accepted is None


@pytest.mark.parametrize(
    "seconds_off",
    [pytest.param(-3600, id="an-hour-old"), pytest.param(3600, id="an-hour-ahead")],
)
async def test_a_replayed_slack_request_is_refused_though_its_signature_is_genuine(
    seconds_off: int,
) -> None:
    """The timestamp is what closes the replay window: the signature over it
    is authentic, and still must not be accepted outside the window."""
    service = SurfaceWebhookSecurityService()
    headers = _slack_headers(_BODY, signed_at=int(time.time()) + seconds_off)

    assert await _refusal(_slack_platform(service, headers, _BODY)) == 401


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        pytest.param({}, _BODY, id="unsigned"),
        pytest.param(
            {"x-slack-signature": _slack_headers(_BODY)["x-slack-signature"]},
            _BODY,
            id="no-timestamp",
        ),
        pytest.param(_slack_headers(_BODY, secret="a-guess"), _BODY, id="forged"),
        pytest.param(
            _slack_headers(_BODY), _BODY.replace(b"hi", b"rm"), id="body-altered"
        ),
    ],
)
async def test_a_slack_request_that_is_not_signed_by_us_is_refused(
    headers: dict[str, str], body: bytes
) -> None:
    service = SurfaceWebhookSecurityService()

    assert await _refusal(_slack_platform(service, headers, body)) == 401


async def test_slack_on_a_deployment_with_no_signing_secret_cannot_be_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(surface_settings, "slack_signing_secret", None)
    service = SurfaceWebhookSecurityService()

    assert await _refusal(_slack_platform(service, _slack_headers(_BODY), _BODY)) == 503


def _candidate(app_id: str, secret: str) -> SlackWebhookVerificationCandidate:
    return SlackWebhookVerificationCandidate(
        app_id=app_id, signing_secret=secret, receiver_surface_ids=(uuid4(),)
    )


async def test_a_slack_request_for_an_app_we_do_not_hold_is_refused() -> None:
    """Signed by a real app in the workspace, but addressed as one we hold no
    secret for. Trying the other apps' secrets would answer for an app we
    were not addressed as."""
    service = SurfaceWebhookSecurityService()

    status_code = await _refusal(
        lambda: service.verify_slack_request(
            headers=_slack_headers(_BODY, secret="ours"),
            raw_body=_BODY,
            api_app_id="A-UNKNOWN",
            candidates=[_candidate("A-OURS", "ours")],
        )
    )

    assert status_code == 401


async def test_a_slack_request_from_a_workspace_with_no_app_is_refused() -> None:
    service = SurfaceWebhookSecurityService()

    status_code = await _refusal(
        lambda: service.verify_slack_request(
            headers=_slack_headers(_BODY),
            raw_body=_BODY,
            api_app_id=None,
            candidates=[],
        )
    )

    assert status_code == 401


async def test_an_unnamed_slack_request_is_attributed_to_the_app_that_signed_it() -> (
    None
):
    """The accepting half of the two refusals above."""
    service = SurfaceWebhookSecurityService()

    verified = service.verify_slack_request(
        headers=_slack_headers(_BODY, secret="second"),
        raw_body=_BODY,
        api_app_id=None,
        candidates=[_candidate("A-FIRST", "first"), _candidate("A-SECOND", "second")],
    )

    assert verified.app_id == "A-SECOND"
