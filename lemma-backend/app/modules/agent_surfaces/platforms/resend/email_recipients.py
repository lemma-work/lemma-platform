"""Who else is on an email thread, and whether the pod was spoken to or copied.

An email with other people on it is a group conversation the way a Telegram
group is, and two things follow from that.

**Reply to all.** Answering the sender alone drops everybody else off the
thread -- the client who asked for the invoice gets it, the colleague who
copied the pod does not see that it went. The pod replies the way a person
replying to all would: to the sender, copying the others.

**Answer when addressed.** Copied is not asked. A pod's address in Cc means
"so you know", and answering every "thanks!" on the thread is the bot talking
over people. Addressed means in To, or named at the start of a line ("Kit,
can you..."), which is how people speak to someone they copied. The name is
only known once the surface is, so that half is decided in ingress
(``domain.addressing.names_the_agent``); the parser only records whether the
pod was in To.

Capped, because a reply is also a send: a thread copied to a mailing list is not
a conversation the pod should write back to wholesale.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.agent_surfaces.platforms.resend.inbound import all_addresses

#: Most people a reply will copy. Past this it is a broadcast, not a thread.
MAX_REPLY_CC = 10


def other_people(
    *,
    addressed_to: Iterable[object],
    cc: Iterable[object],
    sender: str,
    own_address: str,
) -> list[str]:
    """Everybody on the thread except the sender and the pod itself, in order."""
    skip = {sender.strip().lower(), own_address.strip().lower()}
    seen: set[str] = set()
    people: list[str] = []
    for address in [*all_addresses(list(addressed_to)), *all_addresses(list(cc))]:
        key = address.strip().lower()
        if not key or key in skip or key in seen:
            continue
        seen.add(key)
        people.append(address.strip())
    return people[:MAX_REPLY_CC]


def pod_was_addressed(*, addressed_to: Iterable[object], own_address: str) -> bool:
    """Whether the pod was in To -- or nobody said, which reads as addressed.

    A payload with no To list at all (a replay, an older poll) predates this
    distinction, and treating it as "only copied" would silence mail that has
    always been answered.
    """
    addressed = [a.strip().lower() for a in all_addresses(list(addressed_to))]
    return not addressed or own_address.strip().lower() in addressed
