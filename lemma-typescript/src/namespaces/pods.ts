import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { OutcomeInput } from "../openapi_client/models/OutcomeInput.js";
import type { PodCreateRequest } from "../openapi_client/models/PodCreateRequest.js";
import type { PodUpdateRequest } from "../openapi_client/models/PodUpdateRequest.js";
import { PodsService } from "../openapi_client/services/PodsService.js";
import { QueryService } from "../openapi_client/services/QueryService.js";

export class PodsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly http: HttpClient,
  ) {}

  list(options: { organizationId?: string; limit?: number; pageToken?: string; cursor?: string } = {}) {
    if (options.organizationId) {
      return this.listByOrganization(options.organizationId, {
        limit: options.limit,
        pageToken: options.pageToken ?? options.cursor,
      });
    }

    throw new Error("organizationId is required for pods.list(). Use listByOrganization(organizationId).");
  }

  listByOrganization(
    organizationId: string,
    options: { limit?: number; pageToken?: string; cursor?: string } = {},
  ) {
    return this.client.request(() =>
      PodsService.podList(organizationId, options.limit ?? 100, options.pageToken ?? options.cursor),
    );
  }

  get(podId: string) {
    return this.client.request(() => PodsService.podGet(podId));
  }

  create(payload: PodCreateRequest) {
    return this.client.request(() => PodsService.podCreate(payload));
  }

  update(podId: string, payload: PodUpdateRequest) {
    return this.client.request(() => PodsService.podUpdate(podId, payload));
  }

  delete(podId: string) {
    return this.client.request(() => PodsService.podDelete(podId));
  }

  /**
   * Self-join a pod when its join policy (ORG_MEMBERS / PUBLIC) allows it.
   * For invite-only pods, use `podJoinRequests.create(podId)` instead.
   */
  join(podId: string) {
    return this.client.request(() => PodsService.podJoin(podId));
  }

  /**
   * What the pod owns and where its measure stands now. `outcome` is null when
   * it owns nothing; a measure that cannot be read comes back as `problem`.
   */
  outcomeStanding(podId: string) {
    return this.client.request(() => QueryService.outcomeStanding(podId));
  }

  /** The measure's weekly readings, the most recent `weeks` of them (default 8). */
  outcomeHistory(podId: string, options: { weeks?: number } = {}) {
    return this.client.request(() => QueryService.outcomeHistory(podId, options.weeks ?? 8));
  }

  /**
   * Set what the pod owns, replacing what it had. A new or changed measure is
   * read once first, and refused when the caller cannot read every row it counts.
   */
  setOutcome(podId: string, input: OutcomeInput) {
    return this.client.request(() => PodsService.outcomeSet(podId, input));
  }

  /** Let go of what the pod owns. */
  clearOutcome(podId: string) {
    return this.client.request(() => PodsService.outcomeClear(podId));
  }
}
