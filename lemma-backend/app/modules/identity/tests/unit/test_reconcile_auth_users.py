from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    EmailPolicyRejection,
)
from scripts import reconcile_auth_users


class _RecipeUserId:
    def __init__(self, value: str):
        self.value = value

    def get_as_string(self) -> str:
        return self.value


class _FakeSession:
    def __init__(self, user):
        self.user = user
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _user_id):
        return self.user

    async def commit(self):
        self.commits += 1


class _FakeCache:
    def __init__(self):
        self.invalidated = []

    async def invalidate(self, user_id):
        self.invalidated.append(user_id)


def _page(user_id, email="person@example.com"):
    login_method = SimpleNamespace(
        recipe_id="emailpassword",
        email=email,
        recipe_user_id=_RecipeUserId(str(user_id)),
    )
    return SimpleNamespace(
        users=[SimpleNamespace(login_methods=[login_method])],
        next_pagination_token=None,
    )


def _install_fakes(monkeypatch, *, local_user, verified=False, rejection=None):
    fake_session = _FakeSession(local_user)
    fake_cache = _FakeCache()
    revoked: list[str] = []

    async def list_users(*_args, **_kwargs):
        return _page(local_user.id, local_user.email)

    async def validate(email):
        if rejection is not None:
            raise EmailPolicyError(rejection)
        return email

    async def is_verified(*_args, **_kwargs):
        return verified

    async def revoke(user_id):
        revoked.append(user_id)

    monkeypatch.setattr(reconcile_auth_users, "initialize_supertokens", lambda: None)
    monkeypatch.setattr(reconcile_auth_users, "get_users_oldest_first", list_users)
    monkeypatch.setattr(
        reconcile_auth_users, "async_session_maker", lambda: fake_session
    )
    monkeypatch.setattr(reconcile_auth_users, "validate_auth_email", validate)
    monkeypatch.setattr(reconcile_auth_users, "is_email_verified", is_verified)
    monkeypatch.setattr(reconcile_auth_users, "revoke_all_sessions_for_user", revoke)
    monkeypatch.setattr(reconcile_auth_users, "get_user_cache", lambda: fake_cache)
    return fake_session, fake_cache, revoked


@pytest.mark.asyncio
async def test_reconciliation_dry_run_then_apply_is_state_idempotent(monkeypatch):
    user_id = uuid4()
    local_user = SimpleNamespace(
        id=user_id,
        email="person@example.com",
        is_verified=True,
        is_active=True,
        email_verified_at=object(),
        deactivated_at=None,
        deactivation_reason=None,
    )
    session, cache, revoked = _install_fakes(
        monkeypatch, local_user=local_user, verified=False
    )

    dry_run = await reconcile_auth_users.reconcile(apply=False)
    assert dry_run["verification_required"] == 1
    assert local_user.is_verified is True
    assert session.commits == 0
    assert revoked == []

    await reconcile_auth_users.reconcile(apply=True)
    await reconcile_auth_users.reconcile(apply=True)
    assert local_user.is_verified is False
    assert local_user.email_verified_at is None
    assert session.commits == 2
    assert cache.invalidated == [user_id, user_id]
    assert revoked == [str(user_id), str(user_id)]


@pytest.mark.asyncio
async def test_reconciliation_preserves_supertokens_verified_user(monkeypatch):
    user_id = uuid4()
    local_user = SimpleNamespace(
        id=user_id,
        email="verified@example.com",
        is_verified=False,
        is_active=True,
        email_verified_at=None,
        deactivated_at=None,
        deactivation_reason=None,
    )
    session, cache, revoked = _install_fakes(
        monkeypatch, local_user=local_user, verified=True
    )

    result = await reconcile_auth_users.reconcile(apply=True)
    assert result["verified"] == 1
    assert local_user.is_verified is True
    assert local_user.email_verified_at is not None
    assert session.commits == 1
    assert cache.invalidated == [user_id]
    assert revoked == []


@pytest.mark.asyncio
async def test_reconciliation_deactivates_only_permanent_invalid_evidence(monkeypatch):
    user_id = uuid4()
    local_user = SimpleNamespace(
        id=user_id,
        email="person@missing.example",
        is_verified=False,
        is_active=True,
        email_verified_at=None,
        deactivated_at=None,
        deactivation_reason=None,
    )
    _, _, revoked = _install_fakes(
        monkeypatch,
        local_user=local_user,
        rejection=EmailPolicyRejection("INVALID_DOMAIN", "dns"),
    )

    result = await reconcile_auth_users.reconcile(apply=True)
    assert result["deactivate_invalid_domain"] == 1
    assert local_user.is_active is False
    assert local_user.deactivation_reason == "INVALID_DOMAIN"
    assert revoked == [str(user_id)]
