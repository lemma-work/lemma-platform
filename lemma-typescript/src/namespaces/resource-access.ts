import type { GeneratedClientAdapter } from "../generated.js";
import type { ResourceAccessGrantRequest } from "../openapi_client/models/ResourceAccessGrantRequest.js";
import type { ResourceType } from "../openapi_client/models/ResourceType.js";
import { PodResourceAccessService } from "../openapi_client/services/PodResourceAccessService.js";
import { PodResourcePreviewService } from "../openapi_client/services/PodResourcePreviewService.js";
import { PodResourceAccessRequestsService } from "../openapi_client/services/PodResourceAccessRequestsService.js";
import type { ResourceAccessRequestCreateRequest } from "../openapi_client/models/ResourceAccessRequestCreateRequest.js";
import { PodResourceAccessInvitesService } from "../openapi_client/services/PodResourceAccessInvitesService.js";
import type { ResourceAccessInviteCreateRequest } from "../openapi_client/models/ResourceAccessInviteCreateRequest.js";

export class ResourceAccessNamespace {
  constructor(private readonly client: GeneratedClientAdapter, private readonly podId: () => string) {}

  /**
   * Ask whether a shared resource is readable, and what it is.
   *
   * The one call here that does not need pod membership — it is what a link
   * recipient asks before anything is rendered. Rejects with 404 both when the
   * resource does not exist and when it is not theirs to see, so it cannot be
   * used to discover what a pod contains.
   */
  preview(
    resourceType: ResourceType | string,
    target: { id?: string; name?: string },
    podId?: string,
  ) {
    return this.client.request(() =>
      PodResourcePreviewService.podResourcePreview(
        podId ?? this.podId(),
        resourceType as ResourceType,
        target.name,
        target.id,
      ),
    );
  }

  /**
   * Ask for one resource rather than for the pod.
   *
   * Idempotent: asking again returns the request already pending, so a guest
   * refreshing the page does not queue duplicates for someone to read.
   */
  requestAccess(
    payload: ResourceAccessRequestCreateRequest,
    podId?: string,
  ) {
    return this.client.request(() =>
      PodResourceAccessRequestsService.podResourceAccessRequestCreate(
        podId ?? this.podId(),
        payload,
      ),
    );
  }

  /**
   * Share with an address that may not have an account yet.
   *
   * Held as an invite and redeemed into a real grant once that address is
   * verified — grants key on a user id, which a stranger does not have.
   */
  requestInvite(payload: ResourceAccessInviteCreateRequest, podId?: string) {
    return this.client.request(() =>
      PodResourceAccessInvitesService.podResourceAccessInviteCreate(
        podId ?? this.podId(),
        payload,
      ),
    );
  }

  /** The caller's own pending request for a resource, or null. */
  myAccessRequest(
    resourceType: ResourceType | string,
    resourceId: string,
    podId?: string,
  ) {
    return this.client.request(() =>
      PodResourceAccessRequestsService.podResourceAccessRequestMe(
        podId ?? this.podId(),
        resourceType as ResourceType,
        resourceId,
      ),
    );
  }

  listAccessRequests(podId?: string) {
    return this.client.request(() =>
      PodResourceAccessRequestsService.podResourceAccessRequestList(podId ?? this.podId()),
    );
  }

  approveAccessRequest(requestId: string, podId?: string) {
    return this.client.request(() =>
      PodResourceAccessRequestsService.podResourceAccessRequestApprove(
        podId ?? this.podId(),
        requestId,
      ),
    );
  }

  rejectAccessRequest(requestId: string, podId?: string) {
    return this.client.request(() =>
      PodResourceAccessRequestsService.podResourceAccessRequestReject(
        podId ?? this.podId(),
        requestId,
      ),
    );
  }

  get(resourceType: ResourceType | string, resourceName: string, podId?: string) {
    return this.client.request(() =>
      PodResourceAccessService.podResourceAccessGet(podId ?? this.podId(), resourceType as ResourceType, resourceName),
    );
  }

  replaceGrant(
    resourceType: ResourceType | string,
    resourceName: string,
    granteeType: string,
    granteeId: string,
    payload: ResourceAccessGrantRequest,
    podId?: string,
  ) {
    return this.client.request(() =>
      PodResourceAccessService.podResourceAccessGrantReplace(
        podId ?? this.podId(),
        resourceType as ResourceType,
        resourceName,
        granteeType,
        granteeId,
        payload,
      ),
    );
  }

  deleteGrant(
    resourceType: ResourceType | string,
    resourceName: string,
    granteeType: string,
    granteeId: string,
    podId?: string,
  ) {
    return this.client.request(() =>
      PodResourceAccessService.podResourceAccessGrantDelete(
        podId ?? this.podId(),
        resourceType as ResourceType,
        resourceName,
        granteeType,
        granteeId,
      ),
    );
  }
}
