/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Response payload for maintainer feedback reports.
 */
export type ReportFeedbackResponse = {
    /**
     * Delegated agent associated with the report, if available.
     */
    agent_id?: (string | null);
    /**
     * Identifier of the created feedback report.
     */
    feedback_id?: (string | null);
    /**
     * Human-readable status message.
     */
    message?: (string | null);
    /**
     * Whether the feedback was recorded successfully.
     */
    success: boolean;
    /**
     * Authenticated user associated with the report.
     */
    user_id?: (string | null);
};
