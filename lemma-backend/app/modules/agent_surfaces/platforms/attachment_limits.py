"""Per-platform attachment size caps for surface file delivery.

Egress (``display_resource`` type=FILE and email replies) attaches a file's
bytes natively when the file is at or below the platform's cap, and otherwise
falls back to an app/public URL link. Inbound auto-ingest skips downloading
attachments larger than ``INBOUND_ATTACHMENT_BYTE_CAP``.

Caps are conservative approximations of each platform's documented limits; they
are the inline-vs-link threshold, not a hard API guarantee.
"""

from __future__ import annotations

_MB = 1024 * 1024

# Native-attachment ceilings per platform (bytes). At or below → attach inline;
# above → deliver a URL link instead.
SURFACE_ATTACHMENT_BYTE_CAPS: dict[str, int] = {
    "SLACK": 30 * _MB,
    "TELEGRAM": 50 * _MB,  # Bot API sendDocument ceiling
    "WHATSAPP": 16 * _MB,  # Cloud API media ceiling (documents)
    "TEAMS": 25 * _MB,
    "GMAIL": 25 * _MB,
    "OUTLOOK": 25 * _MB,
    "RESEND": 25 * _MB,  # Resend total-message ceiling is ~40 MB
}

# Default cap for any platform not listed above.
_DEFAULT_ATTACHMENT_BYTE_CAP = 16 * _MB

# Universal soft cap on inline attachments: above this we prefer a tidy link even
# when the platform's hard ceiling is higher. Keeps chats light and avoids pushing
# large payloads when a link reads better. The effective inline cap is the smaller
# of this and the platform's hard ceiling.
SURFACE_INLINE_SOFT_BYTE_CAP = 5 * _MB

# Largest inbound attachment we will download + persist to the datastore.
INBOUND_ATTACHMENT_BYTE_CAP = 50 * _MB

# Largest inbound voice/audio attachment whose bytes we hold in memory to
# transcribe at ingress. Larger audio is still saved to the datastore (the agent
# can `listen` to it) but not auto-transcribed, to bound memory on the hot path.
INBOUND_VOICE_TRANSCRIBE_BYTE_CAP = 25 * _MB


def attachment_cap(platform: str | None) -> int:
    """Return the native-attachment hard byte ceiling for a platform."""
    key = str(platform or "").upper()
    return SURFACE_ATTACHMENT_BYTE_CAPS.get(key, _DEFAULT_ATTACHMENT_BYTE_CAP)


def inline_cap(platform: str | None) -> int:
    """Effective inline cap: min(platform hard ceiling, universal soft cap)."""
    return min(attachment_cap(platform), SURFACE_INLINE_SOFT_BYTE_CAP)


def fits_inline(platform: str | None, size_bytes: int | None) -> bool:
    """True when a file of ``size_bytes`` should be attached natively on ``platform``.

    Uses the effective inline cap (``min(hard ceiling, 5 MB soft cap)``). Unknown
    size (``None``) is treated as too large → prefer a URL link, since we cannot
    guarantee it fits.
    """
    if size_bytes is None:
        return False
    return 0 <= size_bytes <= inline_cap(platform)
