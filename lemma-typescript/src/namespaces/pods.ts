import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { OutcomeInput } from "../openapi_client/models/OutcomeInput.js";
import type { PodCreateRequest } from "../openapi_client/models/PodCreateRequest.js";
import type { PodUpdateRequest } from "../openapi_client/models/PodUpdateRequest.js";
import type { StartLemmaRequest } from "../openapi_client/models/StartLemmaRequest.js";
import { PodBundleService } from "../openapi_client/services/PodBundleService.js";
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
   * The templates a Lemma can start from, one card each: what it owns and how
   * that is counted, the kind of work it does (`function`), its face, where
   * its work usually happens, what it brings and the first lines of its plan.
   */
  templates() {
    return this.client.request(() => PodBundleService.podBundleTemplates());
  }

  /**
   * Start a Lemma: a pod that owns something, from a template (`template`,
   * with `due` for a finish line) or from your own words (`outcome` and
   * `name`). Answers the pod, the setup chat its agent sets itself up in
   * (`conversation_id`), and for a template the import bringing its tables,
   * skills and schedules (`import_id`).
   */
  start(payload: StartLemmaRequest) {
    return this.client.request(() => PodBundleService.podStart(payload));
  }

  /** What a pod owns, and where it stands against it. */
  readonly outcome = {
    /** The number now, read the same for every member. */
    standing: (podId: string) =>
      this.client.request(() => QueryService.outcomeStanding(podId)),

    /** The last `weeks` weeks, each on its last reading; null where unread. */
    history: (podId: string, weeks = 8) =>
      this.client.request(() => QueryService.outcomeHistory(podId, weeks)),

    /** Set what the pod owns. A new or changed measure is read once first. */
    set: (podId: string, payload: OutcomeInput) =>
      this.client.request(() => PodsService.outcomeSet(podId, payload)),

    /** Let what the pod owns go. */
    clear: (podId: string) =>
      this.client.request(() => PodsService.outcomeClear(podId)),
  };
}
