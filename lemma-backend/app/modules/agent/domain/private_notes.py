"""A word to the agent in a conversation that also lives on a chat platform.

A conversation that started on Telegram, WhatsApp or Slack is readable in
Lemma, and a person can type into it there. Every run in such a conversation
answers on the platform -- that is what makes "answer in Lemma, and the group
sees it" work -- so until now there was no way to say something to the agent
in that conversation without the group reading the reply: "what should we tell
him?" asked in the owner's thread came back posted in front of the client.

A private note is that way. The person's client says so (``private_note`` in
the metadata it sends), the message and the run it starts are marked
(``private_note`` columns), and a marked run's answer stays in Lemma. A note typed while a run is already going joins that run instead -- it
steers the answer the group is about to get, which is the other half of what a
person in Lemma wants to do there.

A message typed in Lemma into a *group's* conversation needs saying even when it
is not a note. Every line that came in from the chat is labelled with who wrote
it; one typed in Lemma carries no label, and the model reads it as the next line
of the chat. In the conversation that answers people outside the pod that is
worse than a muddle: the brief says everyone writing is a stranger, so "ask him
what's up" from the member who looks after the group was read as the stranger's,
and passed on with ``channel_post`` -- to that same member. So the server stamps
where such a message was written (``written_in``), the history labels it, and
the run it starts is marked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.run_state import RunPrivacy, RunState
from app.modules.agent.domain.value_objects import RunTrigger, WrittenIn

#: The key a client sets in a send's metadata to make the message a note. Read
#: off the request once, into the message's own ``private_note``; never stored
#: as a key.
PRIVATE_NOTE_REQUEST_KEY = "private_note"

#: What heads a note in the history every later turn reads. Every later turn in
#: such a conversation answers on the platform, so the model has to know these
#: words were for it alone.
PRIVATE_NOTE_LABEL = (
    "(A private note to you, written in Lemma. Nobody in the chat has seen it: "
    "act on it, but never quote or reveal it there.)"
)

#: The same, for a note in a member's own direct chat with the bot. The only
#: other reader there is the person who wrote it, so there is nothing to keep
#: from anybody -- only the fact that it was not sent. Never a contact's chat:
#: the person in it is the contact, and the note is the member's.
PRIVATE_NOTE_IN_DM_LABEL = (
    "(A note to you, written in Lemma by the person in this chat. It was not "
    "sent to the chat.)"
)

#: What a turn started by a line written in one of Lemma's own channels says
#: it came from, on the message. A run it starts is ``asked_in_lemma``: one
#: conversation serves a person across every place of a channel -- Lemma and
#: the platforms alike -- so the conversation cannot say where a turn was
#: asked; only the run can. A run asked in Lemma is answered in Lemma: no
#: platform's guidance, tools, files or progress.
LEMMA_CHANNEL = "lemma_channel"

#: A message's ``source`` when the platform handed something back rather than
#: anybody saying it, and what the run it starts was triggered by.
_HANDED_BACK_TRIGGERS: dict[str, RunTrigger] = {
    "message_replies": RunTrigger.REPLIES,
    "subagent_result": RunTrigger.DELEGATION_RESULT,
    "pod_ask_answer": RunTrigger.DELEGATION_RESULT,
}

#: A note in a thread answering people outside the pod. Its writer has to be
#: named: the brief says whoever writes in this conversation is outside it.
PRIVATE_NOTE_FROM_KEEPER_LABEL = (
    "(A private note to you, written in Lemma by the member who looks after "
    "this conversation -- not by anybody in the chat. Nobody in the chat has "
    "seen it: act on it, but never quote or reveal it there.)"
)

#: A reply typed in Lemma into a thread answering people outside the pod: the
#: member's words, not the stranger's or the contact's.
WRITTEN_BY_KEEPER_LABEL = (
    "(Written in Lemma by the member who looks after this conversation -- not "
    "by anybody outside the pod. Nobody in the chat saw this message; only your "
    "answer is posted there, so write it to make sense on its own to the people "
    "in the chat, and do not quote this message.)"
)

#: A reply typed in Lemma into a member's own slice of a group.
WRITTEN_IN_LEMMA_LABEL = (
    "(Written in Lemma, not in the chat. Nobody in the chat saw this message; "
    "only your answer is posted there, so write it to make sense on its own to "
    "the people in the chat.)"
)


def requested_private_note(message_metadata: Mapping[str, object] | None) -> bool:
    """Whether the person's client marked this send as a note to the agent alone."""
    return bool(
        message_metadata and message_metadata.get(PRIVATE_NOTE_REQUEST_KEY) is True
    )


def written_in_lemma_into(
    conversation: Conversation, surface_kind: str | None
) -> WrittenIn | None:
    """What a message typed in Lemma into this conversation is to be marked as.

    Any conversation answering people outside the pod is the keeper's thread,
    a contact's private chat as much as a group's: whoever types in Lemma there
    is the member looking after it, never the person the bot is talking to.
    Otherwise None for anything but a group's conversation: in a member's
    direct chat the person typing in Lemma is the person the bot is talking
    to, and a plain Lemma conversation has no chat to have missed the message.
    ``surface_kind`` is the place's kind as its surface records it.
    """
    if Audience.of(conversation).answers_outsiders:
        return WrittenIn.OUTSIDERS
    if surface_kind == "CHANNEL":
        return WrittenIn.GROUP
    return None


def note_in_member_dm(conversation: Conversation, surface_kind: str | None) -> bool:
    """Whether a note typed into this conversation is one in a member's own DM.

    The only conversation whose note is labelled as written "by the person in
    this chat". A contact's chat is a DM on the platform too, but the person
    in it is the contact: a note there is the keeper's, and is labelled so.
    """
    return surface_kind == "DM" and not Audience.of(conversation).answers_outsiders


def run_state_for(
    message: MessageState, *, message_source: str | None = None
) -> RunState:
    """What a run started by this message is created with."""
    return RunState(
        trigger=_HANDED_BACK_TRIGGERS.get(message_source or "", RunTrigger.MESSAGE),
        private_note=message.private_note,
        asked_in_lemma=message_source == LEMMA_CHANNEL,
        written_in=message.written_in,
    )


def run_delivers_to_surface(run: RunPrivacy | None) -> bool:
    """Whether anything of this run -- progress, files, its answer -- goes to
    the chat platform its conversation also lives on."""
    return run is None or not (run.private_note or run.asked_in_lemma)


def answers_lemma_message(run: RunPrivacy) -> bool:
    """Whether this run was started by a message typed in Lemma into a group.

    The group never saw that message, so whatever the run posts there answers
    nothing anybody in the chat said.
    """
    return run.written_in is not None


def keeper_started(run: RunPrivacy | None) -> bool:
    """Whether the member who looks after a strangers' thread started this run.

    Typed in Lemma, by them: nobody outside the pod is waiting on it, so there
    is nothing to pass on to the member -- they are the one asking.
    """
    return run is not None and run.written_in is WrittenIn.OUTSIDERS


#: How a label above opens, matched loosely: the labels are plain text, so a
#: sender who writes one of these openings at the start of a line is imitating
#: Lemma, whatever the spacing or case.
_LABEL_OPENING = re.compile(
    r"^(\s*)(\(\s*(?:a\s+(?:private\s+)?note\s+to\s+you|written\s+in\s+lemma)"
    # The history's own notices, which frame what is around them -- "the other
    # chat ends here" read inside a copied message would recast the rest of it.
    r"|\[\s*conversation\s+history\s*\])",
    re.IGNORECASE | re.MULTILINE,
)

#: Put in front of an imitated label, so it reads as what it is.
SPOOFED_LABEL_MARK = "[not a Lemma label, written by the sender] "


def defuse_lemma_labels(text: str) -> str:
    """Mark any line of untrusted text that opens like one of the labels above.

    The labels are what say a message came from a member in Lemma rather than
    from the chat, and a private note is acted on with the member's authority.
    Only a leading ``[platform | name]:`` set a stranger's message apart, so a
    body that wrote the keeper's label on a line of its own read as the keeper
    speaking. The text is kept -- it may be what they are asking about -- but
    the line is marked as theirs.
    """
    return _LABEL_OPENING.sub(
        lambda match: f"{match.group(1)}{SPOOFED_LABEL_MARK}{match.group(2)}", text
    )


def lemma_label(message: MessageState) -> str | None:
    """What heads a message typed in Lemma in the history every later turn reads.

    None for a message that came in from the chat, which its sender label
    already describes, and for one typed in Lemma where nobody else reads.
    """
    if message.private_note:
        # The keeper's thread first: a stamp saying "a DM" never makes a note
        # in a conversation answering somebody outside the pod read as that
        # person's own words.
        if message.written_in is WrittenIn.OUTSIDERS:
            return PRIVATE_NOTE_FROM_KEEPER_LABEL
        if message.private_note_in_dm:
            return PRIVATE_NOTE_IN_DM_LABEL
        return PRIVATE_NOTE_LABEL
    if message.written_in is WrittenIn.OUTSIDERS:
        return WRITTEN_BY_KEEPER_LABEL
    if message.written_in is WrittenIn.GROUP:
        return WRITTEN_IN_LEMMA_LABEL
    return None
