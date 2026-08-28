"""What to call an inbound Telegram file, and what to say its bytes are.

Telegram is the only surface that hands over a file with no type at all. WhatsApp
sends ``mime_type`` on every media object and Slack sends ``mimetype``; a Telegram
``photo`` is an array of sizes carrying a ``file_id``, a width, a height and
nothing else -- no filename, because the person never gave it one, and no type,
because the platform assumes you know a photo is a photo.

That absence used to travel all the way to the person as a refusal. The parsed
name was the type word ``photo``, which has no extension, so the mime guess fell
to ``application/octet-stream``; the ingest step declines to invent an extension
from that (correctly -- ``photo.bin`` looks decided and is not); the datastore
types a file by its name alone, so the row said ``application/octet-stream`` too;
and `view_image` answered "This file is not an image". The agent then told the
person it could not read their photo, which was true, and made it sound like the
model's limitation rather than four layers each doing something defensible.

Two things Telegram *does* know are used here instead:

* **`getFile` answers with a real path** -- ``photos/file_42.jpg``,
  ``voice/file_9.oga`` -- so the extension exists, it is just not on the name the
  update carried. Preferring the parsed name was wrong whenever that name had no
  extension and this one does.
* **The bytes are right there.** A magic number settles it when neither name
  helps, which is what happens for a sticker.

Declared beats derived throughout: a document arrives with a real filename and a
real ``mime_type`` and neither is second-guessed.
"""

from __future__ import annotations

from pathlib import Path
import mimetypes
from typing import Any

from app.core.file_types import is_untyped_mime, sniff_media_mime

#: What Telegram calls a file when the person did not. These are the parser's
#: type words, and a name that is only a type word tells the datastore nothing --
#: so it loses to anything carrying an extension.
_TYPE_WORDS = {
    "photo",
    "document",
    "video",
    "video_note",
    "animation",
    "audio",
    "voice",
    "sticker",
}


def _has_extension(name: str) -> bool:
    return bool(Path(name).suffix)


def _name_of(attachment: dict[str, Any], file_path: str) -> str:
    """What to call the file: the parsed name only if it is really a name."""
    declared = str(attachment.get("name") or "").strip()
    if declared and (_has_extension(declared) or declared.lower() not in _TYPE_WORDS):
        return declared
    return Path(file_path).name.strip() or declared or "telegram_file"


def _mime_of(
    attachment: dict[str, Any], name: str, file_path: str, content: bytes
) -> str:
    """The first source that actually names a type, in order of confidence.

    Declared, then the name, then the path ``getFile`` answered with, then the
    bytes. Ordered rather than nested so adding a source is adding a line, and so
    the fallthrough is one statement instead of four ``if`` blocks that each
    looked optional.
    """
    for candidate in (
        str(attachment.get("mime_type") or ""),
        mimetypes.guess_type(name)[0] or "",
        mimetypes.guess_type(file_path)[0] or "",
        sniff_media_mime(content) or "",
    ):
        if not is_untyped_mime(candidate):
            return candidate.strip()
    return "application/octet-stream"


def resolve_attachment_name_and_mime(
    *,
    attachment: dict[str, Any],
    file_path: str,
    content: bytes,
) -> tuple[str, str]:
    """Name and mime for one downloaded attachment.

    ``file_path`` is what ``getFile`` answered with; ``content`` is what was
    downloaded. Both are needed because either can be the only one that knows:
    the path types a voice note, the bytes type a sticker.

    Answers ``application/octet-stream`` only when the name, the path and the
    bytes themselves all fail to say what this is -- at which point it genuinely
    is an unknown blob, and saying so is honest rather than a guess that looks
    decided.
    """
    name = _name_of(attachment, file_path)
    mime = _mime_of(attachment, name, file_path, content)
    # Give the name the extension its type implies. The datastore types a file by
    # its name alone, so `photo` known to be `image/jpeg` has to become
    # `photo.jpg` here or it is stored as a blob whatever we worked out.
    if not _has_extension(name) and not is_untyped_mime(mime):
        name = f"{name}{mimetypes.guess_extension(mime) or ''}"
    return name, mime
