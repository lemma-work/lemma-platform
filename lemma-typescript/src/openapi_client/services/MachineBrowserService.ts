/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BrowserAccessRequest } from '../models/BrowserAccessRequest.js';
import type { BrowserAccessResponse } from '../models/BrowserAccessResponse.js';
import type { BrowserCommandRequest } from '../models/BrowserCommandRequest.js';
import type { BrowserCommandResponse } from '../models/BrowserCommandResponse.js';
import type { BrowserStatusResponse } from '../models/BrowserStatusResponse.js';
import type { CurrentPageUrlResponse } from '../models/CurrentPageUrlResponse.js';
import type { DisplaySizeRequest } from '../models/DisplaySizeRequest.js';
import type { DisplaySizeResponse } from '../models/DisplaySizeResponse.js';
import type { NavigateRequest } from '../models/NavigateRequest.js';
import type { NavigateResponse } from '../models/NavigateResponse.js';
import type { StreamBeacon } from '../models/StreamBeacon.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class MachineBrowserService {
    /**
     * Whether a machine's browser can be watched
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns BrowserStatusResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserStatus(
        ref: string,
    ): CancelablePromise<BrowserStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/browser',
            path: {
                'ref': ref,
            },
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
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param origin
     * @returns CurrentPageUrlResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserCurrentPageUrl(
        ref: string,
        origin: string,
    ): CancelablePromise<CurrentPageUrlResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/browser/current-page-url',
            path: {
                'ref': ref,
            },
            query: {
                'origin': origin,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * A signed link to a machine's browser
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns BrowserAccessResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserAccess(
        ref: string,
        requestBody: BrowserAccessRequest,
    ): CancelablePromise<BrowserAccessResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/browser:access',
            path: {
                'ref': ref,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Report how a live screen session went
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns void
     * @throws ApiError
     */
    public static machineBrowserBeacon(
        ref: string,
        requestBody: StreamBeacon,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/browser:beacon',
            path: {
                'ref': ref,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Steer a machine's browser from the screen's toolbar
     * One toolbar command; the open tabs as they are afterwards.
     *
     * 409 with a sentence when the browser cannot do it right now -- it is not
     * running, the tab has closed -- because the toolbar shows it, and 422 for
     * an address that is not a web address.
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns BrowserCommandResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserCommand(
        ref: string,
        requestBody: BrowserCommandRequest,
    ): CancelablePromise<BrowserCommandResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/browser:command',
            path: {
                'ref': ref,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set the size of a machine's browser display
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
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns DisplaySizeResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserDisplay(
        ref: string,
        requestBody: DisplaySizeRequest,
    ): CancelablePromise<DisplaySizeResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/browser:display',
            path: {
                'ref': ref,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Open a page in a machine's browser
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns NavigateResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserNavigate(
        ref: string,
        requestBody: NavigateRequest,
    ): CancelablePromise<NavigateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/browser:navigate',
            path: {
                'ref': ref,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
