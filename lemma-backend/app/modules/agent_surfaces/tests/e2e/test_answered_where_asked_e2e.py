"""Each message is answered where it was asked, whatever is said elsewhere meanwhile.

A member emails the agent and, while it works on that, sends it something
else on WhatsApp. When one conversation served their email, their WhatsApp
and Lemma, the run answering the email took the WhatsApp message in -- both
were "the person, mid-run" -- and its one answer went where the person had
written last: the email was answered on WhatsApp, and the WhatsApp message was
never answered at all.

Now each platform is a conversation of its own (``agent_conversations.
platform``): the WhatsApp message starts its own run at once, neither run
reads the other's words, and each answers where it was asked
(``agent.domain.run_places``).

Against Postgres, through the Resend and WhatsApp parsers and the fake Resend
and Meta servers, with the real harness and a scripted model.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

import pytest
from sqlalchemy import delete, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.run_places import ASKED_IN_PLACE_KEY, place_source
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    AgentRunStepModel,
    MessageModel,
)
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
    process_ingress_and_run_scripted,
    run_scripted_agent_run,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    deliveries_of_line,
)
from app.modules.channels.infrastructure.answer_models import ChannelLineAnswerModel
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelSourceModel,
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


def _asked_at(metadata: dict | None) -> UUID | None:
    """The source a row says it was asked at, as the server stored it."""
    return place_source((metadata or {}).get(ASKED_IN_PLACE_KEY))


async def _logged_by(run_id: UUID) -> str:
    """Everything a run's steps put in the run log, as the model was sent it."""
    async with async_session_maker() as session:
        return "".join(
            await session.scalars(
                select(AgentRunStepModel.messages).where(
                    AgentRunStepModel.run_id == run_id
                )
            )
        )


async def _owed(text: str) -> tuple[str, ChannelMessageModel | None]:
    """The heard line that says ``text``: how its answer stands, and the
    line that answered it."""
    async with async_session_maker() as session:
        answer = (
            await session.scalars(
                select(ChannelLineAnswerModel)
                .join(
                    ChannelMessageModel,
                    ChannelMessageModel.id == ChannelLineAnswerModel.message_id,
                )
                .where(ChannelMessageModel.body == text)
            )
        ).one()
        line = (
            await session.get(ChannelMessageModel, answer.answer_message_id)
            if answer.answer_message_id
            else None
        )
        return answer.state, line


def _says(messages: list[dict], text: str) -> bool:
    return any(text in json.dumps(message) for message in messages)


async def _by_email_and_on_whatsapp(
    client, db_session, pod_id, user, fake_resend, fake_whatsapp, monkeypatch
) -> str:
    """One agent, reached by email and on WhatsApp; the address to email."""
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.lemma.work")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.client._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    agent = await _create_agent(client, pod_id)
    account = await _ensure_connector_account(
        db_session,
        user_id=user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": fake_resend.api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    email_surface = await _create_surface(
        client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
        agent_name=agent["name"],
    )
    await _create_surface(
        client, pod_id, config={"type": "WHATSAPP"}, agent_name=agent["name"]
    )
    await _set_user_mobile_number(db_session, user_id=user["id"], mobile_number=_PHONE)
    return (
        email_surface.get("surface_identity_email")
        or (
            await db_session.get(AgentSurface, UUID(email_surface["id"]))
        ).surface_identity_email
    )


def _email(user, address: str, message_id: str, text: str):
    return SurfacePlatformWebhookIngress(
        source="resend",
        payload=_resend_payload(
            sender_email=user["email"],
            assistant_address=address,
            message_id=message_id,
            text=text,
        ),
        headers={},
    )


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
    address = await _by_email_and_on_whatsapp(
        authenticated_client,
        db_session,
        test_pod["id"],
        fixed_test_user,
        fake_resend,
        fake_whatsapp,
        monkeypatch,
    )

    emailed = await _arrives(
        db_session,
        _email(fixed_test_user, address, "answered-where-asked-email", _EMAILED),
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
    # Two platforms, two conversations: the WhatsApp message waits behind
    # nothing, and neither run reads what was said on the other.
    assert whatsapped.conversation_id != emailed.conversation_id
    assert whatsapped.source_id != emailed.source_id
    [email_run] = await _runs(emailed.conversation_id)
    assert _asked_at(email_run.run_metadata) == emailed.source_id
    [whatsapp_run] = await _runs(whatsapped.conversation_id)
    assert _asked_at(whatsapp_run.run_metadata) == whatsapped.source_id

    await run_scripted_agent_run(
        db_session,
        conversation_id=emailed.conversation_id,
        user_id=emailed.user_id,
        pod_id=emailed.pod_id,
        agent_name=emailed.agent_name,
        script=[script_text(_EMAIL_ANSWER)],
    )

    # The email is answered by email, and nothing of it went to WhatsApp.
    emails = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert _says(emails, _EMAIL_ANSWER)
    assert not _says(message_store.get_all("WHATSAPP"), _EMAIL_ANSWER)
    assert _WHATSAPPED not in await _logged_by(email_run.id)
    state, answered_by = await _owed(_EMAILED)
    assert state == "ANSWERED"
    assert answered_by is not None and answered_by.body == _EMAIL_ANSWER
    assert answered_by.agent_run_id == email_run.id

    await run_scripted_agent_run(
        db_session,
        conversation_id=whatsapped.conversation_id,
        user_id=whatsapped.user_id,
        pod_id=whatsapped.pod_id,
        agent_name=whatsapped.agent_name,
        script=[script_text(_WHATSAPP_ANSWER)],
    )

    # Its own run answered it on WhatsApp, and only there.
    texts = await wait_for_messages(
        message_store,
        "WHATSAPP",
        predicate=lambda message: _WHATSAPP_ANSWER in json.dumps(message),
    )
    assert _says(texts, _WHATSAPP_ANSWER)
    assert not _says(message_store.get_all("RESEND"), _WHATSAPP_ANSWER)
    logged = await _logged_by(whatsapp_run.id)
    assert _WHATSAPPED in logged
    assert _EMAILED not in logged
    state, answered_by = await _owed(_WHATSAPPED)
    assert state == "ANSWERED"
    assert answered_by is not None and answered_by.agent_run_id == whatsapp_run.id


async def test_an_answer_whose_place_went_mid_run_stays_on_the_timeline(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    fake_whatsapp,
    message_store,
    monkeypatch,
    caplog,
):
    """The email place is removed while its run works. The answer is not sent
    to WhatsApp, where the member also wrote -- and it does not vanish: it is
    the agent's line in their channel, sent nowhere, and the loss is logged."""
    address = await _by_email_and_on_whatsapp(
        authenticated_client,
        db_session,
        test_pod["id"],
        fixed_test_user,
        fake_resend,
        fake_whatsapp,
        monkeypatch,
    )
    # They have written on WhatsApp before: a place the answer could wrongly
    # fall back to.
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="whatsapp",
            payload=_whatsapp_payload(
                text="Hi",
                message_id="wamid-place-went-earlier",
                phone_number_id="1234567890",
                waba_id="waba-001",
                sender_phone=_PHONE,
            ),
            headers={},
        ),
        script=[script_text("Hello.")],
    )
    emailed = await _arrives(
        db_session, _email(fixed_test_user, address, "place-went-email", _EMAILED)
    )
    async with async_session_maker() as session:
        await session.execute(
            delete(ChannelSourceModel).where(ChannelSourceModel.id == emailed.source_id)
        )
        await session.commit()

    with caplog.at_level(logging.WARNING):
        await run_scripted_agent_run(
            db_session,
            conversation_id=emailed.conversation_id,
            user_id=emailed.user_id,
            pod_id=emailed.pod_id,
            agent_name=emailed.agent_name,
            script=[script_text(_EMAIL_ANSWER)],
        )

    assert not _says(message_store.get_all("WHATSAPP"), _EMAIL_ANSWER)
    assert not _says(message_store.get_all("RESEND"), _EMAIL_ANSWER)
    [email_run] = [
        run
        for run in await _runs(emailed.conversation_id)
        if _asked_at(run.run_metadata) == emailed.source_id
    ]
    async with async_session_maker() as session:
        [answer] = (
            await session.scalars(
                select(ChannelMessageModel).where(
                    ChannelMessageModel.agent_run_id == email_run.id
                )
            )
        ).all()
    assert answer.body == _EMAIL_ANSWER
    assert answer.target_source_id is None and answer.source_id is None
    # Sent to no place; a member who hears of it may still be told.
    assert all(
        delivery.source_id is None for delivery in await deliveries_of_line(answer.id)
    )
    assert "agent_surfaces.reply_for_run.asked_place_gone.degraded" in caplog.text
