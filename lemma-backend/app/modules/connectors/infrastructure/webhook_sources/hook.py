"""A Lemma's own hook as a webhook source: whatever a service posts becomes a row.

Most services that send a form on -- Typeform, Tally, Webflow, Zapier, Make --
cannot sign what they send, so the address carries the credential: the hook's
id and its secret, in the query. The secret is per hook and the pod keeps only
its hash, so verifying finds the hook first and compares against that one hash,
the way the MCP source does with a subscription's secret.

The delivery is not matched to schedules. `keep` adds a row to the hook's
table, and a DATASTORE schedule watching that table is what starts work -- so a
form's answers reach a Lemma by the same path as a row anyone else adds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.modules.datastore.contracts.hooks import (
    HOOK_PARAM,
    HOOK_SOURCE,
    KEY_PARAM,
    HookDeliveryRefused,
    HookReceipt,
    admit_hook_delivery,
    keep_hook_delivery,
)
from app.modules.schedule.contracts import (
    NormalizedWebhook,
    VerifiedDelivery,
    WebhookDelivery,
    WebhookNotVerified,
    WebhookPayload,
    WebhookRefused,
)


def _hook_named(delivery: WebhookDelivery) -> UUID | None:
    try:
        return UUID(str(delivery.query.get(HOOK_PARAM) or ""))
    except ValueError:
        return None


Admit = Callable[..., Awaitable[dict[str, object] | None]]
Keep = Callable[..., Awaitable[HookReceipt]]


class HookWebhookSource:
    source = HOOK_SOURCE

    def __init__(self, admit: Admit | None = None, keep: Keep | None = None) -> None:
        self._admit = admit or admit_hook_delivery
        self._keep = keep or keep_hook_delivery

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        hook_id = _hook_named(delivery)
        key = delivery.query.get(KEY_PARAM) or ""
        if hook_id is None or not key:
            raise WebhookNotVerified()
        try:
            body = await self._admit(
                hook_id=hook_id,
                key=key,
                content_type=delivery.header("content-type"),
                raw_body=delivery.raw_body,
            )
        except HookDeliveryRefused as refused:
            raise WebhookRefused(refused.status_code, refused.reason) from None
        if body is None:
            raise WebhookNotVerified()
        return VerifiedDelivery(delivery=delivery, payload=body)

    async def observe(self, verified: VerifiedDelivery) -> None:
        return None

    def normalize(self, verified: VerifiedDelivery) -> NormalizedWebhook | None:
        return None

    async def keep(self, verified: VerifiedDelivery) -> WebhookPayload:
        hook_id = _hook_named(verified.delivery)
        if hook_id is None:  # verify found it; this is not reached
            raise WebhookNotVerified()
        try:
            receipt = await self._keep(hook_id=hook_id, body=verified.payload)
        except HookDeliveryRefused as refused:
            raise WebhookRefused(refused.status_code, refused.reason) from None
        # Nothing about the row: whoever holds the address may add rows, and
        # no more -- not even read back the one it added.
        if receipt.duplicate:
            return {"received": True, "duplicate": True}
        return {"received": True}
