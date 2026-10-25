"""Unit tests for SurfaceIdentityResolutionService — resolving an inbound
sender to an internal Lemma user via cache / email / unique phone.

A Telegram @username is never one of them: it is free text anyone can claim,
so a Telegram sender is known only by a contact they share themselves whose
number is verified on a Lemma account."""

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
    users = _FakeUsers(by_email=existing_user)
    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(platform=platform, email="person@example.com", username="person"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None


class _FakeExternalRepo:
    """Returns the resolved_user_id passed on write, else a preset cached id."""

    def __init__(self, *, cached_user_id=None, cached_phone=None):
        self._cached = cached_user_id
        self._phone = cached_phone
        self.calls: list[dict] = []

    async def upsert(self, **kwargs):
        self.calls.append(kwargs)
        resolved = kwargs.get("resolved_user_id", self._cached)
        if resolved is not None:
            self._cached = resolved
        self._phone = kwargs.get("phone") or self._phone
        return SimpleNamespace(
            resolved_user_id=resolved,
            external_user_id=kwargs.get("external_user_id"),
            email=kwargs.get("email"),
            phone=self._phone,
            display_name=kwargs.get("display_name"),
        )

    async def forget_resolution(self, **kwargs):
        self.calls.append({"forget": kwargs})
        if kwargs["resolved_user_id"] == self._cached:
            self._cached = None
        return 1


class _FakeUsers:
    """The directory port: an email lookup and a phone lookup, nothing else.

    It has no handle lookup because identity publishes none, so a resolution
    that tried to match a Telegram @username would fail here loudly.
    """

    def __init__(
        self,
        *,
        by_email=None,
        by_phone_ids=None,
        by_unverified_phone_ids=None,
    ):
        self._by_email = by_email
        self._by_phone_ids = by_phone_ids or []
        self._by_unverified_phone_ids = by_unverified_phone_ids or []
        self.lookups = 0

    async def user_id_by_email(self, email):
        self.lookups += 1
        return self._by_email

    async def user_ids_by_mobile_numbers(self, candidates, *, verified=True):
        self.lookups += 1
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
    assert users.lookups == 0


async def test_a_telegram_sender_is_nobody_until_they_share_a_contact():
    """An @username alone resolves nobody, and nothing is cached against it."""
    users = _FakeUsers()
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(event=_event(username="@Asha"))

    assert resolved.internal_user_id is None
    assert all(call.get("resolved_user_id") is None for call in external.calls)


async def test_a_self_shared_contact_matching_a_verified_number_resolves_and_is_cached():
    """The proof is the number Telegram attests, matched to a verified one."""
    owner = uuid4()
    users = _FakeUsers(by_phone_ids=[owner])
    external = _FakeExternalRepo()

    proven = await _service(users, external).resolve(
        event=_event(username="asha", phone="+1 555 0100")
    )
    later = await _service(_FakeUsers(), external).resolve(
        event=_event(username="asha")
    )

    assert proven.internal_user_id == owner
    assert any(call.get("resolved_user_id") == owner for call in external.calls)
    assert later.internal_user_id == owner


async def test_a_proven_telegram_account_stays_resolved_whatever_its_handle():
    """PS-SURF-012: a proven resolution is stable across later messages.

    The proof was the person's own number; changing a handle on Telegram
    afterwards is not a reason to ask them again.
    """
    owner = uuid4()
    external = _FakeExternalRepo(cached_user_id=owner)

    resolved = await _service(_FakeUsers(), external).resolve(
        event=_event(username="asha_renamed")
    )

    assert resolved.internal_user_id == owner


async def test_a_squatted_handle_never_binds_anyone_to_the_squatter():
    """Somebody once typed the victim's handle onto their own profile.

    The handle is matched against nothing, so the victim writing with it is a
    stranger -- and once they share their contact they are themselves, proven
    by their own verified number, never the squatter.
    """
    squatter, victim = uuid4(), uuid4()
    external = _FakeExternalRepo()

    unproven = await _service(_FakeUsers(), external).resolve(
        event=_event(username="victim")
    )
    proven = await _service(_FakeUsers(by_phone_ids=[victim]), external).resolve(
        event=_event(username="victim", phone="+1 555 0199")
    )

    assert unproven.internal_user_id is None
    assert proven.internal_user_id == victim
    assert all(call.get("resolved_user_id") != squatter for call in external.calls)


async def test_a_handle_taken_over_by_a_stranger_resolves_nobody():
    """A gave up the handle on Telegram; a stranger now writes with it.

    The stranger's account id is new to the cache and the handle is matched
    against nothing, so they are a stranger -- and sharing a number that no
    Lemma account has verified does not change that.
    """
    users = _FakeUsers(by_phone_ids=[])

    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(external_user_id="new-account", username="asha")
    )
    shared_a_number_nobody_verified = await _service(
        users, _FakeExternalRepo()
    ).resolve(
        event=_event(external_user_id="new-account", username="asha", phone="+1 5")
    )

    assert resolved.internal_user_id is None
    assert shared_a_number_nobody_verified.internal_user_id is None


async def test_a_teams_upn_on_a_verified_domain_resolves_by_email():
    """The Teams adapter hands over the UPN only on a domain the tenant
    verified, so it is the tenant's word rather than the sender's."""
    user_id = uuid4()
    users = _FakeUsers(by_email=user_id)
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TEAMS, email="a@b.test")
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


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM]
)
async def test_a_number_nobody_verified_does_not_route_a_chat_sender_to_its_holder(
    platform,
):
    """Anyone can type someone else's number onto their own profile.

    The sender's number is attested by the platform, but the profile side of the
    match is only a claim until its owner proves it. Routing on that claim hands
    the number's real owner's messages -- and the agent's replies to them -- to
    whoever wrote it on their profile, so an unverified match resolves nobody
    and the sender goes through signup instead.
    """
    squatter = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[squatter])
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(
        event=_event(platform=platform, phone="+1 555 0100")
    )

    assert resolved.internal_user_id is None
    assert squatter not in [call.get("resolved_user_id") for call in external.calls]


@pytest.fixture
def allow_unverified_phone_match():
    """The deployment opts in to routing on a number nobody verified."""
    from app.modules.agent_surfaces.config import surface_settings

    original = surface_settings.surface_allow_unverified_phone_match
    surface_settings.surface_allow_unverified_phone_match = True
    yield
    surface_settings.surface_allow_unverified_phone_match = original


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM]
)
async def test_an_opted_in_deployment_routes_a_number_only_one_profile_claims(
    platform, allow_unverified_phone_match, caplog
):
    claimant = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[claimant])
    external = _FakeExternalRepo()

    with caplog.at_level("WARNING"):
        resolved = await _service(users, external).resolve(
            event=_event(platform=platform, phone="+1 555 0100")
        )

    assert resolved.internal_user_id == claimant
    assert "unverified_phone_match_used" in caplog.text, (
        "routing on a claim nobody proved has to leave a trace"
    )


async def test_an_opted_in_deployment_never_matches_a_number_two_profiles_claim(
    allow_unverified_phone_match,
):
    users = _FakeUsers(by_unverified_phone_ids=[uuid4(), uuid4()])

    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id is None


async def test_an_opted_in_deployment_still_prefers_the_verified_owner(
    allow_unverified_phone_match,
):
    verified_id = uuid4()
    users = _FakeUsers(by_phone_ids=[verified_id], by_unverified_phone_ids=[uuid4()])

    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == verified_id


# --- a cached resolution outlives the policy that made it -------------------
#
# The cache stores who a sender resolved to, not how. A row written while
# unverified numbers were accepted would keep routing after the deployment
# stopped accepting them, so the setting would change nothing for anyone already
# matched.


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM]
)
async def test_a_resolution_made_on_an_unverified_number_is_dropped_when_it_stops_being_accepted(
    platform,
):
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached])
    external = _FakeExternalRepo(cached_user_id=cached)

    resolved = await _service(users, external).resolve(
        event=_event(platform=platform, phone="+1 555 0100")
    )

    assert resolved.internal_user_id is None
    assert external.calls[-1]["resolved_user_id"] is None, (
        "the stale row has to be cleared, or every later message re-reads it"
    )


async def test_that_same_resolution_stands_where_the_deployment_accepts_unverified_numbers(
    allow_unverified_phone_match,
):
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached])

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == cached


async def test_a_resolution_the_number_was_verified_for_stands():
    cached = uuid4()
    users = _FakeUsers(by_phone_ids=[cached], by_unverified_phone_ids=[cached])

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == cached


async def test_a_resolution_bound_by_something_other_than_the_phone_stands():
    """A row with no number on it is not the unverified-number check's to drop."""
    cached = uuid4()
    users = _FakeUsers()

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone=None)
    )

    assert resolved.internal_user_id == cached


async def test_a_telegram_handle_does_not_back_a_resolution_an_unverified_number_made():
    """The handle proves nothing, so it cannot keep what the number no longer may."""
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached])
    event = _event(platform=SurfacePlatform.TELEGRAM, phone="+1 555 0100")
    event = event.model_copy(update={"metadata": {"sender_username": "ada"}})

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=event
    )

    assert resolved.internal_user_id is None


async def test_an_unverified_number_is_not_cached_as_a_resolution():
    """A follow-up message must not inherit a match the first one refused."""
    users = _FakeUsers(by_unverified_phone_ids=[uuid4()])
    external = _FakeExternalRepo()

    await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone="+1 555 0100")
    )
    followup = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone=None)
    )

    assert followup.internal_user_id is None


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
    assert users.lookups == 0


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


async def test_proven_path_accepts_a_cache_the_profile_email_still_names():
    """A known Teams sender stays known when the binding path asks.

    `_match_proven_sender` answers WhatsApp and nothing else, so on Teams this
    cache entry is the only thing standing between an already-known person and
    being sent back through signup. Refusing every cache hit on this path did
    exactly that, to everybody, on their next message. (Slack's entry stands on
    Slack's own proof instead; its profile email is not the platform's word.)
    """
    cached = uuid4()
    users = _FakeUsers(by_email=cached)
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TEAMS, email="known@example.com"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id == cached


async def test_proven_path_refuses_a_cache_nothing_attested_supports():
    """The case the re-derivation exists for.

    A cached Telegram row that neither a profile email nor a verified number
    still names is not proof, whatever handle the sender writes with; the
    binding path must not read it back as one.
    """
    cached = uuid4()
    users = _FakeUsers()  # no email, no verified phone
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, username="@asha"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None


async def test_proven_path_refuses_a_cache_the_email_now_names_somebody_else():
    """Re-derivation has to agree, not merely find somebody.

    The address moved to another account between the cached write and this read.
    Returning the stale id here would bind the platform account to a person the
    attested field no longer points at.
    """
    cached = uuid4()
    users = _FakeUsers(by_email=uuid4())
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TEAMS, email="moved@example.com"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None


async def test_proven_path_refuses_a_cache_supported_only_by_an_unverified_phone():
    """Routing a message and binding an account are not the same permission.

    `_match_user_by_phone` falls back to an unverified number so an ordinary
    message can still reach somebody. `_cache_is_attested` reads only the
    verified half, so that fallback cannot become a binding.
    """
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached])
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+15551230000"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None
