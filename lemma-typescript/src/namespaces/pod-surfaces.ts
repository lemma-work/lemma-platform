import type { GeneratedClientAdapter } from "../generated.js";
import type { SurfaceCreateRequest } from "../openapi_client/models/SurfaceCreateRequest.js";
import type { SurfaceUpdateRequest } from "../openapi_client/models/SurfaceUpdateRequest.js";
import type { SurfaceSendRequest } from "../openapi_client/models/SurfaceSendRequest.js";
import type { TelegramManagedBotSetupRequest } from "../openapi_client/models/TelegramManagedBotSetupRequest.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";

/**
 * Agent surfaces, addressed by `name` (a surface is unique per pod+name). A pod
 * may hold several surfaces of the same platform — different bots/accounts, one
 * per agent — so writes are keyed by the stable surface name rather than the
 * platform.
 *
 * `create` provisions a surface (its `name` defaults to the lowercased platform;
 * pass an explicit name for a second surface of the same platform). `update`
 * applies a partial patch — config/channel edits, account and credential
 * changes, and enable/disable via `is_enabled`; the platform and name are
 * immutable. `delete` removes the surface and frees its account for reuse.
 * `send` delivers a proactive message to a pod member over an existing thread.
 * `setup` merges live readiness, admin-consent, and the platform checklist into
 * one read; `setupGuide` returns the same checklist before any surface exists.
 */
export class PodSurfacesNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  list(
    podId: string,
    options: {
      limit?: number;
      pageToken?: string;
      cursor?: string;
      platform?: string;
      agentName?: string;
    } = {},
  ) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceList(
        podId,
        options.limit ?? 100,
        options.pageToken ?? options.cursor,
        options.platform,
        options.agentName,
      ),
    );
  }

  create(podId: string, payload: SurfaceCreateRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceCreate(podId, payload),
    );
  }

  startTelegramBotSetup(
    podId: string,
    payload: TelegramManagedBotSetupRequest,
  ) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceTelegramManagedStart(podId, payload),
    );
  }

  getTelegramBotSetup(podId: string, setupId: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceTelegramManagedGet(podId, setupId),
    );
  }

  update(podId: string, surfaceName: string, payload: SurfaceUpdateRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceUpdate(podId, surfaceName, payload),
    );
  }

  get(podId: string, surfaceName: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceGet(podId, surfaceName),
    );
  }

  delete(podId: string, surfaceName: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceDelete(podId, surfaceName),
    );
  }

  send(podId: string, surfaceName: string, payload: SurfaceSendRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSend(podId, surfaceName, payload),
    );
  }

  setup(podId: string, surfaceName: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSetup(podId, surfaceName),
    );
  }

  /** Pre-creation platform checklist — works before any surface exists. */
  setupGuide(podId: string, platform: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSetupGuide(podId, platform),
    );
  }

  channels(podId: string, surfaceName: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceChannels(podId, surfaceName),
    );
  }
}
