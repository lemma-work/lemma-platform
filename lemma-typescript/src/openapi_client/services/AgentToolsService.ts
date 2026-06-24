/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReportFeedbackRequest } from '../models/ReportFeedbackRequest.js';
import type { ReportFeedbackResponse } from '../models/ReportFeedbackResponse.js';
import type { WebSearchRequest } from '../models/WebSearchRequest.js';
import type { WebSearchResponse } from '../models/WebSearchResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentToolsService {
    /**
     * Agent Report Feedback
     * Record a maintainer-facing feedback report about system issues, skill issues, incorrect knowledge, or other unexpected behavior.
     * @param requestBody
     * @returns ReportFeedbackResponse Successful Response
     * @throws ApiError
     */
    public static agentToolReportFeedback(
        requestBody: ReportFeedbackRequest,
    ): CancelablePromise<ReportFeedbackResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/tools/report-feedback',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Agent Web Search
     * Run a raw web search and return structured results.
     * @param requestBody
     * @returns WebSearchResponse Successful Response
     * @throws ApiError
     */
    public static agentToolWebSearch(
        requestBody: WebSearchRequest,
    ): CancelablePromise<WebSearchResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/tools/web-search',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
