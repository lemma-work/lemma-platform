/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BulkCreateRecordsRequest } from '../models/BulkCreateRecordsRequest.js';
import type { BulkDeleteRecordsRequest } from '../models/BulkDeleteRecordsRequest.js';
import type { BulkUpdateRecordsRequest } from '../models/BulkUpdateRecordsRequest.js';
import type { CreateRecordRequest } from '../models/CreateRecordRequest.js';
import type { DatastoreCountResponse } from '../models/DatastoreCountResponse.js';
import type { RecordAccessMode } from '../models/RecordAccessMode.js';
import type { RecordListResponse } from '../models/RecordListResponse.js';
import type { UpdateRecordRequest } from '../models/UpdateRecordRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class RecordsService {
    /**
     * List Records
     * List table records with token pagination only. Use the datastore query endpoint for joins, aggregates, or custom read-only SQL.
     * @param podId
     * @param tableName
     * @param limit Max number of rows to return, up to 1000. Page beyond that with `page_token`.
     * @param offset Row offset for direct pagination.
     * @param filter Optional repeated JSON filters for advanced comparisons. Each `filter` value must be a JSON object with shape `{"field":"<column_name>","op":"<operator>","value":<comparison_value>}`. Allowed operators are: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`, `in`. `in` takes an array `value` and matches any of its members; an empty array matches nothing. `like` and `ilike` take a SQL pattern, where `%` matches any run of characters and `_` matches exactly one — to match either literally, escape it with a backslash (`price\_usd`). Repeat the query parameter to combine multiple filters with AND semantics. Examples: `filter={"field":"amount","op":"gt","value":100}`, `filter={"field":"status","op":"in","value":["OPEN","PENDING"]}`.
     * @param sort Optional repeated JSON sort clauses. Each `sort` value must be a JSON object with shape `{"field":"<column_name>","direction":"<direction>"}`. Allowed directions are: `asc`, `desc`. Repeat the query parameter to provide multi-column sorting in priority order. Example: `sort={"field":"created_at","direction":"desc"}`.
     * @param pageToken Opaque token from a previous response page.
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns RecordListResponse Successful Response
     * @throws ApiError
     */
    public static recordList(
        podId: string,
        tableName: string,
        limit: number = 20,
        offset?: number,
        filter?: (Array<string> | null),
        sort?: (Array<string> | null),
        pageToken?: (string | null),
        mode?: (RecordAccessMode | null),
    ): CancelablePromise<RecordListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            query: {
                'limit': limit,
                'offset': offset,
                'filter': filter,
                'sort': sort,
                'page_token': pageToken,
                'mode': mode,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Record
     * Insert a record into a table. Returns the created record object keyed by column name (no envelope). Reserved tables (`reserved_*`) are system-managed and cannot be mutated through record write endpoints.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static recordCreate(
        podId: string,
        tableName: string,
        requestBody: CreateRecordRequest,
    ): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records',
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
     * Bulk Create
     * Insert multiple records in one request. Returns the affected-row count.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns DatastoreCountResponse Successful Response
     * @throws ApiError
     */
    public static recordBulkCreate(
        podId: string,
        tableName: string,
        requestBody: BulkCreateRecordsRequest,
    ): CancelablePromise<DatastoreCountResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/create',
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
     * Bulk Delete
     * Delete multiple records by primary key values. Returns the affected-row count.
     * @param podId
     * @param tableName
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns DatastoreCountResponse Successful Response
     * @throws ApiError
     */
    public static recordBulkDelete(
        podId: string,
        tableName: string,
        requestBody: BulkDeleteRecordsRequest,
        mode?: (RecordAccessMode | null),
    ): CancelablePromise<DatastoreCountResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/delete',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            query: {
                'mode': mode,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Bulk Update
     * Update multiple records in one request (each item needs primary key). Returns the affected-row count.
     * @param podId
     * @param tableName
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns DatastoreCountResponse Successful Response
     * @throws ApiError
     */
    public static recordBulkUpdate(
        podId: string,
        tableName: string,
        requestBody: BulkUpdateRecordsRequest,
        mode?: (RecordAccessMode | null),
    ): CancelablePromise<DatastoreCountResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/update',
            path: {
                'pod_id': podId,
                'table_name': tableName,
            },
            query: {
                'mode': mode,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Record
     * Delete a record by primary key.
     * @param podId
     * @param tableName
     * @param recordId
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns void
     * @throws ApiError
     */
    public static recordDelete(
        podId: string,
        tableName: string,
        recordId: string,
        mode?: (RecordAccessMode | null),
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}',
            path: {
                'pod_id': podId,
                'table_name': tableName,
                'record_id': recordId,
            },
            query: {
                'mode': mode,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Record
     * Fetch one record by primary key value (returns the record object, no envelope). The `record_id` path segment is the table's primary key value as stored in the table, not necessarily a UUID.
     * @param podId
     * @param tableName
     * @param recordId
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static recordGet(
        podId: string,
        tableName: string,
        recordId: string,
        mode?: (RecordAccessMode | null),
    ): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}',
            path: {
                'pod_id': podId,
                'table_name': tableName,
                'record_id': recordId,
            },
            query: {
                'mode': mode,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Record
     * Patch a record by primary key. Returns the updated record object (no envelope). Pass `expected_updated_at` to make the patch conditional on the row not having changed since it was read; the request then answers 409 rather than overwriting another client's edit.
     * @param podId
     * @param tableName
     * @param recordId
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static recordUpdate(
        podId: string,
        tableName: string,
        recordId: string,
        requestBody: UpdateRecordRequest,
        mode?: (RecordAccessMode | null),
    ): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}',
            path: {
                'pod_id': podId,
                'table_name': tableName,
                'record_id': recordId,
            },
            query: {
                'mode': mode,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
