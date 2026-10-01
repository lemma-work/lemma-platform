"""Who else is on an email thread, and whether the pod was spoken to or copied.

An email with other people on it is a group conversation the way a Telegram
group is, and two things follow from that.

**Reply to all.** Answering the sender alone drops everybody else off the
thread -- the client who asked for the invoice gets it, the colleague who
copied the pod does not see that it went. The pod replies the way a person
replying to all would: to the sender, copying the others.

**Answer when addressed.** Copied is not asked. A pod's address in Cc means
"so you know", and answering every "thanks!" on the thread is the bot talking
over people. Addressed means in To, reached without being visibly copied (a
forward, an alias, a Bcc), or spoken to by name ("Kit, can you..."), which is
how people speak to someone they copied. The name is
only known once the surface is, so that half is decided in ingress
(``domain.addressing.names_the_agent``); the parser only records whether the
pod was in To.

Capped, because a reply is also a send: a thread copied to a mailing list is not
a conversation the pod should write back to wholesale.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.modules.agent_surfaces.platforms.resend.inbound import all_addresses

#: Most people a reply will copy. Past this it is a broadcast, not a thread.
MAX_REPLY_CC = 10

#: Everything a message may say and still say nothing but thanks.
_ACKNOWLEDGING = frozenset(
    {
        "thanks",
        "thank",
        "you",
        "ty",
        "thx",
        "much",
        "so",
        "a",
        "lot",
        "all",
        "everyone",
        "got",
        "it",
        "great",
        "perfect",
        "ok",
        "okay",
        "cheers",
        "noted",
        "awesome",
        "received",
        "appreciated",
        "sounds",
        "good",
        "many",
    }
)
_SIGNATURE = re.compile(r"^--\s*$", re.MULTILINE)


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


def reply_all_recipients(event: object) -> list[str]:
    """Everybody a reply on this email reaches besides the sender and the pod.

    What ``other_people`` recorded on the event's ``reply_target`` when it was
    parsed: the reply goes to the sender and is copied to these.
    """
    reply_target = getattr(event, "reply_target", None) or {}
    copied = reply_target.get("cc") if isinstance(reply_target, dict) else None
    return [str(address) for address in copied or [] if str(address).strip()]


def pod_was_addressed(
    *, addressed_to: Iterable[object], cc: Iterable[object], own_address: str
) -> bool:
    """Whether the pod was asked: anything but visibly copied.

    Only the pod's own address in Cc means "so you know". Mail that names the
    pod in neither To nor Cc reached it some other way -- forwarded from
    ``support@``, through an alias, Bcc'd -- and somebody sent it there on
    purpose, so it reads as addressed. So does a payload that lists nobody (a
    replay, an older poll), which predates the distinction.
    """
    own = own_address.strip().lower()
    if own in {a.strip().lower() for a in all_addresses(list(addressed_to))}:
        return True
    return own not in {a.strip().lower() for a in all_addresses(list(cc))}


def only_acknowledges(text: str | None) -> bool:
    """Whether a message says nothing but thanks: "Thanks!", "Got it, cheers".

    Replying to that copies everybody on the thread with "you're welcome", so a
    thread with other people on it does not start a run for it. Its words and
    only those, above any signature: anything else -- "thanks, and can you
    resend...", a name -- may be asking something.
    """
    body = _SIGNATURE.split(text or "", maxsplit=1)[0]
    words = re.findall(r"[a-z']+", body.lower())
    return 0 < len(words) <= 8 and all(word in _ACKNOWLEDGING for word in words)
