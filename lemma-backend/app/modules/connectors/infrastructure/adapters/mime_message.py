"""Build an RFC 822 message from plain fields, for APIs that take raw MIME.

Gmail's ``messages.send`` and ``drafts.create`` accept one thing: a whole
message, base64url-encoded, in ``raw``. So sending an email with an attachment
meant the caller -- usually a model -- assembling multipart MIME by hand and
encoding it, which is exactly the kind of work that goes subtly wrong (a wrong
boundary, a header folded badly, standard base64 where base64url is required)
and fails with an opaque 400.

An operation whose descriptor says ``"encoding": "rfc822"`` instead takes
``to``, ``subject``, ``text``, ``attachments`` and friends, and this builds the
message. Nothing here is Gmail's: any API that takes a raw message can use it.
The attachments arrive already read -- :class:`MaterializedFile` -- by the
file-input resolver, under the caller's authorization.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.modules.connectors.domain.file_input import MaterializedFile


class MimeMessageError(ValueError):
    """The fields cannot make a message; the text says which one."""


def _addresses(value: object, field: str) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ", ".join(str(item) for item in value)
    raise MimeMessageError(f"'{field}' must be an address or a list of addresses.")


def build_rfc822(fields: dict[str, object]) -> bytes:
    """The message as bytes. Raises :class:`MimeMessageError` on bad input."""
    message = EmailMessage()
    _set_headers(message, fields)
    _set_body(message, fields.get("text"), fields.get("html"))
    _add_attachments(message, fields.get("attachments"))
    return message.as_bytes()


def _set_headers(message: EmailMessage, fields: dict[str, object]) -> None:
    to = _addresses(fields.get("to"), "to")
    if to is None:
        raise MimeMessageError("'to' is required.")
    message["To"] = to
    for field, header in (
        ("cc", "Cc"),
        ("bcc", "Bcc"),
        ("reply_to", "Reply-To"),
        ("from", "From"),
    ):
        value = _addresses(fields.get(field), field)
        if value is not None:
            message[header] = value
    message["Subject"] = str(fields.get("subject") or "")
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    # Replying in a thread: the provider threads by these headers as well as
    # by its own thread id, and a reply missing them lands as a new thread in
    # the recipient's client.
    in_reply_to = fields.get("in_reply_to")
    if in_reply_to:
        message["In-Reply-To"] = str(in_reply_to)
        message["References"] = str(fields.get("references") or in_reply_to)


def _set_body(message: EmailMessage, text: object, html: object) -> None:
    if html is not None and text is None:
        message.set_content(str(html), subtype="html")
        return
    message.set_content(str(text or ""))
    if html is not None:
        message.add_alternative(str(html), subtype="html")


def _add_attachments(message: EmailMessage, attachments: object) -> None:
    listed = attachments if isinstance(attachments, list) else [attachments]
    for index, attachment in enumerate(item for item in listed if item is not None):
        if not isinstance(attachment, MaterializedFile):
            raise MimeMessageError(
                f"attachments[{index}] was not read before sending; pass it as "
                '{"pod_path": ...} or {"file_id": ...}.'
            )
        maintype, _, subtype = attachment.media_type.partition("/")
        message.add_attachment(
            attachment.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )


def rfc822_json_body(
    encoding: dict[str, object], payload: dict[str, object]
) -> dict[str, object]:
    """The JSON body an rfc822-encoded operation sends.

    ``encoding`` is the descriptor's ``request_body``: ``raw_field`` names where
    the base64url message goes (``raw``), ``envelope`` optionally wraps it
    (``message`` for a draft), and ``thread_field`` names the provider's own
    thread id field, filled from the caller's ``thread_id``.
    """
    raw = base64.urlsafe_b64encode(build_rfc822(payload)).decode("ascii")
    message: dict[str, object] = {str(encoding.get("raw_field") or "raw"): raw}
    thread_field = encoding.get("thread_field")
    if isinstance(thread_field, str) and payload.get("thread_id"):
        message[thread_field] = payload["thread_id"]
    envelope = encoding.get("envelope")
    return {envelope: message} if isinstance(envelope, str) else message


__all__ = ["MimeMessageError", "build_rfc822", "rfc822_json_body"]
