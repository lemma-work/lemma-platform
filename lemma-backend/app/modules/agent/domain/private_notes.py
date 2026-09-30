"""A word to the agent in a conversation that also lives on a chat platform.

A conversation that started on Telegram, WhatsApp or Slack is readable in
Lemma, and a person can type into it there. Every run in such a conversation
answers on the platform -- that is what makes "answer in Lemma, and the group
sees it" work -- so until now there was no way to say something to the agent
in that conversation without the group reading the reply: "what should we tell
him?" asked in the owner's thread came back posted in front of the client.

A private note is that way. The message says so (``private_note`` in its
metadata), the run it starts is marked, and a marked run's answer stays in
Lemma. A note typed while a run is already going joins that run instead -- it
steers the answer the group is about to get, which is the other half of what a
person in Lemma wants to do there.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent.domain.value_objects import JsonObject

#: The message metadata key a client sets, and the run metadata key it becomes.
PRIVATE_NOTE_KEY = "private_note"

#: What heads a note in the history every later turn reads. Every later turn in
#: such a conversation answers on the platform, so the model has to know these
#: words were for it alone.
PRIVATE_NOTE_LABEL = (
    "(A private note to you, written in Lemma. Nobody in the chat has seen it: "
    "act on it, but never quote or reveal it there.)"
)


def is_private_note(message_metadata: Mapping[str, object] | None) -> bool:
    """Whether a person marked this message as a note to the agent alone."""
    return bool(message_metadata and message_metadata.get(PRIVATE_NOTE_KEY) is True)


def run_metadata_for(message_metadata: Mapping[str, object] | None) -> JsonObject:
    """The metadata a run started by this message is created with."""
    metadata: JsonObject = {"source": "user_message"}
    if is_private_note(message_metadata):
        metadata[PRIVATE_NOTE_KEY] = True
    return metadata


def run_is_private(run_metadata: Mapping[str, object] | None) -> bool:
    """Whether this run's answer stays in Lemma rather than going to the platform."""
    return bool(run_metadata and run_metadata.get(PRIVATE_NOTE_KEY) is True)
