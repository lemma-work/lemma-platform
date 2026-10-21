/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSurfaceListResponse } from '../models/AgentSurfaceListResponse.js';
import type { AgentSurfaceResponse } from '../models/AgentSurfaceResponse.js';
import type { AvailableSurfaceChannelsResponse } from '../models/AvailableSurfaceChannelsResponse.js';
import type { AvailableSurfacesResponse } from '../models/AvailableSurfacesResponse.js';
import type { NoticeRequest } from '../models/NoticeRequest.js';
import type { NoticeResponse } from '../models/NoticeResponse.js';
import type { SlackAppCreateRequest } from '../models/SlackAppCreateRequest.js';
import type { SlackAppCreateResponse } from '../models/SlackAppCreateResponse.js';
import type { SlackAppWorkspaceListResponse } from '../models/SlackAppWorkspaceListResponse.js';
import type { SlackAppWorkspaceResponse } from '../models/SlackAppWorkspaceResponse.js';
import type { SlackConfigurationTokenRequest } from '../models/SlackConfigurationTokenRequest.js';
import type { SlackInstallAvailabilityResponse } from '../models/SlackInstallAvailabilityResponse.js';
import type { SlackInstallRequest } from '../models/SlackInstallRequest.js';
import type { SlackInstallResponse } from '../models/SlackInstallResponse.js';
import type { SlackManagedWorkspaceListResponse } from '../models/SlackManagedWorkspaceListResponse.js';
import type { SlackManagedWorkspaceResponse } from '../models/SlackManagedWorkspaceResponse.js';
import type { SlackManagerBindRequest } from '../models/SlackManagerBindRequest.js';
import type { SlackSignInRequest } from '../models/SlackSignInRequest.js';
import type { SlackSignInResponse } from '../models/SlackSignInResponse.js';
import type { SurfaceCreateRequest } from '../models/SurfaceCreateRequest.js';
import type { SurfacePlatformSetupGuide } from '../models/SurfacePlatformSetupGuide.js';
import type { SurfaceSendRequest } from '../models/SurfaceSendRequest.js';
import type { SurfaceSendResponse } from '../models/SurfaceSendResponse.js';
import type { SurfaceSetupResponse } from '../models/SurfaceSetupResponse.js';
import type { SurfaceUpdateRequest } from '../models/SurfaceUpdateRequest.js';
import type { TelegramManagedBotSetupRequest } from '../models/TelegramManagedBotSetupRequest.js';
import type { TelegramManagedBotSetupResponse } from '../models/TelegramManagedBotSetupResponse.js';
import type { WebWidgetCreatedResponse } from '../models/WebWidgetCreatedResponse.js';
import type { WebWidgetCreateRequest } from '../models/WebWidgetCreateRequest.js';
import type { WebWidgetListResponse } from '../models/WebWidgetListResponse.js';
import type { WebWidgetResponse } from '../models/WebWidgetResponse.js';
import type { WebWidgetSecretResponse } from '../models/WebWidgetSecretResponse.js';
import type { WebWidgetUpdateRequest } from '../models/WebWidgetUpdateRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesService {
    /**
     * List Available Surfaces
     * The connectable-surface catalog: every surface platform with its connector,
     * supported credential modes, the schema to connect an account, and whether this
     * pod's org can still claim the platform's Lemma-managed bot/number. Otherwise
     * platform-level — no surface need exist.
     * @param podId
     * @returns AvailableSurfacesResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceAvailable(
        podId: string,
    ): CancelablePromise<AvailableSurfacesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/available-surfaces',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Send Notice
     * Tell another member of the pod something, in their own channel. The
     * same `Idempotency-Key` from the same member is the same notice; sent with
     * other text, it is refused (422). A member sends at most
     * `NOTICES_PER_MINUTE` a minute (429).
     * @param podId
     * @param member The member to tell: a pod member id, user id, or email address.
     * @param requestBody
     * @param idempotencyKey
     * @returns NoticeResponse Successful Response
     * @throws ApiError
     */
    public static agentNoticeSend(
        podId: string,
        member: string,
        requestBody: NoticeRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<NoticeResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/members/{member}/notices',
            path: {
                'pod_id': podId,
                'member': member,
            },
            headers: {
                'Idempotency-Key': idempotencyKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                404: `Nobody in this pod is that member.`,
                422: `Validation Error`,
                429: `Too many notices this minute; see Retry-After.`,
            },
        });
    }
    /**
     * List Slack App Workspaces
     * The Slack workspaces this pod's organization lets Lemma create apps in.
     * @param podId
     * @returns SlackAppWorkspaceListResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackAppCreationList(
        podId: string,
    ): CancelablePromise<SlackAppWorkspaceListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surface-setup/slack/app-creation',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Save Slack Configuration Token
     * Let Lemma create Slack apps in the workspace this token is for.
     * @param podId
     * @param requestBody
     * @returns SlackAppWorkspaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackAppCreationSave(
        podId: string,
        requestBody: SlackConfigurationTokenRequest,
    ): CancelablePromise<SlackAppWorkspaceResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/surface-setup/slack/app-creation',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Forget Slack Configuration Token
     * Stop Lemma creating apps there. Apps it already made are not touched.
     *
     * Slack offers no way to revoke a configuration token, so it stays valid at
     * Slack: revoke it on api.slack.com/apps as well.
     * @param podId
     * @param teamId
     * @returns void
     * @throws ApiError
     */
    public static agentSurfaceSlackAppCreationForget(
        podId: string,
        teamId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/surface-setup/slack/app-creation/{team_id}',
            path: {
                'pod_id': podId,
                'team_id': teamId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Slack App
     * Create an agent's own Slack app, named after it. What is left is
     * authorising it.
     *
     * One app per agent per workspace: until it is authorised, asking again
     * answers with the same app, and so does the same `Idempotency-Key` from the
     * same member for a day.
     * @param podId
     * @param requestBody
     * @param idempotencyKey
     * @returns SlackAppCreateResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackAppCreate(
        podId: string,
        requestBody: SlackAppCreateRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<SlackAppCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/surface-setup/slack/apps',
            path: {
                'pod_id': podId,
            },
            headers: {
                'Idempotency-Key': idempotencyKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                409: `Not set up, the token needs replacing, or busy.`,
                422: `Validation Error`,
                429: `Slack is rate-limiting app creation.`,
            },
        });
    }
    /**
     * List Slack Managed Workspaces
     * The Slack workspaces Lemma's own app manages for this pod's organization.
     * @param podId
     * @returns SlackManagedWorkspaceListResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackManagerList(
        podId: string,
    ): CancelablePromise<SlackManagedWorkspaceListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surface-setup/slack/manager',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Bind Slack Manager
     * Make Lemma's own app this workspace's manager. The workspace becomes the
     * organization's, and the person who authorised it is known in Slack.
     *
     * Only for a grant this browser finished signed in as the caller: a consent
     * link somebody else completed is refused.
     * @param podId
     * @param requestBody
     * @returns SlackManagedWorkspaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackManagerBind(
        podId: string,
        requestBody: SlackManagerBindRequest,
    ): CancelablePromise<SlackManagedWorkspaceResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/surface-setup/slack/manager',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Surface Setup Guide
     * The static pre-creation checklist for a platform (env/OAuth
     * prerequisites) — works before any surface of this platform exists.
     * @param podId
     * @param platform
     * @returns SurfacePlatformSetupGuide Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSetupGuide(
        podId: string,
        platform: string,
    ): CancelablePromise<SurfacePlatformSetupGuide> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surface-setup/{platform}',
            path: {
                'pod_id': podId,
                'platform': platform,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Surfaces
     * List surfaces in the pod. A pod may have several surfaces of the same
     * ``platform`` (different bots/accounts, one per agent); filter by
     * ``platform`` and/or ``agent_name`` to narrow the results.
     * @param podId
     * @param limit
     * @param pageToken
     * @param platform
     * @param agentName
     * @returns AgentSurfaceListResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceList(
        podId: string,
        limit: number = 100,
        pageToken?: (string | null),
        platform?: (string | null),
        agentName?: (string | null),
    ): CancelablePromise<AgentSurfaceListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
                'platform': platform,
                'agent_name': agentName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Surface
     * Create a surface. ``name`` defaults to the lowercased platform and is the
     * pod-unique handle the API addresses it by. A second surface of the same
     * platform has to belong to a different agent: one agent reaches a platform in
     * one place — one Slack app, one WhatsApp number, one Telegram bot.
     * @param podId
     * @param requestBody
     * @returns AgentSurfaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceCreate(
        podId: string,
        requestBody: SurfaceCreateRequest,
    ): CancelablePromise<AgentSurfaceResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/surfaces',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Surface
     * @param podId
     * @param surfaceName
     * @returns void
     * @throws ApiError
     */
    public static agentSurfaceDelete(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/surfaces/{surface_name}',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Surface
     * @param podId
     * @param surfaceName
     * @returns AgentSurfaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceGet(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<AgentSurfaceResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces/{surface_name}',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Surface
     * Partially update a surface. Only fields present in the request are
     * applied; the surface's platform and name are immutable.
     *
     * Passing ``account_id`` rebinds the surface to a different connected account
     * — the repair when the account it runs on expires, or its owner leaves the
     * pod. It must be an account the caller owns.
     * @param podId
     * @param surfaceName
     * @param requestBody
     * @returns AgentSurfaceResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceUpdate(
        podId: string,
        surfaceName: string,
        requestBody: SurfaceUpdateRequest,
    ): CancelablePromise<AgentSurfaceResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/surfaces/{surface_name}',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Surface Channels
     * List the channels/groups this surface bot can be configured to respond in.
     *
     * Returns an empty list for platforms without an enumerable channel concept
     * (Telegram groups, WhatsApp, email).
     * @param podId
     * @param surfaceName
     * @returns AvailableSurfaceChannelsResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceChannels(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<AvailableSurfaceChannelsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces/{surface_name}/channels',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Send Surface Message
     * Proactively send a message to a pod member on this surface.
     *
     * For functions and workflows that message a member on one platform. Reuses
     * the member's existing thread (bots can't cold-DM), and a 404 carries the
     * reason they could not be reached.
     * @param podId
     * @param surfaceName
     * @param requestBody
     * @returns SurfaceSendResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSend(
        podId: string,
        surfaceName: string,
        requestBody: SurfaceSendRequest,
    ): CancelablePromise<SurfaceSendResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/surfaces/{surface_name}/send',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Surface Setup
     * Live setup state for an existing surface: static platform checklist plus
     * webhook URL and admin-consent status. Readable with ``AGENT_READ``; the org's
     * own shared secrets in it are not. For the pre-creation checklist (before any
     * surface exists) use ``GET /pods/{pod_id}/surface-setup/{platform}``.
     * @param podId
     * @param surfaceName
     * @returns SurfaceSetupResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSetup(
        podId: string,
        surfaceName: string,
    ): CancelablePromise<SurfaceSetupResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/surfaces/{surface_name}/setup',
            path: {
                'pod_id': podId,
                'surface_name': surfaceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Start Telegram Managed Bot Setup
     * @param podId
     * @param requestBody
     * @returns TelegramManagedBotSetupResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceTelegramManagedStart(
        podId: string,
        requestBody: TelegramManagedBotSetupRequest,
    ): CancelablePromise<TelegramManagedBotSetupResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/telegram-bot-setups',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Telegram Managed Bot Setup
     * @param podId
     * @param setupId
     * @returns TelegramManagedBotSetupResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceTelegramManagedGet(
        podId: string,
        setupId: string,
    ): CancelablePromise<TelegramManagedBotSetupResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/telegram-bot-setups/{setup_id}',
            path: {
                'pod_id': podId,
                'setup_id': setupId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Widgets
     * @param podId
     * @returns WebWidgetListResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetList(
        podId: string,
    ): CancelablePromise<WebWidgetListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/web-widgets',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Widget
     * @param podId
     * @param requestBody
     * @returns WebWidgetCreatedResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetCreate(
        podId: string,
        requestBody: WebWidgetCreateRequest,
    ): CancelablePromise<WebWidgetCreatedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/web-widgets',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Widget
     * @param podId
     * @param widgetId
     * @returns void
     * @throws ApiError
     */
    public static agentWebWidgetDelete(
        podId: string,
        widgetId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/web-widgets/{widget_id}',
            path: {
                'pod_id': podId,
                'widget_id': widgetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Widget
     * @param podId
     * @param widgetId
     * @param requestBody
     * @returns WebWidgetResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetUpdate(
        podId: string,
        widgetId: string,
        requestBody: WebWidgetUpdateRequest,
    ): CancelablePromise<WebWidgetResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/web-widgets/{widget_id}',
            path: {
                'pod_id': podId,
                'widget_id': widgetId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reissue Widget
     * Mint a new signing secret. Tokens signed with the old one stop working.
     *
     * So do the sessions they started: a host session is the old secret's word
     * for who somebody is, and a secret is reissued because that word is no
     * longer trusted.
     * @param podId
     * @param widgetId
     * @returns WebWidgetSecretResponse Successful Response
     * @throws ApiError
     */
    public static agentWebWidgetReissue(
        podId: string,
        widgetId: string,
    ): CancelablePromise<WebWidgetSecretResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/web-widgets/{widget_id}/secret',
            path: {
                'pod_id': podId,
                'widget_id': widgetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Slack Install Availability
     * Whether Lemma's own Slack app is set up on this server, so "Add to
     * Slack" can work -- checked before a person with no organization is given
     * one for it.
     * @returns SlackInstallAvailabilityResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackInstallAvailability(): CancelablePromise<SlackInstallAvailabilityResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surface-setup/slack/install',
        });
    }
    /**
     * Start Slack Install Flow
     * Add Lemma to a Slack workspace from "Add to Slack", for this organization.
     *
     * Answers with Slack's consent page for Lemma's own app, asking for its bot
     * alone. Refused before anything is made when Slack isn't set up here, or
     * the caller may not add the organization's installs. When the grant comes
     * back to this same signed-in browser, the workspace becomes the
     * organization's and Lemma's app says hello there; a grant finished anywhere
     * else adopts nothing.
     * @param requestBody
     * @returns SlackInstallResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackInstallStart(
        requestBody: SlackInstallRequest,
    ): CancelablePromise<SlackInstallResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/surface-setup/slack/install',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Slack App Manifest
     * The Slack app manifest to paste when running your own Slack app.
     *
     * Served rather than copied out of the repo so the URLs always match the
     * deployment answering this request, and the scopes always match the code
     * that will consume the events.
     *
     * Signed-in access is the only gate, and that is enough: every value in here
     * is already public — this deployment's URLs and the scopes its own code
     * asks for. It carries no credential and reveals nothing about a pod: the
     * agent name is supplied by the caller and echoed back, never read from one.
     * @param agentName Name the app after this agent, so a bot made for one agent arrives already called by its name. Defaults to Lemma.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackManifest(
        agentName?: (string | null),
    ): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surface-setup/slack/manifest',
            query: {
                'agent_name': agentName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Start Slack Sign In
     * Start Sign in with Slack for the signed-in person, to prove they are the
     * Slack user their App Home's link was for.
     *
     * Connects nothing. The callback connects only when it sees this same Lemma
     * session and Slack names this same Slack user and workspace.
     * @param requestBody
     * @returns SlackSignInResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSlackPersonSignIn(
        requestBody: SlackSignInRequest,
    ): CancelablePromise<SlackSignInResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/surface-setup/slack/people/sign-in',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
