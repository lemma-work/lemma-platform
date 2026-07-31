/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppBundleUploadRequest } from '../models/AppBundleUploadRequest.js';
import type { AppBundleUploadResponse } from '../models/AppBundleUploadResponse.js';
import type { AppDetailResponse } from '../models/AppDetailResponse.js';
import type { AppListResponse } from '../models/AppListResponse.js';
import type { AppMessageResponse } from '../models/AppMessageResponse.js';
import type { CreateAppFromWidgetRequest } from '../models/CreateAppFromWidgetRequest.js';
import type { CreateAppRequest } from '../models/CreateAppRequest.js';
import type { UpdateAppRequest } from '../models/UpdateAppRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AppsService {
    /**
     * List Apps
     * @param podId
     * @param limit
     * @param pageToken
     * @returns AppListResponse Successful Response
     * @throws ApiError
     */
    public static appList(
        podId: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<AppListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/apps',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create App
     * @param podId
     * @param requestBody
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    public static appCreate(
        podId: string,
        requestBody: CreateAppRequest,
    ): CancelablePromise<AppDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/apps',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Save Widget As App
     * Promote a conversation widget into a persisted app.
     *
     * The widget and the app share one source artifact at two lifecycle stages. This
     * fetches the stored fragment, preserves it, and deploys it in the standalone
     * document wrapper (without conversation-only padding or height messaging).
     * @param podId
     * @param requestBody
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    public static appCreateFromWidget(
        podId: string,
        requestBody: CreateAppFromWidgetRequest,
    ): CancelablePromise<AppDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/apps/from-widget',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete App
     * @param podId
     * @param appName
     * @returns AppMessageResponse Successful Response
     * @throws ApiError
     */
    public static appDelete(
        podId: string,
        appName: string,
    ): CancelablePromise<AppMessageResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/apps/{app_name}',
            path: {
                'pod_id': podId,
                'app_name': appName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get App
     * @param podId
     * @param appName
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    public static appGet(
        podId: string,
        appName: string,
    ): CancelablePromise<AppDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/apps/{app_name}',
            path: {
                'pod_id': podId,
                'app_name': appName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update App
     * @param podId
     * @param appName
     * @param requestBody
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    public static appUpdate(
        podId: string,
        appName: string,
        requestBody: UpdateAppRequest,
    ): CancelablePromise<AppDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/apps/{app_name}',
            path: {
                'pod_id': podId,
                'app_name': appName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get App Root Asset
     * @param podId
     * @param appName
     * @returns any Successful Response
     * @throws ApiError
     */
    public static appAssetRootGet(
        podId: string,
        appName: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/apps/{app_name}/assets',
            path: {
                'pod_id': podId,
                'app_name': appName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get App Asset
     * @param podId
     * @param appName
     * @param assetPath
     * @returns any Successful Response
     * @throws ApiError
     */
    public static appAssetGet(
        podId: string,
        appName: string,
        assetPath: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/apps/{app_name}/assets/{asset_path}',
            path: {
                'pod_id': podId,
                'app_name': appName,
                'asset_path': assetPath,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Upload App Bundle
     * @param podId
     * @param appName
     * @param formData
     * @returns AppBundleUploadResponse Successful Response
     * @throws ApiError
     */
    public static appBundleUpload(
        podId: string,
        appName: string,
        formData?: AppBundleUploadRequest,
    ): CancelablePromise<AppBundleUploadResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/apps/{app_name}/bundle',
            path: {
                'pod_id': podId,
                'app_name': appName,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Download App Dist Archive
     * @param podId
     * @param appName
     * @returns binary Zip archive bytes
     * @throws ApiError
     */
    public static appDistArchiveGet(
        podId: string,
        appName: string,
    ): CancelablePromise<Blob> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/apps/{app_name}/dist/archive',
            path: {
                'pod_id': podId,
                'app_name': appName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Download App Source Archive
     * @param podId
     * @param appName
     * @returns binary Zip archive bytes
     * @throws ApiError
     */
    public static appSourceArchiveGet(
        podId: string,
        appName: string,
    ): CancelablePromise<Blob> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/apps/{app_name}/source/archive',
            path: {
                'pod_id': podId,
                'app_name': appName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
