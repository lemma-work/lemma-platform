/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesIngressService {
    /**
     * Teams Admin Consent Callback
     * @param tenant
     * @param adminConsent
     * @param state
     * @param error
     * @param errorDescription
     * @returns any Successful Response
     * @throws ApiError
     */
    public static agentSurfaceTeamsAdminConsentCallback(
        tenant?: (string | null),
        adminConsent?: (string | null),
        state?: (string | null),
        error?: (string | null),
        errorDescription?: (string | null),
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surfaces/teams/admin-consent/callback',
            query: {
                'tenant': tenant,
                'admin_consent': adminConsent,
                'state': state,
                'error': error,
                'error_description': errorDescription,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Verify surface webhook using the platform callback URL
     * Webhook verification endpoint for platforms that require it.
     * @param platform
     * @returns any Successful Response
     * @throws ApiError
     */
    public static surfaceWebhookVerify(
        platform: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surfaces/webhooks/{platform}',
            path: {
                'platform': platform,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Handle platform-level surface webhook
     * Handle platform-level webhook callbacks.
     * @param platform
     * @returns any Successful Response
     * @throws ApiError
     */
    public static surfaceWebhookHandlePlatform(
        platform: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/surfaces/webhooks/{platform}',
            path: {
                'platform': platform,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Verify surface webhook using a surface-level callback URL
     * Webhook verification endpoint for platforms that require it.
     *
     * WhatsApp surfaces bound to a connector account are verified against that
     * account's own ``verify_token`` (never the system-wide one) so each
     * customer's WhatsApp Business webhook config only has to match their own
     * credentials.
     * @param surfaceId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static surfaceWebhookVerifySurface(
        surfaceId: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/surfaces/{surface_id}/webhook',
            path: {
                'surface_id': surfaceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Handle surface-level webhook
     * Handle webhooks addressed to one concrete surface.
     * @param surfaceId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static surfaceWebhookHandleSurface(
        surfaceId: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/surfaces/{surface_id}/webhook',
            path: {
                'surface_id': surfaceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
