/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SetDefaultSurfaceRequest } from '../models/SetDefaultSurfaceRequest.js';
import type { TelegramLinkOptionsResponse } from '../models/TelegramLinkOptionsResponse.js';
import type { TelegramLinkRequest } from '../models/TelegramLinkRequest.js';
import type { TelegramLinkResponse } from '../models/TelegramLinkResponse.js';
import type { UserSurfacesResponse } from '../models/UserSurfacesResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesMeService {
    /**
     * List My Surfaces
     * Every surface across the current user's pods, grouped by platform, with
     * the chosen default and a ``conflict`` flag when two of them answer at the
     * same address.
     * @returns UserSurfacesResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceListMine(): CancelablePromise<UserSurfacesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surfaces/me',
        });
    }
    /**
     * Set My Default Surface
     * Choose which surface answers the current user for a platform when several
     * could (e.g. a shared system bot spanning pods in different orgs).
     * @param requestBody
     * @returns UserSurfacesResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceSetMyDefault(
        requestBody: SetDefaultSurfaceRequest,
    ): CancelablePromise<UserSurfacesResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/surfaces/me/default',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get My Telegram Link Options
     * The shared Telegram bot's username and the pods a chat with it could
     * answer from, for offering a link before minting one. 409 when this
     * deployment has no working shared Telegram bot.
     * @returns TelegramLinkOptionsResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceTelegramLinkOptions(): CancelablePromise<TelegramLinkOptionsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surfaces/me/telegram-link',
        });
    }
    /**
     * Create My Telegram Link
     * Mint a one-time ``t.me`` link that connects the Telegram chat opening it
     * to the current user, answered by ``pod_id``'s agent (or the suggested pod
     * when omitted). Expires after ten minutes and works once. 403 for a pod the
     * user cannot attach a chat to; 409 when there is no shared Telegram bot.
     * @param requestBody
     * @returns TelegramLinkResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceCreateTelegramLink(
        requestBody: TelegramLinkRequest,
    ): CancelablePromise<TelegramLinkResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/surfaces/me/telegram-link',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
