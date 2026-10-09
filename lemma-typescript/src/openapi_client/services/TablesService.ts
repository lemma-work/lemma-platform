/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AddColumnRequest } from '../models/AddColumnRequest.js';
import type { CreateTableRequest } from '../models/CreateTableRequest.js';
import type { OpenReadsRequest } from '../models/OpenReadsRequest.js';
import type { OpenTableRequest } from '../models/OpenTableRequest.js';
import type { OpenTablesResponse } from '../models/OpenTablesResponse.js';
import type { ReadsOpeningResponse } from '../models/ReadsOpeningResponse.js';
import type { TableDetailResponse } from '../models/TableDetailResponse.js';
import type { TableListResponse } from '../models/TableListResponse.js';
import type { TableOpeningResponse } from '../models/TableOpeningResponse.js';
import type { UpdateTableRequest } from '../models/UpdateTableRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class TablesService {
    /**
     * Tables Open To People Outside
     * The open tables of the pod that the caller can read.
     * @param podId
     * @returns OpenTablesResponse Successful Response
     * @throws ApiError
     */
    public static tablePublicRowsList(
        podId: string,
    ): CancelablePromise<OpenTablesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/public-rows',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Tables
     * List tables in a datastore.
     * @param podId
     * @param limit Max number of tables to return.
     * @param pageToken Cursor from a previous response for pagination.
     * @returns TableListResponse Successful Response
     * @throws ApiError
     */
    public static tableList(
        podId: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<TableListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/tables',
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
     * Create Table
     * Create a table in a datastore. Define primary key, column schema, and optional RLS behavior.
     * @param podId
     * @param requestBody
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    public static tableCreate(
        podId: string,
        requestBody: CreateTableRequest,
    ): CancelablePromise<TableDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/tables',
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
     * Delete Table
     * Delete a table and all records in it.
     * @param podId
     * @param tableName
     * @returns void
     * @throws ApiError
     */
    public static tableDelete(
        podId: string,
        tableName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/tables/{table_name}',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Table
     * Get table schema metadata by table name.
     * @param podId
     * @param tableName
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    public static tableGet(
        podId: string,
        tableName: string,
    ): CancelablePromise<TableDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/tables/{table_name}',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Table
     * Update table metadata/configuration, visibility, or toggle row-level security (enable_rls, empty tables only).
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    public static tableUpdate(
        podId: string,
        tableName: string,
        requestBody: UpdateTableRequest,
    ): CancelablePromise<TableDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/datastore/tables/{table_name}',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Column
     * Add a new column to a table. Column names must be unique and compatible with existing table schema rules.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    public static tableColumnAdd(
        podId: string,
        tableName: string,
        requestBody: AddColumnRequest,
    ): CancelablePromise<TableDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/columns',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Column
     * Remove a non-primary, non-system column from a table. System columns and the primary key cannot be removed.
     * @param podId
     * @param tableName
     * @param columnName
     * @returns void
     * @throws ApiError
     */
    public static tableColumnRemove(
        podId: string,
        tableName: string,
        columnName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/columns/{column_name}',
            path: {
                'pod_id': podId,
                'table_name': tableName,
                'column_name': columnName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stop People Outside Reading Rows
     * @param podId
     * @param tableName
     * @returns void
     * @throws ApiError
     */
    public static tablePublicReadsClose(
        podId: string,
        tableName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/public-reads',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Who Outside May Read Rows
     * @param podId
     * @param tableName
     * @returns ReadsOpeningResponse Successful Response
     * @throws ApiError
     */
    public static tablePublicReadsGet(
        podId: string,
        tableName: string,
    ): CancelablePromise<ReadsOpeningResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/public-reads',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Let People Outside Read Rows
     * Open the table's rows to people outside the pod -- confirmed contacts, or anyone -- for the chosen columns only. Rows are read as the member opening it, who must be able to change the table. A table that takes rows from outside can't be opened for reads.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns ReadsOpeningResponse Successful Response
     * @throws ApiError
     */
    public static tablePublicReadsOpen(
        podId: string,
        tableName: string,
        requestBody: OpenReadsRequest,
    ): CancelablePromise<ReadsOpeningResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/public-reads',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stop People Outside Adding Rows
     * @param podId
     * @param tableName
     * @returns void
     * @throws ApiError
     */
    public static tablePublicRowsClose(
        podId: string,
        tableName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/public-rows',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Who Outside May Add Rows
     * @param podId
     * @param tableName
     * @returns TableOpeningResponse Successful Response
     * @throws ApiError
     */
    public static tablePublicRowsGet(
        podId: string,
        tableName: string,
    ): CancelablePromise<TableOpeningResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/public-rows',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Let People Outside Add Rows
     * Open the table to rows from people outside the pod -- confirmed contacts, or anyone -- for the chosen columns only. Rows are added as the member opening it, who must be able to change the table.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns TableOpeningResponse Successful Response
     * @throws ApiError
     */
    public static tablePublicRowsOpen(
        podId: string,
        tableName: string,
        requestBody: OpenTableRequest,
    ): CancelablePromise<TableOpeningResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/public-rows',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
