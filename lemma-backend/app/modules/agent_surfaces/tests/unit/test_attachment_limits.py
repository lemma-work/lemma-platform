"""The inline-vs-link threshold, and the three ways it used to be wrong.

Every test here corresponds to a file that either was refused when the platform
would have taken it, or was attempted when the platform would not.
"""

from __future__ import annotations

import base64

import pytest

from app.modules.agent_surfaces.platforms.attachment_limits import (
    SURFACE_ATTACHMENT_BYTE_CAPS,
    SURFACE_INLINE_SOFT_BYTE_CAP,
    MediaKind,
    attachment_cap,
    email_inline_cap,
    fits_inline,
    inline_cap,
    media_cap_summary,
    media_kind_for_mime,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    resolve_whatsapp_send_type,
)

_MB = 1024 * 1024


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("image/png", MediaKind.IMAGE),
        ("IMAGE/JPEG", MediaKind.IMAGE),
        ("audio/ogg; codecs=opus", MediaKind.AUDIO),
        ("video/mp4", MediaKind.VIDEO),
        ("application/pdf", MediaKind.DOCUMENT),
        ("", MediaKind.DOCUMENT),
        (None, MediaKind.DOCUMENT),
    ],
)
def test_media_kind_for_mime(mime, expected):
    assert media_kind_for_mime(mime) is expected


def test_whatsapp_send_type_and_cap_read_the_same_classification():
    """The size check must agree with the endpoint the sender actually calls.

    A per-type cap consulted from one MIME branch while the send picks its type
    from another is worse than no per-type cap: it clears a file the API then
    rejects, after a full download and upload.
    """
    for mime in ("image/png", "audio/ogg", "video/mp4", "application/pdf"):
        send_type = resolve_whatsapp_send_type(delivery_mode="auto", mime_type=mime)
        assert send_type == media_kind_for_mime(mime).value


def test_whatsapp_caps_each_media_kind_separately():
    """One number per platform was wrong by twentyfold across WhatsApp's own types."""
    assert attachment_cap("WHATSAPP", media_kind=MediaKind.IMAGE) == 5 * _MB
    assert attachment_cap("WHATSAPP", media_kind=MediaKind.AUDIO) == 16 * _MB
    assert attachment_cap("WHATSAPP", media_kind=MediaKind.VIDEO) == 16 * _MB
    assert attachment_cap("WHATSAPP", media_kind=MediaKind.DOCUMENT) == 100 * _MB


def test_a_whatsapp_image_over_5mb_is_not_attempted_inline():
    """Meta refuses it, so paying for the download and upload first is waste."""
    assert fits_inline("WHATSAPP", 4 * _MB, mime_type="image/png") is True
    assert fits_inline("WHATSAPP", 6 * _MB, mime_type="image/png") is False
    # Same bytes, sent as a document: comfortably inside the 20 MB soft cap.
    assert fits_inline("WHATSAPP", 6 * _MB, mime_type="application/pdf") is True


def test_a_16mb_document_now_attaches_instead_of_becoming_a_link():
    """The soft cap moved 5 MB -> 20 MB; this is the file that changed hands."""
    for platform in ("SLACK", "TELEGRAM", "WHATSAPP"):
        assert fits_inline(platform, 16 * _MB, mime_type="application/pdf") is True


def test_platforms_below_the_soft_cap_still_govern():
    """The soft cap raises nothing — a lower platform ceiling still wins."""
    assert inline_cap("WHATSAPP", media_kind=MediaKind.IMAGE) == 5 * _MB
    assert inline_cap("WHATSAPP", media_kind=MediaKind.AUDIO) == 16 * _MB


@pytest.mark.parametrize("platform", sorted(SURFACE_ATTACHMENT_BYTE_CAPS))
def test_no_chat_cap_exceeds_the_soft_cap(platform):
    assert inline_cap(platform) <= SURFACE_INLINE_SOFT_BYTE_CAP


def test_unknown_size_prefers_a_link():
    """An unstamped size cannot be guaranteed to fit, so it must not be attempted."""
    assert fits_inline("SLACK", None) is False
    assert fits_inline("SLACK", None, mime_type="application/pdf") is False


def test_unknown_platform_falls_back_to_a_default():
    assert attachment_cap("DISCORD") == 16 * _MB
    assert attachment_cap(None) == 16 * _MB


@pytest.mark.parametrize("platform", ["GMAIL", "OUTLOOK", "RESEND"])
def test_email_cap_leaves_room_for_base64(platform):
    """The provider measures the encoded bytes, so the raw cap must be smaller.

    A cap set at the provider's own number sent a 25 MB file as ~34 MB on the
    wire and the whole reply failed — attachment and body together.
    """
    raw = email_inline_cap(platform)
    encoded = len(base64.b64encode(b"x" * 1024)) * raw // 1024
    assert encoded < SURFACE_ATTACHMENT_BYTE_CAPS[platform]


def test_email_ignores_the_chat_soft_cap():
    """Email's fallback is a signed URL that works, so shrinking it buys nothing.

    Applying the chat soft cap here would turn a 25 MB Resend attachment the
    provider accepts into a link, for a reason that only applies to chat.
    """
    assert email_inline_cap("RESEND") > SURFACE_INLINE_SOFT_BYTE_CAP


def test_media_cap_summary_only_speaks_when_kinds_differ():
    assert media_cap_summary("SLACK") is None
    assert media_cap_summary("TELEGRAM") is None
    assert media_cap_summary(None) is None
    assert media_cap_summary("WHATSAPP") == (
        "images up to 5 MB; audio and video up to 16 MB"
    )
