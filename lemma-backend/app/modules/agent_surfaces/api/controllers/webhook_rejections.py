"""What a refused per-number WhatsApp delivery leaves behind for an operator.

A refused delivery is answered with a status Meta records and nobody here reads,
so without these the only symptom of a mistyped app secret on one pooled number
was that number going quiet. Each refusal logs which number, why, and which
secret was tried -- never the secret or the signature themselves -- and counts
on ``lemma.surface.webhook.rejected`` so a rate can be alerted on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from opentelemetry import metrics

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity

logger = get_logger(__name__)

meter = metrics.get_meter(__name__)
rejected_counter = meter.create_counter("lemma.surface.webhook.rejected")

SignatureRejection = Literal["missing", "invalid", "unconfigured"]
#: ``not_in_pool`` is the settings secret too, named apart because a number the
#: pool has never heard of on a per-number URL is itself the likely fault.
SecretSource = Literal["pool", "settings", "not_in_pool"]


def whatsapp_secret_source(number: WhatsAppNumberEntity | None) -> SecretSource:
    if number is None:
        return "not_in_pool"
    return "pool" if number.app_secret else "settings"


def whatsapp_signature_rejection(
    *, headers: Mapping[str, str], app_secret: str | None
) -> SignatureRejection:
    """Why the signature check refused, in the order the check itself asks."""
    if not app_secret:
        return "unconfigured"
    if not (headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")):
        return "missing"
    return "invalid"


def record_whatsapp_signature_rejected(
    *,
    phone_number_id: str,
    number: WhatsAppNumberEntity | None,
    headers: Mapping[str, str],
    app_secret: str | None,
) -> None:
    reason = whatsapp_signature_rejection(headers=headers, app_secret=app_secret)
    logger.warning(
        "agent_surfaces.webhook_controller.whatsapp_number_signature_rejected.denied",
        phone_number_id=phone_number_id,
        reason=reason,
        # Not `secret_source`: the log pipeline redacts any field whose name
        # says secret, which would have hidden exactly the answer this is for.
        verified_with=whatsapp_secret_source(number),
    )
    rejected_counter.add(1, {"platform": "whatsapp", "reason": reason})


def record_whatsapp_number_mismatch(
    *, phone_number_id: str, addressed: set[str]
) -> None:
    """An authentic body addressed to a number other than the path's.

    Phone number ids are identifiers Meta publishes, not secrets, so naming the
    ones the body carried is what lets an operator tell a mis-pointed callback
    URL from a co-tenant's traffic.
    """
    logger.warning(
        "agent_surfaces.webhook_controller.whatsapp_number_mismatch.denied",
        phone_number_id=phone_number_id,
        addressed_phone_number_ids=sorted(addressed),
    )
    rejected_counter.add(1, {"platform": "whatsapp", "reason": "number_mismatch"})
