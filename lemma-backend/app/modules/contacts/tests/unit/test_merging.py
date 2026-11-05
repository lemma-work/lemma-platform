"""Proving a handle: when two contacts become one, and which one stays."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.contacts.domain.entities import (
    Contact,
    ContactHandle,
    IdentityKind,
    IdentityStrength,
)
from app.modules.contacts.domain.merging import (
    ProofStep,
    ProofTooWeak,
    plan_proof,
    survivor_of,
)

pytestmark = pytest.mark.unit

_POD = uuid4()
_EPOCH = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _contact(
    *kinds: IdentityKind, minutes: int = 0, contact_id: UUID | None = None
) -> Contact:
    identity = contact_id or uuid4()
    return Contact(
        id=identity,
        pod_id=_POD,
        display_name=None,
        created_at=_EPOCH + timedelta(minutes=minutes),
        identities=tuple(
            ContactHandle(
                id=uuid4(),
                contact_id=identity,
                kind=kind,
                value=f"{kind.value.lower()}-{index}",
                strength=(
                    IdentityStrength.SESSION
                    if kind is IdentityKind.WEB
                    else IdentityStrength.CHANNEL
                ),
                verified_at=_EPOCH,
            )
            for index, kind in enumerate(kinds)
        ),
    )


def test_a_contact_known_only_by_a_browser_is_anonymous():
    assert _contact(IdentityKind.WEB).is_anonymous
    assert not _contact(IdentityKind.WEB, IdentityKind.EMAIL).is_anonymous
    assert not _contact(IdentityKind.PHONE).is_anonymous


def test_a_vouched_contact_survives_an_anonymous_one_even_when_younger():
    anonymous = _contact(IdentityKind.WEB, minutes=0)
    vouched = _contact(IdentityKind.PHONE, minutes=60)

    assert survivor_of(anonymous, vouched) == (vouched, anonymous)
    assert survivor_of(vouched, anonymous) == (vouched, anonymous)


def test_between_two_vouched_contacts_the_older_survives():
    older = _contact(IdentityKind.PHONE, minutes=0)
    younger = _contact(IdentityKind.EMAIL, minutes=5)

    assert survivor_of(younger, older) == (older, younger)


def test_two_merges_of_the_same_pair_agree_on_the_survivor_when_all_else_ties():
    first = _contact(IdentityKind.PHONE, contact_id=UUID(int=1))
    second = _contact(IdentityKind.PHONE, contact_id=UUID(int=2))

    assert survivor_of(first, second) == survivor_of(second, first)
    assert survivor_of(first, second)[0].id == UUID(int=1)


def test_a_handle_nobody_holds_opens_a_contact_for_somebody_new():
    plan = plan_proof(claimant=None, holder=None, strength=IdentityStrength.CODE)

    assert plan.step is ProofStep.OPEN
    assert plan.survivor is None


def test_a_handle_nobody_holds_joins_the_contact_who_proved_it():
    visitor = _contact(IdentityKind.WEB)

    plan = plan_proof(claimant=visitor, holder=None, strength=IdentityStrength.CODE)

    assert plan.step is ProofStep.ATTACH
    assert plan.survivor == visitor


def test_somebody_new_proving_a_held_handle_is_its_holder():
    holder = _contact(IdentityKind.EMAIL)

    plan = plan_proof(claimant=None, holder=holder, strength=IdentityStrength.CODE)

    assert plan.step is ProofStep.HOLDER
    assert plan.survivor == holder


def test_proving_your_own_handle_again_changes_nothing():
    holder = _contact(IdentityKind.EMAIL)

    plan = plan_proof(claimant=holder, holder=holder, strength=IdentityStrength.CODE)

    assert plan.step is ProofStep.ALREADY_THEIRS
    assert plan.merged is None


@pytest.mark.parametrize(
    "strength",
    [IdentityStrength.CODE, IdentityStrength.HOST, IdentityStrength.CHANNEL],
)
def test_a_proven_handle_held_by_another_contact_merges_the_two(strength):
    visitor = _contact(IdentityKind.WEB, minutes=90)
    customer = _contact(IdentityKind.PHONE, IdentityKind.EMAIL, minutes=0)

    plan = plan_proof(claimant=visitor, holder=customer, strength=strength)

    assert plan.step is ProofStep.MERGE
    assert plan.survivor == customer
    assert plan.merged == visitor


@pytest.mark.parametrize(
    "strength", [IdentityStrength.MEMBER, IdentityStrength.SESSION]
)
def test_a_handle_held_by_another_contact_is_not_taken_on_a_weak_claim(strength):
    visitor = _contact(IdentityKind.WEB)
    customer = _contact(IdentityKind.EMAIL)

    with pytest.raises(ProofTooWeak):
        plan_proof(claimant=visitor, holder=customer, strength=strength)
