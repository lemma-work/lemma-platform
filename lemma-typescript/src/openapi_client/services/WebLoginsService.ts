/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnswerSignInRequest } from '../models/AnswerSignInRequest.js';
import type { ForgetResponse } from '../models/ForgetResponse.js';
import type { PendingSignInResponse } from '../models/PendingSignInResponse.js';
import type { SignInOutcomeResponse } from '../models/SignInOutcomeResponse.js';
import type { WebLoginListResponse } from '../models/WebLoginListResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WebLoginsService {
    /**
     * Sign your browser out of a site
     * @param origin The site to forget, as an origin or a host.
     * @returns ForgetResponse Successful Response
     * @throws ApiError
     */
    public static webLoginDelete(
        origin: string,
    ): CancelablePromise<ForgetResponse> {
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
     * List the sites your browser is signed in to
     * @param wake Start the computer if it is paused. Off by default so that rendering this list is never what wakes one.
     * @returns WebLoginListResponse Successful Response
     * @throws ApiError
     */
    public static webLoginList(
        wake: boolean = false,
    ): CancelablePromise<WebLoginListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/web-logins',
            query: {
                'wake': wake,
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
     * Let the waiting run carry on, and check the site while they are here.
     *
     * Nothing is captured: the browser keeps its own profile, so finishing a
     * sign-in is the person finishing it. What this does do is look at the site
     * straight afterwards, while they are still present -- so "it still wants a
     * login" is something they hear now rather than the agent discovering it.
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
            url: '/web-logins/sign-ins/{conversation_id}/{tool_call_id}/answer',
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
