"""Whether a message in a shared place is put to the bot, read from its words.

Slack and Telegram mark a mention for us. Two places do not: an email thread the
pod is only copied on, and a WhatsApp group, whose webhook carries no mention
field at all. What people write there is still unambiguous to a reader -- "Kit,
can you send it?", "@Kit ...", or WhatsApp's own ``@<number>`` when somebody
picks the bot from the participant list -- so it is read from the text.

The name half needs the agent's name, which is known only once the surface is,
so it is asked in ingress; the number half needs only the webhook, so the
WhatsApp parser asks it.
"""

from __future__ import annotations

import re


def names_the_agent(text: str, agent_name: str | None) -> bool:
    """Whether a line of the message speaks to the agent by name.

    "Kit, send them the invoice" or "@Kit ...": the name at the start of a
    line, or after an @. Word-bounded, so an agent called "Lem" is not woken by
    "Lemma".
    """
    name = (agent_name or "").strip()
    if not name or not text:
        return False
    pattern = re.compile(
        rf"(^|\n)[ \t>]*@?{re.escape(name)}\b|@{re.escape(name)}\b",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def mentions_number(text: str, number: str | None) -> bool:
    """Whether the text @-mentions this phone number.

    WhatsApp writes a mention into the message body as ``@`` and the person's
    number. Meta documents no mention field for groups, so the body is all
    there is; digits only, so ``+1 555-0100`` and ``15550100`` are one number.
    """
    digits = "".join(char for char in str(number or "") if char.isdigit())
    if not digits or not text:
        return False
    return re.search(rf"@\+?{digits}(?!\d)", text) is not None
