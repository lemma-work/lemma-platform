"""The contacts cap: the default until an owner chooses, and their choice after.

Whether a cap is reached is decided on the ``contacts_month`` window's running
total, as a run's requests are (``usage/tests/e2e/test_contacts_cap_check_e2e.py``).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.contacts_cap import (
    StoredContactsCap,
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


def test_an_owners_own_cap_replaces_the_default():
    cap = effective_contacts_cap(StoredContactsCap(Decimal(5)), default_usd=50)

    assert cap.limit_usd == Decimal(5)
    assert cap.is_default is False


def test_a_cap_of_zero_is_a_limit_of_nothing_not_no_limit():
    cap = effective_contacts_cap(StoredContactsCap(Decimal(0)), default_usd=50)

    assert cap.limit_usd == Decimal(0)
    assert cap.is_default is False


def test_an_owner_who_set_only_a_spend_still_gets_the_default_count():
    """A count nobody chose is the deployment's, not "no limit"."""
    cap = effective_contacts_cap(
        StoredContactsCap(Decimal(5)), default_usd=50, default_max_contacts=200
    )

    assert cap.limit_usd == Decimal(5)
    assert cap.max_contacts == 200


def test_an_owner_who_removed_the_count_chose_no_limit_on_it():
    cap = effective_contacts_cap(
        StoredContactsCap(Decimal(5), max_contacts=None, max_contacts_set=True),
        default_usd=50,
        default_max_contacts=200,
    )

    assert cap.max_contacts is None


def test_an_owner_who_set_only_a_count_still_gets_the_default_spend():
    cap = effective_contacts_cap(
        StoredContactsCap(
            None, monthly_limit_set=False, max_contacts=10, max_contacts_set=True
        ),
        default_usd=50,
        default_max_contacts=200,
    )

    assert cap.limit_usd == Decimal(50)
    assert cap.is_default is True
    assert cap.max_contacts == 10
