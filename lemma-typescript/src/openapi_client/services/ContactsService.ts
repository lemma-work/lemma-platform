/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactListResponse } from '../models/ContactListResponse.js';
import type { ContactResponse } from '../models/ContactResponse.js';
import type { ContactUpdateRequest } from '../models/ContactUpdateRequest.js';
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
     * Forget a contact: their handles go with them.
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
}
