/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSurfaceListResponse } from '../models/AgentSurfaceListResponse.js';
import type { AgentSurfaceResponse } from '../models/AgentSurfaceResponse.js';
import type { AvailableSurfaceChannelsResponse } from '../models/AvailableSurfaceChannelsResponse.js';
import type { AvailableSurfacesResponse } from '../models/AvailableSurfacesResponse.js';
import type { SurfaceCreateRequest } from '../models/SurfaceCreateRequest.js';
import type { SurfacePlatformSetupGuide } from '../models/SurfacePlatformSetupGuide.js';
import type { SurfaceSendRequest } from '../models/SurfaceSendRequest.js';
import type { SurfaceSendResponse } from '../models/SurfaceSendResponse.js';
import type { SurfaceSetupResponse } from '../models/SurfaceSetupResponse.js';
import type { SurfaceUpdateRequest } from '../models/SurfaceUpdateRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesService {
    /**
     * List Available Surfaces
     * The connectable-surface catalog: every surface platform with its connector,
     * supported credential modes, and the schema to connect an account. Platform-
     * level (no surface need exist); the pod scopes authorization only.
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
     * Create a surface. ``name`` defaults to the lowercased platform — pass an
     * explicit name to create a second surface of the same platform (e.g. a
     * second bot routed to a different agent).
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
     * Powers notifications from functions/workflows. Reuses the member's existing
     * thread on the surface (bots can't cold-DM), so a 404 means the member has no
     * reachable conversation here yet.
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
     * webhook URL and admin-consent status. For the pre-creation checklist (before
     * any surface exists) use ``GET /pods/{pod_id}/surface-setup/{platform}``.
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
}
