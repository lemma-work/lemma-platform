"""The refusals a protocol-2 host gets, held to what that host keys on.

The host is Rust, shipped, and not in this tree any more, so it cannot be run
here. These pin the exact shapes its code reads instead, citing where it reads
them (``desktop/agent-host/src`` at protocol 2, on main before the link merged
-- ``git show <that commit>:desktop/agent-host/src/api.rs``). A change that
breaks one of these changes what every un-updated Desktop does next.
"""

from __future__ import annotations

import json

from app.modules.agent.api.controllers.agent_host_legacy_controller import (
    legacy_poll_answer,
    legacy_refusal,
)

#: ``pub const PROTOCOL_VERSION: u16 = 2;`` in the old ``lib.rs``.
OLD_HOST_PROTOCOL_VERSION = 2
#: ``HostStatus`` in the old ``protocol.rs``: a ``wire_enum!`` in
#: SCREAMING_SNAKE_CASE. Anything outside it would fail to decode, and a
#: decode failure is a transport error the host retries at full speed.
OLD_HOST_STATUSES = {"ONLINE", "OFFLINE", "DRAINING", "UPGRADE_REQUIRED", "REVOKED"}


def test_the_poll_answer_decodes_and_then_fails_on_the_protocol_version():
    body = legacy_poll_answer()

    # ``struct PollResponse`` (old protocol.rs): protocol_version: u16 and
    # host_status are required; commands and poll_after_ms default.
    assert set(body) == {"protocol_version", "host_status", "commands", "poll_after_ms"}
    assert isinstance(body["protocol_version"], int)
    assert 0 <= body["protocol_version"] <= 0xFFFF
    assert body["host_status"] in OLD_HOST_STATUSES
    assert body["commands"] == []
    assert isinstance(body["poll_after_ms"], int) and body["poll_after_ms"] >= 0

    # ``TargetClient::poll`` (old api.rs): ``if result.protocol_version !=
    # crate::PROTOCOL_VERSION { return Err(ApiError::Protocol(..)) }`` -- checked
    # before host_status is looked at. ``ApiError::Protocol`` is not a request
    # rejection, so the worker marks the target OFFLINE with the error and backs
    # off to RETRY_MAX (30s) without bisecting its control batch, and it is not
    # ``is_unauthorized`` / ``is_revoked_or_missing``, so the pairing survives.
    assert body["protocol_version"] != OLD_HOST_PROTOCOL_VERSION


def test_every_other_route_is_a_request_rejection_that_is_not_a_credential_failure():
    response = legacy_refusal()
    body = json.loads(bytes(response.body))

    # ``status_is_request_rejected`` (old api.rs): a client error other than
    # 401, 408 and 429. That is what makes an event batch, a harness publish
    # and an MCP bridge call stop instead of retrying.
    assert 400 <= response.status_code < 500
    assert response.status_code not in {401, 408, 429}
    # ``is_revoked_or_missing`` reads ``detail.code`` and only acts on a 401;
    # this code must never be that one, or the host would forget its pairing.
    assert body["detail"]["code"] == "AGENT_HOST_UPGRADE_REQUIRED"
    assert body["detail"]["code"] != "AGENT_HOST_REVOKED_OR_MISSING"
    assert "Update Lemma Desktop" in body["detail"]["message"]
