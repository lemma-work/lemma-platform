from __future__ import annotations

from typing import Any

from ..openapi_client.api.functions import (
    function_create,
    function_delete,
    function_get,
    function_list,
    function_permissions_get,
    function_permissions_replace,
    function_revision_get,
    function_revision_list,
    function_revision_promote,
    function_run,
    function_run_get,
    function_run_list,
    function_update,
)
from ..openapi_client.models.create_function_request import CreateFunctionRequest
from ..openapi_client.models.execute_function_request import ExecuteFunctionRequest
from ..openapi_client.models.function_detail_response import FunctionDetailResponse
from ..openapi_client.models.function_list_response import FunctionListResponse
from ..openapi_client.models.function_permissions_replace_request import (
    FunctionPermissionsReplaceRequest,
)
from ..openapi_client.models.function_permissions_response import (
    FunctionPermissionsResponse,
)
from ..openapi_client.models.function_revision_list_response import (
    FunctionRevisionListResponse,
)
from ..openapi_client.models.function_revision_promote_response import (
    FunctionRevisionPromoteResponse,
)
from ..openapi_client.models.function_revision_response import (
    FunctionRevisionResponse,
)
from ..openapi_client.models.function_run_list_response import FunctionRunListResponse
from ..openapi_client.models.function_run_response import FunctionRunResponse
from ..openapi_client.models.update_function_request import UpdateFunctionRequest
from ..types import FunctionInput
from .base import BoundResource, as_uuid, compact


class PodFunctions(BoundResource):
    def list(
        self, *, limit: int = 100, include: list[str] | None = None
    ) -> FunctionListResponse:
        """List functions. Pass ``include=["permissions"]`` to get each
        function's grants in the same request — one query for the whole page,
        rather than a per-function permissions call each."""
        return self._call(
            function_list, self._pod_uuid(), limit=limit, include=include or []
        )

    def create(self, request: CreateFunctionRequest) -> FunctionDetailResponse:
        return self._call(function_create, self._pod_uuid(), body=request)

    def get(self, name: str) -> FunctionDetailResponse:
        return self._call(function_get, self._pod_uuid(), name)

    def update(
        self, name: str, request: UpdateFunctionRequest
    ) -> FunctionDetailResponse:
        return self._call(function_update, self._pod_uuid(), name, body=request)

    def delete(self, name: str) -> None:
        self._call(function_delete, self._pod_uuid(), name)

    def run(
        self,
        name: str,
        input: FunctionInput | None = None,
        *,
        revision: str | None = None,
    ) -> FunctionRunResponse:
        """Run a function.

        ``revision`` runs a specific built revision instead of the live one --
        a revision number (``r12``) or a hash prefix. It requires
        ``function.update``: running a superseded build is an authoring action,
        so a caller who may only execute always gets the revision the pod has
        actually signed off on.
        """
        return self._call(
            function_run,
            self._pod_uuid(),
            name,
            body=compact({"input_data": input, "revision": revision}),
            body_model=ExecuteFunctionRequest,
        )

    execute = run

    def runs(self, name: str, *, limit: int = 100) -> FunctionRunListResponse:
        return self._call(function_run_list, self._pod_uuid(), name, limit=limit)

    def run_get(self, name: str, run_id: str) -> FunctionRunResponse:
        return self._call(function_run_get, self._pod_uuid(), name, as_uuid(run_id))

    def permissions(self, name: str) -> FunctionPermissionsResponse:
        return self._call(function_permissions_get, self._pod_uuid(), name)

    def replace_permissions(
        self,
        name: str,
        request: FunctionPermissionsReplaceRequest,
    ) -> FunctionPermissionsResponse:
        return self._call(
            function_permissions_replace,
            self._pod_uuid(),
            name,
            body=request,
        )

    def revisions(
        self,
        name: str,
        *,
        limit: int = 50,
        page_token: str | None = None,
    ) -> FunctionRevisionListResponse:
        """One page of this function's built revisions, newest first.

        Paged. A response whose ``next_page_token`` is set has more -- pass it
        back as ``page_token``, or use :meth:`all_revisions`.
        """
        return self._call(
            function_revision_list,
            self._pod_uuid(),
            name,
            limit=limit,
            page_token=page_token,
        )

    def all_revisions(self, name: str, *, page_size: int = 200) -> list[Any]:
        """Every revision, newest first, paged to exhaustion.

        See :meth:`AppsResource.list_all_releases` for why a history has pages:
        retention stamps these rows rather than removing them, and the live one
        is never stamped at all.
        """
        items: list[Any] = []
        token: str | None = None
        while True:
            page = self.revisions(name, limit=page_size, page_token=token)
            items.extend(getattr(page, "items", None) or [])
            token = getattr(page, "next_page_token", None)
            if not isinstance(token, str) or not token:
                return items

    def revision(self, name: str, revision_ref: str) -> FunctionRevisionResponse:
        """One revision, with its source and the schemas its code implements."""
        return self._call(function_revision_get, self._pod_uuid(), name, revision_ref)

    def promote_revision(
        self, name: str, revision_ref: str
    ) -> FunctionRevisionPromoteResponse:
        """Make an existing revision live.

        Its input/output/config schemas are restored with it, since they are the
        contract its code implements. The response reports whether that contract
        differs from the one that was live.
        """
        return self._call(
            function_revision_promote, self._pod_uuid(), name, revision_ref
        )
