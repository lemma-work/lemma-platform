import type { GeneratedClientAdapter } from "../generated.js";
import type { SurfaceCreateRequest } from "../openapi_client/models/SurfaceCreateRequest.js";
import type { SurfaceUpdateRequest } from "../openapi_client/models/SurfaceUpdateRequest.js";
import type { SurfaceSendRequest } from "../openapi_client/models/SurfaceSendRequest.js";
import type { TelegramManagedBotSetupRequest } from "../openapi_client/models/TelegramManagedBotSetupRequest.js";
import type { SlackAppCreateRequest } from "../openapi_client/models/SlackAppCreateRequest.js";
import type { SlackConfigurationTokenRequest } from "../openapi_client/models/SlackConfigurationTokenRequest.js";
import type { SlackManagerBindRequest } from "../openapi_client/models/SlackManagerBindRequest.js";
import type { SlackSignInRequest } from "../openapi_client/models/SlackSignInRequest.js";
import type { SlackInstallRequest } from "../openapi_client/models/SlackInstallRequest.js";
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

  /**
   * The Slack workspaces this pod's organization lets Lemma create apps in --
   * one per saved app configuration token. Carries no token. `can_manage` says
   * whether the caller may save tokens and create apps (an organization owner
   * or editor); others are shown no workspace.
   */
  slackAppWorkspaces(podId: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackAppCreationList(podId),
    );
  }

  /**
   * Let Lemma create Slack apps in a workspace, from the refresh token of an
   * app configuration token (api.slack.com/apps → Your App Configuration
   * Tokens). Lemma rotates it at once, which proves it works and names the
   * workspace. Needs the right to manage the org's connector installs.
   */
  saveSlackConfigurationToken(podId: string, request: SlackConfigurationTokenRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackAppCreationSave(podId, request),
    );
  }

  /**
   * Stop Lemma creating apps in that workspace. Apps it made are untouched,
   * and the token stays valid at Slack until revoked there.
   */
  forgetSlackConfigurationToken(podId: string, teamId: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackAppCreationForget(podId, teamId),
    );
  }

  /**
   * Create an agent's own Slack app (the pod's own agent unless `agent_id` is
   * given), named after it, and register its credentials as the org's own
   * install. Authorise that install (`auth_config_id`) to finish. Asking again
   * for the same agent and workspace answers with the same app until it is
   * authorised, and so does the same `idempotencyKey` for a day.
   */
  createSlackApp(podId: string, request: SlackAppCreateRequest, idempotencyKey?: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackAppCreate(podId, request, idempotencyKey),
    );
  }

  /**
   * The Slack workspaces Lemma's own app manages for this pod's organization.
   * Lemma's app answers nobody: it is where a workspace sees its teammates,
   * adds one, and hands one a channel.
   */
  slackManagedWorkspaces(podId: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackManagerList(podId),
    );
  }

  /**
   * Make Lemma's own app the manager of the workspace an account authorised it
   * in. The workspace becomes the organization's, and the person who
   * authorised it is known in Slack from then on. Only for a grant this
   * browser finished signed in as the caller.
   */
  bindSlackManager(podId: string, request: SlackManagerBindRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackManagerBind(podId, request),
    );
  }

  /**
   * Start Sign in with Slack for the signed-in person, to prove they are the
   * Slack user their App Home's "Connect your Lemma account" link was for.
   * Returns Slack's sign-in page; the callback connects only when it sees this
   * same Lemma session and Slack names that same Slack user and workspace.
   */
  startSlackSignIn(request: SlackSignInRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackPersonSignIn(request),
    );
  }

  /**
   * Whether Lemma's own Slack app is set up on this server, so "Add to Slack"
   * can work. Ask before giving anybody an organization for it.
   */
  slackInstallAvailability() {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackInstallAvailability(),
    );
  }

  /**
   * Add Lemma's own Slack app to a workspace for an organization, from "Add
   * to Slack" rather than from a pod. Answers with Slack's consent page (bot
   * scopes only), refused before anything is made when Slack isn't set up
   * here or the caller may not add the organization's installs. A grant
   * finished in a browser not signed in as the caller adopts nothing.
   */
  startSlackInstall(request: SlackInstallRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentSurfaceSlackInstallStart(request),
    );
  }
}
