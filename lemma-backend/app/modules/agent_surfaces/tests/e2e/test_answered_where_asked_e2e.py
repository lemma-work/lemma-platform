"""Each message is answered where it was asked, whatever is said elsewhere meanwhile.

A member has one conversation with the pod's agent across their email, their
WhatsApp and Lemma. They email the agent and, while it works on that, send it
something else on WhatsApp. The run answering the email used to take the
WhatsApp message in -- both were "the person, mid-run" -- and its one answer
went where the person had written last: the email was answered on WhatsApp,
and the WhatsApp message was never answered at all.

Now a run answers the place it was asked (``agent.domain.run_places``): the
WhatsApp message waits for the email's run to end, and its own turn answers
it on WhatsApp. That is the trade: the second place's answer comes after the
first's.

Against Postgres, through the Resend and WhatsApp parsers and the fake Resend
and Meta servers, with the real harness and a scripted model.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.queued_messages import STEERED_INTO_RUN
from app.modules.agent.events.run_followup_work import start_queued_followup_for
from app.modules.agent.infrastructure.models import AgentRunModel, MessageModel
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.composition import (
    build_surface_ingress,
    build_surface_turn_starter,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _create_surface,
    _ensure_connector_account,
    _resend_payload,
    _set_user_mobile_number,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    run_scripted_agent_run,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
)
from app.modules.connectors.domain.connector import AuthProvider

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work"),
]

_PHONE = "15550555123"
_EMAILED = "Did the second invoice get paid?"
_WHATSAPPED = "Also, can you move Thursday's call to Friday?"
_EMAIL_ANSWER = "The second invoice was paid on the 3rd."
_WHATSAPP_ANSWER = "Moved the call to Friday at 10."


async def _arrives(db_session, request: SurfacePlatformWebhookIngress):
    """A message reaches the pod and its turn is started -- not run."""
    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(request)
    assert isinstance(context, SurfaceChatContext)
    await uow.commit()
    with suppress_agent_run_enqueue():
        await build_surface_turn_starter(
            SessionUnitOfWorkFactory(async_session_maker)
        ).execute_chat(context)
    return context


async def _runs(conversation_id: UUID) -> list[AgentRunModel]:
    async with async_session_maker() as session:
        return list(
            await session.scalars(
                select(AgentRunModel)
                .where(AgentRunModel.conversation_id == conversation_id)
                .order_by(AgentRunModel.created_at)
            )
        )


async def _user_message(conversation_id: UUID, text: str) -> MessageModel:
    async with async_session_maker() as session:
        return (
            await session.scalars(
                select(MessageModel).where(
                    MessageModel.conversation_id == conversation_id,
                    MessageModel.role == "user",
                    MessageModel.text == text,
                )
            )
        ).one()


def _says(messages: list[dict], text: str) -> bool:
    return any(text in json.dumps(message) for message in messages)


async def test_a_message_sent_elsewhere_mid_run_is_answered_where_it_was_said(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.lemma.work")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.client._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    # One agent, reached by email and on WhatsApp.
    agent = await _create_agent(authenticated_client, pod_id)
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": fake_resend.api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    email_surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
        agent_name=agent["name"],
    )
    await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "WHATSAPP"},
        agent_name=agent["name"],
    )
    address = (
        email_surface.get("surface_identity_email")
        or (
            await db_session.get(AgentSurface, UUID(email_surface["id"]))
        ).surface_identity_email
    )
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number=_PHONE
    )

    emailed = await _arrives(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=address,
                message_id="answered-where-asked-email",
                text=_EMAILED,
            ),
            headers={},
        ),
    )
    # While that run works, the same member writes on WhatsApp.
    whatsapped = await _arrives(
        db_session,
        SurfacePlatformWebhookIngress(
            source="whatsapp",
            payload=_whatsapp_payload(
                text=_WHATSAPPED,
                message_id="wamid-answered-where-asked",
                phone_number_id="1234567890",
                waba_id="waba-001",
                sender_phone=_PHONE,
            ),
            headers={},
        ),
    )
    conversation_id = emailed.conversation_id
    assert whatsapped.conversation_id == conversation_id, "one conversation"
    assert whatsapped.source_id != emailed.source_id
    [email_run] = await _runs(conversation_id)
    assert email_run.run_metadata["asked_at_source"] == str(emailed.source_id)

    await run_scripted_agent_run(
        db_session,
        conversation_id=conversation_id,
        user_id=emailed.user_id,
        pod_id=emailed.pod_id,
        agent_name=emailed.agent_name,
        script=[script_text(_EMAIL_ANSWER)],
    )

    # The email is answered by email, and nothing of it went to WhatsApp.
    emails = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert _says(emails, _EMAIL_ANSWER)
    assert not _says(message_store.get_all("WHATSAPP"), _EMAIL_ANSWER)
    # The WhatsApp message was not taken into the email's run: still owed.
    waiting = await _user_message(conversation_id, _WHATSAPPED)
    assert (waiting.message_metadata or {}).get(STEERED_INTO_RUN) is None

    whatsapp_run_id = await start_queued_followup_for(
        conversation_id,
        email_run.id,
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
    )
    assert whatsapp_run_id is not None
    [_, whatsapp_run] = await _runs(conversation_id)
    assert whatsapp_run.id == whatsapp_run_id
    assert whatsapp_run.run_metadata["asked_at_source"] == str(whatsapped.source_id)
    await run_scripted_agent_run(
        db_session,
        conversation_id=conversation_id,
        user_id=whatsapped.user_id,
        pod_id=whatsapped.pod_id,
        agent_name=whatsapped.agent_name,
        script=[script_text(_WHATSAPP_ANSWER)],
    )

    # Its own turn answered it on WhatsApp -- after the email, and only there.
    texts = await wait_for_messages(
        message_store,
        "WHATSAPP",
        predicate=lambda message: _WHATSAPP_ANSWER in json.dumps(message),
    )
    assert _says(texts, _WHATSAPP_ANSWER)
    assert not _says(message_store.get_all("RESEND"), _WHATSAPP_ANSWER)
    claimed = await _user_message(conversation_id, _WHATSAPPED)
    assert claimed.message_metadata[STEERED_INTO_RUN] == str(whatsapp_run_id)
