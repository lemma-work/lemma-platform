/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request multiple operation details in a single call.
 */
export type OperationDetailsBatchRequest = {
    /**
     * How many to return when `operation_names` is omitted. Ignored when names are given.
     */
    limit?: number;
    /**
     * Operation names to fetch. Omit or pass an empty list to return details for the first `limit` operations in the connector; read `total_operations` on the response to see whether that was all of them.
     */
    operation_names?: (Array<string> | null);
};
