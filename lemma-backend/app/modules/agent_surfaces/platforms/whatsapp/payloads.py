"""Building the JSON bodies WhatsApp's Cloud API expects.

Pure construction: what an outbound message, an interactive reply-button block,
a list picker, a CTA-URL card and a media caption look like on the wire, and how
each of them is kept inside Meta's length limits. Split from
:mod:`service` — which owns credentials, HTTP and the API calls — because it is
the half with no I/O in it and the half worth reading on its own.

Every string that reaches these builders is model-authored, so every one of them
goes through :mod:`text_format` on the way in. That is not decoration: the agent
writes Markdown, WhatsApp renders a different and much smaller syntax, and an
untranslated string arrives on the phone as literal asterisks. Truncation runs
*after* translation and is followed by a re-balance, because a cut can land in
the middle of a pair and leave the marker this module exists to prevent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
)
from app.modules.agent_surfaces.platforms.attachment_limits import (
    MediaKind,
    send_kind,
)
from app.modules.agent_surfaces.platforms.rendering import chunk_text
from app.modules.agent_surfaces.platforms.whatsapp.text_format import (
    balance_whatsapp_delimiters,
    to_plain_text,
    to_whatsapp_text,
)

# Meta's hard ceiling on a text message body.
WHATSAPP_TEXT_LIMIT = 4096

#: Meta's cap on an interactive message's body text. A flow whose body is
#: longer is rejected outright, which would lose the form as well as the words.
INTERACTIVE_BODY_LIMIT = 1024

#: Meta's caps on a reply button's title and a list row's title.
_BUTTON_TITLE_LIMIT = 20
_LIST_TITLE_LIMIT = 24
#: A list row's description line.
_LIST_DESCRIPTION_LIMIT = 72

#: Under every question asked with buttons. A typed reply answers the question
#: too, and nothing on a button card says so -- the person sees three choices
#: and assumes those are the only three.
TYPE_YOUR_OWN_FOOTER = "Or just type your own answer."

# What an approval card may spend on the action line. The rest of the body is
# the title and the model's reason, which gives way first.
_APPROVAL_ACTION_LIMIT = 400


# Separator for encoding ask_user routing into a WhatsApp button/list ``id``
# (``callback_id~header~value``). The callback id itself uses ``|``, so ``~``
# unambiguously splits the three parts. WhatsApp allows ids up to 256 chars.
WHATSAPP_INTERACTION_SEP = "~"

# Sentinel used in place of a question ``header`` to mark an approval button
# reply (``callback_id~__approval__~<decision>``). The parser routes this to an
# approval decision instead of an ask_user answer.
WHATSAPP_APPROVAL_HEADER = "__approval__"


@dataclass(frozen=True, slots=True)
class WhatsAppRecipient:
    """Where a message goes: one person's chat, or a group.

    A group is addressed by its id with ``recipient_type: "group"``, and Meta
    refuses a send whose type and ``to`` disagree. It also takes less: no
    interactive messages at all (buttons, lists, CTA cards, flows), and read
    receipts, typing and reactions are not documented to work -- so each send
    path asks ``is_group`` before choosing what to send.
    """

    to: str
    is_group: bool = False

    @property
    def recipient_type(self) -> str:
        return "group" if self.is_group else "individual"


def whatsapp_recipient(
    reply_target: Mapping[str, object], *, fallback_wa_id: str | None = None
) -> WhatsAppRecipient | None:
    """The recipient an inbound message's reply goes to, or None.

    A group wins over everything: the sender of a group message is a person in
    it, and falling back to their number would answer a group question in
    private -- the one place the answer was not asked for.
    """
    group_id = str(reply_target.get("group_id") or "").strip()
    if group_id:
        return WhatsAppRecipient(to=group_id, is_group=True)
    wa_id = str(reply_target.get("sender_wa_id") or fallback_wa_id or "").strip()
    return WhatsAppRecipient(to=wa_id) if wa_id else None


def build_whatsapp_interactive(
    callback_id: str, question: SurfaceQuestion
) -> dict[str, Any] | None:
    """Build a WhatsApp interactive payload for one question, or ``None`` if it
    can't be expressed natively (id over 256 chars, more than 10 options, or a
    header containing the reserved separator)."""
    # The reply id packs ``callback_id~header~value`` and is decoded with a
    # 2-split, so the value may contain ``~`` but the header must not — otherwise
    # the split misassigns and the answer is mis-keyed. Fall back to text when a
    # header contains the separator (rare; header is model-authored).
    if WHATSAPP_INTERACTION_SEP in (question.header or ""):
        return None
    rows: list[tuple[str, str, str]] = []
    for option in question.options:
        button_id = (
            f"{callback_id}{WHATSAPP_INTERACTION_SEP}{question.header}"
            f"{WHATSAPP_INTERACTION_SEP}{option.label}"
        )
        if len(button_id.encode("utf-8")) > 256:
            return None
        rows.append((button_id, option.label, option.description or ""))
    if not 1 <= len(rows) <= 10:
        return None
    # The question is model-authored, so it arrives as Markdown like every other
    # outbound string and needs the same translation the message body gets.
    question_text = to_whatsapp_text(question.question or "")
    footer = {"text": TYPE_YOUR_OWN_FOOTER}
    if len(rows) <= 3 and _fits_as_buttons(rows):
        # A reply button has a title and nothing else, so what each option means
        # goes in the body -- otherwise "Refund" and "Credit" are all the person
        # gets to choose between, and the explanation the agent wrote is dropped.
        return {
            "type": "button",
            "body": {"text": _body_with_option_notes(question_text, rows)},
            "footer": footer,
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": rid, "title": title}}
                    for rid, title, _description in rows
                ]
            },
        }
    return {
        "type": "list",
        "body": {"text": question_text[:INTERACTIVE_BODY_LIMIT] or "Please choose"},
        "footer": footer,
        "action": {"button": "Choose", "sections": [{"rows": _list_rows(rows)}]},
    }


def _fits_as_buttons(rows: list[tuple[str, str, str]]) -> bool:
    """Whether every option can be a button that says what it is.

    A button title is cut at 20 characters, so "Schedule for Monday morning"
    and "Schedule for Monday evening" both became "Schedule for Monday " --
    two buttons the person cannot tell apart. Meta refuses
    duplicate titles outright. A list row has more room and a line beneath it.
    """
    titles = [title for _rid, title, _desc in rows]
    return all(0 < len(title) <= _BUTTON_TITLE_LIMIT for title in titles) and len(
        set(titles)
    ) == len(titles)


def _list_rows(rows: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    """List rows whose titles are distinct, numbered when cutting made twins.

    A title cut to fit keeps its whole text on the description line when the
    option has no description of its own, so nothing the agent wrote is lost.
    """
    titles = [truncate_whatsapp_text(title, _LIST_TITLE_LIMIT) for _r, title, _ in rows]
    if len(set(titles)) != len(titles):
        titles = [
            truncate_whatsapp_text(f"{index}. {title}", _LIST_TITLE_LIMIT)
            for index, (_rid, title, _desc) in enumerate(rows, start=1)
        ]
    built: list[dict[str, str]] = []
    for (rid, title, desc), shown in zip(rows, titles, strict=True):
        note = desc.strip() or (title if shown != title else "")
        row = {"id": rid, "title": shown}
        if note:
            row["description"] = truncate_whatsapp_text(note, _LIST_DESCRIPTION_LIMIT)
        built.append(row)
    return built


def _body_with_option_notes(
    question_text: str, rows: list[tuple[str, str, str]]
) -> str:
    """The question, then one line per option that has something to say.

    Fits Meta's 1024-character body: the notes are cut before the question is,
    because the question is what the buttons answer.
    """
    notes = [
        f"- {title}: {to_whatsapp_text(desc)}" for _rid, title, desc in rows if desc
    ]
    question_text = question_text.strip() or "Please choose"
    if not notes:
        return question_text[:INTERACTIVE_BODY_LIMIT]
    question_part = truncate_whatsapp_text(question_text, INTERACTIVE_BODY_LIMIT - 200)
    room = INTERACTIVE_BODY_LIMIT - len(question_part) - 2
    return f"{question_part}\n\n" + truncate_whatsapp_text("\n".join(notes), room)


def whatsapp_approval_text(plan: SurfaceApprovalRenderPlan) -> str:
    """Everything an approval says, uncut: the title, the reason, the action."""
    parts = (
        f"*{to_plain_text(plan.title)}*",
        to_whatsapp_text(plan.reason or ""),
        f"Action: {to_whatsapp_text(plan.action_summary)}"
        if plan.action_summary
        else "",
    )
    return "\n\n".join(part for part in parts if part.strip())


def approval_needs_details_first(plan: SurfaceApprovalRenderPlan) -> bool:
    """Whether the card's 1024 characters would cut what is being approved.

    Cutting is what the card did, and the cut fell on the reason and then the
    action: a person was asked to approve a command they could only half see.
    When it will not fit, the whole text goes as a message of its own first.
    """
    return len(whatsapp_approval_text(plan)) > INTERACTIVE_BODY_LIMIT


def build_whatsapp_approval_interactive(
    plan: SurfaceApprovalRenderPlan, *, details_sent: bool = False
) -> dict[str, Any] | None:
    """Build a WhatsApp reply-button payload for an approval prompt, or ``None``
    if it can't be expressed natively (more than 3 buttons, or an id over 256
    chars). Each button id packs ``callback_id~__approval__~<decision>``.

    ``details_sent`` means the full text already went as a message above, so
    the card carries only the title and points up at it."""
    buttons: list[dict[str, Any]] = []
    for button in plan.buttons:
        button_id = (
            f"{plan.callback_id}{WHATSAPP_INTERACTION_SEP}{WHATSAPP_APPROVAL_HEADER}"
            f"{WHATSAPP_INTERACTION_SEP}{button.decision}"
        )
        if len(button_id.encode("utf-8")) > 256:
            return None
        buttons.append(
            {"type": "reply", "reply": {"id": button_id, "title": button.label[:20]}}
        )
    if not 1 <= len(buttons) <= 3:
        return None
    # The title is wrapped in bold here, so its own markers are stripped first —
    # a ``*`` inside it would close the wrapper early and leave the rest literal.
    title_part = f"*{to_plain_text(plan.title)}*"
    if details_sent:
        body = truncate_whatsapp_text(
            f"{title_part}\n\nThe full request is in the message above.",
            INTERACTIVE_BODY_LIMIT,
        )
        return {
            "type": "button",
            "body": {"text": balance_whatsapp_delimiters(body)},
            "action": {"buttons": buttons},
        }
    # The action is the thing being approved, so it is the last line to go: the
    # reason is written by the model and can run to paragraphs, and cutting the
    # tail of a 1024-character body used to drop the action first.
    action_part = (
        truncate_whatsapp_text(
            f"Action: {to_whatsapp_text(plan.action_summary)}", _APPROVAL_ACTION_LIMIT
        )
        if plan.action_summary
        else ""
    )
    reason_room = (
        INTERACTIVE_BODY_LIMIT - len(title_part) - len(action_part) - 4  # separators
    )
    reason_part = (
        truncate_whatsapp_text(to_whatsapp_text(plan.reason), max(reason_room, 0))
        if plan.reason and reason_room > 0
        else ""
    )
    body_text = balance_whatsapp_delimiters(
        "\n\n".join(
            part for part in (title_part, reason_part, action_part) if part.strip()
        )[:INTERACTIVE_BODY_LIMIT]
    ).strip()
    body_text = body_text or "Approval needed"
    return {
        "type": "button",
        "body": {"text": body_text},
        "action": {"buttons": buttons},
    }


def resolve_whatsapp_send_type(
    *, delivery_mode: str, mime_type: str, size_bytes: int | None = None
) -> str:
    """The Cloud API media type this file will be sent as.

    Delegates the ``auto`` case to ``send_kind`` because Meta caps each media
    type separately (an image at 5 MB, a document at 100 MB) and plays only a
    few formats of each, and ``fits_inline`` reads the same rule. Two copies of
    it would let the size check clear a file the send then rejects.
    """
    requested = str(delivery_mode or "auto").lower()
    if requested != "auto":
        return requested
    return send_kind("WHATSAPP", mime_type, size_bytes=size_bytes).value


#: Documents Meta lists by MIME type. Anything else may be refused at upload.
_DOCUMENT_MIME_TYPES = frozenset(
    {
        "text/plain",
        "application/pdf",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

#: Files that are text whatever their MIME type says: they open as text on
#: the phone, and ``text/plain`` is a document type Meta accepts.
_TEXT_LIKE_SUFFIXES = (
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".html",
    ".htm",
    ".svg",
    ".xml",
    ".yaml",
    ".yml",
    ".log",
    ".txt",
)
_TEXT_LIKE_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "image/svg+xml",
    }
)


def whatsapp_upload_mime(*, file_name: str, mime_type: str, kind: str) -> str:
    """The MIME type to upload a file under, for the kind it is sent as.

    A Markdown report or a CSV was refused at upload -- ``text/markdown`` is not
    on Meta's document list -- and fell back to a link the recipient may not be
    able to open. Text is text: uploaded as ``text/plain`` under its own file
    name, it arrives as the file it is. Media and listed documents keep theirs.
    """
    base = str(mime_type or "").split(";", 1)[0].strip().lower()
    if kind != MediaKind.DOCUMENT.value or base in _DOCUMENT_MIME_TYPES:
        return mime_type
    is_text = (
        base.startswith("text/")
        or base in _TEXT_LIKE_MIME_TYPES
        or str(file_name or "").lower().endswith(_TEXT_LIKE_SUFFIXES)
    )
    return "text/plain" if is_text else mime_type


def flow_with_message(flow: dict[str, JsonValue], message: str) -> dict[str, JsonValue]:
    """Put what the caller wanted to say inside the form it is sending.

    A flow carries its own generic prompt ("Enter your verification code..."),
    and sending the form on its own discards the only text that explains *why*
    it is being shown again -- that the code was wrong, or expired, or that a
    fresh one is on its way. The person then sees an identical blank form and
    no reason for it. The caller's words go first, the standing prompt after.
    """
    text = (message or "").strip()
    if not text:
        return flow
    merged = dict(flow)
    body = dict(merged.get("body") or {})
    standing = (body.get("text") or "").strip()
    combined = f"{text}\n\n{standing}" if standing and standing != text else text
    body["text"] = combined[:INTERACTIVE_BODY_LIMIT]
    merged["body"] = body
    return merged


def whatsapp_message_bodies(message: str) -> list[str]:
    """Translate the agent's answer, then split it into sendable messages.

    A long answer used to be sent as one oversized body that Meta rejects
    outright — the person got nothing rather than a truncated something. Split on
    paragraph, then line, then word boundaries, and each piece is re-balanced
    because the split itself can cut a ``*bold*`` pair in half.
    """
    body = to_whatsapp_text(message)
    if not body:
        return []
    return [
        balanced
        for balanced in (
            balance_whatsapp_delimiters(chunk)
            for chunk in chunk_text(body, limit=WHATSAPP_TEXT_LIMIT)
        )
        if balanced.strip()
    ]


def whatsapp_cta_url_payload(
    *,
    recipient_wa_id: str,
    render_plan: SurfaceDisplayRenderPlan,
) -> dict[str, Any]:
    action = render_plan.primary_action
    body = balance_whatsapp_delimiters(
        truncate_whatsapp_text(
            to_whatsapp_text(
                whatsapp_display_resource_text(render_plan, include_action=False)
            ),
            1024,
        )
    )
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_wa_id,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": body},
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": truncate_whatsapp_button_text(
                        action.label if action else "Open"
                    ),
                    "url": action.url if action else "",
                },
            },
        },
    }


def whatsapp_text_payload(
    *,
    recipient_wa_id: str,
    body: str,
    preview_url: bool,
    recipient_type: str = "individual",
) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": recipient_type,
        "to": recipient_wa_id,
        "type": "text",
        "text": {
            # Balanced *after* truncating: a 4096-character slice can land in the
            # middle of a ``*bold*`` pair, which is the broken marker
            # ``to_whatsapp_text`` exists to prevent.
            "body": balance_whatsapp_delimiters(
                truncate_whatsapp_text(to_whatsapp_text(body), WHATSAPP_TEXT_LIMIT)
            ),
            "preview_url": preview_url,
        },
    }


def whatsapp_display_resource_text(
    render_plan: SurfaceDisplayRenderPlan,
    *,
    include_action: bool = True,
) -> str:
    parts = [f"*{render_plan.title}*"]
    if render_plan.summary:
        parts.append(render_plan.summary)
    parts.extend(render_plan.detail_lines[:5])
    if render_plan.preview_block:
        parts.append(f"```\n{render_plan.preview_block}\n```")
    action = render_plan.primary_action
    if include_action and action is not None:
        parts.append(f"{action.label}: {action.url}")
    return "\n\n".join(parts)


def truncate_whatsapp_button_text(value: str) -> str:
    """A CTA's label, inside Meta's 20 characters *including* the ellipsis."""
    text = " ".join(str(value or "").split()) or "Open"
    return truncate_whatsapp_text(text, _BUTTON_TITLE_LIMIT)


def truncate_whatsapp_text(value: str, max_length: int) -> str:
    """``value`` cut to at most ``max_length`` characters, ellipsis included.

    It kept ``max_length - 1`` characters and then added three dots, so every
    cut string came out two over the limit it was cut for -- and Meta refuses an
    over-long field rather than trimming it.
    """
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3].rstrip() + "..."


def filename_from_url(url: str) -> str:
    return str(url or "").rstrip("/").split("/")[-1].strip()
