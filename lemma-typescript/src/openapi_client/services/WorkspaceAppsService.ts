/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BrowserStatusResponse } from '../models/BrowserStatusResponse.js';
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
