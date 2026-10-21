"""``PodSurfaces.upsert`` against a pod whose agent already has a mailbox.

Every agent is given an email surface when it is created, named
``resend-<agent>`` (``resend-assistant`` for the pod's own). So the plain
``upsert("resend", ...)`` finds no surface called ``resend``, and its create is
refused because the agent already reaches Resend somewhere else. The refusal
names that surface; the upsert has to change it, not report the 409 -- and a
``name`` in the request has to be the surface addressed, not thrown away.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from lemma_sdk.errors import api_error
from lemma_sdk.openapi_client.api.agent_surfaces import (
    agent_surface_create,
    agent_surface_update,
)
from lemma_sdk.resources.surfaces import PodSurfaces

POD = uuid4()
CONTACTS_ON = {"config": {"contacts": {"answer": "anyone"}}}


def _conflict(held: str) -> Exception:
    return api_error(
        409,
        f"This agent already has a Resend surface ('{held}').",
        code="AGENT_SURFACE_AGENT_PLATFORM_CONFLICT",
        details={"conflicting_surface": {"pod_id": str(POD), "name": held}},
    )


class PodWithMailbox:
    """A transport that holds surfaces by name, and one mailbox per agent."""

    def __init__(self, *surfaces: str) -> None:
        self.surfaces = set(surfaces)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        body = kwargs["body_model"].from_dict(kwargs["body"]).to_dict()
        if endpoint is agent_surface_update:
            name = path_args[1]
            self.calls.append(("update", name, body))
            if name not in self.surfaces:
                raise api_error(404, f"Agent surface '{name}' not found")
            return {"name": name, **body}
        assert endpoint is agent_surface_create
        self.calls.append(("create", body["name"], body))
        if body["platform"] == "RESEND":
            if body["name"] in self.surfaces:
                raise api_error(
                    409,
                    f"Surface '{body['name']}' already exists in this pod",
                    code="AGENT_SURFACE_ALREADY_EXISTS",
                )
            held = next(iter(self.surfaces))
            raise _conflict(held)
        self.surfaces.add(body["name"])
        return body


def test_upsert_by_platform_updates_the_mailbox_the_agent_already_has() -> None:
    pod = PodWithMailbox("resend-assistant")
    surfaces = PodSurfaces(pod, pod_id=POD)

    result = surfaces.upsert("RESEND", dict(CONTACTS_ON))

    assert result["name"] == "resend-assistant"
    assert pod.calls[-1] == ("update", "resend-assistant", CONTACTS_ON)
    # And the same again: an upsert is idempotent.
    surfaces.upsert("RESEND", dict(CONTACTS_ON))
    assert pod.calls[-1] == ("update", "resend-assistant", CONTACTS_ON)


def test_a_name_in_the_request_is_the_surface_addressed() -> None:
    pod = PodWithMailbox("resend-assistant")
    surfaces = PodSurfaces(pod, pod_id=POD)

    surfaces.upsert("RESEND", {"name": "resend-assistant", **CONTACTS_ON})

    assert pod.calls == [("update", "resend-assistant", CONTACTS_ON)]


def test_a_chosen_name_is_never_redirected_onto_another_surface() -> None:
    """Asking for ``support`` is asking for a distinct surface. Quietly
    rewriting the agent's mailbox in its place would change the wrong thing, so
    the refusal reaches the caller."""
    pod = PodWithMailbox("resend-assistant")
    surfaces = PodSurfaces(pod, pod_id=POD)

    with pytest.raises(Exception) as refused:
        surfaces.upsert("RESEND", {"name": "support", **CONTACTS_ON})

    assert getattr(refused.value, "status_code", None) == 409
    assert [kind for kind, _, _ in pod.calls] == ["update", "create"]


def test_a_new_surface_is_still_created() -> None:
    pod = PodWithMailbox()
    surfaces = PodSurfaces(pod, pod_id=POD)

    surfaces.upsert("slack", {"config": {}})

    assert pod.calls[-1][:2] == ("create", "slack")
    assert pod.calls[-1][2]["platform"] == "SLACK"
