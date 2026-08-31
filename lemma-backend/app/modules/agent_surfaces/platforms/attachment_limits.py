"""Per-platform attachment size caps for surface file delivery.

Egress (``display_resource`` type=FILE and email replies) attaches a file's
bytes natively when the file is at or below the platform's cap. What the
fallback is differs by surface, and the difference matters: an email reply
appends a *signed* download URL that anyone holding it can fetch, while a chat
surface falls back to a Lemma app deep link that only opens for a recipient who
can sign in to the pod. A chat recipient without a Lemma account gets nothing
they can open, so the chat caps below are a delivery guarantee for them, not a
formatting preference. Inbound auto-ingest skips downloading attachments larger
than ``INBOUND_ATTACHMENT_BYTE_CAP``.

Three things the numbers here have to respect, each of which was wrong once:

* **A ceiling can depend on the media type.** WhatsApp's Cloud API caps an image
  at 5 MB and a document at 100 MB — a twentyfold spread behind one platform
  name. One number per platform meant either refusing documents we could send or
  attempting images Meta rejects, so ``attachment_cap`` takes the media kind.
* **Email is measured after base64.** A MIME attachment goes on the wire
  base64-encoded, ~4 bytes per 3, so a provider's 25 MB ceiling is roughly
  17 MB of file. ``email_inline_cap`` does that conversion; the table holds the
  provider's on-the-wire number, not a pre-adjusted one.
* **The soft cap is a chat idea.** It exists so a chat stays light, and it costs
  nothing there because a smaller file is still delivered. On email it would
  only downgrade a working attachment to a link, so email does not apply it.
"""

from __future__ import annotations

from enum import StrEnum

_KB = 1024
_MB = 1024 * 1024


class MediaKind(StrEnum):
    """How a platform will send a file, derived from its MIME type.

    The values double as WhatsApp's Cloud API send types (``resolve_whatsapp_send_type``
    returns these), so the cap consulted here and the endpoint the sender actually
    calls cannot disagree — which is the only way a per-type cap is worth having.
    """

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    STICKER = "sticker"
    DOCUMENT = "document"


def media_kind_for_mime(mime_type: str | None) -> MediaKind:
    """Classify a MIME type into the kind of send a platform will perform."""
    mime = str(mime_type or "").strip().lower()
    if mime.startswith("image/"):
        return MediaKind.IMAGE
    if mime.startswith("audio/"):
        return MediaKind.AUDIO
    if mime.startswith("video/"):
        return MediaKind.VIDEO
    return MediaKind.DOCUMENT


# Hard ceiling for one native attachment, as each platform's own API documents
# it: bytes on the wire. For email that is the encoded size, so callers there
# want ``email_inline_cap`` rather than this.
SURFACE_ATTACHMENT_BYTE_CAPS: dict[str, int] = {
    "SLACK": 30 * _MB,
    "TELEGRAM": 50 * _MB,  # Bot API sendDocument ceiling
    "WHATSAPP": 100 * _MB,  # documents; other kinds are far lower, see below
    "TEAMS": 25 * _MB,
    "RESEND": 40 * _MB,  # total-message ceiling
}

# WhatsApp is the one platform whose ceiling moves with the media type. Keyed by
# ``MediaKind``; anything absent falls back to the platform's entry above.
_PER_MEDIA_BYTE_CAPS: dict[str, dict[MediaKind, int]] = {
    "WHATSAPP": {
        MediaKind.IMAGE: 5 * _MB,
        MediaKind.AUDIO: 16 * _MB,
        MediaKind.VIDEO: 16 * _MB,
        MediaKind.STICKER: 100 * _KB,
        MediaKind.DOCUMENT: 100 * _MB,
    },
}

# Default cap for any platform not listed above.
_DEFAULT_ATTACHMENT_BYTE_CAP = 16 * _MB

# Universal soft cap on inline attachments to a *chat* surface: above this we
# prefer a tidy link even when the platform's hard ceiling is higher. The
# effective chat cap is the smaller of this and the platform's ceiling. Raising
# it trades worker memory (the whole file is held in memory to upload) for a
# delivery guarantee, which is the better trade whenever the recipient may have
# no Lemma account and so cannot open the link fallback at all.
SURFACE_INLINE_SOFT_BYTE_CAP = 20 * _MB

# Raw bytes per 10 bytes on the wire, for an email attachment. base64 is 4 chars
# per 3 bytes (a 1.333x expansion); the rest of the margin covers the encoder's
# line breaks, the MIME headers, and the message body sharing the same ceiling.
_EMAIL_RAW_BYTES_PER_TEN_WIRE = 7

# Largest inbound attachment we will download + persist to the datastore.
INBOUND_ATTACHMENT_BYTE_CAP = 50 * _MB

# Largest inbound voice/audio attachment whose bytes we hold in memory to
# transcribe at ingress. Larger audio is still saved to the datastore (the agent
# can `listen` to it) but not auto-transcribed, to bound memory on the hot path.
INBOUND_VOICE_TRANSCRIBE_BYTE_CAP = 25 * _MB


def attachment_cap(platform: str | None, *, media_kind: MediaKind | None = None) -> int:
    """Hard on-the-wire ceiling for one native attachment.

    ``media_kind`` matters only where a platform caps media types separately
    (WhatsApp); elsewhere it is ignored and the platform's single ceiling applies.
    """
    key = str(platform or "").upper()
    per_media = _PER_MEDIA_BYTE_CAPS.get(key)
    if per_media is not None and media_kind is not None:
        capped = per_media.get(media_kind)
        if capped is not None:
            return capped
    return SURFACE_ATTACHMENT_BYTE_CAPS.get(key, _DEFAULT_ATTACHMENT_BYTE_CAP)


def inline_cap(platform: str | None, *, media_kind: MediaKind | None = None) -> int:
    """Effective chat inline cap: ``min(platform ceiling, universal soft cap)``."""
    return min(
        attachment_cap(platform, media_kind=media_kind),
        SURFACE_INLINE_SOFT_BYTE_CAP,
    )


def email_inline_cap(platform: str | None) -> int:
    """Largest *raw* file whose base64 form still fits the provider's ceiling.

    No soft cap: an oversize email attachment degrades to a signed URL the
    recipient can actually fetch, so shrinking the threshold would only mean
    sending a link where an attachment would have worked.
    """
    return attachment_cap(platform) * _EMAIL_RAW_BYTES_PER_TEN_WIRE // 10


def fits_inline(
    platform: str | None,
    size_bytes: int | None,
    *,
    mime_type: str | None = None,
) -> bool:
    """True when a file should be attached natively on a chat ``platform``.

    Uses the effective chat cap for the kind of send ``mime_type`` implies.
    Unknown size (``None``) is treated as too large → prefer a link, since we
    cannot guarantee it fits.
    """
    if size_bytes is None:
        return False
    cap = inline_cap(platform, media_kind=media_kind_for_mime(mime_type))
    return 0 <= size_bytes <= cap


def media_cap_summary(platform: str | None) -> str | None:
    """Human phrase for the kinds a platform caps below its headline number.

    ``None`` when one number covers every kind, which is every platform but
    WhatsApp. Used to keep the agent-facing guidance honest without hard-coding
    Meta's table into a prompt string.
    """
    key = str(platform or "").upper()
    per_media = _PER_MEDIA_BYTE_CAPS.get(key)
    if not per_media:
        return None
    headline = inline_cap(platform, media_kind=MediaKind.DOCUMENT)
    lower = {
        kind: min(cap, SURFACE_INLINE_SOFT_BYTE_CAP)
        for kind, cap in per_media.items()
        if kind is not MediaKind.STICKER
        and min(cap, SURFACE_INLINE_SOFT_BYTE_CAP) < headline
    }
    if not lower:
        return None
    by_cap: dict[int, list[str]] = {}
    for kind, cap in sorted(lower.items(), key=lambda item: item[1]):
        by_cap.setdefault(cap, []).append(_MEDIA_KIND_LABELS[kind])
    return "; ".join(
        f"{_join(kinds)} up to {cap // _MB} MB" for cap, kinds in by_cap.items()
    )


# Plain-English label per kind, for ``media_cap_summary``. Not derivable by
# adding an "s" — "audios" is not a word.
_MEDIA_KIND_LABELS: dict[MediaKind, str] = {
    MediaKind.IMAGE: "images",
    MediaKind.AUDIO: "audio",
    MediaKind.VIDEO: "video",
    MediaKind.STICKER: "stickers",
    MediaKind.DOCUMENT: "documents",
}


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"
