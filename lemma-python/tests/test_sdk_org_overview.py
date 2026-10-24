"""The organization overview reads, as the SDK sends them.

One call per question across every pod an organization holds; these check the
facade passes the organization and the options where the generated client
expects them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from lemma_sdk.openapi_client.api.organizations import (
    org_apps_list,
    org_home_seen_get,
    org_home_seen_set,
    org_needs_you_list,
    org_recents_get,
)
from lemma_sdk.resources.orgs import Orgs
from lemma_sdk.transport import MISSING

ORG = uuid4()


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


def test_each_read_names_its_organization() -> None:
    transport = RecordingTransport()
    orgs = Orgs(transport)

    orgs.needs_you(str(ORG))
    orgs.needs_you(str(ORG), limit=20)
    orgs.recents(str(ORG), days=7, chats=3, timezone="Europe/Berlin")
    orgs.apps(str(ORG))
    orgs.home_seen(str(ORG))

    assert transport.calls == [
        (org_needs_you_list, (ORG,), {"limit": 100}),
        (org_needs_you_list, (ORG,), {"limit": 20}),
        (
            org_recents_get,
            (ORG,),
            {"days": 7, "chats": 3, "timezone": "Europe/Berlin"},
        ),
        (org_apps_list, (ORG,), {}),
        (org_home_seen_get, (ORG,), {}),
    ]


def test_home_is_marked_seen_now_or_as_of_a_moment() -> None:
    transport = RecordingTransport()
    orgs = Orgs(transport)
    loaded = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)

    orgs.mark_home_seen(str(ORG))
    orgs.mark_home_seen(str(ORG), seen_at=loaded)

    (now_endpoint, now_args, now_kwargs), (at_endpoint, at_args, at_kwargs) = (
        transport.calls
    )
    assert (now_endpoint, now_args, now_kwargs) == (org_home_seen_set, (ORG,), {})
    assert (at_endpoint, at_args) == (org_home_seen_set, (ORG,))
    sent = at_kwargs["body_model"].from_dict(at_kwargs["body"]).to_dict()
    assert datetime.fromisoformat(sent["seen_at"]) == loaded
