from __future__ import annotations

from typing import Any
from uuid import uuid4

from lemma_sdk.openapi_client.api.contact_outreach import contact_outreach_send
from lemma_sdk.resources.contacts import PodContacts

POD = uuid4()
CONTACT = uuid4()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def _sent(call: tuple[Any, tuple[Any, ...], dict[str, Any]]) -> dict[str, Any]:
    _, _, kwargs = call
    return kwargs["body_model"].from_dict(kwargs["body"]).to_dict()


def test_outreach_writes_to_one_handle_and_sends_only_what_was_named() -> None:
    transport = RecordingTransport()
    contacts = PodContacts(transport, pod_id=POD)

    contacts.outreach(CONTACT, handle="dana@client.example", text="Following up")
    contacts.outreach(
        str(CONTACT),
        handle="+447700900123",
        text="Hi",
        kind="PHONE",
        client_message_id="m-1",
    )

    first, second = transport.calls
    assert first[0] is contact_outreach_send
    assert first[1] == (POD, CONTACT)
    assert _sent(first) == {"handle": "dana@client.example", "text": "Following up"}
    assert _sent(second) == {
        "handle": "+447700900123",
        "text": "Hi",
        "kind": "PHONE",
        "client_message_id": "m-1",
    }
