/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EmailDeliveryStatusResponse } from '../models/EmailDeliveryStatusResponse.js';
import type { EmailDeliveryTestResponse } from '../models/EmailDeliveryTestResponse.js';
import type { FirstWorkspaceRequest } from '../models/FirstWorkspaceRequest.js';
import type { FirstWorkspaceResponse } from '../models/FirstWorkspaceResponse.js';
import type { UserProfileRequest } from '../models/UserProfileRequest.js';
import type { UserResponse } from '../models/UserResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class UsersService {
    /**
     * Get Current User
     * Get the current authenticated user's information
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static userCurrentGet(): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/users/me',
        });
    }
    /**
     * Get Email Delivery Status
     * Whether this local installation can send email. Local installations only; 404 elsewhere.
     * @returns EmailDeliveryStatusResponse Successful Response
     * @throws ApiError
     */
    public static userEmailDeliveryGet(): CancelablePromise<EmailDeliveryStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/users/me/email-delivery',
        });
    }
    /**
     * Send A Test Email
     * Send a short test email to the signed-in user's own address with this installation's email settings. Local installations only; 404 elsewhere.
     * @returns EmailDeliveryTestResponse Successful Response
     * @throws ApiError
     */
    public static userEmailDeliveryTest(): CancelablePromise<EmailDeliveryTestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/me/email-delivery/test',
            errors: {
                429: `Too many test emails; see Retry-After`,
            },
        });
    }
    /**
     * Ensure The Current User Has A Workspace
     * Select an eligible organization and idempotently ensure the current user has a private pod and assistant.
     * @param requestBody
     * @returns FirstWorkspaceResponse Successful Response
     * @throws ApiError
     */
    public static usersEnsureFirstWorkspace(
        requestBody?: (FirstWorkspaceRequest | null),
    ): CancelablePromise<FirstWorkspaceResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/me/first-workspace',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get User Profile
     * Get the current user's profile
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static userProfileGet(): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/users/me/profile',
        });
    }
    /**
     * Create or Update Profile
     * Create or update the current user's profile
     * @param requestBody
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static userProfileUpsert(
        requestBody: UserProfileRequest,
    ): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/me/profile',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
