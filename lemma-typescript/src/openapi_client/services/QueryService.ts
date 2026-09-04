/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DatastoreQueryRequest } from '../models/DatastoreQueryRequest.js';
import type { DatastoreQueryResponse } from '../models/DatastoreQueryResponse.js';
import type { RecordAccessMode } from '../models/RecordAccessMode.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class QueryService {
    /**
     * Execute Query
     * Execute a read-only SQL query inside the datastore schema. Joins, aggregates, subqueries, and cross-table reads are allowed, including across RLS-enabled tables — rows of RLS tables are scoped to the caller by default (pod admins included). Pass `mode=admin` to read every member's rows, which requires permission to administer each referenced RLS table. Only a single read-only statement is permitted; mutating statements and cross-schema references are rejected. Results are capped at the deployment's row limit: `total` is how many rows came back, and `truncated` says whether more matched.
     * @param podId
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables referenced by the query. Omitted/`USER` (default) scopes their rows to the signed-in user — the per-user data apps and functions expect. `ADMIN` returns every member's rows and requires permission to administer every RLS table the query touches; a caller without it gets a 403. Non-RLS tables are unaffected.
     * @returns DatastoreQueryResponse Successful Response
     * @throws ApiError
     */
    public static queryExecute(
        podId: string,
        requestBody: DatastoreQueryRequest,
        mode?: (RecordAccessMode | null),
    ): CancelablePromise<DatastoreQueryResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/query',
            path: {
                'pod_id': podId,
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
