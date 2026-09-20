"""Unit tests for SurfaceIdentityResolutionService — resolving an inbound
sender to an internal Lemma user via cache / telegram-username / email / unique
phone, with no dedicated coverage before this."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
    _phone_lookup_candidates,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS, SurfacePlatform.TELEGRAM]
)
async def test_unknown_onboarding_sender_cannot_use_profile_email_or_username(platform):
    existing_user = uuid4()
    users = _FakeUsers(by_email=existing_user, by_telegram=existing_user)
    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(platform=platform, email="person@example.com", username="person"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None
    assert users.telegram_lookups == []


class _FakeExternalRepo:
    """Returns the resolved_user_id passed on write, else a preset cached id."""

    def __init__(self, *, cached_user_id=None):
        self._cached = cached_user_id
        self.calls: list[dict] = []

    async def upsert(self, **kwargs):
        self.calls.append(kwargs)
        resolved = kwargs.get("resolved_user_id", self._cached)
        if resolved is not None:
            self._cached = resolved
        return SimpleNamespace(
            resolved_user_id=resolved,
            external_user_id=kwargs.get("external_user_id"),
            email=kwargs.get("email"),
            phone=kwargs.get("phone"),
            display_name=kwargs.get("display_name"),
        )


class _FakeUsers:
    def __init__(
        self,
        *,
        by_email=None,
        by_telegram=None,
        by_phone_ids=None,
        by_unverified_phone_ids=None,
    ):
        self._by_email = by_email
        self._by_telegram = by_telegram
        self._by_phone_ids = by_phone_ids or []
        self._by_unverified_phone_ids = by_unverified_phone_ids or []
        self.telegram_lookups: list[str] = []

    async def user_id_by_email(self, email):
        return self._by_email

    async def user_id_by_telegram_username(self, username):
        self.telegram_lookups.append(username)
        return self._by_telegram

    async def user_ids_by_mobile_numbers(self, candidates, *, verified=True):
        return list(self._by_phone_ids if verified else self._by_unverified_phone_ids)


def _service(
    users: _FakeUsers,
    external: _FakeExternalRepo,
    *,
    departed: set | None = None,
):
    """The service with identity's directory port answered by a fake.

    Injected rather than patched: which live person a sender resolves to is the
    decision this service exists to make, so the directory is a collaborator the
    test hands over -- and no real database is touched.

    ``departed`` names the ids whose accounts are gone. The live-user check is
    injected for the same reason the directory is: re-validating a cached
    ``resolved_user_id`` is part of that same decision, and a test of it has to
    be able to say who is still here.
    """
    gone = departed or set()

    async def no_verified_identity(event: ParsedInboundSurfaceEvent) -> None:
        return None

    async def live_user(user_id):
        return None if user_id in gone else user_id

    return SurfaceIdentityResolutionService(
        uow=SimpleNamespace(session=object()),
        external_user_repository=external,
        user_directory=users,
        verified_identity_lookup=no_verified_identity,
        live_user_lookup=live_user,
    )


def _event(
    *,
    platform: SurfacePlatform = SurfacePlatform.TELEGRAM,
    external_user_id: str | None = "ext-1",
    email: str | None = None,
    phone: str | None = None,
    username: str | None = None,
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="chat-1",
        sender_external_user_id=external_user_id,
        sender_email=email,
        sender_phone=phone,
        message_text="hi",
        metadata={"sender_username": username} if username else {},
    )


async def test_cache_hit_returns_resolved_user_without_matching():
    cached = uuid4()
    users = _FakeUsers()  # would resolve nothing
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(event=_event())
    assert resolved.internal_user_id == cached
    # No fresh match lookups happened (cache short-circuit).
    assert users.telegram_lookups == []


async def test_resolves_by_telegram_username():
    user_id = uuid4()
    users = _FakeUsers(by_telegram=user_id)
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(event=_event(username="@Asha"))
    assert resolved.internal_user_id == user_id
    # Username is normalized (stripped @, lowercased) before lookup.
    assert users.telegram_lookups == ["asha"]


async def test_resolves_by_email():
    user_id = uuid4()
    users = _FakeUsers(by_email=user_id)
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.SLACK, email="a@b.test")
    )
    assert resolved.internal_user_id == user_id


async def test_resolves_by_unique_phone_only():
    user_id = uuid4()
    users = _FakeUsers(by_phone_ids=[user_id])
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )
    assert resolved.internal_user_id == user_id


async def test_ambiguous_phone_does_not_resolve():
    users = _FakeUsers(by_phone_ids=[uuid4(), uuid4()])  # shared number
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )
    assert resolved.internal_user_id is None


async def test_unresolved_sender_returns_none_internal_id():
    users = _FakeUsers()  # no matches anywhere
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(email="nobody@x.test")
    )
    assert resolved.internal_user_id is None
    assert resolved.external_user_id == "ext-1"


async def test_verified_phone_match_has_priority_over_unverified_fallback():
    verified_id = uuid4()
    users = _FakeUsers(by_phone_ids=[verified_id], by_unverified_phone_ids=[uuid4()])
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == verified_id
    assert external.calls[-1]["resolved_user_id"] == verified_id


async def test_unique_unverified_phone_match_is_cached_for_followup_messages(
    monkeypatch,
):
    unverified_id = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[unverified_id])
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == unverified_id
    assert external.calls[-1]["resolved_user_id"] == unverified_id

    # Telegram exposes the phone only on the contact-share update. The cached
    # external identity must keep ordinary follow-up messages routed.
    followup = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone=None)
    )
    assert followup.internal_user_id == unverified_id


async def test_unverified_phone_fallback_rejects_ambiguous_matches():
    users = _FakeUsers(by_unverified_phone_ids=[uuid4(), uuid4()])

    with patch(
        "app.modules.agent_surfaces.services.identity_resolution_service.logger"
    ) as logger:
        resolved = await _service(users, _FakeExternalRepo()).resolve(
            event=_event(platform=SurfacePlatform.TELEGRAM, phone="+1 555 0100")
        )

    assert resolved.internal_user_id is None
    logger.error.assert_called_once_with(
        "agent_surfaces.identity.ambiguous_mobile_match",
        verification_state="unverified",
        candidate_count=2,
    )


async def test_verified_phone_match_rejects_and_logs_ambiguous_legacy_data():
    users = _FakeUsers(by_phone_ids=[uuid4(), uuid4()])

    with patch(
        "app.modules.agent_surfaces.services.identity_resolution_service.logger"
    ) as logger:
        resolved = await _service(users, _FakeExternalRepo()).resolve(
            event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
        )

    assert resolved.internal_user_id is None
    logger.error.assert_called_once_with(
        "agent_surfaces.identity.ambiguous_mobile_match",
        verification_state="verified",
        candidate_count=2,
    )


async def test_a_cached_sender_whose_account_is_gone_no_longer_resolves():
    """Deactivating somebody has to take their chat access with it.

    The cache is the one path into a run that nothing re-derives: every fresh
    lookup below it excludes deactivated and deleted rows, and a cache hit used
    to skip all of them. Without the liveness re-check this returns `departed`
    and the agent goes on to run as them, holding their pod's tools.
    """
    departed = uuid4()
    users = _FakeUsers()  # nothing matches them any more either
    external = _FakeExternalRepo(cached_user_id=departed)

    resolved = await _service(users, external, departed={departed}).resolve(
        event=_event()
    )

    assert resolved.internal_user_id is None
    assert resolved.external_user_id == "ext-1"


async def test_a_cached_sender_who_is_still_here_is_still_a_cache_hit():
    """The re-check must not cost the match lookups it exists to skip."""
    cached = uuid4()
    users = _FakeUsers()
    external = _FakeExternalRepo(cached_user_id=cached)

    resolved = await _service(users, external).resolve(event=_event(username="asha"))

    assert resolved.internal_user_id == cached
    assert users.telegram_lookups == []


async def test_a_proven_read_does_not_consume_an_unproven_cached_match():
    """A self-asserted handle must not become a proven identity by being cached.

    A Telegram `@username` is a free-text profile field. An ordinary group
    message resolves by it and writes the answer to the cache; the proven path
    then wrote a permanent `VerifiedSurfaceIdentity` from that same row. The
    row does not record what matched it, so the proven path may not read it at
    all -- without the skip this resolves to `claimed_by_handle`.
    """
    claimed_by_handle = uuid4()
    users = _FakeUsers(by_telegram=claimed_by_handle)
    external = _FakeExternalRepo()

    unproven = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, username="asha")
    )
    assert unproven.internal_user_id == claimed_by_handle  # now cached

    proven = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, username="asha"),
        require_proven_identity=True,
    )

    assert proven.internal_user_id is None


async def test_a_proven_sender_is_still_recognised_by_their_verified_phone():
    """Skipping the cache must not cost the proven path its one real match."""
    owner = uuid4()
    users = _FakeUsers(by_phone_ids=[owner])
    external = _FakeExternalRepo(cached_user_id=uuid4())  # an unproven cached id

    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100"),
        require_proven_identity=True,
    )

    assert resolved.internal_user_id == owner


async def test_phone_candidates_handle_provider_and_profile_formatting():
    assert _phone_lookup_candidates("919876543210") == [
        "+919876543210",
        "919876543210",
    ]
    assert _phone_lookup_candidates("+91 98765-43210") == [
        "+919876543210",
        "919876543210",
    ]
