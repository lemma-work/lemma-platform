import type { GeneratedClientAdapter } from "../generated.js";
import type { SetDefaultSurfaceRequest } from "../openapi_client/models/SetDefaultSurfaceRequest.js";
import type { TelegramLinkRequest } from "../openapi_client/models/TelegramLinkRequest.js";
import { AgentSurfacesMeService } from "../openapi_client/services/AgentSurfacesMeService.js";

/**
 * The caller's own surfaces, across every pod they belong to, grouped by
 * platform. When a user is reachable through more than one surface on the same
 * platform (e.g. a shared bot spanning orgs), the group is flagged as a
 * conflict and `setDefault` picks which surface answers them.
 */
export class UserSurfacesNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** List my surfaces across all pods, grouped by platform. */
  list() {
    return this.client.request(() => AgentSurfacesMeService.agentSurfaceListMine());
  }

  /** Choose which surface answers me on a platform when several could. */
  setDefault(payload: SetDefaultSurfaceRequest) {
    return this.client.request(() =>
      AgentSurfacesMeService.agentSurfaceSetMyDefault(payload),
    );
  }

  /** The shared Telegram bot, and the pods a chat linked to me could answer from. */
  telegramLinkOptions() {
    return this.client.request(() => AgentSurfacesMeService.agentSurfaceTelegramLinkOptions());
  }

  /** A one-time `t.me` link that connects the Telegram chat opening it to me. */
  createTelegramLink(payload: TelegramLinkRequest = {}) {
    return this.client.request(() =>
      AgentSurfacesMeService.agentSurfaceCreateTelegramLink(payload),
    );
  }
}
