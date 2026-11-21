"""Which pod a link spelled in names means, and who may be told.

The word and the disclosure rule, held without a database. The SQL that finds
the pods by word, and the route, are proved against Postgres in
``tests/e2e/test_pod_by_name_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.modules.identity.domain.organization_entities import (
    OrganizationJoinPolicy,
    OrganizationRole,
    may_self_join,
)
from app.modules.pod.domain.errors import PodNotFoundError
from app.modules.pod.domain.pod_addresses import NamedPod, findable_by_name, lemma_word
from app.modules.pod.domain.pod_entities import PodJoinPolicy
from app.modules.pod.services.pod_address_service import PodAddressService

pytestmark = pytest.mark.unit


def test_a_name_is_one_word_of_an_address_as_the_web_app_spells_it():
    # The same cases as `slug` in lemma-frontend/tests/named-address.test.ts.
    assert lemma_word("Sales") == "sales"
    assert lemma_word("Customer Support") == "customer-support"
    assert lemma_word("  R&D / Labs  ") == "r-d-labs"
    assert lemma_word("Café Nord") == "cafe-nord"
    assert lemma_word("🚀") == ""
    assert lemma_word("field_ops 2") == "field-ops-2"


@pytest.mark.parametrize(
    ("join_policy", "in_pod", "org_role", "may_join_org", "findable"),
    [
        # In it, or owning the organization: they see it by name already.
        (PodJoinPolicy.INVITE_ONLY, True, OrganizationRole.ORG_MEMBER, True, True),
        (PodJoinPolicy.INVITE_ONLY, False, OrganizationRole.ORG_OWNER, True, True),
        # Invite-only, to anyone else -- a member of its organization included.
        (PodJoinPolicy.INVITE_ONLY, False, OrganizationRole.ORG_MEMBER, True, False),
        (PodJoinPolicy.INVITE_ONLY, False, None, True, False),
        (PodJoinPolicy.INVITE_ONLY, False, None, False, False),
        # Open to the organization: to whoever is in it or may join it.
        (PodJoinPolicy.ORG_MEMBERS, False, OrganizationRole.ORG_MEMBER, True, True),
        (PodJoinPolicy.ORG_MEMBERS, False, None, True, True),
        (PodJoinPolicy.ORG_MEMBERS, False, None, False, False),
        # Open to everyone: to everyone.
        (PodJoinPolicy.PUBLIC, False, None, False, True),
    ],
)
def test_a_name_is_told_only_to_who_could_already_see_or_walk_in(
    join_policy, in_pod, org_role, may_join_org, findable
):
    assert (
        findable_by_name(
            join_policy=join_policy,
            in_pod=in_pod,
            org_role=org_role,
            may_join_org=may_join_org,
        )
        is findable
    )


@pytest.mark.parametrize(
    ("join_policy", "email_domain", "email", "verified", "may"),
    [
        (OrganizationJoinPolicy.PUBLIC, None, "a@gmail.com", False, True),
        (OrganizationJoinPolicy.EMAIL_DOMAIN, "acme.com", "a@acme.com", True, True),
        # An address nobody proved is not a claim on its domain.
        (OrganizationJoinPolicy.EMAIL_DOMAIN, "acme.com", "a@acme.com", False, False),
        (OrganizationJoinPolicy.EMAIL_DOMAIN, "acme.com", "a@other.com", True, False),
        (OrganizationJoinPolicy.EMAIL_DOMAIN, None, "a@acme.com", True, False),
        (OrganizationJoinPolicy.INVITE_ONLY, "acme.com", "a@acme.com", True, False),
    ],
)
def test_who_may_join_an_organization_without_being_asked(
    join_policy, email_domain, email, verified, may
):
    assert (
        may_self_join(
            join_policy=join_policy,
            email_domain=email_domain,
            user_email=email,
            user_verified=verified,
        )
        is may
    )


@dataclass(frozen=True)
class _Standing:
    organization_id: UUID
    member_id: UUID | None
    role: OrganizationRole | None
    may_join: bool


class _Organizations:
    def __init__(self, handle: str, standing: _Standing | None) -> None:
        self._handle = handle
        self._standing = standing

    async def at_handle(self, handle: str, user_id: UUID) -> _Standing | None:
        return self._standing if handle == self._handle else None


class _NamedPods:
    def __init__(
        self,
        organization_id: UUID,
        by_word: dict[str, list[NamedPod]],
        members: set[tuple[UUID, UUID]] = frozenset(),
    ) -> None:
        self._organization_id = organization_id
        self._by_word = by_word
        self._members = members

    async def called(self, organization_id: UUID, word: str) -> list[NamedPod]:
        if organization_id != self._organization_id:
            return []
        return self._by_word.get(word, [])[:2]

    async def has_member(self, pod_id: UUID, organization_member_id: UUID) -> bool:
        return (pod_id, organization_member_id) in self._members


ORG = uuid4()
OPEN = NamedPod(id=uuid4(), join_policy=PodJoinPolicy.ORG_MEMBERS)
CLOSED = NamedPod(id=uuid4(), join_policy=PodJoinPolicy.INVITE_ONLY)
TWIN_A = NamedPod(id=uuid4(), join_policy=PodJoinPolicy.PUBLIC)
TWIN_B = NamedPod(id=uuid4(), join_policy=PodJoinPolicy.PUBLIC)
PODS = {"sales": [OPEN], "board": [CLOSED], "ops": [TWIN_A, TWIN_B]}


def _service(
    standing: _Standing | None, members: set[tuple[UUID, UUID]] = frozenset()
) -> PodAddressService:
    return PodAddressService(
        organizations=_Organizations("acme", standing),
        named_pods=_NamedPods(ORG, PODS, members),
    )


async def test_a_name_open_to_whoever_may_join_the_organization_is_found():
    outsider = _Standing(ORG, member_id=None, role=None, may_join=True)
    assert await _service(outsider).find("acme", "sales", uuid4()) == OPEN.id


async def test_a_member_finds_an_invite_only_pod_they_are_in():
    member = uuid4()
    standing = _Standing(
        ORG, member_id=member, role=OrganizationRole.ORG_MEMBER, may_join=True
    )
    assert (
        await _service(standing, {(CLOSED.id, member)}).find("acme", "board", uuid4())
        == CLOSED.id
    )


@pytest.mark.parametrize(
    ("handle", "word", "standing"),
    [
        # No such organization.
        ("nobody", "sales", _Standing(ORG, None, None, True)),
        # No such pod.
        ("acme", "nothing", _Standing(ORG, None, None, True)),
        # Invite-only, and they are not in it.
        ("acme", "board", _Standing(ORG, None, None, True)),
        ("acme", "board", _Standing(ORG, uuid4(), OrganizationRole.ORG_MEMBER, True)),
        # Open to the organization, which they may not join.
        ("acme", "sales", _Standing(ORG, None, None, False)),
        # A word two pods make names neither, open as both are.
        ("acme", "ops", _Standing(ORG, None, OrganizationRole.ORG_OWNER, True)),
        ("acme", "", _Standing(ORG, None, OrganizationRole.ORG_OWNER, True)),
    ],
)
async def test_every_name_that_is_not_told_is_one_answer(handle, word, standing):
    with pytest.raises(PodNotFoundError):
        await _service(standing).find(handle, word, uuid4())
