"""Who an agent's email says it is from.

The *address* already identifies the agent — ``email_address_allocation`` mints
``priya.acme@`` per agent, and inbound routes back on it. The display name is
the other half: an inbox list shows it and never the body, so it has to answer
"who is writing" in the twenty-odd characters a sender column gives it.

It answered three. ``Lem (Ada Member) via Lemma`` named the pod's assistant,
the person the run was acting for, and the product — and the one part a reader
needs, which pod or which agent this is, sat in the middle of two things that
were already somewhere else. The product is on the sending domain. The person is
in the body, under ``On behalf of`` (see ``attribute()``), where a full name
fits and where the distinction it draws actually belongs. What is left is one
name:

1. the **pod** — what the pod's own assistant is called in the app, and the
   name a member recognises it by
2. the **agent's own name**, when a named agent is writing

Both are resolved by the caller, which is the only place that knows whether the
sender answers as the pod (``group_names``); this module composes the header.

Length is handled by cutting the one name rather than by dropping parts, which
is all that is left to degrade. An agent name is 255 characters of free text as
far as the API is concerned, and no client renders that far.

Pure string composition on purpose: the caller owns the RFC 5322 quoting (see
``formataddr`` in the Resend service), so nothing here has to know that a comma
in an agent name would otherwise split one address into two.
"""

from __future__ import annotations

# Past this the display name is doing nothing a reader will ever see: no client
# renders that far. Well under the RFC 5322 line limit, so composing one never
# forces the header to fold.
MAX_DISPLAY_NAME = 64


def _clean(value: str | None) -> str:
    """Collapse whitespace — including the newlines a header must never carry."""
    return " ".join(str(value or "").split())


def sender_display_name(*, sender_name: str | None, product_name: str) -> str:
    """``Sales`` or ``Priya`` — the one name this message is from.

    ``product_name`` is the deployment's own ``RESEND_FROM_NAME``, so a
    self-hosted instance brands itself without a second setting, and a send that
    knows no sender returns exactly what it returned before any of this existed.

    No guard for a name that is shaped like an address. There was one, and its
    reason left with the actor: what it caught was an *unrelated* address, the
    one ``get_user_display_name`` falls back to when a person has set no name,
    landing in a header that is not theirs. This one is the sender's own name,
    and an agent called ``billing@`` is named billing.
    """
    product = _clean(product_name) or "Lemma"
    name = _clean(sender_name)
    if not name:
        return product
    return name[:MAX_DISPLAY_NAME].rstrip()


__all__ = ["MAX_DISPLAY_NAME", "sender_display_name"]
