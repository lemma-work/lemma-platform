from __future__ import annotations

from pathlib import Path

from ..openapi_client.api.apps import (
    app_create,
    app_delete,
    app_dist_archive_get,
    app_get,
    app_list,
    app_source_archive_get,
    app_update,
)
from ..openapi_client.models.create_app_request import CreateAppRequest
from ..openapi_client.models.app_detail_response import AppDetailResponse
from ..openapi_client.models.app_bundle_upload_response import AppBundleUploadResponse
from ..openapi_client.models.app_list_response import AppListResponse
from ..openapi_client.models.update_app_request import UpdateAppRequest
from .base import BoundResource


class PodApps(BoundResource):
    def list(self, *, limit: int = 100) -> AppListResponse:
        return self._call(app_list, self._pod_uuid(), limit=limit)

    def create(self, request: CreateAppRequest) -> AppDetailResponse:
        return self._call(app_create, self._pod_uuid(), body=request)

    def get(self, name: str) -> AppDetailResponse:
        return self._call(app_get, self._pod_uuid(), name)

    def update(self, name: str, request: UpdateAppRequest) -> AppDetailResponse:
        return self._call(app_update, self._pod_uuid(), name, body=request)

    def delete(self, name: str) -> None:
        self._call(app_delete, self._pod_uuid(), name)

    def upload_bundle(
        self,
        name: str,
        *,
        source_archive: str | Path | None = None,
        dist_archive: str | Path | None = None,
    ) -> AppBundleUploadResponse:
        files = {}
        handles = []
        try:
            if source_archive is not None:
                source_path = Path(source_archive)
                handle = source_path.open("rb")
                handles.append(handle)
                files["source_archive"] = (source_path.name, handle, "application/zip")
            if dist_archive is not None:
                dist_path = Path(dist_archive)
                handle = dist_path.open("rb")
                handles.append(handle)
                files["dist_archive"] = (dist_path.name, handle, "application/zip")
            response = self.generated.get_httpx_client().request(
                method="post",
                url=f"/pods/{self._pod_uuid()}/apps/{name}/bundle",
                files=files,
            )
        finally:
            for handle in handles:
                handle.close()
        if response.status_code >= 400:
            raise self._transport._error_from_response(
                response.status_code, None, response.content
            )
        return AppBundleUploadResponse.from_dict(response.json())

    def download_source_archive(self, name: str) -> bytes:
        result = self._call(app_source_archive_get, self._pod_uuid(), name)
        return result.payload.getvalue()

    def download_dist_archive(self, name: str) -> bytes:
        result = self._call(app_dist_archive_get, self._pod_uuid(), name)
        return result.payload.getvalue()
