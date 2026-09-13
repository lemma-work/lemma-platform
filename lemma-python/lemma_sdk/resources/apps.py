from __future__ import annotations

from pathlib import Path
from typing import Any

from ..openapi_client.api.apps import (
    app_create,
    app_delete,
    app_dist_archive_get,
    app_get,
    app_list,
    app_release_list,
    app_release_promote,
    app_source_archive_get,
    app_update,
)
from ..openapi_client.models.create_app_request import CreateAppRequest
from ..openapi_client.models.app_detail_response import AppDetailResponse
from ..openapi_client.models.app_bundle_upload_response import AppBundleUploadResponse
from ..openapi_client.models.app_list_response import AppListResponse
from ..openapi_client.models.app_release_list_response import AppReleaseListResponse
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
            raise self._transport.error_from_response(
                response.status_code, None, response.content
            )
        return AppBundleUploadResponse.from_dict(response.json())

    def list_releases(
        self,
        name: str,
        *,
        limit: int = 50,
        page_token: str | None = None,
    ) -> AppReleaseListResponse:
        """One page of this app's release history, newest first.

        Paged. A response whose ``next_page_token`` is set has more -- pass it
        back as ``page_token``, or use :meth:`list_all_releases`, which does
        that for you.
        """
        return self._call(
            app_release_list,
            self._pod_uuid(),
            name,
            limit=limit,
            page_token=page_token,
        )

    def list_all_releases(self, name: str, *, page_size: int = 200) -> list[Any]:
        """Every release this app has had, newest first, paged to exhaustion.

        Retention keeps a pruned release's row, so an app deployed daily has
        history past the first page -- and because the live release is never
        pruned, a long-lived one can itself be on a later page. Anything that
        has to be complete wants this rather than :meth:`list_releases`.
        """
        items: list[Any] = []
        token: str | None = None
        while True:
            page = self.list_releases(name, limit=page_size, page_token=token)
            items.extend(getattr(page, "items", None) or [])
            token = getattr(page, "next_page_token", None)
            if not isinstance(token, str) or not token:
                return items

    def promote_release(self, name: str, release_ref: str) -> AppDetailResponse:
        """Make an existing release the one this app serves.

        ``release_ref`` is the release number (``7`` or ``v7``) or a prefix of
        its dist digest. No bytes move -- the app's current-release pointer does.
        """
        return self._call(app_release_promote, self._pod_uuid(), name, release_ref)

    def download_source_archive(self, name: str) -> bytes:
        result = self._call(app_source_archive_get, self._pod_uuid(), name)
        return result.payload.getvalue()

    def download_dist_archive(self, name: str) -> bytes:
        result = self._call(app_dist_archive_get, self._pod_uuid(), name)
        return result.payload.getvalue()
