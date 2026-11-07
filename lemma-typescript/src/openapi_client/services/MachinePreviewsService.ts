/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreatePreviewRequest } from '../models/CreatePreviewRequest.js';
import type { PreviewListResponse } from '../models/PreviewListResponse.js';
import type { PreviewResponse } from '../models/PreviewResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class MachinePreviewsService {
    /**
     * Open a private preview as the signed-in person
     * @param label
     * @returns void
     * @throws ApiError
     */
    public static machinePreviewOpen(
        label: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machine-previews/{label}:open',
            path: {
                'label': label,
            },
            errors: {
                302: `Successful Response`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * A machine's live previews
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns PreviewListResponse Successful Response
     * @throws ApiError
     */
    public static machinePreviewList(
        ref: string,
    ): CancelablePromise<PreviewListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/previews',
            path: {
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Open a port on a machine as a preview URL
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns PreviewResponse Successful Response
     * @throws ApiError
     */
    public static machinePreviewCreate(
        ref: string,
        requestBody: CreatePreviewRequest,
    ): CancelablePromise<PreviewResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/previews',
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
     * Revoke a preview; its URL stops answering
     * @param previewId
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns PreviewResponse Successful Response
     * @throws ApiError
     */
    public static machinePreviewRevoke(
        previewId: string,
        ref: string,
    ): CancelablePromise<PreviewResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/machines/{ref}/previews/{preview_id}',
            path: {
                'preview_id': previewId,
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
