/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MachineFileEntry } from '../models/MachineFileEntry.js';
import type { MachineFileListResponse } from '../models/MachineFileListResponse.js';
import type { MachineStartupResponse } from '../models/MachineStartupResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class WorkspaceService {
    /**
     * @deprecated
     * List workspace files
     * @param path
     * @param wake
     * @param after
     * @returns MachineFileListResponse Successful Response
     * @throws ApiError
     */
    public static workspaceFilesList(
        path?: (string | null),
        wake: boolean = false,
        after?: (string | null),
    ): CancelablePromise<MachineFileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files',
            query: {
                'path': path,
                'wake': wake,
                'after': after,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * @deprecated
     * Read workspace file content
     * @param path
     * @param offset
     * @param length
     * @param range
     * @param ifNoneMatch
     * @returns any Successful Response
     * @throws ApiError
     */
    public static workspaceFilesContent(
        path: string,
        offset?: number,
        length?: (number | null),
        range?: (string | null),
        ifNoneMatch?: (string | null),
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/files:content',
            headers: {
                'Range': range,
                'If-None-Match': ifNoneMatch,
            },
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
     * @deprecated
     * Stat one workspace file
     * @param path
     * @returns MachineFileEntry Successful Response
     * @throws ApiError
     */
    public static workspaceFilesStat(
        path: string,
    ): CancelablePromise<MachineFileEntry> {
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
    /**
     * @deprecated
     * Whether your computer is ready
     * @returns MachineStartupResponse Successful Response
     * @throws ApiError
     */
    public static workspaceStatus(): CancelablePromise<MachineStartupResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workspace/status',
        });
    }
}
