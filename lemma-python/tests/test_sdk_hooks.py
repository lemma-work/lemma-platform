from __future__ import annotations

from typing import Any
from uuid import uuid4

from lemma_sdk.openapi_client.api.hooks import hook_create, hook_rotate, hook_update
from lemma_sdk.resources.hooks import PodHooks

POD = uuid4()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def _sent(call: tuple[Any, tuple[Any, ...], dict[str, Any]]) -> dict[str, Any]:
    _, _, kwargs = call
    return kwargs["body_model"].from_dict(kwargs["body"]).to_dict()


def test_create_sends_fields_only_when_given() -> None:
    transport = RecordingTransport()
    hooks = PodHooks(transport, pod_id=POD)

    hooks.create("typeform", "submissions")
    hooks.create("tally", "leads", fields={"email": "data.email", "raw": "$"})

    whole, mapped = transport.calls
    assert whole[0] is hook_create and whole[1] == (POD,)
    assert _sent(whole) == {"name": "typeform", "table": "submissions"}
    assert _sent(mapped)["fields"] == {"email": "data.email", "raw": "$"}


def test_update_tells_leaving_fields_alone_from_dropping_the_mapping() -> None:
    transport = RecordingTransport()
    hooks = PodHooks(transport, pod_id=POD)

    hooks.update("typeform", enabled=False)
    hooks.update("typeform", fields=None)
    hooks.rotate("typeform")

    switched, whole_body, rotated = transport.calls
    assert switched[0] is hook_update and switched[1] == (POD, "typeform")
    assert _sent(switched) == {"enabled": False}
    assert _sent(whole_body) == {"fields": None}
    assert rotated[0] is hook_rotate and rotated[1] == (POD, "typeform")
