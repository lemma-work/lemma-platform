"""Nobody can post a forged Composio webhook.

Its own file, and not `provider`-marked, because neither is incidental.

This is a security control and it was executing in no CI lane at all:
`provider` is excluded from every one -- `UNIT_MARKERS`, `E2E_SHARD_MARKERS`
and the protected weekly lane all say `not provider` -- and `make
test-e2e-runtime`, the only target that includes them, is invoked by no
workflow. On top of that it skipped unless `COMPOSIO_WEBHOOK_SECRET` was set,
and its own comment admitted it "has been skipping ever since".

Nothing about it needs a provider. The signature is a local HMAC, so the secret
is a key the test can choose. It lived in `test_composio_real_e2e.py`, whose
autouse fixture configures a real Composio key and quietly does nothing without
one -- scaffolding this test has no use for, and which is the reason it has to
sit somewhere else rather than merely lose its marker.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.modules.connectors.config import connector_settings

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_composio_webhook_signature_verification(monkeypatch):
    """Not `provider`-marked, and it supplies its own secret.

    This is a security control -- it is what stops anyone posting a forged
    Composio webhook -- and it was running nowhere. `provider` is excluded from
    every CI lane there is (`UNIT_MARKERS`, `E2E_SHARD_MARKERS`, and the
    protected weekly lane all say `not provider`), and on top of that it
    skipped without `COMPOSIO_WEBHOOK_SECRET`. Nothing about it needs a
    provider: the signature is a local HMAC, so the secret is just a key the
    test can choose.
    """
    secret = "whsec_test_only_not_a_real_secret"
    monkeypatch.setattr(
        connector_settings, "composio_webhook_secret", secret, raising=False
    )

    from app.composition.schedule_connectors import (
        ComposioWebhookVerifier,
    )

    payload = json.dumps(
        {
            "trigger_name": "GMAIL_NEW_GMAIL_MESSAGE",
            "connection_id": "ca_test_connection",
            "trigger_id": "ti_test_trigger",
            "payload": {"message_id": "m1", "subject": "hello"},
            "log_id": "log_test",
        }
    )
    webhook_id = "msg_test_123"
    timestamp = str(int(time.time()))
    to_sign = f"{webhook_id}.{timestamp}.{payload}"
    digest = hmac.new(
        secret.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    signature = "v1," + base64.b64encode(digest).decode("utf-8")

    headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": signature,
    }

    verifier = ComposioWebhookVerifier()
    # `verify` is a coroutine, and deliberately so -- the SDK call it makes is
    # blocking and has to be offloaded, which `test_the_verifier_port_is_async`
    # pins. This test predates that and has been skipping ever since, so nothing
    # noticed it was still calling it synchronously.
    result = await verifier.verify(payload, headers)
    assert result["raw_payload"]["connection_id"] == "ca_test_connection"

    # A tampered signature is rejected. Asserting on the message rather than on
    # bare `Exception`: an `AttributeError` from a broken refactor satisfies
    # `pytest.raises(Exception)`, so the negative case would have kept passing
    # while verifying nothing.
    bad_headers = {
        **headers,
        "webhook-signature": "v1," + base64.b64encode(b"wrong").decode(),
    }
    with pytest.raises(Exception) as rejected:
        await verifier.verify(payload, bad_headers)
    assert "signature" in str(rejected.value).lower(), (
        f"rejected for the wrong reason: {rejected.value!r}"
    )
