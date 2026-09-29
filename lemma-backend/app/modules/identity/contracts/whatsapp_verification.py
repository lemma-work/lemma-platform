"""What a WhatsApp ingest path needs to recognise a mobile-verification command.

A person proves they own a phone by sending a code to Lemma's own number. That
message arrives on the same webhook as every other WhatsApp message, so the
surfaces module has to tell the two apart before it treats one as agent chat --
and identity owns both what the reserved command looks like and whether the
deployment is set up to verify at all.

A submodule, like ``onboarding`` beside it: it reaches the verification service,
and ``contracts/__init__`` is imported by anything that wants any contract.
"""

from __future__ import annotations

from app.modules.identity.services.whatsapp_mobile_verification import (
    is_whatsapp_verification_configured,
    parse_reserved_verification_message,
)

__all__ = [
    "is_whatsapp_verification_configured",
    "parse_reserved_verification_message",
]
