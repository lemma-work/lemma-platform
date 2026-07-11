/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request multiple operation details in a single call.
 */
export type OperationDetailsBatchRequest = {
    /**
     * Operation names to fetch. Omit or pass an empty list to return details for every operation in the connector.
     */
    operation_names?: (Array<string> | null);
};
