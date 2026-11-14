"""The pods a caller can join, as the SDK asks for them.

Checks the facade names the organization -- the bound one, or one given -- and
passes the paging where the generated client expects it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from lemma_sdk.openapi_client.api.pods import pod_list_joinable
from lemma_sdk.openapi_client.types import UNSET
from lemma_sdk.resources.pods import BoundPods
from lemma_sdk.transport import MISSING

BOUND_ORG = uuid4()
OTHER_ORG = uuid4()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        """Records what the facade asked for; a body it did not send is left
        out, as the transport leaves it out of the request."""
        if kwargs.get("body", MISSING) is MISSING:
            kwargs.pop("body", None)
            kwargs.pop("body_model", None)
        self.calls.append((endpoint, path_args, kwargs))
        return None


def test_listing_joinable_pods_names_the_organization_and_the_page() -> None:
    transport = RecordingTransport()
    pods = BoundPods(transport, org_id=str(BOUND_ORG), lemma=None)  # type: ignore[arg-type]

    pods.list_joinable()
    pods.list_joinable(org_id=str(OTHER_ORG), limit=20, page_token="pod-9")

    assert transport.calls == [
        (pod_list_joinable, (BOUND_ORG,), {"limit": 100, "page_token": UNSET}),
        (pod_list_joinable, (OTHER_ORG,), {"limit": 20, "page_token": "pod-9"}),
    ]
