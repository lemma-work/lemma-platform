/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactExportResponse } from '../models/ContactExportResponse.js';
import type { ContactListResponse } from '../models/ContactListResponse.js';
import type { ContactResponse } from '../models/ContactResponse.js';
import type { ContactUpdateRequest } from '../models/ContactUpdateRequest.js';
import type { FollowUpRequest } from '../models/FollowUpRequest.js';
import type { FollowUpResponse } from '../models/FollowUpResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class ContactsService {
    /**
     * List Contacts
     * The pod's contacts, newest first.
     * @param podId
     * @param limit
     * @param before
     * @returns ContactListResponse Successful Response
     * @throws ApiError
     */
    public static contactList(
        podId: string,
        limit: number = 50,
        before?: (string | null),
    ): CancelablePromise<ContactListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/contacts',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
                'before': before,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Contact
     * Forget a contact: their rows, handles, conversations and chat sessions.
     *
     * See ``services/forget`` for the order, which is what makes a failure safe
     * to retry.
     * @param podId
     * @param contactId
     * @returns void
     * @throws ApiError
     */
    public static contactDelete(
        podId: string,
        contactId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/contacts/{contact_id}',
            path: {
                'pod_id': podId,
                'contact_id': contactId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Contact
     * @param podId
     * @param contactId
     * @returns ContactResponse Successful Response
     * @throws ApiError
     */
    public static contactGet(
        podId: string,
        contactId: string,
    ): CancelablePromise<ContactResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/contacts/{contact_id}',
            path: {
                'pod_id': podId,
                'contact_id': contactId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Contact
     * @param podId
     * @param contactId
     * @param requestBody
     * @returns ContactResponse Successful Response
     * @throws ApiError
     */
    public static contactUpdate(
        podId: string,
        contactId: string,
        requestBody: ContactUpdateRequest,
    ): CancelablePromise<ContactResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/contacts/{contact_id}',
            path: {
                'pod_id': podId,
                'contact_id': contactId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Contact
     * A contact's handles, what was said with them, and the rows that are theirs.
     *
     * Takes a pod admin, as forgetting does: both answer the person the data is
     * about, not the member reading it.
     * @param podId
     * @param contactId
     * @param cursor
     * @returns ContactExportResponse Successful Response
     * @throws ApiError
     */
    public static contactExport(
        podId: string,
        contactId: string,
        cursor?: (string | null),
    ): CancelablePromise<ContactExportResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/contacts/{contact_id}/export',
            path: {
                'pod_id': podId,
                'contact_id': contactId,
            },
            query: {
                'cursor': cursor,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Follow Up Contact
     * Write to a contact in their most recent conversation, where the channel allows.
     *
     * Refused (409) when they unsubscribed there, when WhatsApp's 24-hour window
     * has closed, or when they have never written to the pod; 429 past the day's
     * follow-ups for this contact; 502 when the platform did not take it, which
     * the conversation then shows as not sent.
     * @param podId
     * @param contactId
     * @param requestBody
     * @returns FollowUpResponse Successful Response
     * @throws ApiError
     */
    public static contactFollowUp(
        podId: string,
        contactId: string,
        requestBody: FollowUpRequest,
    ): CancelablePromise<FollowUpResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/contacts/{contact_id}/messages',
            path: {
                'pod_id': podId,
                'contact_id': contactId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
