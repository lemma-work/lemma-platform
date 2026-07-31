/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SetDefaultSurfaceRequest } from '../models/SetDefaultSurfaceRequest.js';
import type { UserSurfacesResponse } from '../models/UserSurfacesResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesMeService {
    /**
     * List My Surfaces
     * Every surface across the current user's pods, grouped by platform, with
     * the chosen default and a ``conflict`` flag when more than one could answer.
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
}
