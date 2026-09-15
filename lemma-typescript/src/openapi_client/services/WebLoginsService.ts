/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnswerSignInRequest } from '../models/AnswerSignInRequest.js';
import type { PendingSignInResponse } from '../models/PendingSignInResponse.js';
import type { SignInOutcomeResponse } from '../models/SignInOutcomeResponse.js';
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
     * What a sign-in link is asking for
     * @param conversationId
     * @param toolCallId
     * @returns PendingSignInResponse Successful Response
     * @throws ApiError
     */
    public static webLoginSignInPending(
        conversationId: string,
        toolCallId: string,
    ): CancelablePromise<PendingSignInResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/web-logins/sign-ins/{conversation_id}/{tool_call_id}',
            path: {
                'conversation_id': conversationId,
                'tool_call_id': toolCallId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Say whether you signed in
     * Capture what the browser now holds, and let the waiting run carry on.
     *
     * The capture happens here, while the person is still present, rather than
     * later in the resumed run -- so that "it did not work" is something they can
     * be told at the moment they can still fix it.
     *
     * One route for both answers because it is one answer. Two routes meant two
     * status writes with two different guards, and the weaker one let a stale tab
     * overwrite a decision the agent had already been given.
     * @param conversationId
     * @param toolCallId
     * @param requestBody
     * @returns SignInOutcomeResponse Successful Response
     * @throws ApiError
     */
    public static webLoginSignInAnswer(
        conversationId: string,
        toolCallId: string,
        requestBody: AnswerSignInRequest,
    ): CancelablePromise<SignInOutcomeResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/web-logins/sign-ins/{conversation_id}/{tool_call_id}:answer',
            path: {
                'conversation_id': conversationId,
                'tool_call_id': toolCallId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
