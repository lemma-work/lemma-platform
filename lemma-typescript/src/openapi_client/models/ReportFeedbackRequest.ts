/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FeedbackCategory } from './FeedbackCategory.js';
/**
 * Request payload for maintainer feedback reports.
 */
export type ReportFeedbackRequest = {
    /**
     * What actually happened.
     */
    actual_behavior: string;
    /**
     * High-level category for the feedback report.
     */
    category: FeedbackCategory;
    /**
     * What the caller expected to happen instead.
     */
    expected_behavior: string;
    /**
     * What issue, problem, or incorrect information was encountered.
     */
    issue_encountered: string;
    /**
     * Short subject line summarizing the report.
     */
    subject: string;
    /**
     * Optional proposed fixes, follow-ups, or next steps.
     */
    suggested_next_steps?: (string | null);
};
