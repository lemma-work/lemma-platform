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

  /**
   * The connectable-surface catalog for a pod: every platform with its
   * connector, supported credential modes, the schema to connect an account,
   * and whether the org can still claim the platform's Lemma-managed
   * bot/number. Platform-level — no surface need exist.
   */
  available(podId: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceAvailable(podId),
    );
  }

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

  /**
   * The Slack app manifest to paste when an org runs its own Slack app, with
   * this deployment's event and OAuth callback URLs already substituted.
   * Served rather than copied from the repo so the URLs match the deployment
   * answering, and the scopes match the code consuming the events.
   *
   * Takes no pod: it describes the deployment, and it is what you need before
   * you have anything to scope it to — the app it creates is what issues the
   * client id that connects the account a surface is built on.
   *
   * `agentName` names the app after one agent, for a bot that answers as that
   * agent alone. One Slack app is one bot user, so this is the only chance to
   * set the name without a person editing it in Slack afterwards.
   */
  slackManifest(agentName?: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackManifest(agentName),
    );
  }
}
