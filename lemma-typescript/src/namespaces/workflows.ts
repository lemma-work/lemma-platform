import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { WorkflowCreateRequest } from "../openapi_client/models/WorkflowCreateRequest.js";
import type { WorkflowGraphUpdateRequest } from "../openapi_client/models/WorkflowGraphUpdateRequest.js";
import type { WorkflowRunFormSubmitRequest } from "../openapi_client/models/WorkflowRunFormSubmitRequest.js";
import type { WorkflowRunStatus } from "../openapi_client/models/WorkflowRunStatus.js";
import type { WorkflowUpdateRequest } from "../openapi_client/models/WorkflowUpdateRequest.js";
import { WorkflowsService } from "../openapi_client/services/WorkflowsService.js";

export class WorkflowsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly http: HttpClient,
    private readonly podId: () => string,
  ) {}

  list(options: { limit?: number; pageToken?: string } = {}) {
    return this.client.request(() => WorkflowsService.workflowList(this.podId(), options.limit ?? 100, options.pageToken));
  }

  create(payload: WorkflowCreateRequest) {
    return this.client.request(() => WorkflowsService.workflowCreate(this.podId(), payload));
  }

  get(workflowName: string) {
    return this.client.request(() => WorkflowsService.workflowGet(this.podId(), workflowName));
  }

  update(workflowName: string, payload: WorkflowUpdateRequest) {
    return this.client.request(() => WorkflowsService.workflowUpdate(this.podId(), workflowName, payload));
  }

  delete(workflowName: string) {
    return this.client.request(() => WorkflowsService.workflowDelete(this.podId(), workflowName));
  }

  visualize(workflowName: string) {
    return this.client.request(() => WorkflowsService.workflowVisualize(this.podId(), workflowName));
  }

  readonly graph = {
    update: (workflowName: string, graph: WorkflowGraphUpdateRequest) =>
      this.client.request(() => WorkflowsService.workflowGraphUpdate(this.podId(), workflowName, graph)),
  };

  readonly runs = {
    /**
     * Create a run. Runs take no inputs: when the workflow starts with a
     * form, the returned run is WAITING with `active_wait` describing the
     * form to render and submit via `runs.submitForm`.
     */
    create: (workflowName: string) =>
      this.client.request(() => WorkflowsService.workflowRunCreate(this.podId(), workflowName)),

    list: (workflowName: string, options: { limit?: number; pageToken?: string } = {}) =>
      this.client.request(() =>
        WorkflowsService.workflowRunList(this.podId(), workflowName, options.limit ?? 100, options.pageToken),
      ),

    /**
     * Runs across every workflow in the pod, newest first. Filter by
     * `status` (any of) and `workflowId`. Each run carries its `title` and,
     * while it has one, its active wait as `waiting_on`.
     */
    listInPod: (
      options: { status?: WorkflowRunStatus[]; workflowId?: string; limit?: number; pageToken?: string } = {},
    ) =>
      this.client.request(() =>
        WorkflowsService.workflowRunListForPod(
          this.podId(),
          options.limit ?? 50,
          options.status,
          options.workflowId,
          options.pageToken,
        ),
      ),

    waitingAssignedToMe: (options: { limit?: number; pageToken?: string } = {}) =>
      this.client.request(() =>
        WorkflowsService.workflowRunWaitingAssignedToMe(this.podId(), options.limit ?? 100, options.pageToken),
      ),

    get: (runId: string, podId = this.podId()) =>
      this.client.request(() => WorkflowsService.workflowRunGet(podId, runId)),

    /** Submit the form a run is waiting on. node_id must match active_wait.node_id. */
    submitForm: (runId: string, payload: WorkflowRunFormSubmitRequest, podId = this.podId()) =>
      this.client.request(() => WorkflowsService.workflowRunFormSubmit(podId, runId, payload)),

    visualize: (runId: string, podId = this.podId()) =>
      this.client.request(() => WorkflowsService.workflowRunVisualize(podId, runId)),

    cancel: (runId: string, podId = this.podId()) =>
      this.client.request(() => WorkflowsService.workflowRunCancel(podId, runId)),
  };
}
