from __future__ import annotations

from typing import Any

from ..openapi_client.api.hooks import (
    hook_create,
    hook_delete,
    hook_get,
    hook_list,
    hook_rotate,
    hook_update,
)
from ..openapi_client.models.create_hook_request import CreateHookRequest
from ..openapi_client.models.hook_response import HookResponse
from ..openapi_client.models.hooks_response import HooksResponse
from ..openapi_client.models.issued_hook_response import IssuedHookResponse
from ..openapi_client.models.update_hook_request import UpdateHookRequest
from .base import BoundResource

#: ``update(fields=...)`` left unsaid, as opposed to ``fields=None``, which
#: goes back to keeping the whole body in ``payload``.
_KEEP: Any = object()


class PodHooks(BoundResource):
    """Addresses a service outside the pod posts JSON to: ``pod.hooks``.

    Each delivery becomes one row in the hook's table, written as the member
    who made the hook and marked as from outside the pod. With no ``fields``
    the whole body goes to the table's ``payload`` JSON column; ``fields``
    maps columns to dotted paths into the body (``$`` is the whole body).
    ``create`` and ``rotate`` return the secret and the address once.
    """

    def list(self) -> HooksResponse:
        return self._call(hook_list, self._pod_uuid())

    def get(self, name: str) -> HookResponse:
        return self._call(hook_get, self._pod_uuid(), name)

    def create(
        self, name: str, table: str, *, fields: dict[str, str] | None = None
    ) -> IssuedHookResponse:
        body: dict[str, Any] = {"name": name, "table": table}
        if fields is not None:
            body["fields"] = dict(fields)
        return self._call(
            hook_create, self._pod_uuid(), body=body, body_model=CreateHookRequest
        )

    def update(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        fields: dict[str, str] | None = _KEEP,
    ) -> HookResponse:
        """Switch a hook on or off, or change its mapping (``None``: whole body)."""
        body: dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if fields is not _KEEP:
            body["fields"] = dict(fields) if fields is not None else None
        return self._call(
            hook_update,
            self._pod_uuid(),
            name,
            body=body,
            body_model=UpdateHookRequest,
        )

    def rotate(self, name: str) -> IssuedHookResponse:
        """A new secret and address; the old address stops working at once."""
        return self._call(hook_rotate, self._pod_uuid(), name)

    def delete(self, name: str) -> None:
        self._call(hook_delete, self._pod_uuid(), name)
