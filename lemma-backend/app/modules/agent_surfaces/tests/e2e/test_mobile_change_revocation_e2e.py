"""A member's number changes: what the old one proved goes, and its route too.

``surfaces.mobile_changed`` revokes every identity bound to a number the account
no longer holds. A revoked identity may not keep a destination
(``ck_surface_identity_route_is_live``), so the revoke has to clear the route in
the same write. One that set ``revoked_at`` alone was refused by the database
for anybody whose number already talked to a pod, and the old number stayed
bound to them.

Against Postgres, with the number changed through the profile API so identity
is what announces it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _set_user_mobile_number,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    mobile_changed,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _a_number() -> str:
    return "+15550" + str(int(uuid4().hex[:7], 16)).zfill(9)


@pytest.mark.drives_work
@pytest.mark.parametrize("replaced", [False, True], ids=["cleared", "replaced"])
async def test_a_routed_phone_identity_is_revoked_with_its_route_when_the_number_changes(
    authenticated_client,
    fixed_test_user,
    test_pod,
    db_session,
    fake_whatsapp,
    monkeypatch,
    replaced,
):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.client._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    _, surface = await _create_agent_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    user_id = UUID(fixed_test_user["id"])
    old_number = _a_number()
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number=old_number
    )
    # Proved over WhatsApp by the number alone, never from a Lemma session, and
    # already talking to the pod: what onboarding leaves once a workspace is
    # chosen. Written directly, since how it got here is onboarding's story.
    binding_key = uuid4().hex
    db_session.add(
        VerifiedSurfaceIdentity(
            binding_key=binding_key,
            platform="WHATSAPP",
            tenant_id="",
            external_user_id=old_number.lstrip("+"),
            user_id=user_id,
            verified_phone=old_number,
            installation_surface_id=UUID(surface["id"]),
            pod_id=UUID(test_pod["id"]),
        )
    )
    await db_session.commit()
    routes = VerifiedSurfaceIdentityRepository(SqlAlchemyUnitOfWork(db_session))
    assert await routes.personal_route(binding_key) is not None

    changed = await authenticated_client.post(
        "/users/me/profile",
        json={"mobile_number": _a_number() if replaced else None},
    )
    assert changed.status_code == 201, changed.text
    await mobile_changed(user_id)

    db_session.expire_all()
    identity = await db_session.scalar(
        select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.binding_key == binding_key
        )
    )
    assert identity is not None
    assert identity.revoked_at is not None, (
        "the old number must stop proving who this is"
    )
    assert identity.verified_phone is None, "a revoked identity keeps no number"
    assert (identity.installation_surface_id, identity.pod_id) == (None, None), (
        "a revoked identity keeps nowhere to talk"
    )
    assert await routes.personal_route(binding_key) is None
