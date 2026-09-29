"""One place mints the identity of an inbound delivery, whichever route it took."""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.source_event_ids import (
    native_source_event_id,
    resend_source_event_id,
    resend_webhook_source_event_id,
    webhook_source_event_id,
)


def test_a_provider_id_is_scoped_to_its_receiver_on_every_route() -> None:
    update = {"update_id": 1}

    assert webhook_source_event_id(
        "telegram", update, b"", receiver="bot-a"
    ) != webhook_source_event_id("telegram", update, b"", receiver="bot-b")
    assert native_source_event_id(
        "telegram", update, receiver_key="key-a"
    ) != native_source_event_id("telegram", update, receiver_key="key-b")


def test_native_id_keeps_its_wire_format() -> None:
    assert (
        native_source_event_id("telegram", {"update_id": 7}, receiver_key="k1")
        == "telegram:native:k1:7"
    )
    assert (
        native_source_event_id("telegram", {"update_id": 7}, receiver_key=None)
        == "telegram:native:unkeyed:7"
    )


def test_native_id_hashes_the_payload_when_the_provider_gives_none() -> None:
    first = native_source_event_id("slack", {"a": 1, "b": 2}, receiver_key="k")
    reordered = native_source_event_id("slack", {"b": 2, "a": 1}, receiver_key="k")

    assert first == reordered
    assert first != native_source_event_id("slack", {"a": 1}, receiver_key="k")


def test_resend_prefers_its_own_handle_over_the_senders_message_id() -> None:
    normalized = {"email_id": "em-1", "message_id": "<sender@example.test>"}

    assert resend_source_event_id(normalized, receiver="s") == "resend:s:em-1"
    assert resend_source_event_id({"message_id": "<m>"}, receiver="s") == "resend:s:<m>"
    assert resend_source_event_id({}, receiver="s") is None


@pytest.mark.parametrize("carried", [True, False])
def test_resend_webhook_falls_back_to_the_generic_id(carried: bool) -> None:
    normalized = {"email_id": "em-1"} if carried else {"subject": "hi"}

    minted = resend_webhook_source_event_id(normalized, b"body", receiver="s")

    if carried:
        assert minted == "resend:s:em-1"
    else:
        assert minted.startswith("resend:s:content-sha256:")
