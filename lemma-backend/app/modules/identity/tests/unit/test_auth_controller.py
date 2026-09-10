from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.identity.api.controllers.auth_controller import (
    DesktopAuthRequestCreate,
    DesktopAuthSessionRequest,
    altcha_config,
    complete_desktop_auth_request,
    create_desktop_auth_request,
    create_desktop_auth_session,
    verify_token,
)
from app.modules.identity.domain.user_entities import AuthUserEntity, UserEntity
from app.modules.identity.services.desktop_auth_handoff import (
    DesktopAuthRateLimitExceeded,
    DesktopAuthRequestNotFound,
    DesktopAuthRequestPending,
    DesktopAuthVerifierRejected,
)
from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_POD_ID,
    CLAIM_SCOPE,
)
from app.core.authorization.delegation import WorkloadPrincipalType


class _FakeUserService:
    def __init__(self, user: UserEntity):
        self.user = user
        self.requested_user_ids: list = []

    async def get_user(self, user_id):
        self.requested_user_ids.append(user_id)
        return self.user


class _FakePodMembership:
    def __init__(self, organization_id=None):
        self.organization_id = organization_id
        self.requested_pod_ids = []

    async def get_pod_organization_id(self, pod_id):
        self.requested_pod_ids.append(pod_id)
        return self.organization_id


@pytest.mark.asyncio
async def test_altcha_config_reports_runtime_setting(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_altcha_enabled", True)
    assert (await altcha_config()).enabled is True

    monkeypatch.setattr(settings, "auth_altcha_enabled", False)
    assert (await altcha_config()).enabled is False


@pytest.mark.asyncio
async def test_verify_token_returns_user_context():
    user_id = uuid4()
    request = SimpleNamespace(state=SimpleNamespace(user=AuthUserEntity(id=user_id)))
    service = _FakeUserService(
        UserEntity(
            id=user_id,
            email="lemma@lemma.work",
        )
    )

    pod_membership = _FakePodMembership()

    response = await verify_token(
        request=request,
        user_service=service,
        pod_membership=pod_membership,
    )

    assert response.user_id == user_id
    assert response.email == "lemma@lemma.work"
    assert response.pod_id is None
    assert response.organization_id is None
    assert response.function_id is None
    assert response.function_name is None
    assert response.scopes == []
    assert service.requested_user_ids == [user_id]
    assert pod_membership.requested_pod_ids == []


@pytest.mark.asyncio
async def test_verify_token_returns_function_delegation_claims():
    user_id = uuid4()
    pod_id = uuid4()
    function_id = uuid4()
    organization_id = uuid4()
    request = SimpleNamespace(
        state=SimpleNamespace(
            user=AuthUserEntity(id=user_id),
            auth_claims={
                CLAIM_ACTOR_TYPE: WorkloadPrincipalType.FUNCTION.value,
                CLAIM_ACTOR_ID: str(function_id),
                CLAIM_ACTOR_NAME: "sync_expense",
                CLAIM_POD_ID: str(pod_id),
                CLAIM_SCOPE: ["function:execute"],
            },
        )
    )
    service = _FakeUserService(
        UserEntity(
            id=user_id,
            email="lemma@lemma.work",
        )
    )

    pod_membership = _FakePodMembership(organization_id)

    response = await verify_token(
        request=request,
        user_service=service,
        pod_membership=pod_membership,
    )

    assert response.user_id == user_id
    assert response.pod_id == pod_id
    assert response.organization_id == organization_id
    assert response.function_id == function_id
    assert response.function_name == "sync_expense"
    assert response.scopes == ["function:execute"]
    assert pod_membership.requested_pod_ids == [pod_id]


class _FakeDesktopAuthStore:
    def __init__(self, user_id):
        self.user_id = user_id
        self.created_challenges: list[tuple[str, str]] = []
        self.completed: list[tuple[str, object]] = []
        self.consumed: list[tuple[str, str]] = []

    async def create(self, challenge, *, client_key):
        self.created_challenges.append((challenge, client_key))
        return SimpleNamespace(
            request_id="desktop-request-123456789",
            expires_in_seconds=300,
        )

    async def complete(self, request_id, user_id):
        self.completed.append((request_id, user_id))

    async def consume(self, request_id, verifier):
        self.consumed.append((request_id, verifier))
        return self.user_id


async def _async_value(value):
    return value


async def _rate_limited_create(*_args, **_kwargs):
    raise DesktopAuthRateLimitExceeded(37)


@pytest.mark.asyncio
async def test_desktop_auth_request_rate_limit_returns_retry_after(monkeypatch):
    from app.modules.identity.api.controllers import auth_controller

    monkeypatch.setattr(
        auth_controller,
        "get_desktop_auth_handoff_store",
        lambda: SimpleNamespace(create=_rate_limited_create),
    )

    with pytest.raises(HTTPException) as exc:
        await create_desktop_auth_request(
            DesktopAuthRequestCreate(code_challenge="a" * 43),
            SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
        )

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "37"}


@pytest.mark.asyncio
async def test_desktop_auth_handoff_creates_completes_and_exchanges(monkeypatch):
    from app.modules.identity.api.controllers import auth_controller

    user_id = uuid4()
    store = _FakeDesktopAuthStore(user_id)
    monkeypatch.setattr(
        auth_controller, "get_desktop_auth_handoff_store", lambda: store
    )
    monkeypatch.setattr(
        auth_controller,
        "create_desktop_browser_session",
        lambda request, exchanged_user_id: _async_value("session-handle"),
    )

    challenge = "a" * 43
    created = await create_desktop_auth_request(
        DesktopAuthRequestCreate(code_challenge=challenge),
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
    )
    browser_request = SimpleNamespace(
        state=SimpleNamespace(user=AuthUserEntity(id=user_id))
    )
    completed = await complete_desktop_auth_request(
        created.request_id,
        browser_request,
    )
    webview_request = SimpleNamespace(headers={"st-auth-mode": "cookie"})
    exchanged = await create_desktop_auth_session(
        DesktopAuthSessionRequest(
            request_id=created.request_id,
            code_verifier="b" * 43,
        ),
        webview_request,
        store,
    )

    assert store.created_challenges == [(challenge, "127.0.0.1")]
    assert store.completed == [(created.request_id, user_id)]
    assert store.consumed == [(created.request_id, "b" * 43)]
    assert completed.status == "complete"
    assert exchanged.user_id == user_id
    assert exchanged.session_handle == "session-handle"


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        # Still waiting for the person to finish signing in. The app polls on
        # this, so it must not read as an error it should give up on.
        (DesktopAuthRequestPending("id"), 409),
        # The caller does not hold the verifier that goes with this request.
        (DesktopAuthVerifierRejected("id"), 403),
        # Expired, consumed already, or never existed -- deliberately one
        # answer, so an id cannot be probed for whether it is real.
        (DesktopAuthRequestNotFound("id"), 404),
    ],
)
@pytest.mark.asyncio
async def test_a_handoff_that_cannot_be_exchanged_mints_no_session(failure, status):
    """Three refusals, three codes, and never a session.

    The desktop app branches on these: 409 means keep waiting, 403 means this
    is not its handoff, 404 means start again. They had no test, and the one
    thing they share -- that `create_desktop_browser_session` is not reached --
    had none either. A session minted on a rejected verifier is the whole
    exchange defeated.
    """

    async def refuse(*_args, **_kwargs):
        raise failure

    # If the exchange ever minted a session before consuming, the real
    # `create_desktop_browser_session` would run here against a request with no
    # state and fail loudly. Nothing is patched to notice that; being
    # unreachable is the property, and reaching it is what breaks.
    with pytest.raises(HTTPException) as exc:
        await create_desktop_auth_session(
            DesktopAuthSessionRequest(
                request_id="desktop-request-123456789", code_verifier="b" * 43
            ),
            SimpleNamespace(headers={"st-auth-mode": "cookie"}),
            SimpleNamespace(consume=refuse),
        )

    assert exc.value.status_code == status


@pytest.mark.asyncio
async def test_the_exchange_refuses_a_request_that_is_not_in_cookie_mode():
    """The header decides where the session lands.

    Without cookie mode SuperTokens answers with tokens in the body instead of
    setting cookies, and the webview ends up with a handle it cannot use while
    the handoff has already been consumed -- one-shot, so there is no second
    attempt. Refused before `consume` is called, which is what keeps the
    request usable.
    """
    consumed = []
    store = SimpleNamespace(
        consume=lambda *args, **kwargs: consumed.append(args) or _async_value(None)
    )

    with pytest.raises(HTTPException) as exc:
        await create_desktop_auth_session(
            DesktopAuthSessionRequest(
                request_id="desktop-request-123456789", code_verifier="b" * 43
            ),
            SimpleNamespace(headers={}),
            store,
        )

    assert exc.value.status_code == 400
    assert consumed == [], "a one-shot handoff must not be spent on a bad request"
