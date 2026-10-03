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
     * the chosen default and a ``conflict`` flag when two of them answer at the
     * same address. Shared-bot platforms also list the pods that could answer.
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
     *
     * Takes either an existing ``surface_id`` or a ``pod_id``; a pod with no
     * surface on the shared bot gets one. A pod whose assistant already answers
     * on the platform through its own connection is refused with 409.
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
