/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IconUploadRequest } from '../models/IconUploadRequest.js';
import type { IconUploadResponse } from '../models/IconUploadResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class IconsService {
    /**
     * Upload Icon
     * Upload an image asset and receive a public icon URL.
     * @param formData
     * @returns IconUploadResponse Successful Response
     * @throws ApiError
     */
    public static iconUpload(
        formData: IconUploadRequest,
    ): CancelablePromise<IconUploadResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/icons/upload',
            formData: formData,
            mediaType: 'multipart/form-data',
        });
    }
    /**
     * Get Public Icon
     * Fetch a previously uploaded public icon asset.
     * @param iconPath
     * @returns any Successful Response
     * @throws ApiError
     */
    public static iconPublicGet(
        iconPath: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/public/icons/{icon_path}',
            path: {
                'icon_path': iconPath,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
