/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceAccessInviteCreateRequest } from '../models/ResourceAccessInviteCreateRequest.js';
import type { ResourceAccessInviteListResponse } from '../models/ResourceAccessInviteListResponse.js';
import type { ResourceAccessInviteResponse } from '../models/ResourceAccessInviteResponse.js';
import type { ResourceType } from '../models/ResourceType.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class PodResourceAccessInvitesService {
    /**
     * List Pending Invites for a Resource
     * @param podId
     * @param resourceType
     * @param resourceId
     * @param resourceName
     * @returns ResourceAccessInviteListResponse Successful Response
     * @throws ApiError
     */
    public static podResourceAccessInviteList(
        podId: string,
        resourceType: ResourceType,
        resourceId?: (string | null),
        resourceName?: (string | null),
    ): CancelablePromise<ResourceAccessInviteListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/resource-access-invites',
            path: {
                'pod_id': podId,
            },
            query: {
                'resource_type': resourceType,
                'resource_id': resourceId,
                'resource_name': resourceName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Invite an Email to a Resource
     * @param podId
     * @param requestBody
     * @returns ResourceAccessInviteResponse Successful Response
     * @throws ApiError
     */
    public static podResourceAccessInviteCreate(
        podId: string,
        requestBody: ResourceAccessInviteCreateRequest,
    ): CancelablePromise<ResourceAccessInviteResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/resource-access-invites',
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
     * Revoke a Pending Invite
     * @param podId
     * @param inviteId
     * @returns void
     * @throws ApiError
     */
    public static podResourceAccessInviteRevoke(
        podId: string,
        inviteId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/resource-access-invites/{invite_id}',
            path: {
                'pod_id': podId,
                'invite_id': inviteId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
