"""``domain.access.may_see`` as a SQL predicate, for every read of many channels.

Lists, counts, the inbox, mentions and search all filter channels with this,
so a channel the member may not open is never counted, listed or found.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, exists, false, or_, select
from sqlalchemy.orm import aliased

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import ChannelKind, SenderKind, Visibility
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
)


def is_member_of(viewer: Viewer) -> ColumnElement[bool]:
    # An alias, correlated to the channel only: a caller whose own query joins
    # ``channel_members`` (the inbox's totals) would otherwise have it
    # correlated away too, leaving the subquery with nothing to select from.
    member = aliased(ChannelMemberModel)
    return exists(
        select(member.channel_id)
        .where(member.channel_id == ChannelModel.id, member.user_id == viewer.user_id)
        .correlate(ChannelModel)
    )


def visible_to(viewer: Viewer) -> ColumnElement[bool]:
    """The channels of ``viewer``'s pod they may see."""
    channel = ChannelModel
    return and_(
        channel.pod_id == viewer.pod_id,
        or_(
            and_(
                channel.kind == ChannelKind.DIRECT.value,
                channel.member_user_id == viewer.user_id,
            ),
            and_(channel.kind == ChannelKind.CONTACT.value)
            if viewer.sees_contacts
            else false(),
            and_(
                channel.kind == ChannelKind.GROUP.value,
                or_(
                    channel.visibility == Visibility.PUBLIC.value,
                    is_member_of(viewer),
                ),
            ),
        ),
    )


def shown_in_full_to(viewer: Viewer) -> ColumnElement[bool]:
    """``domain.projection.withheld_from``, negated, as SQL: the lines
    ``viewer`` reads in full rather than only as given. Over a query of
    ``channel_messages`` outer-joined to its ``channel_questions`` row; it
    needs no ``channels`` row -- the viewer's own DIRECT channel is looked up
    once (``uq_channels_direct_member``), not joined, so a read using this is
    free to leave the channels out of its join (``line_search``)."""
    message = ChannelMessageModel
    # Never correlated: a caller joining ``channels`` must not have this read
    # its own row instead of the viewer's DIRECT.
    channel = aliased(ChannelModel)
    their_direct = (
        select(channel.id)
        .where(
            channel.pod_id == viewer.pod_id,
            channel.kind == ChannelKind.DIRECT.value,
            channel.member_user_id == viewer.user_id,
        )
        .correlate(None)
        .scalar_subquery()
    )
    return or_(
        message.sender_kind != SenderKind.AGENT.value,
        # An agent acting for a contact used nobody's own access.
        message.on_behalf_contact_id.is_not(None),
        message.on_behalf_user_id == viewer.user_id,
        # Shared with the channel by the member it was made for.
        message.shared_at.is_not(None),
        message.channel_id == their_direct,
        ChannelQuestionModel.awaits_user_id == viewer.user_id,
    )
