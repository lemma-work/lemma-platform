import type { GeneratedClientAdapter } from "../generated.js";
import type { ResourceAccessGrantRequest } from "../openapi_client/models/ResourceAccessGrantRequest.js";
import type { ResourceType } from "../openapi_client/models/ResourceType.js";
import { PodResourceAccessService } from "../openapi_client/services/PodResourceAccessService.js";
import { PodResourcePreviewService } from "../openapi_client/services/PodResourcePreviewService.js";

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
