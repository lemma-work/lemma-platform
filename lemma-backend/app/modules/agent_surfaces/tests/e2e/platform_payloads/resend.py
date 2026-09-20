"""What an inbound email looks like by the time the parser sees it.

Resend delivers an `email.received` envelope to a catch-all webhook, and the
controller normalizes it before the parser runs — so the shape pinned here is
the normalized one, which is what `ResendInboundParser.parse` consumes. The
builders moved here from `helpers.py` unchanged, including the note about
`authentication-results`: an inbound address names a member only when the
receiving mail service vouched for it, so a payload without that header is
testing a message that does not arrive.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    SurfacePlatform,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads.registry import (
    InboundCase,
    register_inbound,
)

SENDER_EMAIL = "person@example.com"
ASSISTANT_ADDRESS = "assistant@agents.lemma.test"


def authentication_results(sender_email: str) -> str:
    """What SES writes above an inbound message it authenticated.

    Copied from live inbound mail rather than invented, down to the comment
    beside ``spf=`` — that comment is where an attacker's envelope sender is
    echoed, so a payload that omits it cannot exercise the parsing that matters.
    """
    domain = str(sender_email).rpartition("@")[2] or "example.com"
    return (
        f"amazonses.com; spf=pass (spfCheck: domain of {domain} designates "
        f"1.2.3.4 as permitted sender) client-ip=1.2.3.4; "
        f"envelope-from={sender_email}; helo=mail.{domain}; "
        f"dkim=pass header.i=@{domain}; dmarc=pass header.from={domain};"
    )


def message(
    *,
    sender_email: str = SENDER_EMAIL,
    assistant_address: str = ASSISTANT_ADDRESS,
    message_id: str = "resend-001",
    text: str = "Hello from email",
    subject: str = "Surface Resend E2E",
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    signed_by: str | None = None,
) -> dict[str, Any]:
    """The normalized inbound shape the parser consumes.

    ``in_reply_to``/``references`` are what a *reply* carries, and the parser
    derives the thread root from them. Left unset the message threads as brand
    new mail, which is a different conversation — so anything testing a reply
    has to pass them or it silently tests a first contact instead.
    """
    return {
        "from": sender_email,
        "to": assistant_address,
        "subject": subject,
        "text": text,
        "message_id": f"<{message_id}@resend-e2e.test>",
        "in_reply_to": in_reply_to,
        "references": list(references or []),
        "headers": {
            "authentication-results": (
                signed_by
                if signed_by is not None
                else authentication_results(sender_email)
            )
        },
    }


def unsigned(**kwargs: Any) -> dict[str, Any]:
    """Mail the receiving service did not vouch for."""
    return message(signed_by="", **kwargs)


@register_inbound(SurfacePlatform.RESEND)
def inbound_cases() -> list[InboundCase]:
    return [
        InboundCase(
            name="first_contact",
            platform=SurfacePlatform.RESEND,
            payload=message(),
            expected={
                "conversation_type": ConversationType.EXTERNAL_DM,
                "sender_email": SENDER_EMAIL,
                "message_text": "Hello from email",
            },
        ),
        InboundCase(
            name="reply_threads_to_the_original",
            platform=SurfacePlatform.RESEND,
            payload=message(
                message_id="resend-002",
                in_reply_to="<resend-001@resend-e2e.test>",
                references=["<resend-001@resend-e2e.test>"],
                text="and one more thing",
            ),
            expected={
                "external_thread_id": "<resend-001@resend-e2e.test>",
                "sender_email": SENDER_EMAIL,
            },
        ),
        InboundCase(
            name="signed_mail_says_who_vouched",
            platform=SurfacePlatform.RESEND,
            payload=message(),
            expected={"sender_authentication": "PASS"},
        ),
        InboundCase(
            name="unsigned_mail_is_not_a_member",
            platform=SurfacePlatform.RESEND,
            payload=unsigned(),
            # Not None: the parser distinguishes "nobody vouched" from "the
            # question was never asked", and identity resolution refuses to
            # match a member on anything but PASS.
            expected={"sender_authentication": "UNKNOWN"},
        ),
    ]
