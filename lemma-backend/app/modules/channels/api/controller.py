"""A pod's channels: listed, made, read, changed, deleted.

Permissions are tabled in ``api.access``. A GET never writes: a member's own
channel with the pod is made when they join it (``events.member_joined_work``,
on ``PodMemberAdded``); one who joined before that has theirs made by
``POST /channels/me``, or by whatever first writes to them there. Until then
``GET /channels/me`` is 404 and the list does not show it.

A bot's groups are listed here too (``?source_surface=``), each with what the
bot keeps about it; that setting's own routes
(``/channels/{c}/sources/{s}``, ``/channels/whatsapp-groups``,
``/channels/telegram-links``) are ``agent_surfaces``', which talks to the
platforms. They are included first, so the literal segments win over
``/channels/{channel_id}``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid5

from fastapi import APIRouter, Header, Query, Response, status

from app.core.api.dependencies import UoWDep
from app.core.authorization.context import Context
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.api.access import (
    ERRORS,
    Caller,
    CallerDep,
    conflict,
    decode_cursor,
    encode_cursor,
    missing,
    require_contact_message,
    require_manage,
    unprocessable,
    visible,
    wrong_kind,
)
from app.modules.channels.api.schemas import (
    CONTACT_FIELDS,
    GROUP_FIELDS,
    NOT_CLEARABLE,
    ChannelCreateRequest,
    ChannelDetailResponse,
    ChannelListResponse,
    ChannelUpdateRequest,
    UserText,
)
from app.modules.channels.api.views import channel_detail, channel_rows
from app.modules.channels.contracts.source_settings import (
    SourceSettingsQuery,
    SourceSettingsResponse,
    source_settings_lister,
)
from app.modules.channels.domain.access import may_see
from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    ChannelKind,
    ContactStatus,
    Principal,
    SeatedBy,
    Visibility,
)
from app.modules.channels.infrastructure.cases import CaseRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.rows import now
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.contacts.contracts import contact_by_id
from app.modules.pod.contracts.members import pod_member_holds, pod_member_id

router = APIRouter(
    prefix="/pods/{pod_id}/channels", tags=["Channels"], responses=ERRORS
)

#: Namespace for channel ids derived from an ``Idempotency-Key``. Fixed for
#: good: a change would let a retry spanning a deploy make a second channel.
_IDEMPOTENT_CHANNELS = UUID("5f0e9a52-3c4b-4d7e-9b1a-8c2d6e4f1a37")

IdempotencyKey = Header(
    default=None, alias="Idempotency-Key", min_length=1, max_length=255
)


async def _contact_now(
    uow: UoWDep, pod_id: UUID, contact_id: UUID | None
) -> UUID | None:
    """A merged contact's id filters by the person it became, whose channel
    took theirs."""
    if contact_id is None:
        return None
    contact = await contact_by_id(uow, pod_id=pod_id, contact_id=contact_id)
    return contact.id if contact is not None else contact_id


@router.get(
    "",
    operation_id="channel.list",
    response_model=ChannelListResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_channels(
    pod_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    ctx: PodContextDep,
    kind: ChannelKind | None = Query(default=None),
    visibility: Visibility | None = Query(
        default=None,
        description=(
            "Only `PUBLIC` or only `PRIVATE` channels; a member's and a "
            "contact's own channel are private."
        ),
    ),
    q: UserText | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Only channels named by every one of these words, in any order and "
            "case: by title, or -- a member's or a contact's own channel -- by "
            "whose it is."
        ),
    ),
    archived: bool = Query(default=False),
    contact_id: UUID | None = Query(
        default=None, description="Only this contact's channel."
    ),
    assignee: str | None = Query(
        default=None,
        max_length=36,
        description="`me`, or a member's user id: contacts' channels assigned to them.",
    ),
    unassigned: bool = Query(default=False),
    status_: ContactStatus | None = Query(default=None, alias="status"),
    needs_person: bool = Query(
        default=False,
        description=(
            "Only contacts' channels waiting on a person: open, unassigned, "
            "and the agent off or asking for one."
        ),
    ),
    joined: bool = Query(default=False, description="Only channels the reader is in."),
    source_surface: str | None = Query(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "A bot's surface name: only the groups it is in, each with its "
            "`source_settings`. Takes reading the bot."
        ),
    ),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
) -> ChannelListResponse:
    """The channels the reader may see, most recently active first, each with
    what is new in it for them and its last line."""
    settings = (
        await _source_settings(uow, ctx, caller, source_surface)
        if source_surface is not None
        else None
    )
    channels = await ChannelRepository(uow.session).list_for(
        caller.viewer,
        kind=kind,
        visibility=visibility,
        titled=q,
        archived=archived,
        contact_id=await _contact_now(uow, pod_id, contact_id),
        assignee_user_id=_assignee(assignee, caller),
        unassigned=unassigned,
        status=status_,
        needs_person=needs_person,
        joined_only=joined,
        channel_ids=settings.keys() if settings is not None else None,
        # A pod admin finds a bot's private groups to join (or take over) from
        # its settings, before anybody has let them in.
        every_group=settings is not None and caller.manages_channels,
        limit=limit,
        before=decode_cursor(cursor),
    )
    last = channels[-1] if len(channels) == limit else None
    items = await channel_rows(
        uow, caller, channels, unseen=await _unseen(uow, caller, channels)
    )
    if settings is not None:
        items = [
            item.model_copy(update={"source_settings": settings[item.id]})
            for item in items
        ]
    return ChannelListResponse(
        items=items,
        next_cursor=encode_cursor(last.last_line_at, last.id) if last else None,
    )


async def _source_settings(
    uow: SqlAlchemyUnitOfWork, ctx: Context, caller: Caller, surface_name: str
) -> dict[UUID, SourceSettingsResponse]:
    """The named bot's sources' settings, by channel: one each, the newest."""
    lister = source_settings_lister()
    if lister is None:
        raise missing("Surface")
    listed = await lister(
        SourceSettingsQuery(
            uow=uow,
            ctx=ctx,
            pod_id=caller.pod_id,
            viewer_id=caller.user_id,
            surface_name=surface_name,
        )
    )
    by_channel: dict[UUID, SourceSettingsResponse] = {}
    for each in listed:
        by_channel.setdefault(each.channel_id, each)
    return by_channel


async def _unseen(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channels: Sequence[Channel]
) -> set[UUID]:
    """The listed channels the caller is shown but may not read."""
    joined = await MemberRepository(uow.session).channels_of(
        caller.user_id, [channel.id for channel in channels]
    )
    return {
        channel.id
        for channel in channels
        if not may_see(channel, caller.viewer, is_member=channel.id in joined)
    }


def _assignee(value: str | None, caller: Caller) -> UUID | None:
    if value is None:
        return None
    if value == "me":
        return caller.user_id
    try:
        return UUID(value)
    except ValueError:
        raise unprocessable("`assignee` is `me` or a user id") from None


@router.post(
    "",
    operation_id="channel.create",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def create_channel(
    pod_id: UUID,
    body: ChannelCreateRequest,
    uow: UoWDep,
    caller: CallerDep,
    idempotency_key: str | None = IdempotencyKey,
) -> ChannelDetailResponse:
    """A group made in Lemma: public unless asked otherwise, with its maker as
    its owner and the members and agents named in it. The same
    `Idempotency-Key` from the same member returns the group it made; sent
    with another title, topic or visibility, it is refused (422)."""
    channels = ChannelRepository(uow.session)
    title, topic = body.title, body.topic or None
    if idempotency_key is None:
        channel, made = (
            await channels.open_group(
                pod_id=caller.pod_id,
                title=title,
                topic=topic,
                visibility=body.visibility,
                created_by_user_id=caller.user_id,
            ),
            True,
        )
    else:
        channel, made = await channels.open_group_once(
            channel_id=uuid5(
                _IDEMPOTENT_CHANNELS,
                f"{caller.pod_id}:{caller.user_id}:{idempotency_key}",
            ),
            pod_id=caller.pod_id,
            title=title,
            topic=topic,
            visibility=body.visibility,
            created_by_user_id=caller.user_id,
        )
        if channel.pod_id != caller.pod_id:
            raise conflict("That Idempotency-Key was used elsewhere")
        if not made:
            # A replay shows the group only to whoever may still see it: made
            # private since, or they were taken out of it, it is not theirs.
            channel = await visible(uow, caller, channel.id)
            if (channel.title, channel.topic, channel.visibility) != (
                title,
                topic,
                body.visibility,
            ):
                raise unprocessable(
                    "That Idempotency-Key was used for another group; "
                    "send a new key for this one"
                )
    if made:
        await seat_people(uow, channel, body.members, added_by=caller.user_id)
        await seat_agents(
            uow,
            channel,
            [(agent.id, agent.answers) for agent in body.agents],
            added_by=caller.user_id,
        )
    await uow.commit()
    return await channel_detail(uow, caller, channel)


async def seat_people(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    user_ids: list[UUID],
    *,
    added_by: UUID,
) -> None:
    """Put members of the pod in a channel; anybody else is refused."""
    members = MemberRepository(uow.session)
    for user_id in dict.fromkeys(user_ids):
        if await pod_member_id(uow, channel.pod_id, user_id) is None:
            raise unprocessable("Only members of the pod can be added")
        await members.add_person(
            channel,
            Principal.member(user_id),
            seated_by=SeatedBy.ADDED,
            added_by_user_id=added_by,
        )


async def seat_agents(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    agents: list[tuple[UUID, AgentAnswers]],
    *,
    added_by: UUID,
) -> None:
    """Put the pod's agents in a channel, each answering as asked."""
    if not agents:
        return
    known = await Names(uow).agents(
        pod_id=channel.pod_id, agent_ids=[agent_id for agent_id, _ in agents]
    )
    members = MemberRepository(uow.session)
    for agent_id, answers in dict(agents).items():
        if agent_id not in known:
            raise unprocessable("Only the pod's agents can be added")
        await members.add_agent(
            channel,
            agent_id,
            answers=answers,
            added_by_user_id=added_by,
        )


# -- the caller's own channel ------------------------------------------------------


async def _direct_of(uow: SqlAlchemyUnitOfWork, caller: Caller) -> Channel | None:
    repository = ChannelRepository(uow.session)
    for archived in (False, True):
        found = await repository.list_for(
            caller.viewer, kind=ChannelKind.DIRECT, archived=archived, limit=1
        )
        if found:
            return found[0]
    return None


@router.get(
    "/me",
    operation_id="channel.me.get",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def get_my_channel(
    pod_id: UUID, uow: UoWDep, caller: CallerDep
) -> ChannelDetailResponse:
    """The reader's own channel with the pod; 404 until it is made."""
    channel = await _direct_of(uow, caller)
    if channel is None:
        raise missing()
    return await channel_detail(uow, caller, channel)


@router.post(
    "/me",
    operation_id="channel.me.create",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def create_my_channel(
    pod_id: UUID, uow: UoWDep, caller: CallerDep, response: Response
) -> ChannelDetailResponse:
    """Make the reader's own channel with the pod; 200 with it if it exists."""
    existing = await _direct_of(uow, caller)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return await channel_detail(uow, caller, existing)
    channel = await ChannelRepository(uow.session).direct(
        pod_id=caller.pod_id, user_id=caller.user_id
    )
    await uow.commit()
    return await channel_detail(uow, caller, channel)


# -- one channel ---------------------------------------------------------------------


@router.get(
    "/{channel_id}",
    operation_id="channel.get",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def get_channel(
    pod_id: UUID, channel_id: UUID, uow: UoWDep, caller: CallerDep
) -> ChannelDetailResponse:
    """One channel, with the places it is fed from, its members and agents,
    and how the reader hears of it."""
    channel = await visible(uow, caller, channel_id)
    return await channel_detail(uow, caller, channel)


@router.patch(
    "/{channel_id}",
    operation_id="channel.update",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def update_channel(
    pod_id: UUID,
    channel_id: UUID,
    body: ChannelUpdateRequest,
    uow: UoWDep,
    caller: CallerDep,
) -> ChannelDetailResponse:
    """Change exactly the fields named; `null` clears.

    A group: `title`, `topic`, `visibility`, `archived`, `answers_shared`
    (whether agents' answers made with the asker's access are read by
    everybody here, or kept to the asker until they share one) -- its
    owners' or an admin's. A contact's channel: `agents_answer`, `hand_back_after_hours`,
    `status`, `assignee` -- for who may write to contacts. Switching the agent
    on hands the contact back to it now: nobody is assigned.
    """
    channel = await visible(uow, caller, channel_id)
    named = set(body.model_fields_set)
    _check_fields(channel, body, named)
    if channel.kind is ChannelKind.GROUP:
        await require_manage(uow, caller, channel)
        await _change_group(uow, channel, body, named)
    elif named:
        require_contact_message(caller)
        await _change_contact(uow, caller, channel, body, named)
    await uow.commit()
    changed = await ChannelRepository(uow.session).get(channel.id)
    return await channel_detail(uow, caller, changed or channel)


def _check_fields(
    channel: Channel, body: ChannelUpdateRequest, named: set[str]
) -> None:
    allowed = {
        ChannelKind.GROUP: GROUP_FIELDS,
        ChannelKind.CONTACT: CONTACT_FIELDS,
        ChannelKind.DIRECT: frozenset[str](),
    }[channel.kind]
    wrong = named - allowed
    if wrong:
        raise wrong_kind(
            f"A {channel.kind.value} channel has no {', '.join(sorted(wrong))} to change"
        )
    cleared = {field for field in named & NOT_CLEARABLE if getattr(body, field) is None}
    if cleared:
        raise unprocessable(f"Cannot clear {', '.join(sorted(cleared))}")


async def _change_group(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    body: ChannelUpdateRequest,
    named: set[str],
) -> None:
    values: dict[str, object] = {}
    if "title" in named:
        values["title"] = body.title
    if "topic" in named:
        values["topic"] = body.topic or None
    if "visibility" in named:
        assert body.visibility is not None
        if body.visibility is Visibility.PUBLIC and any(
            source.is_private
            for source in await SourceRepository(uow.session).of_channel(channel.id)
        ):
            raise conflict("A private chat's channel stays private")
        values["visibility"] = body.visibility.value
    if "archived" in named:
        values["archived_at"] = now() if body.archived else None
    if "answers_shared" in named:
        values["answers_shared"] = body.answers_shared
    await ChannelRepository(uow.session).change(channel.id, values)


async def _change_contact(
    uow: SqlAlchemyUnitOfWork,
    caller: Caller,
    channel: Channel,
    body: ChannelUpdateRequest,
    named: set[str],
) -> None:
    cases = CaseRepository(uow.session)
    values: dict[str, object] = {}
    for field in ("agents_answer", "hand_back_after_hours"):
        if field in named:
            values[field] = getattr(body, field)
    handing_back = "agents_answer" in named and body.agents_answer
    if handing_back and body.assignee is not None:
        raise unprocessable("Switching the agent on hands the contact back to it")
    if "assignee" in named and body.assignee is not None:
        assignee = caller.user_id if body.assignee == "me" else body.assignee
        if not await pod_member_holds(
            uow, channel.pod_id, assignee, Permissions.CONTACT_READ
        ):
            raise unprocessable("Only a member who may see contacts can be assigned")
        await cases.assign(channel.id, assignee, by=caller.user_id)
        # Seated, so the contact's lines reach them: unread, inbox and push.
        await MemberRepository(uow.session).add_person(
            channel,
            Principal.member(assignee),
            seated_by=SeatedBy.JOINED if assignee == caller.user_id else SeatedBy.ADDED,
            added_by_user_id=caller.user_id,
        )
    elif handing_back or "assignee" in named:
        await cases.assign(channel.id, None, by=caller.user_id)
    if "status" in named:
        assert body.status is not None
        await cases.set_status(channel.id, body.status, by=caller.user_id)
    await ChannelRepository(uow.session).change(channel.id, values)


@router.delete(
    "/{channel_id}",
    operation_id="channel.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def delete_channel(
    pod_id: UUID, channel_id: UUID, uow: UoWDep, caller: CallerDep
) -> None:
    """Delete a group made in Lemma, and its timeline: its owners' or an
    admin's. A channel an outside place feeds is archived instead."""
    channel = await visible(uow, caller, channel_id)
    if channel.kind is not ChannelKind.GROUP:
        raise wrong_kind("Only a group can be deleted")
    if await SourceRepository(uow.session).of_channel(channel.id):
        raise conflict(
            "An outside place feeds this channel: archive it instead",
            code="CHANNEL_HAS_SOURCES",
        )
    await require_manage(uow, caller, channel)
    await ChannelRepository(uow.session).delete(channel.id)
    await uow.commit()
