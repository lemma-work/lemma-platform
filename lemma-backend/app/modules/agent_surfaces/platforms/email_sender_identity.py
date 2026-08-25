"""Who an agent's email says it is from.

The *address* already identifies the agent — ``email_address_allocation`` mints
``priya.acme@`` per agent, and inbound routes back on it. The display name did
not: it was one deployment-wide setting, so every agent in every pod arrived as
"Lemma" and the only trace of who was asking sat in the ``attribute()`` header
inside the body. An inbox list shows the display name and never the body, which
is exactly where that attribution was worth having.

Three things go in it, and the order is the design rather than a preference. The
sender column truncates at roughly 18 characters on a phone and 25-30 in a
desktop client, so whatever comes third is decorative:

1. the **agent** — the unique part, and the "who is talking to me"
2. the **person** it acts for — the "why is this in my inbox"
3. the **product** — identical on every message we ever send, and already
   carried by the sending domain, so it is the part that can afford to be cut

Length is handled by dropping whole parts rather than by cutting characters. An
agent name is 255 characters of free text as far as the API is concerned, and
two long names would push the human out of the visible window while leaving a
header that reads as complete. Degrading to a first name, then to no actor at
all, keeps every form that is shown a form that is true.

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


def _is_real_name(value: str) -> bool:
    """Whether this is a name, or the email address stood in for one.

    ``get_user_display_name`` falls back to the user's email when first and last
    are both empty, and does so deliberately: the body header promises "on
    behalf of <someone>" and must always have a someone. That trade is right for
    prose and wrong here — ``Priya (dj@gmail.com) via Lemma`` puts an unrelated
    address inside a From display name, which is the shape both spam filters and
    people read as a forgery.
    """
    return bool(value) and "@" not in value


def sender_display_name(
    *,
    agent_name: str | None,
    actor_display_name: str | None = None,
    product_name: str,
) -> str:
    """``Priya (Deepak Jha) via Lemma`` — as much of it as fits.

    ``product_name`` is the deployment's own ``RESEND_FROM_NAME``, so a
    self-hosted instance brands the third slot without a second setting, and a
    send that knows neither an agent nor an actor returns exactly what it
    returned before this existed.
    """
    product = _clean(product_name) or "Lemma"
    agent = _clean(agent_name)
    actor = _clean(actor_display_name)

    if not _is_real_name(actor):
        actor = ""
    # An agent-less surface reports its display name as the product already (see
    # ``_egress_metadata_with_agent_name``). Letting that through would compose
    # "Lemma (Deepak Jha) via Lemma".
    if agent.casefold() == product.casefold():
        agent = ""

    # Best first. Each fallback drops a whole part rather than trimming one, so
    # every candidate is a name that is true as written.
    candidates: list[str] = []
    if agent and actor:
        candidates.append(f"{agent} ({actor}) via {product}")
        first_name = actor.split(" ")[0]
        if first_name != actor:
            candidates.append(f"{agent} ({first_name}) via {product}")
    if agent:
        candidates.append(f"{agent} via {product}")
    elif actor:
        candidates.append(f"{actor} via {product}")

    for candidate in candidates:
        if len(candidate) <= MAX_DISPLAY_NAME:
            return candidate

    # Only reachable from an agent name long enough that no form of it fits.
    # Cut the name rather than falling back to the bare product: a clipped
    # "support-triage-escalation-…" still tells the reader which agent this is,
    # and "Lemma" tells them nothing they could not read off the domain.
    if agent:
        room = MAX_DISPLAY_NAME - len(f" via {product}")
        if room > 0:
            return f"{agent[:room].rstrip()} via {product}"
    return product


__all__ = ["MAX_DISPLAY_NAME", "sender_display_name"]
