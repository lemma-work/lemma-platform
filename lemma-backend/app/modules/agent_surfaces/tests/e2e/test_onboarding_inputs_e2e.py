"""Native form handles cannot be moved between actors, steps or installations."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.services.onboarding_inputs import native_prompt_metadata
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    PrivateDeliveryUnavailable,
)
from app.modules.agent_surfaces.services.onboarding_submissions import (
    parse_native_submission,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "step,answer", [("awaiting_email", "ada@example.com"), ("awaiting_code", "012345")]
)
async def test_slack_modal_is_bound_to_actor_tenant_step_and_expiry(
    db_session, step, answer
):
    factory = SessionUnitOfWorkFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    binding = uuid4().hex
    destination = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.SLACK,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="Dprivate",
        external_channel_id="Dprivate",
        sender_external_user_id="Uowner",
        tenant_id="Tcompany",
        is_dm=True,
        message_text="",
        reply_target={"channel": "Dprivate"},
    )
    async with factory() as uow:
        pending = PendingChatOnboarding(
            binding_key=binding,
            platform="SLACK",
            step=step,
            destination=destination.model_dump(mode="json"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        uow.session.add(pending)
        await uow.session.flush()
        pending_id = pending.id
    metadata = await native_prompt_metadata(
        factory, binding_key=binding, platform=SurfacePlatform.SLACK
    )
    token = metadata["onboarding_blocks"][0]["elements"][0]["value"]
    payload = {
        "type": "view_submission",
        "user": {"id": "Uowner"},
        "team": {"id": "Tcompany"},
        "view": {
            "callback_id": "lemma_onboarding",
            "private_metadata": token,
            "state": {
                "values": {
                    "answer": {"answer": {"type": "plain_text_input", "value": answer}}
                }
            },
        },
    }

    async def parse(value, receiver_ids=None):
        return await parse_native_submission(
            value,
            platform=SurfacePlatform.SLACK,
            uows=factory,
            adapters=SurfacePlatformAdapterRegistry(),
            receiver_ids=receiver_ids,
        )

    result = await parse(payload)
    assert result.message_text == answer and result.is_dm
    with pytest.raises(PrivateDeliveryUnavailable):
        await parse({**payload, "user": {"id": "Uattacker"}})
    with pytest.raises(PrivateDeliveryUnavailable):
        await parse({**payload, "team": {"id": "Tother"}})
    with pytest.raises(PrivateDeliveryUnavailable):
        await parse(payload, [uuid4()])
    async with factory() as uow:
        pending = await uow.session.get(PendingChatOnboarding, pending_id)
        pending.step = "verified"
    with pytest.raises(PrivateDeliveryUnavailable):
        await parse(payload)
    async with factory() as uow:
        pending = await uow.session.get(PendingChatOnboarding, pending_id)
        pending.step = step
        pending.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(PrivateDeliveryUnavailable):
        await parse(payload)
