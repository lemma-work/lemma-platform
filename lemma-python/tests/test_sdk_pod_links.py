from __future__ import annotations

from typing import Any
from uuid import uuid4

from lemma_sdk.openapi_client.api.agent_asks import (
    agent_askable_pod_list,
    agent_pod_link_connect,
    agent_pod_link_disconnect,
    agent_pod_link_list,
)
from lemma_sdk.resources.pod_links import PodLinks

POD = uuid4()
OTHER = uuid4()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def _sent(transport: RecordingTransport) -> dict[str, Any]:
    """The body as it would go over the wire, through the generated model."""
    _, _, kwargs = transport.calls[0]
    return kwargs["body_model"].from_dict(kwargs["body"]).to_dict()


def test_connecting_shares_tables_and_folders_for_reading_only() -> None:
    transport = RecordingTransport()

    PodLinks(transport, pod_id=POD).connect(
        str(OTHER), tables=["orders"], folders=["/specs"]
    )

    endpoint, path_args, _ = transport.calls[0]
    assert endpoint is agent_pod_link_connect
    assert path_args == (POD, OTHER)
    assert _sent(transport) == {
        "shares": [
            {
                "resource_type": "datastore_table",
                "resource_name": "orders",
                "permission_ids": ["datastore.table.read", "datastore.record.read"],
            },
            {
                "resource_type": "folder",
                "resource_name": "/specs",
                "permission_ids": ["folder.read"],
            },
        ]
    }


def test_connecting_with_nothing_named_shares_only_what_is_public() -> None:
    transport = RecordingTransport()

    PodLinks(transport, pod_id=POD).connect(OTHER)

    assert _sent(transport) == {"shares": []}


def test_disconnecting_names_both_pods() -> None:
    transport = RecordingTransport()

    PodLinks(transport, pod_id=POD).disconnect(str(OTHER))

    endpoint, path_args, _ = transport.calls[0]
    assert endpoint is agent_pod_link_disconnect
    assert path_args == (POD, OTHER)


def test_both_listings_are_read_from_this_pod() -> None:
    transport = RecordingTransport()
    links = PodLinks(transport, pod_id=POD)

    links.askable()
    links.list()

    assert [(endpoint, args) for endpoint, args, _ in transport.calls] == [
        (agent_askable_pod_list, (POD,)),
        (agent_pod_link_list, (POD,)),
    ]
