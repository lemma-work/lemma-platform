/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Canonical form submission payload — identical across web, SDKs, CLI.
 */
export type WorkflowRunFormSubmitRequest = {
    /**
     * Form field values keyed by field name.
     */
    inputs?: Record<string, any>;
    /**
     * Id of the FORM node being submitted. Must match the run's active wait; mismatches return 422.
     */
    node_id: string;
};
