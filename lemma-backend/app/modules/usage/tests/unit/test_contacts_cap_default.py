"""The contacts cap: the default until an owner chooses, and their choice after."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.contacts_cap import (
    StoredContactsCap,
    cap_reached,
    effective_contacts_cap,
)

pytestmark = pytest.mark.unit


def test_an_organization_that_never_chose_gets_the_default():
    cap = effective_contacts_cap(None, default_usd=50)

    assert cap.limit_usd == Decimal(50)
    assert cap.is_default is True


def test_the_deployment_default_is_fifty_dollars():
    assert UsageSettings().usage_contacts_monthly_default_usd == 50


def test_an_owner_who_removed_the_cap_chose_no_limit_and_keeps_it():
    cap = effective_contacts_cap(StoredContactsCap(None), default_usd=50)

    assert cap.limit_usd is None
    assert cap.is_default is False
    assert not cap_reached(cap, consumed_usd=Decimal(10_000))


def test_an_owners_own_cap_replaces_the_default():
    cap = effective_contacts_cap(StoredContactsCap(Decimal(5)), default_usd=50)

    assert cap.limit_usd == Decimal(5)
    assert cap_reached(cap, consumed_usd=Decimal(5))
    assert not cap_reached(cap, consumed_usd=Decimal("4.99"))


def test_a_cap_of_zero_answers_nobody():
    cap = effective_contacts_cap(StoredContactsCap(Decimal(0)), default_usd=50)

    assert cap_reached(cap, consumed_usd=Decimal(0))
