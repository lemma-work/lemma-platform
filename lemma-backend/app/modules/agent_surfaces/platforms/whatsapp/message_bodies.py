"""What a WhatsApp message says, read by its type.

Split from :mod:`parser`, which owns the webhook envelope and who sent what to
whom, because this is the other half: Cloud API puts the readable part of a
message somewhere different for every ``type``, and a type nobody wrote a reader
for used to reach the agent as the type's name, or as a Python dict printed
into the transcript where the person's words go.

Every reader answers a string the agent can take as the person's message. What
cannot be read is said to be unreadable, in words marked as a system notice --
never left blank, which would start a run about nothing, and never guessed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from app.modules.agent_surfaces.platforms.common import payload_section

#: Types that are not somebody saying something: an emoji on an earlier
#: message, WhatsApp's own notices (a changed number), and the ping Meta sends
#: when a person opens the chat for the first time. Each started a run -- and,
#: from a stranger, a signup -- answering a message nobody wrote.
SILENT_TYPES = frozenset({"reaction", "system", "request_welcome"})

#: The types that carry a downloadable media object. Anything else with an
#: ``id`` somewhere inside it (an order, a contact card) is not a file.
_MEDIA_TYPES = frozenset({"image", "audio", "video", "document", "sticker"})

# A list of several contacts or order lines is context, not a document.
_MAX_LISTED = 10


def is_silent(msg: Mapping[str, object]) -> bool:
    """Whether this message is a notice about the chat rather than a message."""
    return msg.get("type") in SILENT_TYPES


def is_undeliverable(msg: Mapping[str, object]) -> bool:
    """Whether WhatsApp is reporting a message rather than delivering one.

    Cloud API answers a message it cannot hand over with ``type: "unsupported"``
    and the reason in ``errors`` -- a forwarded sticker, a view-once, a poll.
    There is no media id behind it and nothing to download.
    """
    return msg.get("type") == "unsupported" or bool(msg.get("errors"))


def system_notice(what_happened: str) -> str:
    """A line in the person's place that says it is not the person's words."""
    return f"(System notice, not the person's words: {what_happened})"


def undeliverable_notice(msg: Mapping[str, object]) -> str:
    """What to tell the agent about a message that never actually arrived.

    Read as ordinary media this produced the bare word ``unsupported`` as the
    person's text -- the type name fallback, applied to a type that is not a
    kind of content but an error report. The agent then answered it as if they
    had typed it.
    """
    errors = msg.get("errors")
    first = errors[0] if isinstance(errors, list) and errors else {}
    reason = ""
    if isinstance(first, dict):
        reason = (_text(first, "title") or _text(first, "message")).strip()
    return system_notice(
        "WhatsApp could not deliver their message"
        f"{f' — {reason}' if reason else ''}. None of its content reached Lemma."
    )


def message_body(msg: Mapping[str, object]) -> tuple[str, list[dict[str, object]]]:
    """The readable text of a message, and any attachment, by type.

    A media message's caption lives on the *media* object -- ``image.caption``,
    never ``text.body``, which a media message does not have at all. The type
    name stays as the fallback for media genuinely sent without a caption, so
    the agent at least knows something arrived -- but only for types that *are*
    content, which is why the undeliverable check comes first.
    """
    msg_type = _text(msg, "type") or "text"
    if msg_type == "text":
        return _text(payload_section(msg, "text"), "body"), []
    if msg_type == "interactive":
        return interactive_text(payload_section(msg, "interactive")), []
    if is_undeliverable(msg):
        return undeliverable_notice(msg), []
    if msg_type in _MEDIA_TYPES:
        attachment = parse_attachment(msg, msg_type)
        caption = _text(payload_section(msg, msg_type), "caption").strip()
        return caption or msg_type, ([attachment] if attachment else [])
    reader = _READERS.get(msg_type)
    text = reader(msg).strip() if reader is not None else ""
    if text:
        return text, []
    return (
        system_notice(
            f"they sent a WhatsApp {msg_type} message, which Lemma cannot read. "
            "Ask them to send it as text if it matters."
        ),
        [],
    )


def interactive_text(interactive: Mapping[str, object]) -> str:
    """The label the person tapped, for a button or a list reply.

    A Flow submission (``nfm_reply``) is answered before it gets here when the
    form is still open; reaching here means nothing was waiting on it. Its
    answers are JSON a form produced, not something the person said, so they
    are described rather than printed.
    """
    kind = _text(interactive, "type")
    if kind in ("button_reply", "list_reply"):
        title = _text(payload_section(interactive, kind), "title").strip()
        if title:
            return title
    if kind == "nfm_reply":
        return system_notice(
            "they submitted a WhatsApp form that is no longer open, so its "
            "answers were not used. If they still need it, offer it again."
        )
    return system_notice("they tapped a WhatsApp control Lemma does not recognise.")


def parse_attachment(
    msg: Mapping[str, object], msg_type: str
) -> dict[str, object] | None:
    """One inbound media object, as an attachment the ingest step can save.

    ``filename`` is sent for documents and for nothing else, so an image or a
    voice note has only its mime type to be named by. Ingest completes the name
    from the mime type of the bytes it actually downloads. Without an ``id``
    there is nothing to download, and an attachment that cannot be fetched is
    only a failure waiting in the ingest step.
    """
    media_data = msg.get(msg_type)
    if msg_type not in _MEDIA_TYPES or not isinstance(media_data, dict):
        return None
    media_id = _text(media_data, "id").strip()
    if not media_id:
        return None
    return {
        "id": media_id,
        "name": _text(media_data, "filename") or msg_type,
        "content_type": msg_type,
        "mime_type": media_data.get("mime_type"),
        "size": media_data.get("file_size"),
        "download_url": None,
    }


def _location(msg: Mapping[str, object]) -> str:
    """``📍 name, address (lat,lng) maps-link`` -- what a pin is, in one line."""
    location = payload_section(msg, "location")
    latitude, longitude = location.get("latitude"), location.get("longitude")
    place = ", ".join(
        part
        for part in (_text(location, "name"), _text(location, "address"))
        if part.strip()
    )
    if not _is_number(latitude) or not _is_number(longitude):
        return f"📍 {place}" if place else ""
    coordinates = f"{latitude},{longitude}"
    link = f"https://maps.google.com/?q={coordinates}"
    label = f"{place} ({coordinates})" if place else coordinates
    return f"📍 Shared location: {label} {link}"


def _contacts(msg: Mapping[str, object]) -> str:
    """One line per shared contact card: name, then numbers and emails."""
    cards = msg.get("contacts")
    if not isinstance(cards, list):
        return ""
    lines = [line for line in map(_contact_line, cards[:_MAX_LISTED]) if line]
    if not lines:
        return ""
    return (
        "Shared contact"
        + ("s" if len(lines) > 1 else "")
        + ":\n"
        + "\n".join(f"- {line}" for line in lines)
    )


def _contact_line(card: object) -> str:
    if not isinstance(card, dict):
        return ""
    name = _text(payload_section(card, "name"), "formatted_name").strip()
    details = [
        *_listed(card.get("phones"), "phone"),
        *_listed(card.get("emails"), "email"),
    ]
    company = _text(payload_section(card, "org"), "company").strip()
    if company:
        details.append(company)
    return " — ".join(part for part in (name or "Unnamed contact", *details) if part)


def _listed(entries: object, key: str) -> list[str]:
    if not isinstance(entries, list):
        return []
    return [
        value
        for value in (
            _text(entry, key).strip() for entry in entries if isinstance(entry, dict)
        )
        if value
    ]


def _template_button(msg: Mapping[str, object]) -> str:
    """A quick-reply button on a template: what it said, else what it carries."""
    button = payload_section(msg, "button")
    return _text(button, "text").strip() or _text(button, "payload").strip()


def _order(msg: Mapping[str, object]) -> str:
    """A cart sent from a catalog, as the lines a person would read off it."""
    order = payload_section(msg, "order")
    items = order.get("product_items")
    lines = []
    for item in items[:_MAX_LISTED] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        product = _text(item, "product_retailer_id").strip() or "item"
        quantity = _text(item, "quantity").strip() or "1"
        price = " ".join(
            part
            for part in (_text(item, "item_price"), _text(item, "currency"))
            if part.strip()
        )
        lines.append(f"- {quantity} × {product}" + (f" at {price}" if price else ""))
    note = _text(order, "text").strip()
    if not lines and not note:
        return ""
    head = "Placed an order from the catalog" + (f": {note}" if note else "")
    return "\n".join([head, *lines])


_READERS: dict[str, Callable[[Mapping[str, object]], str]] = {
    "location": _location,
    "contacts": _contacts,
    "button": _template_button,
    "order": _order,
}


def _text(source: object, key: str) -> str:
    """A field as text, and only when it *is* text (or a number).

    ``payload_text`` would ``str()`` whatever it found, and a dict in a field
    meant for words is exactly how ``{'type': 'nfm_reply', ...}`` reached an
    agent as the person's message.
    """
    if not isinstance(source, Mapping):
        return ""
    value = source.get(key)
    if isinstance(value, bool):
        return ""
    if isinstance(value, str | int | float):
        return str(value)
    return ""


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
