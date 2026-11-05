"""Rows to entities, in one place, so every repository reads them the same way."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import inspect

from app.modules.channels.domain.entities import (
    AgentAnswers,
    Asker,
    Channel,
    ChannelKind,
    ChannelMessage,
    ContactStatus,
    Delivery,
    DeliveryReasonCode,
    DeliveryStatus,
    ExternalAccount,
    Member,
    MemberRole,
    NotifyLevel,
    Principal,
    Question,
    QuestionAsker,
    QuestionKind,
    QuestionState,
    SeatedBy,
    Sender,
    SenderKind,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.channels.domain.parts import read_parts
from app.modules.channels.infrastructure.models import (
    ChannelCaseModel,
    ChannelMemberModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelSourceModel,
    ExternalAccountModel,
)

#: The most rows any one read returns.
MAX_LINES = 200


def now() -> datetime:
    return datetime.now(timezone.utc)


def account_entity(row: ExternalAccountModel) -> ExternalAccount:
    return ExternalAccount(
        id=row.id,
        pod_id=row.pod_id,
        platform=row.platform,
        workspace_key=row.workspace_key,
        bot_key=row.bot_key,
        surface_id=row.surface_id,
    )


def channel_entity(row: ChannelModel) -> Channel:
    case = _current_case(row)
    return Channel(
        id=row.id,
        pod_id=row.pod_id,
        kind=ChannelKind(row.kind),
        visibility=Visibility(row.visibility),
        title=row.title,
        topic=row.topic,
        member_user_id=row.member_user_id,
        contact_id=row.contact_id,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        archived_at=row.archived_at,
        answers_shared=row.answers_shared,
        agents_answer=row.agents_answer,
        hand_back_after_hours=row.hand_back_after_hours,
        last_seq=row.last_seq,
        replies=row.replies,
        last_line_id=row.last_line_id,
        last_line_at=row.last_line_at,
        **(_case_fields(case) if case is not None else {}),
    )


def _current_case(row: ChannelModel) -> ChannelCaseModel | None:
    """The channel's case, loaded with it (``selectin``); a channel just
    made, with none, is not asked for one."""
    unloaded = inspect(row).unloaded
    if "current_case" in unloaded and "current_case_id" not in unloaded:
        if row.current_case_id is None:
            return None
    return row.current_case


def _case_fields(case: ChannelCaseModel) -> dict[str, object]:
    return {
        "case_id": case.id,
        "assignee_user_id": case.assignee_user_id,
        "assigned_at": case.assigned_at,
        "assignee_last_line_at": case.assignee_last_line_at,
        "status": ContactStatus(case.status),
        "attention_at": case.attention_at,
        "attention_reason": case.attention_reason,
        "acknowledged_at": case.acknowledged_at,
    }


def source_entity(row: ChannelSourceModel, *, surface_id: UUID | None = None) -> Source:
    """``surface_id`` is the account's: the installation it is reached by now."""
    return Source(
        id=row.id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        account_id=row.account_id,
        surface_id=surface_id,
        platform=row.platform,
        kind=SourceKind(row.kind),
        is_private=row.is_private,
        external_channel_id=row.external_channel_id,
        external_user_id=row.external_user_id,
        title=row.title,
        last_inbound_at=row.last_inbound_at,
        delivery_ref=row.delivery_ref,
        owner_user_id=row.owner_user_id,
        answers_outsiders=row.answers_outsiders,
        shared_externally=row.shared_externally,
        invite_link=row.invite_link,
        request_id=row.request_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def member_entity(row: ChannelMemberModel) -> Member:
    return Member(
        channel_id=row.channel_id,
        user_id=row.user_id,
        contact_id=row.contact_id,
        agent_id=row.agent_id,
        role=MemberRole(row.role),
        notify=NotifyLevel(row.notify),
        notify_via=tuple(row.notify_via) if row.notify_via is not None else None,
        muted_until=row.muted_until,
        answers=AgentAnswers(row.answers) if row.answers else None,
        joined_at=row.created_at,
        seat_id=row.id,
        seated_by=SeatedBy(row.seated_by),
    )


def principal_of(user_id: UUID | None, contact_id: UUID | None) -> Principal | None:
    if user_id is not None:
        return Principal.member(user_id)
    if contact_id is not None:
        return Principal.contact(contact_id)
    return None


def sender_values(sender: Sender) -> dict[str, object]:
    """A sender as the columns of its line."""
    behalf = sender.on_behalf_of
    return {
        "sender_kind": sender.kind.value,
        "sender_user_id": sender.user_id,
        "sender_contact_id": sender.contact_id,
        "sender_agent_id": sender.agent_id,
        "on_behalf_user_id": behalf.user_id if behalf else None,
        "on_behalf_contact_id": behalf.contact_id if behalf else None,
        "sender_name": sender.display_name[:255] if sender.display_name else None,
    }


def nameless(row: ChannelMessageModel) -> bool:
    """Said by somebody nobody could name: no member, no contact."""
    return (
        row.sender_kind == SenderKind.CONTACT.value
        and row.sender_contact_id is None
        and row.sender_user_id is None
    )


def sender_of(row: ChannelMessageModel) -> Sender:
    return Sender(
        kind=SenderKind(row.sender_kind),
        user_id=row.sender_user_id,
        contact_id=row.sender_contact_id,
        agent_id=row.sender_agent_id,
        on_behalf_of=principal_of(row.on_behalf_user_id, row.on_behalf_contact_id),
        display_name=row.sender_name,
    )


def _asked_by(row: ChannelQuestionModel) -> Asker | None:
    if row.asker_conversation_id is None and row.asker_workflow_run_id is None:
        return None
    return Asker(
        kind=QuestionAsker(row.asker),
        conversation_id=row.asker_conversation_id,
        workflow_run_id=row.asker_workflow_run_id,
        key=row.asker_key,
    )


def question_entity(row: ChannelQuestionModel) -> Question:
    awaits = principal_of(row.awaits_user_id, row.awaits_contact_id)
    assert awaits is not None  # a CHECK holds it to exactly one
    return Question(
        message_id=row.message_id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        ref=row.ref,
        batch_id=row.batch_id,
        kind=QuestionKind(row.kind),
        state=QuestionState(row.state),
        question=row.question,
        awaits=awaits,
        asker=QuestionAsker(row.asker),
        asked_by=_asked_by(row),
        answer=row.answer,
        answered_by=principal_of(row.answered_by_user_id, row.answered_by_contact_id),
        answered_at=row.answered_at,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


def message_entity(
    row: ChannelMessageModel,
    *,
    question: ChannelQuestionModel | None = None,
    reply_count: int = 0,
    last_reply_at: datetime | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        id=row.id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        seq=row.seq,
        sender=sender_of(row),
        text=row.body,
        created_at=row.created_at,
        client_message_id=row.client_message_id,
        source_id=row.source_id,
        target_source_id=row.target_source_id,
        members_only=row.members_only,
        parts=read_parts(row.parts),
        conversation_id=row.conversation_id,
        agent_run_id=row.agent_run_id,
        external_message_id=row.external_message_id,
        external_thread_id=row.external_thread_id,
        reply_ref=row.reply_ref,
        reply_to_id=row.reply_to_id,
        thread_root_id=row.thread_root_id,
        edited_at=row.edited_at,
        deleted_at=row.deleted_at,
        shared_at=row.shared_at,
        deleted_by_user_id=row.deleted_by_user_id,
        question=question_entity(question) if question is not None else None,
        reply_count=reply_count,
        last_reply_at=last_reply_at,
    )


def delivery_entity(row: ChannelMessageDeliveryModel) -> Delivery:
    return Delivery(
        id=row.id,
        message_id=row.message_id,
        pod_id=row.pod_id,
        channel_id=row.channel_id,
        seq=row.seq,
        source_id=row.source_id,
        recipient_user_id=row.recipient_user_id,
        platform=row.platform,
        status=DeliveryStatus(row.status),
        reason_code=(DeliveryReasonCode(row.reason_code) if row.reason_code else None),
        reason=row.reason,
        from_seq=row.from_seq,
        lines=row.lines,
        urgent=row.urgent,
        external_message_id=row.external_message_id,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        claimed_at=row.claimed_at,
        sent_at=row.sent_at,
        created_at=row.created_at,
    )
