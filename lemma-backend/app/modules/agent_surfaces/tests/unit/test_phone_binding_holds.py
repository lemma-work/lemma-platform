"""A chat binding's phone proof, and what it does and does not depend on.

It used to require the binding's number to *be* the profile's verified number,
which made the profile the only place a person could have a phone -- an
existing user who signed up on WhatsApp from a second number had their web
profile overwritten to make the check pass.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.modules.agent_surfaces.services.verified_surface_identity import (
    phone_binding_holds,
)


def _user(phone: str | None, *, verified: bool = True):
    return SimpleNamespace(
        mobile_number=phone,
        mobile_verified_at=datetime.now(timezone.utc) if verified else None,
    )


@pytest.mark.parametrize("platform", ["WHATSAPP", "TELEGRAM"])
def test_a_self_proving_binding_stands_on_its_own_number(platform: str) -> None:
    identity = SimpleNamespace(verified_phone="+15550000002")
    assert phone_binding_holds(identity, _user("+15550000001"), platform)
    assert phone_binding_holds(identity, _user(None), platform)


def test_a_binding_without_a_phone_is_not_phone_bound() -> None:
    identity = SimpleNamespace(verified_phone=None)
    assert phone_binding_holds(identity, _user(None), "SLACK")


def test_other_phone_bound_platforms_keep_the_profile_rule() -> None:
    identity = SimpleNamespace(verified_phone="+15550000001")
    assert phone_binding_holds(identity, _user("+15550000001"), "TEAMS")
    assert not phone_binding_holds(identity, _user("+15550000002"), "TEAMS")
    assert not phone_binding_holds(
        identity, _user("+15550000001", verified=False), "TEAMS"
    )
