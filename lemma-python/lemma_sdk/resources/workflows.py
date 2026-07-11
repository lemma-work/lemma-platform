from __future__ import annotations

from ..openapi_client.api.workflows import (
    workflow_create,
    workflow_delete,
    workflow_get,
    workflow_graph_update,
    workflow_list,
    workflow_run_cancel,
    workflow_run_create,
    workflow_run_form_submit,
    workflow_run_get,
    workflow_run_list,
    workflow_run_waiting_assigned_to_me,
    workflow_update,
)
from ..openapi_client.models.workflow_detail_response import WorkflowDetailResponse
from ..openapi_client.models.workflow_create_request import WorkflowCreateRequest
from ..openapi_client.models.workflow_graph_update_request import (
    WorkflowGraphUpdateRequest,
)
from ..openapi_client.models.workflow_list_response import WorkflowListResponse
from ..openapi_client.models.workflow_run_form_submit_request import (
    WorkflowRunFormSubmitRequest,
)
from ..openapi_client.models.workflow_run_list_response import WorkflowRunListResponse
from ..openapi_client.models.workflow_run_response import WorkflowRunResponse
from ..openapi_client.models.workflow_run_wait_assignment_list_response import (
    WorkflowRunWaitAssignmentListResponse,
)
from ..openapi_client.models.workflow_update_request import WorkflowUpdateRequest
from ..types import FunctionInput
from .base import BoundResource, as_uuid


class PodWorkflows(BoundResource):
    def list(self, *, limit: int = 100) -> WorkflowListResponse:
        return self._call(workflow_list, self._pod_uuid(), limit=limit)

    def create(self, request: WorkflowCreateRequest) -> WorkflowDetailResponse:
        return self._call(workflow_create, self._pod_uuid(), body=request)

    def get(self, name: str) -> WorkflowDetailResponse:
        return self._call(workflow_get, self._pod_uuid(), name)

    def update(self, name: str, request: WorkflowUpdateRequest) -> WorkflowDetailResponse:
        return self._call(workflow_update, self._pod_uuid(), name, body=request)

    def update_graph(
        self, name: str, request: WorkflowGraphUpdateRequest | dict
    ) -> WorkflowDetailResponse:
        return self._call(
            workflow_graph_update,
            self._pod_uuid(),
            name,
            body=request,
            body_model=WorkflowGraphUpdateRequest,
        )

    def delete(self, name: str) -> None:
        self._call(workflow_delete, self._pod_uuid(), name)

    def create_run(self, name: str) -> WorkflowRunResponse:
        """Create a run. Takes no inputs: if the workflow's entry node is a
        form, the returned run is WAITING with `active_wait` describing the
        form to submit via submit_form()."""
        return self._call(workflow_run_create, self._pod_uuid(), name)

    def run(self, name: str) -> WorkflowRunResponse:
        """Alias for :meth:`create_run`, for symmetry with ``functions.run`` /
        ``agents.run`` / ``queries.run``. Workflow inputs arrive via FORM nodes
        (see :meth:`submit_form`), not at start, so this takes only a name."""
        return self.create_run(name)

    def run_get(self, run_id: str) -> WorkflowRunResponse:
        return self._call(workflow_run_get, self._pod_uuid(), as_uuid(run_id))

    def runs(self, name: str, *, limit: int = 100) -> WorkflowRunListResponse:
        return self._call(workflow_run_list, self._pod_uuid(), name, limit=limit)

    def submit_form(
        self,
        run_id: str,
        *,
        node_id: str,
        inputs: FunctionInput | None = None,
    ) -> WorkflowRunResponse:
        """Submit the form the run is waiting on. node_id must match the
        run's active wait (see run.active_wait.node_id)."""
        return self._call(
            workflow_run_form_submit,
            self._pod_uuid(),
            as_uuid(run_id),
            body={"node_id": node_id, "inputs": inputs or {}},
            body_model=WorkflowRunFormSubmitRequest,
        )

    def cancel_run(self, run_id: str) -> WorkflowRunResponse:
        return self._call(workflow_run_cancel, self._pod_uuid(), as_uuid(run_id))

    def list_my_waits(self, *, limit: int = 100) -> WorkflowRunWaitAssignmentListResponse:
        """Active form waits assigned to the current user (approval queue)."""
        return self._call(
            workflow_run_waiting_assigned_to_me, self._pod_uuid(), limit=limit
        )
