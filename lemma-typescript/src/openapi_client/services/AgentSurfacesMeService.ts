/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SetDefaultSurfaceRequest } from '../models/SetDefaultSurfaceRequest.js';
import type { TelegramAccountsResponse } from '../models/TelegramAccountsResponse.js';
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
     * List My Telegram Accounts
     * The Telegram accounts the current user proved from a Lemma session, still
     * theirs: every bot knows them as this user.
     * @returns TelegramAccountsResponse Successful Response
     * @throws ApiError
     */
    public static agentSurfaceListMyTelegramAccounts(): CancelablePromise<TelegramAccountsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surfaces/me/telegram-accounts',
        });
    }
    /**
     * Unlink My Telegram Account
     * Unlink one of them: from its next message the account is a stranger's.
     * @param accountId
     * @returns void
     * @throws ApiError
     */
    public static agentSurfaceUnlinkMyTelegramAccount(
        accountId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/surfaces/me/telegram-accounts/{account_id}',
            path: {
                'account_id': accountId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
