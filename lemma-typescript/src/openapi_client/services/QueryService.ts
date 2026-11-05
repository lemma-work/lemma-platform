/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DatastoreQueryRequest } from '../models/DatastoreQueryRequest.js';
import type { DatastoreQueryResponse } from '../models/DatastoreQueryResponse.js';
import type { OutcomeHistoryResponse } from '../models/OutcomeHistoryResponse.js';
import type { OutcomeStandingResponse } from '../models/OutcomeStandingResponse.js';
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
    /**
     * Where the pod has stood, week by week
     * The last `weeks` weeks of the pod's measure, each on its last reading. The measure is read on a schedule and when it is set; a week it was never read in has a null value.
     * @param podId
     * @param weeks
     * @returns OutcomeHistoryResponse Successful Response
     * @throws ApiError
     */
    public static outcomeHistory(
        podId: string,
        weeks: number = 8,
    ): CancelablePromise<OutcomeHistoryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/outcome/history',
            path: {
                'pod_id': podId,
            },
            query: {
                'weeks': weeks,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Where the pod stands
     * What the pod owns and what its measure answers now. The measure is read over every row of the tables it names, so every member sees the same number, including members who cannot open some of those rows; only someone who can read all of them may set it. A measure that cannot be read comes back as a `problem` rather than an error.
     * @param podId
     * @returns OutcomeStandingResponse Successful Response
     * @throws ApiError
     */
    public static outcomeStanding(
        podId: string,
    ): CancelablePromise<OutcomeStandingResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/outcome/standing',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
