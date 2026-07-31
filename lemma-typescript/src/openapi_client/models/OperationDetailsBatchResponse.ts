/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationDetail } from './OperationDetail.js';
/**
 * Batch response containing full metadata for multiple operations.
 */
export type OperationDetailsBatchResponse = {
    /**
     * Connector identifier.
     */
    connector_id: string;
    /**
     * Operation details for the requested operations.
     */
    items: Array<OperationDetail>;
    /**
     * Number of operation details returned in this response.
     */
    returned_count: number;
};
