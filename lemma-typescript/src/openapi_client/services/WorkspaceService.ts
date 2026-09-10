/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceFileEntry } from '../models/WorkspaceFileEntry.js';
import type { WorkspaceFileListResponse } from '../models/WorkspaceFileListResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WorkspaceService {
    /**
     * List workspace files
     * @param path
     * @param wake Start the workspace if it is paused. Off by default.
     * @returns WorkspaceFileListResponse Successful Response
     * @throws ApiError
     */
    public static workspaceFilesList(
        path?: (string | null),
        wake: boolean = false,
    ): CancelablePromise<WorkspaceFileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files',
            query: {
                'path': path,
                'wake': wake,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read workspace file content
     * @param path
     * @param offset
     * @param length
     * @returns any Successful Response
     * @throws ApiError
     */
    public static workspaceFilesContent(
        path: string,
        offset?: number,
        length?: (number | null),
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files:content',
            query: {
                'path': path,
                'offset': offset,
                'length': length,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stat one workspace file
     * @param path
     * @returns WorkspaceFileEntry Successful Response
     * @throws ApiError
     */
    public static workspaceFilesStat(
        path: string,
    ): CancelablePromise<WorkspaceFileEntry> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files:stat',
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
