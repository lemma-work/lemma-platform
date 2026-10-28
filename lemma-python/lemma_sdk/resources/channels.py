from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any, Final, Literal, TypedDict
from uuid import UUID

from ..openapi_client.api.agent_surfaces import agent_notice_send
from ..openapi_client.api.channels import (
    channel_case_list,
    channel_create,
    channel_delete,
    channel_delivery_list,
    channel_delivery_retry,
    channel_get,
    channel_inbox_get,
    channel_list,
    channel_me_create,
    channel_me_get,
    channel_member_add,
    channel_member_join,
    channel_member_leave,
    channel_member_list,
    channel_member_me_update,
    channel_member_remove,
    channel_member_update,
    channel_message_answer,
    channel_message_delete,
    channel_message_share,
    channel_message_file,
    channel_message_list,
    channel_message_post,
    channel_message_thread,
    channel_message_update,
    channel_read,
    channel_search,
    channel_update,
)
from ..openapi_client.models.channel_answer_request import ChannelAnswerRequest
from ..openapi_client.models.channel_cases_response import ChannelCasesResponse
from ..openapi_client.models.channel_create_request import ChannelCreateRequest
from ..openapi_client.models.channel_detail_response import ChannelDetailResponse
from ..openapi_client.models.channel_edit_request import ChannelEditRequest
from ..openapi_client.models.channel_kind import ChannelKind
from ..openapi_client.models.channel_list_response import ChannelListResponse
from ..openapi_client.models.channel_member_list_response import (
    ChannelMemberListResponse,
)
from ..openapi_client.models.channel_message_response import ChannelMessageResponse
from ..openapi_client.models.channel_messages_response import ChannelMessagesResponse
from ..openapi_client.models.channel_post_request import ChannelPostRequest
from ..openapi_client.models.channel_update_request import ChannelUpdateRequest
from ..openapi_client.models.contact_status import ContactStatus
from ..openapi_client.models.deliveries_response import DeliveriesResponse
from ..openapi_client.models.delivery_response import DeliveryResponse
from ..openapi_client.models.inbox_response import InboxResponse
from ..openapi_client.models.member_update_request import MemberUpdateRequest
from ..openapi_client.models.members_add_request import MembersAddRequest
from ..openapi_client.models.my_membership_update import MyMembershipUpdate
from ..openapi_client.models.notice_request import NoticeRequest
from ..openapi_client.models.notice_response import NoticeResponse
from ..openapi_client.models.read_request import ReadRequest
from ..openapi_client.models.read_response import ReadResponse
from ..openapi_client.models.search_response import SearchResponse
from ..openapi_client.models.sender_kind import SenderKind
from ..openapi_client.models.thread_response import ThreadResponse
from ..openapi_client.models.visibility import Visibility
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid, compact

if TYPE_CHECKING:
    from .sources import ChannelSources
    from .table_grants import ChannelTableGrants

Kind = Literal["MEMBER", "CONTACT", "AGENT"]
Answers = Literal["MENTIONED", "EVERY_LINE"]
Status = Literal["open", "waiting_on_contact", "closed"]


class _Keep:
    """Leave a field as it is: an update sends only the fields named, so
    ``None`` can mean "clear"."""

    def __repr__(self) -> str:
        return "KEEP"


KEEP: Final = _Keep()


class AgentSeat(TypedDict):
    id: str
    answers: Answers


def _unset(value: Any) -> Any:
    return UNSET if value is None else value


def _named(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not KEEP}


def _uuid_or_none(value: str | UUID | None) -> str | None:
    return str(as_uuid(value)) if value else None


def _seats(agents: Sequence[str | UUID | AgentSeat] | None) -> list[dict[str, str]]:
    seats: list[dict[str, str]] = []
    for agent in agents or []:
        if isinstance(agent, Mapping):
            seats.append(
                {
                    "id": str(as_uuid(agent["id"])),
                    "answers": agent.get("answers", "MENTIONED"),
                }
            )
        else:
            seats.append({"id": str(as_uuid(agent)), "answers": "MENTIONED"})
    return seats


class PodChannels(BoundResource):
    """The places a pod's members, contacts and agents talk.

    A channel is one shared timeline fed by any number of outside sources -- a
    WhatsApp chat, a Telegram group, a Slack channel, an email thread, a web
    chat -- or by none: a channel made in Lemma. ``DIRECT`` is one member and
    the pod, ``CONTACT`` one contact and the pod (a support inbox), ``GROUP``
    a conversation of several, ``public`` (every member sees it) or
    ``private`` (its members only).

    Members are addressed as ``(kind, id)``: ``MEMBER`` and a user id,
    ``CONTACT`` and a contact id, ``AGENT`` and an agent id. Updates send only
    the fields named; ``None`` clears one.
    """

    @cached_property
    def sources(self) -> "ChannelSources":
        """A group channel's sources from the bot's side, and bringing the bot
        into a group (a WhatsApp group, a Telegram link)."""
        from .sources import ChannelSources

        return ChannelSources(self._transport, org_id=self.org_id, pod_id=self.pod_id)

    @cached_property
    def table_grants(self) -> "ChannelTableGrants":
        """Tables a group's people outside the pod may read, or add rows to."""
        from .table_grants import ChannelTableGrants

        return ChannelTableGrants(
            self._transport, org_id=self.org_id, pod_id=self.pod_id
        )

    # -- channels ------------------------------------------------------------------

    def list(
        self,
        *,
        kind: Literal["DIRECT", "CONTACT", "GROUP"] | None = None,
        visibility: Literal["PUBLIC", "PRIVATE"] | None = None,
        q: str | None = None,
        archived: bool = False,
        contact_id: str | UUID | None = None,
        assignee: str | UUID | None = None,
        unassigned: bool = False,
        status: Status | None = None,
        needs_person: bool = False,
        joined: bool = False,
        source_surface: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ChannelListResponse:
        """The channels the caller may see, most recently active first, each
        with what is new in it for them and its last line.

        ``visibility`` keeps only public or only private channels (a member's
        and a contact's own are private); ``q`` keeps channels whose title
        holds these words, in any case.
        ``assignee`` is ``"me"`` or a member's user id (contacts' channels
        assigned to them); ``needs_person`` keeps contacts' channels waiting
        on a person (open, unassigned, the agent off or asking for one);
        ``joined`` keeps only channels the caller is in;
        ``source_surface`` (a bot's surface name) keeps the groups that bot is
        in, each with its ``source_settings``. Page by passing the last page's
        ``next_cursor`` as ``cursor``.
        """
        return self._call(
            channel_list,
            self._pod_uuid(),
            kind=ChannelKind(kind) if kind is not None else UNSET,
            visibility=Visibility(visibility) if visibility is not None else UNSET,
            q=_unset(q),
            archived=archived,
            contact_id=as_uuid(contact_id) if contact_id else UNSET,
            assignee=str(assignee) if assignee else UNSET,
            unassigned=unassigned,
            status=ContactStatus(status) if status is not None else UNSET,
            needs_person=needs_person,
            joined=joined,
            source_surface=_unset(source_surface),
            cursor=_unset(cursor),
            limit=limit,
        )

    def create(
        self,
        title: str,
        *,
        topic: str | None = None,
        visibility: Literal["PUBLIC", "PRIVATE"] = "PUBLIC",
        members: Sequence[str | UUID] | None = None,
        agents: Sequence[str | UUID | AgentSeat] | None = None,
        idempotency_key: str | None = None,
    ) -> ChannelDetailResponse:
        """Make a group in Lemma, with the caller as its owner and the members
        (user ids) and agents named in it. An agent is an id (answers when
        named) or ``{"id", "answers"}``. The same ``idempotency_key`` returns
        the group it made."""
        return self._call(
            channel_create,
            self._pod_uuid(),
            body={
                "title": title,
                "topic": topic,
                "visibility": visibility,
                "members": [str(as_uuid(member)) for member in members or []],
                "agents": _seats(agents),
            },
            body_model=ChannelCreateRequest,
            idempotency_key=_unset(idempotency_key),
        )

    def get(self, channel_id: str | UUID) -> ChannelDetailResponse:
        """One channel, with the places it is fed from, its members and agents,
        and how the caller hears of it."""
        return self._call(channel_get, self._pod_uuid(), as_uuid(channel_id))

    def mine(self) -> ChannelDetailResponse:
        """The caller's own channel with the pod. Not found until it is made:
        ``open_mine`` makes it."""
        return self._call(channel_me_get, self._pod_uuid())

    def open_mine(self) -> ChannelDetailResponse:
        """The caller's own channel with the pod, made if it is not there yet."""
        return self._call(channel_me_create, self._pod_uuid())

    def update(
        self,
        channel_id: str | UUID,
        *,
        title: str | None | _Keep = KEEP,
        topic: str | None | _Keep = KEEP,
        visibility: Literal["PUBLIC", "PRIVATE"] | None | _Keep = KEEP,
        archived: bool | None | _Keep = KEEP,
        answers_shared: bool | None | _Keep = KEEP,
        agents_answer: bool | None | _Keep = KEEP,
        hand_back_after_hours: int | None | _Keep = KEEP,
        assignee: str | UUID | None | _Keep = KEEP,
        status: Status | None | _Keep = KEEP,
    ) -> ChannelDetailResponse:
        """Change exactly the fields named; ``None`` clears one.

        A group: ``title``, ``topic``, ``visibility``, ``archived``,
        ``answers_shared`` (on, the default: an agent's answer made with the
        asker's own access is read by everybody in it; off, by the asker
        alone until they share it) -- its owners' or an admin's. A
        contact's channel: ``agents_answer``,
        ``hand_back_after_hours``, ``status``, ``assignee`` (``"me"`` or a
        user id).
        """
        body = _named(
            {
                "title": title,
                "topic": topic,
                "visibility": visibility,
                "archived": archived,
                "answers_shared": answers_shared,
                "agents_answer": agents_answer,
                "hand_back_after_hours": hand_back_after_hours,
                "assignee": (
                    assignee
                    if assignee == "me"
                    else str(as_uuid(assignee))
                    if isinstance(assignee, (str, UUID))
                    else assignee
                ),
                "status": status,
            }
        )
        return self._call(
            channel_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            body=body,
            body_model=ChannelUpdateRequest,
        )

    def set_agent_answers(
        self,
        channel_id: str | UUID,
        on: bool,
        *,
        hand_back_after_hours: int | None | _Keep = KEEP,
    ) -> ChannelDetailResponse:
        """Whether a contact's agent answers them. Off, only members do; on
        hands them back to the agent at once (nobody is assigned).
        ``hand_back_after_hours`` is how long a member who wrote to them holds
        them before the agent answers again; ``None`` restores the default."""
        return self.update(
            channel_id,
            agents_answer=on,
            hand_back_after_hours=hand_back_after_hours,
        )

    def delete(self, channel_id: str | UUID) -> None:
        """Delete a group made in Lemma and its timeline: its owners' or an
        admin's. A channel an outside place feeds is archived instead."""
        self._call(channel_delete, self._pod_uuid(), as_uuid(channel_id))

    def cases(
        self,
        channel_id: str | UUID,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ChannelCasesResponse:
        """A contact's channel's cases, the most recently opened first, each
        with what happened in it: who answered and when, reopens, how it
        ended. Page with ``next_cursor``."""
        return self._call(
            channel_case_list,
            self._pod_uuid(),
            as_uuid(channel_id),
            cursor=_unset(cursor),
            limit=limit,
        )

    # -- members -------------------------------------------------------------------

    def members(
        self,
        channel_id: str | UUID,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ChannelMemberListResponse:
        """Who is in a channel -- members, contacts and agents -- in the order
        they joined. Page with ``next_cursor``."""
        return self._call(
            channel_member_list,
            self._pod_uuid(),
            as_uuid(channel_id),
            cursor=_unset(cursor),
            limit=limit,
        )

    def add_members(
        self,
        channel_id: str | UUID,
        people: Sequence[str | UUID] = (),
        *,
        agents: Sequence[str | UUID | AgentSeat] | None = None,
    ) -> ChannelDetailResponse:
        """Add members of the pod (user ids) and the pod's agents to a group.
        An agent already in it answers as now asked."""
        return self._call(
            channel_member_add,
            self._pod_uuid(),
            as_uuid(channel_id),
            body={
                "people": [str(as_uuid(person)) for person in people],
                "agents": _seats(agents),
            },
            body_model=MembersAddRequest,
        )

    def update_member(
        self,
        channel_id: str | UUID,
        kind: Kind,
        member_id: str | UUID,
        *,
        role: Literal["owner", "member"] | None = None,
        answers: Answers | None = None,
    ) -> ChannelDetailResponse:
        """A person's role in a group, or when an agent in it answers."""
        return self._call(
            channel_member_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            SenderKind(kind),
            as_uuid(member_id),
            body=compact({"role": role, "answers": answers}),
            body_model=MemberUpdateRequest,
        )

    def remove_member(
        self, channel_id: str | UUID, kind: Kind, member_id: str | UUID
    ) -> None:
        """Take somebody out of a group -- its owners' or an admin's."""
        self._call(
            channel_member_remove,
            self._pod_uuid(),
            as_uuid(channel_id),
            SenderKind(kind),
            as_uuid(member_id),
        )

    def join(self, channel_id: str | UUID) -> ChannelDetailResponse:
        """Join a channel the caller may see: a public group, or -- holding
        ``contact.read`` -- a contact's channel."""
        return self._call(channel_member_join, self._pod_uuid(), as_uuid(channel_id))

    def leave(self, channel_id: str | UUID) -> None:
        """Leave a channel the caller is in."""
        self._call(channel_member_leave, self._pod_uuid(), as_uuid(channel_id))

    def set_notify(
        self,
        channel_id: str | UUID,
        notify: Literal["ALL", "MENTIONS", "NONE"] | _Keep = KEEP,
        *,
        via: list[str] | None | _Keep = KEEP,
        muted_until: str | None | _Keep = KEEP,
    ) -> ChannelDetailResponse:
        """How the caller hears of this channel's lines outside the app.
        ``via`` names the platforms (``None``: the pod's rule); ``muted_until``
        is an ISO time (``None`` unmutes)."""
        return self._call(
            channel_member_me_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            body=_named(
                {"notify": notify, "notify_via": via, "muted_until": muted_until}
            ),
            body_model=MyMembershipUpdate,
        )

    def mark_read(self, channel_id: str | UUID, seq: int) -> ReadResponse:
        """The caller has seen the channel up to line ``seq``. It never goes
        back, and never past the newest line."""
        return self._call(
            channel_read,
            self._pod_uuid(),
            as_uuid(channel_id),
            body={"seq": seq},
            body_model=ReadRequest,
        )

    # -- lines ---------------------------------------------------------------------

    def messages(
        self,
        channel_id: str | UUID,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
    ) -> ChannelMessagesResponse:
        """The timeline, oldest first: the newest page, the page before
        ``before_seq``, or the page after ``after_seq``. A thread's replies
        are left out and counted on its first line. ``next_before_seq`` and
        ``next_after_seq`` continue it."""
        return self._call(
            channel_message_list,
            self._pod_uuid(),
            as_uuid(channel_id),
            before_seq=_unset(before_seq),
            after_seq=_unset(after_seq),
            limit=limit,
        )

    def thread(
        self,
        channel_id: str | UUID,
        message_id: str | UUID,
        *,
        limit: int = 50,
        after_seq: int | None = None,
    ) -> ThreadResponse:
        """A thread: its first line, and its replies oldest first."""
        return self._call(
            channel_message_thread,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            after_seq=_unset(after_seq),
            limit=limit,
        )

    def post(
        self,
        channel_id: str | UUID,
        text: str,
        *,
        members_only: bool = False,
        source_id: str | UUID | None = None,
        thread_root_id: str | UUID | None = None,
        reply_to_id: str | UUID | None = None,
        mentions: Sequence[Mapping[str, str]] | None = None,
        parts: Sequence[Mapping[str, Any]] | None = None,
        client_message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ChannelMessageResponse:
        """Write in a channel.

        A reply goes where its thread is; otherwise to ``source_id``, or the
        place the channel last heard from -- unless ``members_only`` makes it a
        note to members. ``mentions`` names people and agents
        (``{"kind", "id"}``); a named agent answers. ``parts`` are typed parts
        (``mention``, ``file``, ``link``) sent as they are. The same
        ``client_message_id``, or ``idempotency_key`` from the same caller, is
        the same line.
        """
        all_parts: list[dict[str, Any]] = [
            {"type": "mention", "kind": mention["kind"], "id": mention["id"]}
            for mention in mentions or []
        ]
        all_parts.extend(dict(part) for part in parts or [])
        return self._call(
            channel_message_post,
            self._pod_uuid(),
            as_uuid(channel_id),
            body={
                "text": text,
                "members_only": members_only,
                "source_id": _uuid_or_none(source_id),
                "thread_root_id": _uuid_or_none(thread_root_id),
                "reply_to_id": _uuid_or_none(reply_to_id),
                "parts": all_parts,
                "client_message_id": client_message_id,
            },
            body_model=ChannelPostRequest,
            idempotency_key=_unset(idempotency_key),
        )

    def edit_message(
        self,
        channel_id: str | UUID,
        message_id: str | UUID,
        text: str | _Keep = KEEP,
        *,
        parts: Sequence[Mapping[str, Any]] | None | _Keep = KEEP,
    ) -> ChannelMessageResponse:
        """Change what one of the caller's own lines says. Only what is named
        changes; ``parts=None`` clears its parts. A copy already sent outside
        stays as sent."""
        body: dict[str, Any] = {}
        if not isinstance(text, _Keep):
            body["text"] = text
        if not isinstance(parts, _Keep):
            body["parts"] = (
                [dict(part) for part in parts] if parts is not None else None
            )
        return self._call(
            channel_message_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            body=body,
            body_model=ChannelEditRequest,
        )

    def delete_message(self, channel_id: str | UUID, message_id: str | UUID) -> None:
        """Delete a line: the caller's own, or anybody's in a channel the caller
        may change. It keeps its place, empty."""
        self._call(
            channel_message_delete,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
        )

    def answer(
        self,
        channel_id: str | UUID,
        message_id: str | UUID,
        answer: dict[str, Any],
    ) -> ChannelMessageResponse:
        """Answer what a line asks the caller: ``{"choice"}`` or ``{"text"}``
        for a question, ``{"decision"}`` for an approval, a form's fields."""
        return self._call(
            channel_message_answer,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            body={"answer": answer},
            body_model=ChannelAnswerRequest,
        )

    def share(
        self, channel_id: str | UUID, message_id: str | UUID
    ) -> ChannelMessageResponse:
        """Share an agent's answer made with the caller's own access with
        everybody in the channel (``can_share`` on the line). Until then only
        the caller reads it; sharing it again changes nothing."""
        return self._call(
            channel_message_share,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
        )

    def deliveries(
        self, channel_id: str | UUID, message_id: str | UUID
    ) -> DeliveriesResponse:
        """Where a line went outside Lemma, and what became of each send."""
        return self._call(
            channel_delivery_list,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
        )

    def file(
        self, channel_id: str | UUID, message_id: str | UUID, file_id: str | UUID
    ) -> bytes:
        """A file a line shares (a ``file`` part's ``file_id``), read as the
        line's writer reads it."""
        result = self._call(
            channel_message_file,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            as_uuid(file_id),
        )
        return result.payload.getvalue()

    def retry_delivery(
        self,
        channel_id: str | UUID,
        message_id: str | UUID,
        delivery_id: str | UUID,
    ) -> DeliveryResponse:
        """Try a failed or given-up send again, now."""
        return self._call(
            channel_delivery_retry,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(message_id),
            as_uuid(delivery_id),
        )

    # -- across the pod ------------------------------------------------------------

    def inbox(self) -> InboxResponse:
        """What is new for the caller across the pod: the badges, the questions
        waiting on them, and the unread lines naming them."""
        return self._call(channel_inbox_get, self._pod_uuid())

    def search(
        self,
        q: str,
        *,
        channel_id: str | UUID | None = None,
        kind: Literal["DIRECT", "CONTACT", "GROUP"] | None = None,
        sender: tuple[Kind, str | UUID] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        archived: bool = False,
        cursor: str | None = None,
        limit: int = 30,
    ) -> SearchResponse:
        """Lines matching every word, newest first, in the channels the caller
        sees -- or in one of them -- each with its ``rank`` and a plain-text
        ``snippet``. ``sender`` is ``(kind, id)``: only lines a member, contact
        or agent said; ``since``/``until`` bound when; ``archived`` searches
        archived channels too. Page with ``next_cursor``."""
        return self._call(
            channel_search,
            self._pod_uuid(),
            q=q,
            channel_id=as_uuid(channel_id) if channel_id else UNSET,
            kind=ChannelKind(kind) if kind is not None else UNSET,
            sender_kind=SenderKind(sender[0]) if sender is not None else UNSET,
            sender_id=as_uuid(sender[1]) if sender is not None else UNSET,
            since=_unset(since),
            until=_unset(until),
            archived=archived,
            cursor=_unset(cursor),
            limit=limit,
        )

    def notify(
        self, to: str, text: str, *, idempotency_key: str | None = None
    ) -> NoticeResponse:
        """Tell another member of the pod something: a line in their own
        channel. ``to`` is a pod member id, user id or email address. The same
        ``idempotency_key`` from the same caller is the same notice."""
        return self._call(
            agent_notice_send,
            self._pod_uuid(),
            to,
            body={"text": text},
            body_model=NoticeRequest,
            idempotency_key=_unset(idempotency_key),
        )
