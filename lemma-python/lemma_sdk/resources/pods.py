from __future__ import annotations

from typing import TYPE_CHECKING

from ..openapi_client.api.pods import (
    pod_create,
    pod_delete,
    pod_get,
    pod_list,
    pod_update,
)
from ..openapi_client.models.pod_create_request import PodCreateRequest
from ..openapi_client.models.pod_list_response import PodListResponse
from ..openapi_client.models.pod_response import PodResponse
from ..openapi_client.models.pod_update_request import PodUpdateRequest
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid

if TYPE_CHECKING:
    from ..client import Lemma
    from ..pod import Pod


class BoundPods(BoundResource):
    def __init__(self, *args, lemma: "Lemma", **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._lemma = lemma

    def list(
        self,
        *,
        org_id: str | None = None,
        limit: int = 100,
        page_token: str | None = None,
    ) -> PodListResponse:
        return self._call(
            pod_list,
            as_uuid(org_id) if org_id else self._org_uuid(),
            limit=limit,
            page_token=page_token if page_token is not None else UNSET,
        )

    def create(self, request: PodCreateRequest) -> PodResponse:
        return self._call(pod_create, body=request)

    def get(self, pod_id: str) -> PodResponse:
        return self._call(pod_get, as_uuid(pod_id))

    def update(self, pod_id: str, request: PodUpdateRequest) -> PodResponse:
        return self._call(pod_update, as_uuid(pod_id), body=request)

    def delete(self, pod_id: str) -> None:
        self._call(pod_delete, as_uuid(pod_id))

    def client(self, pod_id: str) -> "Pod":
        return self._lemma.pod(pod_id)
