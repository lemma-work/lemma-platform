/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BrowserStatusResponse } from '../models/BrowserStatusResponse.js';
import type { CurrentPageUrlResponse } from '../models/CurrentPageUrlResponse.js';
import type { DisplaySizeRequest } from '../models/DisplaySizeRequest.js';
import type { DisplaySizeResponse } from '../models/DisplaySizeResponse.js';
import type { WorkspaceAppAccessRequest } from '../models/WorkspaceAppAccessRequest.js';
import type { WorkspaceAppAccessResponse } from '../models/WorkspaceAppAccessResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WorkspaceAppsService {
    /**
     * Create workspace browser access URL
     * @param requestBody
     * @returns WorkspaceAppAccessResponse Successful Response
     * @throws ApiError
     */
    public static workspaceBrowserAccess(
        requestBody: WorkspaceAppAccessRequest,
    ): CancelablePromise<WorkspaceAppAccessResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/workspace/apps/browser/access',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * What page a sign-in's browser is actually showing
     * Polled by the sign-in page while its VNC pane is open.
     *
     * VNC is pixels, not events -- it carries no navigation signal the way the
     * JSON stream this replaced did with its `url` message on every
     * navigation. This is what the anti-phishing host display on
     * `sign-in-to-site/[conversationId]/[toolCallId]/page.tsx` reads instead,
     * so a person mid-SSO-redirect still sees which site they are actually on.
     * @param origin
     * @returns CurrentPageUrlResponse Successful Response
     * @throws ApiError
     */
    public static workspaceBrowserCurrentPageUrl(
        origin: string,
    ): CancelablePromise<CurrentPageUrlResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/browser/current-page-url',
            query: {
                'origin': origin,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Fit the workspace display to the pane showing it
     * Resize the sandbox display so the picture matches the pane.
     *
     * The alternative, and what this replaces, is one fixed display scaled to
     * fit: a 3:2 screen letterboxed into whatever box it lands in, small and
     * ringed with dead space. Resizing the display itself means the pixels sent
     * are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
     * site serves its mobile layout to somebody signing in on a phone.
     *
     * A failure here is not an error for the person: they keep the display they
     * had. So an unreachable or sleeping sandbox answers with no size rather
     * than a status code the pane would have to special-case.
     * @param requestBody
     * @returns DisplaySizeResponse Successful Response
     * @throws ApiError
     */
    public static workspaceBrowserResizeDisplay(
        requestBody: DisplaySizeRequest,
    ): CancelablePromise<DisplaySizeResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/workspace/browser/display-size',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Whether the workspace browser can be watched
     * @returns BrowserStatusResponse Successful Response
     * @throws ApiError
     */
    public static workspaceBrowserStatus(): CancelablePromise<BrowserStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/browser/status',
        });
    }
}
