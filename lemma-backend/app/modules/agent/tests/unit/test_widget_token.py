"""Unit tests for short-lived signed widget embed tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest

from app.modules.agent.services.widget_token import (
    InvalidWidgetToken,
    mint_widget_token,
    verify_widget_token,
    widget_serve_path,
)


def _mint(conversation_id, tool_call_id, user_id, *, ttl=300):
    return mint_widget_token(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        user_id=user_id,
        expires_at_epoch=int(time.time()) + ttl,
    )


def test_round_trip_returns_user_id():
    conv, user = uuid4(), uuid4()
    token = _mint(conv, "tc_1", user)
    assert verify_widget_token(token, conversation_id=conv, tool_call_id="tc_1") == user


def test_tampered_signature_rejected():
    conv, user = uuid4(), uuid4()
    token = _mint(conv, "tc_1", user)
    payload, _sig = token.split(".", 1)
    tampered = f"{payload}.AAAA"
    with pytest.raises(InvalidWidgetToken):
        verify_widget_token(tampered, conversation_id=conv, tool_call_id="tc_1")


def test_expired_token_rejected():
    conv, user = uuid4(), uuid4()
    token = _mint(conv, "tc_1", user, ttl=-1)
    with pytest.raises(InvalidWidgetToken):
        verify_widget_token(token, conversation_id=conv, tool_call_id="tc_1")


def test_token_bound_to_conversation_and_tool_call():
    conv, user = uuid4(), uuid4()
    token = _mint(conv, "tc_1", user)
    # Replaying for a different tool_call (same conversation) is rejected.
    with pytest.raises(InvalidWidgetToken):
        verify_widget_token(token, conversation_id=conv, tool_call_id="tc_2")
    # Replaying for a different conversation is rejected.
    with pytest.raises(InvalidWidgetToken):
        verify_widget_token(token, conversation_id=uuid4(), tool_call_id="tc_1")


def test_malformed_token_rejected():
    with pytest.raises(InvalidWidgetToken):
        verify_widget_token("not-a-token", conversation_id=uuid4(), tool_call_id="tc")


def test_legacy_dev_secret_token_rejected():
    """A token forged with the old committed dev secret must no longer verify —
    the insecure raw-HMAC fallback has been removed (security_appsec-02)."""
    conv, user = uuid4(), uuid4()
    payload = json.dumps(
        {"c": str(conv), "t": "tc_1", "u": str(user), "e": int(time.time()) + 300},
        separators=(",", ":"),
    ).encode("utf-8")
    sig = hmac.new(b"lemma-dev-widget-url-secret", payload, hashlib.sha256).digest()

    def _b64e(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    forged = f"{_b64e(payload)}.{_b64e(sig)}"
    with pytest.raises(InvalidWidgetToken):
        verify_widget_token(forged, conversation_id=conv, tool_call_id="tc_1")


def test_serve_path_format():
    conv = uuid4()
    assert widget_serve_path(conv, "tc_9") == f"/public/widgets/serve/{conv}/tc_9"
