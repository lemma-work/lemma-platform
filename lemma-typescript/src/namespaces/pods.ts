import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { PodCreateRequest } from "../openapi_client/models/PodCreateRequest.js";
import type { PodUpdateRequest } from "../openapi_client/models/PodUpdateRequest.js";
import { PodsService } from "../openapi_client/services/PodsService.js";

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
}
