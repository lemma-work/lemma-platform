/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FinishSignInRequest } from '../models/FinishSignInRequest.js';
import type { SignInRequestResponse } from '../models/SignInRequestResponse.js';
import type { WebLoginAuditResponse } from '../models/WebLoginAuditResponse.js';
import type { WebLoginListResponse } from '../models/WebLoginListResponse.js';
import type { WebLoginResponse } from '../models/WebLoginResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WebLoginsService {
    /**
     * Remove a saved site login
     * Forget a site.
     *
     * Removing the row is the whole revocation from Lemma's side. It does **not**
     * sign the person out at the site, and the response says so — a saved session
     * that has been deleted here is still a valid session there until they log out
     * or it expires, and implying otherwise would be the more dangerous lie.
     * @param origin
     * @returns WebLoginResponse Successful Response
     * @throws ApiError
     */
    public static webLoginDelete(
        origin: string,
    ): CancelablePromise<WebLoginResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/web-logins',
            query: {
                'origin': origin,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List saved site logins
     * @returns WebLoginListResponse Successful Response
     * @throws ApiError
     */
    public static webLoginList(): CancelablePromise<WebLoginListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/web-logins',
        });
    }
    /**
     * What has been done with your saved logins
     * @param limit
     * @returns WebLoginAuditResponse Successful Response
     * @throws ApiError
     */
    public static webLoginHistory(
        limit: number = 100,
    ): CancelablePromise<WebLoginAuditResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/web-logins/history',
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * What a sign-in request is asking for
     * @param requestId
     * @returns SignInRequestResponse Successful Response
     * @throws ApiError
     */
    public static webLoginSignInRequestGet(
        requestId: string,
    ): CancelablePromise<SignInRequestResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/web-logins/sign-in-requests/{request_id}',
            path: {
                'request_id': requestId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Say you cannot sign in right now
     * @param requestId
     * @returns SignInRequestResponse Successful Response
     * @throws ApiError
     */
    public static webLoginSignInRequestDecline(
        requestId: string,
    ): CancelablePromise<SignInRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/web-logins/sign-in-requests/{request_id}:decline',
            path: {
                'request_id': requestId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Say you have signed in
     * Capture what the browser now holds, and let the waiting run carry on.
     *
     * The capture happens here, while the person is still present, rather than
     * later in the resumed run — so that "it did not work" is something they can
     * be told at the moment they can still fix it.
     * @param requestId
     * @param requestBody
     * @returns SignInRequestResponse Successful Response
     * @throws ApiError
     */
    public static webLoginSignInRequestFinish(
        requestId: string,
        requestBody: FinishSignInRequest,
    ): CancelablePromise<SignInRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/web-logins/sign-in-requests/{request_id}:finish',
            path: {
                'request_id': requestId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
