/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WebhooksService {
    /**
     * Handle Webhook
     * Receive webhooks from various sources (slack, composio, jira, email, etc.)
     * @param source
     * @returns any Successful Response
     * @throws ApiError
     */
    public static webhookHandle(
        source: string,
    ): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/webhooks/{source}',
            path: {
                'source': source,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Verify Webhook
     * Webhook verification endpoint for platforms that require it
     * @param source
     * @returns any Successful Response
     * @throws ApiError
     */
    public static webhookVerify(
        source: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/webhooks/{source}/verify',
            path: {
                'source': source,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
