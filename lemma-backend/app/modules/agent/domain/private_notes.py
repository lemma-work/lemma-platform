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

A message typed in Lemma into a *group's* conversation needs saying even when it
is not a note. Every line that came in from the chat is labelled with who wrote
it; one typed in Lemma carries no label, and the model reads it as the next line
of the chat. In the conversation that answers people outside the pod that is
worse than a muddle: the brief says everyone writing is a stranger, so "ask him
what's up" from the member who looks after the group was read as the stranger's,
and passed on with ``message_user`` -- to that same member. So the server stamps
where such a message was written (``written_in_lemma``), the history labels it,
and the run it starts is marked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from app.modules.agent.domain.outsiders import AUDIENCE_KEY, OUTSIDERS
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

#: The same, for a note in a person's own direct chat with the bot. The only
#: other reader there is the person who wrote it, so there is nothing to keep
#: from anybody -- only the fact that it was not sent.
PRIVATE_NOTE_IN_DM_LABEL = (
    "(A note to you, written in Lemma by the person in this chat. It was not "
    "sent to the chat.)"
)

#: Stamped on a note written in a direct chat, read when its label is chosen.
NOTE_IN_DM_KEY = "private_note_in_dm"

#: Stamped by the server -- never taken from a client -- on a message typed in
#: Lemma into a group's conversation, saying which kind: a member's own slice of
#: the group, or the thread where the bot answers people outside the pod. Copied
#: onto the run the message starts.
WRITTEN_IN_LEMMA_KEY = "written_in_lemma"

WrittenIn = Literal["group", "outsiders"]
IN_GROUP: WrittenIn = "group"
IN_OUTSIDERS_THREAD: WrittenIn = "outsiders"

#: A note in the strangers' thread. Its writer has to be named: the brief says
#: whoever writes in this conversation is outside the pod.
PRIVATE_NOTE_FROM_KEEPER_LABEL = (
    "(A private note to you, written in Lemma by the member who looks after "
    "this conversation -- not by anybody in the chat. Nobody in the chat has "
    "seen it: act on it, but never quote or reveal it there.)"
)

#: A reply typed in Lemma into the strangers' thread: the member's words, not
#: the stranger's, and the one reading the answer is in the group anyway.
WRITTEN_BY_KEEPER_LABEL = (
    "(Written in Lemma by the member who looks after this conversation -- not "
    "by anybody outside the pod. Nobody in the chat saw this message; only your "
    "answer is posted there, so write it to make sense on its own to the people "
    "in the chat, and do not quote this message. Do not pass it on with "
    "`message_user`: that only reaches the member who wrote it.)"
)

#: A reply typed in Lemma into a member's own slice of a group.
WRITTEN_IN_LEMMA_LABEL = (
    "(Written in Lemma, not in the chat. Nobody in the chat saw this message; "
    "only your answer is posted there, so write it to make sense on its own to "
    "the people in the chat.)"
)


def is_private_note(message_metadata: Mapping[str, object] | None) -> bool:
    """Whether a person marked this message as a note to the agent alone."""
    return bool(message_metadata and message_metadata.get(PRIVATE_NOTE_KEY) is True)


def written_in_lemma_into(
    conversation_metadata: Mapping[str, object] | None,
) -> WrittenIn | None:
    """What a message typed in Lemma into this conversation is to be marked as.

    None for anything but a group's conversation: in a direct chat the person
    typing in Lemma is the person the bot is talking to, and a plain Lemma
    conversation has no chat to have missed the message.
    """
    metadata = conversation_metadata or {}
    if metadata.get(AUDIENCE_KEY) == OUTSIDERS:
        return IN_OUTSIDERS_THREAD
    if metadata.get("surface_platform") and metadata.get("conversation_kind") == (
        "CHANNEL"
    ):
        return IN_GROUP
    return None


def _written_in(metadata: Mapping[str, object] | None) -> WrittenIn | None:
    value = (metadata or {}).get(WRITTEN_IN_LEMMA_KEY)
    if value == IN_GROUP:
        return IN_GROUP
    if value == IN_OUTSIDERS_THREAD:
        return IN_OUTSIDERS_THREAD
    return None


def run_metadata_for(message_metadata: Mapping[str, object] | None) -> JsonObject:
    """The metadata a run started by this message is created with."""
    metadata: JsonObject = {"source": "user_message"}
    if is_private_note(message_metadata):
        metadata[PRIVATE_NOTE_KEY] = True
    if written_in := _written_in(message_metadata):
        metadata[WRITTEN_IN_LEMMA_KEY] = written_in
    return metadata


def run_is_private(run_metadata: Mapping[str, object] | None) -> bool:
    """Whether this run's answer stays in Lemma rather than going to the platform."""
    return bool(run_metadata and run_metadata.get(PRIVATE_NOTE_KEY) is True)


def answers_lemma_message(run_metadata: Mapping[str, object] | None) -> bool:
    """Whether this run was started by a message typed in Lemma into a group.

    The group never saw that message, so whatever the run posts there answers
    nothing anybody in the chat said.
    """
    return _written_in(run_metadata) is not None


def keeper_started(run_metadata: Mapping[str, object] | None) -> bool:
    """Whether the member who looks after a strangers' thread started this run.

    Typed in Lemma, by them: nobody outside the pod is waiting on it, so there
    is nothing to pass on to the member -- they are the one asking.
    """
    return _written_in(run_metadata) == IN_OUTSIDERS_THREAD


def privacy_of(run_metadata: Mapping[str, object] | None) -> JsonObject:
    """The marks a run carries on to the run that continues it.

    A resume, a retry, a follow-up: each continues answering what the earlier
    run answered, so each answers where it would have -- a note's in Lemma --
    and for whom it would have.
    """
    marks: JsonObject = {}
    if run_is_private(run_metadata):
        marks[PRIVATE_NOTE_KEY] = True
    if written_in := _written_in(run_metadata):
        marks[WRITTEN_IN_LEMMA_KEY] = written_in
    return marks


def lemma_label(message_metadata: Mapping[str, object]) -> str | None:
    """What heads a message typed in Lemma in the history every later turn reads.

    None for a message that came in from the chat, which its sender label
    already describes, and for one typed in Lemma where nobody else reads.
    """
    written_in = _written_in(message_metadata)
    if is_private_note(message_metadata):
        if message_metadata.get(NOTE_IN_DM_KEY) is True:
            return PRIVATE_NOTE_IN_DM_LABEL
        if written_in == IN_OUTSIDERS_THREAD:
            return PRIVATE_NOTE_FROM_KEEPER_LABEL
        return PRIVATE_NOTE_LABEL
    if written_in == IN_OUTSIDERS_THREAD:
        return WRITTEN_BY_KEEPER_LABEL
    if written_in == IN_GROUP:
        return WRITTEN_IN_LEMMA_LABEL
    return None
