"""What the model reads for one user message, whichever harness sends it.

Split from ``pydantic_ai_history`` because it is shared: the in-process
history builder, the mid-run steering capability and the Agent Host prompt all
render a person's message the same way, and a fix to one of them -- quoting,
label defusing, attachments -- has to reach the others.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.private_notes import defuse_lemma_labels, lemma_label
from app.modules.agent.domain.prompt.surface import attachment_listing_block
from app.modules.agent.domain.value_objects import (
    MessageKind,
    MessageRole,
    to_json_value,
)
from app.modules.agent.domain.prompt.channel_context import (
    audience_block,
    channel_context_block,
    participants_block,
)
from app.modules.agent.domain.prompt.sender_labels import (
    quoted_message_block,
    sender_label,
)


def _message_text(msg: object) -> str:
    return getattr(msg, "text", None) or ""


def _state(msg: object) -> MessageState:
    state = getattr(msg, "state", None)
    return state if isinstance(state, MessageState) else MessageState()


def timed_user_prompt_text(msg: object, *, with_roster: bool = True) -> str:
    """A person's message as the model reads it, with when they sent it.

    The time comes from the stored message, not the clock, so a past turn
    replays byte-for-byte on every later run and the provider's cached prefix
    runs through it. A note stamped with "now" and never stored -- as this used
    to be -- vanished from the replay, and the next run's prefix diverged at
    the previous message, re-reading the whole run before it.
    """
    note = _sent_at_note(msg)
    text = user_prompt_text(msg, with_roster=with_roster)
    return f"{note}\n\n{text}" if note else text


def _sent_at_note(msg: object) -> str | None:
    """Only on a person's message: a notice Lemma made up has no sending time,
    and one stamped when it was built would change on every run. Nor on what
    a contact merge copied in: the copy was written when the merge ran, which
    is not when they sent it (``folded_turns``)."""
    if getattr(msg, "role", None) is not MessageRole.USER:
        return None
    if getattr(msg, "kind", None) is not MessageKind.TEXT:
        return None
    metadata = getattr(msg, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("synthetic"):
        return None
    if _state(msg).folded_from is not None:
        return None
    sent = getattr(msg, "created_at", None)
    if not isinstance(sent, datetime):
        return None
    stamp = sent.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"<notes>Sent {stamp} (UTC).</notes>"


def user_prompt_text(msg: object, *, with_roster: bool = True) -> str:
    """What the model actually reads for one user message.

    The stored text plus everything the surface knows about it: who sent it,
    what else was said in the thread, what they attached. Each block is
    independent and any of them can be absent, which is why they are appended
    to a list rather than formatted into a template.

    ``with_roster`` is off for a turn replayed as history: who is in a group is
    what it is now, and an old turn's roster still naming somebody since
    removed from the pod as a member would contradict the current one.
    """
    body = _message_text(msg)
    metadata = getattr(msg, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return body

    platform = metadata.get("surface_platform")
    label = lemma_label(_state(msg))
    pieces = [
        label,
        sender_label(metadata, platform),
        # Only a message Lemma labelled is known to be a member's, and only a
        # notice history assembly wrote is known to be Lemma's own; any other
        # body could be a stranger's imitating either.
        body
        if label or metadata.get("synthetic") is True
        else defuse_lemma_labels(body),
        quoted_message_block(metadata),
        participants_block(metadata) if with_roster else None,
        channel_context_block(metadata),
        audience_block(metadata),
        *_shared_files_blocks(metadata, platform),
        # Its own piece rather than part of the block above, because the two are
        # not alternatives: a message can carry three photos of which one
        # arrived, and the saved-paths block returns early once it has any.
        _failed_files_block(metadata),
    ]
    if "state" in metadata:
        pieces.append(_metadata_state_text(metadata["state"]))
    return "\n\n".join(piece for piece in pieces if piece)


def _transcribed_voice_paths(metadata: dict) -> set[str]:
    """Paths of inbound voice notes whose words are already in the message.

    Ingress transcribes voice notes before the run starts and folds the
    transcript in as the user's own words. Listing those files again as
    something to go and read invites the agent to call `listen` on audio that
    has already been transcribed — the same transcript, billed and waited for
    twice. A voice note that failed to transcribe is deliberately absent here,
    because for that one `listen` is the right call.
    """
    provenance = metadata.get("voice_transcripts")
    if not isinstance(provenance, list):
        return set()
    return {
        str(item.get("path"))
        for item in provenance
        if isinstance(item, dict) and item.get("path") and not item.get("failed")
    }


def _failed_files_block(metadata: dict) -> str | None:
    """What the person attached that never reached us, and why.

    Without this the run cannot tell an unattached message from one whose photo
    failed to download, so it answers the text alone and reads as though it
    ignored the file. Stated as fact, not as an instruction: whether to ask for
    a resend depends on whether the file mattered, which the agent can see and
    this layer cannot.
    """
    failed = metadata.get("failed_files")
    if not isinstance(failed, list) or not failed:
        return None
    lines = []
    for item in failed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "a file").strip()
        reason = str(item.get("reason") or "it could not be received").strip()
        lines.append(f"- {name} — {reason}")
    if not lines:
        return None
    return (
        "The person attached the following, and it did NOT reach you. You "
        "cannot open or read it:\n" + "\n".join(lines)
    )


def _shared_files_blocks(metadata: dict, platform: object) -> list[str]:
    """What the user attached, either already ingested or still raw.

    Ingested files win: once they are in the datastore the agent should reach
    them by path rather than re-reading whatever the surface sent. The
    `display_resource` mechanics are stated once as standing platform guidance,
    so only the paths are listed here.
    """
    ingested_files = metadata.get("ingested_files")
    if isinstance(ingested_files, list) and ingested_files:
        transcribed = _transcribed_voice_paths(metadata)
        saved = "\n".join(
            f"- {path}" for path in ingested_files if path and path not in transcribed
        )
        blocks: list[str] = []
        if saved:
            blocks.append(
                "The user shared files; they are saved in the pod datastore "
                f"at:\n{saved}"
            )
        if transcribed:
            spoken = "\n".join(f"- {path}" for path in sorted(transcribed))
            blocks.append(
                "The message above IS the transcript of the voice note they "
                "sent — their spoken words, already transcribed on arrival. "
                "Treat it as what they said and answer it directly. Calling "
                "`listen` on the file returns this same text and tells you "
                "nothing new. The audio itself is saved at:\n" + spoken
            )
        if blocks:
            return blocks
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return []
    block = attachment_listing_block(attachments, platform=str(platform or ""))
    return [block] if block else [f"Attachments: {len(attachments)}"]


def _metadata_state_text(state: object) -> str:
    try:
        state_json = json.dumps(to_json_value(state), indent=2, sort_keys=True)
    except Exception:
        state_json = json.dumps(str(state))
    return "UI state:\n```json\n" + state_json + "\n```"
