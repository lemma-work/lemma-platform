"""A heard message, as the text and parts of its channel line.

``domain/markup`` reads what a platform's markup says -- somebody named by
their platform id, a link -- and this says who those ids are in the pod: a
member the surface has resolved, a contact the pod knows by that handle, or
the bot, which is the agent it answers for. The line reads the way the
message did in the chat, with each mention written ``@Name``, and carries
them as parts so Lemma can show them as people rather than as text.

Who a mention *tells* is the channel's rule (``channels.contracts.named_in``):
only somebody who may see the channel. A Slack message naming a colleague who
cannot open it keeps their name, and gives them no unread mention.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.markup import Link, Markup, Named, markup_of
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (  # noqa: E501
    SurfaceRepository,
)
from app.modules.channels.contracts import (
    MAX_PARTS,
    LinkPart,
    MentionPart,
    Part,
    Principal,
    agent_names,
    named_in,
    names_of,
)
from app.modules.contacts.contracts import (
    IdentityKind,
    find_contact,
    find_contact_by_user,
    normalize_handle,
)

#: The longest label a link part keeps.
_MAX_LABEL = 255
#: The longest address a link part keeps; a longer one stays as text.
_MAX_URL = 2048


@dataclass(frozen=True, slots=True)
class Bot:
    """The bot the message was said to: the surface it is, and what it is
    called; the agent it answers for is the surface's."""

    surface_id: UUID | None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LineText:
    """What a heard message is on the timeline."""

    text: str
    parts: tuple[Part, ...] = ()
    #: The people its mentions tell: those who may see the channel.
    mentions: tuple[Principal, ...] = ()
    #: The pod's agents it names.
    agents: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class _Who:
    name: str | None
    part: MentionPart | None = None


class LineMarkup:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.external_users = ExternalSurfaceUserRepository(uow)
        self.membership = SqlAlchemySurfaceRoutingResolutionAdapter(uow)

    async def read(
        self,
        parsed: ParsedInboundSurfaceEvent,
        *,
        pod_id: UUID,
        channel_id: UUID,
        text: str | None = None,
        bot: Bot | None = None,
    ) -> LineText:
        """The line ``parsed`` -- or ``text``, what was made of it -- is in
        this channel."""
        markup = markup_of(parsed, text=text)
        if markup.is_plain:
            return LineText(text=_rendered_plain(markup, parsed, text))
        who = await self._who(markup.named, parsed, pod_id=pod_id, bot=bot)
        rendered, parts = _render(markup, who)
        mentions, agents = await named_in(self.uow, channel_id=channel_id, parts=parts)
        return LineText(
            text=rendered,
            parts=tuple(parts),
            mentions=tuple(mentions),
            agents=tuple(agents),
        )

    async def _who(
        self,
        named: Sequence[Named],
        parsed: ParsedInboundSurfaceEvent,
        *,
        pod_id: UUID,
        bot: Bot | None,
    ) -> dict[str, _Who]:
        who = await self._people(
            {person.external_id: person.label for person in named if not person.is_bot},
            parsed,
            pod_id=pod_id,
        )
        bots = {person.external_id: person.label for person in named if person.is_bot}
        if bots:
            who |= await self._bots(bots, bot, pod_id=pod_id)
        return who

    async def _people(
        self,
        labels: Mapping[str, str | None],
        parsed: ParsedInboundSurfaceEvent,
        *,
        pod_id: UUID,
    ) -> dict[str, _Who]:
        """Each person named: a member the surface resolved, else a contact
        the pod knows by that handle, else just their name."""
        known = await self.external_users.known_by_external_ids(
            platform=parsed.platform.value,
            tenant_id=parsed.tenant_id,
            external_user_ids=sorted(labels),
        )
        resolved = {
            external: user for external, (user, _) in known.items() if user is not None
        }
        members = await self.membership.pod_members_among(pod_id, resolved.values())
        member_names = await names_of(
            self.uow,
            pod_id=pod_id,
            people=[Principal.member(user) for user in members],
        )
        who: dict[str, _Who] = {}
        for external in sorted(labels):
            shown = labels[external] or (known.get(external) or (None, None))[1]
            user_id = resolved.get(external)
            if user_id is not None and user_id in members:
                who[external] = _mention(
                    "MEMBER",
                    user_id,
                    shown or member_names.get(Principal.member(user_id)),
                )
                continue
            contact = await self._contact(parsed.platform, external, pod_id=pod_id)
            if contact is not None:
                contact_id, contact_name = contact
                who[external] = _mention("CONTACT", contact_id, shown or contact_name)
                continue
            who[external] = _Who(name=shown)
        return who

    async def _bots(
        self, labels: Mapping[str, str | None], bot: Bot | None, *, pod_id: UUID
    ) -> dict[str, _Who]:
        """The bot, named: the agent it answers for."""
        agent_id, agent_name = await self._agent_of(bot, pod_id=pod_id)
        who: dict[str, _Who] = {}
        for external, label in labels.items():
            name = (bot.name if bot else None) or agent_name or label
            who[external] = (
                _mention("AGENT", agent_id, name)
                if agent_id is not None
                else _Who(name=name)
            )
        return who

    async def _agent_of(
        self, bot: Bot | None, *, pod_id: UUID
    ) -> tuple[UUID | None, str | None]:
        """The agent the bot answers for, and its name."""
        if bot is None or bot.surface_id is None:
            return None, None
        surface = await SurfaceRepository(self.uow).get(bot.surface_id)
        if surface is None or surface.agent_id is None:
            return None, None
        names = await agent_names(self.uow, pod_id=pod_id, agent_ids=[surface.agent_id])
        return surface.agent_id, names.get(surface.agent_id)

    async def _contact(
        self, platform: SurfacePlatform, external_id: str, *, pod_id: UUID
    ) -> tuple[UUID, str | None] | None:
        """The pod's contact this platform id is a handle of: a Telegram user
        id, a WhatsApp number -- or, for a Slack user from the other company in
        a shared channel, the contact whose ``{team}:{user}`` handle is theirs.
        A mention names the user, never their workspace; a Slack user id is one
        person across workspaces. A Teams id alone names nobody here."""
        if platform is SurfacePlatform.SLACK:
            found = await find_contact_by_user(
                self.uow, pod_id=pod_id, kind=IdentityKind.SLACK, user_id=external_id
            )
            return (found.id, found.display_name) if found is not None else None
        handle = _handle(platform, external_id)
        if handle is None:
            return None
        kind, raw = handle
        value = normalize_handle(kind, raw)
        if not value:
            return None
        contact = await find_contact(self.uow, pod_id=pod_id, kind=kind, value=value)
        return (contact.id, contact.display_name) if contact is not None else None


def _mention(
    kind: Literal["MEMBER", "CONTACT", "AGENT"], principal_id: UUID, name: str | None
) -> _Who:
    return _Who(
        name=name,
        part=MentionPart(type="mention", kind=kind, id=principal_id, name=_short(name)),
    )


def _handle(
    platform: SurfacePlatform, external_id: str
) -> tuple[IdentityKind, str] | None:
    if platform is SurfacePlatform.TELEGRAM:
        return IdentityKind.TELEGRAM, external_id
    if platform is SurfacePlatform.WHATSAPP:
        return IdentityKind.PHONE, f"+{external_id.lstrip('+')}"
    return None


def _rendered_plain(
    markup: Markup, parsed: ParsedInboundSurfaceEvent, text: str | None
) -> str:
    """Plain text as markup reads it (Slack's ``&amp;`` unescaped), or the
    text as it came where nothing was read."""
    rendered = "".join(
        segment for segment in markup.segments if isinstance(segment, str)
    )
    return rendered or (parsed.message_text if text is None else text)


def _render(markup: Markup, who: Mapping[str, _Who]) -> tuple[str, list[Part]]:
    words: list[str] = []
    parts: list[Part] = []
    seen: set[tuple[str, str]] = set()

    def keep(part: Part, key: tuple[str, str]) -> None:
        if key in seen or len(parts) >= MAX_PARTS:
            return
        seen.add(key)
        parts.append(part)

    for segment in markup.segments:
        if isinstance(segment, str):
            words.append(segment)
        elif isinstance(segment, Named):
            person = who.get(segment.external_id) or _Who(name=segment.label)
            words.append(f"@{person.name or segment.label or segment.external_id}")
            if person.part is not None:
                keep(person.part, (person.part.kind, str(person.part.id)))
        elif isinstance(segment, Link):
            words.append(segment.label or segment.url)
            if len(segment.url) <= _MAX_URL:
                keep(
                    LinkPart(type="link", url=segment.url, label=_short(segment.label)),
                    ("link", segment.url),
                )
    return "".join(words), parts


def _short(name: str | None) -> str | None:
    return name[:_MAX_LABEL] if name else None
