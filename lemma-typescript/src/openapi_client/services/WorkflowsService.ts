/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowCreateRequest } from '../models/WorkflowCreateRequest.js';
import type { WorkflowDetailResponse } from '../models/WorkflowDetailResponse.js';
import type { WorkflowGraphUpdateRequest } from '../models/WorkflowGraphUpdateRequest.js';
import type { WorkflowListResponse } from '../models/WorkflowListResponse.js';
import type { WorkflowRunFormSubmitRequest } from '../models/WorkflowRunFormSubmitRequest.js';
import type { WorkflowRunListResponse } from '../models/WorkflowRunListResponse.js';
import type { WorkflowRunResponse } from '../models/WorkflowRunResponse.js';
import type { WorkflowRunWaitAssignmentListResponse } from '../models/WorkflowRunWaitAssignmentListResponse.js';
import type { WorkflowUpdateRequest } from '../models/WorkflowUpdateRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WorkflowsService {
    /**
     * List Workflow Runs Waiting For Current User
     * The current user's approval queue: active form waits assigned to them, with the owning run.
     * @param podId
     * @param limit
     * @param pageToken
     * @returns WorkflowRunWaitAssignmentListResponse Successful Response
     * @throws ApiError
     */
    public static workflowRunWaitingAssignedToMe(
        podId: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<WorkflowRunWaitAssignmentListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/workflow-runs/waiting/assigned-to-me',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Workflow Run
     * Get current state, context, step history, and the active wait (when WAITING) of a workflow run.
     * @param podId
     * @param runId
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    public static workflowRunGet(
        podId: string,
        runId: string,
    ): CancelablePromise<WorkflowRunResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/workflow-runs/{run_id}',
            path: {
                'pod_id': podId,
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Workflow Run
     * Cancel a non-terminal run. The active wait (if any) is cancelled in the same transaction; late completion events for cancelled waits are dropped. Cancelling a terminal run returns 409.
     * @param podId
     * @param runId
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    public static workflowRunCancel(
        podId: string,
        runId: string,
    ): CancelablePromise<WorkflowRunResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/workflow-runs/{run_id}/cancel',
            path: {
                'pod_id': podId,
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Submit Workflow Run Form
     * Submit the form the run is waiting on. `node_id` must match the run's active HUMAN wait (409 when the run is not waiting on a form, 422 on node mismatch, 403 when the wait is assigned to someone else). The submitted `inputs` become the form node's output, available to later nodes as `<node_id>.<field>`.
     * @param podId
     * @param runId
     * @param requestBody
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    public static workflowRunFormSubmit(
        podId: string,
        runId: string,
        requestBody: WorkflowRunFormSubmitRequest,
    ): CancelablePromise<WorkflowRunResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/workflow-runs/{run_id}/form',
            path: {
                'pod_id': podId,
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Visualize Workflow Run
     * Render an HTML view of a run overlaid on its workflow graph.
     * @param podId
     * @param runId
     * @returns string Successful Response
     * @throws ApiError
     */
    public static workflowRunVisualize(
        podId: string,
        runId: string,
    ): CancelablePromise<string> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/workflow-runs/{run_id}/visualize',
            path: {
                'pod_id': podId,
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Workflows
     * List all workflows in a pod.
     * @param podId
     * @param limit
     * @param pageToken
     * @returns WorkflowListResponse Successful Response
     * @throws ApiError
     */
    public static workflowList(
        podId: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<WorkflowListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/workflows',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Workflow
     * Create a workflow definition. The graph (`nodes`/`edges`) can be included in this call to create a ready-to-run workflow in one step, or omitted to create a shell and upload the graph later with `workflow.graph.update`.
     * @param podId
     * @param requestBody
     * @returns WorkflowDetailResponse Successful Response
     * @throws ApiError
     */
    public static workflowCreate(
        podId: string,
        requestBody: WorkflowCreateRequest,
    ): CancelablePromise<WorkflowDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/workflows',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Workflow
     * Delete a workflow definition.
     * @param podId
     * @param workflowName
     * @returns void
     * @throws ApiError
     */
    public static workflowDelete(
        podId: string,
        workflowName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/workflows/{workflow_name}',
            path: {
                'pod_id': podId,
                'workflow_name': workflowName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Workflow
     * Get a single workflow definition including graph and start configuration.
     * @param podId
     * @param workflowName
     * @returns WorkflowDetailResponse Successful Response
     * @throws ApiError
     */
    public static workflowGet(
        podId: string,
        workflowName: string,
    ): CancelablePromise<WorkflowDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/workflows/{workflow_name}',
            path: {
                'pod_id': podId,
                'workflow_name': workflowName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Workflow Metadata
     * Update workflow-level metadata such as description and schedule mode. Workflow names are immutable after creation. Use `workflow.graph.update` for nodes and edges.
     * @param podId
     * @param workflowName
     * @param requestBody
     * @returns WorkflowDetailResponse Successful Response
     * @throws ApiError
     */
    public static workflowUpdate(
        podId: string,
        workflowName: string,
        requestBody: WorkflowUpdateRequest,
    ): CancelablePromise<WorkflowDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/workflows/{workflow_name}',
            path: {
                'pod_id': podId,
                'workflow_name': workflowName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Workflow Graph
     * Replace the workflow graph. Agent/function node `input_mapping` entries must use explicit typed bindings. Use `{type: "expression", value: "start.payload.issue.key"}` for context lookups and `{type: "literal", value: "abc"}` for fixed JSON values.
     * @param podId
     * @param workflowName
     * @param requestBody
     * @returns WorkflowDetailResponse Successful Response
     * @throws ApiError
     */
    public static workflowGraphUpdate(
        podId: string,
        workflowName: string,
        requestBody: WorkflowGraphUpdateRequest,
    ): CancelablePromise<WorkflowDetailResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/workflows/{workflow_name}/graph',
            path: {
                'pod_id': podId,
                'workflow_name': workflowName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Workflow Runs
     * List recent runs for a given workflow.
     * @param podId
     * @param workflowName
     * @param limit
     * @param pageToken
     * @returns WorkflowRunListResponse Successful Response
     * @throws ApiError
     */
    public static workflowRunList(
        podId: string,
        workflowName: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<WorkflowRunListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/workflows/{workflow_name}/runs',
            path: {
                'pod_id': podId,
                'workflow_name': workflowName,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Workflow Run
     * Create a new run for this workflow. Takes no request body: if the workflow's entry node is a FORM node the run is created WAITING on it (see `active_wait` in the response) and input is submitted via `workflow.run.form.submit`; otherwise the run executes immediately. Trigger payloads for scheduled/event/datastore starts are supplied by the platform, not through this endpoint.
     * @param podId
     * @param workflowName
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    public static workflowRunCreate(
        podId: string,
        workflowName: string,
    ): CancelablePromise<WorkflowRunResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/workflows/{workflow_name}/runs',
            path: {
                'pod_id': podId,
                'workflow_name': workflowName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Visualize Workflow
     * Render an HTML visualization for debugging workflow graph structure.
     * @param podId
     * @param workflowName
     * @returns string Successful Response
     * @throws ApiError
     */
    public static workflowVisualize(
        podId: string,
        workflowName: string,
    ): CancelablePromise<string> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/workflows/{workflow_name}/visualize',
            path: {
                'pod_id': podId,
                'workflow_name': workflowName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
