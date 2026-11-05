"""Who an inbound message is from, believed only on what the platform attests.

Three ways a sender used to be taken at their own word:

* a Slack profile phone or a Teams ``mobilePhone`` -- fields their owner types
  -- matched against a member's *verified* number;
* a Slack profile email, which a workspace admin can set to anybody's;
* on a bring-your-own app, any sender id at all: whoever holds the app's
  signing secret signs whatever body they like, and the cache, Lemma's own
  app's proofs and the shared bot's bindings all answer for the claimed id.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.tests.unit.test_identity_resolution import (
    _event,
    _FakeExternalRepo,
    _FakeUsers,
    _service,
)

pytestmark = pytest.mark.asyncio

_ADMINS_NUMBER = "+1 555 0100"
_INSTALLATION_LOOKUP = (
    "app.modules.agent_surfaces.services.verified_surface_identity."
    "resolve_shared_verified_identity"
)


def _resolutions_written(external: _FakeExternalRepo) -> list:
    return [
        call["resolved_user_id"]
        for call in external.calls
        if "resolved_user_id" in call
    ]


# -- H9: a phone the sender typed ---------------------------------------------


@pytest.mark.parametrize("platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS])
async def test_a_profile_phone_matching_an_admins_verified_number_is_not_the_admin(
    platform,
):
    """Anyone can type a pod admin's number into their own Slack profile."""
    admin = uuid4()
    users = _FakeUsers(by_phone_ids=[admin])
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(
        event=_event(platform=platform, phone=_ADMINS_NUMBER)
    )

    assert resolved.internal_user_id is None
    assert admin not in _resolutions_written(external)


@pytest.mark.parametrize("platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS])
async def test_a_typed_profile_phone_does_not_attest_a_cached_resolution(platform):
    """The binding path asks whether an attested field still names the cached
    person; a typed number is not one."""
    cached = uuid4()
    users = _FakeUsers(by_phone_ids=[cached])

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(platform=platform, phone=_ADMINS_NUMBER),
        require_proven_identity=True,
    )

    assert resolved.internal_user_id is None


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM]
)
async def test_a_number_the_platform_attests_still_resolves_its_verified_owner(
    platform,
):
    owner = uuid4()

    resolved = await _service(
        _FakeUsers(by_phone_ids=[owner]), _FakeExternalRepo()
    ).resolve(event=_event(platform=platform, phone=_ADMINS_NUMBER))

    assert resolved.internal_user_id == owner


# -- M17: a Slack profile email -----------------------------------------------


async def test_a_slack_profile_email_alone_is_not_the_member_it_names():
    """No proof in the workspace, so the sender is somebody to verify, not the
    member whose address an admin wrote on their profile."""
    member = uuid4()
    users = _FakeUsers(by_email=member)
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.SLACK, email="member@example.com")
    )

    assert resolved.internal_user_id is None
    assert member not in _resolutions_written(external)


async def test_a_slack_profile_email_does_not_attest_a_cached_resolution():
    cached = uuid4()

    resolved = await _service(
        _FakeUsers(by_email=cached), _FakeExternalRepo(cached_user_id=cached)
    ).resolve(
        event=_event(platform=SurfacePlatform.SLACK, email="member@example.com"),
        require_proven_identity=True,
    )

    assert resolved.internal_user_id is None


async def test_a_cached_slack_answer_no_proof_backs_is_forgotten_not_honoured():
    """What an email match, or a forged bring-your-own delivery, wrote before
    either was refused: the cache says who, never how, so a Slack answer
    stands only on Slack's proof. None here, so it is forgotten and the
    sender is nobody -- on the ordinary path, not only the binding one."""
    cached = uuid4()
    external = _FakeExternalRepo(cached_user_id=cached)

    resolved = await _service(_FakeUsers(by_email=cached), external).resolve(
        event=_event(platform=SurfacePlatform.SLACK, email="member@example.com")
    )

    assert resolved.internal_user_id is None
    forgotten = [call["forget"] for call in external.calls if "forget" in call]
    assert [one["resolved_user_id"] for one in forgotten] == [cached]


async def test_a_cached_teams_answer_is_not_asked_for_a_slack_proof():
    cached = uuid4()
    external = _FakeExternalRepo(cached_user_id=cached)

    resolved = await _service(_FakeUsers(), external).resolve(
        event=_event(platform=SurfacePlatform.TEAMS)
    )

    assert resolved.internal_user_id == cached
    assert not [call for call in external.calls if "forget" in call]


# -- M16: a sender id signed with a secret a customer holds -------------------


@pytest.mark.parametrize("platform", [SurfacePlatform.SLACK, SurfacePlatform.WHATSAPP])
async def test_a_customer_signed_sender_proven_only_elsewhere_is_not_that_person(
    platform,
):
    """Every answer keyed on the claimed id was made somewhere else: the cache
    (per workspace, written through by Lemma's app's proofs), the email and
    the attested number. None of them may speak for this app's deliveries."""
    victim = uuid4()
    users = _FakeUsers(by_email=victim, by_phone_ids=[victim])
    external = _FakeExternalRepo(cached_user_id=victim)
    installation_binding = AsyncMock(return_value=None)

    with patch(_INSTALLATION_LOOKUP, installation_binding):
        resolved = await _service(users, external).resolve(
            event=_event(
                platform=platform, email="victim@example.com", phone=_ADMINS_NUMBER
            ),
            installation_id=(installation := uuid4()),
            customer_held_secret=True,
        )

    assert resolved.internal_user_id is None
    assert installation_binding.await_args.args[2] == installation
    # Neither claimed for the victim nor cleared from them: a forged delivery
    # writes nobody's resolution.
    assert _resolutions_written(external) == []


async def test_a_binding_made_through_the_same_app_still_names_the_sender():
    person = uuid4()
    proven = ResolvedSurfaceUser(internal_user_id=person, external_user_id="ext-1")

    with patch(_INSTALLATION_LOOKUP, AsyncMock(return_value=proven)):
        resolved = await _service(_FakeUsers(), _FakeExternalRepo()).resolve(
            event=_event(platform=SurfacePlatform.SLACK),
            installation_id=uuid4(),
            customer_held_secret=True,
        )

    assert resolved.internal_user_id == person


# -- which org installs a person brought --------------------------------------


@pytest.mark.parametrize(
    ("platform", "behind", "config_source", "made_by_lemma", "customer_held"),
    [
        # Lemma's own credentials: Lemma's secret, whatever the platform.
        (SurfacePlatform.SLACK, False, None, False, False),
        (SurfacePlatform.WHATSAPP, False, None, False, False),
        # A WhatsApp Business app of the org's own is always theirs.
        (SurfacePlatform.WHATSAPP, True, None, False, True),
        # An org's own Slack app holds its secret, unless Lemma made the app.
        (SurfacePlatform.SLACK, True, "ORG_CUSTOM", False, True),
        (SurfacePlatform.SLACK, True, "ORG_CUSTOM", True, False),
        # Lemma's own Slack app, on an install of Lemma's.
        (SurfacePlatform.SLACK, True, "SYSTEM_DEFAULT", False, False),
        (SurfacePlatform.SLACK, True, None, False, False),
        # Telegram and Teams verify on something no customer holds.
        (SurfacePlatform.TELEGRAM, True, "ORG_CUSTOM", False, False),
        (SurfacePlatform.TEAMS, True, "ORG_CUSTOM", False, False),
    ],
)
def test_who_holds_the_secret_a_delivery_is_signed_with(
    platform, behind, config_source, made_by_lemma, customer_held
):
    from app.modules.agent_surfaces.services.customer_held_secrets import (
        customer_holds_the_secret,
    )

    assert (
        customer_holds_the_secret(
            platform,
            runs_as_connector=behind,
            config_source=config_source,
            app_made_by_lemma=made_by_lemma,
        )
        is customer_held
    )
