"""Who besides the asker reads a member's answer, when some of them are outside the pod.

A member who asks the bot something in a group gets an answer made with their
own access -- that is what being a member means, and the pod chose it. But the
answer is posted where everybody in the group reads it, and some of them may be
nobody the pod knows: a client in a WhatsApp group, the other company in a
Slack Connect channel, a vendor on the Cc line of an email. The run is told so,
plainly, and by name where the names are known, so it can answer the way the
member would in front of them.

What does not depend on the model behaving is done elsewhere: the strangers'
own lines are taken out of a member run's background (``group_log``), so
nothing a stranger wrote reaches a run acting with a member's access except the
message it is answering, when that message points at one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import SurfaceGroup

logger = get_logger(__name__)

#: At most this many outside names are put in front of the model, each cut to
#: this length: a display name is whatever its owner typed, and it lands in a
#: run that acts with a member's access.
_MAX_NAMES = 5
_MAX_NAME_CHARS = 60

#: Whether an email address belongs to somebody in the pod.
IsMemberAddress = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Audience:
    """People outside the pod who will read the answer, as far as is known."""

    #: Where the answer goes: a group's title, "this email thread".
    where: str | None
    #: Their names or addresses; empty when they are known to be there but not
    #: who they are (a group open to outsiders nobody has spoken in yet).
    outsiders: tuple[str, ...] = ()
    #: For email: everybody the reply goes to, outside the pod or not.
    recipients: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, object]:
        return {
            "where": self.where,
            "outsiders": list(self.outsiders),
            "recipients": list(self.recipients),
        }


def chat_audience(
    *,
    platform: SurfacePlatform,
    group: SurfaceGroup | None,
    parsed: ParsedInboundSurfaceEvent,
    outside_authors: Iterable[str],
) -> Audience | None:
    """The outside audience of a member's answer in a group chat, or None.

    Somebody is outside when the group's own log or the platform's history
    shows a line from them, when the group answers outsiders (so they may be
    there now), or when Slack says the channel is shared with another company.
    """
    if parsed.is_dm:
        return None
    names = _distinct(outside_authors)
    shared = bool(
        (group is not None and group.shared_externally)
        or parsed.metadata.get("is_ext_shared_channel")
    )
    welcomes = group is not None and group.welcomes_outsiders
    if not names and not shared and not welcomes:
        return None
    logger.info(
        "agent_surfaces.group_audience.outsiders_present.observed",
        platform=platform.value,
        count=len(names),
    )
    return Audience(
        where=(group.title if group is not None else None)
        or parsed.metadata.get("chat_title")
        or parsed.metadata.get("channel_name"),
        outsiders=names,
    )


async def email_audience(
    *, recipients: Sequence[str], is_member: IsMemberAddress
) -> Audience | None:
    """Who a reply on an email thread goes to, when some are outside the pod.

    ``recipients`` is everybody the reply is addressed or copied to besides the
    pod's own mailbox and the member who wrote.
    """
    everyone = _distinct(recipients, limit=None)
    if not everyone:
        return None
    outsiders = [address for address in everyone if not await is_member(address)]
    if not outsiders:
        return None
    logger.info(
        "agent_surfaces.group_audience.outsiders_present.observed",
        platform=SurfacePlatform.RESEND.value,
        count=len(outsiders),
    )
    return Audience(
        where="this email thread",
        outsiders=tuple(outsiders[:_MAX_NAMES]),
        recipients=everyone,
    )


def outside_names(lines: Iterable[dict[str, object]]) -> list[str]:
    """The authors of the background lines marked as outside the pod."""
    return [
        str(line.get("author") or "")
        for line in lines
        if isinstance(line, dict) and line.get("outside_pod")
    ]


def _distinct(
    values: Iterable[str], *, limit: int | None = _MAX_NAMES
) -> tuple[str, ...]:
    seen: list[str] = []
    lowered: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split())[:_MAX_NAME_CHARS]
        if cleaned and cleaned.lower() not in lowered:
            lowered.add(cleaned.lower())
            seen.append(cleaned)
    return tuple(seen if limit is None else seen[:limit])
