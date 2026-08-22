/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NavigationResponse } from '../models/NavigationResponse.js';
import type { OrganizationCreateRequest } from '../models/OrganizationCreateRequest.js';
import type { OrganizationHomeResponse } from '../models/OrganizationHomeResponse.js';
import type { OrganizationInvitationListResponse } from '../models/OrganizationInvitationListResponse.js';
import type { OrganizationInvitationRequest } from '../models/OrganizationInvitationRequest.js';
import type { OrganizationInvitationResponse } from '../models/OrganizationInvitationResponse.js';
import { OrganizationInvitationStatus } from '../models/OrganizationInvitationStatus.js';
import type { OrganizationListResponse } from '../models/OrganizationListResponse.js';
import type { OrganizationMemberListResponse } from '../models/OrganizationMemberListResponse.js';
import type { OrganizationMemberResponse } from '../models/OrganizationMemberResponse.js';
import type { OrganizationMessageResponse } from '../models/OrganizationMessageResponse.js';
import type { OrganizationResponse } from '../models/OrganizationResponse.js';
import type { OrganizationSlugAvailabilityResponse } from '../models/OrganizationSlugAvailabilityResponse.js';
import type { OrganizationUpdateRequest } from '../models/OrganizationUpdateRequest.js';
import type { UpdateMemberRoleRequest } from '../models/UpdateMemberRoleRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class OrganizationsService {
    /**
     * List My Organizations
     * Get all organizations the current user belongs to
     * @param limit
     * @param pageToken
     * @returns OrganizationListResponse Successful Response
     * @throws ApiError
     */
    public static orgList(
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<OrganizationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations',
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Organization
     * Create a new organization
     * @param requestBody
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    public static orgCreate(
        requestBody: OrganizationCreateRequest,
    ): CancelablePromise<OrganizationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List My Invitations
     * Get all pending invitations for the current user
     * @param status
     * @param limit
     * @param pageToken
     * @returns OrganizationInvitationListResponse Successful Response
     * @throws ApiError
     */
    public static orgInvitationListMine(
        status: OrganizationInvitationStatus = OrganizationInvitationStatus.PENDING,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<OrganizationInvitationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/invitations',
            query: {
                'status': status,
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Revoke Invitation
     * Revoke an organization invitation
     * @param invitationId
     * @returns void
     * @throws ApiError
     */
    public static orgInvitationRevoke(
        invitationId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/organizations/invitations/{invitation_id}',
            path: {
                'invitation_id': invitationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Organization Invitation
     * Get an invitation by id
     * @param invitationId
     * @returns OrganizationInvitationResponse Successful Response
     * @throws ApiError
     */
    public static orgInvitationGet(
        invitationId: string,
    ): CancelablePromise<OrganizationInvitationResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/invitations/{invitation_id}',
            path: {
                'invitation_id': invitationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Accept Invitation
     * Accept an organization invitation
     * @param invitationId
     * @returns OrganizationMessageResponse Successful Response
     * @throws ApiError
     */
    public static orgInvitationAccept(
        invitationId: string,
    ): CancelablePromise<OrganizationMessageResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/invitations/{invitation_id}/accept',
            path: {
                'invitation_id': invitationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Organizations And Their Pods
     * Every organization the current user belongs to, each with the pods they can see in it. Replaces fetching the organization list and then one pod list per organization; the payload stays shallow so it does not grow with the contents of each pod.
     * @returns NavigationResponse Successful Response
     * @throws ApiError
     */
    public static orgNavigation(): CancelablePromise<NavigationResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/navigation',
        });
    }
    /**
     * Check Organization Slug Availability
     * Check whether an organization slug — the handle, and the only unique one of the two — is available. `name_available` is deprecated and always true: display names are labels, and two organizations may share one.
     * @param slug
     * @param name
     * @returns OrganizationSlugAvailabilityResponse Successful Response
     * @throws ApiError
     */
    public static orgSlugAvailability(
        slug: string,
        name?: (string | null),
    ): CancelablePromise<OrganizationSlugAvailabilityResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/slug-availability',
            query: {
                'slug': slug,
                'name': name,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Suggested Organizations
     * Get auto-join organizations matching the current user's email domain
     * @param limit
     * @param pageToken
     * @returns OrganizationListResponse Successful Response
     * @throws ApiError
     */
    public static orgSuggested(
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<OrganizationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/suggested',
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Organization
     * Get organization details
     * @param orgId
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    public static orgGet(
        orgId: string,
    ): CancelablePromise<OrganizationResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{org_id}',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Organization
     * Update an organization's name or join policy (owner only)
     * @param orgId
     * @param requestBody
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    public static orgUpdate(
        orgId: string,
        requestBody: OrganizationUpdateRequest,
    ): CancelablePromise<OrganizationResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/organizations/{org_id}',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Organization Home
     * One organization's landing page: every pod the current user can see, with its apps, its agents, and the user's roles in that pod. Replaces fetching apps and agents per pod. Cached briefly per user.
     * @param orgId
     * @returns OrganizationHomeResponse Successful Response
     * @throws ApiError
     */
    public static orgHome(
        orgId: string,
    ): CancelablePromise<OrganizationHomeResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{org_id}/home',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Organization Invitations
     * Get all pending invitations for an organization
     * @param orgId
     * @param status
     * @param limit
     * @param pageToken
     * @returns OrganizationInvitationListResponse Successful Response
     * @throws ApiError
     */
    public static orgInvitationList(
        orgId: string,
        status: OrganizationInvitationStatus = OrganizationInvitationStatus.PENDING,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<OrganizationInvitationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{org_id}/invitations',
            path: {
                'org_id': orgId,
            },
            query: {
                'status': status,
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Invite Member
     * Invite a user to join the organization
     * @param orgId
     * @param requestBody
     * @returns OrganizationInvitationResponse Successful Response
     * @throws ApiError
     */
    public static orgInvitationInvite(
        orgId: string,
        requestBody: OrganizationInvitationRequest,
    ): CancelablePromise<OrganizationInvitationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{org_id}/invitations',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Join Auto-Join Organization
     * Join an organization when the current user's email domain is allowed to auto-join
     * @param orgId
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    public static orgJoinAutoJoin(
        orgId: string,
    ): CancelablePromise<OrganizationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{org_id}/join',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Organization Members
     * Get all members of an organization
     * @param orgId
     * @param limit
     * @param pageToken
     * @returns OrganizationMemberListResponse Successful Response
     * @throws ApiError
     */
    public static orgMemberList(
        orgId: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<OrganizationMemberListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{org_id}/members',
            path: {
                'org_id': orgId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Member
     * Remove a member from the organization
     * @param orgId
     * @param memberId
     * @returns void
     * @throws ApiError
     */
    public static orgMemberRemove(
        orgId: string,
        memberId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/organizations/{org_id}/members/{member_id}',
            path: {
                'org_id': orgId,
                'member_id': memberId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Member Role
     * Update a member's role in the organization
     * @param orgId
     * @param memberId
     * @param requestBody
     * @returns OrganizationMemberResponse Successful Response
     * @throws ApiError
     */
    public static orgMemberUpdateRole(
        orgId: string,
        memberId: string,
        requestBody: UpdateMemberRoleRequest,
    ): CancelablePromise<OrganizationMemberResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/organizations/{org_id}/members/{member_id}/role',
            path: {
                'org_id': orgId,
                'member_id': memberId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
