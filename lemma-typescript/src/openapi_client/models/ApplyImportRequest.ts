/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for applying a planned import.
 */
export type ApplyImportRequest = {
    /**
     * Required to proceed when the plan has destructive steps.
     */
    confirm_destructive?: boolean;
    /**
     * Resolved values for the plan's ${var} placeholders.
     */
    variables?: Record<string, string>;
};

