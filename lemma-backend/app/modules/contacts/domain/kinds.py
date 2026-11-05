"""What sort of handle names a contact, and who vouched for it."""

from __future__ import annotations

from enum import StrEnum


class IdentityKind(StrEnum):
    """What sort of handle names a contact."""

    PHONE = "PHONE"
    EMAIL = "EMAIL"
    TELEGRAM = "TELEGRAM"
    #: A signed-in user of the customer's own product, as named in a token
    #: their server signed: ``host:{issuer}:{their user id}``. The issuer is
    #: the product the widget declares it speaks for, so two products' user 42
    #: are two people, while one product's user reached through another of
    #: the pod's widgets -- or a widget made again -- is still one contact.
    HOST = "HOST"
    #: Somebody from another company in a Slack channel shared with it:
    #: ``{their team id}:{their user id}``, as Slack names them in a signed event.
    SLACK = "SLACK"
    #: Somebody from another tenant in a Teams chat: ``{tenant id}:{aad id}``.
    TEAMS = "TEAMS"
    #: A browser, by the web chat session it holds: the session's id. Proves
    #: only that the same browser came back -- see ``IdentityStrength.SESSION``.
    WEB = "WEB"


class IdentityStrength(StrEnum):
    """Who vouched for a handle.

    ``CHANNEL``: the platform the message came through, in a payload whose
    signature was checked (WhatsApp, Telegram), or the receiving mail service's
    authentication verdict (email). ``HOST`` and ``CODE``: see their values.
    ``MEMBER``: a pod member added it by hand,
    which says who the member believes it is and nothing about who writes from
    it -- so it never makes a message count as that contact on its own.
    """

    CHANNEL = "CHANNEL"
    MEMBER = "MEMBER"
    #: The customer's server, in a token signed with a web widget's secret.
    HOST = "HOST"
    #: The person entered a one-time code sent to the handle.
    CODE = "CODE"
    #: A browser holds the web chat session's secret. Says the same browser came
    #: back and nothing about who uses it, so it never proves another handle.
    SESSION = "SESSION"


#: The strengths that prove a person holds a handle, and so may join it to the
#: contact they already are -- or merge two contacts into one. A member typing
#: a handle in, or a browser holding a session, proves nothing of the kind.
PROVING_STRENGTHS = frozenset(
    {IdentityStrength.CHANNEL, IdentityStrength.HOST, IdentityStrength.CODE}
)
