"""A person configuring the app from inside the chat, as one typed request.

A platform that drives set-up in-chat (Slack: the home tab, the "answer here"
button, its modal) parses its payloads into this. It used to be a mapping whose
keys the handler read with `.get` and a string `kind` it compared against
literals, so a renamed key read as "nothing to do" rather than as an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChannelSetupKind(StrEnum):
    #: A starter prompt tapped on the home tab: send it as the person's message.
    STARTER_PROMPT = "starter_prompt"
    #: Which of several pods' surfaces answers this person here.
    SELECT_SURFACE = "select_surface"
    #: Open the "answer in this channel?" modal.
    OPEN = "open"
    #: The modal was submitted: allow the channel.
    SUBMIT = "submit"


@dataclass(frozen=True, slots=True)
class ChannelSetupRequest:
    kind: ChannelSetupKind
    tenant_id: str | None
    actor_external_user_id: str | None
    channel_id: str = ""
    #: The surface the request names, where it names one (a button that knows
    #: which pod's bot drew it).
    surface_id: str | None = None
    #: The platform's handle for opening a modal in reply; short-lived.
    trigger_id: str = ""
    prompt: str = ""
