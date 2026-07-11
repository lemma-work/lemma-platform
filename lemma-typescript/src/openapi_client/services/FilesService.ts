/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { attach } from '../models/attach.js';
import type { CreateFolderRequest } from '../models/CreateFolderRequest.js';
import type { DatastoreFileUploadRequest } from '../models/DatastoreFileUploadRequest.js';
import type { DirectoryTreeResponse } from '../models/DirectoryTreeResponse.js';
import type { FileChildrenResponse } from '../models/FileChildrenResponse.js';
import type { FileDetailResponse } from '../models/FileDetailResponse.js';
import type { FileListResponse } from '../models/FileListResponse.js';
import type { FileSearchRequest } from '../models/FileSearchRequest.js';
import type { FileSearchResponse } from '../models/FileSearchResponse.js';
import type { FileSignedUrlRequest } from '../models/FileSignedUrlRequest.js';
import type { FileSignedUrlResponse } from '../models/FileSignedUrlResponse.js';
import type { FileUrlResponse } from '../models/FileUrlResponse.js';
import type { update } from '../models/update.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class FilesService {
    /**
     * List Files
     * @param podId
     * @param directoryPath
     * @param limit
     * @param pageToken
     * @returns FileListResponse Successful Response
     * @throws ApiError
     */
    public static fileList(
        podId: string,
        directoryPath: string = '/',
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<FileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/files',
            path: {
                'pod_id': podId,
            },
            query: {
                'directory_path': directoryPath,
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Upload File
     * @param podId
     * @param formData
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    public static fileUpload(
        podId: string,
        formData: DatastoreFileUploadRequest,
    ): CancelablePromise<FileDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/files',
            path: {
                'pod_id': podId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete File Or Folder
     * @param podId
     * @param path
     * @returns void
     * @throws ApiError
     */
    public static fileDelete(
        podId: string,
        path: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/files/by-path',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get File
     * @param podId
     * @param path
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    public static fileGet(
        podId: string,
        path: string,
    ): CancelablePromise<FileDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/files/by-path',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update File
     * @param podId
     * @param formData
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    public static fileUpdate(
        podId: string,
        formData: update,
    ): CancelablePromise<FileDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/datastore/files/by-path',
            path: {
                'pod_id': podId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Detach Document Markdown
     * Remove a document's user-provided markdown so it reverts to extraction.
     * @param podId
     * @param path
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    public static fileMarkdownDetach(
        podId: string,
        path: string,
    ): CancelablePromise<FileDetailResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/files/by-path/markdown',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Attach Document Markdown
     * Attach user-authored markdown and referenced images to a document.
     *
     * The source file remains unchanged; the markdown is indexed for agent use.
     * @param podId
     * @param formData
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    public static fileMarkdownAttach(
        podId: string,
        formData: attach,
    ): CancelablePromise<FileDetailResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/datastore/files/by-path/markdown',
            path: {
                'pod_id': podId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List a document's derived child files
     * @param podId
     * @param path
     * @returns FileChildrenResponse Successful Response
     * @throws ApiError
     */
    public static fileChildrenList(
        podId: string,
        path: string,
    ): CancelablePromise<FileChildrenResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/files/children',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Fetch a document's child artifact by path
     * @param podId
     * @param path Child path, e.g. /folder/report.pdf/document.md, /folder/report.pdf/image_0.png, or /folder/report.pdf/pages/page_0001.jpg
     * @param pageStart
     * @param pageEnd
     * @returns binary File bytes
     * @throws ApiError
     */
    public static fileChildGet(
        podId: string,
        path: string,
        pageStart?: (number | null),
        pageEnd?: (number | null),
    ): CancelablePromise<Blob> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/files/children/content',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
                'page_start': pageStart,
                'page_end': pageEnd,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Download File
     * @param podId
     * @param path
     * @returns binary File bytes
     * @throws ApiError
     */
    public static fileDownload(
        podId: string,
        path: string,
    ): CancelablePromise<Blob> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/files/download',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Folder
     * @param podId
     * @param requestBody
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    public static fileFolderCreate(
        podId: string,
        requestBody: CreateFolderRequest,
    ): CancelablePromise<FileDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/files/folders',
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
     * Search Files
     * @param podId
     * @param requestBody
     * @returns FileSearchResponse Successful Response
     * @throws ApiError
     */
    public static fileSearch(
        podId: string,
        requestBody: FileSearchRequest,
    ): CancelablePromise<FileSearchResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/files/search',
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
     * Create a public, hit-capped signed URL for a file
     * @param podId
     * @param path
     * @param requestBody
     * @returns FileSignedUrlResponse Successful Response
     * @throws ApiError
     */
    public static fileSignedUrl(
        podId: string,
        path: string,
        requestBody?: (FileSignedUrlRequest | null),
    ): CancelablePromise<FileSignedUrlResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/files/signed-url',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Directory Tree
     * @param podId
     * @param rootPath
     * @param filesPerDirectory
     * @returns DirectoryTreeResponse Successful Response
     * @throws ApiError
     */
    public static fileTree(
        podId: string,
        rootPath: string = '/',
        filesPerDirectory: number = 3,
    ): CancelablePromise<DirectoryTreeResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/files/tree',
            path: {
                'pod_id': podId,
            },
            query: {
                'root_path': rootPath,
                'files_per_directory': filesPerDirectory,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get a short-lived URL for a file
     * @param podId
     * @param path
     * @returns FileUrlResponse Successful Response
     * @throws ApiError
     */
    public static fileUrl(
        podId: string,
        path: string,
    ): CancelablePromise<FileUrlResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/files/url',
            path: {
                'pod_id': podId,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
